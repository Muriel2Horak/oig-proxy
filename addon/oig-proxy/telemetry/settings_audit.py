"""Settings Audit Schema and Record Helper.

This module is the SINGLE SCHEMA AUTHORITY for all settings-audit records
emitted by the repo. All emitters (TwinControlHandler, TwinDelivery,
ProxyServer) must use the helpers defined here.

Schema Design:
- Tags (low-cardinality, for grouping): device_id, table, step, result
- Fields (high-cardinality, for analysis): audit_id, key, session_id, msg_id,
  id_set, raw_text, value_text, confirmed_value_text, value_kind,
  confirmed_value_kind, value_num_float, confirmed_value_num_float,
  raw_text_truncated, raw_text_bytes_original, audit_payload_capped

Influx Constraints:
- Tags only for device_id, table, step, result
- NO high-cardinality values (audit_id, key, raw_text) as tags
- String fields for text content; float fields only for numeric values
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, replace
import hashlib
import logging
import re
import secrets
import time
from enum import Enum
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from protocol.frame import ValidatedFrame
    from protocol.parser import FrameMetadata
    from twin.ack_parser import SettingEvent, SettingResponse
    from twin.state import TransitionAuditSnapshot


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Step and Result Taxonomies
# ----------------------------------------------------------------------

class SettingStep(str, Enum):
    """Lifecycle steps for a settings audit record.

    Terminal steps: superseded, ack_tbl_events, ack_reason_setting, nack,
    timeout, session_cleared
    Non-terminal steps: incoming, rejected_not_allowed, rejected_validation,
    enqueued, deliver_selected, injected_box, ack_box_observed
    """

    INCOMING = "incoming"  # First seen inbound command
    REJECTED_NOT_ALLOWED = "rejected_not_allowed"  # Setting not in allowlist
    REJECTED_VALIDATION = "rejected_validation"  # Value failed validation
    ENQUEUED = "enqueued"  # Accepted and queued for delivery
    SUPERSEDED = "superseded"  # Prior pending setting replaced by new one
    DELIVER_SELECTED = "deliver_selected"  # Chosen from queue for delivery
    INJECTED_BOX = "injected_box"  # Sent to BOX device
    ACK_BOX_OBSERVED = "ack_box_observed"  # BOX acknowledged the setting
    ACK_TBL_EVENTS = "ack_tbl_events"  # Confirmed via tbl_events
    ACK_REASON_SETTING = "ack_reason_setting"  # Confirmed via cloud reason=Setting
    NACK = "nack"  # BOX or cloud rejected
    TIMEOUT = "timeout"  # No response within timeout window
    SESSION_CLEARED = "session_cleared"  # Session ended without ACK

    # Durable Task 8 projection steps. Legacy values above remain importable
    # until the runtime cutover removes TwinDelivery and TwinQueue.
    SELECTED = "selected"
    ATTEMPT_PREPARED = "attempt_prepared"
    WRITE_STARTED = "write_started"
    ATTEMPT_DRAINED = "attempt_drained"
    WRITE_UNKNOWN = "write_unknown"
    WRITE_FAILED = "write_failed"
    ACK_OBSERVED = "ack_observed"
    RETRY = "retry"
    EVENT_CONFIRMED = "event_confirmed"
    EXPIRED = "expired"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class SettingResult(str, Enum):
    """Outcome result for a settings audit record."""

    PENDING = "pending"  # Awaiting further lifecycle step
    REJECTED = "rejected"  # Setting was rejected
    SUPERSEDED = "superseded"  # Replaced by another setting for same key
    CONFIRMED = "confirmed"  # Successfully confirmed
    FAILED = "failed"  # Failed (nack, timeout)
    INCOMPLETE = "incomplete"  # Session cleared without confirmation
    EXPIRED = "expired"


# ----------------------------------------------------------------------
# Terminal Precedence (strongest to weakest)
# ----------------------------------------------------------------------
# ack_tbl_events > ack_reason_setting > ack_box_observed
# ack_box_observed is non-terminal if a stronger confirmation arrives later

TERMINAL_STEPS = frozenset({
    SettingStep.SUPERSEDED,
    SettingStep.ACK_TBL_EVENTS,
    SettingStep.ACK_REASON_SETTING,
    SettingStep.NACK,
    SettingStep.TIMEOUT,
    SettingStep.SESSION_CLEARED,
})

ACK_TERMINAL_PRECEDENCE = {
    SettingStep.ACK_TBL_EVENTS: 3,
    SettingStep.ACK_REASON_SETTING: 2,
    SettingStep.ACK_BOX_OBSERVED: 1,
}


# ----------------------------------------------------------------------
# Privacy Redaction
# ----------------------------------------------------------------------

_PRIVACY_KEY_RE = re.compile(  # NOSONAR - safe pattern, no ReDoS risk (no nested quantifiers)
    r"(password|token|secret|auth|pass|key)",
    re.IGNORECASE,
)

_MAX_RAW_TEXT_BYTES = 16 * 1024  # 16 KiB per raw_text field
_MAX_TOTAL_RAW_BYTES = 64 * 1024  # 64 KiB per audit_id total
_AUDIT_TRACKING_TTL_SECONDS = 300

_audit_raw_bytes: dict[str, int] = {}
_audit_last_seen: dict[str, float] = {}


def _is_sensitive_key(key: str) -> bool:
    """Return True if the key name suggests sensitive content."""
    return bool(_PRIVACY_KEY_RE.search(key))


def redact_sensitive_value(key: str, value: Any) -> str:
    """Redact value if the key looks sensitive, otherwise return str(value)."""
    if _is_sensitive_key(key):
        return "[REDACTED]"
    return str(value)


# ----------------------------------------------------------------------
# Truncation Helpers
# ----------------------------------------------------------------------

@dataclass
class TruncationInfo:
    """Tracks whether truncation occurred and original size."""

    original_bytes: int = 0
    was_truncated: bool = False


def truncate_raw_text(text: str) -> tuple[str, TruncationInfo]:
    """Truncate raw text to MAX_RAW_TEXT_BYTES (16 KiB).

    Returns (truncated_text, info) where info.was_truncated=True if truncation occurred.
    """
    encoded = text.encode("utf-8", errors="replace")
    info = TruncationInfo(original_bytes=len(encoded))
    if len(encoded) <= _MAX_RAW_TEXT_BYTES:
        return text, info
    truncated_bytes = encoded[:_MAX_RAW_TEXT_BYTES]
    truncated_text = truncated_bytes.decode("utf-8", errors="replace")
    info.was_truncated = True
    return truncated_text, info


def _truncate_utf8_text(text: str, byte_limit: int) -> str:
    """Truncate text to a byte limit while preserving UTF-8 validity."""
    if byte_limit <= 0:
        return ""
    truncated_bytes = text.encode("utf-8", errors="replace")[:byte_limit]
    return truncated_bytes.decode("utf-8", errors="replace")


def _cleanup_audit_tracking(now: float | None = None) -> None:
    """Expire stale aggregate raw-text tracking entries."""
    if now is None:
        now = time.time()
    expired_audit_ids = [
        audit_id
        for audit_id, last_seen in _audit_last_seen.items()
        if now - last_seen > _AUDIT_TRACKING_TTL_SECONDS
    ]
    for audit_id in expired_audit_ids:
        _audit_last_seen.pop(audit_id, None)
        _audit_raw_bytes.pop(audit_id, None)


def _touch_audit_tracking(audit_id: str, now: float | None = None) -> None:
    """Refresh last-seen time for tracked aggregate raw-text state."""
    if now is None:
        now = time.time()
    if audit_id in _audit_raw_bytes:
        _audit_last_seen[audit_id] = now


def _apply_raw_text_limits(audit_id: str, raw_text: str) -> tuple[str, TruncationInfo, bool]:
    """Apply per-field and aggregate raw-text caps for an audit_id."""
    now = time.time()
    _cleanup_audit_tracking(now)

    truncated, info = truncate_raw_text(raw_text)
    stored_bytes = len(truncated.encode("utf-8", errors="replace"))
    used_bytes = _audit_raw_bytes.get(audit_id, 0)
    remaining_bytes = max(0, _MAX_TOTAL_RAW_BYTES - used_bytes)
    audit_payload_capped = stored_bytes > remaining_bytes

    if audit_payload_capped:
        truncated = _truncate_utf8_text(truncated, remaining_bytes)
        stored_bytes = len(truncated.encode("utf-8", errors="replace"))
        info.was_truncated = True

    _audit_raw_bytes[audit_id] = used_bytes + stored_bytes
    _audit_last_seen[audit_id] = now

    return truncated, info, audit_payload_capped


# ----------------------------------------------------------------------
# Settings Audit Record
# ----------------------------------------------------------------------

@dataclass
class SettingsAuditRecord:
    """A single settings audit step record.

    This is the canonical record type for all settings lifecycle telemetry.
    All high-cardinality text fields are stored as string fields (not tags)
    to avoid Influx cardinality explosion.
    """

    # --- Identity (used for correlation across steps) ---
    audit_id: str
    device_id: str
    table: str
    key: str

    # --- Lifecycle ---
    step: SettingStep
    result: SettingResult

    # --- Correlation ---
    session_id: str = ""
    msg_id: int | None = 0
    id_set: int | None = 0

    # --- Values ---
    value_text: str = ""
    confirmed_value_text: str = ""
    value_kind: str = ""
    confirmed_value_kind: str = ""
    value_num_float: float | None = None
    confirmed_value_num_float: float | None = None

    # --- Raw text (full original payload, truncated per policy) ---
    raw_text: str = ""
    raw_text_truncated: bool = False
    raw_text_bytes_original: int = 0

    # --- Aggregate truncation tracking per audit_id ---
    audit_payload_capped: bool = False

    # --- Timestamps (set by caller or now) ---
    timestamp: str = ""

    # --- Durable committed-transition identity and evidence ---
    transition_id: int | None = None
    command_id: str | None = None
    attempt_number: int | None = None
    from_state: Any | None = None
    to_state: Any | None = None
    wire_dt: str | None = None
    tsec_text: str | None = None
    ver_text: str | None = None
    crc_text: str | None = None
    write_outcome: str | None = None
    wire_length: int | None = None
    wire_frame: bytes | None = None
    evidence_id: str | None = None
    evidence_frame: bytes | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = _utc_iso()

    def with_truncated_raw_text(self, truncated: str, info: TruncationInfo) -> SettingsAuditRecord:
        """Return a copy with raw_text fields updated after truncation."""
        self.raw_text = truncated
        self.raw_text_truncated = info.was_truncated
        self.raw_text_bytes_original = info.original_bytes
        return self


def _utc_iso(ts: float | None = None) -> str:
    """Return ISO timestamp string in UTC."""
    from datetime import datetime, timezone
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_value_for_text(value: Any) -> str:
    """Convert a setting value to its canonical text representation."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _detect_value_kind(value: Any) -> str:
    """Detect the kind of a value for storage."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def make_incoming_record(
    device_id: str,
    table: str,
    key: str,
    raw_text: str,
    value: Any,
    session_id: str = "",
    msg_id: int = 0,
    id_set: int = 0,
) -> SettingsAuditRecord:
    """Create an 'incoming' audit record for a newly received setting command."""
    audit_id = _generate_audit_id()
    value_text = _normalize_value_for_text(value)
    value_kind = _detect_value_kind(value)
    if isinstance(value, bool):
        value_num_float = None
    elif isinstance(value, int):
        value_num_float = float(value)
    elif isinstance(value, float):
        value_num_float = value
    else:
        value_num_float = None
    if _is_sensitive_key(key):
        raw_text = "[REDACTED]"
    truncated, info, audit_payload_capped = _apply_raw_text_limits(audit_id, raw_text)

    record = SettingsAuditRecord(
        audit_id=audit_id,
        device_id=device_id,
        table=table,
        key=key,
        step=SettingStep.INCOMING,
        result=SettingResult.PENDING,
        session_id=session_id,
        msg_id=msg_id,
        id_set=id_set,
        value_text=redact_sensitive_value(key, value_text),
        value_kind=value_kind,
        value_num_float=value_num_float,
        raw_text="",  # filled below
        raw_text_truncated=False,
        raw_text_bytes_original=0,
        audit_payload_capped=audit_payload_capped,
    )
    record.with_truncated_raw_text(truncated, info)
    return record


def make_step_record(
    parent_record: SettingsAuditRecord,
    step: SettingStep,
    result: SettingResult | None = None,
    *,
    raw_text: str | None = None,
    confirmed_value: Any = None,
    session_id: str | None = None,
    msg_id: int | None = None,
    id_set: int | None = None,
) -> SettingsAuditRecord:
    """Create a subsequent step record sharing the same audit_id."""
    _touch_audit_tracking(parent_record.audit_id)

    if result is None:
        if step in TERMINAL_STEPS:
            result = _terminal_result_for_step(step)
        else:
            result = SettingResult.PENDING

    confirmed_text = ""
    confirmed_kind = ""
    confirmed_float: float | None = None
    if confirmed_value is not None:
        confirmed_text = _normalize_value_for_text(confirmed_value)
        confirmed_kind = _detect_value_kind(confirmed_value)
        if confirmed_kind == "int":
            confirmed_float = float(confirmed_value)
        elif confirmed_kind == "float":
            confirmed_float = confirmed_value

    truncated = ""
    info = TruncationInfo()
    audit_payload_capped = False
    raw_text_source = parent_record.raw_text if raw_text is None else raw_text
    if raw_text_source:
        if _is_sensitive_key(parent_record.key):
            raw_text_source = "[REDACTED]"
        truncated, info, audit_payload_capped = _apply_raw_text_limits(
            parent_record.audit_id,
            raw_text_source,
        )

    record = SettingsAuditRecord(
        audit_id=parent_record.audit_id,
        device_id=parent_record.device_id,
        table=parent_record.table,
        key=parent_record.key,
        step=step,
        result=result,
        session_id=session_id if session_id is not None else parent_record.session_id,
        msg_id=msg_id if msg_id is not None else parent_record.msg_id,
        id_set=id_set if id_set is not None else parent_record.id_set,
        value_text=parent_record.value_text,
        value_kind=parent_record.value_kind,
        value_num_float=parent_record.value_num_float,
        confirmed_value_text=redact_sensitive_value(parent_record.key, confirmed_text),
        confirmed_value_kind=confirmed_kind,
        confirmed_value_num_float=confirmed_float,
        raw_text="",
        raw_text_truncated=False,
        raw_text_bytes_original=0,
        audit_payload_capped=audit_payload_capped,
        timestamp="",  # filled by __post_init__
    )
    if raw_text_source:
        record.with_truncated_raw_text(truncated, info)
    return record


def make_superseded_record(parent_record: SettingsAuditRecord) -> SettingsAuditRecord:
    """Create a superseded record to terminate a pending audit_id."""
    return make_step_record(
        parent_record,
        SettingStep.SUPERSEDED,
        SettingResult.SUPERSEDED,
    )


def _terminal_result_for_step(step: SettingStep) -> SettingResult:
    """Map a terminal step to its canonical result."""
    mapping = {
        SettingStep.REJECTED_NOT_ALLOWED: SettingResult.REJECTED,
        SettingStep.REJECTED_VALIDATION: SettingResult.REJECTED,
        SettingStep.SUPERSEDED: SettingResult.SUPERSEDED,
        SettingStep.ACK_TBL_EVENTS: SettingResult.CONFIRMED,
        SettingStep.ACK_REASON_SETTING: SettingResult.CONFIRMED,
        SettingStep.NACK: SettingResult.FAILED,
        SettingStep.TIMEOUT: SettingResult.FAILED,
        SettingStep.SESSION_CLEARED: SettingResult.INCOMPLETE,
    }
    return mapping.get(step, SettingResult.FAILED)


def _generate_audit_id() -> str:
    """Generate a unique, sortable audit ID using timestamp + random."""
    return f"aud_{int(time.time() * 1000):014d}_{secrets.randbelow(1_000_000):06d}"


def is_stronger_ack(this_step: SettingStep, other_step: SettingStep) -> bool:
    """Return True if this_step represents a stronger ACK than other_step.

    ACK precedence: ack_tbl_events > ack_reason_setting > ack_box_observed
    """
    this_prec = ACK_TERMINAL_PRECEDENCE.get(this_step, 0)
    other_prec = ACK_TERMINAL_PRECEDENCE.get(other_step, 0)
    return this_prec > other_prec


def record_to_dict(record: SettingsAuditRecord) -> dict[str, Any]:
    """Serialize a SettingsAuditRecord to a dict ready for JSON serialization."""
    projected = {
        "timestamp": record.timestamp,
        "device_id": record.device_id,
        "table": record.table,
        "step": record.step.value if isinstance(record.step, Enum) else record.step,
        "result": record.result.value if isinstance(record.result, Enum) else record.result,
        "audit_id": record.audit_id,
        "key": record.key,
        "session_id": record.session_id,
        "msg_id": record.msg_id,
        "id_set": record.id_set,
        "value_text": record.value_text,
        "confirmed_value_text": record.confirmed_value_text,
        "value_kind": record.value_kind,
        "confirmed_value_kind": record.confirmed_value_kind,
        "value_num_float": record.value_num_float,
        "confirmed_value_num_float": record.confirmed_value_num_float,
        "raw_text": record.raw_text,
        "raw_text_truncated": record.raw_text_truncated,
        "raw_text_bytes_original": record.raw_text_bytes_original,
        "audit_payload_capped": record.audit_payload_capped,
        "transition_id": record.transition_id,
        "command_id": record.command_id,
        "attempt_number": record.attempt_number,
        "from_state": (
            record.from_state.value
            if isinstance(record.from_state, Enum)
            else record.from_state
        ),
        "to_state": (
            record.to_state.value
            if isinstance(record.to_state, Enum)
            else record.to_state
        ),
        "wire_dt": record.wire_dt,
        "tsec_text": record.tsec_text,
        "ver_text": record.ver_text,
        "crc_text": record.crc_text,
        "write_outcome": record.write_outcome,
        "wire_length": record.wire_length,
        "wire_frame_b64": _bytes_to_base64(record.wire_frame),
        "evidence_id": record.evidence_id,
        "evidence_frame_b64": _bytes_to_base64(record.evidence_frame),
        "error": record.error,
    }
    return projected


def _bytes_to_base64(value: bytes | None) -> str | None:
    if value is None:
        return None
    return base64.b64encode(value).decode("ascii")


_COMMITTED_STEP_BY_REASON = {
    "accepted_ingress": SettingStep.ENQUEUED,
    "superseded_by_newer": SettingStep.SUPERSEDED,
    "replaced_unsent": SettingStep.SUPERSEDED,
    "selected": SettingStep.SELECTED,
    "attempt_prepared": SettingStep.ATTEMPT_PREPARED,
    "write_started": SettingStep.WRITE_STARTED,
    "attempt_drained": SettingStep.ATTEMPT_DRAINED,
    "write_unknown": SettingStep.WRITE_UNKNOWN,
    "write_failed": SettingStep.WRITE_FAILED,
    "ack_received": SettingStep.ACK_OBSERVED,
    "nack_received": SettingStep.NACK,
    "event_confirmed": SettingStep.EVENT_CONFIRMED,
    "pending_ttl_expired": SettingStep.EXPIRED,
    "event_timeout": SettingStep.INCOMPLETE,
}


def _step_from_snapshot(snapshot: TransitionAuditSnapshot) -> SettingStep:
    reason = snapshot.transition.reason
    mapped = _COMMITTED_STEP_BY_REASON.get(reason)
    if mapped is not None:
        return mapped
    if reason in {
        "disconnect",
        "unexpected_response",
        "stream_error",
        "shutdown",
        "ack_timeout",
    }:
        if snapshot.command.state.value == "retry_pending":
            return SettingStep.RETRY
        return SettingStep.FAILED
    if reason == "render_failed":
        return SettingStep.FAILED
    if snapshot.command.state.value == "failed":
        return SettingStep.FAILED
    raise ValueError(f"unknown committed setting transition reason: {reason}")


def _result_from_snapshot(snapshot: TransitionAuditSnapshot) -> SettingResult:
    state = snapshot.command.state.value
    if state == "confirmed":
        return SettingResult.CONFIRMED
    if state == "superseded":
        return SettingResult.SUPERSEDED
    if state == "expired":
        return SettingResult.EXPIRED
    if state == "incomplete":
        return SettingResult.INCOMPLETE
    if state == "failed":
        return SettingResult.FAILED
    return SettingResult.PENDING


def _project_committed(snapshot: TransitionAuditSnapshot) -> SettingsAuditRecord:
    command = snapshot.command
    transition = snapshot.transition
    attempt = snapshot.attempt
    evidence = snapshot.evidence
    sensitive = _is_sensitive_key(command.item_name)
    raw_text = "[REDACTED]" if sensitive else command.raw_ingress_text
    raw_text, truncation, audit_payload_capped = _apply_raw_text_limits(
        command.audit_id, raw_text
    )
    wire_frame = attempt.wire_frame if attempt is not None else transition.wire_frame
    evidence_frame = (
        evidence.evidence_frame
        if evidence is not None
        else transition.evidence_frame
    )
    if sensitive:
        wire_frame = b"[REDACTED]" if wire_frame is not None else None
        evidence_frame = (
            b"[REDACTED]" if evidence_frame is not None else None
        )
    record = SettingsAuditRecord(
        audit_id=command.audit_id,
        device_id=command.device_id,
        table=command.table_name,
        key=command.item_name,
        step=_step_from_snapshot(snapshot),
        result=_result_from_snapshot(snapshot),
        session_id=transition.session_id or "",
        msg_id=command.wire_id,
        id_set=command.wire_id_set,
        value_text=(
            "[REDACTED]" if sensitive else command.value_text
        ),
        value_kind="string",
        confirmed_value_text=(
            "[REDACTED]"
            if sensitive and command.state.value == "confirmed"
            else command.value_text
            if command.state.value == "confirmed"
            else ""
        ),
        confirmed_value_kind=(
            "string" if command.state.value == "confirmed" else ""
        ),
        raw_text=raw_text,
        raw_text_truncated=truncation.was_truncated,
        raw_text_bytes_original=truncation.original_bytes,
        audit_payload_capped=audit_payload_capped,
        timestamp=_utc_iso(transition.occurred_at_ms / 1000),
        transition_id=transition.transition_id,
        command_id=command.command_id,
        attempt_number=transition.attempt_number,
        from_state=transition.from_state,
        to_state=transition.to_state,
        wire_dt=command.wire_dt,
        tsec_text=attempt.tsec_text if attempt is not None else None,
        ver_text=attempt.ver_text if attempt is not None else None,
        crc_text=attempt.crc_text if attempt is not None else None,
        write_outcome=(
            attempt.write_outcome.value if attempt is not None else None
        ),
        wire_length=attempt.wire_length if attempt is not None else None,
        wire_frame=wire_frame,
        evidence_id=evidence.evidence_id if evidence is not None else None,
        evidence_frame=evidence_frame,
        error=transition.error_text or command.last_error,
    )
    return record


class SettingsAuditPublisher:
    """Task 8 committed-transition publisher boundary."""

    def __init__(
        self, sink: Callable[[SettingsAuditRecord], None] | None = None
    ) -> None:
        self._sink = sink

    def publish_committed(self, snapshot: TransitionAuditSnapshot) -> None:
        """Project one committed snapshot without owning lifecycle truth."""
        if self._sink is None:
            return
        try:
            self._sink(_project_committed(snapshot))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("settings audit sink rejected committed transition")


@dataclass(frozen=True, slots=True)
class CloudSettingAuditRecord:
    """Passive connection-local observation of one cloud Setting."""

    cloud_observation_id: str
    session_id: str
    device_id: str
    table_name: str
    item_name: str
    value_text: str
    wire_id: int | None
    wire_id_set: int | None
    raw_frame: bytes
    step: str
    observed_at_ms: int


def cloud_record_to_dict(record: CloudSettingAuditRecord) -> dict[str, Any]:
    """Serialize passive cloud evidence without losing exact frame bytes."""
    return {
        "cloud_observation_id": record.cloud_observation_id,
        "session_id": record.session_id,
        "device_id": record.device_id,
        "table_name": record.table_name,
        "item_name": record.item_name,
        "value_text": record.value_text,
        "wire_id": record.wire_id,
        "wire_id_set": record.wire_id_set,
        "raw_frame_b64": _bytes_to_base64(record.raw_frame),
        "step": record.step,
        "observed_at_ms": record.observed_at_ms,
    }


class CloudSettingAuditObserver:
    """Observe cloud-owned Setting traffic without influencing local truth."""

    def __init__(
        self, sink: Callable[[CloudSettingAuditRecord], None] | None
    ) -> None:
        self._sink = sink
        self._sessions: dict[str, deque[CloudSettingAuditRecord]] = {}

    def _publish(self, record: CloudSettingAuditRecord) -> None:
        if self._sink is None:
            return
        try:
            self._sink(record)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("cloud setting audit sink rejected observation")

    def setting_forwarded(
        self,
        *,
        session_id: str,
        frame: ValidatedFrame,
        metadata: FrameMetadata,
        observed_at_ms: int,
    ) -> CloudSettingAuditRecord:
        """Record exact cloud Setting bytes after forwarding to BOX."""
        required = (
            metadata.device_id,
            metadata.table_name,
            metadata.item_name,
            metadata.new_value,
        )
        if not session_id or any(value is None for value in required):
            raise ValueError("cloud Setting observation lacks identity fields")
        observation_id = hashlib.sha256(
            session_id.encode("utf-8") + b"\0" + frame.raw
        ).hexdigest()
        record = CloudSettingAuditRecord(
            observation_id,
            session_id,
            metadata.device_id or "",
            metadata.table_name or "",
            metadata.item_name or "",
            metadata.new_value or "",
            metadata.message_id,
            metadata.id_set,
            frame.raw,
            "setting_forwarded",
            observed_at_ms,
        )
        self._sessions.setdefault(session_id, deque()).append(record)
        self._publish(record)
        return record

    def box_response_forwarded(
        self,
        *,
        session_id: str,
        response: SettingResponse,
        observed_at_ms: int,
    ) -> CloudSettingAuditRecord | None:
        """Correlate one forwarded BOX response to the session FIFO head."""
        queue = self._sessions.get(session_id)
        if not queue:
            return None
        pending = queue.popleft()
        if not queue:
            self._sessions.pop(session_id, None)
        record = replace(
            pending,
            step=(
                "box_ack_forwarded"
                if response.result == "ACK"
                else "box_nack_forwarded"
            ),
            observed_at_ms=observed_at_ms,
        )
        self._publish(record)
        return record

    def setting_event_observed(
        self,
        *,
        session_id: str,
        event: SettingEvent,
        raw_frame: bytes,
        observed_at_ms: int,
    ) -> CloudSettingAuditRecord | None:
        """Passively correlate an exact event only to the current FIFO head."""
        queue = self._sessions.get(session_id)
        if not queue:
            return None
        pending = queue[0]
        if (
            pending.device_id,
            pending.table_name,
            pending.item_name,
            pending.value_text,
        ) != (
            event.device_id,
            event.table_name,
            event.item_name,
            event.new_value_text,
        ):
            return None
        queue.popleft()
        if not queue:
            self._sessions.pop(session_id, None)
        record = replace(
            pending,
            raw_frame=raw_frame,
            step="event_observed",
            observed_at_ms=observed_at_ms,
        )
        self._publish(record)
        return record

    def close_session(
        self,
        *,
        session_id: str,
        reason: str = "session_closed",
        observed_at_ms: int | None = None,
    ) -> tuple[CloudSettingAuditRecord, ...]:
        """Publish and discard every still-pending session observation."""
        queue = self._sessions.pop(session_id, deque())
        timestamp = (
            time.time_ns() // 1_000_000
            if observed_at_ms is None
            else observed_at_ms
        )
        closed = tuple(
            replace(record, step=reason, observed_at_ms=timestamp)
            for record in queue
        )
        for record in closed:
            self._publish(record)
        return closed
