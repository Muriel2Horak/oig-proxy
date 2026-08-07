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

import asyncio
import base64
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum
from functools import partial
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from typing import Any, cast, NoReturn, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from protocol.frame import ValidatedFrame
    from protocol.parser import FrameMetadata
    from twin.ack_parser import SettingEvent, SettingResponse
    from twin.state import AuditDeliveryDecision, TransitionAuditSnapshot


class AuditAcceptanceLedger(Protocol):
    """Compact durable proposal/acceptance boundary used by the publisher."""

    def propose_audit_delivery(
        self,
        *,
        audit_id: str,
        command_id: str,
        transition_id: int,
        canonical_payload_sha256: bytes,
        requested_raw_bytes: int,
    ) -> AuditDeliveryDecision:
        """Persist or replay one compact pending decision."""
        raise NotImplementedError

    def accept_audit_delivery(
        self, *, audit_id: str, transition_id: int
    ) -> AuditDeliveryDecision:
        """Mark one pending decision accepted after sink success."""
        raise NotImplementedError

    def reject_audit_delivery(
        self, *, audit_id: str, transition_id: int
    ) -> bool:
        """Delete one pending decision after sink failure."""
        raise NotImplementedError

    def read_transition_audit_snapshot(
        self, transition_id: int
    ) -> TransitionAuditSnapshot:
        """Reconstruct one transition from durable historical facts."""
        raise NotImplementedError

    def read_pending_audit_transition_ids(
        self,
        *,
        after_transition_id: int = 0,
        limit: int = 128,
    ) -> tuple[int, ...]:
        """Read one ordered bounded page of pending transitions."""
        raise NotImplementedError


logger = logging.getLogger(__name__)


class _AuditLifecycleStripe:
    """One lock plus one bounded acquisition lane for an audit-id stripe."""

    __slots__ = ("executor", "lock")

    def __init__(self, index: int) -> None:
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"settings-audit-lock-{index}",
        )

    def acquire(self, blocking: bool = True) -> bool:
        """Delegate lock acquisition for sync publishers and diagnostics."""
        return self.lock.acquire(blocking=blocking)

    def release(self) -> None:
        """Release this stripe after one complete delivery lifecycle."""
        self.lock.release()

    def __enter__(self) -> _AuditLifecycleStripe:
        self.acquire()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.release()


_AUDIT_LIFECYCLE_LOCKS = tuple(
    _AuditLifecycleStripe(index) for index in range(64)
)
_AUDIT_EXTERNAL_LOCK_EXECUTOR = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="settings-audit-external-lock",
)
_AUDIT_LEDGER_EXECUTOR = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="settings-audit-ledger",
)


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
    truncated_text = _truncate_utf8_text(text, _MAX_RAW_TEXT_BYTES)
    info.was_truncated = True
    return truncated_text, info


def _truncate_utf8_text(text: str, byte_limit: int) -> str:
    """Truncate text to a byte limit while preserving UTF-8 validity."""
    if byte_limit <= 0:
        return ""
    truncated_bytes = text.encode("utf-8", errors="replace")[:byte_limit]
    return truncated_bytes.decode("utf-8", errors="ignore")


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
    "recovery_pending_expired": SettingStep.EXPIRED,
    "event_timeout": SettingStep.INCOMPLETE,
    "recovery_event_timeout": SettingStep.INCOMPLETE,
    "recovery_attempt_limit": SettingStep.FAILED,
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
        if snapshot.transition.to_state.value == "retry_pending":
            return SettingStep.RETRY
        return SettingStep.FAILED
    if reason == "render_failed":
        return SettingStep.FAILED
    if snapshot.transition.to_state.value == "failed":
        return SettingStep.FAILED
    raise ValueError(f"unknown committed setting transition reason: {reason}")


def _result_from_snapshot(snapshot: TransitionAuditSnapshot) -> SettingResult:
    state = snapshot.transition.to_state.value
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


