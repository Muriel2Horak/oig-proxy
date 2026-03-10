#!/usr/bin/env python3
"""
OIG Proxy - hlavní orchestrace s ONLINE/HYBRID/OFFLINE režimy.

Modes:
- ONLINE (default): Transparent forward, no local ACK, no health checks
- HYBRID: Smart fallback with timeout-based offline detection
- OFFLINE: Always local ACK, never connects to cloud
"""

# pylint: disable=too-many-lines,too-many-instance-attributes,too-many-statements,too-many-branches,too-many-locals
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-return-statements,broad-exception-caught
# pylint: disable=deprecated-module

import asyncio
import hashlib
import logging
import socket
import time
from contextlib import suppress
from typing import Any

from oig_parser import OIGDataParser
from cloud_forwarder import CloudForwarder
from control_settings import ControlSettings
from control_pipeline import ControlPipeline
from mode_persistence import ModePersistence
from proxy_status import ProxyStatusReporter
from config import (
    CONTROL_API_HOST,
    CONTROL_API_PORT,
    LOCAL_GETACTUAL_ENABLED,
    LOCAL_GETACTUAL_INTERVAL_S,
    FULL_REFRESH_INTERVAL_H,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    PROXY_STATUS_ATTRS_TOPIC,
    TELEMETRY_ENABLED,
    TELEMETRY_INTERVAL_S,
    TWIN_ENABLED,
    TWIN_KILL_SWITCH,
    LOCAL_CONTROL_ROUTING,
    LEGACY_FALLBACK,
)
from control_api import ControlAPIServer
from mqtt_state_cache import MqttStateCache
from telemetry_collector import TelemetryCollector
from telemetry_tap import TelemetryTap
from hybrid_mode import HybridModeManager
from sidecar_orchestrator import (
    ISidecarOrchestrator,
    NoOpSidecarOrchestrator,
    ProxySidecarAdapter,
    SidecarOrchestrator,
)
from oig_frame import (
    build_end_time_frame,
    build_getactual_frame,
    build_offline_ack_frame,
    infer_device_id,
    infer_table_name,
)
from correlation_id import (
    correlation_id_context_frame,
    get_correlation_id,
    set_correlation_id,
    clear_correlation_id,
)
from models import ProxyMode
from digital_twin import DigitalTwin, DigitalTwinConfig, TwinMQTTHandler
from mqtt_publisher import MQTTPublisher
from twin_state import OnAckDTO, OnDisconnectDTO, OnTblEventDTO
from utils import (
    capture_payload,
)

logger = logging.getLogger(__name__)


def _box_session_id(peer: Any) -> str:
    if isinstance(peer, tuple) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    if peer is None:
        return "unknown"
    return str(peer)


class _TelemetryLogHandler(logging.Handler):
    def __init__(self, proxy: "OIGProxy") -> None:
        super().__init__()
        self._proxy = proxy

    def emit(self, record: logging.LogRecord) -> None:
        # pylint: disable=protected-access
        self._proxy._tc.record_log_entry(record)


# ============================================================================
# OIG Proxy - hlavní proxy server
# ============================================================================

