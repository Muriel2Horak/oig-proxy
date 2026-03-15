"""MqttStateCache – MQTT state subscription and table/value caching for OIG Proxy.

Subscribes to oig_local/{device_id}/+/state, parses incoming payloads,
transforms MQTT-friendly values back to raw inverter values, and persists
selected tables via save_prms_state.
"""

# pylint: disable=too-many-instance-attributes,protected-access
# pylint: disable=missing-function-docstring,broad-exception-caught

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from config import MQTT_NAMESPACE
from control_pipeline import ControlPipeline
from utils import get_sensor_config, save_mode_state, save_prms_state

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)


class MqttStateCache:
    """Owns the MQTT state-cache subscription and value transformation."""

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy

        # Cache idempotency guard
        self.cache_device_id: str | None = None

        # In-memory caches
        self.last_values: dict[tuple[str, str], Any] = {}
        self.table_cache: dict[str, dict[str, Any]] = {}

    # -----------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------

    def setup(self) -> None:
        if self._proxy._loop is None:
            return
        device_id = (
            self._proxy.mqtt_publisher.device_id or self._proxy.device_id
        )
        if not device_id or device_id == "AUTO":
            return
        if self.cache_device_id == device_id:
            return
        topic = f"{MQTT_NAMESPACE}/{device_id}/+/state"

        def _handler(
                msg_topic: str,
                payload: bytes,
                _qos: int,
                retain: bool) -> None:
            if self._proxy._loop is None:
                return
            try:
                payload_text = payload.decode("utf-8", errors="strict")
            except Exception:
                payload_text = payload.decode("utf-8", errors="replace")
            self._proxy._loop.call_soon_threadsafe(
                asyncio.create_task,
                self.handle_message(
                    topic=msg_topic,
                    payload_text=payload_text,
                    retain=retain,
                ),
            )

        self._proxy.mqtt_publisher.add_message_handler(
            topic=topic,
            handler=_handler,
            qos=1,
        )
        self.cache_device_id = device_id
        logger.info("MQTT: Cache subscription enabled (%s)", topic)

    # -----------------------------------------------------------------
    # Topic / payload parsing
    # -----------------------------------------------------------------

    @staticmethod
    def parse_topic(topic: str) -> tuple[str | None, str | None]:
        parts = topic.split("/")
        if len(parts) != 4:
            return None, None
        namespace, device_id, table_name, suffix = parts
        if namespace != MQTT_NAMESPACE or suffix != "state":
            return None, None
        return device_id, table_name

    @staticmethod
    def parse_payload(payload_text: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(payload_text)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def validate_device(self, device_id: str) -> bool:
        target_device_id = (
            self._proxy.mqtt_publisher.device_id or self._proxy.device_id
        )
        if not target_device_id or target_device_id == "AUTO":
            return False
        return device_id == target_device_id

    @staticmethod
    def should_persist_table(table_name: str | None) -> bool:
        """Vrací True pro tabulky, které chceme perzistovat pro obnovu po restartu."""
        if not table_name or not table_name.startswith("tbl_"):
            return False
        # tbl_actual chodí typicky každých pár sekund → neperzistujeme
        if table_name == "tbl_actual":
            return False
        return True

    # -----------------------------------------------------------------
    # Value transformation
    # -----------------------------------------------------------------

    @staticmethod
    def to_raw_value(
        *, tbl_name: str, tbl_item: str, value: Any
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
        return ControlPipeline.coerce_value(value)

    def transform_values(
        self,
        payload: dict[str, Any],
        table_name: str,
    ) -> dict[str, Any]:
        raw_values: dict[str, Any] = {}
        for key, value in payload.items():
            if key.startswith("_"):
                continue
            raw_value = self.to_raw_value(
                tbl_name=table_name,
                tbl_item=key,
                value=value,
            )
            raw_values[key] = raw_value
            self.update_cached_value(
                tbl_name=table_name,
                tbl_item=key,
                raw_value=raw_value,
                update_mode=True,
            )
        return raw_values

    # -----------------------------------------------------------------
    # Caching and persistence
    # -----------------------------------------------------------------

    def update_cached_value(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        raw_value: Any,
        update_mode: bool,
    ) -> None:
        if not tbl_name or not tbl_item:
            return
        self.last_values[(tbl_name, tbl_item)] = raw_value
        table_cache = self.table_cache.setdefault(tbl_name, {})
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
        if mode_int == self._proxy._mp.mode_value:
            return

        self._proxy._mp.mode_value = mode_int
        resolved_device_id = (
            (self._proxy.device_id
             if self._proxy.device_id != "AUTO" else None)
            or self._proxy._mp.mode_device_id
            or self._proxy._mp.prms_device_id
        )
        if resolved_device_id:
            self._proxy._mp.mode_device_id = resolved_device_id
        save_mode_state(mode_int, resolved_device_id)

    async def persist_values(
        self,
        table_name: str,
        raw_values: dict[str, Any],
        device_id: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                save_prms_state, table_name, raw_values, device_id)
        except Exception as e:
            logger.debug(
                "STATE: snapshot update failed (%s): %s",
                table_name,
                e)
        existing = self._proxy._mp.prms_tables.get(table_name, {})
        merged: dict[str, Any] = {}
        if isinstance(existing, dict):
            merged.update(existing)
        merged.update(raw_values)
        self._proxy._mp.prms_tables[table_name] = merged
        self._proxy._mp.prms_device_id = device_id

    # -----------------------------------------------------------------
    # Main message handler
    # -----------------------------------------------------------------

    async def handle_message(
        self,
        *,
        topic: str,
        payload_text: str,
        retain: bool,
    ) -> None:
        _ = retain
        device_id, table_name = self.parse_topic(topic)
        if not device_id or not table_name:
            return
        if not self.validate_device(device_id):
            return
        self._proxy.mqtt_publisher.set_cached_payload(topic, payload_text)
        if not table_name.startswith("tbl_"):
            return
        payload = self.parse_payload(payload_text)
        if payload is None:
            return
        raw_values = self.transform_values(payload, table_name)
        if raw_values and self.should_persist_table(table_name):
            await self.persist_values(table_name, raw_values, device_id)