_AUDIT_NO_ATTEMPT_REASONS = frozenset({"selected", "render_failed"})
_AUDIT_REQUIRED_ATTEMPT_REASONS = frozenset(
    {
        "attempt_prepared",
        "write_started",
        "attempt_drained",
        "write_unknown",
        "write_failed",
        "ack_received",
        "nack_received",
        "event_confirmed",
        "disconnect",
        "unexpected_response",
        "stream_error",
        "shutdown",
        "ack_timeout",
        "event_timeout",
        "recovery_event_timeout",
        "recovery_attempt_limit",
    }
)
_AUDIT_ATTEMPT_SESSION_REASONS = frozenset(
    {
        "attempt_prepared",
        "write_started",
        "attempt_drained",
        "write_unknown",
        "write_failed",
        "ack_received",
        "nack_received",
        "disconnect",
        "unexpected_response",
        "stream_error",
        "shutdown",
        "ack_timeout",
    }
)
_HISTORICAL_WRITE_OUTCOME = {
    "attempt_prepared": "prepared",
    "write_started": "started",
    "attempt_drained": "drained",
    "write_unknown": "unknown",
    "write_failed": "failed",
}
_RETRY_ERROR_REASONS = frozenset(
    {
        "disconnect",
        "unexpected_response",
        "stream_error",
        "shutdown",
        "ack_timeout",
    }
)


def _validate_projectable_snapshot(
    snapshot: TransitionAuditSnapshot,
) -> None:
    """Validate cross-row identities before deriving canonical payload."""
    command = snapshot.command
    transition = snapshot.transition
    attempt = snapshot.attempt
    evidence = snapshot.evidence
    if (
        command.command_id != transition.command_id
        or command.audit_id != transition.audit_id
    ):
        raise ValueError("audit command/transition identity changed")
    if transition.reason in _AUDIT_NO_ATTEMPT_REASONS:
        if attempt is not None:
            raise ValueError("audit transition gained a later attempt")
    elif transition.reason in _AUDIT_REQUIRED_ATTEMPT_REASONS:
        if attempt is None:
            raise ValueError("audit transition lost its exact attempt")
    if attempt is not None:
        if (
            attempt.command_id != transition.command_id
            or attempt.attempt_number != transition.attempt_number
        ):
            raise ValueError("audit attempt identity changed")
        if (
            transition.reason in _AUDIT_ATTEMPT_SESSION_REASONS
            and attempt.session_id != transition.session_id
        ):
            raise ValueError("audit attempt session changed")
    if transition.reason == "attempt_prepared" and (
        attempt is None
        or transition.wire_frame is None
        or transition.wire_frame != attempt.wire_frame
    ):
        raise ValueError("audit prepared bytes changed")
    if transition.reason in {"ack_received", "nack_received"} and (
        attempt is None
        or transition.evidence_frame is None
        or attempt.response_fingerprint is None
        or hashlib.sha256(transition.evidence_frame).hexdigest()
        != attempt.response_fingerprint
    ):
        raise ValueError("audit response evidence changed")
    if transition.reason == "event_confirmed":
        if (
            evidence is None
            or evidence.command_id != command.command_id
            or evidence.disposition != "confirmed"
            or evidence.device_id != command.device_id
            or evidence.table_name != command.table_name
            or evidence.item_name != command.item_name
            or evidence.evidence_frame != transition.evidence_frame
        ):
            raise ValueError("audit event evidence identity changed")
    elif evidence is not None:
        raise ValueError("non-event audit gained event evidence")


