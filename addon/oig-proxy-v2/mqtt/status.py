"""ProxyStatusPublisher – periodic MQTT status publishing for OIG Proxy v2."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mqtt.client import MQTTClient
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
    ) -> None:
        self._mqtt = mqtt
        self._interval = interval
        self._proxy_device_id = proxy_device_id
        self._sensor_loader = sensor_loader

        self._frame_count = 0
        self._last_frame_table = ""
        self._last_frame_device_id = ""
        self._last_frame_timestamp = 0.0

        self._running = False

    def record_frame(self, device_id: str, table: str) -> None:
        """Record that a frame was received from a device/table."""
        self._frame_count += 1
        self._last_frame_table = table
        self._last_frame_device_id = device_id
        self._last_frame_timestamp = time.time()

    def _publish(self) -> None:
        """Publish proxy status to MQTT."""
        if not self._mqtt.is_ready():
            logger.debug("MQTT not ready, skipping status publish")
            return

        connection_status = "connected" if self._mqtt.connected else "disconnected"
        now = time.time()
        box_connected = bool(
            self._last_frame_timestamp and (now - self._last_frame_timestamp) <= 90
        )
        last_data_iso = ""
        last_data_age_s: int | None = None
        if self._last_frame_timestamp:
            last_data_iso = datetime.fromtimestamp(
                self._last_frame_timestamp, timezone.utc
            ).isoformat().replace("+00:00", "Z")
            last_data_age_s = int(max(0, now - self._last_frame_timestamp))

        payload = {
            "status": "online" if box_connected else "offline",
            "mode": "online" if box_connected else "offline",
            "connection_status": connection_status,
            "last_data": last_data_iso,
            "last_data_update": last_data_iso,
            "last_data_age_s": last_data_age_s if last_data_age_s is not None else 0,
            "box_connected": int(box_connected),
            "box_data_recent": int(box_connected),
            "cloud_online": int(self._mqtt.connected),
            "cloud_session_connected": int(self._mqtt.connected),
            "mqtt_connected": int(self._mqtt.connected),
            "frame_count": self._frame_count,
            "last_frame_table": self._last_frame_table,
            "box_device_id": self._last_frame_device_id,
        }

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

        self._mqtt.publish_state(
            self._proxy_device_id,
            "proxy_status",
            payload,
        )
        logger.debug(
            "Published proxy status: %s frames, last: %s from %s",
            self._frame_count,
            self._last_frame_table,
            self._last_frame_device_id,
        )

    async def run(self) -> None:
        """Run periodic status publishing loop."""
        if self._interval <= 0:
            logger.info("Proxy status loop disabled (interval <= 0)")
            return

        logger.info("Proxy status: periodic publish every %ss", self._interval)
        self._running = True

        while self._running:
            try:
                self._publish()
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                logger.info("Proxy status loop cancelled")
                break
            except Exception as exc:
                logger.debug("Proxy status loop error: %s", exc)
                await asyncio.sleep(self._interval)

    def stop(self) -> None:
        """Stop the status publishing loop."""
        self._running = False
