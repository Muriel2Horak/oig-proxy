#!/usr/bin/env python3
# pylint: disable=broad-exception-caught
"""
Telemetry client pro odesílání anonymizovaných metrik přes MQTT.

Fail-safe: pokud MQTT není dostupný, ukládá do SQLite bufferu.
Publikuje do: oig/telemetry/{device_id} a oig/events/{device_id}

Token-based authentication:
- On first run, requests JWT token from Registration API
- Token is stored in /data/telemetry_token.json
- Token is used as MQTT password (username = device_id)
- If token request fails, falls back to anonymous (backward compatible)
"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

import config

logger = logging.getLogger("oig.telemetry")

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    mqtt = None

BUFFER_MAX_MESSAGES = 1000
BUFFER_MAX_AGE_HOURS = 24
BUFFER_DB_PATH = Path("/data/telemetry_buffer.db")
WINDOW_METRICS_MAX_LOGS = 50
WINDOW_METRICS_MAX_EVENTS = 20
WINDOW_METRICS_MAX_STATE_CHANGES = 20


def _get_instance_hash() -> str:
    """Generate instance hash from SUPERVISOR_TOKEN or hostname."""
    supervisor_token = os.getenv("SUPERVISOR_TOKEN", "")
    if supervisor_token:
        return hashlib.sha256(supervisor_token.encode()).hexdigest()[:16]
    hostname = os.getenv("HOSTNAME", "unknown")
    return hashlib.sha256(hostname.encode()).hexdigest()[:16]


class WindowMetricsTracker:
    """Tracks logs, events, and state changes for telemetry window metrics."""

    def __init__(self):
        self._logs: deque = deque(maxlen=WINDOW_METRICS_MAX_LOGS)
        self._events: deque = deque(maxlen=WINDOW_METRICS_MAX_EVENTS)
        self._state_changes: deque = deque(maxlen=WINDOW_METRICS_MAX_STATE_CHANGES)
        self._lock = threading.Lock()

    def add_log(self, level: str, source: str, message: str) -> None:
        """Add a log entry."""
        with self._lock:
            self._logs.append({
                "timestamp": datetime.now().astimezone().isoformat(),
                "level": level,
                "source": source,
                "message": message
            })

    def add_event(self, event: str, details: str) -> None:
        """Add an event entry."""
        with self._lock:
            self._events.append({
                "timestamp": datetime.now().astimezone().isoformat(),
                "event": event,
                "details": details
            })

    def add_state_change(self, field: str, old_value: Any, new_value: Any) -> None:
        """Add a state change entry."""
        with self._lock:
            self._state_changes.append({
                "timestamp": datetime.now().astimezone().isoformat(),
                "field": field,
                "old": str(old_value),
                "new": str(new_value)
            })

    def get_window_metrics(self) -> dict[str, list]:
        """Return current window metrics and clear them."""
        with self._lock:
            metrics = {
                "logs": list(self._logs),
                "tbl_events": list(self._events),
                "state_changes": list(self._state_changes)
            }
            self._logs.clear()
            self._events.clear()
            self._state_changes.clear()
            return metrics


class LogCaptureHandler(logging.Handler):
    """Logging handler to capture logs for window metrics."""

    def __init__(self, tracker: WindowMetricsTracker):
        super().__init__()
        self._tracker = tracker

    def emit(self, record: logging.LogRecord) -> None:
        """Capture log record."""
        try:
            self._tracker.add_log(
                level=record.levelname,
                source=record.name,
                message=self.format(record)
            )
        except Exception:
            self.handleError(record)


class TelemetryBuffer:
    """SQLite-based message buffer for offline telemetry storage."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path if db_path is not None else BUFFER_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    retries INTEGER DEFAULT 0
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
            self._conn.commit()
            self._cleanup()
            count = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if count > 0:
                logger.debug("📡 Telemetry buffer: %d pending messages", count)
        except Exception as e:
            logger.warning("Failed to initialize telemetry buffer: %s", e)
            self._conn = None

    def _cleanup(self) -> None:
        """Remove old and excess messages."""
        if not self._conn:
            return
        try:
            cutoff = time.time() - (BUFFER_MAX_AGE_HOURS * 3600)
            self._conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
            self._conn.execute("""
                DELETE FROM messages WHERE id NOT IN (
                    SELECT id FROM messages ORDER BY timestamp DESC LIMIT ?
                )
            """, (BUFFER_MAX_MESSAGES,))
            self._conn.commit()
        except Exception:  # nosec B110 - cleanup, failure is acceptable
            pass

    def store(self, topic: str, payload: dict) -> bool:
        """Store message in buffer. Returns True on success."""
        if not self._conn:
            return False
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
                (topic, payload_json, time.time())
            )
            self._conn.commit()
            count = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if count > BUFFER_MAX_MESSAGES:
                self._cleanup()
            return True
        except Exception:
            return False

    def get_pending(self, limit: int = 50) -> list[tuple[int, str, dict]]:
        """Get pending messages. Returns list of (id, topic, payload)."""
        if not self._conn:
            return []
        try:
            cursor = self._conn.execute(
                "SELECT id, topic, payload FROM messages ORDER BY timestamp ASC LIMIT ?",
                (limit,)
            )
            results = []
            for row in cursor:
                try:
                    payload = json.loads(row[2])
                    results.append((row[0], row[1], payload))
                except json.JSONDecodeError:
                    self._conn.execute("DELETE FROM messages WHERE id = ?", (row[0],))
            return results
        except Exception:
            return []

    def remove(self, message_id: int) -> None:
        """Remove message from buffer after successful send."""
        if self._conn:
            try:
                self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                self._conn.commit()
            except Exception:  # nosec B110 - cleanup, failure is acceptable
                pass

    def count(self) -> int:
        """Get number of pending messages."""
        if not self._conn:
            return 0
        try:
            return self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        except Exception:
            return 0

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:  # nosec B110 - cleanup, failure is acceptable
                pass
            self._conn = None


