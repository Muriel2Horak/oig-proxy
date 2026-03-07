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
import logging
import socket
import time
from contextlib import suppress
from typing import Any

from parser import OIGDataParser
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
    TWIN_CLOUD_ALIGNED,
    TWIN_ENABLED,
    TWIN_KILL_SWITCH,
    LOCAL_CONTROL_ROUTING,
)
from control_api import ControlAPIServer
from mqtt_state_cache import MqttStateCache
from telemetry_collector import TelemetryCollector
from hybrid_mode import HybridModeManager
from oig_frame import (
    build_end_time_frame,
    build_getactual_frame,
    build_offline_ack_frame,
    infer_device_id,
    infer_table_name,
)
from models import ProxyMode
from digital_twin import DigitalTwin, DigitalTwinConfig, TwinMQTTHandler
from mqtt_publisher import MQTTPublisher
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
        self._tc = TelemetryCollector(self, interval_s=TELEMETRY_INTERVAL_S)
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
        twin_config = DigitalTwinConfig(device_id=device_id)
        self._twin: DigitalTwin | None = DigitalTwin(config=twin_config) if self._twin_enabled else None
        self._twin_mqtt_handler: TwinMQTTHandler | None = (
            TwinMQTTHandler(twin=self._twin, mqtt_publisher=self.mqtt_publisher)
            if self._twin is not None
            else None
        )
        self._pending_twin_activation: bool = False
        self._twin_mode_active: bool = False
        self._install_twin_mqtt_activation_hook()

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
            logger.info(
                "TWIN: Pending session activation armed (ONLINE mode, mqtt topic=%s)",
                topic,
            )

        self._twin_mqtt_handler.on_mqtt_message = _on_mqtt_message_with_activation

    async def _queue_has_items(self) -> bool:
        if self._twin is None:
            return False
        return (await self._twin.get_queue_length()) > 0

    def _restore_device_id(self) -> None:
        if self.device_id != "AUTO":
            return
        restored_device_id = self._mp.mode_device_id or self._mp.prms_device_id
        if not restored_device_id:
            return
        self.device_id = restored_device_id
        self.mqtt_publisher.device_id = restored_device_id
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
            from twin_state import OnDisconnectDTO
            results = await self._twin.on_reconnect(conn_id=conn_id)
            for result in results:
                logger.info("TWIN reconnect result: tx_id=%s, status=%s", result.tx_id, result.status)

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
                from twin_state import OnDisconnectDTO
                results = await self._twin.on_disconnect(OnDisconnectDTO(
                    tx_id=None,
                    conn_id=conn_id,
                ))
                for result in results:
                    logger.info("TWIN disconnect result: tx_id=%s, status=%s", result.tx_id, result.status)

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
        if device_id:
            await self._maybe_autodetect_device_id(device_id)

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
            await self._maybe_handle_twin_event(parsed, table_name, device_id)
            await self._mp.maybe_process_mode(parsed, table_name, device_id)
            await self.mqtt_publisher.publish_data(parsed)

        return device_id, table_name

    _infer_table_name = staticmethod(infer_table_name)
    _infer_device_id = staticmethod(infer_device_id)

    async def _process_box_frame_with_guard(
        self,
        *,
        frame_bytes: bytes,
        frame: str,
        conn_id: int,
    ) -> tuple[str | None, str | None] | None:
        try:
            return await self._process_box_frame_common(
                frame_bytes=frame_bytes,
                frame=frame,
                conn_id=conn_id,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            # Nechceme shazovat celé BOX spojení kvůli chybě v publish/discovery/parsing.
            # Traceback nám pomůže najít přesnou příčinu (např. regex v
            # některé knihovně).
            logger.exception(
                "❌ Frame processing error (conn=%s, peer=%s)",
                conn_id,
                self._active_box_peer,
            )
            return None

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
        if current_mode == ProxyMode.OFFLINE:
            cloud_reader, cloud_writer = await self._handle_frame_local_offline(
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )
            return cloud_reader, cloud_writer, False

        if current_mode == ProxyMode.HYBRID and not self._hm.should_try_cloud():
            cloud_reader, cloud_writer = await self._handle_frame_local_offline(
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )
            return cloud_reader, cloud_writer, False

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
        if cloud_writer is None and current_mode == ProxyMode.ONLINE:
            # Cloud selhal v ONLINE módu – BOX nedostal ACK.
            # Ukončíme BOX session, aby mohl reconnectnout čistě.
            logger.info(
                "🔌 Closing BOX session after cloud failure (conn=%s, table=%s)",
                conn_id,
                table_name,
            )
            self._last_box_disconnect_reason = "cloud_failure"
            return cloud_reader, cloud_writer, True

        return cloud_reader, cloud_writer, False

    async def _activate_session_twin_mode_if_needed(self, *, conn_id: int) -> None:
        pending_twin_activation = getattr(self, "_pending_twin_activation", False)
        if pending_twin_activation and await self._queue_has_items():
            self._twin_mode_active = True
            self._pending_twin_activation = False
            logger.info("TWIN: Session twin mode activated (conn=%s)", conn_id)
            return

        is_twin_routing_available = getattr(self, "_is_twin_routing_available", None)
        if not callable(is_twin_routing_available):
            return
        try:
            twin_routing_available = is_twin_routing_available()
        except AttributeError:
            return
        if not twin_routing_available:
            return
        if not self._hm.should_route_settings_via_twin():
            return

        self._twin_mode_active = True
        self._pending_twin_activation = False
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
        frame = data.decode("utf-8", errors="replace")
        processed = await self._process_box_frame_with_guard(
            frame_bytes=data,
            frame=frame,
            conn_id=conn_id,
        )
        if processed is None:
            return cloud_reader, cloud_writer, False

        device_id, table_name = processed

        if await self._maybe_handle_twin_ack(frame, box_writer, conn_id=conn_id):
            return cloud_reader, cloud_writer, False

        self._tc.force_logs_this_window = False
        current_mode = await self._hm.get_current_mode()

        if await self._maybe_handle_local_control_poll(
            table_name=table_name,
            conn_id=conn_id,
            box_writer=box_writer,
        ):
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
        if conn_id is not None:
            self._tc.record_response(
                ack_response.decode("utf-8", errors="replace"),
                source="local",
                conn_id=conn_id,
            )
        box_writer.write(ack_response)
        await box_writer.drain()
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
        """Resolve local control routing target.

        Policy ownership stays in hybrid_mode.py. This method only determines
        the routing target for local control commands based on:
        - LOCAL_CONTROL_ROUTING override (auto|force_twin|force_cloud)
        - Current proxy mode (ONLINE/HYBRID/OFFLINE/REPLAY)
        - Twin availability (TWIN_ENABLED)

        Returns:
            "twin" - Route via digital twin
            "cloud" - Route via cloud forwarder
            "local" - Route via local ACK (offline mode)
        """
        if self._local_control_routing == "force_twin":
            if self._is_twin_routing_available():
                return "twin"
            return "local"

        if self._local_control_routing == "force_cloud":
            if self._hm.should_try_cloud():
                return "cloud"
            return "local"

        mode = self._hm.mode
        configured = self._hm.configured_mode

        if self._hm.should_route_settings_via_twin() or configured == "offline" or mode == ProxyMode.OFFLINE:
            if self._is_twin_routing_available():
                return "twin"
            return "local"

        if mode == ProxyMode.ONLINE:
            if self._twin_mode_active and self._is_twin_routing_available():
                return "twin"
            return "cloud"

        if mode == ProxyMode.HYBRID:
            if not self._hm.should_try_cloud():
                if self._is_twin_routing_available():
                    return "twin"
                return "local"
            return "cloud"

        return "cloud"

    def _is_twin_routing_available(self) -> bool:
        return (not self._twin_kill_switch) and self._twin_enabled and self._twin is not None

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
            self._tc.record_response(
                response.frame_data,
                source="twin",
                conn_id=conn_id,
            )
            logger.info(
                "TWIN: Delivered setting via twin (table=%s, conn=%s)",
                table_name,
                conn_id,
            )
            return True

        if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
            end_frame = build_end_time_frame().decode("utf-8", errors="strict")
            box_writer.write(end_frame.encode("utf-8", errors="strict"))
            await box_writer.drain()
            self.stats["acks_local"] += 1
            self._tc.record_response(
                end_frame,
                source="twin",
                conn_id=conn_id,
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
        if self._twin is None:
            return False

        inflight = await self._twin.get_inflight()
        if inflight is None:
            return False

        has_reason = "<Reason>Setting</Reason>" in frame
        has_ack = "<Result>ACK</Result>" in frame
        has_nack = "<Result>NACK</Result>" in frame
        has_end = "<Result>END</Result>" in frame

        if not has_reason or (not has_ack and not has_nack and not has_end):
            return False

        from twin_state import OnAckDTO
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
                    from oig_frame import build_end_time_frame
                    box_writer.write(build_end_time_frame())
                    try:
                        task = asyncio.create_task(box_writer.drain())
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
        device_id: str | None,
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

        from twin_state import OnTblEventDTO
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
