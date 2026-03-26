"""Twin MQTT control handler for OIG Proxy v2.

Subscribes to control topics and enqueues settings for device twin.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

from settings_constraints import is_setting_allowed, validate_setting_value

if TYPE_CHECKING:
    from .state import TwinQueue

logger = logging.getLogger(__name__)


class TwinControlHandler:
    """Handles MQTT control messages for device twin settings.

    Subscribes to `oig/{device_id}/control/set` and enqueues
    settings for later delivery to the device.

    Example:
        >>> handler = TwinControlHandler(mqtt_client, twin_queue, "device_123")
        >>> await handler.start()
        # Now listening for control messages
        >>> await handler.stop()
    """

    def __init__(
        self,
        mqtt: Any,
        twin_queue: TwinQueue,
        device_id: str,
        namespace: str = "oig_local",
        proxy_control_handler: Callable[[str, str, Any], bool] | None = None,
    ) -> None:
        """Initialize the control handler.

        Args:
            mqtt: The MQTT client instance
            twin_queue: The twin queue for storing pending settings
            device_id: The device ID for topic subscription
        """
        self._mqtt = mqtt
        self._twin_queue = twin_queue
        self._device_id = device_id
        self._namespace = namespace
        self._topic: str = "oig/+/control/set"
        self._topic_compat: str = f"{self._namespace}/+/set/#"
        self._subscribed = False
        self._proxy_control_handler = proxy_control_handler

    async def start(self) -> None:
        """Start the handler by subscribing to the control topic.

        Subscribes to `oig/{device_id}/control/set` and sets up
        the message callback to parse and enqueue settings.
        """
        if not self._mqtt.is_ready():
            logger.warning("TwinControlHandler: MQTT not ready, cannot subscribe")
            return

        # Subscribe to control topic
        self._mqtt.subscribe(self._topic, self._on_message)
        self._mqtt.subscribe(self._topic_compat, self._on_message)
        self._subscribed = True
        logger.info(
            "TwinControlHandler: Subscribed to %s and %s",
            self._topic,
            self._topic_compat,
        )

    async def stop(self) -> None:
        """Stop the handler by unsubscribing from the control topic."""
        if self._subscribed and self._mqtt.is_ready():
            self._mqtt.unsubscribe(self._topic)
            self._mqtt.unsubscribe(self._topic_compat)
            self._subscribed = False
            logger.info(
                "TwinControlHandler: Unsubscribed from %s and %s",
                self._topic,
                self._topic_compat,
            )

    def _on_message(self, topic: str, payload: bytes) -> None:
        """Handle incoming MQTT message.

        Parses JSON payload and enqueues the setting.

        Args:
            topic: The MQTT topic
            payload: The raw message payload
        """
        try:
            raw_payload = payload.decode("utf-8", errors="replace")
            logger.info("📥 Twin MQTT message: topic=%s payload=%s", topic, raw_payload)

            if topic.startswith(f"{self._namespace}/") and "/set/" in topic:
                path = topic.split("/")
                if len(path) >= 5:
                    table = path[3]
                    key = path[4]
                    value_raw = raw_payload
                    if not is_setting_allowed(table, key):
                        logger.warning("Twin setting rejected: %s:%s not allowed", table, key)
                        return
                    ok, normalized, reason = validate_setting_value(table, key, value_raw)
                    if not ok:
                        logger.warning(
                            "Twin setting rejected: %s:%s=%s (%s)",
                            table,
                            key,
                            value_raw,
                            reason,
                        )
                        return
                    if table == "proxy_control" and self._proxy_control_handler is not None:
                        handled = self._proxy_control_handler(table, key, normalized)
                        if handled:
                            logger.info("Proxy control applied: %s:%s=%s", table, key, normalized)
                            return
                    self._twin_queue.enqueue(table, key, normalized)
                    logger.info("Twin setting enqueued: %s:%s=%s", table, key, normalized)
                    return

            # Parse JSON payload
            data = json.loads(raw_payload)
            if not isinstance(data, dict):
                logger.warning(
                    "TwinControlHandler: Invalid message format on %s: expected JSON object",
                    topic,
                )
                return

            # Extract required fields
            table = data.get("table")
            key = data.get("key")
            value = data.get("value")

            if table is None or key is None or value is None:
                logger.warning(
                    "TwinControlHandler: Invalid message format on %s: missing table/key/value",
                    topic,
                )
                return

            if not is_setting_allowed(table, key):
                logger.warning("Twin setting rejected: %s:%s not allowed", table, key)
                return

            ok, normalized, reason = validate_setting_value(table, key, value)
            if not ok:
                logger.warning(
                    "Twin setting rejected: %s:%s=%s (%s)",
                    table,
                    key,
                    value,
                    reason,
                )
                return

            if table == "proxy_control" and self._proxy_control_handler is not None:
                handled = self._proxy_control_handler(table, key, normalized)
                if handled:
                    logger.info("Proxy control applied: %s:%s=%s", table, key, normalized)
                    return

            # Enqueue the setting
            self._twin_queue.enqueue(table, key, normalized)
            logger.info("Twin setting enqueued: %s:%s=%s", table, key, normalized)

        except json.JSONDecodeError as exc:
            logger.warning(
                "TwinControlHandler: Failed to parse JSON on %s: %s",
                topic,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "TwinControlHandler: Error processing message on %s: %s",
                topic,
                exc,
            )
