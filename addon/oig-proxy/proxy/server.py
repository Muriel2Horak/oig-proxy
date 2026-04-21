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
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from typing import TYPE_CHECKING

try:
    from ..capture.frame_capture import FrameCapture
    from ..config import Config
    from ..protocol.frame import build_frame, extract_frame_from_buffer, infer_table_name
    from ..protocol.frames import build_setting_frame, build_end_frame_with_timestamp
    from ..protocol.parser import parse_xml_frame
    from ..twin.ack_parser import parse_box_ack, parse_tbl_events_ack
    from ..twin.delivery import TwinDelivery
    from .dns_resolve import resolve_a_record
    from .mode import ConnectionMode, ModeManager
    from .local_ack import build_local_ack
except ImportError:
    from capture.frame_capture import FrameCapture  # type: ignore[no-redef]
    from config import Config  # type: ignore[no-redef]
    from protocol.frame import build_frame, extract_frame_from_buffer, infer_table_name  # type: ignore[no-redef]
    from protocol.frames import build_setting_frame, build_end_frame_with_timestamp  # type: ignore[no-redef]
    from protocol.parser import parse_xml_frame  # type: ignore[no-redef]
    from twin.ack_parser import parse_box_ack, parse_tbl_events_ack  # type: ignore[no-redef]
    from twin.delivery import TwinDelivery  # type: ignore[no-redef]
    from proxy.dns_resolve import resolve_a_record  # type: ignore[no-redef]
    from proxy.mode import ConnectionMode, ModeManager  # type: ignore[no-redef]
    from proxy.local_ack import build_local_ack  # type: ignore[no-redef]

if TYPE_CHECKING:
    try:
        from ..telemetry.collector import TelemetryCollector
    except ImportError:
        from telemetry.collector import TelemetryCollector  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


def _extract_id_set(frame_text: str) -> int | None:
    marker_open = "<ID_Set>"
    marker_close = "</ID_Set>"
    start = frame_text.find(marker_open)
    if start == -1:
        return None
    start += len(marker_open)
    end = frame_text.find(marker_close, start)
    if end == -1:
        return None
    try:
        return int(frame_text[start:end])
    except ValueError:
        return None


def _extract_msg_id(frame_text: str) -> int | None:
    marker_open = "<ID>"
    marker_close = "</ID>"
    start = frame_text.find(marker_open)
    if start == -1:
        return None
    start += len(marker_open)
    end = frame_text.find(marker_close, start)
    if end == -1:
        return None
    try:
        return int(frame_text[start:end])
    except ValueError:
        return None


def _read_replay_frame_once(path: str) -> bytes | None:
    replay_path = Path(path)
    if not replay_path.exists():
        return None
    try:
        payload = replay_path.read_bytes()
    except OSError:
        return None
    if not payload:
        try:
            replay_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    try:
        replay_path.unlink(missing_ok=True)
    except OSError:
        pass
    return payload


TRACE_LEVEL = 5
TRANSPORT_RESULT_VALUES = frozenset({"ACK", "END"})
POLL_RESULT_VALUES = frozenset({"IsNewSet", "IsNewWeather", "IsNewFW"})
TRANSPORT_METADATA_KEYS = frozenset(
    {
        "Confirm",
        "ID",
        "ID_Server",
        "NewValue",
        "Rdt",
        "Result",
        "TSec",
        "TblItem",
        "Tmr",
        "ToDo",
        "mytimediff",
    }
)

