#!/usr/bin/env python3
"""
Cloud session manager - drží perzistentní TCP spojení na cloud nezávisle na BOXu.

Zajišťuje:
- reconnect s backoffem
- serializaci request/response (jeden frame v letu)
- bezpečné zavření a reset spojení
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class CloudStats:
    connects: int = 0
    disconnects: int = 0
    errors: int = 0
    timeouts: int = 0


class CloudSessionManager:
    """Udržuje jedno TCP spojení na cloud a umožňuje synchronní send+recv."""

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout_s: float = 5.0,
        min_reconnect_s: float = 0.5,
        max_reconnect_s: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout_s = connect_timeout_s
        self.min_reconnect_s = min_reconnect_s
        self.max_reconnect_s = max_reconnect_s

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._conn_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._backoff_s = min_reconnect_s
        self._last_connect_attempt = 0.0
        self.stats = CloudStats()

    def is_connected(self) -> bool:
        w = self._writer
        return bool(w) and not w.is_closing()

    async def close(self) -> None:
        async with self._conn_lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def ensure_connected(self) -> None:
        if self.is_connected():
            return
        async with self._conn_lock:
            if self.is_connected():
                return

            now = time.time()
            since_last = now - self._last_connect_attempt
            if since_last < self._backoff_s:
                await asyncio.sleep(self._backoff_s - since_last)

            self._last_connect_attempt = time.time()
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.connect_timeout_s,
                )
                self.stats.connects += 1
                self._backoff_s = self.min_reconnect_s
                logger.debug(f"☁️ Připojeno k {self.host}:{self.port}")
            except Exception as e:
                self.stats.errors += 1
                await self._close_locked()
                self._backoff_s = min(self.max_reconnect_s, self._backoff_s * 2.0)
                raise e

    async def send_and_read_ack(
        self,
        data: bytes,
        ack_timeout_s: float,
        ack_max_bytes: int = 4096,
    ) -> bytes:
        """
        Pošle bytes na cloud a přečte odpověď (ACK).
        Serializuje komunikaci, aby se nemíchaly ACKy mezi framy.
        """
        async with self._io_lock:
            await self.ensure_connected()
            assert self._writer is not None
            assert self._reader is not None

            try:
                self._writer.write(data)
                await self._writer.drain()
                ack = await asyncio.wait_for(
                    self._reader.read(ack_max_bytes),
                    timeout=ack_timeout_s,
                )
                if not ack:
                    # Cloud ukončil spojení (EOF)
                    self.stats.disconnects += 1
                    await self.close()
                    raise ConnectionError("Cloud connection closed (EOF)")
                return ack
            except asyncio.TimeoutError:
                self.stats.timeouts += 1
                await self.close()
                raise
            except Exception:
                self.stats.errors += 1
                await self.close()
                raise

