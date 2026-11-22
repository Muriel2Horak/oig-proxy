"""Persist frames awaiting delivery to the cloud."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import logging
import sqlite3
from typing import Any, TypeVar


_Result = TypeVar("_Result")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacklogEntry:
    """One committed frame, retaining its original bytes and origin."""

    sequence: int
    device_id: str
    frame: bytes


class CloudBacklog:
    """A durable SQLite FIFO for each device."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._operations: set[asyncio.Task[Any]] = set()
        self._queued_devices: set[str] = set()
        self._pending_writes: Counter[str] = Counter()

    @property
    def available(self) -> bool:
        """Report whether storage opened successfully."""
        return self._connection is not None

    @property
    def pending(self) -> bool:
        """Check whether any device has queued measurements."""
        return bool(self._queued_devices or self._pending_writes)

    def pending_for(self, device_id: str | None) -> bool:
        """Check for queued or committing measurements from this device."""
        return device_id is not None and (
            device_id in self._queued_devices or device_id in self._pending_writes
        )

    async def open(self) -> None:
        """Open the database and load the pending state before serving traffic."""
        await self._run(self._open, on_success=self._queued_devices.update)

    async def close(self) -> None:
        """Complete outstanding operations and release the database connection."""
        await self._run(self._close, on_success=lambda _: self._queued_devices.clear())

    async def put(self, device_id: str, frame: bytes) -> None:
        """Commit the original frame once per device while it remains queued."""
        await self._run(
            lambda: self._put(device_id, frame), pending_device=device_id,
            on_success=lambda _: self._queued_devices.add(device_id),
        )

    async def oldest(self, device_id: str) -> BacklogEntry | None:
        """Read a device's oldest frame without removing it."""
        return await self._run(lambda: self._oldest(device_id))

    async def acknowledge(self, device_id: str, sequence: int) -> bool:
        """Remove only the acknowledged head of the specified device's FIFO."""
        def update_pending(result: tuple[bool, bool]) -> None:
            if not result[1]:
                self._queued_devices.discard(device_id)

        deleted, _ = await self._run(
            lambda: self._acknowledge(device_id, sequence), on_success=update_pending,
        )
        return deleted

    async def _run(
        self, operation: Callable[[], _Result], *,
        pending_device: str | None = None, on_success: Callable[[_Result], None] | None = None,
    ) -> _Result:
        async def serialized() -> _Result:
            try:
                async with self._lock:
                    result = await asyncio.to_thread(operation)
                    if on_success is not None:
                        on_success(result)
                    return result
            finally:
                if pending_device is not None:
                    self._pending_writes[pending_device] -= 1
                    if not self._pending_writes[pending_device]:
                        del self._pending_writes[pending_device]

        # A cancelled caller cannot interrupt SQLite or release its lock early.
        if pending_device is not None:
            self._pending_writes[pending_device] += 1
        task = asyncio.create_task(serialized())
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.add_done_callback(self._report_abandoned_error)
            raise

    @staticmethod
    def _report_abandoned_error(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOGGER.error("Cloud backlog operation failed after caller cancellation: %s", error)

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Cloud backlog database is not open")
        return self._connection

    def _open(self) -> set[str]:
        if self._connection is not None:
            return self._queued_devices.copy()
        connection = sqlite3.connect(self._path, check_same_thread=False)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS cloud_backlog (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    frame BLOB NOT NULL,
                    frame_key BLOB NOT NULL,
                    UNIQUE (device_id, frame_key)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS cloud_backlog_device_sequence ON cloud_backlog (device_id, sequence)
            """)
            devices = connection.execute("SELECT DISTINCT device_id FROM cloud_backlog")
            queued_devices = {device for (device,) in devices}
        except sqlite3.Error:
            connection.close()
            raise
        self._connection = connection
        return queued_devices

    def _close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _put(self, device_id: str, frame: bytes) -> None:
        connection = self._db()
        with connection:
            connection.execute("""
                INSERT INTO cloud_backlog (device_id, frame, frame_key)
                VALUES (?, ?, ?) ON CONFLICT (device_id, frame_key) DO NOTHING
            """, (device_id, frame, frame.rstrip(b"\r\n")))
    def _oldest(self, device_id: str) -> BacklogEntry | None:
        row = self._db().execute("""
            SELECT sequence, device_id, frame FROM cloud_backlog WHERE device_id = ? ORDER BY sequence LIMIT 1
        """, (device_id,)).fetchone()
        return BacklogEntry(*row) if row is not None else None

    def _acknowledge(self, device_id: str, sequence: int) -> tuple[bool, bool]:
        connection = self._db()
        with connection:
            deleted = connection.execute("""
                DELETE FROM cloud_backlog
                WHERE device_id = ? AND sequence = ?
                  AND sequence = (SELECT MIN(sequence) FROM cloud_backlog WHERE device_id = ?)
            """, (device_id, sequence, device_id)).rowcount
            has_rows = connection.execute(
                "SELECT 1 FROM cloud_backlog WHERE device_id = ? LIMIT 1", (device_id,),
            ).fetchone() is not None
        return deleted == 1, has_rows