# Typ callbacku volaného při parsování frame
FrameCallback = Callable[[dict[str, Any]], Awaitable[None]]
ConfirmedSettingCallback = Callable[[str, str, str, Any], Awaitable[None]]


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
        on_confirmed_setting: ConfirmedSettingCallback | None = None,
        twin_delivery: TwinDelivery | None = None,
        frame_capture: FrameCapture | None = None,
        telemetry_collector: "TelemetryCollector | None" = None,
    ) -> None:
        self.config = config
        self.on_frame = on_frame
        self.on_confirmed_setting = on_confirmed_setting
        self.twin_delivery = twin_delivery
        self.frame_capture = frame_capture
        self.telemetry_collector = telemetry_collector
        self._server: asyncio.Server | None = None
        self._active_connections: set[asyncio.Task[None]] = set()
        self.mode_manager = ModeManager(config)

        self._start_time: float = time.time()
        self.frames_received: int = 0
        self.frames_forwarded: int = 0
        self.cloud_connects: int = 0
        self.cloud_disconnects: int = 0
        self.cloud_timeouts: int = 0
        self.cloud_errors: int = 0
        self._box_connected: bool = False
        self.box_peer: str | None = None
        self._cloud_connected: bool = False
        self._active_connection_count: int = 0
        self._cloud_ip: str = self.config.cloud_host

    async def start(self) -> None:
        """Spustí TCP server."""
        dns_upstream = getattr(self.config, "dns_upstream", "8.8.8.8")
        resolved = resolve_a_record(self.config.cloud_host, dns_upstream)
        if resolved:
            self._cloud_ip = resolved
            logger.info(
                "☁️ Cloud host %s resolved to %s via %s",
                self.config.cloud_host, resolved, dns_upstream,
            )
        else:
            self._cloud_ip = self.config.cloud_host
            logger.warning(
                "⚠️ Could not resolve %s via %s, using hostname directly",
                self.config.cloud_host, dns_upstream,
            )
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
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in list(self._active_connections):
            task.cancel()
        if self._active_connections:
            await asyncio.gather(*self._active_connections, return_exceptions=True)
        if self.twin_delivery is not None:
            self.twin_delivery.shutdown()
        logger.info("OIG Proxy v2 zastavena")

    def is_box_connected(self) -> bool:
        return self._box_connected

    def is_cloud_connected(self) -> bool:
        return self._cloud_connected

    def uptime_s(self) -> float:
        return time.time() - self._start_time

    def _record_telemetry_connection_end(
        self,
        *,
        box_connected_since_epoch: float | None,
        box_reason: str,
        box_peer: str | None,
        cloud_connected_since_epoch: float | None,
        cloud_reason: str,
    ) -> None:
        collector = self.telemetry_collector
        if collector is None:
            return
        collector.record_box_session_end(
            connected_since_epoch=box_connected_since_epoch,
            reason=box_reason,
            peer=box_peer,
        )
        if cloud_connected_since_epoch is not None:
            collector.record_cloud_session_end(
                connected_since_epoch=cloud_connected_since_epoch,
                reason=cloud_reason,
            )

    def _record_cloud_connect_failure(
        self,
        *,
        conn_id: int,
        failure_type: str,
        failure_detail: str,
        peer: str,
        will_go_offline: bool,
    ) -> None:
        collector = self.telemetry_collector
        if collector is None:
            return
        if failure_type == "timeout":
            collector.record_timeout(conn_id=conn_id)
        else:
            collector.record_response("", source="error", conn_id=conn_id)
        collector.record_error_context(
            event_type=f"error_cloud_connect_{failure_type}",
            details={
                "cloud_host": self.config.cloud_host,
                "cloud_port": self.config.cloud_port,
                "peer": peer,
                "error": failure_detail,
                "offline_fallback": will_go_offline,
            },
        )
        if will_go_offline:
            collector.record_offline_event(
                reason=f"cloud_connect_{failure_type}",
                local_ack=True,
                mode=str(self.mode_manager.runtime_mode.value),
            )

    async def _handle_box_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        """Handler pro nové připojení od Boxu.
        
        Generates unique session ID for tracking twin delivery per TCP session.
        Cloud-initiated settings take priority over local queue.
        """
        current = asyncio.current_task()
        if current is not None:
            self._active_connections.add(current)
        session_conn_id = id(current)

        peer = box_writer.get_extra_info("peername", ("?", "?"))
        peer_str = f"{peer[0]}:{peer[1]}"

        # Reject excess connections immediately to prevent reconnect storm / FD exhaustion
        if self._active_connection_count >= self.config.max_concurrent_connections:
            logger.warning(
                "⚠️ Max concurrent connections (%d) reached, rejecting %s:%s",
                self.config.max_concurrent_connections,
                *peer[:2],
            )
            box_writer.close()
            try:
                await box_writer.wait_closed()
            except Exception as exc:  # noqa: BLE001
                logger.debug("wait_closed error during rejection: %s", exc)
            if current is not None:
                self._active_connections.discard(current)
            return
        self._active_connection_count += 1

        import uuid
        session_id = str(uuid.uuid4())

        self._box_connected = True
        self.box_peer = peer_str
        box_connected_since_epoch = time.time()
        cloud_connected_since_epoch: float | None = None
        cloud_disconnect_reason = "not_connected"
        box_disconnect_reason = "eof"
        logger.info("📦 BOX připojen z %s:%s (session=%s)", *peer[:2], session_id)

        # Check if we should try cloud connection
        if not self.mode_manager.should_try_cloud():
            logger.info("☁️ Skipping cloud connection (mode=%s)", self.mode_manager.configured_mode)
            box_disconnect_reason = "offline_mode"
            if self.telemetry_collector is not None:
                self.telemetry_collector.record_offline_event(
                    reason="configured_offline_mode",
                    local_ack=True,
                    mode=str(self.mode_manager.runtime_mode.value),
                )
            try:
                await self._pipe_box_offline(box_reader, box_writer, peer, session_id=session_id)
            finally:
                self._record_telemetry_connection_end(
                    box_connected_since_epoch=box_connected_since_epoch,
                    box_reason=box_disconnect_reason,
                    box_peer=peer_str,
                    cloud_connected_since_epoch=None,
                    cloud_reason=cloud_disconnect_reason,
                )
                self._active_connection_count -= 1
                self._box_connected = False
                self.box_peer = None
                if current is not None:
                    self._active_connections.discard(current)
            return

        # Otevřeme spojení do cloudu
        cloud_reader: asyncio.StreamReader | None = None
        cloud_writer: asyncio.StreamWriter | None = None
        try:
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(self._cloud_ip, self.config.cloud_port),
                timeout=self.config.cloud_connect_timeout,
            )
            logger.info(
                "☁️ Cloud spojen: %s:%s (session=%s)",
                self.config.cloud_host,
                self.config.cloud_port,
                session_id,
            )
            self.cloud_connects += 1
            self._cloud_connected = True
            cloud_connected_since_epoch = time.time()
            cloud_disconnect_reason = "eof"
            self.mode_manager.record_success()
        except asyncio.TimeoutError as exc:
            self.cloud_timeouts += 1
            logger.error("❌ Cloud nedostupný: %s", exc)
            self.mode_manager.record_failure(reason=str(exc))
            self._record_cloud_connect_failure(
                conn_id=session_conn_id,
                failure_type="timeout",
                failure_detail=str(exc),
                peer=peer_str,
                will_go_offline=self.mode_manager.is_offline(),
            )
            if self.mode_manager.is_offline():
                box_disconnect_reason = "offline_fallback_timeout"
                try:
                    await self._pipe_box_offline(box_reader, box_writer, peer, session_id=session_id)
                finally:
                    self._record_telemetry_connection_end(
                        box_connected_since_epoch=box_connected_since_epoch,
                        box_reason=box_disconnect_reason,
                        box_peer=peer_str,
                        cloud_connected_since_epoch=None,
                        cloud_reason=cloud_disconnect_reason,
                    )
                    self._active_connection_count -= 1
                    self._box_connected = False
                    self.box_peer = None
                    if current is not None:
                        self._active_connections.discard(current)
                return
            box_disconnect_reason = "cloud_connect_timeout"
            box_writer.close()
            await box_writer.wait_closed()
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=None,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self.box_peer = None
            if current is not None:
                self._active_connections.discard(current)
            return
        except OSError as exc:
            self.cloud_errors += 1
            logger.error("❌ Cloud nedostupný: %s", exc)
            self.mode_manager.record_failure(reason=str(exc))
            self._record_cloud_connect_failure(
                conn_id=session_conn_id,
                failure_type="oserror",
                failure_detail=str(exc),
                peer=peer_str,
                will_go_offline=self.mode_manager.is_offline(),
            )
            if self.mode_manager.is_offline():
                box_disconnect_reason = "offline_fallback_oserror"
                try:
                    await self._pipe_box_offline(box_reader, box_writer, peer, session_id=session_id)
                finally:
                    self._record_telemetry_connection_end(
                        box_connected_since_epoch=box_connected_since_epoch,
                        box_reason=box_disconnect_reason,
                        box_peer=peer_str,
                        cloud_connected_since_epoch=None,
                        cloud_reason=cloud_disconnect_reason,
                    )
                    self._active_connection_count -= 1
                    self._box_connected = False
                    self.box_peer = None
                    if current is not None:
                        self._active_connections.discard(current)
                return
            box_disconnect_reason = "cloud_connect_oserror"
            box_writer.close()
            await box_writer.wait_closed()
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=None,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self.box_peer = None
            if current is not None:
                self._active_connections.discard(current)
            return

        if cloud_reader is None or cloud_writer is None:
            logger.error("❌ Cloud connection missing stream endpoints after successful connect")
            box_writer.close()
            await box_writer.wait_closed()
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason="cloud_connect_missing_stream",
                box_peer=peer_str,
                cloud_connected_since_epoch=None,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self.box_peer = None
            if current is not None:
                self._active_connections.discard(current)
            return

        # Spustíme obousměrný forward.
        # Používáme FIRST_COMPLETED + cancel, aby po odpojení jedné strany
        # byl okamžitě uvolněn i druhý socket (jinak hrozí FD leak – každých
        # ~15 s přibude jeden "stuck" cloud socket dokud systém nevyčerpá FDs).
        pipe_tasks = [
            asyncio.ensure_future(
                self._pipe_box_to_cloud(box_reader, cloud_writer, box_writer, peer=peer, session_id=session_id)
            ),
            asyncio.ensure_future(
                self._pipe_cloud_to_box(cloud_reader, box_writer, peer=peer, session_id=session_id)
            ),
        ]
        try:
            _done, _pending = await asyncio.wait(
                pipe_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for _t in _pending:
                _t.cancel()
            if _pending:
                await asyncio.gather(*_pending, return_exceptions=True)
        except asyncio.CancelledError:
            for _t in pipe_tasks:
                if not _t.done():
                    _t.cancel()
            await asyncio.gather(*pipe_tasks, return_exceptions=True)
            raise
        finally:
            if self.twin_delivery is not None:
                self.twin_delivery.clear_session(session_id)
            for writer in (box_writer, cloud_writer):
                if writer and not writer.is_closing():
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("wait_closed error in pipe cleanup: %s", exc)
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=cloud_connected_since_epoch,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self._cloud_connected = False
            self.box_peer = None
            self.cloud_disconnects += 1
            if current is not None:
                self._active_connections.discard(current)
            logger.info("🔌 BOX odpojen: %s:%s (session=%s)", *peer[:2], session_id)

    async def _pipe_box_to_cloud(
        self,
        box_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter,
        box_writer: asyncio.StreamWriter | None = None,
        peer: tuple | None = None,
        session_id: str | None = None,
    ) -> None:
        """Čte data od Boxu, parsuje framy a forwarduje do cloudu."""
        peer_str = f"{peer[0]}:{peer[1]}" if peer and len(peer) >= 2 else None
        conn_id = id(asyncio.current_task())
        buf = bytearray()
        while True:
            try:
                data = await box_reader.read(4096)
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            if not data:
                try:
                    can_write_eof = getattr(cloud_writer, "can_write_eof", None)
                    if callable(can_write_eof) and can_write_eof():
                        cloud_writer.write_eof()
                except (OSError, ConnectionResetError):
                    pass
                break

            buf.extend(data)
            forward_chunks: list[bytes] = []
            withheld_chunks = False
            while True:
                frame_bytes = extract_frame_from_buffer(buf)
                if frame_bytes is None:
                    break
                self._capture_frame(frame_bytes, "box_to_cloud", conn_id=conn_id, peer=peer_str)
                await self._handle_twin_frames(frame_bytes, box_writer, run_isnewset_hook=False)
                await self._process_frame(frame_bytes)

                parsed_frame = parse_xml_frame(frame_bytes.decode("utf-8", errors="replace"))
                table_name = self._effective_table_name(parsed_frame, frame_bytes.decode("utf-8", errors="replace"))
                device_id = str(parsed_frame.get("_device_id") or "")
                observed_id_set = _extract_id_set(frame_bytes.decode("utf-8", errors="replace"))
                observed_msg_id = _extract_msg_id(frame_bytes.decode("utf-8", errors="replace"))
                if self.twin_delivery is not None:
                    self.twin_delivery.observe_id_set(observed_id_set)
                    self.twin_delivery.observe_msg_id(observed_msg_id)

                if self.telemetry_collector is not None:
                    self.telemetry_collector.record_request(table_name or None, conn_id)
                    self.telemetry_collector.record_frame_direction("box_to_proxy")

                if table_name in {"IsNewSet", "IsNewFW", "IsNewWeather"}:
                    pending = self.twin_delivery.has_pending() if self.twin_delivery else False
                    cloud_inf = self.twin_delivery.is_cloud_inflight() if self.twin_delivery else False
                    logger.debug(
                        "IsNew* poll: table=%s twin_delivery=%s has_pending=%s cloud_inflight=%s box_writer=%s",
                        table_name,
                        self.twin_delivery is not None,
                        pending,
                        cloud_inf,
                        box_writer is not None,
                    )
                if (
                    self.twin_delivery is not None
                    and self.twin_delivery.has_pending()
                    and not self.twin_delivery.is_cloud_inflight()
                    and table_name in {"IsNewSet", "IsNewFW", "IsNewWeather"}
                    and box_writer is not None
                ):
                    replay_frame = _read_replay_frame_once("/data/replay_setting_frame.xml")
                    if replay_frame is not None:
                        try:
                            box_writer.write(replay_frame)
                            await box_writer.drain()
                            self._capture_frame(replay_frame, "proxy_to_box", conn_id=conn_id, peer=peer_str)
                            logger.info("📤 Replayed raw Setting frame to BOX from /data/replay_setting_frame.xml")
                            withheld_chunks = True
                            continue
                        except (OSError, ConnectionResetError) as exc:
                            logger.error("Failed to replay raw Setting frame to BOX: %s", exc)

                    pending_settings = await self.twin_delivery.deliver_pending(
                        device_id,
                        session_id=session_id,
                    )
                    setting = pending_settings[0] if pending_settings else None
                    logger.debug("deliver_pending returned: %s", setting)
                    if setting is not None:
                        audit_session_id = session_id or ""
                        next_id_set = self.twin_delivery.next_id_set()
                        next_msg_id = self.twin_delivery.next_msg_id()
                        setting_frame = build_setting_frame(
                            device_id=device_id,
                            table=setting.table,
                            key=setting.key,
                            value=setting.value,
                            id_set=next_id_set,
                            msg_id=next_msg_id,
                        )
                        logger.debug("Setting frame to BOX: %s", setting_frame.decode("utf-8", errors="replace"))
                        try:
                            box_writer.write(setting_frame)
                            await box_writer.drain()
                            self._capture_frame(setting_frame, "proxy_to_box", conn_id=conn_id, peer=peer_str)
                            logger.info(
                                "📤 Injected local Setting to BOX: %s:%s=%s",
                                setting.table,
                                setting.key,
                                setting.value,
                            )
                            self.twin_delivery.record_injected_box(
                                setting,
                                device_id,
                                session_id=audit_session_id,
                            )
                            withheld_chunks = True
                            continue
                        except (OSError, ConnectionResetError) as exc:
                            logger.error("Failed to inject Setting to BOX: %s", exc)

                forward_chunks.append(frame_bytes)

            if withheld_chunks:
                continue

            if forward_chunks:
                payload = b"".join(forward_chunks)
                try:
                    cloud_writer.write(payload)
                    await cloud_writer.drain()
                    self.frames_forwarded += len(forward_chunks)
                except (OSError, ConnectionResetError) as exc:
                    self.mode_manager.record_failure(reason=str(exc))
                    if self.mode_manager.is_offline():
                        if box_writer is not None:
                            offline_buf = bytearray(payload)
                            await self._handle_offline_frames(offline_buf, box_writer)
                    break
            elif data:
                try:
                    cloud_writer.write(data)
                    await cloud_writer.drain()
                except (OSError, ConnectionResetError) as exc:
                    self.mode_manager.record_failure(reason=str(exc))
                    if self.mode_manager.is_offline():
                        if box_writer is not None:
                            offline_buf = bytearray(data)
                            await self._handle_offline_frames(offline_buf, box_writer)
                    break

    async def _pipe_cloud_to_box(
        self,
        cloud_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        peer: tuple | None = None,
        session_id: str | None = None,
    ) -> None:
        """
        Čte data z cloudu a forwarduje do Boxu.
        """
        peer_str = f"{peer[0]}:{peer[1]}" if peer and len(peer) >= 2 else None
        conn_id = id(asyncio.current_task())
        buf = bytearray()
        while True:
            try:
                data = await cloud_reader.read(4096)
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            if not data:
                try:
                    can_write_eof = getattr(box_writer, "can_write_eof", None)
                    if callable(can_write_eof) and can_write_eof():
                        box_writer.write_eof()
                except (OSError, ConnectionResetError):
                    pass
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
                self._capture_frame(frame_bytes, "cloud_to_box", conn_id=conn_id, peer=peer_str)
                
                frame_text = frame_bytes.decode("utf-8", errors="replace")
                parsed_frame = parse_xml_frame(frame_text)
                table_name = self._effective_table_name(parsed_frame, frame_text)
                observed_id_set = _extract_id_set(frame_text)
                observed_msg_id = _extract_msg_id(frame_text)
                if self.twin_delivery is not None:
                    self.twin_delivery.observe_id_set(observed_id_set)
                    self.twin_delivery.observe_msg_id(observed_msg_id)

                if self.telemetry_collector is not None:
                    self.telemetry_collector.record_response(frame_text, source="cloud", conn_id=conn_id)
                    self.telemetry_collector.record_frame_direction("cloud_to_proxy")
                
                if self.twin_delivery is not None:
                    if (
                        table_name == "Setting"
                        or (
                            "<Reason>Setting</Reason>" in frame_text
                            and "<TblName>" in frame_text
                            and "<TblItem>" in frame_text
                            and "<NewValue>" in frame_text
                        )
                    ):
                        self.twin_delivery.set_cloud_inflight()
                        logger.info("☁️ Cloud Setting detected, marking cloud inflight")
                    elif table_name == "END":
                        self.twin_delivery.clear_cloud_inflight()
                        logger.debug("☁️ Cloud END received, clearing cloud inflight")
                
                await self._handle_twin_frames(frame_bytes, box_writer, session_id=session_id)
                await self._process_frame(frame_bytes)

    async def _handle_twin_frames(
        self,
        frame_bytes: bytes,
        box_writer: asyncio.StreamWriter | None,
        session_id: str | None = None,
        run_isnewset_hook: bool = True,
    ) -> None:
        if not self.twin_delivery:
            return

        audit_session_id = session_id or ""
        frame_text = frame_bytes.decode("utf-8", errors="replace")
        parsed_frame = parse_xml_frame(frame_text)
        table_name = self._effective_table_name(parsed_frame, frame_text)

        if run_isnewset_hook and table_name == "IsNewSet" and box_writer is not None:
            await self._deliver_pending_for_isnewset(frame_text, box_writer)

        inflight_setting = self.twin_delivery.inflight_setting() if self.twin_delivery else None
        confirmed_published = False

        def _unpack_inflight():
            if inflight_setting is None:
                return None
            try:
                setting, device_id = inflight_setting
                return setting, device_id
            except Exception:
                return None

        parsed_ack = parse_box_ack(frame_bytes)
        if (
            parsed_ack
            and parsed_ack.get("result") == "ACK"
            and parsed_ack.get("table")
            and parsed_ack.get("todo")
        ):
            matched_inflight = False
            pair = _unpack_inflight()
            if pair is not None:
                setting, inflight_device_id = pair
                if (setting.table, setting.key) == (parsed_ack["table"], parsed_ack["todo"]):
                    matched_inflight = True
                    self.twin_delivery.record_ack_box_observed(
                        setting,
                        inflight_device_id,
                        session_id=audit_session_id,
                    )
            logger.info(
                "✅ BOX ACK received: %s:%s payload=%s",
                parsed_ack["table"],
                parsed_ack["todo"],
                frame_text,
            )
            if not matched_inflight:
                self.twin_delivery.acknowledge(
                    parsed_ack["table"],
                    parsed_ack["todo"],
                    session_id=session_id,
                )

        event_ack = parse_tbl_events_ack(parsed_frame)
        if event_ack and event_ack.get("table") and event_ack.get("key"):
            await self._publish_confirmed_setting(
                str(parsed_frame.get("_device_id") or ""),
                event_ack["table"],
                event_ack["key"],
                event_ack.get("value"),
            )
            confirmed_published = True
            logger.info(
                "✅ BOX ACK received (tbl_events): %s:%s payload=%s",
                event_ack["table"],
                event_ack["key"],
                frame_text,
            )
            pair = _unpack_inflight()
            if pair is not None:
                setting, inflight_device_id = pair
                if (setting.table, setting.key) == (event_ack["table"], event_ack["key"]):
                    self.twin_delivery.record_ack_tbl_events(
                        setting,
                        inflight_device_id,
                        confirmed_value=event_ack.get("value"),
                        session_id=audit_session_id,
                    )
            self.twin_delivery.acknowledge(
                event_ack["table"],
                event_ack["key"],
                session_id=session_id,
            )

        if parsed_ack and parsed_ack.get("result") == "ACK" and parsed_ack.get("reason") == "Setting":
            pair = _unpack_inflight()
            if pair is not None:
                setting, inflight_device_id = pair
                if not confirmed_published:
                    await self._publish_confirmed_setting(
                        inflight_device_id,
                        setting.table,
                        setting.key,
                        setting.value,
                    )
                self.twin_delivery.record_ack_reason_setting(
                    setting,
                    inflight_device_id,
                    session_id=audit_session_id,
                )
                table, key = setting.table, setting.key
                logger.info(
                    "✅ BOX ACK received (Reason=Setting), acknowledging inflight %s:%s payload=%s",
                    table,
                    key,
                    frame_text,
                )
                self.twin_delivery.acknowledge(table, key, session_id=session_id)

        if parsed_ack and parsed_ack.get("result") == "NACK":
            pair = _unpack_inflight()
            if pair is not None:
                setting, inflight_device_id = pair
                self.twin_delivery.record_nack(
                    setting,
                    inflight_device_id,
                    session_id=audit_session_id,
                )
                logger.info(
                    "❌ BOX NACK received for inflight %s:%s payload=%s",
                    setting.table,
                    setting.key,
                    frame_text,
                )
                self.twin_delivery.acknowledge(
                    setting.table,
                    setting.key,
                    session_id=session_id,
                )

    async def _publish_confirmed_setting(
        self,
        device_id: str | None,
        table: str,
        key: str,
        value: Any,
    ) -> None:
        if self.on_confirmed_setting is None or not device_id:
            return
        try:
            await self.on_confirmed_setting(device_id, table, key, value)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Confirmed setting publish failed for %s:%s=%s: %s",
                table,
                key,
                value,
                exc,
            )

    async def _deliver_pending_for_isnewset(
        self,
        frame_text: str,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        if self.twin_delivery is None:
            return
        parsed_frame = parse_xml_frame(frame_text)
        device_id = str(parsed_frame.get("_device_id") or "")
        pending = await self.twin_delivery.deliver_pending(device_id)
        for setting in pending:
            id_set = self.twin_delivery.next_id_set()
            payload = self.twin_delivery.build_setting_xml(
                setting.table,
                setting.key,
                setting.value,
                device_id=device_id,
                id_set=id_set,
            )
            try:
                frame = build_frame(payload).encode("utf-8", errors="replace")
                box_writer.write(frame)
                await box_writer.drain()
                self.twin_delivery.record_injected_box(setting, device_id)
            except (OSError, ConnectionResetError):
                break

    async def _process_frame(self, frame_bytes: bytes) -> None:
        """Parsuje frame a volá callback."""
        self.frames_received += 1
        if not self.on_frame:
            return
        try:
            text = frame_bytes.decode("utf-8", errors="replace")
            parsed = parse_xml_frame(text)
            if parsed and not self._is_transport_frame(parsed):
                await self.on_frame(parsed)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Frame parse error: %s", exc)

    def _capture_frame(
        self,
        frame_bytes: bytes,
        direction: str,
        conn_id: int | None = None,
        peer: str | None = None,
    ) -> None:
        if self.frame_capture is None:
            self._log_frame_payload(frame_bytes, direction, conn_id=conn_id, peer=peer)
            return
        try:
            raw = frame_bytes.decode("utf-8", errors="replace")
            parsed = parse_xml_frame(raw)
            device_id = str(parsed.get("_device_id") or "")
            table = self._effective_table_name(parsed, raw)
            self._log_frame_payload(frame_bytes, direction, conn_id=conn_id, peer=peer)
            self.frame_capture.capture(
                device_id=device_id or None,
                table=table or None,
                raw=raw,
                raw_bytes=frame_bytes,
                parsed=parsed,
                direction=direction,
                conn_id=conn_id,
                peer=peer,
                length=len(frame_bytes),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_capture_frame error: %s", exc)

    def _log_frame_payload(
        self,
        frame_bytes: bytes,
        direction: str,
        conn_id: int | None = None,
        peer: str | None = None,
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return

        payload = frame_bytes.decode("utf-8", errors="replace")
        parsed = parse_xml_frame(payload)
        table = self._effective_table_name(parsed, payload)
        device_id = str(parsed.get("_device_id") or "")

        logger.debug(
            "📦 FRAME direction=%s table=%s device_id=%s peer=%s conn_id=%s len=%d payload=%s",
            direction,
            table or "unknown",
            device_id or "unknown",
            peer or "unknown",
            conn_id,
            len(frame_bytes),
            payload,
        )

        if logger.isEnabledFor(TRACE_LEVEL):
            logger.log(
                TRACE_LEVEL,
                "📦 FRAME RAW direction=%s table=%s bytes_hex=%s",
                direction,
                table or "unknown",
                frame_bytes.hex(),
            )

    @staticmethod
    def _effective_table_name(parsed: dict[str, Any], payload: str) -> str:
        result = str(parsed.get("Result") or "")
        if result in POLL_RESULT_VALUES | TRANSPORT_RESULT_VALUES:
            return result
        return str(parsed.get("_table") or infer_table_name(payload) or "")

    @staticmethod
    def _is_transport_frame(parsed: dict[str, Any]) -> bool:
        result = str(parsed.get("Result") or "")
        if result in TRANSPORT_RESULT_VALUES:
            return True

        if result in POLL_RESULT_VALUES:
            publishable_keys = [
                key
                for key in parsed
                if not key.startswith("_") and key not in TRANSPORT_METADATA_KEYS
            ]
            return not publishable_keys

        keys = {key for key in parsed if not key.startswith("_")}
        if {"TblItem", "NewValue"}.issubset(keys) and keys & {
            "Confirm",
            "ID",
            "ID_Server",
            "TSec",
            "mytimediff",
        }:
            return True

        return False

    async def _pipe_box_offline(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        peer: tuple,
        session_id: str | None = None,
    ) -> None:
        """Handle Box connection in offline mode - send local ACKs."""
        logger.info("📴 OFFLINE mode: handling Box connection from %s:%s (session=%s)", *peer[:2], session_id)
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
            except Exception as exc:  # noqa: BLE001
                logger.debug("wait_closed error in offline pipe: %s", exc)
            logger.info("🔌 BOX odpojen (offline): %s:%s", *peer[:2])

    async def _handle_offline_frames(
        self,
        buf: bytearray,
        box_writer: asyncio.StreamWriter,
        session_id: str | None = None,
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
            await self._handle_twin_frames(frame_bytes, box_writer, session_id=session_id)
            # Process frame for MQTT publishing
            await self._process_frame(frame_bytes)
