"""Twin MQTT control handler for OIG Proxy v2.

Subscribes to control topics and enqueues settings for device twin.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from settings_constraints import is_setting_allowed, validate_setting_value
from telemetry.settings_audit import (
    SettingResult,
    SettingStep,
    SettingsAuditRecord,
    make_incoming_record,
    make_step_record,
)

if TYPE_CHECKING:
    from telemetry.collector import TelemetryCollector

    from .state import TwinQueue
    from .state import TwinSetting

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
        telemetry_collector: TelemetryCollector | None = None,
        proxy_device_id: str = "oig_proxy",
        require_profile: bool = False,
        box_writes_enabled: bool = True,
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
        self._proxy_device_id = proxy_device_id
        self._require_profile = require_profile
        self._namespace = namespace
        self._box_writes_enabled = box_writes_enabled
        if box_writes_enabled:
            self._topic = "oig/+/control/set"
            self._topic_compat = f"{namespace}/+/set/#"
        else:
            self._topic = f"oig/{proxy_device_id}/control/set"
            self._topic_compat = f"{namespace}/{proxy_device_id}/set/proxy_control/PROXY_MODE"
        self._subscribed = False
        self._proxy_control_handler = proxy_control_handler
        self._telemetry_collector = telemetry_collector

    def _record_setting_audit(self, record: SettingsAuditRecord) -> None:
        if self._telemetry_collector is None:
            return
        self._telemetry_collector.record_setting_audit_step(record)

    def _make_pending_setting_record(self, setting: TwinSetting) -> SettingsAuditRecord:
        record = make_incoming_record(
            device_id=self._device_id,
            table=setting.table,
            key=setting.key,
            raw_text=setting.raw_text,
            value=setting.value,
            msg_id=setting.msg_id,
            id_set=setting.id_set,
        )
        record.audit_id = setting.audit_id
        return record

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

    def set_device_id(self, device_id: str) -> None:
        """Bind fresh BOX controls after the first validated device observation."""
        if device_id and device_id != "unknown":
            self._device_id = device_id

    def _on_message(self, topic: str, payload: bytes) -> None:
        """Validate exact target/path and delegate the value to the shared policy."""
        try:
            raw_payload = payload.decode("utf-8")
            parts = topic.split("/")
            compatibility = len(parts) == 5 and parts[0] == self._namespace and parts[2] == "set"
            envelope = len(parts) == 4 and parts[0] == "oig" and parts[2:] == ["control", "set"]
            if not compatibility and not envelope:
                logger.warning("Twin request rejected: invalid command topic: %s", topic)
                return
            table: Any
            key: Any
            value: Any
            if compatibility:
                table, key, value = parts[3], parts[4], raw_payload
            else:
                data = json.loads(raw_payload, parse_float=Decimal)
                if not isinstance(data, dict):
                    logger.warning("TwinControlHandler: Invalid message format: expected JSON object")
                    return
                table, key, value = data.get("table"), data.get("key"), data.get("value")
            if not isinstance(table, str) or not isinstance(key, str) or not table or not key or value is None:
                logger.warning("TwinControlHandler: Invalid message format: missing table/key/value")
                return
            if not self._box_writes_enabled and table != "proxy_control":
                logger.warning("Twin request rejected: BOX writes are disabled")
                return
            expected = self._proxy_device_id if table == "proxy_control" else self._device_id
            allowed_targets = {expected}
            if (table == "proxy_control" and self._box_writes_enabled
                    and self._device_id and self._device_id != "unknown"):
                allowed_targets.add(self._device_id)  # Preserve own-BOX JSON control compatibility.
            if not expected or expected == "unknown" or parts[1] not in allowed_targets:
                logger.warning("Twin request rejected: command target %s does not match configured device", parts[1])
                return
            logger.info("Twin MQTT message: topic=%s", topic)
            incoming_record = make_incoming_record(
                device_id=expected, table=table, key=key, raw_text=raw_payload, value=value,
            )
            self._record_setting_audit(incoming_record)
            if not is_setting_allowed(table, key):
                self._record_setting_audit(make_step_record(
                    incoming_record, SettingStep.REJECTED_NOT_ALLOWED,
                    SettingResult.REJECTED, raw_text=raw_payload,
                ))
                logger.warning("Twin setting rejected: %s:%s not allowed", table, key)
                return
            ok, normalized, reason = validate_setting_value(table, key, value)
            if not ok:
                self._record_setting_audit(make_step_record(
                    incoming_record, SettingStep.REJECTED_VALIDATION,
                    SettingResult.REJECTED, raw_text=raw_payload,
                ))
                logger.warning("Twin setting rejected: %s:%s=%s (%s)", table, key, value, reason)
                return
            if table == "proxy_control":
                if self._proxy_control_handler is not None and self._proxy_control_handler(table, key, normalized):
                    logger.info("Proxy control applied: %s:%s=%s", table, key, normalized)
                else:
                    logger.warning("Proxy control rejected: local handler unavailable or refused")
                return  # Proxy-local controls never enter the BOX queue.
            previous = self._twin_queue.get(table, key, device_id=expected)
            if (previous is not None and previous.audit_id
                    and not self._twin_queue.is_sent(previous)):
                self._record_setting_audit(make_step_record(
                    self._make_pending_setting_record(previous),
                    SettingStep.SUPERSEDED, SettingResult.SUPERSEDED,
                ))
            setting = self._twin_queue.enqueue(
                table, key, normalized, audit_id=incoming_record.audit_id,
                raw_text=incoming_record.raw_text, device_id=expected,
                require_profile=self._require_profile,
            )
            if setting is None:
                self._record_setting_audit(make_step_record(
                    incoming_record, SettingStep.REJECTED_VALIDATION, SettingResult.REJECTED,
                ))
                logger.warning("Twin setting rejected: no qualified profile for %s", expected)
                return
            self._record_setting_audit(make_step_record(
                incoming_record, SettingStep.ENQUEUED,
                msg_id=setting.msg_id,
                id_set=setting.id_set,
            ))
            logger.info("Twin setting enqueued: %s:%s=%s", table, key, normalized)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("TwinControlHandler: Failed to parse JSON or UTF-8 on %s: %s", topic, exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("TwinControlHandler: error processing %s: %s", topic, exc)
