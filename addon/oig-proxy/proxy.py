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
import secrets
import json
import uuid
from collections import deque
from contextlib import suppress
import re
from datetime import datetime, timezone
from typing import Any

from parser import OIGDataParser
from config import (
    CLOUD_ACK_TIMEOUT,
    CONTROL_API_HOST,
    CONTROL_API_PORT,
    CONTROL_MQTT_ENABLED,
    LOCAL_GETACTUAL_ENABLED,
    LOCAL_GETACTUAL_INTERVAL_S,
    FULL_REFRESH_INTERVAL_H,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    PROXY_STATUS_INTERVAL,
    PROXY_STATUS_ATTRS_TOPIC,
    MQTT_NAMESPACE,
    MQTT_PUBLISH_QOS,
    MQTT_STATE_RETAIN,
    TARGET_PORT,
    TARGET_SERVER,
    TELEMETRY_ENABLED,
    TELEMETRY_INTERVAL_S,
)
from control_api import ControlAPIServer
from control_pipeline import ControlPipeline
from mqtt_state_cache import MqttStateCache
from telemetry_collector import TelemetryCollector
from hybrid_mode import HybridModeManager
from oig_frame import (
    RESULT_ACK,
    RESULT_END,
    build_ack_only_frame,
    build_end_time_frame,
    build_frame,
    build_getactual_frame,
    build_offline_ack_frame,
    extract_one_xml_frame,
    infer_device_id,
    infer_table_name,
)
from models import ProxyMode
from mqtt_publisher import MQTTPublisher
from utils import (
    capture_payload,
    get_sensor_config,
    load_mode_state,
    load_prms_state,
    resolve_cloud_host,
    save_mode_state,
    save_prms_state,
)

