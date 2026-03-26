#!/usr/bin/env python3
"""
SQLite frame capture pro OIG Proxy v2.

Portováno z v1 addon/oig-proxy/utils.py.
Používá background daemon thread + queue pro neblokující asyncio event loop.

Architektura:
  - capture_payload() je non-blocking: vloží tuple do queue.Queue(maxsize=5000)
  - Daemon thread odebírá z fronty, zapisuje do SQLite v batchích (200 nebo 0.5s)
  - WAL journal mode, NORMAL synchronous – rychlé a bezpečné
  - Pruning každých 600s na základě CAPTURE_RETENTION_DAYS
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import queue
import sqlite3
import threading
import time
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

_INSERT_SQL = (
    "INSERT INTO frames "
    "(ts, device_id, table_name, raw, raw_b64, parsed, direction, conn_id, peer, length) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)"
)


class _Empty:
    pass


_EMPTY = _Empty()


class FrameCapture:
    """
    SQLite frame capture s background writer threadem.

    Použití:
        fc = FrameCapture(db_path="/data/payloads.db", capture_raw_bytes=True, retention_days=7)
        fc.start()
        fc.capture(device_id, table, raw_text, raw_bytes, parsed_dict, direction, conn_id, peer, length)
        fc.stop()
    """

    def __init__(
        self,
        db_path: str = "/data/payloads.db",
        capture_raw_bytes: bool = False,
        retention_days: int = 7,
    ) -> None:
        self.db_path = db_path
        self.capture_raw_bytes = capture_raw_bytes
        self.retention_days = retention_days

        self._queue: queue.Queue[tuple[Any, ...] | _Empty | None] = queue.Queue(maxsize=5000)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._ensure_schema()
        self._thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name="capture-writer",
        )
        self._thread.start()
        logger.info("FrameCapture started: db=%s raw_bytes=%s retention=%dd",
                    self.db_path, self.capture_raw_bytes, self.retention_days)

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            try:
                self._queue.put(None, timeout=2.0)
            except queue.Full:
                pass
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("FrameCapture stopped")

    def capture(
        self,
        device_id: str | None,
        table: str | None,
        raw: str,
        raw_bytes: bytes | None,
        parsed: dict[str, Any],
        direction: str | None = None,
        conn_id: int | None = None,
        peer: str | None = None,
        length: int | None = None,
    ) -> None:
        try:
            ts = _iso_now()
            raw_b64: str | None = None
            if self.capture_raw_bytes and raw_bytes is not None:
                raw_b64 = base64.b64encode(raw_bytes).decode("ascii")
            values = (
                ts,
                device_id,
                table,
                raw,
                raw_b64,
                json.dumps(parsed, ensure_ascii=False),
                direction,
                conn_id,
                peer,
                length,
            )
            self._queue.put_nowait(values)
        except queue.Full:
            logger.debug("FrameCapture queue full – dropping frame")
        except Exception as exc:  # noqa: BLE001
            logger.debug("FrameCapture.capture error: %s", exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Vytvoří DB schema pokud neexistuje (volá se z start(), ne z writeru)."""
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            _configure_pragmas(conn)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS frames (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        TEXT,
                    device_id TEXT,
                    table_name TEXT,
                    raw       TEXT,
                    raw_b64   TEXT,
                    parsed    TEXT,
                    direction TEXT,
                    conn_id   INTEGER,
                    peer      TEXT,
                    length    INTEGER
                )
            """)
            # Backward compat: přidat chybějící sloupce
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(frames)")}
            for col_name, col_type in [
                ("raw_b64", "TEXT"),
                ("direction", "TEXT"),
                ("conn_id", "INTEGER"),
                ("peer", "TEXT"),
                ("length", "INTEGER"),
            ]:
                if col_name not in existing_cols:
                    with suppress(sqlite3.Error):
                        conn.execute(f"ALTER TABLE frames ADD COLUMN {col_name} {col_type}")
            conn.commit()
            conn.close()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("FrameCapture schema init failed: %s", exc)

    def _writer_loop(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            _configure_pragmas(conn)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("FrameCapture writer cannot open DB: %s", exc)
            return

        _SENTINEL = _EMPTY
        batch: list[Any] = []
        last_commit = time.monotonic()
        last_prune = 0.0

        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                item = _SENTINEL

            timed_out = item is _SENTINEL
            stop = item is None

            if not timed_out and not stop:
                batch.append(item)

            now = time.monotonic()
            if timed_out or stop or len(batch) >= 200 or (now - last_commit) >= 0.5:
                _commit_batch(conn, batch)
                batch.clear()
                last_commit = now

            if self.retention_days > 0 and (now - last_prune) >= 600:
                _prune_db(conn, self.retention_days)
                last_prune = now

            if stop:
                break

        with suppress(sqlite3.Error, OSError):
            conn.close()


def _configure_pragmas(conn: sqlite3.Connection) -> None:
    with suppress(sqlite3.Error):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA busy_timeout=2000")


def _commit_batch(conn: sqlite3.Connection, batch: list[tuple[Any, ...]]) -> None:
    if not batch:
        return
    try:
        conn.executemany(_INSERT_SQL, batch)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("FrameCapture batch write failed (dropping): %s", exc)
        with suppress(Exception):
            conn.rollback()


def _prune_db(conn: sqlite3.Connection, retention_days: int) -> None:
    if retention_days <= 0:
        return
    try:
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=retention_days)
        )
        cutoff_iso = cutoff.replace(microsecond=0).isoformat()
        cur = conn.execute("DELETE FROM frames WHERE ts < ?", (cutoff_iso,))
        deleted = cur.rowcount or 0
        conn.commit()
        if deleted:
            with suppress(sqlite3.Error):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            logger.debug("FrameCapture pruned %d old frames", deleted)
    except Exception as exc:  # noqa: BLE001
        logger.debug("FrameCapture prune failed: %s", exc)


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
