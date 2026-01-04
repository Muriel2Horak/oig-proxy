#!/usr/bin/env python3
"""
MQTT Publisher s persistentní frontou a replay.
"""

import asyncio
import concurrent.futures
import datetime
import json
import logging
import os
import re
import sqlite3
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
    MQTT_QUEUE_DB_PATH,
    MQTT_QUEUE_MAX_SIZE,
    MQTT_STATE_RETAIN,
    MQTT_REPLAY_RATE,
    PROXY_DEVICE_ID,
    MQTT_USERNAME,
)
from models import SensorConfig
from utils import get_sensor_config, iso_now

if MQTT_AVAILABLE:
    import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


# ============================================================================
# MQTT Queue - Persistentní fronta pro offline režim
# ============================================================================

class MQTTQueue:
    """Persistentní fronta pro MQTT zprávy (SQLite)."""

    def __init__(
        self,
        db_path: str = MQTT_QUEUE_DB_PATH,
        max_size: int = MQTT_QUEUE_MAX_SIZE
    ):
        self.db_path = db_path
        self.max_size = max_size
        self.conn = self._init_db()
        self.lock = asyncio.Lock()

    def _init_db(self) -> sqlite3.Connection:
        """Inicializuje SQLite databázi."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                topic TEXT NOT NULL,
                payload TEXT NOT NULL,
                retain INTEGER NOT NULL DEFAULT 0,
                queued_at TEXT NOT NULL
            )
        """)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON queue(timestamp)"
        )
        conn.commit()
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(queue)")}
            if "retain" not in cols:
                conn.execute("ALTER TABLE queue ADD COLUMN retain INTEGER NOT NULL DEFAULT 0")
                conn.commit()
        except sqlite3.Error as exc:
            logger.debug("MQTTQueue: retain column check failed: %s", exc)

        logger.info("MQTTQueue: Initialized (%s)", self.db_path)
        return conn

    async def add(self, topic: str, payload: str, retain: bool = False) -> bool:
        """Přidá MQTT zprávu do fronty (FIFO)."""
        async with self.lock:
            try:
                # Check size limit
                size = self.size()
                if size >= self.max_size:
                    # Drop oldest
                    self.conn.execute(
                        "DELETE FROM queue WHERE id IN "
                        "(SELECT id FROM queue ORDER BY id LIMIT 1)"
                    )
                    logger.warning(
                        "MQTTQueue full (%s), dropped oldest message",
                        self.max_size,
                    )

                # Pokud je zpráva retain, typicky nás zajímá jen poslední stav/config
                # pro daný topic.
                # Tímhle zabráníme tomu, aby po delším výpadku brokera replay poslal tisíce
                # historických stavů pro stejný topic (což v HA fan-outne do spousty
                # state_changed událostí).
                if retain:
                    self.conn.execute("DELETE FROM queue WHERE topic = ?", (topic,))

                self.conn.execute(
                    "INSERT INTO queue "
                    "(timestamp, topic, payload, retain, queued_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), topic, payload, int(retain), iso_now())
                )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                logger.error("MQTTQueue: Add failed: %s", exc)
                return False

    async def get_next(self) -> tuple[int, str, str, bool] | None:
        """Vrátí další zprávu (id, topic, payload, retain) nebo None."""
        async with self.lock:
            try:
                cursor = self.conn.execute(
                    "SELECT id, topic, payload, retain FROM queue "
                    "ORDER BY id LIMIT 1"
                )
                row = cursor.fetchone()
                if not row:
                    return None
                msg_id, topic, payload, retain = row
                return int(msg_id), str(topic), str(payload), bool(retain)
            except sqlite3.Error as exc:
                logger.error("MQTTQueue: Get next failed: %s", exc)
                return None

    async def remove(self, msg_id: int) -> bool:
        """Odstraní zprávu po úspěšném odeslání."""
        async with self.lock:
            try:
                self.conn.execute(
                    "DELETE FROM queue WHERE id = ?", (msg_id,)
                )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                logger.error("MQTTQueue: Remove failed: %s", exc)
                return False

    def size(self) -> int:
        """Vrátí počet zpráv ve frontě."""
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM queue")
            return cursor.fetchone()[0]
        except sqlite3.Error as exc:
            logger.error("MQTTQueue: Size failed: %s", exc)
            return 0

    def clear(self) -> None:
        """Vymaže celou frontu."""
        try:
            self.conn.execute("DELETE FROM queue")
            self.conn.commit()
            logger.info("MQTTQueue: Cleared")
        except sqlite3.Error as exc:
            logger.error("MQTTQueue: Clear failed: %s", exc)


# ============================================================================
# MQTT Publisher s replay podporou
# ============================================================================