def _project_committed(snapshot: TransitionAuditSnapshot) -> SettingsAuditRecord:
    _validate_projectable_snapshot(snapshot)
    command = snapshot.command
    transition = snapshot.transition
    attempt = snapshot.attempt
    evidence = snapshot.evidence
    sensitive = _is_sensitive_key(command.item_name)
    raw_text = "[REDACTED]" if sensitive else command.raw_ingress_text
    raw_text, truncation = truncate_raw_text(raw_text)
    wire_frame = (
        transition.wire_frame
        if transition.wire_frame is not None
        else attempt.wire_frame
        if attempt is not None
        else None
    )
    evidence_frame = transition.evidence_frame
    evidence_id = evidence.evidence_id if evidence is not None else None
    if (
        evidence_id is None
        and transition.reason in {"ack_received", "nack_received"}
        and attempt is not None
    ):
        evidence_id = attempt.response_fingerprint
    if sensitive:
        wire_frame = b"[REDACTED]" if wire_frame is not None else None
        evidence_frame = (
            b"[REDACTED]" if evidence_frame is not None else None
        )
    first_attempt_not_yet_persisted = (
        transition.reason in _AUDIT_NO_ATTEMPT_REASONS
        and transition.from_state is not None
        and transition.from_state.value == "pending"
    )
    has_wire_identity = (
        transition.attempt_number is not None
        and not first_attempt_not_yet_persisted
    )
    confirmed = transition.to_state.value == "confirmed"
    write_outcome = (
        _HISTORICAL_WRITE_OUTCOME.get(
            transition.reason,
            attempt.write_outcome.value,
        )
        if attempt is not None
        else None
    )
    error = transition.error_text
    if error is None and transition.reason in _RETRY_ERROR_REASONS:
        error = transition.reason
    return SettingsAuditRecord(
        audit_id=transition.audit_id,
        device_id=command.device_id,
        table=command.table_name,
        key=command.item_name,
        step=_step_from_snapshot(snapshot),
        result=_result_from_snapshot(snapshot),
        session_id=transition.session_id or "",
        msg_id=command.wire_id if has_wire_identity else None,
        id_set=command.wire_id_set if has_wire_identity else None,
        value_text=("[REDACTED]" if sensitive else command.value_text),
        value_kind="string",
        confirmed_value_text=(
            "[REDACTED]" if sensitive and confirmed else command.value_text
            if confirmed
            else ""
        ),
        confirmed_value_kind="string" if confirmed else "",
        raw_text=raw_text,
        raw_text_truncated=truncation.was_truncated,
        raw_text_bytes_original=truncation.original_bytes,
        audit_payload_capped=False,
        timestamp=_utc_iso(transition.occurred_at_ms / 1000),
        transition_id=transition.transition_id,
        command_id=transition.command_id,
        attempt_number=transition.attempt_number,
        from_state=transition.from_state,
        to_state=transition.to_state,
        wire_dt=command.wire_dt if has_wire_identity else None,
        tsec_text=attempt.tsec_text if attempt is not None else None,
        ver_text=attempt.ver_text if attempt is not None else None,
        crc_text=attempt.crc_text if attempt is not None else None,
        write_outcome=write_outcome,
        wire_length=attempt.wire_length if attempt is not None else None,
        wire_frame=wire_frame,
        evidence_id=evidence_id,
        evidence_frame=evidence_frame,
        error=error,
    )


def _canonical_payload_sha256(record: SettingsAuditRecord) -> bytes:
    """Return the domain-separated canonical pre-budget payload identity."""
    serialized = json.dumps(
        record_to_dict(record),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"settings-audit-v2\0" + serialized).digest()


@dataclass(frozen=True, slots=True)
class AuditAccountingDiagnostics:
    """Bounded publisher-local accounting state."""

    volatile_entries: int
    volatile_payload_bytes: int


@dataclass(frozen=True, slots=True)
class AuditReplayReport:
    """Ordered transition IDs attempted by one bounded restart replay."""

    transition_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _AuditLedgerCompletion:
    """Definitive executor outcome plus cancellation observed while waiting."""

    result: Any | None
    error: BaseException | None
    owner_cancellation: asyncio.CancelledError | None