class TelemetryClient:  # pylint: disable=too-many-instance-attributes
    """
    MQTT telemetry client for sending metrics to central server.

    All operations are fail-safe - errors are logged but never propagate.
    When MQTT is unavailable, messages are buffered to SQLite.
    
    Token-based authentication:
    - Requests JWT token from Registration API on first run
    - Token stored in /data/telemetry_token.json
    - Falls back to anonymous if token unavailable (backward compatible)
    """

    def __init__(self, device_id: str, version: str):
        self.device_id = device_id
        self.version = version
        self.instance_hash = _get_instance_hash()
        self._enabled = config.TELEMETRY_ENABLED and bool(device_id) and MQTT_AVAILABLE
        self._consecutive_errors = 0
        self._client: Optional[Any] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._buffer = TelemetryBuffer() if self._enabled else None
        self._last_buffer_flush = 0.0
        self._mqtt_host, self._mqtt_port = self._parse_mqtt_url(config.TELEMETRY_MQTT_BROKER)
        
        # JWT token for MQTT authentication
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._load_token()
        
        # Window metrics tracker
        self._window_metrics = WindowMetricsTracker()
        
        # Attach log capture handler to oig logger
        if self._enabled:
            oig_logger = logging.getLogger("oig")
            log_handler = LogCaptureHandler(self._window_metrics)
            log_handler.setLevel(logging.WARNING)  # Capture WARNING and above
            oig_logger.addHandler(log_handler)

        logger.warning("📡 TelemetryClient init: enabled=%s (TELEMETRY_ENABLED=%s, device_id=%s, MQTT_AVAILABLE=%s)",
                      self._enabled, config.TELEMETRY_ENABLED, device_id, MQTT_AVAILABLE)
        if self._enabled:
            logger.debug("📡 Telemetry enabled, token=%s", "present" if self._token else "none")

    def _load_token(self) -> None:
        """Load JWT token from persistent storage."""
        try:
            token_path = Path(config.TELEMETRY_TOKEN_PATH)
            if token_path.exists():
                with open(token_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._token = data.get("token")
                    self._token_expires = data.get("expires", 0)
                    # Check if token expired
                    if self._token_expires > 0 and time.time() > self._token_expires:
                        logger.info("📡 Stored token expired, will request new one")
                        self._token = None
                        self._token_expires = 0
                    elif self._token:
                        logger.debug("📡 Loaded token from storage (expires in %d hours)", 
                                    (self._token_expires - time.time()) / 3600 if self._token_expires else 0)
        except Exception as e:
            logger.debug("📡 Failed to load token: %s", e)

    def _save_token(self, token: str, expires_in: int) -> None:
        """Save JWT token to persistent storage."""
        try:
            token_path = Path(config.TELEMETRY_TOKEN_PATH)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "token": token,
                "expires": time.time() + expires_in - 3600,  # Refresh 1h before expiry
                "device_id": self.device_id,
                "issued": datetime.utcnow().isoformat()
            }
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            self._token = token
            self._token_expires = data["expires"]
            logger.info("📡 Token saved (expires in %d days)", expires_in // 86400)
        except Exception as e:
            logger.warning("📡 Failed to save token: %s", e)

    async def _request_token(self) -> bool:
        """Request JWT token from Registration API."""
        if not AIOHTTP_AVAILABLE:
            logger.debug("📡 aiohttp not available, skipping token request")
            return False
        
        try:
            url = config.TELEMETRY_REGISTRATION_URL
            headers = {
                "X-Client-Secret": config.TELEMETRY_CLIENT_SECRET,
                "Content-Type": "application/json"
            }
            payload = {
                "device_id": self.device_id,
                "instance_hash": self.instance_hash,
                "version": self.version
            }
            
            logger.debug("📡 Requesting token from %s", url)
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, 
                                        timeout=aiohttp.ClientTimeout(total=10),
                                        ssl=False) as response:
                    if response.status == 200:
                        data = await response.json()
                        token = data.get("token")
                        expires_in = data.get("expires_in", 30 * 24 * 3600)
                        if token:
                            self._save_token(token, expires_in)
                            logger.info("📡 Token obtained successfully")
                            return True
                    else:
                        text = await response.text()
                        logger.warning("📡 Token request failed: %d %s", response.status, text[:100])
        except asyncio.TimeoutError:
            logger.debug("📡 Token request timeout")
        except Exception as e:
            logger.debug("📡 Token request error: %s", e)
        
        return False

    async def _ensure_token(self) -> None:
        """Ensure we have a valid token, requesting one if needed."""
        # Token refresh threshold: 7 days before expiry
        REFRESH_THRESHOLD = 7 * 24 * 3600
        
        needs_token = (
            self._token is None or 
            (self._token_expires > 0 and time.time() > self._token_expires - REFRESH_THRESHOLD)
        )
        
        if needs_token:
            logger.debug("📡 Token missing or expiring soon, requesting new one")
            await self._request_token()

    @staticmethod
    def _parse_mqtt_url(url: str) -> tuple[str, int]:
        """Parse MQTT URL into host and port."""
        url = url.replace("mqtt://", "").replace("tcp://", "")
        if ":" in url:
            host, port_str = url.rsplit(":", 1)
            try:
                return host, int(port_str)
            except ValueError:
                pass
        return url, 1883

    def _create_client(self) -> bool:
        """Create MQTT client (synchronous, called from thread)."""
        if not MQTT_AVAILABLE or not mqtt:
            return False
        try:
            client_id = f"oig-proxy-{self.device_id}-{self.instance_hash[:8]}"
            self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311,
                                       callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

            # Set authentication if token available
            if self._token:
                self._client.username_pw_set(self.device_id, self._token)
                logger.debug("📡 Using token auth for MQTT")
            
            def on_connect(_client, _userdata, _flags, rc, _properties=None):
                if rc == 0:
                    self._connected = True
                    self._consecutive_errors = 0
                    logger.debug("📡 Telemetry MQTT connected")
                elif rc == 5:  # Auth failed
                    logger.warning("📡 MQTT auth failed, token may be invalid")
                    self._token = None
                    self._token_expires = 0

            def on_disconnect(_client, _userdata, _disconnect_flags, _rc, _properties=None):
                self._connected = False
                logger.debug("📡 Telemetry MQTT disconnected")

            self._client.on_connect = on_connect
            self._client.on_disconnect = on_disconnect
            self._client.connect(self._mqtt_host, self._mqtt_port, keepalive=60)
            self._client.loop_start()

            for _ in range(50):
                if self._connected:
                    return True
                time.sleep(0.1)
            logger.warning("📡 Telemetry MQTT connection timeout (5s) to %s:%s", 
                          self._mqtt_host, self._mqtt_port)
            return False
        except Exception as e:
            logger.warning("📡 Telemetry MQTT connection error: %s", e)
            return False

    def _ensure_connected(self) -> bool:
        """Ensure MQTT client is connected."""
        if self._connected and self._client:
            return True
        return self._create_client()

    def _publish_sync(self, topic: str, payload: dict) -> bool:
        """Publish message synchronously."""
        logger.debug("📡 _publish_sync: attempting to ensure connection")
        if not self._ensure_connected():
            logger.debug("📡 _publish_sync: connection failed")
            return False
        try:
            message = json.dumps(payload, ensure_ascii=False)
            result = self._client.publish(topic, message, qos=1)
            logger.debug("📡 _publish_sync: publish result rc=%s", result.rc)
            return result.rc == 0
        except Exception as e:
            logger.debug("📡 _publish_sync: exception %s", e)
            return False

    def _flush_buffer_sync(self) -> int:
        """Flush buffered messages. Returns number of successfully sent messages."""
        if not self._buffer or not self._ensure_connected():
            return 0
        sent = 0
        pending = self._buffer.get_pending(limit=50)
        for msg_id, topic, payload in pending:
            try:
                message = json.dumps(payload, ensure_ascii=False)
                result = self._client.publish(topic, message, qos=1)
                if result.rc == 0:
                    self._buffer.remove(msg_id)
                    sent += 1
                else:
                    break
            except Exception:
                break
        if sent > 0:
            logger.debug("📡 Flushed %d buffered messages", sent)
        return sent

    async def send_telemetry(self, metrics: dict) -> bool:
        """
        Send telemetry metrics via MQTT.

        If MQTT unavailable, stores in buffer for later retry.
        Returns True on success or successful buffering.
        """
        logger.debug("📡 send_telemetry called: enabled=%s, device_id=%s, metrics=%s", 
                      self._enabled, self.device_id, list(metrics.keys()))
        if not self._enabled:
            logger.debug("📡 send_telemetry: telemetry is DISABLED, returning False")
            return False
        
        # Ensure we have a valid token (request if missing or expiring soon)
        await self._ensure_token()
        
        # Get window metrics
        window_metrics = self._window_metrics.get_window_metrics()
        
        payload = {
            "device_id": self.device_id,
            "instance_hash": self.instance_hash,
            "version": self.version,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "interval_s": config.TELEMETRY_INTERVAL_S,
            "window_metrics": window_metrics,
            **metrics
        }
        topic = f"oig/telemetry/{self.device_id}"
        loop = asyncio.get_event_loop()
        async with self._lock:
            success = await loop.run_in_executor(None, self._publish_sync, topic, payload)
            if success:
                self._consecutive_errors = 0
                logger.info("📡 Telemetry sent: device=%s mode=%s uptime=%ss",
                            self.device_id, metrics.get("mode", "-"), metrics.get("uptime_s", "-"))
                now = time.time()
                if self._buffer and now - self._last_buffer_flush > 60.0:
                    self._last_buffer_flush = now
                    await loop.run_in_executor(None, self._flush_buffer_sync)
                return True
            # Failed to send - increment error count and buffer
            self._consecutive_errors += 1
            if self._buffer:
                if self._buffer.store(topic, payload):
                    logger.info("📡 Telemetry buffered (MQTT unavailable, errors=%d)", 
                               self._consecutive_errors)
                    return True
                logger.warning("📡 Telemetry buffer failed")
            else:
                logger.warning("📡 Telemetry send failed (no buffer available)")
            return False

    async def send_event(self, event_type: str, details: Optional[dict] = None) -> bool:
        """
        Send one-time event via MQTT.

        Events are also buffered if MQTT unavailable.
        """
        if not self._enabled:
            return False
        payload = {
            "device_id": self.device_id,
            "instance_hash": self.instance_hash,
            "version": self.version,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event_type": event_type,
            "details": details or {}
        }
        topic = f"oig/events/{self.device_id}"
        loop = asyncio.get_event_loop()
        async with self._lock:
            success = await loop.run_in_executor(None, self._publish_sync, topic, payload)
            if success:
                logger.debug("📡 Event sent: %s", event_type)
                return True
            # Failed to send - buffer for later
            if self._buffer:
                if self._buffer.store(topic, payload):
                    logger.debug("📡 Event buffered: %s (MQTT unavailable)", event_type)
                    return True
            return False

    def track_event(self, event: str, details: str = "") -> None:
        """Track an event for window metrics."""
        if self._enabled:
            self._window_metrics.add_event(event, details)

    def track_state_change(self, field: str, old_value: Any, new_value: Any) -> None:
        """Track a state change for window metrics."""
        if self._enabled:
            self._window_metrics.add_state_change(field, old_value, new_value)

    # Convenience methods for common error events

    async def event_error_cloud_timeout(self, cloud_host: str, timeout_s: float) -> bool:
        """Send cloud timeout error event."""
        return await self.send_event("error_cloud_timeout", {
            "cloud_host": cloud_host, "timeout_s": timeout_s
        })

    async def event_error_cloud_disconnect(self, reason: str = "unknown") -> bool:
        """Send cloud disconnect error event."""
        return await self.send_event("error_cloud_disconnect", {"reason": reason})

    async def event_error_box_disconnect(self, box_peer: str) -> bool:
        """Send BOX disconnect error event."""
        return await self.send_event("error_box_disconnect", {"box_peer": box_peer})

    async def event_error_crc(self, frame_info: str) -> bool:
        """Send CRC error event."""
        return await self.send_event("error_crc", {"frame_info": frame_info})

    async def event_error_mqtt_local(self, broker: str, error: str) -> bool:
        """Send local MQTT error event."""
        return await self.send_event("error_mqtt_local", {"broker": broker, "error": error})

    async def event_warning_mode_fallback(
        self, from_mode: str, to_mode: str, reason: str = ""
    ) -> bool:
        """Send mode fallback warning event."""
        return await self.send_event("warning_mode_fallback", {
            "from_mode": from_mode, "to_mode": to_mode, "reason": reason
        })

    async def event_box_reconnect(self, box_peer: str) -> bool:
        """Send BOX reconnect event."""
        return await self.send_event("box_reconnect", {"box_peer": box_peer})

    async def event_cloud_reconnect(self) -> bool:
        """Send cloud reconnect event."""
        return await self.send_event("cloud_reconnect", {})

    async def event_startup(self) -> bool:
        """Send startup event."""
        return await self.send_event("startup", {"instance_hash": self.instance_hash})

    async def event_shutdown(self) -> bool:
        """Send shutdown event."""
        return await self.send_event("shutdown", {})

    async def provision(self) -> bool:
        """Send startup event (replaces HTTP provisioning)."""
        return await self.event_startup()

    def reset_error_count(self) -> None:
        """Reset consecutive error counter."""
        self._consecutive_errors = 0

    def get_buffer_count(self) -> int:
        """Get number of messages in buffer."""
        return self._buffer.count() if self._buffer else 0

    def disconnect(self) -> None:
        """Disconnect MQTT client and close buffer."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # nosec B110 - cleanup, failure is acceptable
                pass
            self._client = None
            self._connected = False
        if self._buffer:
            self._buffer.close()

    @property
    def is_ready(self) -> bool:
        """Check if telemetry is enabled and connected."""
        return self._enabled and self._connected

    @property
    def is_buffering(self) -> bool:
        """Check if messages are being buffered (MQTT unavailable)."""
        return self._enabled and not self._connected and self._buffer is not None


_TELEMETRY_CLIENT: Optional[TelemetryClient] = None


def init_telemetry(device_id: str, version: str) -> TelemetryClient:
    """Initialize global telemetry client."""
    global _TELEMETRY_CLIENT  # pylint: disable=global-statement
    _TELEMETRY_CLIENT = TelemetryClient(device_id, version)
    return _TELEMETRY_CLIENT


def get_telemetry_client() -> Optional[TelemetryClient]:
    """Get global telemetry client instance."""
    return _TELEMETRY_CLIENT
