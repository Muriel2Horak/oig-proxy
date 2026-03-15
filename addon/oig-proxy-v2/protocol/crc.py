#!/usr/bin/env python3
"""
CRC-16/MODBUS pro OIG protokol.

Poly: 0x8005, Init: 0xFFFF, RefIn/RefOut: True, XorOut: 0x0000
"""

from __future__ import annotations

import functools
import re

_CRC_TAG_RE = re.compile(rb"<CRC>\d+</CRC>")


def _reflect_bits(x: int, width: int) -> int:
    out = 0
    for _ in range(width):
        out = (out << 1) | (x & 1)
        x >>= 1
    return out


@functools.lru_cache(maxsize=1)
def _crc16_table() -> tuple[int, ...]:
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
    """CRC-16/MODBUS nad zadanými daty."""
    table = _crc16_table()
    crc = 0xFFFF
    for b in data:
        crc = ((crc >> 8) ^ table[(crc ^ b) & 0xFF]) & 0xFFFF
    return crc


def strip_crc_tag(data: bytes) -> bytes:
    """Odstraní <CRC>xxxxx</CRC> tag z bytes."""
    return _CRC_TAG_RE.sub(b"", data)