class MQTTPublisher:  # pylint: disable=too-many-instance-attributes
    """MQTT publisher s persistentní frontou a replay."""

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
        self.client: mqtt.Client | None = None
        self.connected = False
        self.discovery_sent: set[str] = set()
        self._last_payload_by_topic: dict[str, str] = {}
        self._local_tzinfo = datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc
        self._message_handlers: dict[str, tuple[int, Callable[[str, bytes, int, bool], None]]] = {}
        self._wildcard_handlers: list[tuple[str, int, Callable[[str, bytes, int, bool], None]]] = []
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._replay_future: concurrent.futures.Future[Any] | None = None

        # Queue
        self.queue = MQTTQueue()
        self._replay_task: asyncio.Task[Any] | None = None

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

    def connect(self, timeout: float | None = None) -> bool:
        """Připojí k MQTT brokeru s timeoutem."""
        if not MQTT_AVAILABLE:
            logger.error("MQTT library paho-mqtt is not installed")
            return False

        # Pokud connect voláme z asyncio kontextu, uložíme si loop pro thread-safe scheduling.
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._main_loop = None

        timeout = timeout or self.CONNECT_TIMEOUT

        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"{MQTT_NAMESPACE}_{self.device_id}",
                protocol=mqtt.MQTTv311
            )
            if MQTT_USERNAME:
                self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

            availability_topic = (
                f"{MQTT_NAMESPACE}/{self.device_id}/availability"
            )
            self.client.will_set(availability_topic, "offline", retain=True)

            # Callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_publish = self._on_publish
            self.client.on_message = self._on_message

            logger.info(
                "MQTT: Connecting to %s:%s (timeout %ss)",
                MQTT_HOST,
                MQTT_PORT,
                timeout,
            )

            self.client.connect(MQTT_HOST, MQTT_PORT, 60)
            self.client.loop_start()

            # Čekáme na callback
            start = time.time()
            while not self.connected and (time.time() - start) < timeout:
                time.sleep(0.1)

            if self.connected:
                self.reconnect_attempts = 0
                return True
            logger.error("MQTT: ❌ Connection timeout after %ss", timeout)
            self._cleanup_client()
            return False

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("MQTT: ❌ Connection failed: %s", e)
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
                    logger.debug("MQTT: Subscribed %s", topic)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning("MQTT: Subscribe failed %s: %s", topic, e)
            for topic, qos, _ in self._wildcard_handlers:
                try:
                    client.subscribe(topic, qos=qos)
                    logger.debug("MQTT: Subscribed %s", topic)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning("MQTT: Subscribe failed %s: %s", topic, e)

            # Trigger replay
            self._schedule_replay()
        else:
            logger.error("MQTT: ❌ Connection refused: %s", rc_msg)
            self.connected = False
            self.last_error_time = time.time()
            self.last_error_msg = rc_msg

    def _on_disconnect(
        self, _client: Any, _userdata: Any, rc: int
    ) -> None:
        was_connected = self.connected
        self.connected = False
        self._last_payload_by_topic.clear()

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
                logger.debug("MQTT: Subscribed %s", topic)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning("MQTT: Subscribe failed %s: %s", topic, e)

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

    async def publish_raw(  # pylint: disable=too-many-arguments
        self,
        *,
        topic: str,
        payload: str,
        qos: int = 1,
        retain: bool = False,
        queue_if_offline: bool = True,
    ) -> bool:
        """Publikuje raw payload na libovolný topic (bez mapování/discovery)."""
        if not self.is_ready():
            if queue_if_offline:
                await self.queue.add(topic, payload, retain)
            return False
        if not self.client:
            return False
        try:
            result = self.client.publish(topic, payload, qos=qos, retain=retain)
            return result.rc == 0
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("MQTT: publish_raw exception: %s", e)
            if queue_if_offline:
                await self.queue.add(topic, payload, retain)
            return False

    def is_ready(self) -> bool:
        """Vrací True pokud je MQTT připraveno."""
        return self.client is not None and self.connected

    async def replay_queue(self) -> None:
        """Replay fronty po reconnectu (rate limited)."""
        queue_size = self.queue.size()
        if queue_size == 0:
            logger.debug("MQTT: Replay queue empty")
            return

        logger.info("MQTT: Starting replay of %s messages...", queue_size)
        replayed = 0
        interval = 1.0 / MQTT_REPLAY_RATE  # ~0.1s pro 10 msg/s

        while True:
            if not self.is_ready():
                logger.warning("MQTT: Replay interrupted - disconnected")
                break

            item = await self.queue.get_next()
            if not item:
                break
            msg_id, topic, payload, retain = item

            try:
                result = self.client.publish(topic, payload, qos=1, retain=retain)
                if result.rc == 0:
                    await self.queue.remove(msg_id)
                    replayed += 1

                    if replayed % 10 == 0:
                        remaining = self.queue.size()
                        logger.debug(
                            "MQTT: Replay progress: %s/%s (%s remaining)",
                            replayed,
                            queue_size,
                            remaining,
                        )
                else:
                    logger.error(
                        "MQTT: Replay publish failed rc=%s",
                        result.rc,
                    )
                    break
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("MQTT: Replay exception: %s", e)
                break

            await asyncio.sleep(interval)

        logger.info("MQTT: Replay complete (%s messages)", replayed)

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

    async def start_health_check(self) -> None:
        """Spustí health check jako background task."""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(
                self.health_check_loop()
            )

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Naváže asyncio loop (pro scheduling z MQTT threadu)."""
        self._main_loop = loop

    def _schedule_replay(self) -> None:
        """Naplánuje replay MQTT fronty do asyncio loopu (thread-safe)."""
        if self._main_loop is None:
            logger.debug("MQTT: Replay skipped - no asyncio loop")
            return
        if self._replay_future is not None and not self._replay_future.done():
            return
        try:
            self._replay_future = asyncio.run_coroutine_threadsafe(
                self.replay_queue(),
                self._main_loop,
            )
            logger.debug("MQTT: Replay task scheduled")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("MQTT: Replay schedule failed: %s", e)

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

    def _build_discovery_payload(  # pylint: disable=too-many-locals
        self,
        *,
        sensor_id: str,
        config: SensorConfig,
        table: str | None,
        device_id: str,
    ) -> tuple[str, dict[str, Any]]:
        device_type = config.device_mapping or "inverter"
        device_name = DEVICE_NAMES.get(device_type, "Střídač")

        device_identifier = f"{MQTT_NAMESPACE}_{device_id}_{device_type}"
        full_device_name = f"OIG {device_name} ({device_id})"

        safe_sensor_id = sensor_id.replace(":", "_").lower()
        unique_id = f"{MQTT_NAMESPACE}_{device_id}_{safe_sensor_id}"
        availability_topic = f"{MQTT_NAMESPACE}/{device_id}/availability"

        component = "binary_sensor" if config.is_binary else "sensor"
        base_object_id = f"{MQTT_NAMESPACE}_{device_id}_{safe_sensor_id}"

        payload: dict[str, Any] = {
            "name": config.name,
            "unique_id": unique_id,
            "state_topic": self._state_topic(device_id, table),
            "value_template": f"{{{{ value_json.{self._json_key(sensor_id)} }}}}",
            "availability": [{"topic": availability_topic}],
            "default_entity_id": f"{component}.{base_object_id}",
            "device": {
                "identifiers": [device_identifier],
                "name": full_device_name,
                "manufacturer": "OIG Power",
                "model": f"OIG BatteryBox - {device_name}",
            },
        }
        if config.json_attributes_topic:
            if config.json_attributes_topic == "state":
                payload["json_attributes_topic"] = self._state_topic(device_id, table)
            else:
                payload["json_attributes_topic"] = config.json_attributes_topic

        if device_type not in ("inverter", "proxy"):
            payload["device"]["via_device"] = f"{MQTT_NAMESPACE}_{self.device_id}_inverter"

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
                bool(self.client),
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

    async def publish_data(self, data: dict[str, Any]) -> bool:  # pylint: disable=too-many-locals
        """Publikuje data na MQTT."""
        table = data.get("_table")
        # Proxy a eventy jdou na pevný proxy device_id
        target_device_id = (
            self.proxy_device_id
            if table in ("proxy_status", "tbl_events")
            else self.device_id
        )

        publish_data, mapped_count = self._map_data_for_publish(
            data, table=str(table) if table else None, target_device_id=target_device_id
        )

        topic = self._state_topic(target_device_id, str(table) if table else None)
        payload = json.dumps(publish_data)

        # De-dupe: pokud payload pro topic je stejný jako minule, nepublikuj ani nequeueuj.
        if self._last_payload_by_topic.get(topic) == payload:
            return True
        self._last_payload_by_topic[topic] = payload

        # Pokud není připojeno, přidej do fronty
        if not self.is_ready():
            await self.queue.add(topic, payload, MQTT_STATE_RETAIN)
            if self.publish_count % 100 == 0:
                logger.warning(
                    "MQTT: Offline - data queued (%s messages)",
                    self.queue.size(),
                )
            self.publish_failed += 1
            return False

        self.publish_count += 1

        try:
            result = self.client.publish(
                topic, payload, qos=MQTT_PUBLISH_QOS, retain=MQTT_STATE_RETAIN
            )
            if result.rc == 0:
                # Detailní log - topic, keys, mapped count
                keys_list = sorted(publish_data.keys())
                logger.debug(
                    "MQTT: → %s | %s/%s mapped | keys: %s",
                    topic,
                    mapped_count,
                    len(publish_data),
                    keys_list,
                )
                return True

            # Pokud publish selže, přidej do fronty
            await self.queue.add(topic, payload, MQTT_STATE_RETAIN)
            self.publish_failed += 1
            logger.error("MQTT: Publish failed rc=%s", result.rc)
            return False
        except Exception as e:  # pylint: disable=broad-exception-caught
            await self.queue.add(topic, payload, MQTT_STATE_RETAIN)
            self.publish_failed += 1
            self.last_error_time = time.time()
            self.last_error_msg = str(e)
            logger.exception("MQTT: Publish exception")
            return False

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

            self.send_discovery(unique_key, cfg, table, device_id=target_device_id)
            mapped_count += 1

            if cfg.options and isinstance(value, int) and 0 <= value < len(cfg.options):
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

    async def publish_proxy_status(self, status_payload: dict[str, Any]) -> bool:
        """Publikuje stav proxy jako samostatnou tabulku proxy_status."""
        data = {"_table": "proxy_status"}
        data.update(status_payload)
        return await self.publish_data(data)
