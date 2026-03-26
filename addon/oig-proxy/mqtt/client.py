#!/usr/bin/env python3
"""
MQTT Client pro OIG Proxy v2.

Paho-mqtt wrapper s:
- auto-reconnect
- HA MQTT discovery
- publish state topics
- LWT (Last Will Testament) pro availability
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from settings_constraints import CONTROL_WRITE_WHITELIST, SETTING_CONSTRAINTS, SettingConstraint

logger = logging.getLogger(__name__)

DEVICE_NAMES: dict[str, str] = {
    "inverter": "Střídač",
    "battery": "Baterie",
    "boiler": "Bojler",
    "recuper": "Rekuperace",
    "heat_pump": "Tepelné čerpadlo",
    "aircon": "Klimatizace",
    "wl_charge": "Wallbox",
    "box": "OIG Box",
    "pv": "FVE",
    "grid": "Síť",
    "load": "Spotřeba",
    "proxy": "Diagnostika OIG",
}

try:
    import paho.mqtt.client as _paho_mqtt  # pyright: ignore[reportMissingImports]
    PAHO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _paho_mqtt = None  # type: ignore[assignment]
    PAHO_AVAILABLE = False


class MQTTClient:
    """
    Async-friendly paho-mqtt wrapper.

    connect() je synchronní (blokuje max timeout sekund).
    publish_*() metody jsou neblokující (paho loop_start).
    health_check_loop() je asyncio korutina.
    """

    CONNECT_TIMEOUT = 5.0
    HEALTH_CHECK_INTERVAL = 30.0

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        namespace: str = "oig_local",
        qos: int = 1,
        state_retain: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.namespace = namespace
        self.qos = qos
        self.state_retain = state_retain

        self._client: Any | None = None
        self.connected = False
        self._discovery_sent: set[str] = set()
        self._availability_online_sent: set[str] = set()

        # Statistiky
        self.publish_count = 0
        self.publish_success = 0
        self.publish_failed = 0

        # Subscriptions
        self._subscriptions: dict[str, Callable[[str, bytes], None]] = {}
        self._connect_device_id: str = "unknown"

    # ------------------------------------------------------------------
    # Připojení
    # ------------------------------------------------------------------

    def connect(self, device_id: str, timeout: float | None = None) -> bool:
        """Připojí se k MQTT brokeru. Vrátí True při úspěchu."""
        if not PAHO_AVAILABLE:
            logger.error("paho-mqtt není nainstalováno")
            return False

        timeout = timeout or self.CONNECT_TIMEOUT
        self._connect_device_id = device_id
        client = self._create_client(device_id)
        if client is None:
            return False

        self._client = client
        try:
            client.connect(self.host, self.port, keepalive=60)
            client.loop_start()
        except OSError as exc:
            logger.error("MQTT: Připojení selhalo: %s", exc)
            return False

        # Čekáme na on_connect callback
        deadline = time.monotonic() + timeout
        while not self.connected and time.monotonic() < deadline:
            time.sleep(0.05)

        if not self.connected:
            logger.error("MQTT: Timeout připojení po %.1fs", timeout)
            self._cleanup()
            return False

        logger.info("MQTT: ✅ Připojeno k %s:%s", self.host, self.port)
        return True

    def disconnect(self) -> None:
        """Odpojí se od brokeru."""
        self._cleanup()

    def _create_client(self, device_id: str) -> Any | None:
        if _paho_mqtt is None:
            return None

        client_id = f"{self.namespace}_{device_id}_v2"
        kwargs: dict[str, Any] = {
            "client_id": client_id,
            "protocol": getattr(_paho_mqtt, "MQTTv311", 4),
        }
        callback_api = getattr(_paho_mqtt, "CallbackAPIVersion", None)
        if callback_api is not None:
            kwargs["callback_api_version"] = callback_api.VERSION1

        client = _paho_mqtt.Client(**kwargs)  # type: ignore[call-arg]

        if self.username:
            client.username_pw_set(self.username, self.password)

        # LWT – availability offline při výpadku
        lwt_topic = f"{self.namespace}/{device_id}/availability"
        client.will_set(lwt_topic, "offline", retain=True, qos=1)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        return client

    def _cleanup(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        self.connected = False

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, rc: int) -> None:
        if rc == 0:
            self.connected = True
            self._discovery_sent.clear()
            self._availability_online_sent.clear()
            device_id = getattr(client, "_oig_device_id", self._connect_device_id)
            avail_topic = f"{self.namespace}/{device_id}/availability"
            client.publish(avail_topic, "online", retain=True, qos=1)
            self._availability_online_sent.add(device_id)
            logger.info("MQTT: Připojeno (rc=0)")
        else:
            self.connected = False
            logger.error("MQTT: Odmítnuto (rc=%s)", rc)

    def _on_disconnect(self, _client: Any, _userdata: Any, rc: int) -> None:
        self.connected = False
        if rc != 0:
            logger.warning("MQTT: Neočekávané odpojení (rc=%s)", rc)

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._client is not None and self.connected

    def publish_state(self, device_id: str, table: str, data: dict[str, Any]) -> bool:
        """
        Publikuje data jako JSON na state topic.

        Topic: {namespace}/{device_id}/{table}/state
        """
        if not self.is_ready():
            self.publish_failed += 1
            return False

        topic = f"{self.namespace}/{device_id}/{table}/state"
        payload = json.dumps(data)

        self.publish_count += 1
        try:
            client = self._client
            if client is None:
                self.publish_failed += 1
                return False
            if device_id not in self._availability_online_sent:
                avail_topic = f"{self.namespace}/{device_id}/availability"
                try:
                    avail_result = client.publish(avail_topic, "online", retain=True, qos=1)
                    if getattr(avail_result, "rc", 1) == 0:
                        self._availability_online_sent.add(device_id)
                except Exception:  # noqa: BLE001
                    pass
            result = client.publish(
                topic, payload, qos=self.qos, retain=self.state_retain
            )
            if result.rc == 0:
                self.publish_success += 1
                logger.debug("MQTT: → %s (%d keys)", topic, len(data))
                return True
            self.publish_failed += 1
            logger.error("MQTT: publish rc=%s", result.rc)
            return False
        except Exception as exc:  # noqa: BLE001
            self.publish_failed += 1
            logger.error("MQTT: publish exception: %s", exc)
            return False

    # ------------------------------------------------------------------
    # HA Discovery
    # ------------------------------------------------------------------

    def send_discovery(
        self,
        *,
        device_id: str,
        table: str,
        sensor_key: str,
        sensor_name: str,
        unit: str = "",
        device_class: str = "",
        state_class: str = "",
        icon: str = "",
        device_mapping: str = "",
        entity_category: str = "",
        is_binary: bool = False,
        enum_map: dict[str, str] | None = None,
    ) -> bool:
        """
        Pošle HA MQTT discovery config pro jeden sensor.

        Topic: homeassistant/sensor/{unique_id}/config
        """
        if not self.is_ready():
            return False

        safe_key = sensor_key.lower()
        unique_id = f"{self.namespace}_{device_id}_{table}_{safe_key}".lower()
        object_id = self._build_object_id(device_id, table, safe_key)
        if unique_id in self._discovery_sent:
            return True  # Už odesláno

        state_topic = f"{self.namespace}/{device_id}/{table}/state"
        availability_topic = f"{self.namespace}/{device_id}/availability"
        mapped_device = device_mapping or "inverter"
        device_identifier = f"{self.namespace}_{device_id}_{mapped_device}"
        device_label = DEVICE_NAMES.get(mapped_device, DEVICE_NAMES["inverter"])
        is_setting = table in CONTROL_WRITE_WHITELIST and sensor_key in CONTROL_WRITE_WHITELIST[table]
        component = "binary_sensor" if is_binary else "sensor"

        value_template = f"{{{{ value_json.get('{sensor_key}') }}}}"
        if enum_map:
            enum_json = json.dumps(enum_map, ensure_ascii=False)
            value_template = (
                f"{{{{ ({enum_json}).get((value_json.get('{sensor_key}') | string), "
                f"value_json.get('{sensor_key}')) }}}}"
            )

        payload: dict[str, Any] = {
            "name": sensor_name,
            "object_id": object_id,
            "default_entity_id": f"{component}.{object_id}",
            "unique_id": unique_id,
            "state_topic": state_topic,
            "value_template": value_template,
            "availability": [{"topic": availability_topic}],
            "force_update": True,
            "device": {
                "identifiers": [device_identifier],
                "name": f"{device_label} ({device_id})",
                "manufacturer": "OIG Power",
                "model": f"OIG BatteryBox - {device_label}",
            },
        }

        if mapped_device not in ("inverter", "proxy"):
            payload["device"]["via_device"] = f"{self.namespace}_{device_id}_inverter"

        if unit:
            payload["unit_of_measurement"] = unit
        if device_class:
            payload["device_class"] = device_class
        if state_class:
            payload["state_class"] = state_class
        if icon:
            payload["icon"] = icon
        if entity_category:
            payload["entity_category"] = entity_category
        if is_binary:
            payload["payload_on"] = 1
            payload["payload_off"] = 0

        discovery_topic = f"homeassistant/{component}/{unique_id}/config"

        try:
            client = self._client
            if client is None:
                return False
            if is_setting:
                control_unique_id = f"{unique_id}_cfg"
                dual_publish_sensor = table == "tbl_box_prms" and sensor_key == "MODE"

                if control_unique_id in self._discovery_sent and (
                    not dual_publish_sensor or unique_id in self._discovery_sent
                ):
                    return True

                if dual_publish_sensor and unique_id not in self._discovery_sent:
                    sensor_result = client.publish(
                        discovery_topic,
                        json.dumps(payload),
                        retain=True,
                        qos=1,
                    )
                    if sensor_result.rc == 0:
                        self._discovery_sent.add(unique_id)
                        logger.debug("MQTT: Discovery → %s", discovery_topic)
                    else:
                        return False

                constraint = SETTING_CONSTRAINTS.get((table, sensor_key))
                binary_control = is_binary or self._is_binary_control_constraint(constraint)
                if self._is_select_control(table, sensor_key, enum_map):
                    control_component = "select"
                else:
                    control_component = "switch" if binary_control else "number"
                control_object_id = self._build_object_id(device_id, table, safe_key, is_control=True)
                control_payload: dict[str, Any] = {
                    "name": sensor_name,
                    "object_id": control_object_id,
                    "default_entity_id": f"{control_component}.{control_object_id}",
                    "unique_id": control_unique_id,
                    "state_topic": state_topic,
                    "value_template": f"{{{{ value_json.get('{sensor_key}') }}}}",
                    "command_topic": f"{self.namespace}/{device_id}/set/{table}/{sensor_key}",
                    "availability": [{"topic": availability_topic}],
                    "device": payload["device"],
                    "entity_category": "config",
                }

                if control_component == "number":
                    control_payload["mode"] = "box"
                    if unit:
                        control_payload["unit_of_measurement"] = unit
                    if device_class:
                        control_payload["device_class"] = device_class
                    if constraint is not None:
                        if constraint.min_value is not None:
                            control_payload["min"] = constraint.min_value
                        if constraint.max_value is not None:
                            control_payload["max"] = constraint.max_value
                        if constraint.step is not None:
                            control_payload["step"] = constraint.step
                elif control_component == "switch":
                    control_payload["payload_on"] = 1
                    control_payload["payload_off"] = 0
                    control_payload["state_on"] = 1
                    control_payload["state_off"] = 0
                else:
                    ordered_items = self._ordered_enum_items(enum_map)
                    state_map = {raw: label for raw, label in ordered_items}
                    command_map = {label: raw for raw, label in ordered_items}
                    control_payload["options"] = [label for _raw, label in ordered_items]
                    control_payload["value_template"] = (
                        f"{{{{ ({json.dumps(state_map, ensure_ascii=False)}).get((value_json.get('{sensor_key}') | string), "
                        f"value_json.get('{sensor_key}') | string) }}}}"
                    )
                    control_payload["command_template"] = (
                        f"{{{{ ({json.dumps(command_map, ensure_ascii=False)}).get(value, value) }}}}"
                    )

                control_topic = f"homeassistant/{control_component}/{control_unique_id}/config"
                control_result = client.publish(
                    control_topic,
                    json.dumps(control_payload),
                    retain=True,
                    qos=1,
                )
                if control_result.rc == 0:
                    self._discovery_sent.add(control_unique_id)
                    logger.debug("MQTT: Discovery → %s", control_topic)
                    return True
                return False

            result = client.publish(
                discovery_topic, json.dumps(payload), retain=True, qos=1
            )
            if result.rc == 0:
                self._discovery_sent.add(unique_id)
                logger.debug("MQTT: Discovery → %s", discovery_topic)
                return True
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("MQTT: discovery exception: %s", exc)
            return False

    @staticmethod
    def _build_object_id(device_id: str, table: str, safe_key: str, is_control: bool = False) -> str:
        """Build deterministic HA object_id for stable entity_id naming."""
        base = f"oig_local_{device_id}_{table}_{safe_key}".lower()
        base = re.sub(r"[^a-z0-9_]+", "_", base)
        base = re.sub(r"_+", "_", base).strip("_")
        if is_control:
            return f"{base}_cfg"
        return base

    @staticmethod
    def _is_binary_control_constraint(constraint: SettingConstraint | None) -> bool:
        if constraint is None:
            return False
        return (
            constraint.integer_only
            and constraint.min_value == 0
            and constraint.max_value == 1
            and (constraint.step is None or constraint.step == 1)
        )

    @staticmethod
    def _ordered_enum_items(enum_map: dict[str, str] | None) -> list[tuple[str, str]]:
        if not enum_map:
            return []
        try:
            return sorted(enum_map.items(), key=lambda item: int(item[0]))
        except (TypeError, ValueError):
            return sorted(enum_map.items(), key=lambda item: item[0])

    @staticmethod
    def _is_select_control(table: str, sensor_key: str, enum_map: dict[str, str] | None) -> bool:
        if not enum_map:
            return False
        return (table, sensor_key) in {
            ("proxy_control", "PROXY_MODE"),
            ("tbl_box_prms", "MODE"),
        }

    def send_discovery_for_table(
        self, device_id: str, table: str, data: dict[str, Any]
    ) -> None:
        """
        Odešle discovery pro všechny klíče z parsovaných dat.

        Ignoruje klíče začínající _ (interní metadata).
        """
        for key, value in data.items():
            if key.startswith("_"):
                continue
            self.send_discovery(
                device_id=device_id,
                table=table,
                sensor_key=key,
                sensor_name=key,
                # Základní typ inference
                unit="" if isinstance(value, str) else "",
            )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check_loop(self, device_id: str) -> None:
        """Asyncio korutina – periodicky reconnectuje pokud je odpojeno."""
        import asyncio

        logger.info(
            "MQTT: Health check spuštěn (interval %.0fs)",
            self.HEALTH_CHECK_INTERVAL,
        )
        while True:
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
            if not self.connected:
                logger.warning("MQTT: Reconnect...")
                if self.connect(device_id):
                    logger.info("MQTT: ✅ Reconnect úspěšný")
                else:
                    logger.warning("MQTT: ❌ Reconnect selhal")

    # ------------------------------------------------------------------
    # Subscribe / Unsubscribe
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, callback: Callable[[str, bytes], None]) -> bool:
        """Subscribe to an MQTT topic.

        Args:
            topic: The topic to subscribe to
            callback: Function to call when message received (topic: str, payload: bytes)

        Returns:
            True if subscription was successful
        """
        if not self.is_ready():
            logger.error("MQTT: Cannot subscribe, not connected")
            return False

        self._subscriptions[topic] = callback

        try:
            client = self._client
            if client is None:
                return False
            result, _mid = client.subscribe(topic, qos=self.qos)
            if result == 0:
                logger.debug("MQTT: Subscribed to %s", topic)
                return True
            logger.error("MQTT: Subscribe failed for %s (rc=%s)", topic, result)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("MQTT: Subscribe exception for %s: %s", topic, exc)
            return False

    def unsubscribe(self, topic: str) -> bool:
        """Unsubscribe from an MQTT topic.

        Args:
            topic: The topic to unsubscribe from

        Returns:
            True if unsubscription was successful
        """
        if not self.is_ready():
            return False

        if topic in self._subscriptions:
            del self._subscriptions[topic]

        try:
            client = self._client
            if client is None:
                return False
            result, _mid = client.unsubscribe(topic)
            if result == 0:
                logger.debug("MQTT: Unsubscribed from %s", topic)
                return True
            logger.error("MQTT: Unsubscribe failed for %s (rc=%s)", topic, result)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("MQTT: Unsubscribe exception for %s: %s", topic, exc)
            return False

    def _on_message(self, client: Any, _userdata: Any, msg: Any) -> None:
        """Internal callback for incoming MQTT messages.

        Routes messages to registered callbacks.
        """
        topic = msg.topic
        payload = msg.payload

        # Find matching callback
        for sub_topic, callback in self._subscriptions.items():
            if self._topic_matches(sub_topic, topic):
                try:
                    callback(topic, payload)
                except Exception as exc:  # noqa: BLE001
                    logger.error("MQTT: Callback error for %s: %s", topic, exc)
                return

    @staticmethod
    def _topic_matches(subscription: str, topic: str) -> bool:
        """Check if a topic matches a subscription pattern.

        Supports + and # wildcards.
        """
        if subscription == topic:
            return True

        sub_parts = subscription.split("/")
        topic_parts = topic.split("/")

        for i, sub_part in enumerate(sub_parts):
            if sub_part == "#":
                return True
            if sub_part == "+":
                continue
            if i >= len(topic_parts) or sub_part != topic_parts[i]:
                return False

        return len(sub_parts) == len(topic_parts)
