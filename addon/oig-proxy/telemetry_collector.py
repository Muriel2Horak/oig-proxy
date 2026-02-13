#!/usr/bin/env python3
"""
TelemetryCollector – sběr a agregace telemetrických dat pro OIG Proxy.

Extrahováno z God Object OIGProxy pro lepší oddělení odpovědností.
Přistupuje ke sdílenému stavu proxy přes referenci self._proxy.
"""

# pylint: disable=too-many-instance-attributes,too-many-lines,broad-exception-caught

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from models import ProxyMode
from telemetry_client import TelemetryClient

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)

ISNEW_STATE_TOPIC_ALIASES = {
    "isnewfw": "IsNewFW",
    "isnewset": "IsNewSet",
    "isnewweather": "IsNewWeather",
}


class TelemetryCollector:
    """Sbírá, agreguje a odesílá telemetrická data pro diagnostiku proxy."""

    def __init__(self, proxy: OIGProxy, *, interval_s: int) -> None:
        self._proxy = proxy
        self.client: TelemetryClient | None = None
        self.task: asyncio.Task[Any] | None = None
        self.interval_s: int = interval_s

        # Per-window deques
        self.box_sessions: deque[dict[str, Any]] = deque()
        self.cloud_sessions: deque[dict[str, Any]] = deque()
        self.hybrid_sessions: deque[dict[str, Any]] = deque()
        self.offline_events: deque[dict[str, Any]] = deque()
        self.tbl_events: deque[dict[str, Any]] = deque()
        self.error_context: deque[dict[str, Any]] = deque()

        # Log buffer
        self.logs: deque[dict[str, Any]] = deque()
        self.log_window_s: int = 60
        self.log_max: int = 1000
        self.log_error: bool = False

        # Per-window flags
        self.debug_windows_remaining: int = 0
        self.box_seen_in_window: bool = False
        self.force_logs_this_window: bool = True
        self.cloud_ok_in_window: bool = False
        self.cloud_failed_in_window: bool = False
        self.cloud_eof_short_in_window: bool = False

        # Request/response stats
        self.req_pending: dict[int, deque[str]] = defaultdict(deque)
        self.stats: dict[tuple[str, str, str], Counter[str]] = {}

    # ------------------------------------------------------------------
    # Timestamp helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _utc_iso(ts: float | None = None) -> str:
        if ts is None:
            ts = time.time()
        return datetime.fromtimestamp(
            ts, timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _utc_log_ts(ts: float) -> str:
        return datetime.fromtimestamp(
            ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

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

    # ------------------------------------------------------------------
    # Log buffer management
    # ------------------------------------------------------------------
    def _prune_log_buffer(self) -> None:
        cutoff = time.time() - float(self.log_window_s)
        while self.logs and self.logs[0]["_epoch"] < cutoff:
            self.logs.popleft()
        while len(self.logs) > self.log_max:
            self.logs.popleft()

    def record_log_entry(self, record: logging.LogRecord) -> None:
        """Zachytí log záznam do telemetry bufferu."""
        if self.log_error:
            return
        if record.levelno >= logging.WARNING:
            self.debug_windows_remaining = 2
        if (
            self.debug_windows_remaining <= 0
            and not self.force_logs_this_window
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
            self.logs.append(entry)
            self._prune_log_buffer()
        except Exception:
            self.log_error = True
            try:
                logger.exception(
                    "Failed to record telemetry log entry for record %r",
                    record,
                )
            finally:
                self.log_error = False

    def _snapshot_logs(self) -> list[dict[str, Any]]:
        self._prune_log_buffer()
        return [
            {k: v for k, v in item.items() if k != "_epoch"}
            for item in self.logs
        ]

    def _flush_log_buffer(self) -> list[dict[str, Any]]:
        logs = self._snapshot_logs()
        self.logs.clear()
        return logs

    # ------------------------------------------------------------------
    # Request / response tracking
    # ------------------------------------------------------------------
    def record_request(self, table_name: str | None, conn_id: int) -> None:
        """Zaznamená odeslaný request (tabulku) pro korelaci s odpovědí."""
        if not table_name:
            return
        queue = self.req_pending[conn_id]
        queue.append(table_name)
        if len(queue) > 1000:
            queue.popleft()

    @staticmethod
    def _response_kind(response_text: str) -> str:  # pylint: disable=too-many-return-statements
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

    def record_response(
        self,
        response_text: str,
        *,
        source: str,
        conn_id: int,
    ) -> None:
        """Spáruje odpověď s requestem a aktualizuje statistiky."""
        queue = self.req_pending.get(conn_id)
        if queue:
            table_name = queue.popleft()
        else:
            table_name = "unmatched"
        if queue is not None and not queue:
            self.req_pending.pop(conn_id, None)
        mode_value = getattr(self._proxy, "mode", None)
        if mode_value is None:
            mode_value = getattr(self._proxy, "_mode_value", ProxyMode.OFFLINE.value)
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
        stats_counter = self.stats.setdefault(
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
        stats_counter["req_count"] += 1
        stats_counter[self._response_kind(response_text)] += 1
        if source == "cloud":
            self.cloud_ok_in_window = True

    def record_timeout(self, *, conn_id: int) -> None:
        """Zaznamená timeout při čekání na odpověď z cloudu."""
        self.cloud_failed_in_window = True
        self.record_response("", source="timeout", conn_id=conn_id)

    def _flush_stats(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for (table, source, mode), counts in self.stats.items():
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
        self.stats.clear()
        return items

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------
    def record_error_context(self, *, event_type: str,
                             details: dict[str, Any]) -> None:
        """Zaznamená kontext chyby pro diagnostiku."""
        try:
            details_json = json.dumps(details, ensure_ascii=False)
        except Exception:
            details_json = json.dumps(
                {"detail": str(details)}, ensure_ascii=False)
        self.error_context.append({
            "timestamp": self._utc_iso(),
            "event_type": event_type,
            "details": details_json,
            "logs": json.dumps(self._snapshot_logs(), ensure_ascii=False),
        })

    def record_tbl_event(
        self,
        *,
        parsed: dict[str, Any],
        device_id: str | None,
    ) -> None:
        """Zaznamená TBL (tabulkový) event z box framu."""
        event_time = self._parse_frame_dt(parsed.get("_dt"))
        if event_time is None:
            event_time = self._utc_iso()
        self.tbl_events.append({
            "timestamp": event_time,
            "event_time": event_time,
            "type": parsed.get("Type"),
            "confirm": parsed.get("Confirm"),
            "content": parsed.get("Content"),
            "device_id": device_id,
        })

    def record_box_session_end(
            self,
            *,
            reason: str,
            peer: str | None) -> None:
        """Zaznamená konec TCP session s boxem."""
        if self._proxy._box_connected_since_epoch is None:  # pylint: disable=protected-access
            return
        disconnected_at = time.time()
        self.box_sessions.append({
            "timestamp": self._utc_iso(disconnected_at),
            "connected_at": self._utc_iso(self._proxy._box_connected_since_epoch),  # pylint: disable=protected-access
            "disconnected_at": self._utc_iso(disconnected_at),
            "duration_s": int(disconnected_at - self._proxy._box_connected_since_epoch),  # pylint: disable=protected-access
            "peer": peer,
            "reason": reason,
        })
        self._proxy._box_connected_since_epoch = None  # pylint: disable=protected-access

    def record_cloud_session_end(self, *, reason: str) -> None:
        """Zaznamená konec TCP session s cloudem."""
        if self._proxy._cloud_connected_since_epoch is None:  # pylint: disable=protected-access
            return
        disconnected_at = time.time()
        duration = disconnected_at - self._proxy._cloud_connected_since_epoch  # pylint: disable=protected-access
        if (
            reason == "eof"
            and duration < 1.0
            and not self.cloud_ok_in_window
        ):
            self.cloud_eof_short_in_window = True
        self.cloud_sessions.append({
            "timestamp": self._utc_iso(disconnected_at),
            "connected_at": self._utc_iso(self._proxy._cloud_connected_since_epoch),  # pylint: disable=protected-access
            "disconnected_at": self._utc_iso(disconnected_at),
            "duration_s": int(duration),
            "reason": reason,
        })
        self._proxy._cloud_connected_since_epoch = None  # pylint: disable=protected-access

    def record_hybrid_state_end(
            self,
            *,
            ended_at: float,
            reason: str | None = None) -> None:
        """Zaznamená konec hybrid mode stavu (online/offline)."""
        hm = self._proxy._hm  # pylint: disable=protected-access
        if hm.state_since_epoch is None or hm.state is None:
            return
        self.hybrid_sessions.append({
            "timestamp": self._utc_iso(ended_at),
            "state": hm.state,
            "started_at": self._utc_iso(hm.state_since_epoch),
            "ended_at": self._utc_iso(ended_at),
            "duration_s": int(ended_at - hm.state_since_epoch),
            "reason": reason,
            "mode": hm.mode.value,
        })
        hm.state_since_epoch = None
        hm.state = None

    def record_offline_event(
            self,
            *,
            reason: str | None,
            local_ack: bool | None) -> None:
        """Zaznamená offline fallback event."""
        self.offline_events.append({
            "timestamp": self._utc_iso(),
            "reason": reason or "unknown",
            "local_ack": bool(local_ack),
            "mode": self._proxy._hm.mode.value,  # pylint: disable=protected-access
        })

    # ------------------------------------------------------------------
    # Telemetry client lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Inicializuje telemetry klienta (fail-safe)."""
        try:
            proxy_version = self._load_version_from_config()
            device_id = self._proxy.device_id if self._proxy.device_id != "AUTO" else ""
            self.client = TelemetryClient(device_id, proxy_version)
            logger.info(
                "📊 Telemetry client initialized (version=%s, interval=%ss)",
                proxy_version,
                self.interval_s)
        except Exception as exc:
            logger.warning("Telemetry init failed (disabled): %s", exc)
            self.client = None

    @staticmethod
    def _load_version_from_config() -> str:
        """Load version from config.json or fallback to package metadata."""
        import os  # pylint: disable=import-outside-toplevel
        try:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(config_path):
                with open(config_path, encoding="utf-8") as fobj:
                    config_data = json.load(fobj)
                    version = config_data.get("version")
                    if version:
                        logger.debug("Loaded version %s from config.json", version)
                        return version
        except Exception as exc:
            logger.debug("Failed to load version from config.json: %s", exc)

        try:
            # pylint: disable=import-outside-toplevel
            from importlib.metadata import version as pkg_version
            version = pkg_version("oig-proxy")
            logger.debug("Loaded version %s from package metadata", version)
            return version
        except Exception as exc:
            logger.debug("Failed to load version from package metadata: %s", exc)

        logger.warning("Could not determine version, using default 1.6.2")
        return "1.6.2"

    async def loop(self) -> None:
        """Periodicky odesílá telemetrii na diagnostický server."""
        if not self.client:
            return

        await asyncio.sleep(30)

        # Initial provisioning
        try:
            if self.client.device_id == "" and self._proxy.device_id != "AUTO":
                self.client.device_id = self._proxy.device_id
            await self.client.provision()
        except Exception as exc:
            logger.debug("Initial telemetry provisioning failed: %s", exc)

        logger.info(
            "📊 Telemetry loop started (every %ss)",
            self.interval_s)

        # First telemetry immediately
        try:
            if self.client.device_id == "" and self._proxy.device_id != "AUTO":
                self.client.device_id = self._proxy.device_id
            metrics = self.collect_metrics()
            await self.client.send_telemetry(metrics)
            logger.info("📊 First telemetry sent")
        except Exception as exc:
            logger.debug("First telemetry send failed: %s", exc)

        while True:
            await asyncio.sleep(self.interval_s)
            try:
                if self.client.device_id == "" and self._proxy.device_id != "AUTO":
                    self.client.device_id = self._proxy.device_id
                metrics = self.collect_metrics()
                await self.client.send_telemetry(metrics)
            except Exception as exc:
                logger.debug("Telemetry send failed: %s", exc)

    # ------------------------------------------------------------------
    # Metrics collection
    # ------------------------------------------------------------------
    def _get_box_connected_window_status(self) -> bool:
        box_connected_window = self._proxy.box_connected or self.box_seen_in_window
        self.box_seen_in_window = False
        return box_connected_window

    @staticmethod
    def _should_include_logs(
        debug_active: bool,
        box_connected_window: bool,
    ) -> bool:
        return debug_active or not box_connected_window

    def _get_telemetry_logs(
        self,
        debug_active: bool,
        include_logs: bool,
    ) -> list[dict[str, Any]]:
        logs = self._flush_log_buffer() if include_logs else []
        if not include_logs:
            self.logs.clear()
        if debug_active:
            self.debug_windows_remaining -= 1
        return logs

    def _get_cloud_online_window_status(self) -> bool:
        if self.cloud_ok_in_window:
            cloud_online_window = True
        elif self.cloud_failed_in_window or self.cloud_eof_short_in_window:
            cloud_online_window = False
        elif self._proxy.cloud_session_connected:
            cloud_online_window = True
        else:
            cloud_online_window = False
        self.cloud_ok_in_window = False
        self.cloud_failed_in_window = False
        self.cloud_eof_short_in_window = False
        return cloud_online_window

    def _collect_hybrid_sessions(self) -> list[dict[str, Any]]:
        result = list(self.hybrid_sessions)
        hm = self._proxy._hm  # pylint: disable=protected-access
        if (hm.configured_mode == "hybrid"
                and hm.state_since_epoch is not None):
            now = time.time()
            result.append({
                "timestamp": self._utc_iso(now),
                "state": hm.state,
                "started_at": self._utc_iso(hm.state_since_epoch),
                "ended_at": None,
                "duration_s": int(now - hm.state_since_epoch),
                "reason": (
                    hm.last_offline_reason
                    if hm.state == "offline"
                    else None
                ),
                "mode": hm.mode.value,
            })
        return result

    def _collect_and_clear_window_metrics(
        self,
        logs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        window_metrics = {
            "box_sessions": list(self.box_sessions),
            "cloud_sessions": list(self.cloud_sessions),
            "hybrid_sessions": self._collect_hybrid_sessions(),
            "offline_events": list(self.offline_events),
            "tbl_events": list(self.tbl_events),
            "error_context": list(self.error_context),
            "stats": self._flush_stats(),
            "logs": logs,
        }
        self.box_sessions.clear()
        self.cloud_sessions.clear()
        self.hybrid_sessions.clear()
        self.offline_events.clear()
        self.tbl_events.clear()
        self.error_context.clear()
        self.force_logs_this_window = True
        return window_metrics

    def _cached_state_value(  # pylint: disable=too-many-return-statements
        self,
        device_id: str,
        table_name: str,
        field_name: str,
    ) -> Any | None:
        mqtt_pub = self._proxy.mqtt_publisher
        if not mqtt_pub:
            return None
        table_candidates = [table_name]
        alias = ISNEW_STATE_TOPIC_ALIASES.get(table_name)
        if alias:
            table_candidates.append(alias)
        payload = None
        for candidate in table_candidates:
            topic = mqtt_pub.state_topic(device_id, candidate)
            payload = mqtt_pub.get_cached_payload(topic)
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

    def _build_device_specific_metrics(self, device_id: str) -> dict[str, Any]:
        return {
            "isnewfw_fw": self._cached_state_value(
                device_id, "isnewfw", "fw"
            ),
            "isnewset_lat": self._cached_state_value(
                device_id, "isnewset", "lat"
            ),
            "tbl_box_tmlastcall": self._cached_state_value(
                device_id, "tbl_box", "tmlastcall"
            ),
            "isnewweather_loadedon": self._cached_state_value(
                device_id, "isnewweather", "loadedon"
            ),
            "tbl_box_strnght": self._cached_state_value(
                device_id, "tbl_box", "strnght"
            ),
            "tbl_invertor_prms_model": self._cached_state_value(
                device_id, "tbl_invertor_prms", "model"
            ),
        }

    def collect_metrics(self) -> dict[str, Any]:
        """Sestaví kompletní telemetrický payload."""
        proxy = self._proxy
        uptime_s = int(time.time() - proxy._start_time)  # pylint: disable=protected-access
        set_commands = proxy._set_commands_buffer[:]  # pylint: disable=protected-access
        proxy._set_commands_buffer.clear()  # pylint: disable=protected-access
        debug_active = self.debug_windows_remaining > 0
        box_connected_window = self._get_box_connected_window_status()
        include_logs = self._should_include_logs(debug_active, box_connected_window)
        logs = self._get_telemetry_logs(debug_active, include_logs)
        cloud_online_window = self._get_cloud_online_window_status()
        window_metrics = self._collect_and_clear_window_metrics(logs)

        metrics: dict[str, Any] = {
            "timestamp": self._utc_iso(),
            "interval_s": int(self.interval_s),
            "uptime_s": uptime_s,
            "mode": proxy._hm.mode.value,  # pylint: disable=protected-access
            "configured_mode": proxy._hm.configured_mode,  # pylint: disable=protected-access
            "box_connected": box_connected_window,
            "box_peer": proxy._active_box_peer,  # pylint: disable=protected-access
            "frames_received": proxy.stats.get("frames_received", 0),
            "frames_forwarded": proxy.stats.get("frames_forwarded", 0),
            "cloud_connects": proxy.cloud_connects,
            "cloud_disconnects": proxy.cloud_disconnects,
            "cloud_timeouts": proxy.cloud_timeouts,
            "cloud_errors": proxy.cloud_errors,
            "cloud_online": cloud_online_window,
            "mqtt_ok": proxy.mqtt_publisher.is_ready() if proxy.mqtt_publisher else False,
            "mqtt_queue": proxy.mqtt_publisher.queue.size() if proxy.mqtt_publisher else 0,
            "set_commands": set_commands,
            "window_metrics": window_metrics,
        }
        device_id = proxy.device_id if proxy.device_id != "AUTO" else ""
        if device_id:
            metrics.update(self._build_device_specific_metrics(device_id))
        return metrics

    # ------------------------------------------------------------------
    # Fire-and-forget events
    # ------------------------------------------------------------------
    def fire_event(self, event_name: str, **kwargs: Any) -> None:
        """Odešle telemetrický event (non-blocking, fire and forget)."""
        if not self.client:
            return
        if event_name.startswith(("error_", "warning_")):
            self.record_error_context(event_type=event_name, details=kwargs)
        method = getattr(self.client, f"event_{event_name}", None)
        if method:
            task = asyncio.create_task(method(**kwargs))
            self._proxy._background_tasks.add(task)  # pylint: disable=protected-access
            task.add_done_callback(self._proxy._background_tasks.discard)  # pylint: disable=protected-access
