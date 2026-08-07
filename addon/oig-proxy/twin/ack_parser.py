"""Legacy ACK helpers and validated local-setting evidence classifiers."""
# pylint: disable=too-many-instance-attributes

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

from protocol.frame import FrameDirection, ValidatedFrame
from protocol.parser import parse_direct_text, parse_frame_metadata

_RESULT_RE = re.compile(rb"<Result>(ACK|END|NACK)</Result>")  # NOSONAR
_TABLE_RE = re.compile(rb"<TblName>([^<]+)</TblName>")  # NOSONAR
_TODO_RE = re.compile(rb"<ToDo>([^<]+)</ToDo>")  # NOSONAR
_DT_RE = re.compile(rb"<DT>([^<]+)</DT>")  # NOSONAR
_REASON_RE = re.compile(rb"<Reason>([^<]+)</Reason>")  # NOSONAR
_TBL_EVENT_CONTENT_RE = re.compile(  # NOSONAR
    r"Remotely\s*:\s*([A-Za-z0-9_]+)\s*/\s*([A-Za-z0-9_]+)\s*:\s*\[[^\]]*\]->\[([^\]]*)\]"
)
_MAX_SIGNED_64 = (1 << 63) - 1


class _SettingEventContentParser:  # pylint: disable=too-few-public-methods
    """Monotonic parser for the exact Setting event content grammar."""

    def __init__(self, content: str) -> None:
        self._content = content
        self._position = 0
        self._inspected_character_count = 0

    @property
    def inspected_character_count(self) -> int:
        """Return deterministic character-inspection work for diagnostics."""
        return self._inspected_character_count

    def parse(  # pylint: disable=too-many-return-statements
        self,
    ) -> tuple[str, str, str, str] | None:
        """Return table, item, old, and new values for one full exact match."""
        if not self._consume_literal("Remotely"):
            return None
        self._consume_whitespace()
        if not self._consume_literal(":"):
            return None
        self._consume_whitespace()

        table = self._consume_until("/", forbidden="[]")
        if table is None:
            return None
        self._consume_whitespace()
        item = self._consume_until(":", forbidden="[]")
        if item is None:
            return None
        self._consume_whitespace()
        if not self._consume_literal("["):
            return None
        old_value = self._consume_until("]")
        if old_value is None or not self._consume_literal("->["):
            return None
        new_value = self._consume_until("]")
        if new_value is None or self._position != len(self._content):
            return None

        table_name = table.strip()
        item_name = item.strip()
        if not table_name or not item_name:
            return None
        return table_name, item_name, old_value, new_value

    def _consume_literal(self, literal: str) -> bool:
        for expected in literal:
            if self._take() != expected:
                return False
        return True

    def _consume_whitespace(self) -> None:
        while self._position < len(self._content):
            self._inspected_character_count += 1
            if not self._content[self._position].isspace():
                return
            self._position += 1

    def _consume_until(
        self, delimiter: str, *, forbidden: str = ""
    ) -> str | None:
        start = self._position
        while self._position < len(self._content):
            value = self._take()
            if value is None:
                return None
            if value == delimiter:
                return self._content[start:self._position - 1]
            if value in forbidden:
                return None
        return None

    def _take(self) -> str | None:
        if self._position >= len(self._content):
            return None
        self._inspected_character_count += 1
        value = self._content[self._position]
        self._position += 1
        return value


def parse_setting_event_content(content: str) -> tuple[str, str, str, str] | None:
    """Parse one complete remote Setting-event content value."""
    if not isinstance(content, str):
        return None
    return _SettingEventContentParser(content).parse()


@dataclass(frozen=True, slots=True)
class SettingResponse:
    """Immutable BOX-originated ACK or NACK evidence."""

    result: Literal["ACK", "NACK"]
    reason: str | None
    rdt_text: str | None
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SettingEvent:
    """Immutable BOX-originated setting-change evidence."""

    evidence_id: str
    device_id: str
    event_id_set: int
    device_dt: str
    content_text: str
    table_name: str
    item_name: str
    old_value_text: str
    new_value_text: str