def _audit_control_flow_chain(
    *errors: BaseException | None,
    excluded: tuple[BaseException, ...] = (),
) -> BaseException | None:
    """Compose exact audit errors and explicit causes without cycles."""
    unique_errors: list[BaseException] = []
    seen = {id(error) for error in excluded}
    for error in errors:
        current = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            unique_errors.append(current)
            current = current.__cause__
    chain: BaseException | None = None
    for error in reversed(unique_errors):
        if chain is None:
            if error.__cause__ is not None:
                error.__cause__ = None
            chain = error
            continue
        if error.__cause__ is chain:
            chain = error
            continue
        try:
            raise error from chain
        except BaseException as chained:  # pylint: disable=broad-exception-caught
            chain = chained
    return chain


def _raise_audit_owner_cancellation(
    owner_cancellation: asyncio.CancelledError,
    cause: BaseException | None = None,
) -> NoReturn:
    """Raise one exact audit owner with cycle-free cleanup provenance."""
    chain = _audit_control_flow_chain(
        owner_cancellation.__cause__,
        cause,
        excluded=(owner_cancellation,),
    )
    if chain is None:
        if owner_cancellation.__cause__ is owner_cancellation:
            owner_cancellation.__cause__ = None
        raise owner_cancellation
    raise owner_cancellation from chain


def _first_audit_owner_cancellation(
    current: asyncio.CancelledError | None,
    candidate: asyncio.CancelledError | None,
) -> asyncio.CancelledError | None:
    """Retain the first exact owner cancellation across cleanup phases."""
    return current if current is not None else candidate


async def _offload_audit_ledger(
    operation: Callable[[], Any],
) -> _AuditLedgerCompletion:
    """Own a raw executor completion independently of asyncio wrappers."""
    worker = _AUDIT_LEDGER_EXECUTOR.submit(operation)
    wrapped = asyncio.wrap_future(worker)
    owner_cancellation: asyncio.CancelledError | None = None
    result: Any = None
    worker_error: BaseException | None = None
    while True:
        try:
            result = await asyncio.shield(wrapped)
            break
        except asyncio.CancelledError as error:
            if wrapped.cancelled():
                worker_error = error
                break
            if wrapped.done():
                operation_error = wrapped.exception()
                if isinstance(operation_error, asyncio.CancelledError):
                    if (
                        error is not operation_error
                        and owner_cancellation is None
                    ):
                        owner_cancellation = error
                    worker_error = operation_error
                    break
            if owner_cancellation is None:
                owner_cancellation = error
        except BaseException as error:  # pylint: disable=broad-exception-caught
            worker_error = error
            break
    return _AuditLedgerCompletion(
        result,
        worker_error,
        owner_cancellation,
    )


def _audit_lifecycle_lock(audit_id: str) -> _AuditLifecycleStripe:
    digest = hashlib.sha256(audit_id.encode("utf-8")).digest()
    stripe = int.from_bytes(digest[:2], "big") % len(_AUDIT_LIFECYCLE_LOCKS)
    return _AUDIT_LIFECYCLE_LOCKS[stripe]