logger = logging.getLogger(__name__)


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

    # Frame string constants (aliases for backward compatibility;
    # canonical values live in oig_frame module)
    _RESULT_ACK = RESULT_ACK
    _RESULT_END = RESULT_END
    _TIME_OFFSET = "+00:00"
    _POST_DRAIN_SA_KEY = "post_drain_sa_refresh"
    _CLOUD_ACK_MAX_BYTES = 4096

    @staticmethod
    def _get_current_timestamp() -> str:
        """Get current timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def __init__(self, device_id: str):
        self.device_id = device_id

        # Komponenty
        self.mqtt_publisher = MQTTPublisher(device_id)
        self.parser = OIGDataParser()
        loaded_mode, loaded_dev = load_mode_state()
        self._mode_value: int | None = loaded_mode
        self._mode_device_id: str | None = loaded_dev
        self._mode_pending_publish: bool = self._mode_value is not None
        self._prms_tables, self._prms_device_id = load_prms_state()
        self._prms_pending_publish: bool = bool(self._prms_tables)
        self._msc = MqttStateCache(self)
        self._mqtt_was_ready: bool = False

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
        self._local_setting_pending: dict[str, Any] | None = None
        self._set_commands_buffer: list[dict[str, str]] = []  # For telemetry

        # Control over MQTT (production)
        self._ctrl = ControlPipeline(self)
        self._proxy_status_attrs_topic: str = str(PROXY_STATUS_ATTRS_TOPIC)
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
        # Cloud session telemetry
        self.cloud_connects = 0
        self.cloud_disconnects = 0
        self.cloud_timeouts = 0
        self.cloud_errors = 0
        self.cloud_session_connected = False
        self._cloud_connected_since_epoch: float | None = None
        self._cloud_peer: str | None = None
        self._cloud_rx_buf = bytearray()
        self._last_hb_ts: float = 0.0
        self._hb_interval_s: float = max(60.0, float(PROXY_STATUS_INTERVAL))

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

        if self._ctrl.mqtt_enabled:
            self._ctrl.setup_mqtt()
        self._msc.setup()

    def _restore_device_id(self) -> None:
        if self.device_id != "AUTO":
            return
        restored_device_id = self._mode_device_id or self._prms_device_id
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
            self._status_task = asyncio.create_task(self._proxy_status_loop())
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
        logger.info("📡 Cloud target: %s:%s", TARGET_SERVER, TARGET_PORT)
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
        await self._ctrl.publish_restart_errors()
        self._mqtt_was_ready = self.mqtt_publisher.is_ready()
        self._start_background_tasks()

        await self._start_tcp_server()

    def _build_status_payload(self) -> dict[str, Any]:
        """Vytvoří payload pro proxy_status MQTT sensor."""
        inflight = self._ctrl.inflight
        inflight_str = ControlPipeline.format_tx(inflight) if inflight else ""
        last_result_str = ControlPipeline.format_result(
            self._ctrl.last_result)
        inflight_key = str(inflight.get("request_key")
                           or "") if inflight else ""
        queue_keys = [str(tx.get("request_key") or "")
                      for tx in self._ctrl.queue]
        payload = {
            "status": self._hm.mode.value,
            "mode": self._hm.mode.value,
            "configured_mode": self._hm.configured_mode,
            "control_session_id": self._ctrl.session_id,
            "box_device_id": self.device_id if self.device_id != "AUTO" else None,
            "cloud_online": int(not self._hm.in_offline),
            "hybrid_fail_count": self._hm.fail_count,
            "cloud_connects": self.cloud_connects,
            "cloud_disconnects": self.cloud_disconnects,
            "cloud_timeouts": self.cloud_timeouts,
            "cloud_errors": self.cloud_errors,
            "cloud_session_connected": int(self.cloud_session_connected),
            "cloud_session_active": int(self.cloud_session_connected),
            "mqtt_queue": self.mqtt_publisher.queue.size(),
            "box_connected": int(self.box_connected),
            "box_connections": self.box_connections,
            "box_connections_active": int(self.box_connected),
            "box_data_recent": int(
                self._last_data_epoch is not None
                and (time.time() - self._last_data_epoch) <= 90
            ),
            "last_data": self._last_data_iso,
            "isnewset_polls": self._isnew_polls,
            "isnewset_last_poll": self._isnew_last_poll_iso,
            "isnewset_last_response": self._isnew_last_response,
            "isnewset_last_rtt_ms": self._isnew_last_rtt_ms,
            "control_queue_len": len(self._ctrl.queue),
            "control_inflight": inflight_str,
            "control_inflight_key": inflight_key,
            "control_queue_keys": [k for k in queue_keys if k],
            "control_last_result": last_result_str,
        }
        return payload

    def _build_status_attrs_payload(self) -> dict[str, Any]:
        if self._ctrl.inflight:
            inflight_key = str(self._ctrl.inflight.get("request_key") or "")
        else:
            inflight_key = ""
        queue_keys = [
            str(tx.get("request_key") or "")
            for tx in self._ctrl.queue
        ]
        return {
            "control_inflight_key": inflight_key,
            "control_queue_keys": [k for k in queue_keys if k],
        }

    async def publish_proxy_status(self) -> None:
        """Publikuje stav proxy."""
        payload = self._build_status_payload()
        try:
            await self.mqtt_publisher.publish_proxy_status(payload)
        except Exception as e:
            logger.debug("Proxy status publish failed: %s", e)
        try:
            await self.mqtt_publisher.publish_raw(
                topic=self._proxy_status_attrs_topic,
                payload=json.dumps(self._build_status_attrs_payload(), ensure_ascii=True),
                qos=self._ctrl.qos,
                retain=True,
            )
        except Exception as e:
            logger.debug("Proxy status attrs publish failed: %s", e)

    # Frame building methods delegated to oig_frame module
    _build_getactual_frame = staticmethod(build_getactual_frame)
    _build_ack_only_frame = staticmethod(build_ack_only_frame)
    _build_end_time_frame = staticmethod(build_end_time_frame)

    @staticmethod
    def _build_offline_ack_frame(table_name: str | None) -> bytes:
        return build_offline_ack_frame(table_name)

    async def _send_getactual_to_box(
        self, writer: asyncio.StreamWriter, *, conn_id: int
    ) -> None:
        frame_bytes = self._build_getactual_frame()
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
            if self._ctrl.inflight or self._ctrl.queue:
                continue
            try:
                logger.info("CONTROL: Full refresh (SA) requested")
                await self._send_setting_to_box(
                    tbl_name="tbl_box_prms",
                    tbl_item="SA",
                    new_value="1",
                    confirm="New",
                )
            except Exception as e:
                logger.debug("Full refresh (SA) failed: %s", e)

    async def _publish_mode_if_ready(
        self,
        device_id: str | None = None,
        *,
        reason: str | None = None
    ) -> None:
        """Publikuje známý MODE do MQTT."""
        if self._mode_value is None:
            return
        target_device_id = device_id
        if not target_device_id:
            if self.device_id and self.device_id != "AUTO":
                target_device_id = self.device_id
            elif self._mode_device_id:
                target_device_id = self._mode_device_id
        if not target_device_id:
            logger.debug("MODE: No device_id, publish deferred")
            return

        payload: dict[str, Any] = {
            "_table": "tbl_box_prms",
            "MODE": int(self._mode_value),
        }
        payload["_device_id"] = target_device_id

        try:
            await self.mqtt_publisher.publish_data(payload)
            if reason:
                logger.info(
                    "MODE: Published state %s (%s)",
                    self._mode_value,
                    reason,
                )
        except Exception as e:
            logger.debug("MODE publish failed: %s", e)

    def _maybe_persist_table_state(
        self,
        _parsed: dict[str, Any] | None,
        _table_name: str | None,
        _device_id: str | None,
    ) -> None:
        """Uloží poslední známé hodnoty pro vybrané tabulky (pro obnovu po restartu)."""

    async def _publish_prms_if_ready(
            self, *, reason: str | None = None) -> None:
        """Publikuje uložené *_prms hodnoty do MQTT (obnova po restartu/reconnectu)."""
        if not self._prms_tables:
            return

        if not self.mqtt_publisher.is_ready():
            self._prms_pending_publish = True
            return

        if self.device_id == "AUTO":
            self._prms_pending_publish = True
            return

        # Publish jen když je potřeba (startup nebo po MQTT reconnectu)
        if not self._prms_pending_publish and reason not in (
                "startup", "device_autodetect"):
            return

        for table_name, values in self._prms_tables.items():
            if not isinstance(values, dict) or not values:
                continue
            payload: dict[str, Any] = {"_table": table_name, **values}
            try:
                await self.mqtt_publisher.publish_data(payload)
            except Exception as e:
                logger.debug("STATE publish failed (%s): %s", table_name, e)
                self._prms_pending_publish = True
                return

        self._prms_pending_publish = False
        if reason:
            logger.info("STATE: Published snapshot (%s)", reason)

    async def _handle_mode_update(
        self,
        new_mode: Any,
        device_id: str | None,
        source: str
    ) -> None:
        """Uloží a publikuje MODE pokud máme nové info."""
        if new_mode is None:
            return
        try:
            mode_int = int(new_mode)
        except Exception:
            return
        if mode_int < 0 or mode_int > 5:
            logger.debug(
                "MODE: Value %s out of range 0-5, source %s, ignoring",
                mode_int,
                source,
            )
            return

        if mode_int != self._mode_value:
            self._mode_value = mode_int
            await asyncio.to_thread(
                save_mode_state,
                mode_int,
                device_id or self.device_id or self._mode_device_id)
            logger.info("MODE: %s → %s", source, mode_int)
        if device_id:
            self._mode_device_id = device_id

        await self._publish_mode_if_ready(device_id, reason=source)

    async def _maybe_process_mode(
        self,
        parsed: dict[str, Any],
        table_name: str | None,
        device_id: str | None
    ) -> None:
        """Detekuje MODE ze známých zdrojů a zajistí publish + persist."""
        if not parsed:
            return

        if table_name == "tbl_box_prms" and "MODE" in parsed:
            await self._handle_mode_update(parsed.get("MODE"), device_id, "tbl_box_prms")
            return

        if table_name == "tbl_events":
            content = parsed.get("Content")
            if content:
                new_mode = self.parser.parse_mode_from_event(str(content))
                if new_mode is not None:
                    await self._handle_mode_update(new_mode, device_id, "tbl_events")

    def _note_mqtt_ready_transition(self, mqtt_ready: bool) -> None:
        """Uloží změnu MQTT readiness (bez re-publish ze snapshotu)."""
        self._mqtt_was_ready = mqtt_ready

    def _log_status_heartbeat(self) -> None:
        if self._hb_interval_s <= 0:
            return
        now = time.time()
        if (now - self._last_hb_ts) < self._hb_interval_s:
            return
        self._last_hb_ts = now

        last_data_age = "n/a"
        if self._last_data_epoch is not None:
            last_data_age = f"{int(now - self._last_data_epoch)}s"

        box_uptime = "n/a"
        if self._box_connected_since_epoch is not None:
            box_uptime = f"{int(now - self._box_connected_since_epoch)}s"

        logger.info(
            "💓 HB: mode=%s box=%s cloud=%s cloud_sess=%s mqtt=%s q_mqtt=%s "
            "frames_rx=%s tx=%s ack=%s/%s last_data_age=%s box_uptime=%s",
            self._hm.mode.value,
            "on" if self.box_connected else "off",
            "off" if self._hm.in_offline else "on",
            "on" if self.cloud_session_connected else "off",
            "on" if self.mqtt_publisher.is_ready() else "off",
            self.mqtt_publisher.queue.size(),
            self.stats["frames_received"],
            self.stats["frames_forwarded"],
            self.stats["acks_local"],
            self.stats["acks_cloud"],
            last_data_age,
            box_uptime,
        )

    async def _proxy_status_loop(self) -> None:
        """Periodicky publikuje proxy_status do MQTT (pro HA restart)."""
        if PROXY_STATUS_INTERVAL <= 0:
            logger.info("Proxy status loop disabled (interval <= 0)")
            return

        logger.info(
            "Proxy status: periodic publish every %ss",
            PROXY_STATUS_INTERVAL,
        )
        while True:
            await asyncio.sleep(PROXY_STATUS_INTERVAL)
            try:
                mqtt_ready = self.mqtt_publisher.is_ready()
                self._note_mqtt_ready_transition(mqtt_ready)
                await self.publish_proxy_status()
                self._log_status_heartbeat()
            except Exception as e:
                logger.debug("Proxy status loop publish failed: %s", e)


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
            self._active_box_writer = writer
            self._active_box_peer = f"{addr[0]}:{addr[1]}" if addr else None
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
            if self._ctrl.mqtt_enabled:
                await self._ctrl.note_box_disconnect()
            await self._unregister_box_connection(writer)
            await self.publish_proxy_status()

    async def _handle_online_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int
    ) -> None:
        """Zpětná kompatibilita: ONLINE režim je řešen per-frame v `_handle_box_connection()`."""
        await self._handle_box_connection(box_reader, box_writer, conn_id)

    async def _note_cloud_failure(
            self,
            *,
            reason: str,
            local_ack: bool | None = None) -> None:
        """Zaznamená cloud selhání. V HYBRID mode může přepnout do offline."""
        logger.debug("☁️ Cloud failure noted: %s", reason)
        self._hm.record_failure(reason=reason, local_ack=local_ack)

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
        if (
            not table_name
            and parsed.get("Result") in ("IsNewSet", "IsNewWeather", "IsNewFW")
        ):
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

    async def _fallback_offline_from_cloud_issue(
        self,
        *,
        reason: str,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
        note_cloud_failure: bool = True,
        send_box_ack: bool = True,
        conn_id: int | None = None,
    ) -> tuple[None, None]:
        if self.cloud_session_connected:
            self._tc.record_cloud_session_end(reason=reason)
        self.cloud_session_connected = False
        await self._close_writer(cloud_writer)
        await self._process_frame_offline(
            frame_bytes,
            table_name,
            device_id,
            box_writer,
            send_ack=send_box_ack,
            conn_id=conn_id,
        )
        if note_cloud_failure:
            await self._note_cloud_failure(reason=reason, local_ack=send_box_ack)
        return None, None

    async def _ensure_cloud_connected(
        self,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        *,
        conn_id: int,
        table_name: str | None,
        connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None, bool]:
        # Check if we should try to connect to cloud
        if not self._hm.should_try_cloud():
            await self._close_writer(cloud_writer)
            if self.cloud_session_connected:
                self._tc.record_cloud_session_end(reason="manual_offline")
            self.cloud_session_connected = False
            return None, None, False
        if cloud_writer is not None and not cloud_writer.is_closing():
            return cloud_reader, cloud_writer, False
        try:
            target_host = resolve_cloud_host(TARGET_SERVER)
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, TARGET_PORT),
                timeout=connect_timeout_s,
            )
            self.cloud_connects += 1
            was_connected = self.cloud_session_connected
            self.cloud_session_connected = True
            if not was_connected:
                self._cloud_connected_since_epoch = time.time()
                self._cloud_peer = f"{target_host}:{TARGET_PORT}"
            if not was_connected:
                logger.info(
                    "☁️ Cloud session connected (%s:%s, conn=%s, table=%s)",
                    TARGET_SERVER,
                    TARGET_PORT,
                    conn_id,
                    table_name or "-",
                )
            return cloud_reader, cloud_writer, True
        except Exception as e:
            logger.warning(
                "⚠️ Cloud unavailable: %s (conn=%s, table=%s)",
                e,
                conn_id,
                table_name,
            )
            self.cloud_errors += 1
            self.cloud_session_connected = False
            await self._close_writer(cloud_writer)
            return None, None, True

    async def _handle_cloud_connection_failed(
        self,
        *,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
        cloud_attempted: bool,
    ) -> tuple[None, None]:
        self._tc.cloud_failed_in_window = True
        if cloud_attempted:
            self._tc.fire_event(
                "error_cloud_connect",
                cloud_host=TARGET_SERVER,
                reason="connect_failed",
            )
        if self._hm.is_hybrid_mode():
            self._hm.record_failure(
                reason="connect_failed", local_ack=True)
            return await self._fallback_offline_from_cloud_issue(
                reason="connect_failed",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        if self._cloud_rx_buf:
            self._cloud_rx_buf.clear()
        self._tc.record_timeout(conn_id=conn_id)
        return None, None

    async def _handle_cloud_eof(
        self,
        *,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[None, None]:
        self._tc.cloud_failed_in_window = True
        logger.warning(
            "⚠️ Cloud closed connection (conn=%s, table=%s)",
            conn_id,
            table_name,
        )
        self.cloud_disconnects += 1
        if self.cloud_session_connected:
            self._tc.record_cloud_session_end(reason="eof")
        self._tc.fire_event(
            "error_cloud_disconnect", reason="eof")
        self._hm.record_failure(
            reason="cloud_eof",
            local_ack=self._hm.is_hybrid_mode(),
        )
        await self._close_writer(cloud_writer)
        self._cloud_rx_buf.clear()
        if self._hm.is_hybrid_mode():
            return await self._fallback_offline_from_cloud_issue(
                reason="cloud_eof",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=None,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        self._tc.record_timeout(conn_id=conn_id)
        return None, None

    async def _handle_cloud_timeout(
        self,
        *,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        self._tc.cloud_failed_in_window = True
        self.cloud_timeouts += 1
        self._tc.fire_event(
            "error_cloud_timeout",
            cloud_host=TARGET_SERVER,
            timeout_s=CLOUD_ACK_TIMEOUT,
        )
        self._hm.record_failure(
            reason="ack_timeout",
            local_ack=self._hm.is_hybrid_mode(),
        )
        logger.warning(
            "⏱️ Cloud ACK timeout (%.1fs) (conn=%s, table=%s)",
            CLOUD_ACK_TIMEOUT,
            conn_id,
            table_name,
        )
        if self._hm.is_hybrid_mode():
            if table_name == "END":
                logger.info(
                    "📤 HYBRID: Sending local END (conn=%s)",
                    conn_id,
                )
                end_frame = self._build_end_time_frame()
                self._tc.record_response(
                    end_frame.decode("utf-8", errors="replace"),
                    source="local",
                    conn_id=conn_id,
                )
                box_writer.write(end_frame)
                await box_writer.drain()
                self.stats["acks_local"] += 1
                return cloud_reader, cloud_writer
            return await self._fallback_offline_from_cloud_issue(
                reason="ack_timeout",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        if self.cloud_session_connected:
            self._tc.record_cloud_session_end(reason="timeout")
        await self._close_writer(cloud_writer)
        self._cloud_rx_buf.clear()
        self._tc.record_timeout(conn_id=conn_id)
        return None, None

    async def _handle_cloud_error(
        self,
        *,
        error: Exception,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[None, None]:
        self._tc.cloud_failed_in_window = True
        logger.warning(
            "⚠️ Cloud error: %s (conn=%s, table=%s)",
            error,
            conn_id,
            table_name,
        )
        self.cloud_errors += 1
        if self.cloud_session_connected:
            self._tc.record_cloud_session_end(reason="cloud_error")
        self._hm.record_failure(
            reason="cloud_error",
            local_ack=self._hm.is_hybrid_mode(),
        )
        await self._close_writer(cloud_writer)
        self._cloud_rx_buf.clear()
        if self._hm.is_hybrid_mode():
            return await self._fallback_offline_from_cloud_issue(
                reason="cloud_error",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=None,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        self._tc.record_timeout(conn_id=conn_id)
        return None, None

    async def _send_frame_to_cloud(
        self,
        *,
        frame_bytes: bytes,
        cloud_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader,
        table_name: str | None,
        conn_id: int,
    ) -> tuple[bytes | None, None]:
        cloud_writer.write(frame_bytes)
        await cloud_writer.drain()
        self.stats["frames_forwarded"] += 1
        self._tc.cloud_ok_in_window = True
        ack_data = await self._read_cloud_ack(
            cloud_reader=cloud_reader,
            ack_timeout_s=CLOUD_ACK_TIMEOUT,
            ack_max_bytes=self._CLOUD_ACK_MAX_BYTES,
        )
        if not ack_data:
            raise EOFError("Cloud closed connection")
        return ack_data, None

    async def _read_cloud_ack(
        self,
        *,
        cloud_reader: asyncio.StreamReader,
        ack_timeout_s: float,
        ack_max_bytes: int,
    ) -> bytes:
        existing = self._extract_one_xml_frame(self._cloud_rx_buf)
        if existing is not None:
            return existing

        async def _read_until_frame() -> bytes:
            while True:
                chunk = await cloud_reader.read(4096)
                if not chunk:
                    return b""
                self._cloud_rx_buf.extend(chunk)
                frame = self._extract_one_xml_frame(self._cloud_rx_buf)
                if frame is not None:
                    return frame
                if len(self._cloud_rx_buf) > ack_max_bytes:
                    data = bytes(self._cloud_rx_buf)
                    self._cloud_rx_buf.clear()
                    return data

        return await asyncio.wait_for(_read_until_frame(), timeout=ack_timeout_s)

    _extract_one_xml_frame = staticmethod(extract_one_xml_frame)

    async def _forward_ack_to_box(
        self,
        *,
        ack_data: bytes,
        table_name: str | None,
        box_writer: asyncio.StreamWriter,
        conn_id: int,
    ) -> None:
        self._hm.record_success()
        ack_str = ack_data.decode("utf-8", errors="replace")
        self._tc.cloud_ok_in_window = True
        capture_payload(
            None,
            table_name,
            ack_str,
            ack_data,
            {},
            direction="cloud_to_proxy",
            length=len(ack_data),
            conn_id=conn_id,
            peer=self._active_box_peer,
        )
        self._tc.record_response(
            ack_str, source="cloud", conn_id=conn_id
        )
        if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
            self._isnew_last_response = self._last_data_iso
            if self._isnew_last_poll_epoch:
                self._isnew_last_rtt_ms = round(
                    (time.time() - self._isnew_last_poll_epoch) * 1000, 1
                )
        box_writer.write(ack_data)
        await box_writer.drain()
        self.stats["acks_cloud"] += 1

    async def _forward_frame_online(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        if self._hm.force_offline_enabled():
            return await self._handle_frame_offline_mode(
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )

        cloud_reader, cloud_writer, cloud_attempted = await self._ensure_cloud_connected(
            cloud_reader,
            cloud_writer,
            conn_id=conn_id,
            table_name=table_name,
            connect_timeout_s=connect_timeout_s,
        )

        if cloud_writer is None or cloud_reader is None:
            return await self._handle_cloud_connection_failed(
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
                cloud_attempted=cloud_attempted,
            )

        try:
            ack_data, _ = await self._send_frame_to_cloud(
                frame_bytes=frame_bytes,
                cloud_writer=cloud_writer,
                cloud_reader=cloud_reader,
                table_name=table_name,
                conn_id=conn_id,
            )
            if not ack_data:
                return await self._handle_cloud_eof(
                    conn_id=conn_id,
                    table_name=table_name,
                    frame_bytes=frame_bytes,
                    device_id=device_id,
                    box_writer=box_writer,
                    cloud_writer=cloud_writer,
                )

            await self._forward_ack_to_box(
                ack_data=ack_data,
                table_name=table_name,
                box_writer=box_writer,
                conn_id=conn_id,
            )
            return cloud_reader, cloud_writer

        except asyncio.TimeoutError:
            return await self._handle_cloud_timeout(
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_reader=cloud_reader,
                cloud_writer=cloud_writer,
            )
        except EOFError:
            return await self._handle_cloud_eof(
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )
        except Exception as e:
            return await self._handle_cloud_error(
                error=e,
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )

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

        self._maybe_persist_table_state(parsed, table_name, device_id)
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
            self._cache_last_values(parsed, table_name)
            await self._handle_setting_event(parsed, table_name, device_id)
            await self._ctrl.observe_box_frame(parsed, table_name, frame)
            await self._maybe_process_mode(parsed, table_name, device_id)
            await self._ctrl.maybe_start_next()
            await self.mqtt_publisher.publish_data(parsed)

        return device_id, table_name

    _infer_table_name = staticmethod(infer_table_name)
    _infer_device_id = staticmethod(infer_device_id)

    async def _handle_frame_offline_mode(
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
        if self.cloud_session_connected:
            self._tc.record_cloud_session_end(reason="manual_offline")
        self.cloud_session_connected = False
        await self._process_frame_offline(
            frame_bytes,
            table_name,
            device_id,
            box_writer,
            conn_id=conn_id,
        )
        return None, None

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

        try:
            while True:
                data = await self._read_box_bytes(
                    box_reader, conn_id=conn_id, idle_timeout_s=box_idle_timeout_s
                )
                if data is None:
                    break

                frame = data.decode("utf-8", errors="replace")
                try:
                    device_id, table_name = await self._process_box_frame_common(
                        frame_bytes=data, frame=frame, conn_id=conn_id
                    )
                except Exception:
                    # Nechceme shazovat celé BOX spojení kvůli chybě v publish/discovery/parsing.
                    # Traceback nám pomůže najít přesnou příčinu (např. regex v
                    # některé knihovně).
                    logger.exception(
                        "❌ Frame processing error (conn=%s, peer=%s)",
                        conn_id,
                        self._active_box_peer,
                    )
                    continue

                if self._maybe_handle_local_setting_ack(
                    frame, box_writer, conn_id=conn_id
                ):
                    continue
                self._tc.force_logs_this_window = False
                current_mode = await self._hm.get_current_mode()

                if current_mode == ProxyMode.OFFLINE:
                    cloud_reader, cloud_writer = await self._handle_frame_offline_mode(
                        frame_bytes=data,
                        table_name=table_name,
                        device_id=device_id,
                        conn_id=conn_id,
                        box_writer=box_writer,
                        cloud_writer=cloud_writer,
                    )
                    continue

                if current_mode == ProxyMode.HYBRID and not self._hm.should_try_cloud():
                    cloud_reader, cloud_writer = await self._handle_frame_offline_mode(
                        frame_bytes=data,
                        table_name=table_name,
                        device_id=device_id,
                        conn_id=conn_id,
                        box_writer=box_writer,
                        cloud_writer=cloud_writer,
                    )
                    continue

                cloud_reader, cloud_writer = await self._forward_frame_online(
                    frame_bytes=data,
                    table_name=table_name,
                    device_id=device_id,
                    conn_id=conn_id,
                    box_writer=box_writer,
                    cloud_reader=cloud_reader,
                    cloud_writer=cloud_writer,
                    connect_timeout_s=cloud_connect_timeout_s,
                )

        except ConnectionResetError:
            # Běžné: BOX přeruší TCP (např. reconnect po modem resetu).
            # Nechceme z toho dělat ERROR.
            logger.debug(
                "🔌 BOX closed the connection (RST, conn=%s, peer=%s)",
                conn_id,
                self._active_box_peer,
            )
        except Exception:
            logger.exception(
                "❌ Box connection handler error (conn=%s, peer=%s)",
                conn_id,
                self._active_box_peer,
            )
        finally:
            await self._close_writer(cloud_writer)
            if self.cloud_session_connected:
                self._tc.record_cloud_session_end(reason="box_disconnect")
            self.cloud_session_connected = False
            self._cloud_rx_buf.clear()

    async def _process_frame_offline(
        self,
        _frame_bytes: bytes,
        table_name: str | None,
        _device_id: str | None,
        box_writer: asyncio.StreamWriter,
        *,
        send_ack: bool = True,
        conn_id: int | None = None,
    ):
        """Zpracuj frame v offline režimu - lokální ACK pouze (žádné queueování)."""
        if send_ack:
            ack_response = self._build_offline_ack_frame(table_name)
            if conn_id is not None:
                self._tc.record_response(
                    ack_response.decode("utf-8", errors="replace"),
                    source="local",
                    conn_id=conn_id,
                )
            box_writer.write(ack_response)
            await box_writer.drain()
            self.stats["acks_local"] += 1

    async def _handle_offline_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int
    ) -> None:
        """Zpětná kompatibilita: OFFLINE režim je řešen per-frame.

        Vše se odbavuje v `_handle_box_connection()`.
        """
        await self._handle_box_connection(box_reader, box_writer, conn_id)

    def get_stats(self) -> dict[str, Any]:
        """Vrátí statistiky proxy."""
        return {
            "mode": self._hm.mode.value,
            "configured_mode": self._hm.configured_mode,
            "cloud_online": not self._hm.in_offline,
            "hybrid_fail_count": self._hm.fail_count,
            "mqtt_queue_size": self.mqtt_publisher.queue.size(),
            "mqtt_connected": self.mqtt_publisher.connected,
            **self.stats
        }

    @staticmethod
    def _parse_setting_event(
            content: str) -> tuple[str, str, str | None, str | None] | None:
        # Example:
        #   "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]"
        m = re.search(
            r"tbl_([a-z0-9_]+)\s*/\s*([A-Z0-9_]+):\s*\[([^\]]*)\]\s*->\s*\[([^\]]*)\]",
            content,
        )
        if not m:
            return None
        tbl_name = f"tbl_{m.group(1)}"
        return tbl_name, m.group(2), m.group(3), m.group(4)

    async def _handle_setting_event(
        self,
        parsed: dict[str, Any],
        table_name: str | None,
        device_id: str | None,
    ) -> None:
        if table_name != "tbl_events":
            return
        if not parsed or parsed.get("Type") != "Setting":
            return
        content = parsed.get("Content")
        if not content:
            return
        ev = self._parse_setting_event(str(content))
        if not ev:
            return
        tbl_name, tbl_item, _old_value, new_value = ev
        if new_value is None:
            return
        # Record for telemetry (cloud or local applied setting)
        self._set_commands_buffer.append({
            "key": f"{tbl_name}:{tbl_item}",
            "value": str(new_value),
            "result": "applied",
            "source": "tbl_events",
        })
        await self._ctrl.publish_setting_event_state(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=new_value,
            device_id=device_id,
            source="tbl_events",
        )

    def _cache_last_values(
            self, _parsed: dict[str, Any], _table_name: str | None) -> None:
        return

    # ---------------------------------------------------------------------
    # Control API (prototype)
    # ---------------------------------------------------------------------

    def get_control_api_health(self) -> dict[str, Any]:
        """Vrátí stavové info pro Control API health endpoint."""
        now = time.time()
        last_age_s: float | None = None
        if self._last_data_epoch is not None:
            last_age_s = max(0.0, now - self._last_data_epoch)
        return {
            "ok": True,
            "device_id": None if self.device_id == "AUTO" else self.device_id,
            "box_connected": bool(self.box_connected),
            "box_peer": self._active_box_peer,
            "box_data_age_s": last_age_s,
        }

    def control_api_send_setting(
            self,
            *,
            tbl_name: str,
            tbl_item: str,
            new_value: str,
            confirm: str = "New",
    ) -> dict[str, Any]:
        """Odešle Setting do BOXu přes event loop a vrátí výsledek."""
        if not self._validate_event_loop_ready():
            return {"ok": False, "error": "event_loop_not_ready"}

        return self._send_setting_via_event_loop(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=new_value,
            confirm=confirm,
        )

    def _validate_event_loop_ready(self) -> bool:
        """Ověří že event loop je připraven."""
        return self._loop is not None

    def _send_setting_via_event_loop(
            self,
            *,
            tbl_name: str,
            tbl_item: str,
            new_value: str,
            confirm: str,
    ) -> dict[str, Any]:
        """Pošle setting přes event loop s timeoutem."""
        validation = self._validate_control_parameters(
            tbl_name, tbl_item, new_value
        )
        if not validation["ok"]:
            return validation

        return self._run_coroutine_threadsafe(tbl_name, tbl_item, new_value, confirm)

    def _validate_control_parameters(
            self,
            tbl_name: str,  # noqa: ARG001 - parameter reserved for future use
            tbl_item: str,  # noqa: ARG001 - parameter reserved for future use
            new_value: str,  # noqa: ARG001 - parameter reserved for future use
    ) -> dict[str, Any]:
        """Validace parametrů pro control."""
        if not self.box_connected:
            return {"ok": False, "error": "box_not_connected"}
        if self._last_data_epoch is None or (
                time.time() - self._last_data_epoch) > 30:
            return {"ok": False, "error": "box_not_sending_data"}
        if self.device_id == "AUTO":
            return {"ok": False, "error": "device_id_unknown"}
        return {"ok": True}

    def _build_control_frame(
            self,
            tbl_name: str,
            tbl_item: str,
            new_value: str,
            confirm: str,
    ) -> bytes:
        """Sestaví rámec pro nastavení."""
        msg_id = secrets.randbelow(90_000_000) + 10_000_000
        id_set = int(time.time())
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)

        inner = (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{self.device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{now_local.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
            f"<NewValue>{new_value}</NewValue>"
            f"<Confirm>{confirm}</Confirm>"
            f"<TblName>{tbl_name}</TblName>"
            f"<TblItem>{tbl_item}</TblItem>"
            "<ID_Server>5</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
            "<ver>55734</ver>"
        )
        return build_frame(
            inner,
            add_crlf=True).encode(
            "utf-8",
            errors="strict")

    def _run_coroutine_threadsafe(
            self, tbl_name: str, tbl_item: str, new_value: str, confirm: str
    ) -> dict[str, Any]:
        """Spustí coroutines threadsafe s timeoutem."""
        fut = asyncio.run_coroutine_threadsafe(
            self._send_setting_to_box(
                tbl_name=tbl_name,
                tbl_item=tbl_item,
                new_value=new_value,
                confirm=confirm,
            ),
            self._loop if self._loop else None,  # type: ignore[arg-type]
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as e:
            return {"ok": False, "error": f"send_failed:{type(e).__name__}"}

    async def _send_setting_to_box(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: str,
        confirm: str,
        tx_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.box_connected:
            return {"ok": False, "error": "box_not_connected"}
        if self._last_data_epoch is None or (
                time.time() - self._last_data_epoch) > 30:
            return {"ok": False, "error": "box_not_sending_data"}
        if self.device_id == "AUTO":
            return {"ok": False, "error": "device_id_unknown"}

        async with self._box_conn_lock:
            writer = self._active_box_writer
        if writer is None:
            return {"ok": False, "error": "no_active_box_writer"}

        msg_id = secrets.randbelow(90_000_000) + 10_000_000
        id_set = int(time.time())
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)

        inner = (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{self.device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{now_local.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
            f"<NewValue>{new_value}</NewValue>"
            f"<Confirm>{confirm}</Confirm>"
            f"<TblName>{tbl_name}</TblName>"
            f"<TblItem>{tbl_item}</TblItem>"
            "<ID_Server>5</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
            "<ver>55734</ver>"
        )
        frame = build_frame(
            inner,
            add_crlf=True).encode(
            "utf-8",
            errors="strict")

        self._local_setting_pending = {
            "sent_at": time.monotonic(),
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": new_value,
            "id": msg_id,
            "id_set": id_set,
            "tx_id": tx_id,
        }

        writer.write(frame)
        await writer.drain()

        logger.info(
            "CONTROL: Sent Setting %s/%s=%s (id=%s id_set=%s)",
            tbl_name,
            tbl_item,
            new_value,
            msg_id,
            id_set,
        )

        return {
            "ok": True,
            "sent": True,
            "device_id": self.device_id,
            "id": msg_id,
            "id_set": id_set,
        }

    def _maybe_handle_local_setting_ack(
        self, frame: str, box_writer: asyncio.StreamWriter, *, conn_id: int
    ) -> bool:
        _ = conn_id
        pending = self._local_setting_pending
        if not pending:
            return False
        if (
            time.monotonic() - float(pending.get("sent_at", 0.0))
        ) > self._ctrl.ack_timeout_s:
            return False
        if "<Reason>Setting</Reason>" not in frame:
            return False
        if "<Result>ACK</Result>" not in frame and "<Result>NACK</Result>" not in frame:
            return False

        ack_ok = "<Result>ACK</Result>" in frame
        tx_id = pending.get("tx_id")

        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)
        end_inner = (
            "<Result>END</Result>"
            f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
            f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
        )
        end_frame = build_frame(
            end_inner,
            add_crlf=True).encode(
            "utf-8",
            errors="strict")

        box_writer.write(end_frame)
        try:
            task = asyncio.create_task(box_writer.drain())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except Exception as exc:
            logger.debug("CONTROL: Failed to schedule END drain: %s", exc)

        try:
            task = asyncio.create_task(
                self._ctrl.on_box_setting_ack(
                    tx_id=str(tx_id) if tx_id else None,
                    ack=ack_ok,
                )
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except Exception as exc:
            logger.debug("CONTROL: Failed to schedule ACK handling: %s", exc)

        logger.info(
            "CONTROL: BOX responded to local Setting (sent END), last=%s/%s=%s",
            pending.get("tbl_name"),
            pending.get("tbl_item"),
            pending.get("new_value"),
        )
        # Record for telemetry (local control command)
        self._set_commands_buffer.append({
            "key": f"{pending.get('tbl_name')}:{pending.get('tbl_item')}",
            "value": str(pending.get("new_value", "")),
            "result": "ack" if ack_ok else "nack",
            "source": "local",
        })
        self._local_setting_pending = None
        return True
