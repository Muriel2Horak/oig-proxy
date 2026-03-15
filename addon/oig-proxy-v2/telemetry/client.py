from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from importlib import import_module
from pathlib import Path
from typing import Any

logger = logging.getLogger("oig.telemetry")

try:
    import_module("paho.mqtt.client")
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

BUFFER_MAX_MESSAGES = 1000
BUFFER_MAX_AGE_HOURS = 168
SQL_SELECT_COUNT = "SELECT COUNT(*) FROM messages"
BUFFER_DB_PATH = Path("/data/telemetry_buffer.db")


def _get_instance_hash() -> str:
    supervisor_token = os.getenv("SUPERVISOR_TOKEN", "")
    if supervisor_token:
        return hashlib.sha256(supervisor_token.encode()).hexdigest()[:32]
    hostname = os.getenv("HOSTNAME", "unknown")
    return hashlib.sha256(hostname.encode()).hexdigest()[:32]


def _init_sqlite_db(db_path: str, schema_sql: str, indexes_sql: str = "") -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(schema_sql)
    if indexes_sql:
        conn.executescript(indexes_sql)
    return conn


class TelemetryBuffer:
    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path if db_path is not None else BUFFER_DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            schema_sql = """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    retries INTEGER DEFAULT 0
                );
            """
            indexes_sql = """
                CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp);
            """
            self._conn = _init_sqlite_db(str(self._db_path), schema_sql, indexes_sql)
            self._conn.commit()
            self._cleanup()
            count = self._conn.execute(SQL_SELECT_COUNT).fetchone()[0]
            if count > 0:
                logger.debug("Telemetry buffer pending messages: %d", count)
        except Exception as exc:
            logger.warning("Failed to initialize telemetry buffer: %s", exc)
            self._conn = None

    def _cleanup(self) -> None:
        if not self._conn:
            return
        try:
            cutoff = time.time() - (BUFFER_MAX_AGE_HOURS * 3600)
            self._conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
            self._conn.execute(
                """
                DELETE FROM messages WHERE id NOT IN (
                    SELECT id FROM messages ORDER BY timestamp DESC LIMIT ?
                )
                """,
                (BUFFER_MAX_MESSAGES,),
            )
            self._conn.commit()
        except Exception:
            return

    def store(self, topic: str, payload: dict[str, Any]) -> bool:
        if not self._conn:
            return False
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
                (topic, payload_json, time.time()),
            )
            self._conn.commit()
            count = self._conn.execute(SQL_SELECT_COUNT).fetchone()[0]
            if count > BUFFER_MAX_MESSAGES:
                self._cleanup()
            return True
        except Exception:
            return False

    def get_pending(self, limit: int = 50) -> list[tuple[int, str, dict[str, Any]]]:
        if not self._conn:
            return []
        try:
            cursor = self._conn.execute(
                "SELECT id, topic, payload FROM messages ORDER BY timestamp ASC LIMIT ?",
                (limit,),
            )
            results: list[tuple[int, str, dict[str, Any]]] = []
            deleted = False
            for row in cursor:
                try:
                    payload = json.loads(row[2])
                    if isinstance(payload, dict):
                        results.append((row[0], row[1], payload))
                except json.JSONDecodeError:
                    self._conn.execute("DELETE FROM messages WHERE id = ?", (row[0],))
                    deleted = True
            if deleted:
                self._conn.commit()
            return results
        except Exception:
            return []

    def remove(self, message_id: int) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            self._conn.commit()
        except Exception:
            return

    def count(self) -> int:
        if not self._conn:
            return 0
        try:
            return self._conn.execute(SQL_SELECT_COUNT).fetchone()[0]
        except Exception:
            return 0

    def close(self) -> None:
        if not self._conn:
            return
        try:
            self._conn.close()
        except Exception:
            return
        self._conn = None


