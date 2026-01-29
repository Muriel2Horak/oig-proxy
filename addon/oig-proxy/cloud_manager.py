#!/usr/bin/env python3
"""
Cloud management - CloudQueue, CloudHealthChecker.
"""

import asyncio
import logging
import os
import sqlite3
import time
from typing import Any

from config import (
    CLOUD_HEALTH_CHECK_INTERVAL,
    CLOUD_HEALTH_CHECK_TIMEOUT,
    CLOUD_HEALTH_FAIL_THRESHOLD,
    CLOUD_HEALTH_SUCCESS_THRESHOLD,
    CLOUD_QUEUE_DB_PATH,
    CLOUD_QUEUE_MAX_SIZE,
    TARGET_PORT,
    TARGET_SERVER,
)
from utils import iso_now, resolve_cloud_host

logger = logging.getLogger(__name__)


# ============================================================================
# Cloud Queue - Persistentní fronta pro offline režim
# ============================================================================

class DisabledCloudQueue:  # pylint: disable=missing-function-docstring,unused-argument
    """No-op CloudQueue when queueing is disabled."""

    def __init__(self):
        self.lock = asyncio.Lock()

    async def add(self, frame_data: bytes, table_name: str, device_id: str | None) -> bool:
        return False

    async def get_next(self) -> tuple[int, str, bytes] | None:
        return None

    async def next_ready_in(self) -> float | None:
        return None

    async def defer(self, frame_id: int, delay_s: float = 60.0) -> bool:
        return False

    async def remove(self, frame_id: int) -> bool:
        return False

    def size(self) -> int:
        return 0

    def oldest_age(self) -> float | None:
        return None

    def clear(self) -> None:
        return None


