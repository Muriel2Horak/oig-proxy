#!/usr/bin/env python3
"""
Frame builder/parser pro OIG protokol.

Formát: <Frame>{inner_xml}<CRC>xxxxx</CRC></Frame>\r\n
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .crc import crc16_modbus, strip_crc_tag

_FRAME_INNER_RE = re.compile(rb"^<Frame>(.*)</Frame>\r?\n?$", re.DOTALL)
_TABLE_NAME_RE = re.compile(r"<TblName>([^<]+)</TblName>")
_RESULT_RE = re.compile(r"<Result>([^<]+)</Result>")
_DEVICE_ID_RE = re.compile(r"<ID_Device>(\d+)</ID_Device>")

RESULT_ACK = "<Result>ACK</Result>"
RESULT_END = "<Result>END</Result>"


def build_frame(inner_xml: str, *, add_crlf: bool = True) -> str:
    """Sestaví <Frame>...</Frame> s CRC tagem."""
    inner_bytes = inner_xml.encode("utf-8")
    inner_wo_crc = strip_crc_tag(inner_bytes)
    crc = crc16_modbus(inner_wo_crc)
    crc_tag = f"<CRC>{crc:05d}</CRC>"
    inner_text = inner_wo_crc.decode("utf-8")
    out = f"<Frame>{inner_text}{crc_tag}</Frame>"
    if add_crlf:
        out += "\r\n"
    return out


def parse_frame(frame_bytes: bytes) -> bytes | None:
    """
    Extrahuje inner content z <Frame>...</Frame>.

    Vrátí bytes bez CRC tagu, nebo None pokud frame není validní.
    """
    m = _FRAME_INNER_RE.match(frame_bytes.rstrip(b"\r\n"))
    if not m:
        return None
    inner = m.group(1)
    inner_wo_crc = strip_crc_tag(inner)
    return inner_wo_crc


def extract_frame_from_buffer(buf: bytearray) -> bytes | None:
    """
    Extrahuje jeden kompletní XML frame z bufferu (in-place odstraní).

    Vrátí frame bytes nebo None pokud buffer neobsahuje kompletní frame.
    """
    end_tag = b"</Frame>"
    end_idx = buf.find(end_tag)
    if end_idx < 0:
        return None

    frame_end = end_idx + len(end_tag)
    # Konzumuj volitelný CRLF terminador
    if len(buf) > frame_end:
        if buf[frame_end: frame_end + 2] == b"\r\n":
            frame_end += 2
        elif buf[frame_end: frame_end + 1] in (b"\n", b"\r"):
            if buf[frame_end: frame_end + 1] == b"\r" and len(buf) < frame_end + 2:
                return None  # Neúplný CRLF
            frame_end += 1

    frame = bytes(buf[:frame_end])
    del buf[:frame_end]
    return frame


def infer_table_name(frame: str) -> str | None:
    """Extrahuje název tabulky nebo Result z XML frame."""
    tbl = _TABLE_NAME_RE.search(frame)
    if tbl:
        return tbl.group(1)
    res = _RESULT_RE.search(frame)
    if res:
        return res.group(1)
    return None


def infer_device_id(frame: str) -> str | None:
    """Extrahuje ID zařízení z XML frame."""
    m = _DEVICE_ID_RE.search(frame)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# ACK frame builders
# ---------------------------------------------------------------------------

def build_ack_frame() -> bytes:
    """Prostý ACK frame."""
    return build_frame(RESULT_ACK).encode("utf-8")


def build_end_time_frame() -> bytes:
    """END frame s aktuálním časem a příkazem GetActual."""
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    inner = (
        f"{RESULT_END}"
        f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
        f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
        "<ToDo>GetActual</ToDo>"
    )
    return build_frame(inner).encode("utf-8")


def build_getactual_frame() -> bytes:
    """ACK frame s příkazem GetActual."""
    inner = f"{RESULT_ACK}<ToDo>GetActual</ToDo>"
    return build_frame(inner).encode("utf-8")