class TelemetryClient:
    def __init__(
        self,
        device_id: str,
        version: str,
        *,
        telemetry_enabled: bool = True,
        telemetry_mqtt_broker: str = "telemetry.muriel-cz.cz:1883",
        telemetry_interval_s: int = 300,
        db_path: Path | None = None,
    ):
        self._device_id = device_id
        self.version = version
        self.instance_hash = _get_instance_hash()
        self._telemetry_interval_s = telemetry_interval_s
        self._enabled = telemetry_enabled and MQTT_AVAILABLE
        self._client: Any | None = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._buffer = TelemetryBuffer(db_path=db_path) if self._enabled else None
        self._last_buffer_flush = 0.0
        self._mqtt_host, self._mqtt_port = self._parse_mqtt_url(telemetry_mqtt_broker)
        self._last_connect_attempt = 0.0
        self._connect_backoff_s = 5.0

    @property
    def device_id(self) -> str:
        return self._device_id

    @device_id.setter
    def device_id(self, value: str) -> None:
        self._device_id = value or ""

    @staticmethod
    def _parse_mqtt_url(url: str) -> tuple[str, int]:
        url = url.replace("mqtt://", "").replace("tcp://", "")
        if ":" in url:
            host, port_str = url.rsplit(":", 1)
            try:
                return host, int(port_str)
            except ValueError:
                return host, 1883
        return url, 1883

    def _cleanup_client(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass
        self._client = None
        self._connected = False

    def _create_client(self) -> bool:
        if not MQTT_AVAILABLE:
            return False
        try:
            mqtt = import_module("paho.mqtt.client")
            client_id = f"oig-proxy-{self.device_id}-{self.instance_hash[:8]}"
            kwargs: dict[str, Any] = {
                "client_id": client_id,
                "clean_session": True,
                "protocol": mqtt.MQTTv311,
            }
            callback_api = getattr(mqtt, "CallbackAPIVersion", None)
            if callback_api is not None:
                kwargs["callback_api_version"] = callback_api.VERSION2
            client: Any = mqtt.Client(**kwargs)  # type: ignore[call-arg]
            self._client = client

            def on_connect(_client, _userdata, _flags, rc, _properties=None):
                if rc == 0:
                    self._connected = True
                    self._connect_backoff_s = 5.0

            def on_disconnect(_client, _userdata, _disconnect_flags, _rc, _properties=None):
                self._connected = False

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.connect(self._mqtt_host, self._mqtt_port, keepalive=60)
            client.loop_start()

            for _ in range(50):
                if self._connected:
                    return True
                time.sleep(0.1)

            self._cleanup_client()
            self._connect_backoff_s = min(self._connect_backoff_s * 2.0, 300.0)
            return False
        except Exception:
            self._cleanup_client()
            self._connect_backoff_s = min(self._connect_backoff_s * 2.0, 300.0)
            return False

    def _ensure_connected(self) -> bool:
        if self._connected and self._client:
            return True
        now = time.monotonic()
        if (now - self._last_connect_attempt) < self._connect_backoff_s:
            return False
        self._last_connect_attempt = now

        if self._client:
            try:
                if self._client.is_connected():
                    self._connected = True
                    return True
                self._client.reconnect()
                self._client.loop_start()
            except Exception:
                self._cleanup_client()

        if not self._client:
            return self._create_client()

        return self._connected

    def _publish_sync(self, topic: str, payload: dict[str, Any]) -> bool:
        if not self._ensure_connected():
            return False
        try:
            if not self._client:
                return False
            message = json.dumps(payload, ensure_ascii=False)
            result = self._client.publish(topic, message, qos=1)
            return result.rc == 0
        except Exception:
            return False

    def _flush_buffer_sync(self) -> int:
        if not self._buffer or not self._ensure_connected():
            return 0
        sent = 0
        for msg_id, topic, payload in self._buffer.get_pending(limit=50):
            try:
                if not self._client:
                    break
                message = json.dumps(payload, ensure_ascii=False)
                result = self._client.publish(topic, message, qos=1)
                if result.rc == 0:
                    self._buffer.remove(msg_id)
                    sent += 1
                else:
                    break
            except Exception:
                break
        return sent

    async def send_telemetry(self, metrics: dict[str, Any]) -> bool:
        if not self._enabled:
            return False
        if not self.device_id:
            return False
        payload = {
            "device_id": self.device_id,
            "instance_hash": self.instance_hash,
            "version": self.version,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "interval_s": self._telemetry_interval_s,
            **metrics,
        }
        topic = f"oig/telemetry/{self.device_id}"
        loop = asyncio.get_event_loop()
        async with self._lock:
            success = await loop.run_in_executor(None, self._publish_sync, topic, payload)
            if success:
                now = time.time()
                if self._buffer and now - self._last_buffer_flush > 60.0:
                    self._last_buffer_flush = now
                    await loop.run_in_executor(None, self._flush_buffer_sync)
                return True
            if self._buffer and self._buffer.store(topic, payload):
                return True
            return False

    async def send_event(self, event_type: str, details: dict[str, Any] | None = None) -> bool:
        if not self._enabled:
            return False
        if not self.device_id:
            return False
        payload = {
            "device_id": self.device_id,
            "instance_hash": self.instance_hash,
            "version": self.version,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event_type": event_type,
            "details": details or {},
        }
        topic = f"oig/events/{self.device_id}"
        loop = asyncio.get_event_loop()
        async with self._lock:
            success = await loop.run_in_executor(None, self._publish_sync, topic, payload)
            if success:
                return True
            if self._buffer and self._buffer.store(topic, payload):
                return True
            return False

    async def event_error_cloud_timeout(self, cloud_host: str, timeout_s: float) -> bool:
        return await self.send_event("error_cloud_timeout", {"cloud_host": cloud_host, "timeout_s": timeout_s})

    async def event_error_cloud_disconnect(self, reason: str = "unknown") -> bool:
        return await self.send_event("error_cloud_disconnect", {"reason": reason})

    async def event_error_box_disconnect(self, box_peer: str) -> bool:
        return await self.send_event("error_box_disconnect", {"box_peer": box_peer})

    async def event_error_crc(self, frame_info: str) -> bool:
        return await self.send_event("error_crc", {"frame_info": frame_info})

    async def event_error_mqtt_local(self, broker: str, error: str) -> bool:
        return await self.send_event("error_mqtt_local", {"broker": broker, "error": error})

    async def event_warning_mode_fallback(self, from_mode: str, to_mode: str, reason: str = "") -> bool:
        return await self.send_event("warning_mode_fallback", {"from_mode": from_mode, "to_mode": to_mode, "reason": reason})

    async def event_box_reconnect(self, box_peer: str) -> bool:
        return await self.send_event("box_reconnect", {"box_peer": box_peer})

    async def event_cloud_reconnect(self) -> bool:
        return await self.send_event("cloud_reconnect", {})

    async def event_startup(self) -> bool:
        return await self.send_event("startup", {"instance_hash": self.instance_hash})

    async def event_shutdown(self) -> bool:
        return await self.send_event("shutdown", {})

    async def provision(self) -> bool:
        return await self.event_startup()

    def get_buffer_count(self) -> int:
        return self._buffer.count() if self._buffer else 0

    def disconnect(self) -> None:
        self._cleanup_client()
        if self._buffer:
            self._buffer.close()

    @property
    def is_ready(self) -> bool:
        return self._enabled and self._connected

    @property
    def is_buffering(self) -> bool:
        return self._enabled and not self._connected and self._buffer is not None