class CloudQueue:
    """Persistentní fronta pro cloud frames (SQLite)."""

    def __init__(
        self,
        db_path: str = CLOUD_QUEUE_DB_PATH,
        max_size: int = CLOUD_QUEUE_MAX_SIZE
    ):
        self.db_path = db_path
        self.max_size = max_size
        self._has_frame_bytes = False
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
                table_name TEXT NOT NULL,
                frame_data TEXT NOT NULL,
                frame_bytes BLOB,
                device_id TEXT,
                queued_at TEXT NOT NULL
            )
        """)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON queue(timestamp)"
        )
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(queue)")}
            if "frame_bytes" not in cols:
                conn.execute("ALTER TABLE queue ADD COLUMN frame_bytes BLOB")
                conn.commit()
                cols = {row[1] for row in conn.execute("PRAGMA table_info(queue)")}
            self._has_frame_bytes = "frame_bytes" in cols
        except sqlite3.Error as exc:
            logger.warning("CloudQueue: Schema migration failed: %s", exc)
        conn.commit()

        logger.info("CloudQueue: Initialized (%s)", self.db_path)
        return conn

    async def add(
        self,
        frame_data: bytes,
        table_name: str,
        device_id: str | None
    ) -> bool:
        """Přidá frame do fronty (FIFO)."""
        async with self.lock:
            try:
                # Check size limit
                size = self.size()
                if size >= self.max_size:
                    # Drop oldest
                    self.conn.execute(
                        "DELETE FROM queue WHERE id IN "
                        "(SELECT id FROM queue ORDER BY timestamp, id LIMIT 1)"
                    )
                    logger.warning(
                        "CloudQueue full (%s), dropped oldest frame",
                        self.max_size,
                    )

                frame_text = frame_data.decode("utf-8", errors="replace")
                if self._has_frame_bytes:
                    self.conn.execute(
                        "INSERT INTO queue "
                        "(timestamp, table_name, frame_data, frame_bytes, device_id, queued_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            time.time(),
                            table_name,
                            frame_text,
                            frame_data,
                            device_id,
                            iso_now()
                        )
                    )
                else:
                    self.conn.execute(
                        "INSERT INTO queue "
                        "(timestamp, table_name, frame_data, device_id, queued_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (time.time(), table_name, frame_text, device_id, iso_now())
                    )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                logger.error("CloudQueue: Add failed: %s", exc)
                return False

    async def get_next(self) -> tuple[int, str, bytes] | None:
        """Vrátí další frame (id, table_name, frame_bytes) nebo None.

        Respektuje `timestamp` jako `not_before` (defer); pokud je ve frontě
        něco odloženého do budoucna, vrátí `None`.
        """
        async with self.lock:
            try:
                now = time.time()
                if self._has_frame_bytes:
                    cursor = self.conn.execute(
                        "SELECT id, table_name, frame_bytes, frame_data FROM queue "
                        "WHERE timestamp <= ? "
                        "ORDER BY timestamp, id LIMIT 1",
                        (now,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        return None
                    frame_id, table_name, frame_bytes, frame_text = row
                    if frame_bytes is not None:
                        return int(frame_id), str(table_name), bytes(frame_bytes)
                    # Fallback: staré záznamy jen s textem
                    return int(frame_id), str(table_name), str(frame_text).encode("utf-8")

                cursor = self.conn.execute(
                    "SELECT id, table_name, frame_data FROM queue "
                    "WHERE timestamp <= ? "
                    "ORDER BY timestamp, id LIMIT 1",
                    (now,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                frame_id, table_name, frame_text = row
                return int(frame_id), str(table_name), str(frame_text).encode("utf-8")
            except sqlite3.Error as exc:
                logger.error("CloudQueue: Get next failed: %s", exc)
                return None

    async def next_ready_in(self) -> float | None:
        """Za jak dlouho je nejbližší frame připravený k replay (sekundy)."""
        async with self.lock:
            try:
                cursor = self.conn.execute("SELECT MIN(timestamp) FROM queue")
                ts = cursor.fetchone()[0]
                if ts is None:
                    return None
                return max(0.0, float(ts) - time.time())
            except sqlite3.Error:
                return None

    async def defer(self, frame_id: int, delay_s: float = 60.0) -> bool:
        """Posune frame v queue (aby neblokoval FIFO při opakovaných chybách)."""
        async with self.lock:
            try:
                self.conn.execute(
                    "UPDATE queue SET timestamp=? WHERE id=?",
                    (time.time() + float(delay_s), frame_id),
                )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                logger.error("CloudQueue: Defer failed: %s", exc)
                return False

    async def remove(self, frame_id: int) -> bool:
        """Odstraní frame po úspěšném odeslání."""
        async with self.lock:
            try:
                self.conn.execute("DELETE FROM queue WHERE id = ?", (frame_id,))
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                logger.error("CloudQueue: Remove failed: %s", exc)
                return False

    def size(self) -> int:
        """Vrátí počet frames ve frontě."""
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM queue")
            return cursor.fetchone()[0]
        except sqlite3.Error as exc:
            logger.error("CloudQueue: Size failed: %s", exc)
            return 0

    def oldest_age(self) -> float | None:
        """Vrátí stáří nejstaršího frame v sekundách."""
        try:
            cursor = self.conn.execute("SELECT MIN(timestamp) FROM queue")
            oldest = cursor.fetchone()[0]
            return time.time() - oldest if oldest else None
        except sqlite3.Error:
            return None

    def clear(self) -> None:
        """Vymaže celou frontu."""
        try:
            self.conn.execute("DELETE FROM queue")
            self.conn.commit()
            logger.info("CloudQueue: Cleared")
        except sqlite3.Error as exc:
            logger.error("CloudQueue: Clear failed: %s", exc)


# ============================================================================
# Cloud Health Checker - Monitoring cloud dostupnosti
# ============================================================================

class DisabledCloudHealthChecker:
    """No-op health checker when cloud health monitoring is disabled.

    Vždy reportuje cloud jako online (is_online=True), ale neprovádí žádné
    TCP spojení na cloud - cloud tak nevidí žádný síťový provoz.
    """

    is_online = True
    last_check_time = 0.0
    consecutive_failures = 0
    consecutive_successes = 0
    fail_threshold = 3

    def set_mode_callback(self, callback):  # pylint: disable=unused-argument
        """No-op - disabled checker nevyvolává callbacky."""

    async def start(self):
        """No-op - disabled checker nespouští background tasky."""
        logger.info("CloudHealthChecker: Disabled (stealth mode)")

    async def stop(self):
        """No-op."""


class CloudHealthChecker:  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-instance-attributes
    """Monitoruje zdraví cloud spojení a řídí režimy."""

    def __init__(
        self,
        host: str = TARGET_SERVER,
        port: int = TARGET_PORT,
        check_interval: int = CLOUD_HEALTH_CHECK_INTERVAL,
        timeout: float = CLOUD_HEALTH_CHECK_TIMEOUT,
        fail_threshold: int = CLOUD_HEALTH_FAIL_THRESHOLD,
        success_threshold: int = CLOUD_HEALTH_SUCCESS_THRESHOLD
    ):
        self.host = host
        self.port = port
        self.check_interval = check_interval
        self.timeout = timeout
        self.fail_threshold = fail_threshold
        self.success_threshold = success_threshold

        # State
        self.is_online = True  # Optimistický start
        self.last_check_time = 0.0
        self.consecutive_failures = 0
        self.consecutive_successes = 0

        # Callback pro notifikaci změn stavu
        self._mode_change_callback = None

        # Background task
        self._health_check_task: asyncio.Task[Any] | None = None

    def set_mode_callback(self, callback):
        """Nastaví callback pro notifikaci změn stavu.

        Callback bude volán s parametrem: "cloud_down" nebo "cloud_recovered"
        """
        self._mode_change_callback = callback

    async def check_health(self) -> bool:
        """Zkontroluje cloud dostupnost (TCP handshake)."""
        try:
            target_host = resolve_cloud_host(self.host)
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, self.port),
                timeout=self.timeout
            )
            writer.close()
            await writer.wait_closed()

            self.consecutive_successes += 1
            self.consecutive_failures = 0
            self.last_check_time = time.time()

            # Přechod offline → online (po N úspěších)
            if (not self.is_online and
                self.consecutive_successes >= self.success_threshold):
                logger.info("☁️ Cloud ONLINE - recovered!")
                self.is_online = True
                # Notify
                if self._mode_change_callback:
                    await self._mode_change_callback("cloud_recovered")

            return True

        except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            self.last_check_time = time.time()

            # Přechod online → offline (po N selháních)
            if (self.is_online and
                self.consecutive_failures >= self.fail_threshold):
                logger.warning("☁️ Cloud OFFLINE - %s", exc)
                self.is_online = False
                # Notify
                if self._mode_change_callback:
                    await self._mode_change_callback("cloud_down")

            return False

    async def health_check_loop(self):
        """Periodicky kontroluje cloud spojení."""
        logger.info(
            "CloudHealthChecker: Started (interval %ss)",
            self.check_interval,
        )

        while True:
            await asyncio.sleep(self.check_interval)
            await self.check_health()

    async def start(self):
        """Spustí health check jako background task."""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(
                self.health_check_loop()
            )
            logger.debug("CloudHealthChecker: Task started")
