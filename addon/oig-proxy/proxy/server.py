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
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from typing import TYPE_CHECKING

try:
    from ..capture.frame_capture import FrameCapture
    from ..config import Config
    from ..parameter_refresh import ParameterReadbackRefresh
    from ..protocol.frame import FRAME_END, FRAME_START, MAX_FRAME_BYTES, extract_frame_from_buffer, infer_table_name
    from ..protocol.frames import build_end_time_frame, build_getactual_frame
    from ..protocol.parser import parse_xml_frame
    from ..protocol.ver_profile import BOX_POLL_RESULTS, native_frame_fields, native_receipt_fields
    from ..twin.ack_parser import parse_tbl_events_ack
    from ..twin.delivery import TwinDelivery
    from .dns_resolve import DEFAULT_DNS_SERVER, resolve_a_record
    from .mode import ModeManager
    from .local_ack import build_local_ack
except ImportError:
    from capture.frame_capture import FrameCapture  # type: ignore[no-redef]
    from config import Config  # type: ignore[no-redef]
    from parameter_refresh import ParameterReadbackRefresh  # type: ignore[no-redef]
    from protocol.frame import (  # type: ignore[no-redef]
        FRAME_END, FRAME_START, MAX_FRAME_BYTES, extract_frame_from_buffer, infer_table_name,
    )
    from protocol.frames import build_end_time_frame, build_getactual_frame  # type: ignore[no-redef]
    from protocol.parser import parse_xml_frame  # type: ignore[no-redef]
    from protocol.ver_profile import BOX_POLL_RESULTS, native_frame_fields, native_receipt_fields  # type: ignore[no-redef]
    from twin.ack_parser import parse_tbl_events_ack  # type: ignore[no-redef]
    from twin.delivery import TwinDelivery  # type: ignore[no-redef]
    from proxy.dns_resolve import DEFAULT_DNS_SERVER, resolve_a_record  # type: ignore[no-redef]
    from proxy.mode import ModeManager  # type: ignore[no-redef]
    from proxy.local_ack import build_local_ack  # type: ignore[no-redef]

if TYPE_CHECKING:
    try:
        from ..telemetry.collector import TelemetryCollector
        from ..twin.state import TwinSetting
    except ImportError:
        from telemetry.collector import TelemetryCollector  # type: ignore[no-redef]
        from twin.state import TwinSetting  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


TRACE_LEVEL = 5
TRANSPORT_RESULT_VALUES = frozenset({"ACK", "END"})
SETTING_POLL_TAGS = tuple(f"<Result>{result}</Result>".encode("ascii") for result in BOX_POLL_RESULTS)
# NOTE: kept in sync with sensor/processor.py:TRANSPORT_METADATA_KEYS. Not extracted
# to a shared module: the HAOS git-addon rebuild runs `git clean`, which deletes any
# new untracked file before the image is built.
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
ConfirmedSettingCallback = Callable[..., Awaitable[None]]


@dataclass
class _SessionTraffic:
    box_started: bool = False
    cloud_started: bool = False
    cloud_verified: bool = False

    @property
    def started(self) -> bool:
        return self.box_started or self.cloud_started


def _before_partial_marker(data: bytes, marker: bytes, start: int) -> int:
    for size in range(min(len(marker) - 1, len(data) - start), 0, -1):
        if data.endswith(marker[:size]):
            return len(data) - size
    return len(data)


def _box_frame_spans(data: bytes, oversized: bool) -> tuple[list[tuple[int, int]], int, bool]:
    """Find complete frames and the prefix ready to send, leaving only an unfinished tail."""
    frames: list[tuple[int, int]] = []
    cursor = 0
    if oversized:
        close = data.find(FRAME_END)
        if close < 0:
            return frames, _before_partial_marker(data, FRAME_END, cursor), True
        cursor = close + len(FRAME_END)

    while (start := data.find(FRAME_START, cursor)) >= 0:
        close = data.find(FRAME_END, start + len(FRAME_START))
        if close < 0:
            if len(data) - start < MAX_FRAME_BYTES:
                return frames, start, False
            return frames, _before_partial_marker(data, FRAME_END, start), True
        end = close + len(FRAME_END)
        cursor = end
        if end - start <= MAX_FRAME_BYTES:
            if data.startswith(b"\r\n", end):
                cursor += 2
            elif data.startswith((b"\r", b"\n"), end):
                cursor += 1
            frames.append((start, cursor))
    return frames, _before_partial_marker(data, FRAME_START, cursor), False


def _may_acknowledge_setting(frame: bytes) -> bool:
    return (b"<Result>ACK</Result>" in frame or b"<Result>NACK</Result>" in frame
            or b"<TblName>tbl_events</TblName>" in frame and b"<Type>Setting</Type>" in frame)


def _may_offer_setting_slot(frame: bytes) -> bool:
    return any(tag in frame for tag in SETTING_POLL_TAGS)


