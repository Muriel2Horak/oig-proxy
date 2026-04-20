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

import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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


class SettingResult(str, Enum):
    """Outcome result for a settings audit record."""

    PENDING = "pending"  # Awaiting further lifecycle step
    REJECTED = "rejected"  # Setting was rejected
    SUPERSEDED = "superseded"  # Replaced by another setting for same key
    CONFIRMED = "confirmed"  # Successfully confirmed
    FAILED = "failed"  # Failed (nack, timeout)
    INCOMPLETE = "incomplete"  # Session cleared without confirmation


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

_PRIVACY_KEY_RE = re.compile(
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
    msg_id: int = 0
    id_set: int = 0

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
    raw_text: str = "",
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
    if raw_text:
        if _is_sensitive_key(parent_record.key):
            raw_text = "[REDACTED]"
        truncated, info, audit_payload_capped = _apply_raw_text_limits(parent_record.audit_id, raw_text)

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
    if raw_text:
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


def is_terminal_step(step: SettingStep) -> bool:
    """Return True if the step is a terminal (final) lifecycle step."""
    return step in TERMINAL_STEPS


def is_stronger_ack(this_step: SettingStep, other_step: SettingStep) -> bool:
    """Return True if this_step represents a stronger ACK than other_step.

    ACK precedence: ack_tbl_events > ack_reason_setting > ack_box_observed
    """
    this_prec = ACK_TERMINAL_PRECEDENCE.get(this_step, 0)
    other_prec = ACK_TERMINAL_PRECEDENCE.get(other_step, 0)
    return this_prec > other_prec


def record_to_dict(record: SettingsAuditRecord) -> dict[str, Any]:
    """Serialize a SettingsAuditRecord to a dict ready for JSON serialization."""
    return {
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
    }
