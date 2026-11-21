"""ProxyStatusPublisher – periodic MQTT status publishing for OIG Proxy v2."""

from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import logging
import time
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

if TYPE_CHECKING:
    from mqtt.client import MQTTClient
    from protocol.ver_profile import ProfileStatus
    from sensor.loader import SensorMapLoader

logger = logging.getLogger(__name__)


class ProxyStatusPublisher:
    """Builds and publishes proxy status payloads periodically."""

    def __init__(
        self,
        mqtt: MQTTClient,
        interval: int,
        proxy_device_id: str,
        sensor_loader: SensorMapLoader | None = None,
        get_configured_mode: Callable[[], str] | None = None,
        get_cloud_connects: Callable[[], int] | None = None,
        get_cloud_disconnects: Callable[[], int] | None = None,
        get_cloud_timeouts: Callable[[], int] | None = None,
        get_cloud_errors: Callable[[], int] | None = None,
        initial_device_id: str | None = None,
        get_control_inflight: Callable[[], Any] | None = None,
        get_control_queue_len: Callable[[], int] | None = None,
        get_ver_profile_status: Callable[[], ProfileStatus] | None = None,
    ) -> None:
        self._mqtt = mqtt
        self._interval = interval
        self._proxy_device_id = proxy_device_id
        self._sensor_loader = sensor_loader
        self._get_configured_mode = get_configured_mode
        self._get_cloud_connects = get_cloud_connects
        self._get_cloud_disconnects = get_cloud_disconnects
        self._get_cloud_timeouts = get_cloud_timeouts
        self._get_cloud_errors = get_cloud_errors
        self._get_control_inflight = get_control_inflight
        self._get_control_queue_len = get_control_queue_len
        self._get_ver_profile_status = get_ver_profile_status
        self._published_ver_profile_status: ProfileStatus | None = None
        self._last_setting_result: dict[str, Any] | None = None
        self._pending_setting_results: deque[dict[str, Any]] = deque()

        self._frame_count = 0
        self._last_frame_table = ""
        self._last_frame_device_id = initial_device_id or ""
        self._last_frame_timestamp = 0.0

        self._running = False

    def record_frame(self, device_id: str, table: str) -> None:
        """Record that a frame was received from a device/table."""
        self._frame_count += 1
        self._last_frame_table = table
        self._last_frame_device_id = device_id
        self._last_frame_timestamp = time.time()

    def box_data_recent(self, now: float | None = None) -> bool:
        """Apply the existing BOX availability boundary."""
        current = time.time() if now is None else now
        return bool(self._last_frame_timestamp and current - self._last_frame_timestamp <= 90)

    def _publish(self) -> bool:
        """Publish proxy status to MQTT."""
        if not self._mqtt.is_ready():
            logger.debug("MQTT not ready, skipping status publish")
            return False
        if self._pending_setting_results:
            self.flush_setting_results()
            return not self._pending_setting_results
        return self._publish_status(self._last_setting_result)

    def publish_ver_profile_change(self) -> None:
        """Refresh profile freshness and publish transitions on the owner loop."""
        getter = self._get_ver_profile_status
        if getter is not None and getter() != self._published_ver_profile_status:
            self._publish()

    def record_setting_result(self, event: dict[str, Any]) -> None:
        """Snapshot a terminal result on the asyncio owner and publish without coalescing."""
        result = deepcopy(event)
        result.setdefault("event_id", str(uuid4()))
        result.setdefault("at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        self._last_setting_result = result
        self._pending_setting_results.append(result)
        self.flush_setting_results()

    def flush_setting_results(self) -> None:
        """Retry the in-memory FIFO on the owner loop after status/recovery callbacks."""
        while self._pending_setting_results and self._mqtt.is_ready():
            result = self._pending_setting_results[0]
            if not self._publish_status(result, result_only=True):
                return
            self._pending_setting_results.popleft()

    def _publish_status(self, result: dict[str, Any] | None, *, result_only: bool = False) -> bool:
        """Publish one coherent status snapshot; result retries never announce availability."""

        connection_status = "connected" if self._mqtt.connected else "disconnected"
        now = time.time()
        box_connected = self.box_data_recent(now)
        last_data_iso = ""
        last_data_age_s: int | None = None
        if self._last_frame_timestamp:
            last_data_iso = datetime.fromtimestamp(
                self._last_frame_timestamp, timezone.utc
            ).isoformat().replace("+00:00", "Z")
            last_data_age_s = int(max(0, now - self._last_frame_timestamp))

        configured_mode = (
            self._get_configured_mode()
            if self._get_configured_mode is not None
            else "online"
        )
        payload: dict[str, Any] = {
            "status": "online" if box_connected else "offline",
            "mode": "online" if box_connected else "offline",
            "configured_mode": configured_mode,
            "connection_status": connection_status,
            "last_data_age_s": last_data_age_s if last_data_age_s is not None else 0,
            "box_connected": int(box_connected),
            "box_data_recent": int(box_connected),
            "cloud_online": int(self._mqtt.connected),
            "cloud_session_connected": int(self._mqtt.connected),
            "cloud_connects": (
                int(self._get_cloud_connects()) if self._get_cloud_connects else 0
            ),
            "cloud_disconnects": (
                int(self._get_cloud_disconnects())
                if self._get_cloud_disconnects
                else 0
            ),
            "cloud_timeouts": (
                int(self._get_cloud_timeouts()) if self._get_cloud_timeouts else 0
            ),
            "cloud_errors": (
                int(self._get_cloud_errors()) if self._get_cloud_errors else 0
            ),
            "mqtt_connected": int(self._mqtt.connected),
            "frame_count": self._frame_count,
            "last_frame_table": self._last_frame_table,
            "box_device_id": self._last_frame_device_id,
            "control_last_result": (
                f"{result['result']} {result['table']}:{result['key']}={result['value']}"
                if result is not None else "unknown"
            ),
            "control_result": deepcopy(result) if result is not None else {},
        }
        if self._get_control_inflight is not None:
            payload["control_inflight"] = self._get_control_inflight()
        if self._get_control_queue_len is not None:
            payload["control_queue_len"] = int(self._get_control_queue_len())
        profile_status = (
            self._get_ver_profile_status() if self._get_ver_profile_status is not None else None
        )
        if profile_status is not None:
            payload["control_ver_ready"] = int(profile_status.profile is not None)
            payload["control_ver_reason"] = profile_status.reason
        if last_data_iso:
            payload["last_data"] = last_data_iso
            payload["last_data_update"] = last_data_iso

        if self._sensor_loader is not None:
            for key in payload:
                metadata = self._sensor_loader.lookup("proxy_status", key)
                if metadata is None:
                    continue
                sensor_name = metadata.get("name_cs") or metadata.get("name") or key
                unit = metadata.get("unit_of_measurement") or ""
                device_class = metadata.get("device_class") or ""
                state_class = metadata.get("state_class") or ""
                entity_category = metadata.get("entity_category") or ""
                device_mapping = metadata.get("device_mapping") or "proxy"
                is_binary = bool(metadata.get("is_binary", False))
                self._mqtt.send_discovery(
                    device_id=self._proxy_device_id,
                    table="proxy_status",
                    sensor_key=key,
                    sensor_name=sensor_name,
                    unit=unit,
                    device_class=device_class,
                    state_class=state_class,
                    device_mapping=device_mapping,
                    entity_category=entity_category,
                    is_binary=is_binary,
                )

            for table, key, metadata in self._sensor_loader.iter_sensors():
                if table != "proxy_control":
                    continue
                sensor_name = metadata.get("name_cs") or metadata.get("name") or key
                unit = metadata.get("unit_of_measurement") or ""
                device_class = metadata.get("device_class") or ""
                state_class = metadata.get("state_class") or ""
                entity_category = metadata.get("entity_category") or "config"
                device_mapping = metadata.get("device_mapping") or "proxy"
                is_binary = bool(metadata.get("is_binary", False))
                enum_map = metadata.get("enum_map") or None
                self._mqtt.send_discovery(
                    device_id=self._proxy_device_id,
                    table=table,
                    sensor_key=key,
                    sensor_name=sensor_name,
                    unit=unit,
                    device_class=device_class,
                    state_class=state_class,
                    device_mapping=device_mapping,
                    entity_category=entity_category,
                    is_binary=is_binary,
                    enum_map=enum_map,
                )

        publish = self._mqtt.publish_confirmed_state if result_only else self._mqtt.publish_state
        published = publish(
            self._proxy_device_id,
            "proxy_status",
            payload,
        )
        if published:
            self._published_ver_profile_status = profile_status
        if self._sensor_loader is not None and not result_only:
            self._mqtt.publish_state(
                self._proxy_device_id,
                "proxy_control",
                {"PROXY_MODE": configured_mode},
            )
        logger.debug(
            "Published proxy status: %s frames, last: %s from %s",
            self._frame_count,
            self._last_frame_table,
            self._last_frame_device_id,
        )
        return published

    async def run(self) -> None:
        """Run periodic status publishing loop."""
        if self._interval <= 0:
            logger.info("Proxy status loop disabled (interval <= 0)")
            return

        logger.info("Proxy status: periodic publish every %ss", self._interval)
        self._running = True

        try:
            while self._running:
                try:
                    self._publish()
                except Exception as exc:
                    logger.debug("Proxy status loop error: %s", exc)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            logger.info("Proxy status loop cancelled")
            raise
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop the status publishing loop."""
        self._running = False
