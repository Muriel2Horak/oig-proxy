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
from collections import Counter, defaultdict, deque
from contextlib import suppress
import re
from datetime import datetime, timezone
from typing import Any

from parser import OIGDataParser
from config import (
    CLOUD_ACK_TIMEOUT,
    CONTROL_API_HOST,
    CONTROL_API_PORT,
    CONTROL_MQTT_ACK_TIMEOUT_S,
    CONTROL_MQTT_APPLIED_TIMEOUT_S,
    CONTROL_MQTT_BOX_READY_SECONDS,
    CONTROL_MQTT_ENABLED,
    CONTROL_MQTT_MODE_QUIET_SECONDS,
    CONTROL_MQTT_QOS,
    CONTROL_MQTT_LOG_ENABLED,
    CONTROL_MQTT_LOG_PATH,
    CONTROL_MQTT_PENDING_PATH,
    CONTROL_MQTT_RETAIN,
    CONTROL_MQTT_RESULT_TOPIC,
    CONTROL_MQTT_SET_TOPIC,
    CONTROL_MQTT_STATUS_PREFIX,
    CONTROL_MQTT_STATUS_RETAIN,
    CONTROL_WRITE_WHITELIST,
    LOCAL_GETACTUAL_ENABLED,
    LOCAL_GETACTUAL_INTERVAL_S,
    FULL_REFRESH_INTERVAL_H,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    PROXY_MODE,
    PROXY_STATUS_INTERVAL,
    PROXY_STATUS_ATTRS_TOPIC,
    HYBRID_CONNECT_TIMEOUT,
    HYBRID_FAIL_THRESHOLD,
    HYBRID_RETRY_INTERVAL,
    MQTT_NAMESPACE,
    MQTT_PUBLISH_QOS,
    MQTT_STATE_RETAIN,
    TARGET_PORT,
    TARGET_SERVER,
    TELEMETRY_ENABLED,
    TELEMETRY_INTERVAL_S,
)
from control_api import ControlAPIServer
from telemetry_client import TelemetryClient
from oig_frame import build_frame
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

ISNEW_STATE_TOPIC_ALIASES = {
    "isnewfw": "IsNewFW",
    "isnewset": "IsNewSet",
    "isnewweather": "IsNewWeather",
}


class _TelemetryLogHandler(logging.Handler):
    def __init__(self, proxy: "OIGProxy") -> None:
        super().__init__()
        self._proxy = proxy

    def emit(self, record: logging.LogRecord) -> None:
        # pylint: disable=protected-access
        self._proxy._record_log_entry(record)


# ============================================================================
# OIG Proxy - hlavní proxy server
# ============================================================================

