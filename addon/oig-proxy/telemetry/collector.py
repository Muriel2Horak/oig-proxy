from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .client import TelemetryClient

logger = logging.getLogger(__name__)

ISNEW_STATE_TOPIC_ALIASES = {
    "isnewfw": "IsNewFW",
    "isnewset": "IsNewSet",
    "isnewweather": "IsNewWeather",
}


class TelemetryCollector:
    def __init__(
        self,
        *,
        interval_s: int,
        version: str = "unknown",
        telemetry_enabled: bool = True,
        telemetry_mqtt_broker: str = "telemetry.muriel-cz.cz:1883",
        telemetry_interval_s: int = 300,
        device_id: str = "",
        mqtt_namespace: str = "oig_local",
        mqtt_publisher: Any | None = None,
        get_mode: Callable[[], Any] | None = None,
        get_configured_mode: Callable[[], Any] | None = None,
        get_box_connected: Callable[[], Any] | None = None,
        get_box_peer: Callable[[], Any] | None = None,
        get_uptime_s: Callable[[], Any] | None = None,
        get_frames_received: Callable[[], Any] | None = None,
        get_frames_forwarded: Callable[[], Any] | None = None,
        get_cloud_connects: Callable[[], Any] | None = None,
        get_cloud_disconnects: Callable[[], Any] | None = None,
        get_cloud_timeouts: Callable[[], Any] | None = None,
        get_cloud_errors: Callable[[], Any] | None = None,
        get_cloud_session_connected: Callable[[], Any] | None = None,
        consume_set_commands: Callable[[], Any] | None = None,
        get_background_tasks: Callable[[], Any] | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.client: TelemetryClient | None = None
        self.task: asyncio.Task[Any] | None = None
        self.interval_s = interval_s
        self._version = version
        self._device_id = device_id
        self._mqtt_namespace = mqtt_namespace
        self._mqtt_publisher = mqtt_publisher
        self._get_mode = get_mode
        self._get_configured_mode = get_configured_mode
        self._get_box_connected = get_box_connected
        self._get_box_peer = get_box_peer
        self._get_uptime_s = get_uptime_s
        self._get_frames_received = get_frames_received
        self._get_frames_forwarded = get_frames_forwarded
        self._get_cloud_connects = get_cloud_connects
        self._get_cloud_disconnects = get_cloud_disconnects
        self._get_cloud_timeouts = get_cloud_timeouts
        self._get_cloud_errors = get_cloud_errors
        self._get_cloud_session_connected = get_cloud_session_connected
        self._consume_set_commands = consume_set_commands
        self._get_background_tasks = get_background_tasks
        self._telemetry_enabled = telemetry_enabled
        self._telemetry_mqtt_broker = telemetry_mqtt_broker
        self._telemetry_interval_s = telemetry_interval_s
        self._db_path = db_path

        self.box_sessions: deque[dict[str, Any]] = deque()
        self.cloud_sessions: deque[dict[str, Any]] = deque()
        self.hybrid_sessions: deque[dict[str, Any]] = deque()
        self.offline_events: deque[dict[str, Any]] = deque()
        self.tbl_events: deque[dict[str, Any]] = deque()
        self.error_context: deque[dict[str, Any]] = deque()

        self.logs: deque[dict[str, Any]] = deque()
        self._logs_lock = threading.Lock()
        self.log_window_s = 60
        self.log_max = 1000
        self.log_error = False

        self.debug_windows_remaining = 0
        self.box_seen_in_window = False
        self.force_logs_this_window = True
        self.cloud_ok_in_window = False
        self.cloud_failed_in_window = False
        self.cloud_eof_short_in_window = False

        self.req_pending: dict[int, deque[str]] = defaultdict(deque)
        self.stats: dict[tuple[str, str, str], Counter[str]] = {}

        self.nack_reasons: Counter[str] = Counter()
        self.conn_mismatch_drops = 0
        self.cloud_gap_durations: deque[dict[str, Any]] = deque()
        self.pairing_high = 0
        self.pairing_medium = 0
        self.pairing_low = 0
        self.frames_box_to_proxy = 0
        self.frames_cloud_to_proxy = 0
        self.frames_proxy_to_box = 0
        self.signal_class_counts: Counter[str] = Counter()
        self.end_frames_received = 0
        self.end_frames_sent = 0
        self.last_end_frame_time: float | None = None

    def update_device_id(self, device_id: str) -> None:
        self._device_id = device_id
        if self.client:
            self.client.device_id = device_id

    @staticmethod
    def _utc_iso(ts: float | None = None) -> str:
        if ts is None:
            ts = time.time()
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _utc_log_ts(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_frame_dt(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
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

    def _prune_log_buffer(self) -> None:
        cutoff = time.time() - float(self.log_window_s)
        while self.logs and self.logs[0]["_epoch"] < cutoff:
            self.logs.popleft()
        while len(self.logs) > self.log_max:
            self.logs.popleft()

    def record_log_entry(self, record: logging.LogRecord) -> None:
        if self.log_error:
            return
        if record.levelno >= logging.WARNING:
            self.debug_windows_remaining = 2
        if self.debug_windows_remaining <= 0 and not self.force_logs_this_window:
            return
        try:
            entry = {
                "_epoch": record.created,
                "timestamp": self._utc_log_ts(record.created),
                "level": record.levelname,
                "message": record.getMessage(),
                "source": record.name,
            }
            with self._logs_lock:
                self.logs.append(entry)
                self._prune_log_buffer()
        except Exception:
            self.log_error = True
            self.log_error = False

    def _snapshot_logs(self) -> list[dict[str, Any]]:
        with self._logs_lock:
            self._prune_log_buffer()
            return [{k: v for k, v in item.items() if k != "_epoch"} for item in self.logs]

    def _flush_log_buffer(self) -> list[dict[str, Any]]:
        with self._logs_lock:
            self._prune_log_buffer()
            logs = [{k: v for k, v in item.items() if k != "_epoch"} for item in self.logs]
            self.logs.clear()
        return logs

    def record_request(self, table_name: str | None, conn_id: int) -> None:
        if not table_name:
            return
        queue = self.req_pending[conn_id]
        queue.append(table_name)
        if len(queue) > 1000:
            queue.popleft()

    @staticmethod
    def _response_kind(response_text: str) -> str:
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

    @staticmethod
    def _extract_nack_reason(response_text: str) -> str:
        import re

        match = re.search(r"<Reason>([^<]*)</Reason>", response_text)
        return match.group(1).strip() if match else "unknown"

    def record_nack_reason(self, reason: str) -> None:
        self.nack_reasons[reason] += 1

    def record_conn_mismatch(self) -> None:
        self.conn_mismatch_drops += 1

    def record_cloud_gap(self, duration_s: float) -> None:
        self.cloud_gap_durations.append({"timestamp": self._utc_iso(), "duration_s": duration_s})

    def record_pairing_confidence(self, confidence: float) -> None:
        if confidence >= 0.8:
            self.pairing_high += 1
        elif confidence >= 0.5:
            self.pairing_medium += 1
        else:
            self.pairing_low += 1

    def record_frame_direction(self, direction: str) -> None:
        if direction == "box_to_proxy":
            self.frames_box_to_proxy += 1
        elif direction == "cloud_to_proxy":
            self.frames_cloud_to_proxy += 1
        elif direction == "proxy_to_box":
            self.frames_proxy_to_box += 1

    def record_signal_class(self, signal_class: str) -> None:
        self.signal_class_counts[signal_class] += 1

    def record_end_frame(self, sent: bool = False) -> None:
        if sent:
            self.end_frames_sent += 1
        else:
            self.end_frames_received += 1
        self.last_end_frame_time = time.time()

    def _safe_mode_value(self) -> str:
        if self._get_mode is None:
            return "offline"
        mode_value = self._get_mode()
        mode_value_str = str(mode_value).strip().lower() if mode_value is not None else "offline"
        if mode_value_str not in {"online", "hybrid", "offline"}:
            return "offline"
        return mode_value_str

    def record_response(self, response_text: str, *, source: str, conn_id: int) -> None:
        queue = self.req_pending.get(conn_id)
        if queue:
            table_name = queue.popleft()
        else:
            table_name = "unmatched"
        if queue is not None and not queue:
            self.req_pending.pop(conn_id, None)
        key = (table_name, source, self._safe_mode_value())
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
        if "<Result>NACK</Result>" in response_text:
            self.record_nack_reason(self._extract_nack_reason(response_text))
        if source == "cloud":
            self.cloud_ok_in_window = True

    def record_timeout(self, *, conn_id: int) -> None:
        self.cloud_failed_in_window = True
        self.record_response("", source="timeout", conn_id=conn_id)

    def _flush_stats(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for (table, source, mode), counts in self.stats.items():
            items.append(
                {
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
                }
            )
        self.stats.clear()
        return items

    def record_error_context(self, *, event_type: str, details: dict[str, Any]) -> None:
        try:
            details_json = json.dumps(details, ensure_ascii=False)
        except Exception:
            details_json = json.dumps({"detail": str(details)}, ensure_ascii=False)
        self.error_context.append(
            {
                "timestamp": self._utc_iso(),
                "event_type": event_type,
                "details": details_json,
                "logs": json.dumps(self._snapshot_logs(), ensure_ascii=False),
            }
        )

    def record_tbl_event(self, *, parsed: dict[str, Any], device_id: str | None) -> None:
        event_time = self._parse_frame_dt(parsed.get("_dt")) or self._utc_iso()
        self.tbl_events.append(
            {
                "timestamp": event_time,
                "event_time": event_time,
                "type": parsed.get("Type"),
                "confirm": parsed.get("Confirm"),
                "content": parsed.get("Content"),
                "device_id": device_id,
            }
        )

    def record_box_session_end(self, *, connected_since_epoch: float | None, reason: str, peer: str | None) -> None:
        if connected_since_epoch is None:
            return
        disconnected_at = time.time()
        self.box_sessions.append(
            {
                "timestamp": self._utc_iso(disconnected_at),
                "connected_at": self._utc_iso(connected_since_epoch),
                "disconnected_at": self._utc_iso(disconnected_at),
                "duration_s": int(disconnected_at - connected_since_epoch),
                "peer": peer,
                "reason": reason,
            }
        )

    def record_cloud_session_end(self, *, connected_since_epoch: float | None, reason: str) -> None:
        if connected_since_epoch is None:
            return
        disconnected_at = time.time()
        duration = disconnected_at - connected_since_epoch
        if reason == "eof" and duration < 1.0 and not self.cloud_ok_in_window:
            self.cloud_eof_short_in_window = True
        self.cloud_sessions.append(
            {
                "timestamp": self._utc_iso(disconnected_at),
                "connected_at": self._utc_iso(connected_since_epoch),
                "disconnected_at": self._utc_iso(disconnected_at),
                "duration_s": int(duration),
                "reason": reason,
            }
        )

    def record_hybrid_state_end(
        self,
        *,
        state: str | None,
        state_since_epoch: float | None,
        ended_at: float,
        mode: str,
        reason: str | None = None,
    ) -> None:
        if state_since_epoch is None or state is None:
            return
        self.hybrid_sessions.append(
            {
                "timestamp": self._utc_iso(ended_at),
                "state": state,
                "started_at": self._utc_iso(state_since_epoch),
                "ended_at": self._utc_iso(ended_at),
                "duration_s": int(ended_at - state_since_epoch),
                "reason": reason,
                "mode": mode,
            }
        )

    def record_offline_event(self, *, reason: str | None, local_ack: bool | None, mode: str) -> None:
        self.offline_events.append(
            {
                "timestamp": self._utc_iso(),
                "reason": reason or "unknown",
                "local_ack": bool(local_ack),
                "mode": mode,
            }
        )

    def init(self) -> None:
        try:
            self.client = TelemetryClient(
                self._device_id,
                self._version,
                telemetry_enabled=self._telemetry_enabled,
                telemetry_mqtt_broker=self._telemetry_mqtt_broker,
                telemetry_interval_s=self._telemetry_interval_s,
                db_path=self._db_path,
            )
        except Exception:
            self.client = None

    @staticmethod
    def _load_version_from_config(config_path: Path) -> str:
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as fobj:
                    data = json.load(fobj)
                version = data.get("version")
                if version:
                    return str(version)
        except Exception:
            return "unknown"
        return "unknown"

    async def loop(self) -> None:
        if not self.client:
            return
        await asyncio.sleep(30)
        try:
            if self.client.device_id == "" and self._device_id:
                self.client.device_id = self._device_id
            await self.client.provision()
        except Exception:
            pass
        try:
            if self.client.device_id == "" and self._device_id:
                self.client.device_id = self._device_id
            metrics = self.collect_metrics()
            await self.client.send_telemetry(metrics)
        except Exception:
            pass
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                if self.client.device_id == "" and self._device_id:
                    self.client.device_id = self._device_id
                metrics = self.collect_metrics()
                await self.client.send_telemetry(metrics)
            except Exception:
                pass

    def _get_box_connected_window_status(self) -> bool:
        box_connected_now = bool(self._get_box_connected()) if self._get_box_connected else False
        box_connected_window = box_connected_now or self.box_seen_in_window
        self.box_seen_in_window = False
        return box_connected_window

    @staticmethod
    def _should_include_logs(debug_active: bool, box_connected_window: bool) -> bool:
        return debug_active or not box_connected_window

    def _get_telemetry_logs(self, debug_active: bool, include_logs: bool) -> list[dict[str, Any]]:
        logs = self._flush_log_buffer() if include_logs else []
        if not include_logs:
            with self._logs_lock:
                self.logs.clear()
        if debug_active:
            self.debug_windows_remaining -= 1
        return logs

    def _get_cloud_online_window_status(self) -> bool:
        if self.cloud_ok_in_window:
            cloud_online_window = True
        elif self.cloud_failed_in_window or self.cloud_eof_short_in_window:
            cloud_online_window = False
        elif self._get_cloud_session_connected and bool(self._get_cloud_session_connected()):
            cloud_online_window = True
        else:
            cloud_online_window = False
        self.cloud_ok_in_window = False
        self.cloud_failed_in_window = False
        self.cloud_eof_short_in_window = False
        return cloud_online_window

    def _collect_hybrid_sessions(self) -> list[dict[str, Any]]:
        return list(self.hybrid_sessions)

    def _collect_and_clear_window_metrics(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
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

    def _cached_state_value(self, device_id: str, table_name: str, field_name: str) -> Any | None:
        mqtt_pub = self._mqtt_publisher
        if not mqtt_pub:
            return None
        table_candidates = [table_name]
        alias = ISNEW_STATE_TOPIC_ALIASES.get(table_name)
        if alias:
            table_candidates.append(alias)
        payload = None
        for candidate in table_candidates:
            topic = f"{self._mqtt_namespace}/{device_id}/{candidate}/state"
            getter = getattr(mqtt_pub, "get_cached_payload", None)
            if not getter:
                return None
            payload = getter(topic)
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

    def _build_cloud_gap_histogram(self) -> dict[str, int]:
        buckets = {"lt_60s": 0, "60_120s": 0, "120_300s": 0, "300_600s": 0, "gt_600s": 0}
        for entry in self.cloud_gap_durations:
            d = entry["duration_s"]
            if d < 60:
                buckets["lt_60s"] += 1
            elif d < 120:
                buckets["60_120s"] += 1
            elif d < 300:
                buckets["120_300s"] += 1
            elif d <= 600:
                buckets["300_600s"] += 1
            else:
                buckets["gt_600s"] += 1
        return buckets

    def _build_device_specific_metrics(self, device_id: str) -> dict[str, Any]:
        return {
            "isnewfw_fw": self._cached_state_value(device_id, "isnewfw", "fw"),
            "isnewset_lat": self._cached_state_value(device_id, "isnewset", "lat"),
            "tbl_box_tmlastcall": self._cached_state_value(device_id, "tbl_box", "tmlastcall"),
            "isnewweather_loadedon": self._cached_state_value(device_id, "isnewweather", "loadedon"),
            "tbl_box_strnght": self._cached_state_value(device_id, "tbl_box", "strnght"),
            "tbl_invertor_prms_model": self._cached_state_value(device_id, "tbl_invertor_prms", "model"),
        }

    def collect_metrics(self) -> dict[str, Any]:
        uptime_s = int(self._get_uptime_s()) if self._get_uptime_s else 0
        set_commands = self._consume_set_commands() if self._consume_set_commands else []
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
            "mode": self._safe_mode_value(),
            "configured_mode": self._get_configured_mode() if self._get_configured_mode else "offline",
            "box_connected": box_connected_window,
            "box_peer": self._get_box_peer() if self._get_box_peer else None,
            "frames_received": int(self._get_frames_received()) if self._get_frames_received else 0,
            "frames_forwarded": int(self._get_frames_forwarded()) if self._get_frames_forwarded else 0,
            "cloud_connects": int(self._get_cloud_connects()) if self._get_cloud_connects else 0,
            "cloud_disconnects": int(self._get_cloud_disconnects()) if self._get_cloud_disconnects else 0,
            "cloud_timeouts": int(self._get_cloud_timeouts()) if self._get_cloud_timeouts else 0,
            "cloud_errors": int(self._get_cloud_errors()) if self._get_cloud_errors else 0,
            "cloud_online": cloud_online_window,
            "mqtt_ok": self._mqtt_publisher.is_ready() if self._mqtt_publisher else False,
            "mqtt_queue": 0,
            "set_commands": set_commands,
            "window_metrics": window_metrics,
            "nack_reasons": dict(self.nack_reasons),
            "conn_mismatch_drops": self.conn_mismatch_drops,
            "cloud_gap_histogram": self._build_cloud_gap_histogram(),
            "pairing_confidence": {
                "high": self.pairing_high,
                "medium": self.pairing_medium,
                "low": self.pairing_low,
            },
            "frame_directions": {
                "box_to_proxy": self.frames_box_to_proxy,
                "cloud_to_proxy": self.frames_cloud_to_proxy,
                "proxy_to_box": self.frames_proxy_to_box,
            },
            "signal_distribution": dict(self.signal_class_counts),
            "end_frames": {
                "received": self.end_frames_received,
                "sent": self.end_frames_sent,
                "time_since_last_s": int(time.time() - self.last_end_frame_time)
                if self.last_end_frame_time
                else None,
            },
        }
        self.nack_reasons.clear()
        self.conn_mismatch_drops = 0
        self.cloud_gap_durations.clear()
        self.pairing_high = 0
        self.pairing_medium = 0
        self.pairing_low = 0
        self.frames_box_to_proxy = 0
        self.frames_cloud_to_proxy = 0
        self.frames_proxy_to_box = 0
        self.signal_class_counts.clear()
        self.end_frames_received = 0
        self.end_frames_sent = 0
        self.last_end_frame_time = None
        if self._device_id:
            metrics.update(self._build_device_specific_metrics(self._device_id))
        return metrics

    def fire_event(self, event_name: str, **kwargs: Any) -> None:
        if not self.client:
            return
        if event_name.startswith(("error_", "warning_")):
            self.record_error_context(event_type=event_name, details=kwargs)
        method = getattr(self.client, f"event_{event_name}", None)
        if method:
            task = asyncio.create_task(method(**kwargs))
            if self._get_background_tasks:
                tasks = self._get_background_tasks()
                if isinstance(tasks, set):
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