def _buffered_box_bytes(reader: asyncio.StreamReader) -> int:
    # StreamReader exposes no public count for bytes already received before a write.
    buffer = getattr(reader, "_buffer", None)
    return len(buffer) if isinstance(buffer, bytearray) else 0


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
        *,
        device_id_provider: Callable[[], str | None] | None = None,
        request_readback: Callable[[], TwinSetting | None] | None = None,
    ) -> None:
        self.config = config
        self.on_frame = on_frame
        self.on_confirmed_setting = on_confirmed_setting
        self.twin_delivery = twin_delivery
        self.frame_capture = frame_capture
        self.telemetry_collector = telemetry_collector
        self._device_id_provider = device_id_provider
        self._request_readback = request_readback
        self._readback = ParameterReadbackRefresh()
        self._server: asyncio.Server | None = None
        self._active_connections: set[asyncio.Task[None]] = set()
        self._box_peers: dict[str, str] = {}
        self._cloud_sessions: set[str] = set()
        self._hybrid_probe: str | None = None
        self._cloud_partial_sessions: set[str | None] = set()
        self._box_devices: dict[str, str | None] = {}
        self._offline_writers: set[asyncio.StreamWriter] = set()
        self._offline_reply_deadlines: dict[int, float] = {}
        self.mode_manager = ModeManager(config)

        self._start_time: float = time.time()
        self.frames_received: int = 0
        self.frames_forwarded: int = 0
        self.cloud_connects: int = 0
        self.cloud_disconnects: int = 0
        self.cloud_timeouts: int = 0
        self.cloud_errors: int = 0
        self._active_connection_count: int = 0
        self._cloud_ip: str = self.config.cloud_host

    async def start(self) -> None:
        """Spustí TCP server."""
        dns_upstream = getattr(self.config, "dns_upstream", DEFAULT_DNS_SERVER)
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
        return bool(self._box_peers)

    @property
    def box_peer(self) -> str | None:
        return next(reversed(self._box_peers.values()), None)

    async def apply_configured_mode(self, configured_mode: str) -> bool:
        """Apply the mode and reconnect idle offline BOX sockets to cloud."""
        if not await self.mode_manager.apply_configured_mode(configured_mode):
            return False
        if not self.mode_manager.is_offline():
            self._close_idle_offline_connections()
        return True

    def _close_idle_offline_connections(self) -> None:
        for writer in self._offline_writers:
            if id(writer) not in self._offline_reply_deadlines:
                writer.close()

    def is_cloud_connected(self) -> bool:
        return bool(self._cloud_sessions)

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

    def _local_getactual_interval_s(self) -> int:
        try:
            interval_s = int(getattr(self.config, "local_getactual_interval_s", 10))
        except (TypeError, ValueError):
            interval_s = 10
        return max(10, interval_s)

    def _start_local_getactual_task(
        self,
        box_writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> asyncio.Task[None] | None:
        if not bool(getattr(self.config, "local_getactual_enabled", False)):
            return None
        return asyncio.create_task(
            self._local_getactual_loop(box_writer, conn_id=conn_id, peer=peer),
            name=f"local-getactual-{conn_id}",
        )

    async def _stop_local_getactual_task(
        self,
        task: asyncio.Task[None] | None,
    ) -> None:
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _local_getactual_loop(
        self,
        box_writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> None:
        if not bool(getattr(self.config, "local_getactual_enabled", False)):
            return

        interval_s = self._local_getactual_interval_s()
        while not box_writer.is_closing():
            if id(box_writer) in self._offline_reply_deadlines:
                await asyncio.sleep(interval_s)
                continue
            frame = build_getactual_frame()
            try:
                box_writer.write(frame)
                await box_writer.drain()
            except OSError as exc:
                logger.debug("Local GetActual stopped for %s: %s", peer or "unknown", exc)
                break

            self._capture_frame(frame, "proxy_to_box", conn_id=conn_id, peer=peer)
            if self.telemetry_collector is not None:
                self.telemetry_collector.record_frame_direction("proxy_to_box")
            logger.debug(
                "📤 Sent local GetActual to BOX peer=%s conn_id=%s interval=%ss",
                peer or "unknown",
                conn_id,
                interval_s,
            )
            await asyncio.sleep(interval_s)

    def _claim_cloud_attempt(self, session_id: str) -> bool:
        if not self.mode_manager.should_try_cloud():
            return False
        if self.mode_manager.is_hybrid_mode() and self.mode_manager.is_offline():
            if self._hybrid_probe is not None:
                return False
            self._hybrid_probe = session_id
        return True

    def _release_cloud_probe(self, session_id: str | None) -> None:
        if self._hybrid_probe == session_id:
            self._hybrid_probe = None

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

        self._box_peers[session_id] = peer_str
        box_connected_since_epoch = time.time()
        cloud_connected_since_epoch: float | None = None
        cloud_disconnect_reason = "not_connected"
        box_disconnect_reason = "eof"
        cloud_writer: asyncio.StreamWriter | None = None
        local_getactual_task: asyncio.Task[None] | None = None
        logger.info("📦 BOX připojen z %s:%s (session=%s)", *peer[:2], session_id)

        try:
            if self._claim_cloud_attempt(session_id):
                try:
                    cloud_reader, cloud_writer = await asyncio.wait_for(
                        asyncio.open_connection(self._cloud_ip, self.config.cloud_port),
                        timeout=self.config.cloud_connect_timeout,
                    )
                except OSError as exc:
                    failure_type = "timeout" if isinstance(exc, TimeoutError) else "oserror"
                    if failure_type == "timeout":
                        self.cloud_timeouts += 1
                    else:
                        self.cloud_errors += 1
                    logger.error("Cloud connect failed (%s): %s (session=%s)", failure_type, exc, session_id)
                    self.mode_manager.record_failure(reason=str(exc))
                    fallback = self.mode_manager.is_hybrid_mode() or self.mode_manager.is_offline()
                    self._record_cloud_connect_failure(
                        conn_id=session_conn_id,
                        failure_type=failure_type,
                        failure_detail=str(exc),
                        peer=peer_str,
                        will_go_offline=fallback,
                    )
                    box_disconnect_reason = f"cloud_connect_{failure_type}"
                    if not fallback:
                        return
                    box_disconnect_reason = f"offline_fallback_{failure_type}"
                else:
                    if cloud_reader is None or cloud_writer is None:
                        box_disconnect_reason = "cloud_connect_missing_stream"
                        return
                    logger.info(
                        "☁️ Cloud spojen: %s:%s (session=%s)",
                        self.config.cloud_host, self.config.cloud_port, session_id,
                    )
                    self.cloud_connects += 1
                    self._cloud_sessions.add(session_id)
                    cloud_connected_since_epoch = time.time()
                    cloud_disconnect_reason = "box_closed"
                    local_getactual_task = self._start_local_getactual_task(
                        box_writer, conn_id=session_conn_id, peer=peer_str,
                    )
                    traffic = _SessionTraffic()
                    reply_timeout = (
                        self.config.cloud_ack_timeout if self._hybrid_probe == session_id else None
                    )
                    cloud_reason = await self._run_cloud_pipes(
                        box_reader, box_writer, cloud_reader, cloud_writer, peer, session_id,
                        session_conn_id, traffic, reply_timeout,
                    )
                    if cloud_reason is None:
                        return

                    cloud_disconnect_reason = cloud_reason
                    box_disconnect_reason = f"cloud_{cloud_reason}"
                    upstream_failed = cloud_reason in {"eof", "read_error", "reply_timeout"}
                    if (upstream_failed and not traffic.cloud_verified
                            and (not traffic.started or self.mode_manager.is_hybrid_mode())):
                        phase = "before_valid_reply" if traffic.started else "before_exchange"
                        cloud_disconnect_reason = f"{cloud_reason}_{phase}"
                        if cloud_reason == "reply_timeout":
                            self.cloud_timeouts += 1
                        else:
                            self.cloud_errors += 1
                        self.mode_manager.record_failure(reason=cloud_disconnect_reason)
                    elif cloud_reason == "read_error":
                        self.cloud_errors += 1

                    if (not upstream_failed or traffic.started or box_writer.is_closing()
                            or not self.mode_manager.is_hybrid_mode()):
                        return
                    box_disconnect_reason = f"offline_fallback_{cloud_disconnect_reason}"
                    logger.info(
                        "Cloud disconnected before relaying data; handling BOX locally "
                        "(session=%s, reason=%s, failures=%d)",
                        session_id, cloud_disconnect_reason, self.mode_manager.fail_count,
                    )
                    cloud_writer.close()
                    self._cloud_sessions.discard(session_id)
                    self.cloud_disconnects += 1
                    if self.telemetry_collector is not None:
                        self.telemetry_collector.record_cloud_session_end(
                            connected_since_epoch=cloud_connected_since_epoch,
                            reason=cloud_disconnect_reason,
                        )
                    cloud_connected_since_epoch = None
            else:
                logger.info("☁️ Skipping cloud connection (mode=%s)", self.mode_manager.configured_mode)
                box_disconnect_reason = "offline_mode"
                if self.telemetry_collector is not None:
                    self.telemetry_collector.record_offline_event(
                        reason="configured_offline_mode",
                        local_ack=True,
                        mode=str(self.mode_manager.runtime_mode.value),
                    )

            self._release_cloud_probe(session_id)
            self._offline_writers.add(box_writer)
            if local_getactual_task is None:
                local_getactual_task = self._start_local_getactual_task(
                    box_writer, conn_id=session_conn_id, peer=peer_str,
                )
            await self._pipe_box_offline(box_reader, box_writer, peer, session_id=session_id)
        finally:
            self._release_cloud_probe(session_id)
            self._offline_writers.discard(box_writer)
            await self._stop_local_getactual_task(local_getactual_task)
            if self.twin_delivery is not None:
                self.twin_delivery.clear_session(session_id)
            self._box_devices.pop(session_id, None)
            self._cloud_partial_sessions.discard(session_id)
            if self.telemetry_collector is not None:
                self.telemetry_collector.discard_requests(session_conn_id)
            for writer in (box_writer, cloud_writer):
                if writer is not None and not writer.is_closing():
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError as exc:
                        logger.debug("wait_closed error in pipe cleanup: %s", exc)
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=cloud_connected_since_epoch,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_peers.pop(session_id, None)
            self._cloud_sessions.discard(session_id)
            if cloud_connected_since_epoch is not None:
                self.cloud_disconnects += 1
            if current is not None:
                self._active_connections.discard(current)
            logger.info(
                "🔌 BOX odpojen: %s:%s (session=%s) reason=%s cloud_reason=%s",
                *peer[:2], session_id, box_disconnect_reason, cloud_disconnect_reason,
            )

    async def _run_cloud_pipes(
        self,
        box_reader: asyncio.StreamReader, box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader, cloud_writer: asyncio.StreamWriter,
        peer: tuple, session_id: str, conn_id: int, traffic: _SessionTraffic,
        reply_timeout: float | None,
    ) -> str | None:
        """Return the Cloud closure reason if the Cloud pipe ended first."""
        box_pipe = asyncio.create_task(self._pipe_box_to_cloud(
            box_reader, cloud_writer, box_writer, peer=peer, session_id=session_id, conn_id=conn_id,
            traffic=traffic,
        ))
        cloud_pipe = asyncio.create_task(self._pipe_cloud_to_box(
            cloud_reader, box_writer, peer=peer, session_id=session_id, conn_id=conn_id,
            traffic=traffic, reply_timeout=reply_timeout,
        ))
        try:
            done, _ = await asyncio.wait((box_pipe, cloud_pipe), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (box_pipe, cloud_pipe):
                if not task.done():
                    task.cancel()
            await asyncio.gather(box_pipe, cloud_pipe, return_exceptions=True)

        if box_pipe in done:
            return "write_error" if box_pipe.result() == "cloud_write_error" else None
        return cloud_pipe.result()

    async def _pipe_box_to_cloud(
        self,
        box_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter,
        box_writer: asyncio.StreamWriter | None = None,
        peer: tuple | None = None,
        session_id: str | None = None,
        *,
        conn_id: int | None = None,
        traffic: _SessionTraffic | None = None,
    ) -> str:
        """Forward original bytes, substituting at most one eligible poll in a read."""
        peer_str = f"{peer[0]}:{peer[1]}" if peer and len(peer) >= 2 else None
        pipe_id = id(asyncio.current_task())
        if conn_id is None:
            conn_id = pipe_id
        tail = b""
        prewire_end = 0
        oversized = False
        dropped_delimiter = b""

        def record_local_write() -> None:
            nonlocal prewire_end
            prewire_end = len(data) + _buffered_box_bytes(box_reader)

        while True:
            try:
                # Positive reads return the bytes currently available, subject to
                # StreamReader/transport backpressure; avoid an artificial frame boundary.
                incoming = await box_reader.read(sys.maxsize)
                eof = not incoming
            except asyncio.CancelledError:
                # Cloud may half-close while it can still read the withheld BOX tail.
                if tail and not cloud_writer.is_closing():
                    try:
                        cloud_writer.write(tail)
                    except OSError:
                        pass
                raise
            except asyncio.IncompleteReadError as exc:
                incoming = exc.partial
                eof = True
            except OSError:
                incoming = b""
                eof = True

            if incoming and traffic is not None:
                traffic.box_started = True
            received_at = time.monotonic()
            # The optional line ending belongs to the previous replaced frame, even
            # when either delimiter byte arrives in a later read.
            received_size = len(incoming)
            if dropped_delimiter == b"\r\n" and incoming.startswith(b"\r"):
                incoming = incoming[1:]
                dropped_delimiter = b"\n"
            if dropped_delimiter and incoming:
                incoming = incoming.removeprefix(b"\n")
                dropped_delimiter = b""
            prewire_end = max(0, prewire_end - (received_size - len(incoming)))
            data = tail + incoming
            spans, ready, oversized = _box_frame_spans(data, oversized)
            tail = data[ready:]
            if eof:
                ready = len(data)
            frames = [data[start:end] for start, end in spans]
            poll_candidates = [_may_offer_setting_slot(frame) for frame in frames]
            poll_count = sum(poll_candidates)
            last_reply_start = max((start for frame, (start, _) in zip(frames, spans)
                                    if _may_acknowledge_setting(frame)), default=-1)
            send_from = 0
            local_write_failed = False
            forwarded_frames: list[bytes] = []
            readback_fields: list[dict[str, str]] = []
            for frame, (start, end), poll_candidate in zip(frames, spans, poll_candidates):
                is_poll = box_writer is not None and poll_candidate
                ambiguous = poll_count > 1
                if is_poll and not ambiguous and send_from < start:
                    if not await self._forward_box_bytes(
                        data[send_from:start], forwarded_frames, readback_fields, cloud_writer,
                        session_id, conn_id,
                    ):
                        return "cloud_write_error"
                    send_from = start
                    forwarded_frames.clear()
                    readback_fields.clear()
                fields = await self._observe_box_stream_frame(
                    frame, session_id, pipe_id, peer_str, allow_local_receipt=start >= prewire_end,
                    received_at=received_at,
                )
                if box_writer is not None and is_poll:
                    try:
                        replaced = await self._deliver_pending_for_poll(
                            frame, box_writer, session_id, conn_id=pipe_id, peer=peer_str,
                            transport_block="ambiguous_box_batch" if ambiguous else None,
                            buffered_reply=bool(tail) or start < last_reply_start or bool(_buffered_box_bytes(box_reader)),
                            before_write=record_local_write,
                        )
                    except OSError:
                        # The Box may already have received the Setting. Forward its
                        # neighbors, and leave this poll owned by the local attempt.
                        replaced = local_write_failed = True
                    if replaced:
                        send_from = end
                        if end == len(data) and not frame.endswith(b"\n"):
                            dropped_delimiter = b"\n" if frame.endswith(b"\r") else b"\r\n"
                        continue
                forwarded_frames.append(frame)
                if fields is not None and fields.get("TblName") != "tbl_events":
                    readback_fields.append(fields)
            prewire_end = max(0, prewire_end - ready)
            if not await self._forward_box_bytes(
                data[send_from:len(data) if local_write_failed else ready],
                forwarded_frames, readback_fields, cloud_writer,
                session_id, conn_id,
            ):
                return "cloud_write_error"
            if local_write_failed:
                return "box_write_error"
            if eof:
                self._write_eof(cloud_writer)
                return "box_closed"

    async def _forward_box_bytes(
        self, data: bytes, frames: list[bytes], observed: list[dict[str, str]],
        cloud_writer: asyncio.StreamWriter,
        session_id: str | None, conn_id: int,
    ) -> bool:
        if not data:
            return True
        delivery = self.twin_delivery
        try:
            self._record_cloud_requests(frames, conn_id)
            cloud_writer.write(data)
            forwarded = data.strip(b"\r\n")
            if delivery is not None and self._bound_box_device(session_id) is not None and forwarded:
                poll = frames[0] if (len(frames) == 1 and session_id not in self._cloud_partial_sessions
                                     and not cloud_writer.is_closing()
                                     and forwarded == frames[0].rstrip(b"\r\n")) else None
                # The response can arrive while drain yields to the Cloud pipe.
                delivery.note_forwarded_clock_poll(poll, session_id)
            await cloud_writer.drain()
        except OSError as exc:
            if delivery is not None:
                delivery.note_forwarded_clock_poll(None, session_id)
            self.cloud_errors += 1
            self.mode_manager.record_failure(reason="cloud_write_error")
            logger.info("Cloud write failed after BOX traffic: %s (session=%s)", exc, session_id)
            return False
        self.frames_forwarded += len(frames)
        for fields in observed:
            self._observe_parameter_readback(fields, session_id)
        return True

    async def _observe_box_stream_frame(
        self, frame: bytes, session_id: str | None, conn_id: int, peer: str | None,
        *, allow_local_receipt: bool, received_at: float,
    ) -> dict[str, str] | None:
        self._capture_frame(frame, "box_to_cloud", conn_id=conn_id, peer=peer)
        fields = await self._process_frame(frame)
        await self._handle_twin_frames(
            frame, session_id, allow_local_receipt=allow_local_receipt, received_at=received_at,
        )
        if fields is not None and fields.get("TblName") == "tbl_events":
            self._observe_parameter_readback(fields, session_id)
        if self.telemetry_collector is not None:
            self.telemetry_collector.record_frame_direction("box_to_proxy")
        return fields

    def _record_cloud_requests(self, frames: list[bytes], conn_id: int) -> None:
        collector = self.telemetry_collector
        if collector is None:
            return
        for frame in frames:
            text = frame.decode("utf-8", errors="replace")
            table = self._effective_table_name(parse_xml_frame(text), text)
            collector.record_request(table or None, conn_id)

    @staticmethod
    def _write_eof(writer: asyncio.StreamWriter) -> None:
        """Forward a peer half-close when the transport supports it."""
        try:
            can_write_eof = getattr(writer, "can_write_eof", None)
            if callable(can_write_eof) and can_write_eof():
                writer.write_eof()
        except OSError as exc:
            logger.debug("EOF forwarding failed: %s", type(exc).__name__)

    async def _pipe_cloud_to_box(
        self,
        cloud_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        peer: tuple | None = None,
        session_id: str | None = None,
        *,
        conn_id: int | None = None,
        traffic: _SessionTraffic | None = None,
        reply_timeout: float | None = None,
    ) -> str:
        """Forward unchanged cloud bytes; no private transform or injection path."""
        peer_str = f"{peer[0]}:{peer[1]}" if peer and len(peer) >= 2 else None
        if conn_id is None:
            conn_id = id(asyncio.current_task())
        buf = bytearray()
        deadline = None if reply_timeout is None else time.monotonic() + reply_timeout
        while True:
            try:
                remaining = None if deadline is None else deadline - time.monotonic()
                data = await asyncio.wait_for(cloud_reader.read(4096), remaining)
            except asyncio.TimeoutError:
                return "reply_timeout"
            except (asyncio.IncompleteReadError, OSError):
                return "read_error"
            if not data:
                # The connection handler can still reuse an untouched BOX socket.
                if traffic is None or traffic.started:
                    self._write_eof(box_writer)
                return "eof"
            if traffic is not None:
                traffic.cloud_started = True
            verify_response = (
                traffic is not None and self.mode_manager.is_hybrid_mode()
                and (not traffic.cloud_verified or self.mode_manager.fail_count > 0)
            )
            try:
                box_writer.write(data)
                buf.extend(data)
                self._cloud_partial_sessions.add(session_id)
                verified = self._observe_cloud_buffer(
                    buf, peer_str, session_id, conn_id, verify_response=verify_response,
                )
                if not buf:
                    self._cloud_partial_sessions.discard(session_id)
                remaining = None if deadline is None else deadline - time.monotonic()
                await asyncio.wait_for(box_writer.drain(), remaining)
            except (asyncio.TimeoutError, OSError):
                return "box_write_error"
            if verified and traffic is not None:
                traffic.cloud_verified = True
                deadline = None
                self._release_cloud_probe(session_id)
                self.mode_manager.record_success()
                self._close_idle_offline_connections()

    def _observe_cloud_buffer(
        self, buf: bytearray,
        peer: str | None, session_id: str | None, conn_id: int,
        *, verify_response: bool = False,
    ) -> bool:
        """Parse only complete output frames without changing their byte order."""
        verified = False
        while True:
            # A TCP read can split the delimiter after a complete XML frame.
            buf[:] = buf.lstrip(b"\r\n")
            frame = extract_frame_from_buffer(buf)
            if frame is None:
                return verified
            if verify_response and not verified:
                verified = native_frame_fields(frame, allow_repeated_identity=False) is not None
            self._observe_cloud_frame(frame, peer, session_id, conn_id)

    def _bound_box_device(self, session_id: str | None) -> str | None:
        if session_id is None or self._device_id_provider is None:
            return None
        bound = self._box_devices.get(session_id)
        return bound if bound == self._device_id_provider() else None

    def _observe_box_identity(self, fields: dict[str, str], session_id: str) -> str | None:
        """Bind matching BOX identity from native evidence on this connection."""
        device_id = fields.get("ID_Device")
        bound = self._bound_box_device(session_id)
        if device_id is None:
            return bound if fields.get("Result") in {"ACK", "NACK"} else None
        expected = self._device_id_provider() if self._device_id_provider is not None else None
        if expected is None:
            return None
        if device_id != expected:
            self._box_devices[session_id] = None
            return None
        if session_id not in self._box_devices and fields.get("Result") not in {"ACK", "NACK", "END"}:
            self._box_devices[session_id] = device_id
            return device_id
        return bound

    def _observe_cloud_frame(
        self, frame_bytes: bytes, peer: str | None, session_id: str | None, conn_id: int,
    ) -> None:
        """Observe the exact complete frame submitted to the BOX socket."""
        self._capture_frame(frame_bytes, "cloud_to_box", conn_id=id(asyncio.current_task()), peer=peer)
        frame_text = frame_bytes.decode("utf-8", errors="replace")
        self.frames_received += 1
        if self.telemetry_collector is not None:
            self.telemetry_collector.record_response(frame_text, source="cloud", conn_id=conn_id)
            self.telemetry_collector.record_frame_direction("cloud_to_proxy")

        delivery = self.twin_delivery
        device_id = self._bound_box_device(session_id)
        if delivery is None or session_id is None or device_id is None:
            return
        fields = native_frame_fields(frame_bytes, allow_repeated_identity=False)
        delivery.observe_cloud_profile(frame_bytes, session_id, device_id)
        if fields is None or fields.get("ID_Device", device_id) != device_id:
            return
        if fields.get("Reason") == "Setting":
            self._track_cloud_setting(delivery, fields, frame_text, device_id, session_id)
        elif fields.get("Result") == "END":
            delivery.clear_cloud_inflight(session_id)

    @staticmethod
    def _track_cloud_setting(
        delivery: TwinDelivery, fields: dict[str, str], frame_text: str,
        device_id: str, session_id: str,
    ) -> None:
        table, key, value = fields.get("TblName"), fields.get("TblItem"), fields.get("NewValue")
        if not table or not key or value is None or fields.get("ID_Device") != device_id:
            delivery.set_cloud_inflight(session_id)
            return
        delivery.begin_cloud_setting(
            device_id=device_id, table=table, key=key, value=value, raw_text=frame_text,
            msg_id=ProxyServer._wire_counter(fields.get("ID", "")),
            id_set=ProxyServer._wire_counter(fields.get("ID_Set", "")),
            confirm=fields.get("Confirm", "New"), session_id=session_id,
        )

    @staticmethod
    def _wire_counter(value: str) -> int:
        return int(value) if len(value) <= 10 and value.isascii() and value.isdecimal() else 0

    async def _handle_twin_frames(
        self, frame_bytes: bytes, session_id: str | None, *, allow_local_receipt: bool = True,
        received_at: float | None = None,
    ) -> str | None:
        delivery = self.twin_delivery
        if delivery is None or session_id is None:
            return None
        fields = native_frame_fields(frame_bytes)
        if fields is None:
            return None
        device_id = self._observe_box_identity(fields, session_id)
        if device_id is None:
            return None

        counter = delivery.observe_box_profile(frame_bytes, session_id, device_id)
        if counter is not None:
            await self._observe_box_event(
                delivery, fields, device_id, session_id, counter, allow_local_receipt=allow_local_receipt,
                received_at=received_at,
            )
        result = fields.get("Result")
        if result not in {"ACK", "NACK"}:
            return None
        receipt = native_receipt_fields(frame_bytes)
        if receipt is None:
            return None
        if result == "ACK" and receipt.get("Reason") == "Setting":
            acknowledged = delivery.mark_cloud_reason_setting(
                device_id, session_id=session_id, reply_fields=receipt,
            )
            if acknowledged is not None:
                setting, _ = acknowledged
                self._note_ack_readback(delivery, setting, device_id, receipt.get("Rdt", ""))
                return None
        elif result == "NACK" and delivery.acknowledge_cloud(
            device_id, session_id=session_id, result="nack", evidence="protocol_nack", reply_fields=receipt,
        ):
            return None
        return self._accept_local_reply(delivery, frame_bytes, device_id, session_id) if allow_local_receipt else None

    async def _observe_box_event(
        self, delivery: TwinDelivery, fields: dict[str, str], device_id: str, session_id: str,
        counter: int, *, allow_local_receipt: bool, received_at: float | None,
    ) -> None:
        event = parse_tbl_events_ack({**fields, "_table": fields.get("TblName")})
        source_dt = fields.get("DT")
        if event is not None:
            table, key, value = event["table"], event["key"], event["value"]
            delivery.match_cloud_tbl_events(
                device_id, table, key, value, session_id=session_id,
                source_dt=source_dt, event_id_set=counter, previous_value=event["previous_value"],
            )
            if allow_local_receipt:
                delivery.acknowledge_local_event(
                    device_id, table, key, value, session_id=session_id,
                    source_dt=source_dt, event_id_set=counter, previous_value=event["previous_value"],
                )
        # A matching event cannot supply its own pre-wire counter baseline.
        delivery.observe_box_frame(device_id, counter, source_dt, session_id=session_id, received_at=received_at)
        if event is not None:
            await self._publish_confirmed_setting(device_id, table, key, value, source_dt=source_dt)

    def _accept_local_reply(
        self, delivery: TwinDelivery, frame_bytes: bytes, device_id: str, session_id: str,
    ) -> str | None:
        reply = delivery.local_protocol_reply(frame_bytes, session_id)
        if reply is None:
            return None
        if reply.get("event_confirmed") == "true":
            return "ack"
        pair = delivery.local_inflight_setting(session_id=session_id)
        if pair is None or pair[1] != device_id:
            return None
        setting, _ = pair
        if reply["result"] == "NACK":
            if delivery.acknowledge(
                setting.table, setting.key, session_id=session_id,
                result="nack", evidence="protocol_nack",
            ):
                return "nack"
        elif delivery.acknowledge(
            setting.table, setting.key, session_id=session_id, receipt_dt=reply.get("rdt"),
            receipt_echoed=reply.get("wire_echo") == "true",
        ):
            self._note_ack_readback(delivery, setting, device_id, reply.get("rdt", ""))
            if reply.get("todo"):
                delivery.record_ack_box_observed(setting, device_id, session_id=session_id)
            else:
                delivery.record_ack_reason_setting(setting, device_id, session_id=session_id, terminal=False)
            return "ack"
        return None

    def _note_ack_readback(
        self, delivery: TwinDelivery, setting: TwinSetting, device_id: str, received_dt: str,
    ) -> None:
        if self._request_readback is not None and not self.mode_manager.is_offline():
            self._readback.note_ack(
                setting.table, setting.key, setting.value,
                box_time=delivery.box_readback_time(device_id, received_dt),
            )

    async def _publish_confirmed_setting(
        self,
        device_id: str | None,
        table: str,
        key: str,
        value: Any,
        source_dt: str | None = None,
    ) -> None:
        if self.on_confirmed_setting is None or not device_id:
            return
        try:
            if source_dt is None:
                await self.on_confirmed_setting(device_id, table, key, value)
            else:
                await self.on_confirmed_setting(device_id, table, key, value, source_dt=source_dt)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Confirmed setting publish failed for %s:%s=%s: %s",
                table,
                key,
                value,
                exc,
            )

    async def _deliver_pending_for_poll(
        self,
        poll: bytes,
        box_writer: asyncio.StreamWriter,
        session_id: str | None,
        *,
        conn_id: int | None = None,
        peer: str | None = None,
        offline: bool = False,
        transport_block: str | None = None,
        buffered_reply: bool = False,
        before_write: Callable[[], None] | None = None,
    ) -> bool:
        """Use a qualified BOX poll slot for exactly one local Setting."""
        delivery = self.twin_delivery
        device_id = self._bound_box_device(session_id)
        writer_id = id(box_writer)
        if delivery is None:
            return False
        if device_id is None:
            expected = self._device_id_provider() if self._device_id_provider is not None else None
            if expected is not None and delivery.has_pending(expected):
                reason = "poll_malformed" if native_frame_fields(poll) is None else "box_identity_unbound"
                delivery.record_poll_skip(expected, session_id, reason, stage="transport")
            return False
        if box_writer.is_closing():
            transport_block = "box_socket_closing"
        elif offline and writer_id in self._offline_reply_deadlines:
            transport_block = "offline_exchange_active"
        elif offline and not self.mode_manager.is_offline():
            transport_block = "offline_mode_changed"
        elif not offline and session_id is not None and session_id == self._hybrid_probe:
            transport_block = "cloud_recovery_probe"
        elif any(self._bound_box_device(flow) == device_id for flow in self._cloud_partial_sessions):
            transport_block = "cloud_partial_frame"
        if not delivery.valid_local_poll(poll, device_id):
            delivery.record_poll_skip(device_id, session_id, "poll_unqualified", stage="transport")
            return False
        if transport_block is not None:
            delivery.record_poll_skip(device_id, session_id, transport_block, stage="transport")
            return False
        request_readback = self._request_readback
        automatic = not offline and request_readback is not None and self._readback.is_due()
        admitted: TwinSetting | None = None

        def admit_readback() -> bool:
            nonlocal admitted
            if request_readback is not None and self._readback.is_due():
                admitted = request_readback()
            return admitted is not None

        try:
            pending = await delivery.deliver_pending(
                device_id, session_id=session_id, admit_if_idle=admit_readback if automatic else None,
                online_poll=poll.rstrip(b"\r\n") if not offline else None,
                buffered_reply=buffered_reply,
            )
            if not pending:
                return False
            setting = pending[0]
            frame = delivery.build_local_frame(setting, session_id)
            delivery.bind_local_wire(setting, frame, session_id=session_id, retain_receipt=offline)
        except ValueError:
            delivery.record_poll_skip(device_id, session_id, "local_wire_invalid", stage="transport")
            return False
        finally:
            if admitted is not None:
                delivery.discard_unsent(admitted, "readback_send_aborted")
        if before_write is not None:
            before_write()
        box_writer.write(frame)
        delivery.record_injected_box(setting, device_id, session_id=session_id or "")
        if (not offline and self._readback.is_due()
                and (setting.table, setting.key, setting.value) == ("tbl_box_prms", "SA", 1)):
            self._readback.mark_sent()
        self._capture_frame(frame, "proxy_to_box", conn_id=conn_id, peer=peer)
        deadline = delivery.local_wire_deadline(session_id) if offline else None
        if deadline is None:
            await box_writer.drain()
        else:
            self._offline_reply_deadlines[writer_id] = deadline
            await asyncio.wait_for(box_writer.drain(), max(0, deadline - time.monotonic()))
        return True

    def poll_readback_timeouts(self) -> None:
        """Expire outstanding synchronization reads while the BOX is quiet."""
        self._readback.poll_timeouts()

    def _observe_parameter_readback(self, fields: dict[str, str], session_id: str | None) -> None:
        device_id = self._bound_box_device(session_id)
        delivery = self.twin_delivery
        if (self._request_readback is not None and delivery is not None and device_id
                and fields.get("ID_Device") == device_id
                and fields.get("ID_SubD", "0") == "0"):
            self._readback.observe(fields, box_time=delivery.box_readback_time(device_id, fields.get("DT", "")))

    async def _process_frame(self, frame_bytes: bytes) -> dict[str, str] | None:
        """Publish only native BOX frames whose outer checksum validates."""
        self.frames_received += 1
        if self.on_frame is None and self._request_readback is None:
            return None
        fields = native_frame_fields(frame_bytes)
        if fields is None:
            return None
        try:
            if self.on_frame is None:
                return fields
            text = frame_bytes.decode("utf-8", errors="replace")
            parsed = parse_xml_frame(text)
            if fields.get("Result") == "ACK" and fields.get("Reason") == "Table":
                receipt = native_receipt_fields(frame_bytes)
                if receipt is not None and receipt.get("Reason") == "Setting":
                    # A qualified combined envelope carries an independent actual table.
                    parsed.pop("Result", None)
                    parsed.pop("Rdt", None)
            if parsed and not self._is_transport_frame(parsed):
                await self.on_frame(parsed)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Frame parse error: %s", exc)
        return fields

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
        if result in BOX_POLL_RESULTS | TRANSPORT_RESULT_VALUES:
            return result
        return str(parsed.get("_table") or infer_table_name(payload) or "")

    @staticmethod
    def _is_transport_frame(parsed: dict[str, Any]) -> bool:
        result = str(parsed.get("Result") or "")
        if result in TRANSPORT_RESULT_VALUES:
            return True

        if result in BOX_POLL_RESULTS:
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
        """Read one offline BOX socket until EOF or its Setting exchange ends."""
        logger.info("OFFLINE mode: handling Box connection from %s:%s (session=%s)", *peer[:2], session_id)
        conn_id = id(asyncio.current_task())
        peer_str = f"{peer[0]}:{peer[1]}"
        buf = bytearray()
        try:
            while True:
                deadline = self._offline_reply_deadlines.get(id(box_writer))
                if deadline is None:
                    data = await box_reader.read(4096)
                else:
                    data = await asyncio.wait_for(box_reader.read(4096), max(0, deadline - time.monotonic()))
                if not data:
                    break
                received_at = time.monotonic()
                buf.extend(data)
                if await self._handle_offline_frames(
                    buf, box_writer, session_id, conn_id=conn_id, peer=peer_str, received_at=received_at,
                ):
                    break
        except asyncio.TimeoutError:
            logger.warning("Offline Setting reply timed out (session=%s)", session_id)
            if self.twin_delivery is not None:
                self.twin_delivery.poll_timeouts()
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass
        finally:
            deadline = self._offline_reply_deadlines.pop(id(box_writer), None)
            box_writer.close()
            close_timeout = None if deadline is None else max(0, deadline - time.monotonic())
            try:
                await asyncio.wait_for(box_writer.wait_closed(), close_timeout)
            except asyncio.TimeoutError:
                box_writer.transport.abort()
            except Exception as exc:  # noqa: BLE001
                logger.debug("wait_closed error in offline pipe: %s", exc)
            logger.info("BOX disconnected (offline): %s:%s", *peer[:2])

    async def _handle_offline_frames(
        self,
        buf: bytearray,
        box_writer: asyncio.StreamWriter,
        session_id: str | None = None,
        *,
        conn_id: int | None = None,
        peer: str | None = None,
        received_at: float | None = None,
    ) -> bool:
        """Process offline frames and close the socket after a terminal Setting response."""
        while True:
            buf[:] = buf.lstrip(b"\r\n")
            frame_bytes = extract_frame_from_buffer(buf)
            if frame_bytes is None:
                return False
            text = frame_bytes.decode("utf-8", errors="replace")
            parsed = parse_xml_frame(text)
            table = self._effective_table_name(parsed, text)
            self._capture_frame(frame_bytes, "box_to_proxy", conn_id=conn_id, peer=peer)
            if box_writer.is_closing():
                return True
            deadline = self._offline_reply_deadlines.get(id(box_writer))
            if deadline is None:
                await self._process_frame(frame_bytes)
                await self._handle_twin_frames(frame_bytes, session_id, received_at=received_at)
                # A reply buffered before the Setting cannot acknowledge it.
                sent = (table in BOX_POLL_RESULTS and not buf.strip()
                        and await self._deliver_pending_for_poll(
                            frame_bytes, box_writer, session_id, conn_id=conn_id, peer=peer, offline=True,
                        ))
                if not sent and not box_writer.is_closing():
                    ack = build_local_ack(table)
                    box_writer.write(ack)
                    self._capture_frame(ack, "proxy_to_box", conn_id=conn_id, peer=peer)
                    await box_writer.drain()
                continue

            # Keep the existing terminal deadline through receipt processing and END drain.
            remaining = max(0, deadline - time.monotonic())
            reply = await asyncio.wait_for(
                self._handle_twin_frames(frame_bytes, session_id, received_at=received_at), remaining,
            )
            if reply == "ack":
                end = build_end_time_frame(getactual=False)
                box_writer.write(end)
                self._capture_frame(end, "proxy_to_box", conn_id=conn_id, peer=peer)
                await asyncio.wait_for(box_writer.drain(), max(0, deadline - time.monotonic()))
                await asyncio.wait_for(self._process_frame(frame_bytes), max(0, deadline - time.monotonic()))
                return True
            if reply == "nack":
                return True
            await asyncio.wait_for(self._process_frame(frame_bytes), max(0, deadline - time.monotonic()))