def parse_box_ack(xml_bytes: bytes) -> dict[str, str] | None:
    """Parse the legacy ACK dictionary used by pre-cutover callers."""
    result_match = _RESULT_RE.search(xml_bytes)
    if not result_match:
        return None

    result: dict[str, str] = {"result": result_match.group(1).decode("utf-8", errors="replace")}

    table_match = _TABLE_RE.search(xml_bytes)
    if table_match:
        result["table"] = table_match.group(1).decode("utf-8", errors="replace")

    todo_match = _TODO_RE.search(xml_bytes)
    if todo_match:
        result["todo"] = todo_match.group(1).decode("utf-8", errors="replace")

    dt_match = _DT_RE.search(xml_bytes)
    if dt_match:
        result["timestamp"] = dt_match.group(1).decode("utf-8", errors="replace")

    reason_match = _REASON_RE.search(xml_bytes)
    if reason_match:
        result["reason"] = reason_match.group(1).decode("utf-8", errors="replace")

    return result


def parse_tbl_events_ack(parsed_frame: dict[str, Any]) -> dict[str, str] | None:
    """Parse the legacy setting-event dictionary used by existing callers."""
    table_name = parsed_frame.get("_table")
    if table_name != "tbl_events":
        return None

    event_type = parsed_frame.get("Type")
    if event_type != "Setting":
        return None

    content = parsed_frame.get("Content")
    if not isinstance(content, str):
        return None

    m = _TBL_EVENT_CONTENT_RE.search(content)
    if not m:
        return None

    return {
        "table": m.group(1),
        "key": m.group(2),
        "value": m.group(3),
    }


def parse_setting_response(
    frame: ValidatedFrame, *, direction: FrameDirection
) -> SettingResponse | None:
    """Classify validated BOX bytes as immutable setting response evidence."""
    if direction is not FrameDirection.BOX_TO_PROXY:
        return None
    metadata = parse_frame_metadata(frame)
    if metadata is None or metadata.result not in ("ACK", "NACK"):
        return None
    result = cast(Literal["ACK", "NACK"], metadata.result)
    return SettingResponse(
        result=result,
        reason=metadata.reason,
        rdt_text=metadata.rdt,
        fingerprint=hashlib.sha256(frame.raw).hexdigest(),
    )


def derive_event_evidence_id(
    device_id: str,
    event_id_set: int,
    device_dt: str,
    content_text: str,
) -> str:
    """Hash unambiguous NUL-delimited event identity fields."""
    text_fields = (device_id, device_dt, content_text)
    if any("\0" in field for field in text_fields):
        raise ValueError("event evidence fields must not contain NUL")
    if (
        isinstance(event_id_set, bool)
        or not isinstance(event_id_set, int)
        or not 0 <= event_id_set <= _MAX_SIGNED_64
    ):
        raise ValueError("event_id_set must be a non-negative signed-64-bit integer")
    encoded = b"\0".join(
        (
            device_id.encode("utf-8"),
            str(event_id_set).encode("ascii"),
            device_dt.encode("utf-8"),
            content_text.encode("utf-8"),
        )
    )
    return hashlib.sha256(encoded).hexdigest()


def parse_setting_event(  # pylint: disable=too-many-return-statements
    frame: ValidatedFrame, *, direction: FrameDirection
) -> SettingEvent | None:
    """Classify validated BOX bytes as immutable setting event evidence."""
    if direction is not FrameDirection.BOX_TO_PROXY:
        return None
    metadata = parse_frame_metadata(frame)
    if metadata is None:
        return None
    if metadata.table_name != "tbl_events" or metadata.event_type != "Setting":
        return None
    if not metadata.device_id or metadata.id_set is None or metadata.content is None:
        return None

    device_dt = parse_direct_text(frame, "DT")
    if not device_dt:
        return None
    parsed_content = parse_setting_event_content(metadata.content)
    if parsed_content is None:
        return None
    table_name, item_name, old_value, new_value = parsed_content

    evidence_id = derive_event_evidence_id(
        metadata.device_id, metadata.id_set, device_dt, metadata.content
    )
    return SettingEvent(
        evidence_id=evidence_id,
        device_id=metadata.device_id,
        event_id_set=metadata.id_set,
        device_dt=device_dt,
        content_text=metadata.content,
        table_name=table_name,
        item_name=item_name,
        old_value_text=old_value,
        new_value_text=new_value,
    )
