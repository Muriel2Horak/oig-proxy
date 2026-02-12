#!/usr/bin/env python3
"""
Frame utilities for OIG protocol frames.
"""

from __future__ import annotations

import functools
import re
from datetime import datetime, timezone


_CRC_TAG_RE = re.compile(rb"<CRC>\d+</CRC>")
_FRAME_INNER_RE = re.compile(rb"^<Frame>(.*)</Frame>\r?\n?$", re.DOTALL)


def _reflect_bits(x: int, width: int) -> int:
    """Bitově zrcadlí `width` bitů hodnoty `x`."""
    out = 0
    for _ in range(width):
        out = (out << 1) | (x & 1)
        x >>= 1
    return out


@functools.lru_cache(maxsize=16)
def _crc16_table_modbus() -> tuple[int, ...]:
    """Předpočítaná tabulka pro CRC16/Modbus."""
    poly = 0x8005
    poly_r = _reflect_bits(poly, 16) & 0xFFFF
    table: list[int] = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ poly_r if (c & 1) else (c >> 1)
        table.append(c & 0xFFFF)
    return tuple(table)


def crc16_modbus(data: bytes) -> int:
    """Spočítá CRC16/Modbus pro zadaná data."""
    table = _crc16_table_modbus()
    crc = 0xFFFF
    for b in data:
        crc = ((crc >> 8) ^ table[(crc ^ b) & 0xFF]) & 0xFFFF
    return crc


def frame_inner_bytes(frame_bytes: bytes) -> bytes:
    """Vrátí vnitřek `<Frame>...</Frame>` bez CR/LF."""
    m = _FRAME_INNER_RE.match(frame_bytes.rstrip(b"\r\n"))
    return m.group(1) if m else frame_bytes


def compute_frame_checksum(frame_bytes: bytes) -> int:
    """Spočítá checksum z `<Frame>` bytes (bez CRC tagu)."""
    inner = frame_inner_bytes(frame_bytes)
    inner_wo_crc = _CRC_TAG_RE.sub(b"", inner)
    return crc16_modbus(inner_wo_crc)


def build_frame(inner_xml: str, *, add_crlf: bool = True) -> str:
    """
    Builds `<Frame>...</Frame>` with a correct decimal `<CRC>xxxxx</CRC>`.

    `inner_xml` must NOT include the outer `<Frame>` tag.
    """
    inner_bytes = inner_xml.encode("utf-8", errors="strict")
    inner_wo_crc = _CRC_TAG_RE.sub(b"", inner_bytes)
    checksum = crc16_modbus(inner_wo_crc)
    crc_tag = f"<CRC>{checksum:05d}</CRC>"
    inner_text = inner_wo_crc.decode("utf-8", errors="strict")
    out = f"<Frame>{inner_text}{crc_tag}</Frame>"
    if add_crlf:
        out += "\r\n"
    return out


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------
RESULT_ACK = "<Result>ACK</Result>"
RESULT_END = "<Result>END</Result>"

# Regex patterns for frame parsing
_TABLE_NAME_RE = re.compile(r"<TblName>([^<]+)</TblName>")
_RESULT_RE = re.compile(r"<Result>([^<]+)</Result>")
_DEVICE_ID_RE = re.compile(r"<ID_Device>(\d+)</ID_Device>")


# ---------------------------------------------------------------------------
# Frame building helpers
# ---------------------------------------------------------------------------
def build_getactual_frame() -> bytes:
    """Sestaví ACK frame s příkazem GetActual."""
    inner = f"{RESULT_ACK}<ToDo>GetActual</ToDo>"
    return build_frame(inner).encode("utf-8", errors="strict")


def build_ack_only_frame() -> bytes:
    """Sestaví prostý ACK frame."""
    return build_frame(RESULT_ACK).encode("utf-8", errors="strict")


def build_end_time_frame() -> bytes:
    """Sestaví END frame s aktuálním časem (local + UTC)."""
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    inner = (
        f"{RESULT_END}"
        f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
        f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
    )
    return build_frame(inner).encode("utf-8", errors="strict")


def build_offline_ack_frame(table_name: str | None) -> bytes:
    """Sestaví odpověď pro offline režim podle typu tabulky."""
    if table_name == "END":
        return build_end_time_frame()
    if table_name == "IsNewSet":
        return build_end_time_frame()
    if table_name in ("IsNewWeather", "IsNewFW"):
        return build_frame(RESULT_END).encode("utf-8", errors="strict")
    return build_ack_only_frame()


# ---------------------------------------------------------------------------
# Frame parsing helpers
# ---------------------------------------------------------------------------
def extract_one_xml_frame(buf: bytearray) -> bytes | None:
    """Extrahuje jeden kompletní XML frame z bufferu (včetně CRLF)."""
    end_tag = b"</Frame>"
    end_idx = buf.find(end_tag)
    if end_idx < 0:
        return None

    frame_end = end_idx + len(end_tag)
    if len(buf) > frame_end:
        if buf[frame_end:frame_end + 2] == b"\r\n":
            frame_end += 2
        elif buf[frame_end:frame_end + 1] == b"\n":
            frame_end += 1
        elif buf[frame_end:frame_end + 1] == b"\r":
            if len(buf) < frame_end + 2:
                return None
            frame_end += 1

    frame = bytes(buf[:frame_end])
    del buf[:frame_end]
    return frame


def infer_table_name(frame: str) -> str | None:
    """Extrahuje název tabulky z XML frame."""
    tbl = _TABLE_NAME_RE.search(frame)
    if tbl:
        return tbl.group(1)
    res = _RESULT_RE.search(frame)
    if res:
        return res.group(1)
    return None


def infer_device_id(frame: str) -> str | None:
    """Extrahuje ID zařízení z XML frame."""
    match = _DEVICE_ID_RE.search(frame)
    return match.group(1) if match else None