async def _acquire_audit_lifecycle_lock(
    lock: Any,
) -> asyncio.CancelledError | None:
    """Acquire one stripe and return exact cancellation retained by its owner."""
    executor = (
        lock.executor
        if isinstance(lock, _AuditLifecycleStripe)
        else _AUDIT_EXTERNAL_LOCK_EXECUTOR
    )
    worker = executor.submit(lock.acquire)
    wrapped = asyncio.wrap_future(worker)
    outer_cancellation: asyncio.CancelledError | None = None
    acquired = False
    worker_error: BaseException | None = None
    while True:
        try:
            acquired = await asyncio.shield(wrapped)
            break
        except asyncio.CancelledError as error:
            if wrapped.cancelled():
                worker_error = error
                break
            if wrapped.done():
                operation_error = wrapped.exception()
                if isinstance(operation_error, asyncio.CancelledError):
                    if (
                        error is not operation_error
                        and outer_cancellation is None
                    ):
                        outer_cancellation = error
                    worker_error = operation_error
                    break
            if outer_cancellation is None:
                outer_cancellation = error
        except BaseException as error:  # pylint: disable=broad-exception-caught
            worker_error = error
            break
    if worker_error is not None:
        if outer_cancellation is not None:
            _raise_audit_owner_cancellation(
                outer_cancellation,
                worker_error,
            )
        raise worker_error
    if not acquired:
        acquisition_error = RuntimeError(
            "audit lifecycle lock acquisition failed"
        )
        if outer_cancellation is not None:
            _raise_audit_owner_cancellation(
                outer_cancellation,
                acquisition_error,
            )
        raise acquisition_error
    if outer_cancellation is not None:
        if isinstance(lock, _AuditLifecycleStripe):
            return outer_cancellation
        lock.release()
        _raise_audit_owner_cancellation(outer_cancellation)
    return None


