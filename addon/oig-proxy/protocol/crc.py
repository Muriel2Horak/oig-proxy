#!/usr/bin/env python3
"""
CRC-16/MODBUS pro OIG protokol.

Poly: 0x8005, Init: 0xFFFF, RefIn/RefOut: True, XorOut: 0x0000
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum

_CRC_TAG_RE = re.compile(rb"<CRC>\d+</CRC>")
_EXACT_CRC_TAG_RE = re.compile(rb"<CRC>([0-9]{5})</CRC>")


class CrcError(str, Enum):
    """Failure codes for the raw inbound CRC contract."""

    MISSING = "missing"
    MALFORMED = "malformed"
    DUPLICATE = "duplicate"
    NOT_FINAL = "not_final"
    MISMATCH = "mismatch"


@dataclass(frozen=True, slots=True)
class CrcValidation:
    """Result of validating one raw CRC tag and its preceding payload."""

    payload_without_crc: bytes | None
    transmitted: int | None
    computed: int | None
    error: CrcError | None

    @property
    def valid(self) -> bool:
        """Return whether shape and value validation both succeeded."""
        return self.error is None


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


def validate_crc_tag(data: bytes) -> CrcValidation:
    """Validate one exact final CRC5 tag over every preceding raw byte."""
    opening_count = data.count(b"<CRC")
    if opening_count == 0:
        return CrcValidation(None, None, None, CrcError.MISSING)
    if opening_count > 1:
        return CrcValidation(None, None, None, CrcError.DUPLICATE)

    match = _EXACT_CRC_TAG_RE.search(data)
    if match is None:
        return CrcValidation(None, None, None, CrcError.MALFORMED)
    if match.end() != len(data):
        return CrcValidation(None, None, None, CrcError.NOT_FINAL)

    payload = data[:match.start()]
    transmitted = int(match.group(1))
    computed = crc16_modbus(payload)
    error = None if transmitted == computed else CrcError.MISMATCH
    return CrcValidation(payload, transmitted, computed, error)
