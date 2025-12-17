#!/usr/bin/env python3
"""
MQTT Publisher s persistentní frontou a replay.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import sqlite3
import time
from typing import Any

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
    MQTT_USERNAME,
)
from models import SensorConfig
from utils import get_sensor_config

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
        except Exception:
            pass
        
        logger.info(f"MQTTQueue: Inicializováno ({self.db_path})")
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
                        f"MQTTQueue full ({self.max_size}), "
                        "dropped oldest message"
                    )
                
                import time as time_module
                from utils import iso_now
                
                self.conn.execute(
                    "INSERT INTO queue "
                    "(timestamp, topic, payload, retain, queued_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time_module.time(), topic, payload, int(retain), iso_now())
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"MQTTQueue: Add failed: {e}")
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
            except Exception as e:
                logger.error(f"MQTTQueue: Get next failed: {e}")
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
            except Exception as e:
                logger.error(f"MQTTQueue: Remove failed: {e}")
                return False
    
    def size(self) -> int:
        """Vrátí počet zpráv ve frontě."""
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM queue")
            return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"MQTTQueue: Size failed: {e}")
            return 0
    
    def clear(self) -> None:
        """Vymaže celou frontu."""
        try:
            self.conn.execute("DELETE FROM queue")
            self.conn.commit()
            logger.info("MQTTQueue: Cleared")
        except Exception as e:
            logger.error(f"MQTTQueue: Clear failed: {e}")


# ============================================================================
# MQTT Publisher s replay podporou
# ============================================================================

class MQTTPublisher:
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
        from config import PROXY_DEVICE_ID  # avoid circular import on module load
        self.device_id = device_id
        self.proxy_device_id = PROXY_DEVICE_ID or device_id
        self.client: mqtt.Client | None = None
        self.connected = False
        self.discovery_sent: set[str] = set()
        self._last_payload_by_topic: dict[str, str] = {}
        self._local_tzinfo = datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc
        
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
            logger.error("MQTT knihovna paho-mqtt není nainstalována")
            return False
        
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
            
            logger.info(
                f"MQTT: Připojuji k {MQTT_HOST}:{MQTT_PORT} "
                f"(timeout {timeout}s)"
            )
            
            self.client.connect(MQTT_HOST, MQTT_PORT, 60)
            self.client.loop_start()
            
            # Čekáme na callback
            start = time.time()
            while not self.connected and (time.time() - start) < timeout:
                time.sleep(0.1)
            
            if self.connected:
                logger.info(f"MQTT: ✅ Připojeno k {MQTT_HOST}:{MQTT_PORT}")
                self.reconnect_attempts = 0
                return True
            else:
                logger.error(f"MQTT: ❌ Timeout připojení po {timeout}s")
                self._cleanup_client()
                return False
                
        except Exception as e:
            logger.error(f"MQTT: ❌ Připojení selhalo: {e}")
            self._cleanup_client()
            return False
    
    def _cleanup_client(self) -> None:
        """Bezpečně uklidí MQTT klienta."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.connected = False
    
    def _on_connect(
        self, client: Any, userdata: Any, flags: Any, rc: int
    ) -> None:
        rc_msg = self.RC_CODES.get(rc, f"Unknown error ({rc})")
        
        if rc == 0:
            logger.info(f"MQTT: Připojeno (flags={flags})")
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
            
            # Trigger replay
            if self._replay_task is None or self._replay_task.done():
                try:
                    loop = asyncio.get_running_loop()
                    self._replay_task = loop.create_task(
                        self.replay_queue()
                    )
                    logger.info("MQTT: Replay task started")
                except RuntimeError:
                    # No event loop in MQTT thread - schedule on main loop
                    logger.debug(
                        "MQTT: Callback v threadu - skipuju replay task"
                    )
        else:
            logger.error(f"MQTT: ❌ Připojení odmítnuto: {rc_msg}")
            self.connected = False
            self.last_error_time = time.time()
            self.last_error_msg = rc_msg
    
    def _on_disconnect(
        self, client: Any, userdata: Any, rc: int
    ) -> None:
        was_connected = self.connected
        self.connected = False
        self._last_payload_by_topic.clear()
        
        if rc == 0:
            logger.info("MQTT: Odpojeno (čisté odpojení)")
        else:
            logger.warning(f"MQTT: ⚠️ Neočekávané odpojení (rc={rc})")
            self.last_error_time = time.time()
            self.last_error_msg = f"Unexpected disconnect (rc={rc})"
            
        if was_connected:
            logger.warning(
                "MQTT: 🔴 Zpracování dat pozastaveno "
                "do obnovení spojení"
            )
    
    def _on_publish(self, client: Any, userdata: Any, mid: int) -> None:
        """Callback při potvrzení publish od brokera."""
        self.publish_success += 1
        self.last_publish_time = time.time()
        
        if self.publish_success % self.PUBLISH_LOG_EVERY == 0:
            logger.info(
                f"MQTT: 📊 Stats: {self.publish_success} OK, "
                f"{self.publish_failed} FAIL z {self.publish_count} celkem"
            )
    
    def is_ready(self) -> bool:
        """Vrací True pokud je MQTT připraveno."""
        return self.client is not None and self.connected
    
    async def replay_queue(self) -> None:
        """Replay fronty po reconnectu (rate limited)."""
        queue_size = self.queue.size()
        if queue_size == 0:
            logger.debug("MQTT: Replay queue prázdná")
            return
        
        logger.info(f"MQTT: Začínám replay {queue_size} zpráv...")
        replayed = 0
        interval = 1.0 / MQTT_REPLAY_RATE  # ~0.1s pro 10 msg/s
        
        while True:
            if not self.is_ready():
                logger.warning("MQTT: Replay přerušeno - odpojeno")
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
                        logger.info(
                            f"MQTT: Replay progress: {replayed}/{queue_size} "
                            f"({remaining} zbývá)"
                        )
                else:
                    logger.error(
                        f"MQTT: Replay publish failed rc={result.rc}"
                    )
                    break
            except Exception as e:
                logger.error(f"MQTT: Replay exception: {e}")
                break
            
            await asyncio.sleep(interval)
        
        logger.info(f"MQTT: Replay dokončen ({replayed} zpráv)")
    
    async def health_check_loop(self) -> None:
        """Periodicky kontroluje MQTT spojení."""
        logger.info(
            f"MQTT: Health check spuštěn "
            f"(interval {self.HEALTH_CHECK_INTERVAL}s)"
        )
        
        while True:
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
            
            if not self.connected:
                self.reconnect_attempts += 1
                logger.warning(
                    f"MQTT: 🔄 Health check - pokus o reconnect "
                    f"#{self.reconnect_attempts}"
                )
                
                if self.connect(timeout=self.CONNECT_TIMEOUT):
                    logger.info(
                        f"MQTT: ✅ Reconnect úspěšný po "
                        f"{self.reconnect_attempts} pokusech"
                    )
                else:
                    logger.warning(
                        f"MQTT: ❌ Reconnect selhal, další pokus za "
                        f"{self.HEALTH_CHECK_INTERVAL}s"
                    )
    
    def publish_availability(self, device_id: str | None = None) -> None:
        """Publikuje availability status na MQTT."""
        if not self.client or not self.connected:
            return
        dev_id = device_id or self.device_id
        topic = f"{MQTT_NAMESPACE}/{dev_id}/availability"
        self.client.publish(topic, "online", retain=True, qos=1)
        logger.info(f"MQTT: Availability published to {topic}")
    
    async def start_health_check(self) -> None:
        """Spustí health check jako background task."""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(
                self.health_check_loop()
            )

    @staticmethod
    def _state_topic(dev_id: str, table: str | None) -> str:
        if table:
            return f"{MQTT_NAMESPACE}/{dev_id}/{table}/state"
        return f"{MQTT_NAMESPACE}/{dev_id}/state"

    @staticmethod
    def _json_key(sensor_id: str) -> str:
        return sensor_id.split(":", 1)[1] if ":" in sensor_id else sensor_id

    def _build_discovery_payload(
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
            logger.debug(f"MQTT: Discovery {sensor_id} skipped - not connected (client={bool(self.client)}, connected={self.connected})")
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
            f"MQTT: Discovery {sensor_id} → {component}/{device_name} "
            f"(mid={result.mid})"
        )
    
    async def publish_data(self, data: dict[str, Any]) -> bool:
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
                    f"MQTT: Offline - data ve frontě "
                    f"({self.queue.size()} zpráv)"
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
                    f"MQTT: → {topic} | "
                    f"{mapped_count}/{len(publish_data)} mapped | "
                    f"keys: {keys_list}"
                )
                return True
            else:
                # Pokud publish selže, přidej do fronty
                await self.queue.add(topic, payload, MQTT_STATE_RETAIN)
                self.publish_failed += 1
                logger.error(f"MQTT: Publish selhal rc={result.rc}")
                return False
        except Exception as e:
            await self.queue.add(topic, payload, MQTT_STATE_RETAIN)
            self.publish_failed += 1
            self.last_error_time = time.time()
            self.last_error_msg = str(e)
            logger.error(f"MQTT: Publish exception: {e}")
            return False

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

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
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
