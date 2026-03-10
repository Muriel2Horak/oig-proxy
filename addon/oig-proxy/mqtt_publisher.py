#!/usr/bin/env python3
import asyncio
import datetime
import json
import logging
import re
import time
from typing import Any
from collections.abc import Callable

from config import (
    DEVICE_NAMES,
    MQTT_AVAILABLE,
    MQTT_HOST,
    MQTT_NAMESPACE,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_PUBLISH_QOS,
    MQTT_STATE_RETAIN,
    PROXY_DEVICE_ID,
    MQTT_USERNAME,
)
from models import SensorConfig
from utils import get_sensor_config

if MQTT_AVAILABLE:  # pragma: no cover
    import paho.mqtt.client as mqtt  # pragma: no cover
else:  # pragma: no cover
    mqtt = None  # type: ignore[assignment]  # pylint: disable=invalid-name

logger = logging.getLogger(__name__)

_MQTT_LOG_SUBSCRIBED = "MQTT: Subscribed %s"
_MQTT_LOG_SUBSCRIBE_FAILED = "MQTT: Subscribe failed %s: %s"


class MQTTPublisher:  # pylint: disable=too-many-instance-attributes
    # MQTT return codes
    RC_CODES = {
        0: "Connection successful",
        1: "Incorrect protocol version",
        2: "Invalid client identifier",
        3: "Server unavailable",
        4: "Bad username or password",
        5: "Not authorized",
    }

    # Konfigurace
    CONNECT_TIMEOUT = 5
    HEALTH_CHECK_INTERVAL = 30
    PUBLISH_LOG_EVERY = 100

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.proxy_device_id = PROXY_DEVICE_ID or device_id
        self.client: Any | None = None
        self.connected = False
        self.discovery_sent: set[str] = set()
        self._last_payload_by_topic: dict[str, str] = {}
        self._last_publish_time_by_topic: dict[str, float] = {}
        self._local_tzinfo = datetime.datetime.now(
        ).astimezone().tzinfo or datetime.timezone.utc
        self._message_handlers: dict[str, tuple[int,
                                                Callable[[str, bytes, int, bool], None]]] = {}
        self._wildcard_handlers: list[tuple[str, int,
                                            Callable[[str, bytes, int, bool], None]]] = []
        self._on_connect_handlers: list[Callable[[], None]] = []
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Statistiky
        self.publish_count = 0
        self.publish_success = 0
        self.publish_failed = 0
        self.last_publish_time: float = 0
        self.last_error_time: float = 0
        self.last_error_msg: str = ""
        self.reconnect_attempts = 0

        # Health check
        self._health_check_task: asyncio.Task[Any] | None = None

    def _setup_client_callbacks(self) -> None:
        """Nastavení callbacků pro MQTT klienta."""
        if self.client is None:
            return
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.on_message = self._on_message

    def _create_client(self) -> Any | None:
        if not MQTT_AVAILABLE:
            logger.error("MQTT library paho-mqtt is not installed")
            return None
        if mqtt is None:
            return None

        client_kwargs: dict[str, Any] = {
            "client_id": f"{MQTT_NAMESPACE}_{self.device_id}",
            "protocol": getattr(mqtt, "MQTTv311", 4),
        }
        callback_api = getattr(mqtt, "CallbackAPIVersion", None)
        if callback_api is not None:
            client_kwargs["callback_api_version"] = callback_api.VERSION1
        client = mqtt.Client(**client_kwargs)  # type: ignore[call-arg]
        if MQTT_USERNAME:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        availability_topic = (
            f"{MQTT_NAMESPACE}/{self.device_id}/availability"
        )
        client.will_set(availability_topic, "offline", retain=True)

        return client

    def _wait_for_connection(self, timeout: float) -> bool:
        start = time.time()
        while not self.connected and (time.time() - start) < timeout:
            time.sleep(0.1)
        return self.connected

    def connect(self, timeout: float | None = None) -> bool:
        """Připojení k MQTT brokeru."""
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._main_loop = None

        timeout = timeout or self.CONNECT_TIMEOUT

        self.client = self._create_client()
        if self.client is None:
            return False

        self._setup_client_callbacks()

        logger.info(
            "MQTT: Connecting to %s:%s (timeout %ss)",
            MQTT_HOST,
            MQTT_PORT,
            timeout,
        )

        self.client.connect(MQTT_HOST, MQTT_PORT, 60)
        self.client.loop_start()

        if self._wait_for_connection(timeout):
            self.reconnect_attempts = 0
            return True
        logger.error("MQTT: ❌ Connection timeout after %ss", timeout)
        self._cleanup_client()
        return False

    def _cleanup_client(self) -> None:
        """Bezpečně uklidí MQTT klienta."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("MQTT: Client cleanup failed: %s", exc)
            self.client = None
        self.connected = False

    def _on_connect(
        self, client: Any, _userdata: Any, flags: Any, rc: int
    ) -> None:
        rc_msg = self.RC_CODES.get(rc, f"Unknown error ({rc})")

        if rc == 0:
            logger.info("MQTT: Connected (flags=%s)", flags)
            self.connected = True
            self.reconnect_attempts = 0
            self._last_payload_by_topic.clear()
            self._last_publish_time_by_topic.clear()

            # Availability online
            client.publish(
                f"{MQTT_NAMESPACE}/{self.device_id}/availability",
                "online",
                retain=True,
                qos=1
            )
            if self.proxy_device_id != self.device_id:
                client.publish(
                    f"{MQTT_NAMESPACE}/{self.proxy_device_id}/availability",
                    "online",
                    retain=True,
                    qos=1
                )

            # Reset discovery
            self.discovery_sent.clear()

            # Subscribe handlers (if any)
            for topic, (qos, _) in self._message_handlers.items():
                try:
                    client.subscribe(topic, qos=qos)
                    logger.debug(_MQTT_LOG_SUBSCRIBED, topic)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning(_MQTT_LOG_SUBSCRIBE_FAILED, topic, e)
            for topic, qos, _ in self._wildcard_handlers:
                try:
                    client.subscribe(topic, qos=qos)
                    logger.debug(_MQTT_LOG_SUBSCRIBED, topic)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning(_MQTT_LOG_SUBSCRIBE_FAILED, topic, e)

            for handler in self._on_connect_handlers:
                try:
                    handler()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.debug("MQTT: on_connect handler failed: %s", e)
        else:
            logger.error("MQTT: ❌ Connection refused: %s", rc_msg)
            self.connected = False
            self.last_error_time = time.time()
            self.last_error_msg = rc_msg

    def add_on_connect_handler(self, handler: Callable[[], None]) -> None:
        """Register callback executed after each successful MQTT connect."""
        if handler not in self._on_connect_handlers:
            self._on_connect_handlers.append(handler)

    def _on_disconnect(
        self, _client: Any, _userdata: Any, rc: int
    ) -> None:
        was_connected = self.connected
        self.connected = False
        self._last_payload_by_topic.clear()
        self._last_publish_time_by_topic.clear()

        if rc == 0:
            logger.info("MQTT: Disconnected (clean disconnect)")
        else:
            logger.warning("MQTT: ⚠️ Unexpected disconnect (rc=%s)", rc)
            self.last_error_time = time.time()
            self.last_error_msg = f"Unexpected disconnect (rc={rc})"

        if was_connected:
            logger.warning(
                "MQTT: 🔴 Data processing paused "
                "until reconnection"
            )

    def _on_publish(self, _client: Any, _userdata: Any, _mid: int) -> None:
        """Callback při potvrzení publish od brokera."""
        self.publish_success += 1
        self.last_publish_time = time.time()

        if self.publish_success % self.PUBLISH_LOG_EVERY == 0:
            logger.debug(
                "MQTT: 📊 Stats: %s OK, %s FAIL out of %s total",
                self.publish_success,
                self.publish_failed,
                self.publish_count,
            )

    def add_message_handler(
        self,
        *,
        topic: str,
        handler: Callable[[str, bytes, int, bool], None],
        qos: int = 1,
    ) -> None:
        """Zaregistruje handler pro příchozí MQTT zprávy na daném topicu."""
        if "+" in topic or "#" in topic:
            self._wildcard_handlers.append((topic, qos, handler))
        else:
            self._message_handlers[topic] = (qos, handler)
        if self.client and self.connected:
            try:
                self.client.subscribe(topic, qos=qos)
                logger.debug(_MQTT_LOG_SUBSCRIBED, topic)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning(_MQTT_LOG_SUBSCRIBE_FAILED, topic, e)

    def _on_message(self, _client: Any, _userdata: Any, msg: Any) -> None:
        try:
            topic = str(getattr(msg, "topic", ""))
            entry = self._message_handlers.get(topic)
            payload: bytes = getattr(msg, "payload", b"") or b""
            qos: int = int(getattr(msg, "qos", 0) or 0)
            retain: bool = bool(getattr(msg, "retain", False))
            if entry is not None:
                _, handler = entry
                handler(topic, payload, qos, retain)
            for pattern, _, handler in self._wildcard_handlers:
                if self._topic_matches(pattern, topic):
                    handler(topic, payload, qos, retain)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("MQTT: Message handler failed: %s", e)

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        if pattern == topic:
            return True
        p_parts = pattern.split("/")
        t_parts = topic.split("/")
        for idx, p in enumerate(p_parts):
            if p == "#":
                return True
            if idx >= len(t_parts):
                return False
            if p == "+":
                continue
            if p != t_parts[idx]:
                return False
        return len(t_parts) == len(p_parts)

    async def publish_raw( # pylint: disable=too-many-arguments, no-await-async
        self,
        *,
        topic: str,
        payload: str,
        qos: int = 1,
        retain: bool = False,
    ) -> bool:
        """Publikuje raw payload na libovolný topic (bez mapování/discovery)."""
        if not self.is_ready():
            return False
        if not self.client:
            return False
        try:
            result = self.client.publish(
                topic, payload, qos=qos, retain=retain)
            return result.rc == 0
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("MQTT: publish_raw exception: %s", e)
            return False

    def is_ready(self) -> bool:
        """Vrací True pokud je MQTT připraveno."""
        return self.client is not None and self.connected

    async def health_check_loop(self) -> None:
        """Periodicky kontroluje MQTT spojení."""
        logger.info(
            "MQTT: Health check started (interval %ss)",
            self.HEALTH_CHECK_INTERVAL,
        )

        while True:
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

            if not self.connected:
                self.reconnect_attempts += 1
                logger.warning(
                    "MQTT: 🔄 Health check - reconnect attempt #%s",
                    self.reconnect_attempts,
                )

                if self.connect(timeout=self.CONNECT_TIMEOUT):
                    logger.info(
                        "MQTT: ✅ Reconnect succeeded after %s attempts",
                        self.reconnect_attempts,
                    )
                else:
                    logger.warning(
                        "MQTT: ❌ Reconnect failed, next attempt in %ss",
                        self.HEALTH_CHECK_INTERVAL,
                    )

    def publish_availability(self, device_id: str | None = None) -> None:
        """Publikuje availability status na MQTT."""
        if not self.client or not self.connected:
            return
        dev_id = device_id or self.device_id
        topic = f"{MQTT_NAMESPACE}/{dev_id}/availability"
        self.client.publish(topic, "online", retain=True, qos=1)
        logger.debug("MQTT: Availability published to %s", topic)

    async def start_health_check(self) -> None:  # noqa: C417 - uses asyncio.create_task
        """Spustí health check jako background task."""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(
                self.health_check_loop()
            )

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Naváže asyncio loop (pro scheduling z MQTT threadu)."""
        self._main_loop = loop

    @staticmethod
    def _state_topic(dev_id: str, table: str | None) -> str:
        """Vrátí state topic pro tabulku."""
        if table:
            return f"{MQTT_NAMESPACE}/{dev_id}/{table}/state"
        return f"{MQTT_NAMESPACE}/{dev_id}/state"

    def state_topic(self, dev_id: str, table: str | None) -> str:
        """Veřejný wrapper pro výpočet state topicu."""
        return self._state_topic(dev_id, table)

    def get_cached_payload(self, topic: str) -> str | None:
        """Vrátí poslední publikovaný payload pro topic."""
        return self._last_payload_by_topic.get(topic)

    def set_cached_payload(self, topic: str, payload: str) -> None:
        """Uloží poslední payload pro topic do cache."""
        self._last_payload_by_topic[topic] = payload

    @staticmethod
    def _json_key(sensor_id: str) -> str:
        return sensor_id.split(":", 1)[1] if ":" in sensor_id else sensor_id

    def _build_device_info(
        self,
        device_type: str,
        device_id: str,
    ) -> tuple[str, str]:
        device_name = DEVICE_NAMES.get(device_type, "Střídač")
        device_identifier = f"{MQTT_NAMESPACE}_{device_id}_{device_type}"
        full_device_name = f"OIG {device_name} ({device_id})"
        return device_identifier, full_device_name

    def _build_sensor_ids(
        self,
        sensor_id: str,
        device_id: str,
        is_binary: bool,
    ) -> tuple[str, str, str]:
        safe_sensor_id = sensor_id.replace(":", "_").lower()
        unique_id = f"{MQTT_NAMESPACE}_{device_id}_{safe_sensor_id}"
        component = "binary_sensor" if is_binary else "sensor"
        base_object_id = f"{MQTT_NAMESPACE}_{device_id}_{safe_sensor_id}"
        return unique_id, component, base_object_id

    def _apply_json_attributes(
        self,
        payload: dict[str, Any],
        config: SensorConfig,
        device_id: str,
        table: str | None,
    ) -> None:
        if config.json_attributes_topic:
            if config.json_attributes_topic == "state":
                payload["json_attributes_topic"] = self._state_topic(
                    device_id, table)
            else:
                payload["json_attributes_topic"] = config.json_attributes_topic

    def _apply_sensor_config(
        self,
        payload: dict[str, Any],
        config: SensorConfig,
    ) -> None:
        if config.is_binary:
            payload["payload_on"] = "1"
            payload["payload_off"] = "0"
        else:
            if config.state_class:
                payload["state_class"] = config.state_class
            if config.options:
                payload["options"] = config.options

        if config.unit and not config.is_binary:
            payload["unit_of_measurement"] = config.unit
        if config.device_class:
            payload["device_class"] = config.device_class
        if config.icon:
            payload["icon"] = config.icon
        if config.entity_category:
            payload["entity_category"] = config.entity_category

    def _build_discovery_payload(
        self,
        *,
        sensor_id: str,
        config: SensorConfig,
        table: str | None,
        device_id: str,
    ) -> tuple[str, dict[str, Any]]:
        device_type = config.device_mapping or "inverter"
        device_identifier, full_device_name = self._build_device_info(
            device_type, device_id)

        unique_id, component, base_object_id = self._build_sensor_ids(
            sensor_id, device_id, config.is_binary)

        availability_topic = f"{MQTT_NAMESPACE}/{device_id}/availability"

        payload: dict[str, Any] = {
            "name": config.name,
            "unique_id": unique_id,
            "state_topic": self._state_topic(device_id, table),
            "value_template": f"{{{{ value_json.{self._json_key(sensor_id)} }}}}",
            "availability": [{"topic": availability_topic}],
            "default_entity_id": f"{component}.{base_object_id}",
            "force_update": True,
            "device": {
                "identifiers": [device_identifier],
                "name": full_device_name,
                "manufacturer": "OIG Power",
                "model": f"OIG BatteryBox - {DEVICE_NAMES.get(device_type, 'Střídač')}",
            },
        }

        self._apply_json_attributes(payload, config, device_id, table)

        if device_type not in ("inverter", "proxy"):
            payload["device"]["via_device"] = (
                f"{MQTT_NAMESPACE}_{self.device_id}_inverter"
            )

        self._apply_sensor_config(payload, config)

        topic = f"homeassistant/{component}/{unique_id}/config"
        return topic, payload

    def send_discovery(
        self,
        sensor_id: str,
        config: SensorConfig,
        table: str | None = None,
        device_id: str | None = None
    ) -> None:
        """Odešle MQTT discovery pro senzor."""
        if not self.client or not self.connected:
            logger.debug(
                "MQTT: Discovery %s skipped - not connected (client=%s, connected=%s)",
                sensor_id,
                bool(
                    self.client),
                self.connected,
            )
            return
        if sensor_id in self.discovery_sent:
            return

        dev_id = device_id or self.device_id
        topic, discovery_payload = self._build_discovery_payload(
            sensor_id=sensor_id,
            config=config,
            table=table,
            device_id=dev_id,
        )
        result = self.client.publish(
            topic, json.dumps(discovery_payload), retain=True, qos=1
        )
        self.discovery_sent.add(sensor_id)
        component = "binary_sensor" if config.is_binary else "sensor"
        device_type = config.device_mapping or "inverter"
        device_name = DEVICE_NAMES.get(device_type, "Střídač")
        logger.debug(
            "MQTT: Discovery %s → %s/%s (mid=%s)",
            sensor_id,
            component,
            device_name,
            result.mid,
        )

    def _determine_target_device_id(self, table: str | None) -> str:
        if table in ("proxy_status", "tbl_events"):
            return self.proxy_device_id
        return self.device_id

    def _check_payload_deduplication(self, topic: str, payload: str) -> bool:
        now = time.time()
        last_publish = self._last_publish_time_by_topic.get(topic, 0)
        # Povolit publikaci pokud se payload zmenil NEBO uplynulo 60 sekund
        if self._last_payload_by_topic.get(topic) == payload:
            if now - last_publish < 60:
                return True  # Stejny payload a jeste neuplynulo 60s
        self._last_payload_by_topic[topic] = payload
        self._last_publish_time_by_topic[topic] = now
        return False

    def _handle_offline_queueing(
        self,
        _topic: str,
        _payload: str,
    ) -> bool:
        if self.publish_count % 100 == 0:
            logger.warning("MQTT: Offline - data dropped")
        self.publish_failed += 1
        return False

    def _execute_publish(
        self,
        topic: str,
        payload: str,
        mapped_count: int,
    ) -> bool:
        self.publish_count += 1

        try:
            client = self.client
            if client is None:
                self.publish_failed += 1
                logger.error("MQTT: Publish aborted - MQTT client is not initialized")
                return False
            result = client.publish(
                topic, payload, qos=MQTT_PUBLISH_QOS, retain=MQTT_STATE_RETAIN
            )
            if result.rc == 0:
                keys_list = sorted(json.loads(payload).keys())
                logger.debug(
                    "MQTT: → %s | %s/%s mapped | keys: %s",
                    topic,
                    mapped_count,
                    len(json.loads(payload)),
                    keys_list,
                )
                return True

            self.publish_failed += 1
            logger.error("MQTT: Publish failed rc=%s", result.rc)
            return False
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.publish_failed += 1
            self.last_error_time = time.time()
            self.last_error_msg = str(e)
            logger.exception("MQTT: Publish exception")
            return False

    async def publish_data(self, data: dict[str, Any]) -> bool:
        """Publikování dat do MQTT."""
        table = data.get("_table")
        target_device_id = self._determine_target_device_id(table)

        publish_data, mapped_count = self._map_data_for_publish(
            data, table=str(table) if table else None, target_device_id=target_device_id)

        if not publish_data:
            logger.debug(
                "MQTT: Skip publish of empty state payload (table=%s device=%s)",
                table,
                target_device_id,
            )
            return False

        topic = self._state_topic(
            target_device_id,
            str(table) if table else None)
        payload = json.dumps(publish_data)

        if not self.is_ready():
            return self._handle_offline_queueing(topic, payload)

        return self._execute_publish(topic, payload, mapped_count)

    def map_data_for_publish(
        self,
        data: dict[str, Any],
        *,
        table: str | None,
        target_device_id: str,
    ) -> tuple[dict[str, Any], int]:
        """Veřejný wrapper pro mapování dat před publikací."""
        return self._map_data_for_publish(
            data,
            table=table,
            target_device_id=target_device_id,
        )

    def _map_data_for_publish(
        self,
        data: dict[str, Any],
        *,
        table: str | None,
        target_device_id: str,
    ) -> tuple[dict[str, Any], int]:
        publish_data: dict[str, Any] = {}
        mapped_count = 0
        for key, value in data.items():
            if key.startswith("_"):
                continue
            cfg, unique_key = get_sensor_config(key, table)
            if cfg is None:
                publish_data[key] = value
                continue

            self.send_discovery(
                unique_key, cfg, table, device_id=target_device_id)
            mapped_count += 1

            if cfg.options and isinstance(
                    value, int) and 0 <= value < len(
                    cfg.options):
                publish_data[key] = cfg.options[value]
            else:
                publish_data[key] = self._coerce_state_value(cfg, value)
        return publish_data, mapped_count

    _DT_WITH_TZ_RE = re.compile(r"(Z|[+-]\\d{2}:\\d{2})\\s*$")

    def _coerce_state_value(self, cfg: SensorConfig, value: Any) -> Any:
        if cfg.device_class != "timestamp" or not isinstance(value, str):
            return value

        raw = value.strip()
        if not raw or self._DT_WITH_TZ_RE.search(raw):
            return value

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                dt = datetime.datetime.strptime(raw, fmt)
                dt = dt.replace(tzinfo=self._local_tzinfo)
                return dt.isoformat()
            except ValueError:
                continue

        return value

    async def publish_proxy_status(
            self, status_payload: dict[str, Any]) -> bool:
        """Publikuje stav proxy jako samostatnou tabulku proxy_status."""
        data = {"_table": "proxy_status"}
        data.update(status_payload)
        return await self.publish_data(data)
