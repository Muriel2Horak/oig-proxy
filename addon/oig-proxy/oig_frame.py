#!/usr/bin/env python3
"""
Frame utilities for OIG protocol frames.
"""

from __future__ import annotations

import functools
import re


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
    out = f"<Frame>{inner_wo_crc.decode('utf-8', errors='strict')}{crc_tag}</Frame>"
    if add_crlf:
        out += "\r\n"
    return out
