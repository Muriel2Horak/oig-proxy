"""Retain-aware, exact-device MQTT ingress for durable local Settings."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any, Callable
import uuid

from mqtt.client import MQTTClient
from protocol.frames import is_xml_1_0_text
from settings_constraints import is_setting_allowed, validate_setting_value
from telemetry.settings_audit import SettingsAuditPublisher
from twin.state import ControlIngress, IngressDisposition
from twin.store import TwinCommandStore

logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 16_384
_MAX_TOPIC_CHARS = 1_024
_UNSAFE_TOPIC_SEGMENT = frozenset("/+\x00#")

ProxyControlHandler = Callable[[str, str, str], bool]


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _safe_segment(value: object) -> bool:
    return bool(
        type(value) is str
        and value
        and len(value) <= 128
        and not any(char in value for char in _UNSAFE_TOPIC_SEGMENT)
        and is_xml_1_0_text(value)
    )


def _bounded_raw_text(payload: bytes) -> str:
    raw = bytes(payload[:_MAX_PAYLOAD_BYTES]).decode(
        "utf-8",
        errors="backslashreplace",
    )
    return raw[:_MAX_PAYLOAD_BYTES]


class TwinControlHandler:  # pylint: disable=too-many-instance-attributes
    """Validate and durably audit one exact device's MQTT control ingress."""

    def __init__(
        self,
        *,
        mqtt: MQTTClient,
        store: TwinCommandStore,
        device_id: str,
        control_enabled: bool,
        loop: asyncio.AbstractEventLoop,
        namespace: str = "oig_local",
        proxy_control_handler: ProxyControlHandler | None = None,
        audit_publisher: SettingsAuditPublisher | None = None,
    ) -> None:
        self._mqtt = mqtt
        self._store = store
        self._device_id = device_id
        self._control_enabled = bool(control_enabled)
        self._loop = loop
        self._namespace = namespace
        self._proxy_control_handler = proxy_control_handler
        self._audit_publisher = audit_publisher
        self._topic = f"oig/{device_id}/control/set"
        self._topic_compat = f"{namespace}/{device_id}/set/#"
        self._subscribed = False
        self._accepting_callbacks = False
        self._tasks: set[asyncio.Task[None]] = set()
        self.store_failure_count = 0

    async def start(self) -> bool:
        """Register only exact bound-device topics, with rollback on failure."""
        if self._subscribed:
            return True
        if (
            not self._control_enabled
            or not _safe_segment(self._device_id)
            or not _safe_segment(self._namespace)
            or not self._mqtt.is_ready()
        ):
            return False
        if not self._mqtt.subscribe(self._topic, self._on_message):
            return False
        if not self._mqtt.subscribe(self._topic_compat, self._on_message):
            self._mqtt.unsubscribe(self._topic)
            return False
        self._subscribed = True
        self._accepting_callbacks = True
        logger.info(
            "Twin control subscribed for exact device %s",
            self._device_id,
        )
        return True

    async def stop(self) -> None:
        """Unregister both topics and drain all application-loop handlers."""
        self._accepting_callbacks = False
        if self._subscribed:
            self._mqtt.unsubscribe(self._topic)
            self._mqtt.unsubscribe(self._topic_compat)
            self._subscribed = False
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def _on_message(self, topic: str, payload: bytes, retain: bool) -> None:
        """Move Paho-thread input onto the owning asyncio loop."""
        if not self._accepting_callbacks:
            return
        received_at_ms = time.time_ns() // 1_000_000
        self._loop.call_soon_threadsafe(
            self._spawn_message_task,
            str(topic),
            bytes(payload),
            bool(retain),
            received_at_ms,
        )

    def _spawn_message_task(
        self,
        topic: str,
        payload: bytes,
        retain: bool,
        received_at_ms: int,
    ) -> None:
        if not self._accepting_callbacks:
            return
        task = self._loop.create_task(
            self.handle_message(
                topic,
                payload,
                retain,
                received_at_ms=received_at_ms,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._message_task_done)

    def _message_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Twin control handler task failed: %s",
                error,
            )

    async def handle_message(  # pylint: disable=too-many-branches,too-many-return-statements
        self,
        topic: str,
        payload: bytes,
        retain: bool,
        *,
        received_at_ms: int,
    ) -> None:
        """Apply bounded validation order before any command transition."""
        safe_topic = str(topic)[:_MAX_TOPIC_CHARS]
        payload_bytes = bytes(payload)
        topic_device_id = self._topic_device_id(str(topic))
        ingress = ControlIngress(
            str(uuid.uuid4()),
            received_at_ms,
            safe_topic,
            topic_device_id,
            bool(retain),
            _bounded_raw_text(payload_bytes),
        )

        if not self._control_enabled:
            self._reject(
                ingress,
                IngressDisposition.REJECTED_DISABLED,
                "local control is disabled",
            )
            return
        if retain:
            self._reject(
                ingress,
                IngressDisposition.REJECTED_RETAINED,
                "retained control is not executable",
            )
            return
        if not _safe_segment(self._device_id):
            self._reject(
                ingress,
                IngressDisposition.REJECTED_UNKNOWN_DEVICE,
                "bound device is unavailable or unsafe",
            )
            return

        topic_kind, topic_disposition = self._classify_topic(str(topic))
        if topic_disposition is not None:
            self._reject(
                ingress,
                topic_disposition,
                "topic does not match the exact bound device",
            )
            return
        if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
            self._reject(
                ingress,
                IngressDisposition.REJECTED_OVERSIZE,
                "payload exceeds 16384 bytes",
            )
            return
        try:
            text = payload_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self._reject(
                ingress,
                IngressDisposition.REJECTED_UTF8,
                "payload is not strict UTF-8",
            )
            return

        if topic_kind == "json":
            parsed = self._parse_json_ingress(ingress, text)
            if parsed is None:
                return
            table, key, value = parsed
        else:
            parts = str(topic).split("/")
            table, key, value = parts[3], parts[4], text

        if not is_setting_allowed(table, key):
            self._reject(
                ingress,
                IngressDisposition.REJECTED_NOT_ALLOWED,
                "setting target is not allowlisted",
            )
            return
        if not all(is_xml_1_0_text(item) for item in (table, key)) or (
            type(value) is str and not is_xml_1_0_text(value)
        ):
            self._reject(
                ingress,
                IngressDisposition.REJECTED_XML,
                "dynamic setting text is not valid XML 1.0",
            )
            return
        validation = validate_setting_value(table, key, value)
        if not validation.accepted or validation.value_text is None:
            self._reject(
                ingress,
                IngressDisposition.REJECTED_VALUE,
                validation.reason or "setting value is invalid",
            )
            return
        value_text = validation.value_text
        if not all(
            is_xml_1_0_text(item)
            for item in (self._device_id, table, key, value_text)
        ):
            self._reject(
                ingress,
                IngressDisposition.REJECTED_XML,
                "dynamic setting text is not valid XML 1.0",
            )
            return

        if table == "proxy_control":
            try:
                self._store.record_proxy_control_ingress(
                    ingress,
                    reason="accepted proxy control",
                )
            except Exception as error:  # noqa: BLE001
                self._record_store_failure(error)
                return
            if self._proxy_control_handler is not None:
                try:
                    self._proxy_control_handler(table, key, value_text)
                except Exception as error:  # noqa: BLE001
                    logger.error("Proxy control dispatch failed: %s", error)
            return

        try:
            result = self._store.enqueue_command(
                ingress,
                device_id=self._device_id,
                table_name=table,
                item_name=key,
                value_text=value_text,
            )
        except Exception as error:  # noqa: BLE001
            self._record_store_failure(error)
            try:
                self._store.record_ingress_disposition(
                    ingress,
                    disposition=IngressDisposition.REJECTED_STORE,
                    reason="durable command enqueue failed",
                )
            except Exception as audit_error:  # noqa: BLE001
                self._record_store_failure(audit_error)
            return
        if self._audit_publisher is not None:
            for snapshot in sorted(
                result.snapshots,
                key=lambda item: item.transition.transition_id,
            ):
                await self._audit_publisher.publish_committed_async(snapshot)

    def _parse_json_ingress(
        self,
        ingress: ControlIngress,
        text: str,
    ) -> tuple[str, str, Any] | None:
        try:
            data = json.loads(
                text,
                parse_int=Decimal,
                parse_float=Decimal,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            self._reject(
                ingress,
                IngressDisposition.REJECTED_JSON,
                "payload is not strict JSON",
            )
            return None
        if not isinstance(data, dict):
            self._reject(
                ingress,
                IngressDisposition.REJECTED_SCHEMA,
                "JSON payload must be an object",
            )
            return None
        table = data.get("table")
        key = data.get("key")
        value = data.get("value")
        supplied_device = data.get("device_id")
        if (
            type(table) is not str
            or type(key) is not str
            or value is None
            or (
                supplied_device is not None
                and type(supplied_device) is not str
            )
        ):
            self._reject(
                ingress,
                IngressDisposition.REJECTED_SCHEMA,
                "JSON table/key/value/device_id schema is invalid",
            )
            return None
        if supplied_device is not None and supplied_device != self._device_id:
            self._reject(
                ingress,
                IngressDisposition.REJECTED_DEVICE_MISMATCH,
                "payload device does not match bound device",
            )
            return None
        return table, key, value

    def _classify_topic(
        self,
        topic: str,
    ) -> tuple[str | None, IngressDisposition | None]:
        if topic == self._topic:
            return "json", None
        parts = topic.split("/")
        if len(parts) == 4 and parts[:1] == ["oig"] and parts[2:] == ["control", "set"]:
            return None, IngressDisposition.REJECTED_DEVICE_MISMATCH
        if len(parts) >= 3 and parts[0] == self._namespace and parts[2] == "set":
            if parts[1] != self._device_id:
                return None, IngressDisposition.REJECTED_DEVICE_MISMATCH
            if len(parts) != 5:
                return None, IngressDisposition.REJECTED_TOPIC
            return "compat", None
        return None, IngressDisposition.REJECTED_TOPIC

    @staticmethod
    def _topic_device_id(topic: str) -> str | None:
        parts = topic.split("/")
        if len(parts) < 2 or not _safe_segment(parts[1]):
            return None
        return parts[1]

    def _reject(
        self,
        ingress: ControlIngress,
        disposition: IngressDisposition,
        reason: str,
    ) -> None:
        try:
            self._store.record_ingress_disposition(
                ingress,
                disposition=disposition,
                reason=reason[:1_024],
            )
        except Exception as error:  # noqa: BLE001
            self._record_store_failure(error)

    def _record_store_failure(self, error: BaseException) -> None:
        self.store_failure_count += 1
        logger.error(
            "Twin control durable ingress failure (%s)",
            type(error).__name__,
        )
