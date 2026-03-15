#!/usr/bin/env python3
"""
TCP Proxy Server pro OIG Box ↔ Cloud.

Přijímá TCP spojení od OIG Boxu, forwarduje data do cloudu
a zpět. Paralelně parsuje XML framy a předává je do callbacku.

Supports ONLINE, HYBRID, and OFFLINE modes with local ACK generation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

try:
    from ..config import Config
    from ..protocol.frame import build_frame, extract_frame_from_buffer, infer_table_name
    from ..protocol.parser import parse_xml_frame
    from ..twin.ack_parser import parse_box_ack, parse_tbl_events_ack
    from ..twin.delivery import TwinDelivery
    from .mode import ConnectionMode, ModeManager
    from .local_ack import build_local_ack
except ImportError:
    from config import Config  # type: ignore[no-redef]
    from protocol.frame import build_frame, extract_frame_from_buffer, infer_table_name  # type: ignore[no-redef]
    from protocol.parser import parse_xml_frame  # type: ignore[no-redef]
    from twin.ack_parser import parse_box_ack, parse_tbl_events_ack  # type: ignore[no-redef]
    from twin.delivery import TwinDelivery  # type: ignore[no-redef]
    from proxy.mode import ConnectionMode, ModeManager  # type: ignore[no-redef]
    from proxy.local_ack import build_local_ack  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Typ callbacku volaného při parsování frame
FrameCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ProxyServer:
    """
    TCP proxy server.

    Přijímá spojení od Boxu na proxy_port.
    Pro každé spojení otevře spojení do cloudu.
    Forwarduje data oběma směry a parsuje framy z Boxu.
    """

    def __init__(
        self,
        config: Config,
        on_frame: FrameCallback | None = None,
        twin_delivery: TwinDelivery | None = None,
    ) -> None:
        self.config = config
        self.on_frame = on_frame
        self.twin_delivery = twin_delivery
        self._server: asyncio.Server | None = None
        self._active_connections: set[asyncio.Task[None]] = set()
        self.mode_manager = ModeManager(config)

    async def start(self) -> None:
        """Spustí TCP server."""
        self._server = await asyncio.start_server(
            self._handle_box_connection,
            self.config.proxy_host,
            self.config.proxy_port,
        )
        addr = self._server.sockets[0].getsockname() if self._server.sockets else "?"
        logger.info("🚀 OIG Proxy v2 naslouchá na %s:%s", *addr[:2])

    async def serve_forever(self) -> None:
        """Blokuje dokud není server zastaven."""
        if self._server is None:
            await self.start()
        server = self._server
        if server is None:
            return
        async with server:
            await server.serve_forever()

    async def stop(self) -> None:
        """Graceful shutdown."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        # Zrušíme aktivní spojení
        for task in list(self._active_connections):
            task.cancel()
        if self._active_connections:
            await asyncio.gather(*self._active_connections, return_exceptions=True)
        logger.info("OIG Proxy v2 zastavena")

    async def _handle_box_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        """Handler pro nové připojení od Boxu."""
        current = asyncio.current_task()
        if current is not None:
            self._active_connections.add(current)

        peer = box_writer.get_extra_info("peername", ("?", "?"))
        logger.info("📦 BOX připojen z %s:%s", *peer[:2])

        # Check if we should try cloud connection
        if not self.mode_manager.should_try_cloud():
            logger.info("☁️ Skipping cloud connection (mode=%s)", self.mode_manager.configured_mode)
            await self._pipe_box_offline(box_reader, box_writer, peer)
            return

        # Otevřeme spojení do cloudu
        cloud_reader: asyncio.StreamReader | None = None
        cloud_writer: asyncio.StreamWriter | None = None
        try:
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.cloud_host, self.config.cloud_port),
                timeout=self.config.cloud_connect_timeout,
            )
            logger.info(
                "☁️ Cloud spojen: %s:%s",
                self.config.cloud_host,
                self.config.cloud_port,
            )
            # Record success for hybrid mode
            self.mode_manager.record_success()
        except (OSError, asyncio.TimeoutError) as exc:
            logger.error("❌ Cloud nedostupný: %s", exc)
            # Record failure for hybrid mode
            self.mode_manager.record_failure(reason=str(exc))

            # If in offline mode after failure, handle offline
            if self.mode_manager.is_offline():
                await self._pipe_box_offline(box_reader, box_writer, peer)
                return

            # Otherwise close connection
            box_writer.close()
            await box_writer.wait_closed()
            return

        # Spustíme obousměrný forward
        try:
            await asyncio.gather(
                self._pipe_box_to_cloud(box_reader, cloud_writer, box_writer),
                self._pipe_cloud_to_box(cloud_reader, box_writer),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            raise
        finally:
            # Cleanup po ukončení
            for writer in (box_writer, cloud_writer):
                if writer and not writer.is_closing():
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:  # noqa: BLE001
                        pass
            if current is not None:
                self._active_connections.discard(current)
            logger.info("🔌 BOX odpojen: %s:%s", *peer[:2])

    async def _pipe_box_to_cloud(
        self,
        box_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter,
        box_writer: asyncio.StreamWriter | None = None,
    ) -> None:
        """Čte data od Boxu, parsuje framy a forwarduje do cloudu."""
        buf = bytearray()
        while True:
            try:
                data = await box_reader.read(4096)
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            if not data:
                break

            buf.extend(data)
            # Forward raw bytes do cloudu
            try:
                cloud_writer.write(data)
                await cloud_writer.drain()
            except (OSError, ConnectionResetError) as exc:
                # Cloud connection lost - record failure
                self.mode_manager.record_failure(reason=str(exc))
                if self.mode_manager.is_offline():
                    # Switch to offline mode for remaining frames
                    if box_writer is not None:
                        await self._handle_offline_frames(buf, box_writer)
                break

            # Parsuj kompletní framy
            while True:
                frame_bytes = extract_frame_from_buffer(buf)
                if frame_bytes is None:
                    break
                await self._handle_twin_frames(frame_bytes, box_writer)
                await self._process_frame(frame_bytes)

    async def _pipe_cloud_to_box(
        self,
        cloud_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        """
        Čte data z cloudu a forwarduje do Boxu.
        """
        buf = bytearray()
        while True:
            try:
                data = await cloud_reader.read(4096)
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            if not data:
                break

            try:
                box_writer.write(data)
                await box_writer.drain()
            except (OSError, ConnectionResetError):
                break

            buf.extend(data)
            while True:
                frame_bytes = extract_frame_from_buffer(buf)
                if frame_bytes is None:
                    break
                await self._handle_twin_frames(frame_bytes, box_writer)
                await self._process_frame(frame_bytes)

    async def _handle_twin_frames(
        self,
        frame_bytes: bytes,
        box_writer: asyncio.StreamWriter | None,
    ) -> None:
        if not self.twin_delivery:
            return

        frame_text = frame_bytes.decode("utf-8", errors="replace")
        parsed_frame = parse_xml_frame(frame_text)
        table_name = str(parsed_frame.get("_table") or infer_table_name(frame_text) or "")

        if table_name in {"IsNewSet", "tbl_actual"} and box_writer is not None:
            await self._deliver_pending_for_isnewset(frame_text, box_writer)

        parsed_ack = parse_box_ack(frame_bytes)
        if (
            parsed_ack
            and parsed_ack.get("result") == "ACK"
            and parsed_ack.get("table")
            and parsed_ack.get("todo")
        ):
            self.twin_delivery.acknowledge(parsed_ack["table"], parsed_ack["todo"])

        event_ack = parse_tbl_events_ack(parsed_frame)
        if event_ack and event_ack.get("table") and event_ack.get("key"):
            self.twin_delivery.acknowledge(event_ack["table"], event_ack["key"])

    async def _deliver_pending_for_isnewset(
        self,
        frame_text: str,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        if self.twin_delivery is None:
            return
        parsed_frame = parse_xml_frame(frame_text)
        device_id = str(parsed_frame.get("_device_id", ""))
        pending = await self.twin_delivery.deliver_pending(device_id)
        for setting in pending:
            payload = self.twin_delivery.build_setting_xml(
                setting.table,
                setting.key,
                setting.value,
            )
            try:
                frame = build_frame(payload).encode("utf-8", errors="replace")
                box_writer.write(frame)
                await box_writer.drain()
            except (OSError, ConnectionResetError):
                break

    async def _process_frame(self, frame_bytes: bytes) -> None:
        """Parsuje frame a volá callback."""
        if not self.on_frame:
            return
        try:
            text = frame_bytes.decode("utf-8", errors="replace")
            parsed = parse_xml_frame(text)
            if parsed:
                await self.on_frame(parsed)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Frame parse error: %s", exc)

    async def _pipe_box_offline(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        peer: tuple,
    ) -> None:
        """Handle Box connection in offline mode - send local ACKs."""
        logger.info("📴 OFFLINE mode: handling Box connection from %s:%s", *peer[:2])
        buf = bytearray()
        try:
            while True:
                data = await box_reader.read(4096)
                if not data:
                    break
                buf.extend(data)
                await self._handle_offline_frames(buf, box_writer)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass
        finally:
            box_writer.close()
            try:
                await box_writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            logger.info("🔌 BOX odpojen (offline): %s:%s", *peer[:2])

    async def _handle_offline_frames(
        self,
        buf: bytearray,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        """Process frames from buffer and send local ACKs."""
        while True:
            frame_bytes = extract_frame_from_buffer(buf)
            if frame_bytes is None:
                break
            # Parse frame to get table name
            try:
                text = frame_bytes.decode("utf-8", errors="replace")
                table_name = infer_table_name(text) or ""
            except Exception:  # noqa: BLE001
                table_name = ""
            # Build and send local ACK
            ack_frame = build_local_ack(table_name, has_queued_data=False)
            try:
                box_writer.write(ack_frame)
                await box_writer.drain()
                logger.debug("📤 Sent local ACK for %s", table_name or "unknown")
            except (OSError, ConnectionResetError):
                break
            await self._handle_twin_frames(frame_bytes, box_writer)
            # Process frame for MQTT publishing
            await self._process_frame(frame_bytes)
