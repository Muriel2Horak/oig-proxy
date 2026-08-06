#!/usr/bin/env python3
"""
Frame builder/parser pro OIG protokol.

Formát: <Frame>{inner_xml}<CRC>xxxxx</CRC></Frame>\r\n
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum

from .crc import CrcError, crc16_modbus, strip_crc_tag, validate_crc_tag

_TABLE_NAME_RE = re.compile(r"<TblName>([^<]+)</TblName>")
_RESULT_RE = re.compile(r"<Result>([^<]+)</Result>")
_DEVICE_ID_RE = re.compile(r"<ID_Device>(\d+)</ID_Device>")

FRAME_PREFIX = b"<Frame>"
FRAME_TERMINATOR = b"</Frame>\r\n"
_FRAME_CLOSE = b"</Frame>"
MAX_FRAME_BYTES = 1_048_576


class FrameDirection(str, Enum):
    """Direction in which an assembled frame travelled."""

    BOX_TO_PROXY = "box_to_proxy"
    CLOUD_TO_PROXY = "cloud_to_proxy"


class StreamErrorCode(str, Enum):
    """Stable failure codes for bounded TCP frame assembly."""

    INVALID_PREFIX = "invalid_prefix"
    FORBIDDEN_TERMINATOR = "forbidden_terminator"
    BUFFER_OVERFLOW = "buffer_overflow"
    EOF_PARTIAL = "eof_partial"


class FrameStreamError(ValueError):
    """Bounded stream assembly failure with a machine-readable code."""

    def __init__(self, code: StreamErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class FrameValidationError(str, Enum):
    """Stable failure codes for exact frame validation."""

    INVALID_ENVELOPE = "invalid_envelope"
    INVALID_XML = "invalid_xml"
    MISSING_CRC = "missing_crc"
    MALFORMED_CRC = "malformed_crc"
    DUPLICATE_CRC = "duplicate_crc"
    CRC_NOT_FINAL = "crc_not_final"
    CRC_MISMATCH = "crc_mismatch"


@dataclass(frozen=True, slots=True)
class AssembledFrame:
    """One exact frame and the timestamp of the chunk that completed it."""

    raw: bytes
    received_at_ms: int


@dataclass(frozen=True, slots=True)
class ValidatedFrame:
    """Frame bytes that passed envelope, CRC, and XML validation."""

    raw: bytes
    received_at_ms: int
    inner_without_crc: bytes
    transmitted_crc: int
    computed_crc: int


@dataclass(frozen=True, slots=True)
class FrameValidation:
    """Validation outcome that always preserves the original evidence."""

    frame: AssembledFrame
    validated: ValidatedFrame | None
    error: FrameValidationError | None


_CRC_ERROR_MAP = {
    CrcError.MISSING: FrameValidationError.MISSING_CRC,
    CrcError.MALFORMED: FrameValidationError.MALFORMED_CRC,
    CrcError.DUPLICATE: FrameValidationError.DUPLICATE_CRC,
    CrcError.NOT_FINAL: FrameValidationError.CRC_NOT_FINAL,
    CrcError.MISMATCH: FrameValidationError.CRC_MISMATCH,
}

RESULT_ACK = "<Result>ACK</Result>"
RESULT_END = "<Result>END</Result>"


class FrameStreamAssembler:
    """Bounded incremental assembler for exact OIG TCP frames."""

    def __init__(self, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if max_frame_bytes <= 0:
            raise ValueError("max_frame_bytes must be positive")
        self._max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()

    def feed(
        self, chunk: bytes, *, received_at_ms: int
    ) -> tuple[AssembledFrame, ...]:
        """Consume a TCP chunk and return every frame completed by it."""
        completed: list[AssembledFrame] = []
        try:
            for value in chunk:
                self._buffer.append(value)
                self._validate_prefix()
                if len(self._buffer) > self._max_frame_bytes:
                    raise FrameStreamError(StreamErrorCode.BUFFER_OVERFLOW)

                frame_end = self._complete_frame_end()
                if frame_end is None:
                    continue
                raw = bytes(self._buffer[:frame_end])
                del self._buffer[:frame_end]
                completed.append(AssembledFrame(raw, received_at_ms))
        except FrameStreamError:
            self.reset()
            raise
        return tuple(completed)

    def finish(self) -> None:
        """Accept clean EOF, or reject and discard an incomplete frame."""
        if not self._buffer:
            return
        self.reset()
        raise FrameStreamError(StreamErrorCode.EOF_PARTIAL)

    def reset(self) -> None:
        """Discard all pending bytes."""
        self._buffer.clear()

    def _validate_prefix(self) -> None:
        probe_length = min(len(self._buffer), len(FRAME_PREFIX))
        if self._buffer[:probe_length] != FRAME_PREFIX[:probe_length]:
            raise FrameStreamError(StreamErrorCode.INVALID_PREFIX)

    def _complete_frame_end(self) -> int | None:
        close_at = self._buffer.find(_FRAME_CLOSE, len(FRAME_PREFIX))
        if close_at < 0:
            return None

        suffix_at = close_at + len(_FRAME_CLOSE)
        available = len(self._buffer) - suffix_at
        if available == 0:
            return None
        if self._buffer[suffix_at] != ord("\r"):
            raise FrameStreamError(StreamErrorCode.FORBIDDEN_TERMINATOR)
        if available == 1:
            return None
        if self._buffer[suffix_at + 1] != ord("\n"):
            raise FrameStreamError(StreamErrorCode.FORBIDDEN_TERMINATOR)
        return suffix_at + 2


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
    validation = validate_frame(AssembledFrame(frame_bytes, 0))
    if validation.validated is None:
        return None
    return validation.validated.inner_without_crc


def validate_frame(  # pylint: disable=too-many-return-statements
    frame: AssembledFrame,
) -> FrameValidation:
    """Validate exact envelope, raw CRC, and XML without normalizing bytes."""
    raw = frame.raw
    if not raw.startswith(FRAME_PREFIX) or not raw.endswith(FRAME_TERMINATOR):
        return FrameValidation(frame, None, FrameValidationError.INVALID_ENVELOPE)
    if len(raw) < len(FRAME_PREFIX) + len(FRAME_TERMINATOR):
        return FrameValidation(frame, None, FrameValidationError.INVALID_ENVELOPE)

    inner_with_crc = raw[len(FRAME_PREFIX):-len(FRAME_TERMINATOR)]
    crc_validation = validate_crc_tag(inner_with_crc)
    if crc_validation.error is not None:
        return FrameValidation(frame, None, _CRC_ERROR_MAP[crc_validation.error])

    lower_raw = raw.lower()
    if b"<!doctype" in lower_raw or b"<!entity" in lower_raw:
        return FrameValidation(frame, None, FrameValidationError.INVALID_XML)

    try:
        root = ET.fromstring(raw[:-2])
    except (ET.ParseError, ValueError):
        return FrameValidation(frame, None, FrameValidationError.INVALID_XML)
    if root.tag != "Frame":
        return FrameValidation(frame, None, FrameValidationError.INVALID_XML)
    direct_crc = [child for child in root if child.tag == "CRC"]
    if len(direct_crc) != 1 or root[-1] is not direct_crc[0]:
        return FrameValidation(frame, None, FrameValidationError.INVALID_XML)

    payload = crc_validation.payload_without_crc
    transmitted = crc_validation.transmitted
    computed = crc_validation.computed
    if payload is None or transmitted is None or computed is None:
        return FrameValidation(frame, None, FrameValidationError.INVALID_XML)
    validated = ValidatedFrame(
        raw=raw,
        received_at_ms=frame.received_at_ms,
        inner_without_crc=payload,
        transmitted_crc=transmitted,
        computed_crc=computed,
    )
    return FrameValidation(frame, validated, None)


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
