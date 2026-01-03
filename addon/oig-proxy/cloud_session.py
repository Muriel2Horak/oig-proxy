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
from contextlib import suppress
from dataclasses import dataclass

from utils import resolve_cloud_host


logger = logging.getLogger(__name__)

_WARN_THROTTLE_S = 30.0
_READ_CHUNK_BYTES = 4096
_DEFAULT_ACK_MAX_BYTES = 4096


@dataclass
class CloudStats:
    """Statistiky cloud spojení."""
    connects: int = 0
    disconnects: int = 0
    errors: int = 0
    timeouts: int = 0


class CloudSessionManager:  # pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments
    """Udržuje jedno TCP spojení na cloud a umožňuje synchronní send+recv."""

    def __init__(
        self,
        host: str,
        port: int,
        stats: CloudStats | None = None,
        connect_timeout_s: float = 5.0,
        min_reconnect_s: float = 0.5,
        max_reconnect_s: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.stats = stats or CloudStats()
        self.connect_timeout_s = connect_timeout_s
        self.min_reconnect_s = min_reconnect_s
        self.max_reconnect_s = max_reconnect_s

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._conn_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._backoff_s = min_reconnect_s
        self._last_connect_attempt = 0.0
        self._last_warn_ts = 0.0
        self._rx_buf = bytearray()

    def is_connected(self) -> bool:
        """Vrátí True, pokud je aktivní TCP spojení."""
        writer = self._writer
        return writer is not None and not writer.is_closing()

    async def close(self, *, count_disconnect: bool = False) -> None:
        """Uzavře cloud spojení (bez vyhazování výjimek)."""
        async with self._conn_lock:
            await self._close_locked(count_disconnect=count_disconnect)

    async def _close_locked(self, *, count_disconnect: bool = False) -> None:
        """Uzavře spojení uvnitř locku a vynuluje interní stav."""
        writer = self._writer
        if writer is not None:
            if count_disconnect and not writer.is_closing():
                self.stats.disconnects += 1
            with suppress(Exception):
                writer.close()
                await writer.wait_closed()
        self._reader = None
        self._writer = None
        self._rx_buf.clear()

    @staticmethod
    def _extract_one_xml_frame(buf: bytearray) -> bytes | None:
        """Vrátí první kompletní `<Frame>...</Frame>` z bufferu, jinak None."""
        end_tag = b"</Frame>"
        end_idx = buf.find(end_tag)
        if end_idx < 0:
            return None

        frame_end = end_idx + len(end_tag)
        # Volitelný CRLF za frame (BOX typicky posílá \r\n)
        if len(buf) > frame_end:
            if buf[frame_end:frame_end + 2] == b"\r\n":
                frame_end += 2
            elif buf[frame_end:frame_end + 1] == b"\n":
                frame_end += 1
            elif buf[frame_end:frame_end + 1] == b"\r":
                # Pokud máme jen '\r' bez dalšího bajtu, počkáme (může být rozseknuté CRLF)
                if len(buf) < frame_end + 2:
                    return None
                frame_end += 1

        frame = bytes(buf[:frame_end])
        del buf[:frame_end]
        return frame

    async def _read_one_ack_frame(
        self,
        *,
        ack_timeout_s: float,
        ack_max_bytes: int,
    ) -> bytes:
        reader = self._reader
        if reader is None:
            raise ConnectionError("Cloud connection not established")

        # Nejprve zkus vyzobnout z interního bufferu.
        existing = self._extract_one_xml_frame(self._rx_buf)
        if existing is not None:
            return existing

        async def _read_until_frame() -> bytes:
            while True:
                chunk = await reader.read(_READ_CHUNK_BYTES)
                if not chunk:
                    return b""
                self._rx_buf.extend(chunk)
                frame = self._extract_one_xml_frame(self._rx_buf)
                if frame is not None:
                    return frame
                if len(self._rx_buf) > ack_max_bytes:
                    # Fallback: vratíme, co máme (pro kompatibilitu), ale
                    # nenecháme buffer růst donekonečna.
                    data = bytes(self._rx_buf)
                    self._rx_buf.clear()
                    return data

        return await asyncio.wait_for(_read_until_frame(), timeout=ack_timeout_s)

    async def ensure_connected(self) -> None:
        """Zajistí připojení k cloudu; při chybě vyhodí výjimku."""
        if self.is_connected():
            return
        async with self._conn_lock:
            if self.is_connected():
                return

            now = time.monotonic()
            since_last = now - self._last_connect_attempt
            if since_last < self._backoff_s:
                await asyncio.sleep(self._backoff_s - since_last)

            self._last_connect_attempt = time.monotonic()
            try:
                target_host = resolve_cloud_host(self.host)
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(target_host, self.port),
                    timeout=self.connect_timeout_s,
                )
                self.stats.connects += 1
                self._backoff_s = self.min_reconnect_s
                logger.debug(
                    "☁️ Connected to %s:%s "
                    "(connects=%s, timeouts=%s, errors=%s, disconnects=%s)",
                    self.host,
                    self.port,
                    self.stats.connects,
                    self.stats.timeouts,
                    self.stats.errors,
                    self.stats.disconnects,
                )
            except asyncio.TimeoutError:
                self.stats.timeouts += 1
                self.stats.errors += 1
                await self._close_locked()
                self._backoff_s = min(self.max_reconnect_s, self._backoff_s * 2.0)
                now = time.monotonic()
                if (now - self._last_warn_ts) >= _WARN_THROTTLE_S:
                    logger.warning(
                        "☁️ Cloud connect timeout (backoff=%.1fs)",
                        self._backoff_s,
                    )
                    self._last_warn_ts = now
                raise
            except (OSError, ConnectionError, RuntimeError) as exc:
                self.stats.errors += 1
                await self._close_locked()
                self._backoff_s = min(self.max_reconnect_s, self._backoff_s * 2.0)
                now = time.monotonic()
                if (now - self._last_warn_ts) >= _WARN_THROTTLE_S:
                    logger.warning(
                        "☁️ Cloud connect failed: %s (backoff=%.1fs)",
                        exc,
                        self._backoff_s,
                    )
                    self._last_warn_ts = now
                raise

    async def send_and_read_ack(
        self,
        data: bytes,
        ack_timeout_s: float,
        ack_max_bytes: int = _DEFAULT_ACK_MAX_BYTES,
    ) -> bytes:
        """
        Pošle bytes na cloud a přečte odpověď (ACK).
        Serializuje komunikaci, aby se nemíchaly ACKy mezi framy.
        """
        async with self._io_lock:
            await self.ensure_connected()
            writer = self._writer
            if writer is None:
                raise ConnectionError("Cloud connection not established")

            try:
                writer.write(data)
                await writer.drain()
                ack = await self._read_one_ack_frame(
                    ack_timeout_s=ack_timeout_s,
                    ack_max_bytes=ack_max_bytes,
                )
                if not ack:
                    # Cloud ukončil spojení (EOF)
                    self.stats.disconnects += 1
                    now = time.monotonic()
                    if (now - self._last_warn_ts) >= _WARN_THROTTLE_S:
                        logger.warning("☁️ Cloud connection closed (EOF)")
                        self._last_warn_ts = now
                    await self.close()
                    raise ConnectionError("Cloud connection closed (EOF)")
                return ack
            except asyncio.TimeoutError:
                self.stats.timeouts += 1
                now = time.monotonic()
                if (now - self._last_warn_ts) >= _WARN_THROTTLE_S:
                    logger.warning("☁️ Cloud ACK timeout")
                    self._last_warn_ts = now
                await self.close()
                raise
            except Exception:
                self.stats.errors += 1
                now = time.monotonic()
                if (now - self._last_warn_ts) >= _WARN_THROTTLE_S:
                    logger.warning("☁️ Cloud error (connection reset)")
                    self._last_warn_ts = now
                await self.close()
                raise
