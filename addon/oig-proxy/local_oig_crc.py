#!/usr/bin/env python3
"""
CRC utilities for OIG protocol frames.

Based on captured traffic, <CRC> is CRC-16/MODBUS calculated over the inner
contents of <Frame>...</Frame> with the <CRC>...</CRC> tag removed.
"""

from __future__ import annotations

import functools
import re


_CRC_TAG_RE = re.compile(rb"<CRC>\d+</CRC>")
_FRAME_INNER_RE = re.compile(rb"^<Frame>(.*)</Frame>\r?\n?$", re.DOTALL)


def _reflect_bits(x: int, width: int) -> int:
    out = 0
    for _ in range(width):
        out = (out << 1) | (x & 1)
        x >>= 1
    return out


@functools.lru_cache(maxsize=16)
def _crc16_table_modbus() -> tuple[int, ...]:
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
    """
    CRC-16/MODBUS:
    - poly 0x8005
    - init 0xFFFF
    - refin/refout True
    - xorout 0x0000
    """
    table = _crc16_table_modbus()
    crc = 0xFFFF
    for b in data:
        crc = ((crc >> 8) ^ table[(crc ^ b) & 0xFF]) & 0xFFFF
    return crc


def frame_inner_bytes(frame_bytes: bytes) -> bytes:
    """Extracts bytes inside <Frame>...</Frame>; falls back to input bytes."""
    m = _FRAME_INNER_RE.match(frame_bytes.rstrip(b"\r\n"))
    return m.group(1) if m else frame_bytes


def compute_frame_crc(frame_bytes: bytes) -> int:
    """
    Computes OIG frame CRC:
    - uses the inner content (without <Frame> wrapper)
    - strips any <CRC>...</CRC> tag (digits form)
    - computes CRC16/MODBUS over remaining bytes
    """
    inner = frame_inner_bytes(frame_bytes)
    inner_wo_crc = _CRC_TAG_RE.sub(b"", inner)
    return crc16_modbus(inner_wo_crc)


def build_frame(inner_xml: str, *, add_crlf: bool = True) -> str:
    """
    Builds <Frame>...</Frame> with a correct decimal <CRC>xxxxx</CRC>.

    `inner_xml` must NOT include the outer <Frame> tag.
    If it includes a <CRC>...</CRC> tag already, it will be removed for CRC
    calculation and replaced.
    """
    inner_bytes = inner_xml.encode("utf-8", errors="strict")
    inner_wo_crc = _CRC_TAG_RE.sub(b"", inner_bytes)
    crc = crc16_modbus(inner_wo_crc)
    crc_tag = f"<CRC>{crc:05d}</CRC>"
    inner_text = inner_wo_crc.decode("utf-8", errors="strict")
    out = f"<Frame>{inner_text}{crc_tag}</Frame>"
    if add_crlf:
        out += "\r\n"
    return out