class SettingsAuditPublisher:
    """Durable, replay-stable committed-transition publisher boundary."""

    __slots__ = ("_sink", "_acceptance_ledger")

    def __init__(
        self,
        sink: Callable[[SettingsAuditRecord], None] | None = None,
        *,
        acceptance_ledger: AuditAcceptanceLedger | None = None,
    ) -> None:
        if sink is not None and acceptance_ledger is None:
            raise ValueError("a durable acceptance_ledger is required")
        self._sink = sink
        self._acceptance_ledger = acceptance_ledger

    @property
    def accounting_diagnostics(self) -> AuditAccountingDiagnostics:
        """Report the intentionally empty volatile accounting footprint."""
        return AuditAccountingDiagnostics(0, 0)

    @staticmethod
    def _bounded_record(
        record: SettingsAuditRecord,
        decision: AuditDeliveryDecision,
        canonical_payload_sha256: bytes,
    ) -> SettingsAuditRecord:
        if record.transition_id != decision.transition_id:
            raise ValueError("audit delivery transition identity changed")
        if record.audit_id != decision.audit_id:
            raise ValueError("audit delivery audit identity changed")
        if record.command_id != decision.command_id:
            raise ValueError("audit delivery command identity changed")
        if canonical_payload_sha256 != decision.canonical_payload_sha256:
            raise ValueError("audit delivery canonical identity changed")
        decision.verify_integrity()
        raw_text = _truncate_utf8_text(record.raw_text, decision.raw_bytes)
        return replace(
            record,
            raw_text=raw_text,
            raw_text_truncated=(
                record.raw_text_truncated
                or len(raw_text.encode("utf-8", errors="replace"))
                < len(record.raw_text.encode("utf-8", errors="replace"))
            ),
            audit_payload_capped=decision.payload_capped,
        )

    @staticmethod
    def _identity(record: SettingsAuditRecord) -> tuple[str, int]:
        if record.transition_id is None:
            raise ValueError("committed audit record requires transition_id")
        return record.audit_id, record.transition_id

    @staticmethod
    def _assert_authoritative_projection(
        caller: SettingsAuditRecord,
        authoritative: SettingsAuditRecord,
    ) -> bytes:
        caller_digest = _canonical_payload_sha256(caller)
        authoritative_digest = _canonical_payload_sha256(authoritative)
        if (
            caller.transition_id != authoritative.transition_id
            or caller.audit_id != authoritative.audit_id
            or caller.command_id != authoritative.command_id
            or caller_digest != authoritative_digest
        ):
            raise ValueError("audit delivery canonical identity changed")
        return authoritative_digest

    @staticmethod
    def _validate_page_size(page_size: int) -> None:
        if type(page_size) is not int or not 1 <= page_size <= 1024:
            raise ValueError("page_size must be between 1 and 1024")

    def publish_committed(self, snapshot: TransitionAuditSnapshot) -> None:
        """Publish off-loop; running-loop callers must use the async API."""
        if self._sink is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "durable audit publication on a running loop requires "
                "publish_committed_async"
            )
        caller_record = _project_committed(snapshot)
        ledger = self._acceptance_ledger
        if ledger is None:
            raise RuntimeError("durable acceptance ledger is unavailable")
        try:
            authoritative_snapshot = ledger.read_transition_audit_snapshot(
                snapshot.transition.transition_id
            )
            record = _project_committed(authoritative_snapshot)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("settings audit authoritative projection failed")
            return
        canonical_digest = self._assert_authoritative_projection(
            caller_record,
            record,
        )
        audit_id, transition_id = self._identity(record)
        if record.command_id is None:
            raise ValueError("committed audit record requires command_id")
        with _audit_lifecycle_lock(audit_id):
            try:
                decision = ledger.propose_audit_delivery(
                    audit_id=audit_id,
                    command_id=record.command_id,
                    transition_id=transition_id,
                    canonical_payload_sha256=canonical_digest,
                    requested_raw_bytes=len(
                        record.raw_text.encode("utf-8", errors="replace")
                    ),
                )
                bounded = self._bounded_record(
                    record,
                    decision,
                    canonical_digest,
                )
            except ValueError:
                raise
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("settings audit delivery proposal failed")
                return
            try:
                self._sink(replace(bounded))
            except Exception:  # pylint: disable=broad-exception-caught
                try:
                    ledger.reject_audit_delivery(
                        audit_id=audit_id,
                        transition_id=transition_id,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("settings audit proposal rollback failed")
                logger.exception("settings audit sink rejected committed transition")
                return
            try:
                ledger.accept_audit_delivery(
                    audit_id=audit_id,
                    transition_id=transition_id,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "settings audit acceptance finalization failed; "
                    "replay remains pending"
                )

    async def publish_committed_async(
        self, snapshot: TransitionAuditSnapshot
    ) -> None:
        """Publish with ledger SQLite work offloaded from the owning loop."""
        if self._sink is None:
            return
        caller_record = _project_committed(snapshot)
        ledger = self._acceptance_ledger
        if ledger is None:
            raise RuntimeError("durable acceptance ledger is unavailable")
        authority = await _offload_audit_ledger(
            partial(
                ledger.read_transition_audit_snapshot,
                snapshot.transition.transition_id,
            )
        )
        owner_cancellation = authority.owner_cancellation
        cancellation_cause = authority.error
        if authority.error is not None:
            if not isinstance(authority.error, Exception):
                if owner_cancellation is not None:
                    _raise_audit_owner_cancellation(
                        owner_cancellation,
                        authority.error,
                    )
                raise authority.error
            logger.error(
                "settings audit authoritative projection failed",
                exc_info=(
                    type(authority.error),
                    authority.error,
                    authority.error.__traceback__,
                ),
            )
            if owner_cancellation is not None:
                _raise_audit_owner_cancellation(
                    owner_cancellation,
                    authority.error,
                )
            return
        try:
            record = _project_committed(
                cast("TransitionAuditSnapshot", authority.result)
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.exception("settings audit authoritative projection failed")
            if owner_cancellation is not None:
                _raise_audit_owner_cancellation(owner_cancellation, error)
            return
        except BaseException as error:  # pylint: disable=broad-exception-caught
            if owner_cancellation is not None:
                _raise_audit_owner_cancellation(owner_cancellation, error)
            raise
        try:
            canonical_digest = self._assert_authoritative_projection(
                caller_record,
                record,
            )
            audit_id, transition_id = self._identity(record)
            if record.command_id is None:
                raise ValueError("committed audit record requires command_id")
            lifecycle_lock = _audit_lifecycle_lock(audit_id)
            acquisition_cancellation = (
                await _acquire_audit_lifecycle_lock(lifecycle_lock)
            )
        except BaseException as error:  # pylint: disable=broad-exception-caught
            if owner_cancellation is not None:
                _raise_audit_owner_cancellation(owner_cancellation, error)
            raise
        owner_cancellation = _first_audit_owner_cancellation(
            owner_cancellation,
            acquisition_cancellation,
        )
        integrity_error: ValueError | None = None
        control_flow_error: BaseException | None = None
        body_error: BaseException | None = None
        try:
            proposal = await _offload_audit_ledger(
                partial(
                    ledger.propose_audit_delivery,
                    audit_id=audit_id,
                    command_id=record.command_id,
                    transition_id=transition_id,
                    canonical_payload_sha256=canonical_digest,
                    requested_raw_bytes=len(
                        record.raw_text.encode("utf-8", errors="replace")
                    ),
                )
            )
            owner_cancellation = _first_audit_owner_cancellation(
                owner_cancellation,
                proposal.owner_cancellation,
            )
            cancellation_cause = proposal.error
            if proposal.error is not None:
                if isinstance(proposal.error, ValueError):
                    integrity_error = proposal.error
                elif not isinstance(proposal.error, Exception):
                    control_flow_error = proposal.error
                else:
                    logger.error(
                        "settings audit delivery proposal failed",
                        exc_info=(
                            type(proposal.error),
                            proposal.error,
                            proposal.error.__traceback__,
                        ),
                    )
            else:
                try:
                    bounded = self._bounded_record(
                        record,
                        cast("AuditDeliveryDecision", proposal.result),
                        canonical_digest,
                    )
                except ValueError as error:
                    integrity_error = error
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("settings audit delivery proposal failed")
                else:
                    try:
                        self._sink(replace(bounded))
                    except Exception:  # pylint: disable=broad-exception-caught
                        rejection = await _offload_audit_ledger(
                            partial(
                                ledger.reject_audit_delivery,
                                audit_id=audit_id,
                                transition_id=transition_id,
                            )
                        )
                        owner_cancellation = _first_audit_owner_cancellation(
                            owner_cancellation,
                            rejection.owner_cancellation,
                        )
                        if rejection.error is not None:
                            cancellation_cause = rejection.error
                            if not isinstance(rejection.error, Exception):
                                control_flow_error = rejection.error
                            logger.error(
                                "settings audit proposal rollback failed",
                                exc_info=(
                                    type(rejection.error),
                                    rejection.error,
                                    rejection.error.__traceback__,
                                ),
                            )
                        logger.exception(
                            "settings audit sink rejected committed transition"
                        )
                    else:
                        acceptance = await _offload_audit_ledger(
                            partial(
                                ledger.accept_audit_delivery,
                                audit_id=audit_id,
                                transition_id=transition_id,
                            )
                        )
                        owner_cancellation = _first_audit_owner_cancellation(
                            owner_cancellation,
                            acceptance.owner_cancellation,
                        )
                        if acceptance.error is not None:
                            cancellation_cause = acceptance.error
                            if not isinstance(acceptance.error, Exception):
                                control_flow_error = acceptance.error
                            logger.error(
                                "settings audit acceptance finalization failed; "
                                "replay remains pending",
                                exc_info=(
                                    type(acceptance.error),
                                    acceptance.error,
                                    acceptance.error.__traceback__,
                                ),
                            )
        except BaseException as caught:  # pylint: disable=broad-exception-caught
            body_error = caught
        finally:
            lifecycle_lock.release()
        if body_error is not None:
            if owner_cancellation is not None:
                _raise_audit_owner_cancellation(
                    owner_cancellation,
                    body_error,
                )
            raise body_error
        if owner_cancellation is not None:
            owner_cause = cancellation_cause
            if owner_cause is None:
                owner_cause = integrity_error
            if owner_cause is None:
                owner_cause = control_flow_error
            _raise_audit_owner_cancellation(
                owner_cancellation,
                owner_cause,
            )
        if control_flow_error is not None:
            raise control_flow_error
        if integrity_error is not None:
            raise integrity_error

    def replay_pending(self, *, page_size: int = 128) -> AuditReplayReport:
        """Replay every pending durable transition without retained snapshots."""
        self._validate_page_size(page_size)
        ledger = self._acceptance_ledger
        if ledger is None:
            raise RuntimeError("durable acceptance ledger is unavailable")
        attempted: list[int] = []
        after_transition_id = 0
        while True:
            transition_ids = ledger.read_pending_audit_transition_ids(
                after_transition_id=after_transition_id,
                limit=page_size,
            )
            if not transition_ids:
                break
            for transition_id in transition_ids:
                snapshot = ledger.read_transition_audit_snapshot(
                    transition_id
                )
                self.publish_committed(snapshot)
                attempted.append(transition_id)
            after_transition_id = transition_ids[-1]
        return AuditReplayReport(tuple(attempted))

    async def replay_pending_async(
        self, *, page_size: int = 128
    ) -> AuditReplayReport:
        """Asynchronously replay pending transitions from durable pages."""
        self._validate_page_size(page_size)
        ledger = self._acceptance_ledger
        if ledger is None:
            raise RuntimeError("durable acceptance ledger is unavailable")
        attempted: list[int] = []
        after_transition_id = 0
        while True:
            page = await _offload_audit_ledger(
                partial(
                    ledger.read_pending_audit_transition_ids,
                    after_transition_id=after_transition_id,
                    limit=page_size,
                )
            )
            if page.error is not None:
                if page.owner_cancellation is not None:
                    _raise_audit_owner_cancellation(
                        page.owner_cancellation,
                        page.error,
                    )
                raise page.error
            if page.owner_cancellation is not None:
                _raise_audit_owner_cancellation(page.owner_cancellation)
            transition_ids = page.result
            if not transition_ids:
                break
            for transition_id in transition_ids:
                snapshot_result = await _offload_audit_ledger(
                    partial(
                        ledger.read_transition_audit_snapshot,
                        transition_id,
                    )
                )
                if snapshot_result.error is not None:
                    if snapshot_result.owner_cancellation is not None:
                        _raise_audit_owner_cancellation(
                            snapshot_result.owner_cancellation,
                            snapshot_result.error,
                        )
                    raise snapshot_result.error
                if snapshot_result.owner_cancellation is not None:
                    _raise_audit_owner_cancellation(
                        snapshot_result.owner_cancellation
                    )
                await self.publish_committed_async(
                    cast(
                        "TransitionAuditSnapshot",
                        snapshot_result.result,
                    )
                )
                attempted.append(transition_id)
            after_transition_id = transition_ids[-1]
        return AuditReplayReport(tuple(attempted))


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
        self._acked_sessions: dict[
            str, deque[CloudSettingAuditRecord]
        ] = {}

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
        if response.result == "ACK":
            self._acked_sessions.setdefault(session_id, deque()).append(record)
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
        """Match exact execution evidence to one ACKed session observation."""
        queue = self._acked_sessions.get(session_id)
        if not queue:
            return None
        pending = next(
            (
                record
                for record in queue
                if (
                    record.device_id,
                    record.table_name,
                    record.item_name,
                    record.value_text,
                )
                == (
                    event.device_id,
                    event.table_name,
                    event.item_name,
                    event.new_value_text,
                )
            ),
            None,
        )
        if pending is None:
            return None
        queue.remove(pending)
        if not queue:
            self._acked_sessions.pop(session_id, None)
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
        acknowledged = self._acked_sessions.pop(session_id, deque())
        pending = self._sessions.pop(session_id, deque())
        timestamp = (
            time.time_ns() // 1_000_000
            if observed_at_ms is None
            else observed_at_ms
        )
        retained = (*acknowledged, *pending)
        closed = tuple(
            replace(record, step=reason, observed_at_ms=timestamp)
            for record in retained
        )
        for record in closed:
            self._publish(record)
        return closed
