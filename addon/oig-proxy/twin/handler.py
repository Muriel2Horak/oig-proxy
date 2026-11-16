"""Twin MQTT control handler for OIG Proxy v2.

Subscribes to control topics and enqueues settings for device twin.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from settings_constraints import PowerControlLimits, is_setting_allowed, validate_setting_value
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
        power_limits: PowerControlLimits = PowerControlLimits(),
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
        self._power_limits = power_limits
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
        self.admit_message(topic, payload)

    def admit_message(self, topic: str, payload: bytes, *, only_if_empty: bool = False) -> TwinSetting | None:
        """Validate exact target/path and delegate the value to the shared policy."""
        try:
            raw_payload = payload.decode("utf-8")
            parts = topic.split("/")
            compatibility = len(parts) == 5 and parts[0] == self._namespace and parts[2] == "set"
            envelope = len(parts) == 4 and parts[0] == "oig" and parts[2:] == ["control", "set"]
            if not compatibility and not envelope:
                logger.warning("Twin request rejected: invalid command topic: %s", topic)
                return None
            table: Any
            key: Any
            value: Any
            if compatibility:
                table, key, value = parts[3], parts[4], raw_payload
            else:
                data = json.loads(raw_payload, parse_float=Decimal)
                if not isinstance(data, dict):
                    logger.warning("TwinControlHandler: Invalid message format: expected JSON object")
                    return None
                table, key, value = data.get("table"), data.get("key"), data.get("value")
            if not isinstance(table, str) or not isinstance(key, str) or not table or not key or value is None:
                logger.warning("TwinControlHandler: Invalid message format: missing table/key/value")
                return None
            if not self._box_writes_enabled and table != "proxy_control":
                logger.warning("Twin request rejected: BOX writes are disabled")
                return None
            expected = self._proxy_device_id if table == "proxy_control" else self._device_id
            allowed_targets = {expected}
            if (table == "proxy_control" and self._box_writes_enabled
                    and self._device_id and self._device_id != "unknown"):
                allowed_targets.add(self._device_id)  # Preserve own-BOX JSON control compatibility.
            if not expected or expected == "unknown" or parts[1] not in allowed_targets:
                logger.warning("Twin request rejected: command target %s does not match configured device", parts[1])
                return None
            logger.info("Twin MQTT message: topic=%s", topic)
            incoming_record = make_incoming_record(
                device_id=expected, table=table, key=key, raw_text=raw_payload, value=value,
            )
            if not is_setting_allowed(table, key, power_limits=self._power_limits):
                self._record_setting_audit(incoming_record)
                self._record_setting_audit(make_step_record(
                    incoming_record, SettingStep.REJECTED_NOT_ALLOWED,
                    SettingResult.REJECTED, raw_text=raw_payload,
                ))
                logger.warning("Twin setting rejected: %s:%s not allowed", table, key)
                return None
            ok, normalized, reason = validate_setting_value(table, key, value, power_limits=self._power_limits)
            if not ok:
                self._record_setting_audit(incoming_record)
                self._record_setting_audit(make_step_record(
                    incoming_record, SettingStep.REJECTED_VALIDATION,
                    SettingResult.REJECTED, raw_text=raw_payload,
                ))
                logger.warning("Twin setting rejected: %s:%s=%s (%s)", table, key, value, reason)
                return None
            if table == "proxy_control":
                self._record_setting_audit(incoming_record)
                if self._proxy_control_handler is not None and self._proxy_control_handler(table, key, normalized):
                    logger.info("Proxy control applied: %s:%s=%s", table, key, normalized)
                else:
                    logger.warning("Proxy control rejected: local handler unavailable or refused")
                return None  # Proxy-local controls never enter the BOX queue.
            previous = None if only_if_empty else self._twin_queue.get(table, key, device_id=expected)
            if previous is not None and self._twin_queue.is_sent(previous):
                previous = None
            setting, duplicate = self._twin_queue.admit(
                table, key, normalized, audit_id=incoming_record.audit_id,
                raw_text=incoming_record.raw_text, device_id=expected,
                require_profile=self._require_profile, only_if_empty=only_if_empty,
            )
            if duplicate:
                return setting
            self._record_setting_audit(incoming_record)
            if setting is None:
                self._record_setting_audit(make_step_record(
                    incoming_record, SettingStep.REJECTED_VALIDATION, SettingResult.REJECTED,
                ))
                logger.warning("Twin setting rejected: profile unavailable or a command is queued for %s", expected)
                return None
            if previous is not None and previous.audit_id:
                self._record_setting_audit(make_step_record(
                    self._make_pending_setting_record(previous),
                    SettingStep.SUPERSEDED, SettingResult.SUPERSEDED,
                ))
            self._record_setting_audit(make_step_record(
                incoming_record, SettingStep.ENQUEUED,
                msg_id=setting.msg_id,
                id_set=setting.id_set,
            ))
            logger.info("Twin setting enqueued: %s:%s=%s", table, key, normalized)
            return setting
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("TwinControlHandler: Failed to parse JSON or UTF-8 on %s: %s", topic, exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("TwinControlHandler: error processing %s: %s", topic, exc)

        return None