class OIGProxy:
    """OIG Proxy s podporou ONLINE/HYBRID/OFFLINE režimů."""

    # Frame string constants
    _RESULT_ACK = "<Result>ACK</Result>"
    _RESULT_END = "<Result>END</Result>"
    _TIME_OFFSET = "+00:00"
    _POST_DRAIN_SA_KEY = "post_drain_sa_refresh"

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
        self._table_cache: dict[str, dict[str, Any]] = {}
        self._mqtt_cache_device_id: str | None = None
        self._mqtt_was_ready: bool = False

        # Proxy mode from config (online/hybrid/offline)
        self._configured_mode: str = PROXY_MODE
        # Current runtime mode (for HYBRID: can flip between online/offline)
        self.mode = self._get_initial_mode()
        self.mode_lock = asyncio.Lock()

        # HYBRID mode state
        self._hybrid_fail_count: int = 0
        self._hybrid_fail_threshold: int = HYBRID_FAIL_THRESHOLD
        self._hybrid_retry_interval: float = float(HYBRID_RETRY_INTERVAL)
        self._hybrid_connect_timeout: float = float(HYBRID_CONNECT_TIMEOUT)
        self._hybrid_last_offline_time: float = 0.0
        self._hybrid_in_offline: bool = False

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
        self._control_mqtt_enabled: bool = bool(CONTROL_MQTT_ENABLED)
        self._control_set_topic: str = CONTROL_MQTT_SET_TOPIC
        self._control_result_topic: str = CONTROL_MQTT_RESULT_TOPIC
        self._control_status_prefix: str = CONTROL_MQTT_STATUS_PREFIX
        self._control_qos: int = int(CONTROL_MQTT_QOS)
        self._control_retain: bool = bool(CONTROL_MQTT_RETAIN)
        self._control_status_retain: bool = bool(CONTROL_MQTT_STATUS_RETAIN)
        self._control_log_enabled: bool = bool(CONTROL_MQTT_LOG_ENABLED)
        self._control_log_path: str = str(CONTROL_MQTT_LOG_PATH)
        self._control_box_ready_s: float = float(
            CONTROL_MQTT_BOX_READY_SECONDS)
        self._control_ack_timeout_s: float = float(CONTROL_MQTT_ACK_TIMEOUT_S)
        self._control_applied_timeout_s: float = float(
            CONTROL_MQTT_APPLIED_TIMEOUT_S)
        self._control_mode_quiet_s: float = float(
            CONTROL_MQTT_MODE_QUIET_SECONDS)
        self._control_whitelist: dict[str, set[str]] = CONTROL_WRITE_WHITELIST
        self._control_max_attempts: int = 5
        self._control_retry_delay_s: float = 120.0
        self._control_session_id: str = uuid.uuid4().hex
        self._control_pending_path: str = str(CONTROL_MQTT_PENDING_PATH)
        self._control_pending_keys: set[str] = self._control_load_pending_keys(
        )
        self._proxy_status_attrs_topic: str = str(PROXY_STATUS_ATTRS_TOPIC)
        self._local_getactual_enabled: bool = bool(LOCAL_GETACTUAL_ENABLED)
        self._local_getactual_interval_s: float = float(
            LOCAL_GETACTUAL_INTERVAL_S)
        self._local_getactual_task: asyncio.Task[Any] | None = None
        self._full_refresh_interval_h: int = int(FULL_REFRESH_INTERVAL_H)
        self._full_refresh_task: asyncio.Task[Any] | None = None

        # Telemetry to diagnostic server (muriel-cz.cz)
        self._telemetry_client: TelemetryClient | None = None
        self._telemetry_task: asyncio.Task[Any] | None = None
        self._telemetry_interval_s: int = TELEMETRY_INTERVAL_S
        self._start_time: float = time.time()
        # prevent task GC
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._telemetry_box_sessions: deque[dict[str, Any]] = deque()
        self._telemetry_cloud_sessions: deque[dict[str, Any]] = deque()
        self._telemetry_hybrid_sessions: deque[dict[str, Any]] = deque()
        self._telemetry_offline_events: deque[dict[str, Any]] = deque()
        self._telemetry_tbl_events: deque[dict[str, Any]] = deque()
        self._telemetry_error_context: deque[dict[str, Any]] = deque()
        self._telemetry_logs: deque[dict[str, Any]] = deque()
        self._telemetry_log_window_s: int = 60
        self._telemetry_log_max: int = 1000
        self._telemetry_log_error: bool = False
        self._telemetry_debug_windows_remaining: int = 0
        self._telemetry_box_seen_in_window: bool = False
        self._telemetry_force_logs_this_window: bool = True
        self._telemetry_cloud_ok_in_window: bool = False
        self._telemetry_cloud_failed_in_window: bool = False
        # Treat very short EOFs as a timeout-like failure unless we observed a
        # successful cloud response in the same telemetry window.
        self._telemetry_cloud_eof_short_in_window: bool = False
        self._hybrid_state: str | None = None
        self._hybrid_state_since_epoch: float | None = None
        self._hybrid_last_offline_reason: str | None = None
        self._telemetry_req_pending: dict[int, deque[str]] = defaultdict(deque)
        self._telemetry_stats: dict[tuple[str, str, str], Counter[str]] = {}
        # Create copy for safe iteration during modification
        for handler in list(logger.handlers):  # noqa: C417
            if isinstance(handler, _TelemetryLogHandler):
                logger.removeHandler(handler)
        self._telemetry_log_handler = _TelemetryLogHandler(self)
        logger.addHandler(self._telemetry_log_handler)

        self._control_queue: deque[dict[str, Any]] = deque()
        self._control_inflight: dict[str, Any] | None = None
        self._control_lock = asyncio.Lock()
        self._control_ack_task: asyncio.Task[Any] | None = None
        self._control_applied_task: asyncio.Task[Any] | None = None
        self._control_quiet_task: asyncio.Task[Any] | None = None
        self._control_retry_task: asyncio.Task[Any] | None = None
        self._control_last_result: dict[str, Any] | None = None
        self._control_key_state: dict[str, dict[str, Any]] = {}
        self._control_post_drain_refresh_pending: bool = False

        self._box_connected_since_epoch: float | None = None
        self._last_box_disconnect_reason: str | None = None
        self._last_values: dict[tuple[str, str], Any] = {}

        if self._configured_mode == "hybrid":
            self._hybrid_state = "offline" if self._hybrid_in_offline else "online"
            self._hybrid_state_since_epoch = time.time()

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
        self._last_hb_ts: float = 0.0
        self._hb_interval_s: float = max(60.0, float(PROXY_STATUS_INTERVAL))

    async def start(self):
        """Spustí proxy server."""
        self._loop = asyncio.get_running_loop()
        self.mqtt_publisher.attach_loop(self._loop)

        if CONTROL_API_PORT and CONTROL_API_PORT > 0:
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

        # MQTT connect
        if self.mqtt_publisher.connect():
            await self.mqtt_publisher.start_health_check()
        else:
            logger.warning(
                "MQTT: Initial connect failed, health check will retry reconnect")
            await self.mqtt_publisher.start_health_check()

        if self._control_mqtt_enabled:
            self._setup_control_mqtt()
        self._setup_mqtt_state_cache()

        # Pokud máme uložené device_id a aktuální device je AUTO, použij ho
        if self.device_id == "AUTO":
            restored_device_id = self._mode_device_id or self._prms_device_id
            if restored_device_id:
                self.device_id = restored_device_id
                self.mqtt_publisher.device_id = restored_device_id
                logger.info(
                    "🔑 Restoring device_id from saved state: %s",
                    self.device_id,
                )
                self.mqtt_publisher.publish_availability()
                self._setup_mqtt_state_cache()

        # Log startup mode
        logger.info(
            "🚀 Proxy mode: %s (configured: %s)",
            self.mode.value,
            self._configured_mode,
        )

        # Po připojení MQTT publikuj stav (init)
        await self.publish_proxy_status()
        await self._control_publish_restart_errors()
        self._mqtt_was_ready = self.mqtt_publisher.is_ready()
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._proxy_status_loop())
        if self._full_refresh_task is None or self._full_refresh_task.done():
            self._full_refresh_task = asyncio.create_task(
                self._full_refresh_loop())

        # Initialize telemetry client (fail-safe, async provisioning)
        if TELEMETRY_ENABLED:
            self._init_telemetry()
            if self._telemetry_task is None or self._telemetry_task.done():
                self._telemetry_task = asyncio.create_task(
                    self._telemetry_loop())

        # Spustíme TCP server
        server = await asyncio.start_server(
            self.handle_connection,
            PROXY_LISTEN_HOST,
            PROXY_LISTEN_PORT
        )

        addr = server.sockets[0].getsockname()
        logger.info("🚀 OIG Proxy listening on %s:%s", addr[0], addr[1])
        logger.info("📡 Cloud target: %s:%s", TARGET_SERVER, TARGET_PORT)
        logger.info("🔄 Mode: %s", self.mode.value)

        async with server:
            await server.serve_forever()

    def _build_status_payload(self) -> dict[str, Any]:
        """Vytvoří payload pro proxy_status MQTT sensor."""
        inflight = self._control_inflight
        inflight_str = self._format_control_tx(inflight) if inflight else ""
        last_result_str = self._format_control_result(
            self._control_last_result)
        inflight_key = str(inflight.get("request_key")
                           or "") if inflight else ""
        queue_keys = [str(tx.get("request_key") or "")
                      for tx in self._control_queue]
        payload = {
            "status": self.mode.value,
            "mode": self.mode.value,
            "configured_mode": self._configured_mode,
            "control_session_id": self._control_session_id,
            "box_device_id": self.device_id if self.device_id != "AUTO" else None,
            "cloud_online": int(not self._hybrid_in_offline),
            "hybrid_fail_count": self._hybrid_fail_count,
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
            "control_queue_len": len(self._control_queue),
            "control_inflight": inflight_str,
            "control_inflight_key": inflight_key,
            "control_queue_keys": [k for k in queue_keys if k],
            "control_last_result": last_result_str,
        }
        return payload

    def _build_status_attrs_payload(self) -> dict[str, Any]:
        if self._control_inflight:
            inflight_key = str(self._control_inflight.get("request_key") or "")
        else:
            inflight_key = ""
        queue_keys = [
            str(tx.get("request_key") or "")
            for tx in self._control_queue
        ]
        return {
            "control_inflight_key": inflight_key,
            "control_queue_keys": [k for k in queue_keys if k],
        }

    @staticmethod
    def _format_control_tx(tx: dict[str, Any] | None) -> str:
        if not tx:
            return ""
        tbl = str(tx.get("tbl_name") or "")
        item = str(tx.get("tbl_item") or "")
        val = str(tx.get("new_value") or "")
        stage = str(tx.get("stage") or "")
        attempts = tx.get("_attempts")
        tx_id = str(tx.get("tx_id") or "")
        if attempts is None:
            return f"{tbl}/{item}={val} ({stage}) tx={tx_id}".strip()
        return f"{tbl}/{item}={val} ({stage} {attempts}) tx={tx_id}".strip()

    @staticmethod
    def _format_control_result(result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        status = str(result.get("status") or "")
        tbl = str(result.get("tbl_name") or "")
        item = str(result.get("tbl_item") or "")
        val = str(result.get("new_value") or "")
        err = result.get("error")
        tx_id = str(result.get("tx_id") or "")
        if err:
            return f"{status} {tbl}/{item}={val} err={err} tx={tx_id}".strip()
        return f"{status} {tbl}/{item}={val} tx={tx_id}".strip()

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
                qos=self._control_qos,
                retain=True,
            )
        except Exception as e:
            logger.debug("Proxy status attrs publish failed: %s", e)

    @staticmethod
    def _build_getactual_frame() -> bytes:
        inner = "<Result>ACK</Result><ToDo>GetActual</ToDo>"
        return build_frame(inner).encode("utf-8", errors="strict")

    @staticmethod
    def _build_ack_only_frame() -> bytes:
        inner = OIGProxy._RESULT_ACK
        return build_frame(inner).encode("utf-8", errors="strict")

    def _build_offline_ack_frame(self, table_name: str | None) -> bytes:
        if table_name == "END":
            return self._build_end_time_frame()
        if table_name == "IsNewSet":
            return self._build_end_time_frame()
        if table_name in ("IsNewWeather", "IsNewFW"):
            return build_frame(OIGProxy._RESULT_END).encode("utf-8", errors="strict")
        return self._build_ack_only_frame()

    @staticmethod
    def _build_end_time_frame() -> bytes:
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)
        inner = (
            "<Result>END</Result>"
            f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
            f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
        )
        return build_frame(inner).encode("utf-8", errors="strict")

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

    def _force_offline_enabled(self) -> bool:
        """Returns True if configured mode is OFFLINE."""
        return self._configured_mode == "offline"

    def _get_initial_mode(self) -> ProxyMode:
        """Determine initial ProxyMode from config."""
        if self._configured_mode == "offline":
            return ProxyMode.OFFLINE
        if self._configured_mode == "hybrid":
            return ProxyMode.HYBRID
        return ProxyMode.ONLINE

    def _is_hybrid_mode(self) -> bool:
        """Returns True if configured mode is HYBRID."""
        return self._configured_mode == "hybrid"

    def _should_try_cloud(self) -> bool:
        """Determine if we should try to connect to cloud.

        ONLINE mode: always try
        HYBRID mode: try if not in offline state, or if retry interval passed
        OFFLINE mode: never try
        """
        if self._configured_mode == "offline":
            return False
        if self._configured_mode == "online":
            return True
        # HYBRID mode
        if not self._hybrid_in_offline:
            return True
        # Check if retry interval passed
        elapsed = time.time() - self._hybrid_last_offline_time
        if elapsed >= self._hybrid_retry_interval:
            logger.info(
                "☁️ HYBRID: retry interval (%.0fs) passed, trying cloud...",
                self._hybrid_retry_interval,
            )
            return True
        return False

    def _hybrid_record_failure(
        self,
        *,
        reason: str | None = None,
        local_ack: bool | None = None,
    ) -> None:
        """Record a cloud failure for HYBRID mode."""
        if not self._is_hybrid_mode():
            return
        self._hybrid_fail_count += 1
        if self._hybrid_in_offline:
            # Restart offline window after each failed probe so we only
            # attempt once per retry interval.
            self._hybrid_last_offline_time = time.time()
            self._hybrid_last_offline_reason = reason or self._hybrid_last_offline_reason
        if self._hybrid_fail_count >= self._hybrid_fail_threshold:
            if not self._hybrid_in_offline:
                transition_time = time.time()
                self._record_hybrid_state_end(
                    ended_at=transition_time,
                    reason=reason or "cloud_failure",
                )
                self._hybrid_in_offline = True
                self._hybrid_last_offline_time = time.time()
                self._hybrid_state = "offline"
                self._hybrid_state_since_epoch = transition_time
                self._hybrid_last_offline_reason = reason or "unknown"
                self._record_offline_event(reason=reason, local_ack=local_ack)
                logger.warning(
                    "☁️ HYBRID: %d failures → switching to offline mode",
                    self._hybrid_fail_count,
                )

    def _hybrid_record_success(self) -> None:
        """Record a cloud success for HYBRID mode."""
        if not self._is_hybrid_mode():
            return
        if self._hybrid_in_offline:
            logger.info(
                "☁️ HYBRID: cloud recovered → switching to online mode")
            transition_time = time.time()
            self._record_hybrid_state_end(
                ended_at=transition_time,
                reason=self._hybrid_last_offline_reason or "cloud_recovered",
            )
            self._hybrid_state = "online"
            self._hybrid_state_since_epoch = transition_time
            self._hybrid_last_offline_reason = None
        self._hybrid_fail_count = 0
        self._hybrid_in_offline = False

    async def _full_refresh_loop(self) -> None:
        interval_s = max(1, int(self._full_refresh_interval_h)) * 3600
        while True:
            await asyncio.sleep(interval_s)
            if self._force_offline_enabled():
                continue
            if not self.box_connected:
                continue
            if await self._get_current_mode() != ProxyMode.ONLINE:
                continue
            if self._control_inflight or self._control_queue:
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

    @staticmethod
    def _should_persist_table(table_name: str | None) -> bool:
        """Vrací True pro tabulky, které chceme perzistovat pro obnovu po restartu."""
        if not table_name or not table_name.startswith("tbl_"):
            return False

        # tbl_actual chodí typicky každých pár sekund → neperzistujeme
        # (zbytečné zápisy)
        if table_name == "tbl_actual":
            return False

        return True

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
            save_mode_state(
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

    async def _switch_mode(self, new_mode: ProxyMode) -> ProxyMode:
        """Atomicky přepne režim a vrátí předchozí hodnotu."""
        async with self.mode_lock:
            old_mode = self.mode
            if old_mode != new_mode:
                self.mode = new_mode
                self.stats["mode_changes"] += 1
            return old_mode

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
            self.mode.value,
            "on" if self.box_connected else "off",
            "off" if self._hybrid_in_offline else "on",
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

    # ============================================================================
    # Telemetry to diagnostic server
    # ============================================================================

    @staticmethod
    def _utc_iso(ts: float | None = None) -> str:
        if ts is None:
            ts = time.time()
        return datetime.fromtimestamp(
            ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _utc_log_ts(ts: float) -> str:
        return datetime.fromtimestamp(
            ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _prune_log_buffer(self) -> None:
        cutoff = time.time() - float(self._telemetry_log_window_s)
        while self._telemetry_logs and self._telemetry_logs[0]["_epoch"] < cutoff:
            self._telemetry_logs.popleft()
        while len(self._telemetry_logs) > self._telemetry_log_max:
            self._telemetry_logs.popleft()

    def _record_log_entry(self, record: logging.LogRecord) -> None:
        if getattr(self, "_telemetry_log_error", False):
            return
        if record.levelno >= logging.WARNING:
            self._telemetry_debug_windows_remaining = 2
        if (
            getattr(self, "_telemetry_debug_windows_remaining", 0) <= 0
            and not getattr(self, "_telemetry_force_logs_this_window", False)
        ):
            return
        try:
            entry = {
                "_epoch": record.created,
                "timestamp": self._utc_log_ts(record.created),
                "level": record.levelname,
                "message": record.getMessage(),
                "source": record.name,
            }
            self._telemetry_logs.append(entry)
            self._prune_log_buffer()
        except Exception:  # pylint: disable=broad-exception-caught
            self._telemetry_log_error = True
            try:
                logger.exception(
                    "Failed to record telemetry log entry for record %r",
                    record,
                )
            finally:
                self._telemetry_log_error = False

    def _snapshot_logs(self) -> list[dict[str, Any]]:
        self._prune_log_buffer()
        return [
            {k: v for k, v in item.items() if k != "_epoch"}
            for item in self._telemetry_logs
        ]

    def _flush_log_buffer(self) -> list[dict[str, Any]]:
        logs = self._snapshot_logs()
        self._telemetry_logs.clear()
        return logs

    def _telemetry_record_request(self, table_name: str | None, conn_id: int) -> None:
        if not hasattr(self, "_telemetry_req_pending"):
            self._telemetry_req_pending = defaultdict(deque)
        if not table_name:
            return
        queue = self._telemetry_req_pending[conn_id]
        queue.append(table_name)
        if len(queue) > 1000:
            queue.popleft()

    def _telemetry_response_kind(self, response_text: str) -> str:
        if "<Result>Weather</Result>" in response_text:
            return "resp_weather"
        if "<Result>END</Result>" in response_text:
            return "resp_end"
        if "<Result>NACK</Result>" in response_text:
            return "resp_nack"
        if "<Result>ACK</Result>" in response_text and "<ToDo>GetAll</ToDo>" in response_text:
            return "resp_ack_getall"
        if "<Result>ACK</Result>" in response_text and "<ToDo>GetActual</ToDo>" in response_text:
            return "resp_ack_getactual"
        if "<Result>ACK</Result>" in response_text:
            return "resp_ack"
        return "resp_other"

    def _telemetry_record_response(
        self,
        response_text: str,
        *,
        source: str,
        conn_id: int,
    ) -> None:
        if not hasattr(self, "_telemetry_req_pending"):
            self._telemetry_req_pending = defaultdict(deque)
        if not hasattr(self, "_telemetry_stats"):
            self._telemetry_stats = {}
        queue = self._telemetry_req_pending.get(conn_id)
        if queue:
            table_name = queue.popleft()
        else:
            table_name = "unmatched"
        if queue is not None and not queue:
            self._telemetry_req_pending.pop(conn_id, None)
        mode_value = getattr(self, "mode", None)
        if mode_value is None:
            mode_value = getattr(self, "_mode_value", ProxyMode.OFFLINE.value)
        if isinstance(mode_value, ProxyMode):
            mode_value_str = mode_value.value
        elif isinstance(mode_value, str):
            mode_value_str = str(mode_value).strip().lower()
        else:
            mode_value_str = (
                str(mode_value).strip().lower()
                if mode_value is not None
                else ProxyMode.OFFLINE.value
            )
        if mode_value_str not in {"online", "hybrid", "offline"}:
            mode_value_str = ProxyMode.OFFLINE.value
        key = (table_name, source, mode_value_str)
        stats = self._telemetry_stats.setdefault(
            key,
            Counter(
                req_count=0,
                resp_ack=0,
                resp_end=0,
                resp_weather=0,
                resp_nack=0,
                resp_ack_getall=0,
                resp_ack_getactual=0,
                resp_other=0,
            ),
        )
        stats["req_count"] += 1
        stats[self._telemetry_response_kind(response_text)] += 1
        if source == "cloud":
            self._telemetry_cloud_ok_in_window = True

    def _telemetry_record_timeout(self, *, conn_id: int) -> None:
        self._telemetry_cloud_failed_in_window = True
        self._telemetry_record_response("", source="timeout", conn_id=conn_id)

    def _telemetry_flush_stats(self) -> list[dict[str, Any]]:
        if not hasattr(self, "_telemetry_stats"):
            self._telemetry_stats = {}
        items: list[dict[str, Any]] = []
        for (table, source, mode), counts in self._telemetry_stats.items():
            items.append({
                "timestamp": self._utc_iso(),
                "table": table,
                "mode": mode,
                "response_source": source,
                "req_count": counts["req_count"],
                "resp_ack": counts["resp_ack"],
                "resp_end": counts["resp_end"],
                "resp_weather": counts["resp_weather"],
                "resp_nack": counts["resp_nack"],
                "resp_ack_getall": counts["resp_ack_getall"],
                "resp_ack_getactual": counts["resp_ack_getactual"],
                "resp_other": counts["resp_other"],
            })
        self._telemetry_stats.clear()
        return items

    def _record_error_context(self, *, event_type: str,
                              details: dict[str, Any]) -> None:
        try:
            details_json = json.dumps(details, ensure_ascii=False)
        except Exception:
            details_json = json.dumps(
                {"detail": str(details)}, ensure_ascii=False)
        self._telemetry_error_context.append({
            "timestamp": self._utc_iso(),
            "event_type": event_type,
            "details": details_json,
            "logs": json.dumps(self._snapshot_logs(), ensure_ascii=False),
        })

    def _record_tbl_event(
        self,
        *,
        parsed: dict[str, Any],
        device_id: str | None,
    ) -> None:
        event_time = self._parse_frame_dt(parsed.get("_dt"))
        if event_time is None:
            event_time = self._utc_iso()
        self._telemetry_tbl_events.append({
            "timestamp": event_time,
            "event_time": event_time,
            "type": parsed.get("Type"),
            "confirm": parsed.get("Confirm"),
            "content": parsed.get("Content"),
            "device_id": device_id,
        })

    @staticmethod
    def _parse_frame_dt(value: Any) -> str | None:
        if value is None:
            return None
        try:
            text = str(value).strip()
        except Exception:
            return None
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _record_box_session_end(
            self,
            *,
            reason: str,
            peer: str | None) -> None:
        if self._box_connected_since_epoch is None:
            return
        disconnected_at = time.time()
        self._telemetry_box_sessions.append({
            "timestamp": self._utc_iso(disconnected_at),
            "connected_at": self._utc_iso(self._box_connected_since_epoch),
            "disconnected_at": self._utc_iso(disconnected_at),
            "duration_s": int(disconnected_at - self._box_connected_since_epoch),
            "peer": peer,
            "reason": reason,
        })
        self._box_connected_since_epoch = None

    def _record_cloud_session_end(self, *, reason: str) -> None:
        if self._cloud_connected_since_epoch is None:
            return
        disconnected_at = time.time()
        duration = disconnected_at - self._cloud_connected_since_epoch
        if (
            reason == "eof"
            and duration < 1.0
            and not self._telemetry_cloud_ok_in_window
        ):
            self._telemetry_cloud_eof_short_in_window = True
        self._telemetry_cloud_sessions.append({
            "timestamp": self._utc_iso(disconnected_at),
            "connected_at": self._utc_iso(self._cloud_connected_since_epoch),
            "disconnected_at": self._utc_iso(disconnected_at),
            "duration_s": int(duration),
            "reason": reason,
        })
        self._cloud_connected_since_epoch = None

    def _record_hybrid_state_end(
            self,
            *,
            ended_at: float,
            reason: str | None = None) -> None:
        if self._hybrid_state_since_epoch is None or self._hybrid_state is None:
            return
        self._telemetry_hybrid_sessions.append({
            "timestamp": self._utc_iso(ended_at),
            "state": self._hybrid_state,
            "started_at": self._utc_iso(self._hybrid_state_since_epoch),
            "ended_at": self._utc_iso(ended_at),
            "duration_s": int(ended_at - self._hybrid_state_since_epoch),
            "reason": reason,
            "mode": self.mode.value,
        })
        self._hybrid_state_since_epoch = None
        self._hybrid_state = None

    def _record_offline_event(
            self,
            *,
            reason: str | None,
            local_ack: bool | None) -> None:
        self._telemetry_offline_events.append({
            "timestamp": self._utc_iso(),
            "reason": reason or "unknown",
            "local_ack": bool(local_ack),
            "mode": self.mode.value,
        })

    def _init_telemetry(self) -> None:
        """Initialize telemetry client (fail-safe)."""
        try:
            # Try to load version from config.json
            proxy_version = self._load_version_from_config()
            device_id = self.device_id if self.device_id != "AUTO" else ""
            self._telemetry_client = TelemetryClient(device_id, proxy_version)
            logger.info(
                "📊 Telemetry client initialized (version=%s, interval=%ss)",
                proxy_version,
                self._telemetry_interval_s)
        except Exception as e:
            logger.warning("Telemetry init failed (disabled): %s", e)
            self._telemetry_client = None

    def _load_version_from_config(self) -> str:
        """Load version from config.json or fallback to package metadata."""
        import os
        try:
            # First try config.json in addon directory
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(config_path):
                with open(config_path, encoding="utf-8") as f:
                    config_data = json.load(f)
                    version = config_data.get("version")
                    if version:
                        logger.debug("Loaded version %s from config.json", version)
                        return version
        except Exception as e:
            logger.debug("Failed to load version from config.json: %s", e)

        # Fallback to package metadata
        try:
            # pylint: disable=import-outside-toplevel
            from importlib.metadata import version as pkg_version
            version = pkg_version("oig-proxy")
            logger.debug("Loaded version %s from package metadata", version)
            return version
        except Exception as e:
            logger.debug("Failed to load version from package metadata: %s", e)

        # Final fallback
        logger.warning("Could not determine version, using default 1.6.2")
        return "1.6.2"

    async def _telemetry_loop(self) -> None:
        """Periodically send telemetry metrics to diagnostic server."""
        if not self._telemetry_client:
            return

        # Wait a bit before first telemetry (let proxy initialize)
        await asyncio.sleep(30)

        # Try initial provisioning
        try:
            # Update device_id if it was detected later
            if self._telemetry_client.device_id == "" and self.device_id != "AUTO":
                self._telemetry_client.device_id = self.device_id
            await self._telemetry_client.provision()
        except Exception as e:
            logger.debug("Initial telemetry provisioning failed: %s", e)

        logger.info(
            "📊 Telemetry loop started (every %ss)",
            self._telemetry_interval_s)

        # Send first telemetry immediately after startup
        try:
            if self._telemetry_client.device_id == "" and self.device_id != "AUTO":
                self._telemetry_client.device_id = self.device_id
            metrics = self._collect_telemetry_metrics()
            await self._telemetry_client.send_telemetry(metrics)
            logger.info("📊 First telemetry sent")
        except Exception as e:
            logger.debug("First telemetry send failed: %s", e)

        while True:
            await asyncio.sleep(self._telemetry_interval_s)
            try:
                # Update device_id if needed
                if self._telemetry_client.device_id == "" and self.device_id != "AUTO":
                    self._telemetry_client.device_id = self.device_id

                # Collect metrics
                metrics = self._collect_telemetry_metrics()

                # Send (fail-safe)
                await self._telemetry_client.send_telemetry(metrics)

            except Exception as e:
                logger.debug("Telemetry send failed: %s", e)

    def _collect_telemetry_metrics(self) -> dict[str, Any]:
        """Collect current proxy metrics for telemetry."""
        uptime_s = int(time.time() - self._start_time)
        # Get and clear SET commands buffer
        set_commands = self._set_commands_buffer[:]
        self._set_commands_buffer.clear()
        debug_active = getattr(self, "_telemetry_debug_windows_remaining", 0) > 0
        box_connected_window = self.box_connected or self._telemetry_box_seen_in_window
        self._telemetry_box_seen_in_window = False
        include_logs = (
            debug_active
            or not box_connected_window
        )
        logs = self._flush_log_buffer() if include_logs else []
        if not include_logs:
            self._telemetry_logs.clear()
        if debug_active:
            self._telemetry_debug_windows_remaining -= 1
        # cloud_online window logic:
        # - any successful cloud response -> green (success wins)
        # - otherwise: failures (timeout/connect error) or very short EOF (<1s) -> red
        # - otherwise: if session is currently connected -> green
        if self._telemetry_cloud_ok_in_window:
            cloud_online_window = True
        elif self._telemetry_cloud_failed_in_window or self._telemetry_cloud_eof_short_in_window:
            cloud_online_window = False
        elif self.cloud_session_connected:
            cloud_online_window = True
        else:
            cloud_online_window = False
        self._telemetry_cloud_ok_in_window = False
        self._telemetry_cloud_failed_in_window = False
        self._telemetry_cloud_eof_short_in_window = False
        hybrid_sessions = list(self._telemetry_hybrid_sessions)
        if self._configured_mode == "hybrid" and self._hybrid_state_since_epoch is not None:
            now = time.time()
            hybrid_sessions.append({
                "timestamp": self._utc_iso(now),
                "state": self._hybrid_state,
                "started_at": self._utc_iso(self._hybrid_state_since_epoch),
                "ended_at": None,
                "duration_s": int(now - self._hybrid_state_since_epoch),
                "reason": (
                    self._hybrid_last_offline_reason
                    if self._hybrid_state == "offline"
                    else None
                ),
                "mode": self.mode.value,
            })
        window_metrics = {
            "box_sessions": list(self._telemetry_box_sessions),
            "cloud_sessions": list(self._telemetry_cloud_sessions),
            "hybrid_sessions": hybrid_sessions,
            "offline_events": list(self._telemetry_offline_events),
            "tbl_events": list(self._telemetry_tbl_events),
            "error_context": list(self._telemetry_error_context),
            "stats": self._telemetry_flush_stats(),
            "logs": logs,
        }
        self._telemetry_box_sessions.clear()
        self._telemetry_cloud_sessions.clear()
        self._telemetry_hybrid_sessions.clear()
        self._telemetry_offline_events.clear()
        self._telemetry_tbl_events.clear()
        self._telemetry_error_context.clear()
        self._telemetry_force_logs_this_window = True
        metrics: dict[str, Any] = {
            "timestamp": self._utc_iso(),
            "interval_s": int(self._telemetry_interval_s),
            "uptime_s": uptime_s,
            "mode": self.mode.value,
            "configured_mode": self._configured_mode,
            "box_connected": box_connected_window,
            "box_peer": self._active_box_peer,
            "frames_received": self.stats.get("frames_received", 0),
            "frames_forwarded": self.stats.get("frames_forwarded", 0),
            "cloud_connects": self.cloud_connects,
            "cloud_disconnects": self.cloud_disconnects,
            "cloud_timeouts": self.cloud_timeouts,
            "cloud_errors": self.cloud_errors,
            "cloud_online": cloud_online_window,
            "mqtt_ok": self.mqtt_publisher.is_ready() if self.mqtt_publisher else False,
            "mqtt_queue": self.mqtt_publisher.queue.size() if self.mqtt_publisher else 0,
            "set_commands": set_commands,
            "window_metrics": window_metrics,
        }
        device_id = self.device_id if self.device_id != "AUTO" else ""
        if device_id:
            metrics.update({
                "isnewfw_fw": self._telemetry_cached_state_value(
                    device_id, "isnewfw", "fw"
                ),
                "isnewset_lat": self._telemetry_cached_state_value(
                    device_id, "isnewset", "lat"
                ),
                "tbl_box_tmlastcall": self._telemetry_cached_state_value(
                    device_id, "tbl_box", "tmlastcall"
                ),
                "isnewweather_loadedon": self._telemetry_cached_state_value(
                    device_id, "isnewweather", "loadedon"
                ),
                "tbl_box_strnght": self._telemetry_cached_state_value(
                    device_id, "tbl_box", "strnght"
                ),
                "tbl_invertor_prms_model": self._telemetry_cached_state_value(
                    device_id, "tbl_invertor_prms", "model"
                ),
            })
        return metrics

    def _telemetry_cached_state_value(
        self,
        device_id: str,
        table_name: str,
        field_name: str,
    ) -> Any | None:
        if not self.mqtt_publisher:
            return None
        table_candidates = [table_name]
        alias = ISNEW_STATE_TOPIC_ALIASES.get(table_name)
        if alias:
            table_candidates.append(alias)
        payload = None
        for candidate in table_candidates:
            topic = self.mqtt_publisher.state_topic(device_id, candidate)
            payload = self.mqtt_publisher.get_cached_payload(topic)
            if payload:
                break
        if not payload:
            return None
        try:
            data = json.loads(payload)
        except Exception:
            return payload
        if not isinstance(data, dict):
            return data
        if field_name in data:
            return data[field_name]
        field_key = field_name.lower()
        for key, value in data.items():
            if str(key).lower() == field_key:
                return value
        return None

    def _telemetry_fire_event(self, event_name: str, **kwargs: Any) -> None:
        """Fire telemetry event (non-blocking, fire and forget)."""
        if not self._telemetry_client:
            return
        if event_name.startswith(("error_", "warning_")):
            self._record_error_context(event_type=event_name, details=kwargs)
        method = getattr(self._telemetry_client, f"event_{event_name}", None)
        if method:
            task = asyncio.create_task(method(**kwargs))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    def _looks_like_all_data_sent_end(frame: str) -> bool:
        return "<Result>END</Result>" in frame and "<Reason>All data sent</Reason>" in frame

    async def _register_box_connection(
        self, writer: asyncio.StreamWriter, addr: Any
    ) -> int:
        async with self._box_conn_lock:
            previous = self._active_box_writer
            if previous is not None and not previous.is_closing():
                self._last_box_disconnect_reason = "forced"
                self._record_box_session_end(
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

    async def _control_note_box_disconnect(self) -> None:
        """Mark inflight control command as interrupted by box disconnect."""
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            if tx.get("stage") in ("sent_to_box", "accepted"):
                tx["disconnected"] = True

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
        self._telemetry_box_seen_in_window = True
        self._telemetry_force_logs_this_window = False
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
            self._record_box_session_end(
                reason=self._last_box_disconnect_reason or "unknown",
                peer=self._active_box_peer or (f"{addr[0]}:{addr[1]}" if addr else None),
            )
            self._last_box_disconnect_reason = None
            self._telemetry_fire_event(
                "error_box_disconnect",
                box_peer=self._active_box_peer or str(addr))
            if self._control_mqtt_enabled:
                await self._control_note_box_disconnect()
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
        self._hybrid_record_failure(reason=reason, local_ack=local_ack)

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
        self._setup_mqtt_state_cache()

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
            self._record_cloud_session_end(reason=reason)
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
        if not self._should_try_cloud():
            await self._close_writer(cloud_writer)
            if self.cloud_session_connected:
                self._record_cloud_session_end(reason="manual_offline")
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
        """Forward frame to cloud.

        HYBRID mode: Immediate offline switch + local ACK if cloud fails.
        ONLINE mode: Transparent - no local ACK, BOX handles timeout itself.
        """
        # OFFLINE mode should not reach here, but just in case
        if self._force_offline_enabled():
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

        # Connection failed
        if cloud_writer is None or cloud_reader is None:
            self._telemetry_cloud_failed_in_window = True
            if cloud_attempted:
                self._telemetry_fire_event(
                    "error_cloud_connect",
                    cloud_host=TARGET_SERVER,
                    reason="connect_failed",
                )
            if self._is_hybrid_mode():
                self._hybrid_record_failure(
                    reason="connect_failed", local_ack=True)
                # HYBRID: immediate offline + local ACK (threshold=1)
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
            # ONLINE: no local ACK, BOX will timeout
            self._telemetry_record_timeout(conn_id=conn_id)
            return None, None

        try:
            cloud_writer.write(frame_bytes)
            await cloud_writer.drain()
            self.stats["frames_forwarded"] += 1
            # Frame successfully sent to cloud in this window
            self._telemetry_cloud_ok_in_window = True

            ack_data = await asyncio.wait_for(
                cloud_reader.read(4096),
                timeout=CLOUD_ACK_TIMEOUT,
            )
            if not ack_data:
                self._telemetry_cloud_failed_in_window = True
                logger.warning(
                    "⚠️ Cloud closed connection (conn=%s, table=%s)",
                    conn_id,
                    table_name,
                )
                self.cloud_disconnects += 1
                if self.cloud_session_connected:
                    self._record_cloud_session_end(reason="eof")
                self._telemetry_fire_event(
                    "error_cloud_disconnect", reason="eof")
                self._hybrid_record_failure(
                    reason="cloud_eof",
                    local_ack=self._is_hybrid_mode(),
                )
                await self._close_writer(cloud_writer)
                # HYBRID: fallback to local ACK
                if self._is_hybrid_mode():
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
                # ONLINE: no local ACK
                self._telemetry_record_timeout(conn_id=conn_id)
                return None, None

            # Success - forward ACK to BOX
            self._hybrid_record_success()
            ack_str = ack_data.decode("utf-8", errors="replace")
            self._telemetry_cloud_ok_in_window = True
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
            self._telemetry_record_response(
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
            return cloud_reader, cloud_writer

        except asyncio.TimeoutError:
            self._telemetry_cloud_failed_in_window = True
            self.cloud_timeouts += 1
            self._telemetry_fire_event(
                "error_cloud_timeout",
                cloud_host=TARGET_SERVER,
                timeout_s=CLOUD_ACK_TIMEOUT,
            )
            self._hybrid_record_failure(
                reason="ack_timeout",
                local_ack=self._is_hybrid_mode(),
            )

            logger.warning(
                "⏱️ Cloud ACK timeout (%.1fs) (conn=%s, table=%s)",
                CLOUD_ACK_TIMEOUT,
                conn_id,
                table_name,
            )

            # HYBRID: send local ACK (including END)
            if self._is_hybrid_mode():
                if table_name == "END":
                    logger.info(
                        "📤 HYBRID: Sending local END (conn=%s)",
                        conn_id,
                    )
                    end_frame = self._build_end_time_frame()
                    self._telemetry_record_response(
                        end_frame.decode("utf-8", errors="replace"),
                        source="local",
                        conn_id=conn_id,
                    )
                    box_writer.write(end_frame)
                    await box_writer.drain()
                    self.stats["acks_local"] += 1
                    return cloud_reader, cloud_writer
                # Non-END: fallback to local ACK
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

            # ONLINE: no local ACK, close connection
            if self.cloud_session_connected:
                self._record_cloud_session_end(reason="timeout")
            await self._close_writer(cloud_writer)
            self._telemetry_record_timeout(conn_id=conn_id)
            return None, None

        except Exception as e:
            self._telemetry_cloud_failed_in_window = True
            logger.warning(
                "⚠️ Cloud error: %s (conn=%s, table=%s)",
                e,
                conn_id,
                table_name,
            )
            self.cloud_errors += 1
            if self.cloud_session_connected:
                self._record_cloud_session_end(reason="cloud_error")
            self._hybrid_record_failure(
                reason="cloud_error",
                local_ack=self._is_hybrid_mode(),
            )
            await self._close_writer(cloud_writer)

            # HYBRID: fallback to local ACK
            if self._is_hybrid_mode():
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
            # ONLINE: no local ACK
            self._telemetry_record_timeout(conn_id=conn_id)
            return None, None

    async def _process_box_frame_common(
        self, *, frame_bytes: bytes, frame: str, conn_id: int
    ) -> tuple[str | None, str | None]:
        self.stats["frames_received"] += 1
        self._telemetry_box_seen_in_window = True
        self._telemetry_force_logs_this_window = False
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
        self._telemetry_record_request(table_name, conn_id)

        if parsed:
            if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
                self._isnew_polls += 1
                self._isnew_last_poll_epoch = time.time()
                self._isnew_last_poll_iso = self._last_data_iso
            if table_name == "tbl_events":
                self._record_tbl_event(parsed=parsed, device_id=device_id)
            self._cache_last_values(parsed, table_name)
            await self._handle_setting_event(parsed, table_name, device_id)
            await self._control_observe_box_frame(parsed, table_name, frame)
            await self._maybe_process_mode(parsed, table_name, device_id)
            await self._control_maybe_start_next()
            await self.mqtt_publisher.publish_data(parsed)

        return device_id, table_name

    @staticmethod
    def _infer_table_name(frame: str) -> str | None:
        tbl = re.search(r"<TblName>([^<]+)</TblName>", frame)
        if tbl:
            return tbl.group(1)
        res = re.search(r"<Result>([^<]+)</Result>", frame)
        if res:
            return res.group(1)
        return None

    @staticmethod
    def _infer_device_id(frame: str) -> str | None:
        m = re.search(r"<ID_Device>(\d+)</ID_Device>", frame)
        return m.group(1) if m else None

    async def _get_current_mode(self) -> ProxyMode:
        if self._force_offline_enabled():
            return ProxyMode.OFFLINE
        async with self.mode_lock:
            return self.mode

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
            self._record_cloud_session_end(reason="manual_offline")
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
                self._telemetry_force_logs_this_window = False
                current_mode = await self._get_current_mode()

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

                if current_mode == ProxyMode.HYBRID and not self._should_try_cloud():
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
                self._record_cloud_session_end(reason="box_disconnect")
            self.cloud_session_connected = False

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
                self._telemetry_record_response(
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
            "mode": self.mode.value,
            "configured_mode": self._configured_mode,
            "cloud_online": not self._hybrid_in_offline,
            "hybrid_fail_count": self._hybrid_fail_count,
            "mqtt_queue_size": self.mqtt_publisher.queue.size(),
            "mqtt_connected": self.mqtt_publisher.connected,
            **self.stats
        }

    # ---------------------------------------------------------------------
    # Control over MQTT (production)
    # ---------------------------------------------------------------------

    def _setup_mqtt_state_cache(self) -> None:
        if self._loop is None:
            return
        device_id = self.mqtt_publisher.device_id or self.device_id
        if not device_id or device_id == "AUTO":
            return
        if self._mqtt_cache_device_id == device_id:
            return
        topic = f"{MQTT_NAMESPACE}/{device_id}/+/state"

        def _handler(
                msg_topic: str,
                payload: bytes,
                _qos: int,
                retain: bool) -> None:
            if self._loop is None:
                return
            try:
                payload_text = payload.decode("utf-8", errors="strict")
            except Exception:
                payload_text = payload.decode("utf-8", errors="replace")
            self._loop.call_soon_threadsafe(
                asyncio.create_task,
                self._handle_mqtt_state_message(
                    topic=msg_topic,
                    payload_text=payload_text,
                    retain=retain,
                ),
            )

        self.mqtt_publisher.add_message_handler(
            topic=topic,
            handler=_handler,
            qos=1,
        )
        self._mqtt_cache_device_id = device_id
        logger.info("MQTT: Cache subscription enabled (%s)", topic)

    def _setup_control_mqtt(self) -> None:
        if self._loop is None:
            return

        def _handler(
                topic: str,
                payload: bytes,
                _qos: int,
                retain: bool) -> None:
            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(self._control_on_mqtt_message(
                topic=topic, payload=payload, retain=retain), self._loop, )

        self.mqtt_publisher.add_message_handler(
            topic=self._control_set_topic,
            handler=_handler,
            qos=self._control_qos,
        )
        logger.info(
            "CONTROL: MQTT enabled (set=%s result=%s)",
            self._control_set_topic,
            self._control_result_topic,
        )

    def _parse_mqtt_state_topic(
            self, topic: str) -> tuple[str | None, str | None]:
        parts = topic.split("/")
        if len(parts) != 4:
            return None, None
        namespace, device_id, table_name, suffix = parts
        if namespace != MQTT_NAMESPACE or suffix != "state":
            return None, None
        return device_id, table_name

    def _mqtt_state_to_raw_value(
        self, *, tbl_name: str, tbl_item: str, value: Any
    ) -> Any:
        if isinstance(value, (dict, list)):
            return value
        cfg, _ = get_sensor_config(tbl_item, tbl_name)
        if cfg and cfg.options:
            if isinstance(value, str):
                text = value.strip()
                for idx, opt in enumerate(cfg.options):
                    if text == opt or text.lower() == opt.lower():
                        return idx
                try:
                    return int(float(text))
                except Exception:
                    return text
            if isinstance(value, (int, float)):
                idx = int(value)
                if 0 <= idx < len(cfg.options):
                    return idx
        return self._control_coerce_value(value)

    async def _handle_mqtt_state_message(
        self,
        *,
        topic: str,
        payload_text: str,
        retain: bool,
    ) -> None:
        _ = retain
        device_id, table_name = self._parse_mqtt_state_topic(topic)
        if not device_id or not table_name:
            return
        target_device_id = self.mqtt_publisher.device_id or self.device_id
        if not target_device_id or target_device_id == "AUTO":
            return
        if device_id != target_device_id:
            return
        self.mqtt_publisher.set_cached_payload(topic, payload_text)
        if not table_name.startswith("tbl_"):
            return
        try:
            payload = json.loads(payload_text)
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        raw_values: dict[str, Any] = {}
        for key, value in payload.items():  # noqa: C901
            if key.startswith("_"):
                continue
            raw_value = self._mqtt_state_to_raw_value(
                tbl_name=table_name,
                tbl_item=key,
                value=value,
            )
            raw_values[key] = raw_value
            self._update_cached_value(
                tbl_name=table_name,
                tbl_item=key,
                raw_value=raw_value,
                update_mode=True,
            )

        if raw_values and self._should_persist_table(table_name):
            try:
                save_prms_state(table_name, raw_values, device_id)
            except Exception as e:
                logger.debug(
                    "STATE: snapshot update failed (%s): %s",
                    table_name,
                    e)
            existing = self._prms_tables.get(table_name, {})
            merged: dict[str, Any] = {}
            if isinstance(existing, dict):
                merged.update(existing)
            merged.update(raw_values)
            self._prms_tables[table_name] = merged
            self._prms_device_id = device_id

    async def _control_publish_result(
        self,
        *,
        tx: dict[str, Any],
        status: str,
        error: str | None = None,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tx_id": tx.get("tx_id"),
            "request_key": tx.get("request_key"),
            "device_id": None if self.device_id == "AUTO" else self.device_id,
            "tbl_name": tx.get("tbl_name"),
            "tbl_item": tx.get("tbl_item"),
            "new_value": tx.get("new_value"),
            "status": status,
            "error": error,
            "detail": detail,
            "ts": datetime.now(timezone.utc).isoformat().replace(OIGProxy._TIME_OFFSET, "Z"),
        }
        if extra:
            payload.update(extra)
        self._control_last_result = payload
        await self.mqtt_publisher.publish_raw(
            topic=self._control_result_topic,
            payload=json.dumps(payload, ensure_ascii=True),
            qos=self._control_qos,
            retain=self._control_retain,
        )
        if self._control_log_enabled:
            try:
                log_entry = json.dumps(payload, ensure_ascii=True) + "\n"
                await asyncio.to_thread(
                    lambda: self._append_to_control_log(log_entry)
                )
            except Exception as e:
                logger.debug("CONTROL: Log write failed: %s", e)
        try:
            await self.publish_proxy_status()
        except Exception as e:
            logger.debug("CONTROL: Status publish failed: %s", e)

        key_state = self._control_result_key_state(
            status=status, detail=detail)
        if key_state:
            try:
                await self._control_publish_key_status(tx=tx, state=key_state, detail=detail)
            except Exception as e:
                logger.debug("CONTROL: Key status publish failed: %s", e)

        # SA only after real applied/completed change (avoid noop/duplicate)
        if status in ("applied", "completed") and not error:
            if detail not in ("noop_already_set", "duplicate_ignored"):
                if (tx.get("tbl_name"), tx.get("tbl_item")) != (
                        "tbl_box_prms", "SA"):
                    self._control_post_drain_refresh_pending = True

    def _control_drop_post_drain_sa_locked(self) -> list[dict[str, Any]]:
        """Drop queued post-drain SA refresh so new commands can proceed."""
        removed: list[dict[str, Any]] = []
        if not self._control_queue:
            return removed
        kept: deque[dict[str, Any]] = deque()
        for tx in self._control_queue:
            if tx.get("tx_key") != POST_DRAIN_SA_KEY:
                kept.append(tx)
            else:
                removed.append(tx)
        self._control_queue = kept
        return removed

    def _append_to_control_log(self, log_entry: str) -> None:
        """Append entry to control log file (synchronous, called via to_thread)."""
        with open(self._control_log_path, "a", encoding="utf-8") as fh:
            fh.write(log_entry)
        kept: deque[dict[str, Any]] = deque()
        for queued in self._control_queue:
            if ((queued.get("tbl_name"), queued.get("tbl_item")) == (
                    "tbl_box_prms", "SA") and queued.get("_internal") == "post_drain_sa"):
                removed.append(queued)
                continue
            kept.append(queued)
        if removed:
            self._control_queue = kept
        return removed

    def _control_cancel_post_drain_sa_inflight_locked(
            self) -> dict[str, Any] | None:
        """Cancel inflight post-drain SA refresh so new commands can proceed."""
        tx = self._control_inflight
        if (not tx or (tx.get("tbl_name"), tx.get("tbl_item")) != (
                "tbl_box_prms", "SA") or tx.get("_internal") != "post_drain_sa"):
            return None
        self._control_inflight = None
        for task in (
                self._control_ack_task,
                self._control_applied_task,
                self._control_quiet_task):
            if task and not task.done():
                task.cancel()
        self._control_ack_task = None
        self._control_applied_task = None
        self._control_quiet_task = None
        return tx

    @staticmethod
    def _control_build_request_key(
        *, tbl_name: str, tbl_item: str, canon_value: str
    ) -> str:
        return f"{tbl_name}/{tbl_item}/{canon_value}"

    def _control_status_topic(self, request_key: str) -> str:
        return f"{self._control_status_prefix}/{request_key}"

    @staticmethod
    def _control_result_key_state(
            status: str,
            detail: str | None) -> str | None:
        if status == "completed" and detail in (
                "duplicate_ignored", "noop_already_set"):
            return None
        mapping = {
            "accepted": "queued",
            "deferred": "queued",
            "sent_to_box": "sent",
            "box_ack": "acked",
            "applied": "applied",
            "completed": "done",
            "error": "error",
        }
        return mapping.get(status)

    async def _control_publish_key_status(
        self, *, tx: dict[str, Any], state: str, detail: str | None = None
    ) -> None:
        request_key = str(tx.get("request_key") or "").strip()
        if not request_key:
            return
        payload: dict[str, Any] = {
            "request_key": request_key,
            "state": state,
            "tx_id": tx.get("tx_id"),
            "tbl_name": tx.get("tbl_name"),
            "tbl_item": tx.get("tbl_item"),
            "new_value": tx.get("new_value"),
            "detail": detail,
            "ts": datetime.now(timezone.utc).isoformat().replace(OIGProxy._TIME_OFFSET, "Z"),
        }
        self._control_key_state[request_key] = payload
        self._control_update_pending_keys(request_key=request_key, state=state)
        await self.mqtt_publisher.publish_raw(
            topic=self._control_status_topic(request_key),
            payload=json.dumps(payload, ensure_ascii=True),
            qos=self._control_qos,
            retain=self._control_status_retain,
        )

    def _control_load_pending_keys(self) -> set[str]:
        try:
            with open(self._control_pending_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return set()
        except Exception as e:
            logger.debug("CONTROL: Pending load failed: %s", e)
            return set()
        if isinstance(data, list):
            return {str(item) for item in data if item}
        return set()

    def _control_store_pending_keys(self) -> None:
        try:
            with open(self._control_pending_path, "w", encoding="utf-8") as fh:
                json.dump(
                    sorted(
                        self._control_pending_keys),
                    fh,
                    ensure_ascii=True)
        except Exception as e:
            logger.debug("CONTROL: Pending save failed: %s", e)

    def _control_update_pending_keys(
            self, *, request_key: str, state: str) -> None:
        if state in ("queued", "sent", "acked", "applied"):
            if request_key not in self._control_pending_keys:
                self._control_pending_keys.add(request_key)
                self._control_store_pending_keys()
            return
        if state in ("done", "error"):
            if request_key in self._control_pending_keys:
                self._control_pending_keys.discard(request_key)
                self._control_store_pending_keys()

    async def _control_publish_restart_errors(self) -> None:
        if not self._control_pending_keys:
            return
        for request_key in sorted(self._control_pending_keys):
            tbl_name = ""
            tbl_item = ""
            new_value = ""
            parts = request_key.split("/", 2)
            if len(parts) == 3:
                tbl_name, tbl_item, new_value = parts
            tx = {
                "tx_id": None,
                "request_key": request_key,
                "tbl_name": tbl_name,
                "tbl_item": tbl_item,
                "new_value": new_value,
            }
            await self._control_publish_result(
                tx=tx, status="error", error="proxy_restart", detail="proxy_restart"
            )
        self._control_pending_keys.clear()
        self._control_store_pending_keys()

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
        await self._publish_setting_event_state(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=new_value,
            device_id=device_id,
            source="tbl_events",
        )

    def _cache_last_values(
            self, _parsed: dict[str, Any], _table_name: str | None) -> None:
        return

    def _update_cached_value(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        raw_value: Any,
        update_mode: bool,
    ) -> None:
        if not tbl_name or not tbl_item:
            return
        self._last_values[(tbl_name, tbl_item)] = raw_value
        table_cache = self._table_cache.setdefault(tbl_name, {})
        table_cache[tbl_item] = raw_value

        if not update_mode:
            return

        if (tbl_name, tbl_item) != ("tbl_box_prms", "MODE"):
            return
        try:
            mode_int = int(raw_value)
        except Exception:
            return
        if mode_int < 0 or mode_int > 5:
            return
        if mode_int == self._mode_value:
            return

        self._mode_value = mode_int
        resolved_device_id = (
            (self.device_id if self.device_id != "AUTO" else None)
            or self._mode_device_id
            or self._prms_device_id
        )
        if resolved_device_id:
            self._mode_device_id = resolved_device_id
        save_mode_state(mode_int, resolved_device_id)

    def _control_is_box_ready(self) -> tuple[bool, str | None]:
        if not self.box_connected:
            return False, "box_not_connected"
        if self.device_id == "AUTO":
            return False, "device_id_unknown"
        if self._box_connected_since_epoch is None:
            return False, "box_not_ready"
        if (time.time() - self._box_connected_since_epoch) < self._control_box_ready_s:
            return False, "box_not_ready"
        if self._last_data_epoch is None:
            return False, "box_not_sending_data"
        if (time.time() - self._last_data_epoch) > 30:
            return False, "box_not_sending_data"
        return True, None

    async def _control_defer_inflight(self, *, reason: str) -> None:
        """Requeue inflight command for retry; stop after max attempts."""
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            attempts = int(tx.get("_attempts") or 0)
            if attempts >= self._control_max_attempts:
                self._control_inflight = None
            else:
                tx["stage"] = "deferred"
                tx["deferred_reason"] = reason
                tx["next_attempt_at"] = time.monotonic() + \
                    self._control_retry_delay_s
                self._control_queue.appendleft(tx)
                self._control_inflight = None
            for task in (
                self._control_ack_task,
                self._control_applied_task,
                self._control_quiet_task,
            ):
                if task and not task.done():
                    task.cancel()
            self._control_ack_task = None
            self._control_applied_task = None
            self._control_quiet_task = None

        if attempts >= self._control_max_attempts:
            await self._control_publish_result(
                tx=tx,
                status="error",
                error="max_attempts_exceeded",
                detail=reason,
                extra={"attempts": attempts, "max_attempts": self._control_max_attempts},
            )
            await self._control_maybe_start_next()
            await self._control_maybe_queue_post_drain_refresh(last_tx=tx)
            return

        await self._control_publish_result(
            tx=tx,
            status="deferred",
            detail=reason,
            extra={
                "attempts": attempts,
                "max_attempts": self._control_max_attempts,
                "retry_in_s": self._control_retry_delay_s,
            },
        )
        await self._control_maybe_start_next()

    def _control_normalize_value(
        self, *, tbl_name: str, tbl_item: str, new_value: Any
    ) -> tuple[str, str] | tuple[None, str]:
        raw = new_value
        if isinstance(raw, (int, float)):
            raw_str = str(raw)
        else:
            raw_str = str(raw).strip()

        key = (tbl_name, tbl_item)
        if key == ("tbl_box_prms", "MODE"):
            try:
                mode_int = int(float(raw_str))
            except Exception:
                return None, "bad_value"
            if mode_int < 0 or mode_int > 5:
                return None, "bad_value"
            v = str(mode_int)
            return v, v

        if key in (("tbl_invertor_prm1", "AAC_MAX_CHRG"),
                   ("tbl_invertor_prm1", "A_MAX_CHRG")):
            try:
                f = float(raw_str)
            except Exception:
                return None, "bad_value"
            v = f"{f:.1f}"
            return v, v

        return raw_str, raw_str

    async def _control_on_mqtt_message(
        self, *, topic: str, payload: bytes, retain: bool
    ) -> None:
        _ = topic
        _ = retain
        try:
            data = json.loads(payload.decode("utf-8", errors="strict"))
        except Exception:
            await self.mqtt_publisher.publish_raw(
                topic=self._control_result_topic,
                payload=json.dumps(
                    {
                        "tx_id": None,
                        "status": "error",
                        "error": "bad_json",
                        "ts": datetime.now(timezone.utc).isoformat().replace(OIGProxy._TIME_OFFSET, "Z"),
                    }
                ),
                qos=self._control_qos,
                retain=self._control_retain,
            )
            return

        tx_id = str(data.get("tx_id") or "").strip()
        tbl_name = str(data.get("tbl_name") or "").strip()
        tbl_item = str(data.get("tbl_item") or "").strip()
        if not tx_id or not tbl_name or not tbl_item or "new_value" not in data:
            tx_payload = {
                "tx_id": tx_id or None,
                "tbl_name": tbl_name,
                "tbl_item": tbl_item,
                "new_value": data.get("new_value"),
            }
            await self._control_publish_result(
                tx=tx_payload,
                status="error",
                error="missing_fields",
            )
            return

        tx: dict[str, Any] = {
            "tx_id": tx_id,
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": data.get("new_value"),
            "confirm": str(data.get("confirm") or "New"),
            "received_at": datetime.now(timezone.utc).isoformat().replace(OIGProxy._TIME_OFFSET, "Z"),
            "_attempts": 0,
        }

        allowed = tbl_item in self._control_whitelist.get(tbl_name, set())
        if not allowed:
            await self._control_publish_result(tx=tx, status="error", error="not_allowed")
            return

        send_value, err = self._control_normalize_value(
            tbl_name=tbl_name, tbl_item=tbl_item, new_value=tx["new_value"]
        )
        if send_value is None:
            await self._control_publish_result(tx=tx, status="error", error=err)
            return
        tx["new_value"] = send_value
        tx["_canon"] = send_value
        request_key_raw = str(data.get("request_key") or "").strip()
        request_key = self._control_build_request_key(
            tbl_name=tbl_name, tbl_item=tbl_item, canon_value=send_value
        )
        if request_key_raw and request_key_raw != request_key:
            tx["request_key_raw"] = request_key_raw
        tx["request_key"] = request_key

        active_state = None
        dropped_sa: list[dict[str, Any]] = []
        canceled_sa: dict[str, Any] | None = None
        async with self._control_lock:
            active_state = (
                self._control_key_state.get(request_key, {}).get("state")
                if request_key
                else None
            )
            current = self._last_values.get((tbl_name, tbl_item))
            if current is not None:
                current_norm, _ = self._control_normalize_value(
                    tbl_name=tbl_name, tbl_item=tbl_item, new_value=current
                )
                if (
                    current_norm is not None
                    and str(current_norm) == str(send_value)
                    and self._control_inflight is None
                    and not self._control_queue
                ):
                    await self._control_publish_result(
                        tx=tx, status="completed", detail="noop_already_set"
                    )
                    return

            if active_state in ("queued", "sent", "acked", "applied"):
                await self._control_publish_result(
                    tx=tx, status="completed", detail="duplicate_ignored"
                )
                return

            canceled_sa = self._control_cancel_post_drain_sa_inflight_locked()
            dropped_sa = self._control_drop_post_drain_sa_locked()
            self._control_queue.append(tx)

        if canceled_sa:
            logger.info(
                "CONTROL: Canceling inflight post-drain SA to allow new command (%s/%s)",
                tx.get("tbl_name"),
                tx.get("tbl_item"),
            )
            await self._control_publish_result(
                tx=canceled_sa,
                status="completed",
                detail="canceled_by_new_command",
            )

        for sa_tx in dropped_sa:
            logger.info(
                "CONTROL: Dropping post-drain SA to allow new command (%s/%s)",
                tx.get("tbl_name"),
                tx.get("tbl_item"),
            )
            await self._control_publish_result(
                tx=sa_tx,
                status="completed",
                detail="canceled_by_new_command",
            )

        await self._control_publish_result(tx=tx, status="accepted")
        await self._control_maybe_start_next()

    async def _control_maybe_start_next(self) -> None:
        ok, _ = self._control_is_box_ready()
        if not ok:
            return

        schedule_delay: float | None = None
        async with self._control_lock:
            if self._control_inflight is not None:
                return
            if not self._control_queue:
                return
            tx_dict: dict[str, Any] = self._control_queue[0]
            next_at = float(tx_dict.get("next_attempt_at") or 0.0)
            now = time.monotonic()
            tx: dict[str, Any] | None
            if next_at and now < next_at:
                schedule_delay = next_at - now
                tx = None
            else:
                tx = self._control_queue.popleft()
            self._control_inflight = tx
            if tx is not None:
                tx["stage"] = "accepted"

        if schedule_delay is not None:
            await self._control_schedule_retry(schedule_delay)
            return
        if tx is None:
            return
        await self._control_start_inflight()

    async def _control_schedule_retry(self, delay_s: float) -> None:
        if delay_s <= 0:
            await self._control_maybe_start_next()
            return
        if self._control_retry_task and not self._control_retry_task.done():
            return

        async def _wait_and_retry() -> None:
            await asyncio.sleep(delay_s)
            await self._control_maybe_start_next()

        self._control_retry_task = asyncio.create_task(_wait_and_retry())

    async def _control_start_inflight(self) -> None:
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            attempts = int(tx.get("_attempts") or 0)
            too_many = attempts >= self._control_max_attempts

        if too_many:
            await self._control_publish_result(
                tx=tx,
                status="error",
                error="max_attempts_exceeded",
                extra={"attempts": attempts, "max_attempts": self._control_max_attempts},
            )
            await self._control_finish_inflight()
            return

        result = await self._send_setting_to_box(
            tbl_name=str(tx["tbl_name"]),
            tbl_item=str(tx["tbl_item"]),
            new_value=str(tx["new_value"]),
            confirm=str(tx.get("confirm") or "New"),
            tx_id=str(tx["tx_id"]),
        )
        if not result.get("ok"):
            err = str(result.get("error") or "send_failed")
            if err in (
                "box_not_connected",
                "box_not_sending_data",
                    "no_active_box_writer"):
                await self._control_defer_inflight(reason=err)
                return
            await self._control_publish_result(tx=tx, status="error", error=err)
            await self._control_finish_inflight()
            return

        tx["attempts"] = attempts + 1
        tx["_attempts"] = attempts + 1
        tx["stage"] = "sent_to_box"
        tx["id"] = result.get("id")
        tx["id_set"] = result.get("id_set")
        tx["sent_at_mono"] = time.monotonic()
        tx["disconnected"] = False

        await self._control_publish_result(
            tx=tx,
            status="sent_to_box",
            extra={
                "id": tx.get("id"),
                "id_set": tx.get("id_set"),
                "attempts": tx.get("_attempts"),
                "max_attempts": self._control_max_attempts,
            },
        )

        if self._control_ack_task and not self._control_ack_task.done():
            self._control_ack_task.cancel()
        self._control_ack_task = asyncio.create_task(
            self._control_ack_timeout())

    async def _control_on_box_setting_ack(
            self, *, tx_id: str | None, ack: bool) -> None:
        if not tx_id:
            return
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None or str(tx.get("tx_id")) != str(tx_id):
                return
            tx["stage"] = "box_ack" if ack else "error"

        if self._control_ack_task and not self._control_ack_task.done():
            self._control_ack_task.cancel()
        self._control_ack_task = None

        if not ack:
            await self._control_publish_result(tx=tx, status="error", error="box_nack")
            await self._control_finish_inflight()
            return

        await self._control_publish_result(tx=tx, status="box_ack")

        if self._control_applied_task and not self._control_applied_task.done():
            self._control_applied_task.cancel()
        self._control_applied_task = asyncio.create_task(
            self._control_applied_timeout())

    @staticmethod
    def _control_coerce_value(value: Any) -> Any:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        text = str(value).strip()
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        if re.fullmatch(r"-?\d+", text):
            try:
                return int(text)
            except Exception:
                return value
        if re.fullmatch(r"-?\d+\.\d+", text):
            try:
                return float(text)
            except Exception:
                return value
        return value

    def _control_map_optimistic_value(
            self,
            *,
            tbl_name: str,
            tbl_item: str,
            value: Any) -> Any:
        cfg, _ = get_sensor_config(tbl_item, tbl_name)
        if cfg and cfg.options:
            text = str(value).strip()
            if re.fullmatch(r"-?\d+", text):
                idx = int(text)
                if 0 <= idx < len(cfg.options):
                    return cfg.options[idx]
        return self._control_coerce_value(value)

    def _control_update_persisted_snapshot(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        raw_value: Any,
    ) -> None:
        """Upraví perzistentní snapshot (prms_state) bez zásahu do cache."""
        if not tbl_name or not tbl_item:
            return
        if not self._should_persist_table(tbl_name):
            return

        resolved_device_id = (
            (self.device_id if self.device_id != "AUTO" else None)
            or self._prms_device_id
        )

        try:
            save_prms_state(
                tbl_name, {
                    tbl_item: raw_value}, resolved_device_id)
        except Exception as e:
            logger.debug(
                "STATE: snapshot update failed (%s/%s): %s",
                tbl_name,
                tbl_item,
                e,
            )

        existing = self._prms_tables.get(tbl_name, {})
        merged: dict[str, Any] = {}
        if isinstance(existing, dict):
            merged.update(existing)
        merged[tbl_item] = raw_value
        self._prms_tables[tbl_name] = merged
        if resolved_device_id:
            self._prms_device_id = resolved_device_id

    async def _publish_setting_event_state(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: Any,
        device_id: str | None,
        source: str,
    ) -> None:
        if not tbl_name or not tbl_item:
            return

        raw_value = self._control_coerce_value(new_value)
        target_device_id = device_id
        if not target_device_id:
            target_device_id = (
                self.device_id if self.device_id != "AUTO" else self.mqtt_publisher.device_id)
        if not target_device_id:
            return
        topic = self.mqtt_publisher.state_topic(target_device_id, tbl_name)
        cached = self.mqtt_publisher.get_cached_payload(topic)
        payload: dict[str, Any] = {}
        try:
            if cached:
                payload = json.loads(cached)
        except Exception:
            payload = {}

        if not payload:
            table_values = self._table_cache.get(tbl_name)
            if not table_values:
                table_values = self._prms_tables.get(tbl_name)
            if isinstance(table_values, dict) and table_values:
                raw_payload = dict(table_values)
                raw_payload[tbl_item] = raw_value
                payload, _ = self.mqtt_publisher.map_data_for_publish(
                    {"_table": tbl_name, **raw_payload},
                    table=tbl_name,
                    target_device_id=target_device_id,
                )

        payload[tbl_item] = self._control_map_optimistic_value(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            value=new_value,
        )

        updated = json.dumps(payload, ensure_ascii=True)
        self.mqtt_publisher.set_cached_payload(topic, updated)
        await self.mqtt_publisher.publish_raw(
            topic=topic,
            payload=updated,
            qos=MQTT_PUBLISH_QOS,
            retain=MQTT_STATE_RETAIN,
        )
        logger.info(
            "SETTING: State publish %s/%s=%s (source=%s)",
            tbl_name,
            tbl_item,
            payload.get(tbl_item),
            source,
        )

    async def _control_ack_timeout(self) -> None:
        await asyncio.sleep(self._control_ack_timeout_s)
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            if tx.get("stage") not in ("sent_to_box", "accepted"):
                return
        if not self.box_connected or tx.get("disconnected"):
            await self._control_defer_inflight(reason="box_not_connected")
            return
        await self._control_defer_inflight(reason="timeout_waiting_ack")

    async def _control_applied_timeout(self) -> None:
        await asyncio.sleep(self._control_applied_timeout_s)
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            if tx.get("stage") in ("applied", "completed", "error"):
                return
        await self._control_publish_result(
            tx=tx, status="error", error="timeout_waiting_applied"
        )
        await self._control_finish_inflight()

    async def _control_quiet_wait(self) -> None:
        while True:
            async with self._control_lock:
                tx = self._control_inflight
                if tx is None:
                    return
                if tx.get("stage") not in ("applied",):
                    return
                last = float(tx.get("last_inv_ack_mono")
                             or tx.get("applied_at_mono") or 0.0)
                wait_s = max(
                    0.0, (last + self._control_mode_quiet_s) - time.monotonic())
            if wait_s > 0:
                await asyncio.sleep(wait_s)
                continue
            break

        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            if tx.get("stage") != "applied":
                return
        await self._control_publish_result(tx=tx, status="completed", detail="quiet_window")
        await self._control_finish_inflight()

    async def _control_finish_inflight(self) -> None:
        async with self._control_lock:
            tx = self._control_inflight
            self._control_inflight = None
            for task in (
                self._control_ack_task,
                self._control_applied_task,
                self._control_quiet_task,
            ):
                if task and not task.done():
                    task.cancel()
            self._control_ack_task = None
            self._control_applied_task = None
            self._control_quiet_task = None
        await self._control_maybe_start_next()
        await self._control_maybe_queue_post_drain_refresh(last_tx=tx)

    async def _control_maybe_queue_post_drain_refresh(
        self,
        *,
        last_tx: dict[str, Any] | None,
    ) -> None:
        if not last_tx:
            return
        if (last_tx.get("tbl_name"), last_tx.get(
                "tbl_item")) == ("tbl_box_prms", "SA"):
            return

        async with self._control_lock:
            if self._control_inflight is not None or self._control_queue:
                return
            if not self._control_post_drain_refresh_pending:
                return
            self._control_post_drain_refresh_pending = False

        await self._control_enqueue_internal_sa(reason="queue_drained")

    async def _control_enqueue_internal_sa(self, *, reason: str) -> None:
        request_key = self._control_build_request_key(
            tbl_name="tbl_box_prms",
            tbl_item="SA",
            canon_value="1",
        )
        tx = {
            "tx_id": f"internal_sa_{int(time.time() * 1000)}",
            "tbl_name": "tbl_box_prms",
            "tbl_item": "SA",
            "new_value": "1",
            "confirm": "New",
            "received_at": datetime.now(timezone.utc).isoformat().replace(OIGProxy._TIME_OFFSET, "Z"),
            "_attempts": 0,
            "_canon": "1",
            "request_key": request_key,
            "_internal": "post_drain_sa",
        }

        async with self._control_lock:
            if self._control_inflight and self._control_inflight.get(
                    "request_key") == request_key:
                return
            for queued in self._control_queue:
                if queued.get("request_key") == request_key:
                    return
            self._control_queue.append(tx)

        await self._control_publish_result(tx=tx, status="accepted", detail=f"internal_sa:{reason}")
        await self._control_maybe_start_next()

    async def _control_observe_box_frame(
        self, parsed: dict[str, Any], table_name: str | None, _frame: str
    ) -> None:
        async with self._control_lock:
            tx = self._control_inflight
        if tx is None or not parsed or not table_name:
            return

        if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW", "END"):
            async with self._control_lock:
                tx2 = self._control_inflight
                if tx2 is None or tx2.get("tx_id") != tx.get("tx_id"):
                    return
                stage = tx2.get("stage")
            if stage in ("box_ack", "applied"):
                await self._control_publish_result(
                    tx=tx, status="completed", detail=f"box_marker:{table_name}"
                )
                await self._control_finish_inflight()
            return

        if table_name != "tbl_events":
            return

        content = parsed.get("Content")
        typ = parsed.get("Type")
        if not content or not isinstance(content, str):
            return

        if typ == "Setting":
            ev = self._parse_setting_event(content)
            if not ev:
                return
            ev_tbl, ev_item, old_v, new_v = ev
            if ev_tbl != tx.get("tbl_name") or ev_item != tx.get("tbl_item"):
                return
            desired = str(tx.get("new_value"))
            if str(new_v) != desired:
                return
            async with self._control_lock:
                tx2 = self._control_inflight
                if tx2 is None or tx2.get("tx_id") != tx.get("tx_id"):
                    return
                tx2["stage"] = "applied"
                tx2["applied_at_mono"] = time.monotonic()
                tx2["last_inv_ack_mono"] = tx2["applied_at_mono"]
            await self._control_publish_result(
                tx=tx,
                status="applied",
                extra={
                    "old_value": old_v,
                    "observed_new_value": new_v,
                },
            )

            if (tx.get("tbl_name"), tx.get("tbl_item")) != (
                    "tbl_box_prms", "MODE"):
                await self._control_publish_result(tx=tx, status="completed", detail="applied")
                await self._control_finish_inflight()
                return

            if self._control_quiet_task and not self._control_quiet_task.done():
                self._control_quiet_task.cancel()
            self._control_quiet_task = asyncio.create_task(
                self._control_quiet_wait())
            return

        if ("Invertor ACK" in content and (tx.get("tbl_name"),
                                           tx.get("tbl_item")) == ("tbl_box_prms", "MODE")):
            async with self._control_lock:
                tx2 = self._control_inflight
                if tx2 is None or tx2.get("tx_id") != tx.get("tx_id"):
                    return
                if tx2.get("stage") != "applied":
                    return
                tx2["last_inv_ack_mono"] = time.monotonic()
            if self._control_quiet_task and not self._control_quiet_task.done():
                self._control_quiet_task.cancel()
            self._control_quiet_task = asyncio.create_task(
                self._control_quiet_wait())

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
        if self._loop is None:
            return {"ok": False, "error": "event_loop_not_ready"}

        fut = asyncio.run_coroutine_threadsafe(
            self._send_setting_to_box(
                tbl_name=tbl_name,
                tbl_item=tbl_item,
                new_value=new_value,
                confirm=confirm,
            ),
            self._loop,
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
        ) > self._control_ack_timeout_s:
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
                self._control_on_box_setting_ack(
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