class OIGProxy:
    """OIG Proxy s podporou ONLINE/HYBRID/OFFLINE režimů."""

    def __init__(self, device_id: str):
        self.device_id = device_id

        # Komponenty
        self.mqtt_publisher = MQTTPublisher(device_id)
        self.parser = OIGDataParser()
        self._mp = ModePersistence(self)
        self._msc = MqttStateCache(self)

        # Proxy mode – hybrid state machine
        self._hm = HybridModeManager(self)

        # Background tasky
        self._status_task: asyncio.Task[Any] | None = None
        self._box_conn_lock = asyncio.Lock()
        self._active_box_writer: asyncio.StreamWriter | None = None
        self._active_box_peer: str | None = None
        self._conn_seq: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._control_api: ControlAPIServer | None = None
        self._cs = ControlSettings(self)
        self._ctrl = ControlPipeline(self)
        self._proxy_status_attrs_topic: str = str(PROXY_STATUS_ATTRS_TOPIC)
        self._ps = ProxyStatusReporter(self)
        self._local_getactual_enabled: bool = bool(LOCAL_GETACTUAL_ENABLED)
        self._local_getactual_interval_s: float = float(
            LOCAL_GETACTUAL_INTERVAL_S)
        self._local_getactual_task: asyncio.Task[Any] | None = None
        self._full_refresh_interval_h: int = int(FULL_REFRESH_INTERVAL_H)
        self._full_refresh_task: asyncio.Task[Any] | None = None

        # Telemetry to diagnostic server (muriel-cz.cz)
        self._telemetry_task: asyncio.Task[Any] | None = None
        self._start_time: float = time.time()
        # prevent task GC
        self._background_tasks: set[asyncio.Task[Any]] = set()
        # TelemetryTap for non-blocking telemetry publish (Task 8)
        self._telemetry_tap = TelemetryTap(
            background_tasks=self._background_tasks,
        )
        self._tc = TelemetryCollector(
            self,
            interval_s=TELEMETRY_INTERVAL_S,
            telemetry_tap=self._telemetry_tap,
        )
        # Create copy for safe iteration during modification
        for handler in list(logger.handlers):  # noqa: C417
            if isinstance(handler, _TelemetryLogHandler):
                logger.removeHandler(handler)
        self._telemetry_log_handler = _TelemetryLogHandler(self)
        logger.addHandler(self._telemetry_log_handler)

        self._box_connected_since_epoch: float | None = None
        self._last_box_disconnect_reason: str | None = None

        # Cloud forwarder
        self._cf = CloudForwarder(self)

        # Digital twin for local control routing
        self._twin_kill_switch: bool = bool(TWIN_KILL_SWITCH)
        self._twin_enabled: bool = bool(TWIN_ENABLED) and not self._twin_kill_switch
        self._local_control_routing: str = str(LOCAL_CONTROL_ROUTING)
        self._legacy_fallback_enabled: bool = bool(LEGACY_FALLBACK)
        twin_config = DigitalTwinConfig(device_id=device_id)
        self._twin: DigitalTwin | None = (
            DigitalTwin(config=twin_config) if self._twin_enabled else None
        )
        self._twin_mqtt_handler: TwinMQTTHandler | None = (
            TwinMQTTHandler(twin=self._twin, mqtt_publisher=self.mqtt_publisher)
            if self._twin is not None
            else None
        )
        self._install_twin_mqtt_on_connect_hook()
        self._pending_twin_activation: bool = False
        self._pending_twin_activation_since: float | None = None
        self._twin_mode_active: bool = False
        self._install_twin_mqtt_activation_hook()

        # Sidecar orchestrator for activation/deactivation (Task 9)
        # Uses composition to separate activation logic from frame forwarding
        self._sidecar_adapter: ProxySidecarAdapter | None = None
        if self._twin_enabled:
            self._sidecar_adapter = ProxySidecarAdapter(
                proxy=self,
                orchestrator=SidecarOrchestrator(),
            )

        # Statistiky
        self.stats = {
            "frames_received": 0,
            "frames_forwarded": 0,
            "acks_local": 0,
            "acks_cloud": 0,
            "mode_changes": 0,
        }
        self.box_connected = False
        self.box_connections = 0
        self._last_data_iso: str | None = None
        self._last_data_epoch: float | None = None
        # IsNewSet/Weather/FW telemetry
        self._isnew_polls = 0
        self._isnew_last_poll_iso: str | None = None
        self._isnew_last_response: str | None = None
        self._isnew_last_rtt_ms: float | None = None
        self._isnew_last_poll_epoch: float | None = None

        self._stale_table_name: str | None = None
        self._stale_payload_hash: str | None = None
        self._stale_first_seen_epoch: float | None = None
        self._stale_last_seen_epoch: float | None = None
        self._stale_repeat_count: int = 0
        self._stale_last_log_epoch: float | None = None

    def _initialize_control_api(self) -> None:
        if not (CONTROL_API_PORT and CONTROL_API_PORT > 0):
            return
        try:
            self._control_api = ControlAPIServer(
                host=CONTROL_API_HOST,
                port=CONTROL_API_PORT,
                proxy=self,
            )
            self._control_api.start()
            logger.info(
                "🧪 Control API listening on http://%s:%s",
                CONTROL_API_HOST,
                CONTROL_API_PORT,
            )
        except Exception as e:
            logger.error("Control API start failed: %s", e)

    async def _initialize_mqtt(self) -> None:
        connected = await asyncio.to_thread(self.mqtt_publisher.connect)
        if connected:
            await self.mqtt_publisher.start_health_check()
        else:
            logger.warning(
                "MQTT: Initial connect failed, health check will retry reconnect")
            await self.mqtt_publisher.start_health_check()

        if self._twin_mqtt_handler is not None and self._loop is not None:
            self._twin_mqtt_handler.setup_mqtt(self._loop)
        self._msc.setup()

    def _install_twin_mqtt_on_connect_hook(self) -> None:
        twin = self._twin
        if twin is None:
            return

        def _on_mqtt_connected() -> None:
            if self._loop is None:
                return
            fut = asyncio.run_coroutine_threadsafe(
                twin.publish_initial_state(),
                self._loop,
            )

            def _consume(_fut: Any) -> None:
                try:
                    _fut.result()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.debug("TWIN: publish_initial_state on connect failed: %s", exc)

            fut.add_done_callback(_consume)

        self.mqtt_publisher.add_on_connect_handler(_on_mqtt_connected)

    def _install_twin_mqtt_activation_hook(self) -> None:
        if self._twin_mqtt_handler is None:
            return

        original_on_mqtt_message = self._twin_mqtt_handler.on_mqtt_message

        async def _on_mqtt_message_with_activation(
            *, topic: str, payload: bytes
        ) -> None:
            await original_on_mqtt_message(topic=topic, payload=payload)

            if await self._hm.get_current_mode() != ProxyMode.ONLINE:
                return

            if not await self._queue_has_items():
                return

            self._pending_twin_activation = True
            self._pending_twin_activation_since = time.time()
            logger.info(
                "TWIN: Pending session activation armed (ONLINE mode, mqtt topic=%s)",
                topic,
            )

        self._twin_mqtt_handler.on_mqtt_message = _on_mqtt_message_with_activation

    async def _queue_has_items(self) -> bool:
        if self._twin is None:
            return False
        return (await self._twin.get_queue_length()) > 0

    async def _maybe_expire_pending_twin_activation(self) -> None:
        """Clear pending twin activation if idle for too long (Task 5)."""
        if not self._pending_twin_activation:
            return
        if self._twin is None:
            return
        # Don't expire if queue has items or inflight exists
        if await self._queue_has_items():
            return
        inflight = await self._twin.get_inflight()
        if inflight is not None:
            return
        # Check if pending has been idle for timeout period (60s)
        pending_activation_timeout_s = 60.0
        if self._pending_twin_activation_since is None:
            return
        elapsed = time.time() - self._pending_twin_activation_since
        if elapsed >= pending_activation_timeout_s:
            self._pending_twin_activation = False
            self._pending_twin_activation_since = None
            logger.info(
                "TWIN: Pending activation expired after %.1fs (queue=0, inflight=None)",
                elapsed,
            )

    def _restore_device_id(self) -> None:
        if self.device_id != "AUTO":
            return
        restored_device_id = self._mp.mode_device_id or self._mp.prms_device_id
        if not restored_device_id:
            return
        self.device_id = restored_device_id
        self.mqtt_publisher.device_id = restored_device_id
        twin = getattr(self, "_twin", None)
        if twin is not None and hasattr(twin, "config"):
            twin.config.device_id = restored_device_id
        logger.info(
            "🔑 Restoring device_id from saved state: %s",
            self.device_id,
        )
        self.mqtt_publisher.publish_availability()
        self._msc.setup()

    def _start_background_tasks(self) -> None:
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._ps.status_loop())
        if self._full_refresh_task is None or self._full_refresh_task.done():
            self._full_refresh_task = asyncio.create_task(
                self._full_refresh_loop())

        if not TELEMETRY_ENABLED:
            return
        self._tc.init()
        if self._telemetry_task is None or self._telemetry_task.done():
            self._telemetry_task = asyncio.create_task(
                self._tc.loop())

    async def _start_tcp_server(self) -> None:
        server = await asyncio.start_server(
            self.handle_connection,
            PROXY_LISTEN_HOST,
            PROXY_LISTEN_PORT
        )

        addr = server.sockets[0].getsockname()
        logger.info("🚀 OIG Proxy listening on %s:%s", addr[0], addr[1])
        logger.info("🔄 Mode: %s", self._hm.mode.value)

        async with server:
            await server.serve_forever()

    async def start(self):
        """Spustí proxy server."""
        self._loop = asyncio.get_running_loop()
        self.mqtt_publisher.attach_loop(self._loop)
        self._telemetry_tap.attach_loop(self._loop)

        self._initialize_control_api()
        await self._initialize_mqtt()
        self._restore_device_id()

        logger.info(
            "🚀 Proxy mode: %s (configured: %s)",
            self._hm.mode.value,
            self._hm.configured_mode,
        )

        await self.publish_proxy_status()
        self._ps.mqtt_was_ready = self.mqtt_publisher.is_ready()
        self._start_background_tasks()

        await self._start_tcp_server()

    async def publish_proxy_status(self) -> None:
        """Delegate to ProxyStatusReporter."""
        await self._ps.publish()

    def _record_proxy_to_box_frame(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        conn_id: int,
        source: str,
        tx_id: str | None = None,
    ) -> None:
        frame_text = frame_bytes.decode("utf-8", errors="replace")
        # Task 15: Include correlation ID in frame recording
        cid = get_correlation_id()
        capture_payload(
            self.device_id,
            table_name,
            frame_text,
            frame_bytes,
            {},
            direction="proxy_to_box",
            length=len(frame_bytes),
            conn_id=conn_id,
            peer=self._active_box_peer,
            correlation_id=cid,
        )
        self._tc.record_response(
            frame_text,
            source=source,
            conn_id=conn_id,
            correlation_id=cid,
        )
        logger.info(
            "PROXY_TO_BOX: source=%s table=%s conn=%s tx_id=%s cid=%s frame=%s",
            source,
            table_name,
            conn_id,
            tx_id or "-",
            cid,
            frame_text,
        )

    async def _send_getactual_to_box(
        self, writer: asyncio.StreamWriter, *, conn_id: int
    ) -> None:
        frame_bytes = build_getactual_frame()
        writer.write(frame_bytes)
        await writer.drain()
        capture_payload(
            None,
            "GetActual",
            frame_bytes.decode("utf-8", errors="replace"),
            frame_bytes,
            {},
            direction="proxy_to_box",
            length=len(frame_bytes),
            conn_id=conn_id,
            peer=self._active_box_peer,
        )

    async def _local_getactual_loop(
        self, writer: asyncio.StreamWriter, *, conn_id: int
    ) -> None:
        if not self._local_getactual_enabled:
            return
        while True:
            if writer.is_closing():
                return
            if not self.box_connected:
                await asyncio.sleep(self._local_getactual_interval_s)
                continue
            try:
                await self._send_getactual_to_box(writer, conn_id=conn_id)
            except Exception as e:
                logger.debug("GetActual poll failed (conn=%s): %s", conn_id, e)
            await asyncio.sleep(self._local_getactual_interval_s)

    async def _full_refresh_loop(self) -> None:
        interval_s = max(1, int(self._full_refresh_interval_h)) * 3600
        while True:
            await asyncio.sleep(interval_s)
            if self._hm.force_offline_enabled():
                continue
            if not self.box_connected:
                continue
            if await self._hm.get_current_mode() != ProxyMode.ONLINE:
                continue
            if self._twin is not None:
                if await self._queue_has_items():
                    continue
                if await self._twin.get_inflight() is not None:
                    continue
            try:
                logger.info("CONTROL: Full refresh (SA) requested")
                await self._cs.queue_setting(
                    tbl_name="tbl_box_prms",
                    tbl_item="SA",
                    new_value="1",
                    confirm="New",
                )
            except Exception as e:
                logger.debug("Full refresh (SA) failed: %s", e)

    async def _register_box_connection(
        self, writer: asyncio.StreamWriter, addr: Any
    ) -> int:
        async with self._box_conn_lock:
            previous = self._active_box_writer
            if previous is not None and not previous.is_closing():
                self._last_box_disconnect_reason = "forced"
                self._tc.record_box_session_end(
                    reason="forced",
                    peer=self._active_box_peer,
                )
                await self._close_writer(previous)
                logger.info(
                    "BOX: closing previous connection due to new connection"
                )

            self._conn_seq += 1
            conn_id = self._conn_seq
            peer_str = f"{addr[0]}:{addr[1]}" if addr else None
            logger.info(
                "CTRL_DIAG proxy_box_connect | conn_id=%d box_session=%s peer=%s ts=%.3f",
                conn_id, _box_session_id(addr), peer_str, time.time(),
            )
            self._active_box_writer = writer
            self._active_box_peer = peer_str
            return conn_id

    @staticmethod
    def _tune_socket(writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        with suppress(Exception):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with suppress(Exception):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for opt, val in (
                (getattr(socket, "TCP_KEEPIDLE", None), 60),
                (getattr(socket, "TCP_KEEPINTVL", None), 20),
                (getattr(socket, "TCP_KEEPCNT", None), 3),
            ):
                if opt is None:
                    continue
                with suppress(Exception):
                    sock.setsockopt(socket.IPPROTO_TCP, opt, val)

    async def _unregister_box_connection(
            self, writer: asyncio.StreamWriter) -> None:
        async with self._box_conn_lock:
            if self._active_box_writer is writer:
                self._active_box_writer = None
                self._active_box_peer = None

    async def handle_connection(  # noqa: C417
            self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle jednoho BOX připojení - persistent connection."""
        addr = writer.get_extra_info("peername")
        conn_id = await self._register_box_connection(writer, addr)
        self._tune_socket(writer)

        logger.info("🔌 BOX connected (conn=%s, peer=%s)", conn_id, addr)
        self.box_connected = True
        self._tc.box_seen_in_window = True
        self._tc.force_logs_this_window = False
        self.box_connections += 1
        self._box_connected_since_epoch = time.time()
        self._last_box_disconnect_reason = None
        await self.publish_proxy_status()
        if self._local_getactual_task and not self._local_getactual_task.done():
            self._local_getactual_task.cancel()
        self._local_getactual_task = asyncio.create_task(
            self._local_getactual_loop(writer, conn_id=conn_id)
        )

        # Twin lifecycle: on_reconnect
        if self._twin is not None:
            results = await self._twin.on_reconnect(conn_id=conn_id)
            for result in results:
                logger.info(
                    "TWIN reconnect result: tx_id=%s, status=%s",
                    result.tx_id,
                    result.status,
                )

        try:
            await self._handle_box_connection(reader, writer, conn_id)
        except Exception as e:
            self._last_box_disconnect_reason = "exception"
            logger.error("❌ Error handling connection from %s: %s", addr, e)
        finally:
            if self._local_getactual_task and not self._local_getactual_task.done():
                self._local_getactual_task.cancel()
            self._local_getactual_task = None
            await self._close_writer(writer)
            self.box_connected = False
            self._tc.record_box_session_end(
                reason=self._last_box_disconnect_reason or "unknown",
                peer=self._active_box_peer or (f"{addr[0]}:{addr[1]}" if addr else None),
            )
            self._last_box_disconnect_reason = None
            self._tc.fire_event(
                "error_box_disconnect",
                box_peer=self._active_box_peer or str(addr))

            if self._pending_twin_activation:
                pending_queue = await self._queue_has_items()
                logger.info(
                    "TWIN: BOX disconnected with pending activation (queue_has_items=%s)",
                    pending_queue,
                )
            self._twin_mode_active = False

            # Twin lifecycle: on_disconnect
            if self._twin is not None:
                results = await self._twin.on_disconnect(OnDisconnectDTO(
                    tx_id=None,
                    conn_id=conn_id,
                ))
                for result in results:
                    logger.info(
                        "TWIN disconnect result: tx_id=%s, status=%s",
                        result.tx_id,
                        result.status,
                    )

            await self._unregister_box_connection(writer)
            await self.publish_proxy_status()

    async def _close_writer(self, writer: asyncio.StreamWriter | None) -> None:
        if writer is None:
            return
        with suppress(Exception):
            writer.close()
            await writer.wait_closed()

    async def _read_box_bytes(  # noqa: C417
            self,
        reader: asyncio.StreamReader,
        *,
        conn_id: int,
        idle_timeout_s: float,
    ) -> bytes | None:
        try:
            data = await asyncio.wait_for(reader.read(8192), timeout=idle_timeout_s)
        except ConnectionResetError:
            # BOX (nebo síť) spojení tvrdě ukončil – bereme jako běžné
            # odpojení.
            self._last_box_disconnect_reason = "reset"
            logger.info(
                "🔌 BOX reset the connection (conn=%s)", conn_id
            )
            await self.publish_proxy_status()
            return None
        except asyncio.TimeoutError:  # noqa: C417 - actual error handling
            self._last_box_disconnect_reason = "timeout"
            logger.warning(
                "⏱️ BOX idle timeout (15 min) - closing session (conn=%s)",
                conn_id,
            )
            return None

        if not data:
            self._last_box_disconnect_reason = "eof"
            logger.info(
                "🔌 BOX closed the connection (EOF, conn=%s, frames_rx=%s, frames_tx=%s)",
                conn_id,
                self.stats["frames_received"],
                self.stats["frames_forwarded"],
            )
            await self.publish_proxy_status()
            return None

        return data

    def _touch_last_data(self) -> None:
        self._last_data_iso = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z", time.localtime())
        self._last_data_epoch = time.time()

    def _update_stale_frame_detector(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
    ) -> None:
        if table_name is None:
            return
        current_table = getattr(self, "_stale_table_name", None)
        current_hash = getattr(self, "_stale_payload_hash", None)
        current_repeat_count = int(getattr(self, "_stale_repeat_count", 0))
        current_first_seen = getattr(self, "_stale_first_seen_epoch", None)
        current_last_log = getattr(self, "_stale_last_log_epoch", None)
        now = time.time()
        payload_hash = hashlib.sha256(frame_bytes).hexdigest()
        if current_table == table_name and current_hash == payload_hash:
            self._stale_repeat_count = current_repeat_count + 1
            self._stale_last_seen_epoch = now
        else:
            self._stale_table_name = table_name
            self._stale_payload_hash = payload_hash
            self._stale_repeat_count = 1
            self._stale_first_seen_epoch = now
            self._stale_last_seen_epoch = now
            self._stale_last_log_epoch = None
            return

        first_seen = current_first_seen if current_first_seen is not None else self._stale_first_seen_epoch
        if first_seen is None:
            self._stale_first_seen_epoch = now
            return

        elapsed_s = now - first_seen
        threshold_repeats = 120
        threshold_elapsed_s = 180.0
        if self._stale_repeat_count < threshold_repeats or elapsed_s < threshold_elapsed_s:
            return

        cooldown_s = 120.0
        last_log = current_last_log
        if last_log is not None and (now - last_log) < cooldown_s:
            return

        self._stale_last_log_epoch = now
        logger.warning(
            "⚠️ STALE_STREAM detected: table=%s repeats=%s elapsed_s=%.1f hash=%s peer=%s",
            table_name,
            self._stale_repeat_count,
            elapsed_s,
            payload_hash[:12],
            self._active_box_peer,
        )

    def _extract_device_and_table(
        self, parsed: dict[str, Any] | None
    ) -> tuple[str | None, str | None]:
        if not parsed:
            return None, None
        device_id = parsed.get("_device_id") if parsed else None
        table_name = parsed.get("_table") if parsed else None
        # IsNewSet/IsNewWeather/IsNewFW polls carry <TblName>tbl_actual</TblName>
        if parsed.get("Result") in ("IsNewSet", "IsNewWeather", "IsNewFW"):
            table_name = str(parsed["Result"])
            parsed["_table"] = table_name
        return device_id, table_name

    async def _maybe_autodetect_device_id(self, device_id: str) -> None:
        if not device_id or self.device_id != "AUTO":
            return
        self.device_id = device_id
        self.mqtt_publisher.device_id = device_id
        twin = getattr(self, "_twin", None)
        if twin is not None:
            await twin.set_device_id(device_id)
        self.mqtt_publisher.discovery_sent.clear()
        self.mqtt_publisher.publish_availability()
        logger.info("🔑 Device ID detected: %s", device_id)
        self._msc.setup()

    async def _process_box_frame_common(
        self, *, frame_bytes: bytes, frame: str, conn_id: int
    ) -> tuple[str | None, str | None]:
        self.stats["frames_received"] += 1
        self._tc.box_seen_in_window = True
        self._tc.force_logs_this_window = False
        self._touch_last_data()

        parsed = self.parser.parse_xml_frame(frame)
        device_id, table_name = self._extract_device_and_table(parsed)
        if table_name is None:
            table_name = self._infer_table_name(frame)
        if device_id is None:
            device_id = self._infer_device_id(frame)
        # DEBUG: Log ALL received tables with sample data
        sample_keys = list(parsed.keys())[:3] if parsed else []
        sample_values = {k: str(parsed[k])[:30] for k in sample_keys} if parsed else {}
        logger.info("RECV: table=%s device=%s conn=%s keys=%s sample=%s", table_name, device_id, conn_id, sample_keys, sample_values)
        if device_id:
            await self._maybe_autodetect_device_id(device_id)

        self._update_stale_frame_detector(frame_bytes=frame_bytes, table_name=table_name)

        self._tc.record_frame_direction("box_to_proxy")
        if table_name in ("ACK", "END", "NACK", "IsNewSet", "IsNewWeather", "IsNewFW"):
            self._tc.record_signal_class(table_name)
        if table_name == "END":
            self._tc.record_end_frame(sent=False)

        self._mp.maybe_persist_table_state(parsed, table_name, device_id)
        capture_payload(
            device_id,
            table_name,
            frame,
            frame_bytes,
            parsed or {},
            direction="box_to_proxy",
            length=len(frame_bytes),
            conn_id=conn_id,
            peer=self._active_box_peer,
        )
        self._tc.record_request(table_name, conn_id)

        if parsed:
            if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
                self._isnew_polls += 1
                self._isnew_last_poll_epoch = time.time()
                self._isnew_last_poll_iso = self._last_data_iso
            if table_name == "tbl_events":
                self._tc.record_tbl_event(parsed=parsed, device_id=device_id)
            await self._cs.handle_setting_event(parsed, table_name, device_id)
            try:
                await self._maybe_handle_twin_event(parsed, table_name, device_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "TWIN: Sidecar dependency failure ignored (transport fail-open, conn=%s, table=%s): %s",
                    conn_id,
                    table_name,
                    exc,
                )
            await self._mp.maybe_process_mode(parsed, table_name, device_id)
            # DEBUG: Log which tables are being published
            if table_name and table_name.startswith("tbl_"):
                logger.info("DEBUG: Publishing table=%s, keys=%s", table_name, list(parsed.keys())[:5])
            try:
                await self.mqtt_publisher.publish_data(parsed)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "TELEMETRY: publish_data failed (transport fail-open, conn=%s, table=%s): %s",
                    conn_id,
                    table_name,
                    exc,
                )

        return device_id, table_name

    _infer_table_name = staticmethod(infer_table_name)
    _infer_device_id = staticmethod(infer_device_id)

    async def _process_box_frame_with_guard(
        self,
        *,
        frame_bytes: bytes,
        frame: str,
        conn_id: int,
    ) -> tuple[str | None, str | None, bool]:
        try:
            device_id, table_name = await self._process_box_frame_common(
                frame_bytes=frame_bytes,
                frame=frame,
                conn_id=conn_id,
            )
            return device_id, table_name, False
        except (ValueError, KeyError, TypeError, AttributeError):
            # Nechceme shazovat celé BOX spojení kvůli chybě v publish/discovery/parsing.
            # Traceback nám pomůže najít přesnou příčinu (např. regex v
            # některé knihovně).
            table_name = self._infer_table_name(frame)
            device_id = self._infer_device_id(frame)
            logger.warning(
                "⚠️ Frame processing failed; continuing with inferred header routing "
                "(conn=%s, peer=%s, table=%s, device_id=%s)",
                conn_id,
                self._active_box_peer,
                table_name,
                device_id,
                exc_info=True,
            )
            return device_id, table_name, True

    async def _maybe_handle_local_control_poll(
        self,
        *,
        table_name: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
    ) -> bool:
        is_local_control_poll = table_name in (
            "IsNewSet",
            "IsNewWeather",
            "IsNewFW",
        )
        if not is_local_control_poll:
            return False

        routing = self._resolve_local_control_routing()
        if routing != "twin":
            return False

        return await self._dispatch_local_control_via_twin(
            table_name=table_name,
            conn_id=conn_id,
            box_writer=box_writer,
        )

    async def _maybe_deactivate_session_twin_mode_if_idle(self, *, conn_id: int) -> None:
        if not self._twin_mode_active:
            return
        if self._pending_twin_activation:
            return

        sidecar_adapter = getattr(self, "_sidecar_adapter", None)
        if sidecar_adapter is not None:
            if not sidecar_adapter.orchestrator.is_active():
                sidecar_adapter.record_activation()
            if not await sidecar_adapter.check_and_deactivate():
                return
            self._twin_mode_active = False
            logger.info("TWIN: Session twin mode deactivated (idle, conn=%s)", conn_id)
            return

        if self._twin is None:
            self._twin_mode_active = False
            return
        if self._hm.should_route_settings_via_twin():
            return

        inflight = await self._twin.get_inflight()
        if inflight is not None:
            return

        queue_len = await self._twin.get_queue_length()
        if queue_len > 0:
            return

        self._twin_mode_active = False
        logger.info("TWIN: Session twin mode deactivated (idle, conn=%s)", conn_id)

    async def _transport_only_forward(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        cloud_connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None, bool]:
        cloud_reader, cloud_writer = await self._cf.forward_frame(
            frame_bytes=frame_bytes,
            table_name=table_name,
            device_id=device_id,
            conn_id=conn_id,
            box_writer=box_writer,
            cloud_reader=cloud_reader,
            cloud_writer=cloud_writer,
            connect_timeout_s=cloud_connect_timeout_s,
        )
        if cloud_writer is None:
            logger.info(
                "🔌 Closing BOX session after cloud failure (conn=%s, table=%s)",
                conn_id,
                table_name,
            )
            self._last_box_disconnect_reason = "cloud_failure"
            return cloud_reader, cloud_writer, True

        return cloud_reader, cloud_writer, False

    async def _route_box_frame_by_mode(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        cloud_connect_timeout_s: float,
        current_mode: ProxyMode,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None, bool]:
        legacy_fallback_enabled = bool(getattr(self, "_legacy_fallback_enabled", True))

        if legacy_fallback_enabled and current_mode == ProxyMode.OFFLINE:
            cloud_reader, cloud_writer = await self._handle_frame_local_offline(
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )
            return cloud_reader, cloud_writer, False

        if (
            legacy_fallback_enabled
            and current_mode == ProxyMode.HYBRID
            and not self._hm.should_try_cloud()
        ):
            cloud_reader, cloud_writer = await self._handle_frame_local_offline(
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )
            return cloud_reader, cloud_writer, False

        return await self._transport_only_forward(
            frame_bytes=frame_bytes,
            table_name=table_name,
            device_id=device_id,
            conn_id=conn_id,
            box_writer=box_writer,
            cloud_reader=cloud_reader,
            cloud_writer=cloud_writer,
            cloud_connect_timeout_s=cloud_connect_timeout_s,
        )

    async def _activate_session_twin_mode_if_needed(self, *, conn_id: int) -> None:
        # Task 5: Check if pending activation has expired (idle timeout)
        await self._maybe_expire_pending_twin_activation()

        pending_twin_activation = getattr(self, "_pending_twin_activation", False)
        if pending_twin_activation and await self._queue_has_items():
            self._twin_mode_active = True
            self._pending_twin_activation = False
            self._pending_twin_activation_since = None
            sidecar_adapter = getattr(self, "_sidecar_adapter", None)
            if sidecar_adapter is not None:
                sidecar_adapter.record_activation()
            logger.info("TWIN: Session twin mode activated (conn=%s)", conn_id)
            return

        is_twin_routing_available = getattr(self, "_is_twin_routing_available", None)
        if not callable(is_twin_routing_available):
            return
        try:
            twin_routing_available = is_twin_routing_available()  # pylint: disable=not-callable
        except AttributeError:
            return
        if not twin_routing_available:
            return
        if not self._hm.should_route_settings_via_twin():
            return

        self._twin_mode_active = True
        self._pending_twin_activation = False
        sidecar_adapter = getattr(self, "_sidecar_adapter", None)
        if sidecar_adapter is not None:
            sidecar_adapter.record_activation()
        logger.info(
            "TWIN: Session twin mode activated for offline settings (conn=%s)",
            conn_id,
        )

    async def _handle_box_frame_iteration(
        self,
        *,
        data: bytes,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        cloud_connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None, bool]:
        # Task 15: Generate correlation ID at frame entry
        with correlation_id_context_frame(data) as cid:
            result = await self._handle_box_frame_with_cid(
                data=data,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_reader=cloud_reader,
                cloud_writer=cloud_writer,
                cloud_connect_timeout_s=cloud_connect_timeout_s,
                correlation_id=cid,
            )
            return result

    async def _handle_box_frame_with_cid(
        self,
        *,
        data: bytes,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        cloud_connect_timeout_s: float,
        correlation_id: str,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None, bool]:
        """Handle frame with correlation ID already set in context."""
        # Use correlation_id for observability
        logger.debug("Processing frame with correlation_id=%s", correlation_id)
        frame = data.decode("utf-8", errors="replace")
        processed = await self._process_box_frame_with_guard(
            frame_bytes=data,
            frame=frame,
            conn_id=conn_id,
        )
        device_id, table_name, processing_failed = processed

        if processing_failed:
            current_mode = await self._hm.get_current_mode()
            return await self._route_box_frame_by_mode(
                frame_bytes=data,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_reader=cloud_reader,
                cloud_writer=cloud_writer,
                cloud_connect_timeout_s=cloud_connect_timeout_s,
                current_mode=current_mode,
            )

        legacy_fallback_enabled = bool(getattr(self, "_legacy_fallback_enabled", True))
        if not legacy_fallback_enabled:
            current_mode = await self._hm.get_current_mode()
            return await self._route_box_frame_by_mode(
                frame_bytes=data,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_reader=cloud_reader,
                cloud_writer=cloud_writer,
                cloud_connect_timeout_s=cloud_connect_timeout_s,
                current_mode=current_mode,
            )

        if await self._maybe_handle_twin_ack(frame, box_writer, conn_id=conn_id):
            await self._maybe_deactivate_session_twin_mode_if_idle(conn_id=conn_id)
            return cloud_reader, cloud_writer, False

        self._tc.force_logs_this_window = False
        current_mode = await self._hm.get_current_mode()

        if await self._maybe_handle_local_control_poll(
            table_name=table_name,
            conn_id=conn_id,
            box_writer=box_writer,
        ):
            await self._maybe_deactivate_session_twin_mode_if_idle(conn_id=conn_id)
            return cloud_reader, cloud_writer, False

        return await self._route_box_frame_by_mode(
            frame_bytes=data,
            table_name=table_name,
            device_id=device_id,
            conn_id=conn_id,
            box_writer=box_writer,
            cloud_reader=cloud_reader,
            cloud_writer=cloud_writer,
            cloud_connect_timeout_s=cloud_connect_timeout_s,
            current_mode=current_mode,
        )

    async def _handle_box_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int,
    ) -> None:
        """Jednotný handler pro BOX session, který respektuje změny režimu během spojení."""
        box_idle_timeout_s = 900  # 15 minut
        cloud_connect_timeout_s = 5.0

        cloud_reader: asyncio.StreamReader | None = None
        cloud_writer: asyncio.StreamWriter | None = None

        await self._activate_session_twin_mode_if_needed(conn_id=conn_id)

        try:
            while True:
                data = await self._read_box_bytes(
                    box_reader, conn_id=conn_id, idle_timeout_s=box_idle_timeout_s
                )
                if data is None:
                    break

                cloud_reader, cloud_writer, should_break = await self._handle_box_frame_iteration(
                    data=data,
                    conn_id=conn_id,
                    box_writer=box_writer,
                    cloud_reader=cloud_reader,
                    cloud_writer=cloud_writer,
                    cloud_connect_timeout_s=cloud_connect_timeout_s,
                )
                if should_break:
                    break
                # Task 4: Mid-session twin activation - check pending activation after each frame
                if self._pending_twin_activation:
                    await self._activate_session_twin_mode_if_needed(conn_id=conn_id)

        except ConnectionResetError:
            # Běžné: BOX přeruší TCP (např. reconnect po modem resetu).
            # Nechceme z toho dělat ERROR.
            logger.debug(
                "🔌 BOX closed the connection (RST, conn=%s, peer=%s)",
                conn_id,
                self._active_box_peer,
            )
        except (OSError, TimeoutError):
            logger.exception(
                "❌ Box connection handler error (conn=%s, peer=%s)",
                conn_id,
                self._active_box_peer,
            )
        finally:
            await self._close_writer(cloud_writer)
            if self._cf.session_connected:
                self._tc.record_cloud_session_end(reason="box_disconnect")
            self._cf.session_connected = False
            self._cf.rx_buf.clear()

    async def _respond_local_offline(
        self,
        _frame_bytes: bytes,
        table_name: str | None,
        _device_id: str | None,
        box_writer: asyncio.StreamWriter,
        *,
        send_ack: bool = True,
        conn_id: int | None = None,
    ):
        if not send_ack:
            return

        _peer = box_writer.get_extra_info("peername")
        logger.info(
            "CTRL_DIAG proxy_offline_check | table=%s box_session=%s conn_id=%s ts=%.3f",
            table_name,
            _box_session_id(_peer), conn_id, time.time(),
        )

        ack_response = build_offline_ack_frame(table_name)
        box_writer.write(ack_response)
        await box_writer.drain()
        if conn_id is not None:
            self._record_proxy_to_box_frame(
                frame_bytes=ack_response,
                table_name=table_name,
                conn_id=conn_id,
                source="local",
            )
        self.stats["acks_local"] += 1

    async def _handle_frame_local_offline(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[None, None]:
        await self._close_writer(cloud_writer)
        if self._cf.session_connected:
            self._tc.record_cloud_session_end(reason="manual_offline")
        self._cf.session_connected = False
        self._cf.rx_buf.clear()
        await self._respond_local_offline(
            frame_bytes,
            table_name,
            device_id,
            box_writer,
            conn_id=conn_id,
        )
        return None, None

    def _resolve_local_control_routing(self) -> str:
        cloud_healthy = self._hm.should_try_cloud()
        force_twin = self._local_control_routing == "force_twin"
        force_cloud = self._local_control_routing == "force_cloud"

        if force_twin and not getattr(self, "_sidecar_adapter", None):
            return "twin" if self._is_twin_routing_available() else "local"

        sidecar_adapter = getattr(self, "_sidecar_adapter", None)
        if sidecar_adapter is not None:
            route = sidecar_adapter.resolve_route_target(
                cloud_healthy=cloud_healthy,
                force_twin=force_twin,
                force_cloud=force_cloud,
            )
            logger.debug(
                "ROUTING_ARBITRATION: route=%s cloud_healthy=%s force_twin=%s force_cloud=%s",
                route, cloud_healthy, force_twin, force_cloud
            )
            return route

        if force_cloud:
            return "cloud" if cloud_healthy else "local"

        twin_available = self._is_twin_routing_available()
        if self._hm.should_route_settings_via_twin() and twin_available:
            return "twin"

        configured_mode = str(getattr(self._hm, "configured_mode", "")).lower()
        if configured_mode == "offline":
            return "twin" if twin_available else "local"

        mode = getattr(self._hm, "mode", None)
        if mode == ProxyMode.ONLINE:
            if self._twin_mode_active and twin_available:
                return "twin"
            return "cloud"
        if mode == ProxyMode.HYBRID:
            if cloud_healthy:
                return "cloud"
            return "twin" if twin_available else "local"
        if mode == ProxyMode.OFFLINE:
            return "twin" if twin_available else "local"
        if configured_mode == "online":
            if self._twin_mode_active and twin_available:
                return "twin"
            return "cloud"

        if cloud_healthy:
            return "cloud"
        return "cloud"

    def _is_twin_routing_available(self) -> bool:
        return (
            (not self._twin_kill_switch)
            and self._twin_enabled
            and self._twin is not None
        )

    def set_twin_kill_switch(self, enabled: bool) -> None:
        """Enable or disable the twin kill switch.

        When enabled, twin routing is disabled regardless of other settings.
        """
        self._twin_kill_switch = bool(enabled)
        if self._twin_kill_switch:
            logger.warning("TWIN: Kill-switch enabled, twin routing disabled")
        else:
            logger.info("TWIN: Kill-switch disabled, twin routing enabled")

    async def _dispatch_local_control_via_twin(
        self,
        *,
        table_name: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
    ) -> bool:
        """Dispatch local control command via digital twin.

        This method is called when routing resolves to "twin".
        It uses the twin's poll-driven delivery mechanism.

        Returns:
            True if twin handled the delivery, False otherwise
        """
        if self._twin is None:
            return False

        if table_name not in ("IsNewSet", "IsNewWeather", "IsNewFW"):
            return False

        response = await self._twin.on_poll(
            tx_id=None,
            conn_id=conn_id,
            table_name=table_name,
        )

        if response.frame_data is not None:
            frame_bytes = response.frame_data.encode("utf-8", errors="strict")
            box_writer.write(frame_bytes)
            await box_writer.drain()
            self.stats["acks_local"] += 1
            self._record_proxy_to_box_frame(
                frame_bytes=frame_bytes,
                table_name=table_name,
                conn_id=conn_id,
                source="twin",
                tx_id=getattr(response, "tx_id", None),
            )
            logger.info(
                "TWIN: Delivered setting via twin (table=%s, conn=%s)",
                table_name,
                conn_id,
            )
            return True

        if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
            end_frame = build_end_time_frame().decode("utf-8", errors="strict")
            end_frame_bytes = end_frame.encode("utf-8", errors="strict")
            box_writer.write(end_frame_bytes)
            await box_writer.drain()
            self.stats["acks_local"] += 1
            self._record_proxy_to_box_frame(
                frame_bytes=end_frame_bytes,
                table_name=table_name,
                conn_id=conn_id,
                source="twin",
            )
            logger.debug(
                "TWIN: Poll handled with END (table=%s, conn=%s)",
                table_name,
                conn_id,
            )
            return True

        return False

    async def _maybe_handle_twin_ack(
        self,
        frame: str,
        box_writer: asyncio.StreamWriter,
        *,
        conn_id: int,
    ) -> bool:
        """Handle ACK/NACK for twin-routed commands.

        This method checks if the incoming frame is an ACK/NACK for a
        twin-routed command and routes it through the twin's on_ack handler.

        Returns:
            True if twin handled the ACK, False otherwise
        """
        has_reason = "<Reason>Setting</Reason>" in frame
        has_ack = "<Result>ACK</Result>" in frame
        has_nack = "<Result>NACK</Result>" in frame
        has_end = "<Result>END</Result>" in frame

        if not has_reason or (not has_ack and not has_nack and not has_end):
            return False

        if self._twin is None:
            return False

        inflight = await self._twin.get_inflight()
        if inflight is None:
            return False

        ack_ok = has_ack or has_end

        dto = OnAckDTO(
            tx_id=inflight.tx_id,
            conn_id=conn_id,
            ack=ack_ok,
            delivered_conn_id=inflight.delivered_conn_id,
        )

        try:
            result = await self._twin.on_ack(dto)
            if result is not None:
                if not has_end:
                    end_frame = build_end_time_frame()
                    box_writer.write(end_frame)

                    async def _flush_end_frame() -> None:
                        try:
                            await box_writer.drain()
                        except asyncio.CancelledError:
                            logger.debug(
                                "TWIN: END flush cancelled for tx_id=%s (conn=%s)",
                                inflight.tx_id,
                                conn_id,
                            )
                            raise
                        except Exception as exc:
                            logger.debug(
                                "TWIN: END flush failed for tx_id=%s (conn=%s): %s",
                                inflight.tx_id,
                                conn_id,
                                exc,
                            )
                            return
                        self._record_proxy_to_box_frame(
                            frame_bytes=end_frame,
                            table_name="END",
                            conn_id=conn_id,
                            source="twin_ack",
                            tx_id=inflight.tx_id,
                        )

                    try:
                        task = asyncio.create_task(_flush_end_frame())
                        self._background_tasks.add(task)
                        task.add_done_callback(self._background_tasks.discard)
                    except Exception as exc:
                        logger.debug("TWIN: Failed to schedule END drain: %s", exc)
                logger.info(
                    "TWIN: %s handled for tx_id=%s (conn=%s)",
                    "ACK" if ack_ok else "NACK",
                    inflight.tx_id,
                    conn_id,
                )
                return True
        except Exception as e:
            logger.warning("TWIN: Error handling ACK: %s", e)

        return False

    async def _maybe_handle_twin_event(
        self,
        parsed: dict[str, Any] | None,
        table_name: str | None,
        _device_id: str | None,
    ) -> None:
        twin = getattr(self, "_twin", None)
        if twin is None:
            return
        if table_name != "tbl_events":
            return
        if not parsed or parsed.get("Type") != "Setting":
            return

        inflight = await twin.get_inflight()
        if inflight is None:
            return

        content = parsed.get("Content")
        if not content:
            return

        ev = self._cs.parse_setting_event(str(content))
        if not ev:
            return

        ev_tbl, ev_item, _old_v, new_v = ev
        if ev_tbl != inflight.tbl_name or ev_item != inflight.tbl_item:
            return
        if str(new_v) != str(inflight.new_value):
            return

        dto = OnTblEventDTO(
            tx_id=inflight.tx_id,
            conn_id=inflight.conn_id,
            event_type="Setting",
            content=content,
            tbl_name=ev_tbl,
            tbl_item=ev_item,
            old_value=_old_v,
            new_value=new_v,
        )

        try:
            result = await twin.on_tbl_event(dto)
            if result is not None:
                logger.debug("TWIN: Event handled for tx_id=%s", result.tx_id)
        except Exception as e:
            logger.warning("TWIN: Error handling event: %s", e)
