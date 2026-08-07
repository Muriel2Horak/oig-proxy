"""Immutable durable twin transaction state."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TYPE_CHECKING

from protocol.frame import FrameDirection

if TYPE_CHECKING:
    from .ack_parser import SettingEvent


class CommandState(str, Enum):
    """Durable local-setting transaction states."""

    PENDING = "pending"
    RETRY_PENDING = "retry_pending"
    AWAITING_ACK = "awaiting_ack"
    AWAITING_EVENT = "awaiting_event"
    CONFIRMED = "confirmed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


TERMINAL_STATES = frozenset(
    {
        CommandState.CONFIRMED,
        CommandState.INCOMPLETE,
        CommandState.FAILED,
        CommandState.EXPIRED,
        CommandState.SUPERSEDED,
    }
)


class AttemptWriteOutcome(str, Enum):
    """Durable milestones for one wire write attempt."""

    PREPARED = "prepared"
    STARTED = "started"
    DRAINED = "drained"
    UNKNOWN = "unknown"
    FAILED = "failed"


class AuditDeliveryState(str, Enum):
    """Durable audit sink proposal lifecycle."""

    PENDING = "pending"
    ACCEPTED = "accepted"


class ClaimDisposition(str, Enum):
    """Result of trying to claim a transaction for delivery."""

    PREPARED = "prepared"
    NO_ELIGIBLE = "no_eligible"
    ACTIVE_DELIVERY_ELSEWHERE = "active_delivery_elsewhere"
    CONTROL_DISABLED = "control_disabled"
    RENDER_FAILED = "render_failed"


class IngressDisposition(str, Enum):
    """Audit classification for one control ingress payload."""

    ACCEPTED_COMMAND = "accepted_command"
    ACCEPTED_PROXY_CONTROL = "accepted_proxy_control"
    REJECTED_DISABLED = "rejected_disabled"
    REJECTED_RETAINED = "rejected_retained"
    REJECTED_TOPIC = "rejected_topic"
    REJECTED_UNKNOWN_DEVICE = "rejected_unknown_device"
    REJECTED_DEVICE_MISMATCH = "rejected_device_mismatch"
    REJECTED_OVERSIZE = "rejected_oversize"
    REJECTED_UTF8 = "rejected_utf8"
    REJECTED_JSON = "rejected_json"
    REJECTED_SCHEMA = "rejected_schema"
    REJECTED_NOT_ALLOWED = "rejected_not_allowed"
    REJECTED_VALUE = "rejected_value"
    REJECTED_XML = "rejected_xml"
    REJECTED_STORE = "rejected_store"


class RetryReason(str, Enum):
    """Durable reasons for releasing one exact attempt for retry."""

    WRITE_FAILED = "write_failed"
    WRITE_UNKNOWN = "write_unknown"
    DISCONNECT = "disconnect"
    ACK_TIMEOUT = "ack_timeout"
    UNEXPECTED_RESPONSE = "unexpected_response"
    STREAM_ERROR = "stream_error"
    SHUTDOWN = "shutdown"


class EventDisposition(str, Enum):
    """Result of durably recording one immutable setting event."""

    CONFIRMED = "confirmed"
    UNMATCHED = "unmatched"
    DUPLICATE = "duplicate"


class LocalResponseDisposition(str, Enum):
    """Runtime-facing classification for local-setting response handling."""

    ACK_ACCEPTED = "ack_accepted"
    NEXT_SENT = "next_sent"
    NACK_ACCEPTED = "nack_accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class DeliveryTrigger(str, Enum):
    """Only protocol events authorized to begin or continue local delivery."""

    CORRELATED_CLOUD_END = "correlated_cloud_end"
    OFFLINE_ISNEWSET = "offline_isnewset"
    LOCAL_ACK_CONTINUATION = "local_ack_continuation"


class DeliveryDisposition(str, Enum):
    """Runtime result of one authorized durable delivery attempt."""

    SENT = "sent"
    NO_ELIGIBLE = "no_eligible"
    ACTIVE_DELIVERY_ELSEWHERE = "active_delivery_elsewhere"
    CONTROL_DISABLED = "control_disabled"
    UNAUTHORIZED = "unauthorized"
    RENDER_FAILED = "render_failed"
    WRITE_FAILED = "write_failed"
    WRITE_UNKNOWN = "write_unknown"


def _require_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


_AUDIT_DELIVERY_DECISION_INTEGRITY_DOMAIN = b"settings-audit-decision-v2\0"


def derive_audit_delivery_decision_integrity(
    *,
    transition_id: int,
    audit_id: str,
    command_id: str,
    canonical_payload_sha256: bytes,
    raw_bytes: int,
    payload_capped: bool,
) -> bytes:
    """Derive the v2 digest binding one final audit accounting decision."""
    audit_id_bytes = audit_id.encode("utf-8")
    command_id_bytes = command_id.encode("utf-8")
    encoded = b"".join(
        (
            _AUDIT_DELIVERY_DECISION_INTEGRITY_DOMAIN,
            transition_id.to_bytes(8, "big", signed=False),
            len(audit_id_bytes).to_bytes(4, "big", signed=False),
            audit_id_bytes,
            len(command_id_bytes).to_bytes(4, "big", signed=False),
            command_id_bytes,
            canonical_payload_sha256,
            raw_bytes.to_bytes(4, "big", signed=False),
            b"\x01" if payload_capped else b"\x00",
        )
    )
    return hashlib.sha256(encoded).digest()


@dataclass(frozen=True, slots=True)
class ControlPolicy:
    """Validated transaction lifecycle limits, expressed in milliseconds."""

    ack_timeout_ms: int
    event_timeout_ms: int
    pending_ttl_ms: int
    max_attempts: int

    def __post_init__(self) -> None:
        for name in ("ack_timeout_ms", "event_timeout_ms", "pending_ttl_ms"):
            value = getattr(self, name)
            _require_int(name, value)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        _require_int("max_attempts", self.max_attempts)
        if not 1 <= self.max_attempts <= 8:
            raise ValueError("max_attempts must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class TwinCommand:  # pylint: disable=too-many-instance-attributes
    """Immutable snapshot of every column in one command row."""

    command_id: str
    audit_id: str
    device_id: str
    table_name: str
    item_name: str
    value_text: str
    raw_ingress_text: str
    state: CommandState
    created_at_ms: int
    updated_at_ms: int
    pending_expires_at_ms: int
    wire_id: int | None
    wire_id_set: int | None
    wire_dt: str | None
    attempt_count: int
    active_session_id: str | None
    ack_deadline_ms: int | None
    event_deadline_ms: int | None
    acked_at_ms: int | None
    ack_device_rdt: str | None
    completed_at_ms: int | None
    predecessor_command_id: str | None
    last_wire_frame: bytes | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class CommandAttempt:  # pylint: disable=too-many-instance-attributes
    """Immutable snapshot of every column in one attempt row."""

    command_id: str
    attempt_number: int
    session_id: str
    prepared_at_ms: int
    write_started_at_ms: int | None
    drain_completed_at_ms: int | None
    ack_deadline_ms: int
    tsec_text: str
    ver_text: str
    crc_text: str
    wire_frame: bytes
    wire_length: int
    write_outcome: AttemptWriteOutcome
    write_error: str | None
    response_fingerprint: str | None
    response_rdt: str | None


@dataclass(frozen=True, slots=True)
class CommandTransition:  # pylint: disable=too-many-instance-attributes
    """Immutable snapshot of every column in one transition row."""

    transition_id: int
    command_id: str
    audit_id: str
    from_state: CommandState | None
    to_state: CommandState
    occurred_at_ms: int
    attempt_number: int | None
    session_id: str | None
    reason: str
    error_text: str | None
    wire_frame: bytes | None
    evidence_frame: bytes | None


@dataclass(frozen=True, slots=True)
class AuditDeliveryDecision:
    """Compact replay-stable raw-text decision for one transition."""

    transition_id: int
    audit_id: str
    command_id: str
    canonical_payload_sha256: bytes
    decision_integrity_sha256: bytes
    raw_bytes: int
    payload_capped: bool
    state: AuditDeliveryState

    def __post_init__(self) -> None:
        _require_int("transition_id", self.transition_id)
        if self.transition_id < 1:
            raise ValueError("transition_id must be positive")
        if not self.audit_id or len(self.audit_id) > 256:
            raise ValueError("audit_id length must be between 1 and 256")
        if not self.command_id or len(self.command_id) > 256:
            raise ValueError("command_id length must be between 1 and 256")
        if (
            not isinstance(self.canonical_payload_sha256, bytes)
            or len(self.canonical_payload_sha256) != 32
        ):
            raise ValueError("canonical_payload_sha256 must be 32 bytes")
        if (
            not isinstance(self.decision_integrity_sha256, bytes)
            or len(self.decision_integrity_sha256) != 32
        ):
            raise ValueError("decision_integrity_sha256 must be 32 bytes")
        _require_int("raw_bytes", self.raw_bytes)
        if not 0 <= self.raw_bytes <= 16 * 1024:
            raise ValueError("raw_bytes must be between 0 and 16384")
        if not isinstance(self.payload_capped, bool):
            raise TypeError("payload_capped must be a boolean")
        if not isinstance(self.state, AuditDeliveryState):
            raise TypeError("state must be an AuditDeliveryState")
        self.verify_integrity()

    def verify_integrity(self) -> None:
        """Recompute and verify final accounting at any consumer boundary."""
        expected_integrity = derive_audit_delivery_decision_integrity(
            transition_id=self.transition_id,
            audit_id=self.audit_id,
            command_id=self.command_id,
            canonical_payload_sha256=self.canonical_payload_sha256,
            raw_bytes=self.raw_bytes,
            payload_capped=self.payload_capped,
        )
        if self.decision_integrity_sha256 != expected_integrity:
            raise ValueError("audit delivery decision integrity mismatch")


@dataclass(frozen=True, slots=True)
class SettingEventReceipt:  # pylint: disable=too-many-instance-attributes
    """Immutable snapshot of every column in one event receipt row."""

    evidence_id: str
    received_at_ms: int
    device_id: str
    event_id_set: int
    device_dt: str
    table_name: str
    item_name: str
    old_value_text: str
    new_value_text: str
    evidence_frame: bytes
    disposition: str
    command_id: str | None
    duplicate_count: int
    last_seen_at_ms: int


@dataclass(frozen=True, slots=True)
class ControlIngress:  # pylint: disable=too-many-instance-attributes
    """Immutable snapshot of every column in one ingress audit row."""

    ingress_id: str
    received_at_ms: int
    topic: str
    topic_device_id: str | None
    retain: bool
    raw_text: str
    disposition: IngressDisposition | None = None
    reason: str | None = None
    command_id: str | None = None
    audit_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Observed device identity and the next durable wire counters."""

    device_id: str
    first_seen_at_ms: int
    last_seen_at_ms: int
    next_wire_id: int
    next_wire_id_set: int


@dataclass(frozen=True, slots=True)
class PragmaSnapshot:
    """SQLite safety pragmas read back from the live connection."""

    journal_mode: str
    synchronous: int
    foreign_keys: int
    busy_timeout_ms: int


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Reserved Task 7 recovery transition counts."""

    expired_pending: int
    retry_pending: int
    failed_attempt_limit: int
    kept_awaiting_event: int
    incomplete_event_timeout: int


@dataclass(frozen=True, slots=True)
class StoreStatus:
    """Immutable command counts and control availability snapshot."""

    state_counts: tuple[tuple[CommandState, int], ...]
    nonterminal_commands: int
    control_available: bool
    degradation_reason: str | None

    def __post_init__(self) -> None:
        normalized_counts = tuple(
            (state, count) for state, count in self.state_counts
        )
        if tuple(state for state, _ in normalized_counts) != tuple(CommandState):
            raise ValueError("state_counts must contain every CommandState in enum order")
        for _, count in normalized_counts:
            _require_int("state count", count)
            if count < 0:
                raise ValueError("state counts must be non-negative")
        _require_int("nonterminal_commands", self.nonterminal_commands)
        if self.nonterminal_commands < 0:
            raise ValueError("nonterminal_commands must be non-negative")
        expected_nonterminal = sum(
            count
            for state, count in normalized_counts
            if state not in TERMINAL_STATES
        )
        if self.nonterminal_commands != expected_nonterminal:
            raise ValueError("nonterminal_commands does not match state_counts")
        if self.degradation_reason is not None and len(self.degradation_reason) > 1024:
            raise ValueError("degradation_reason exceeds 1024 characters")
        object.__setattr__(self, "state_counts", normalized_counts)

    def count(self, state: CommandState) -> int:
        """Return the exact count for one command state."""
        for candidate, count in self.state_counts:
            if candidate is state:
                return count
        raise ValueError(f"unknown command state: {state!r}")

    @property
    def pending(self) -> int:
        """Return pending command count for passive telemetry."""
        return self.count(CommandState.PENDING)

    @property
    def retry_pending(self) -> int:
        """Return retry-pending command count for passive telemetry."""
        return self.count(CommandState.RETRY_PENDING)

    @property
    def awaiting_ack(self) -> int:
        """Return active delivery count for passive telemetry."""
        return self.count(CommandState.AWAITING_ACK)

    @property
    def awaiting_event(self) -> int:
        """Return delivery-only ACK count for passive telemetry."""
        return self.count(CommandState.AWAITING_EVENT)


@dataclass(frozen=True, slots=True)
class AttemptRenderContext:
    """Inputs required to render one durable wire attempt."""

    command: TwinCommand
    attempt_number: int
    prepared_at_ms: int
    wire_id: int
    wire_id_set: int
    wire_dt: str
    used_ver_texts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RenderedAttempt:
    """Wire fields and bytes produced by an attempt renderer."""

    tsec_text: str
    ver_text: str
    crc_text: str
    wire_frame: bytes


AttemptRenderer = Callable[[AttemptRenderContext], RenderedAttempt]


@dataclass(frozen=True, slots=True)
class ConfirmedSetting:  # pylint: disable=too-many-instance-attributes
    """Canonical execution confirmation linked to immutable event evidence."""

    command_id: str
    audit_id: str
    evidence_id: str
    device_id: str
    table_name: str
    item_name: str
    value_text: str
    confirmed_at_ms: int


@dataclass(frozen=True, slots=True)
class TransitionAuditSnapshot:
    """Committed command, transition, and optional related durable rows."""

    command: TwinCommand
    transition: CommandTransition
    attempt: CommandAttempt | None = None
    evidence: SettingEventReceipt | None = None


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Atomic accepted-ingress result with optional replaced successor."""

    command: TwinCommand
    superseded_command: TwinCommand | None
    snapshots: tuple[TransitionAuditSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Result of selecting and durably preparing one exact attempt."""

    disposition: ClaimDisposition
    command: TwinCommand | None
    attempt: CommandAttempt | None
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class AckResult:
    """Atomic ACK acceptance and optional next-attempt preparation."""

    accepted_command: TwinCommand | None
    duplicate: bool
    next_claim: ClaimResult
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class NackResult:
    """Atomic terminal NACK result."""

    accepted_command: TwinCommand | None
    duplicate: bool
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class EventMatchResult:
    """Durable event receipt and at-most-once execution match result."""

    disposition: EventDisposition
    command: TwinCommand | None
    prior_state: CommandState | None
    active_session_id: str | None
    evidence: SettingEventReceipt
    confirmation: ConfirmedSetting | None
    snapshot: TransitionAuditSnapshot | None


@dataclass(frozen=True, slots=True)
class EventTimeoutCandidate:
    """Read-only overdue event candidate for runtime socket reconciliation."""

    command_id: str
    device_id: str
    table_name: str
    item_name: str
    value_text: str
    acked_at_ms: int
    ack_device_rdt: str | None
    event_deadline_ms: int


@dataclass(frozen=True, slots=True)
class SweepReport:
    """Committed deadline transition counts and ordered audit snapshots."""

    expired_pending: int
    retry_pending: int
    failed_attempt_limit: int
    incomplete_event_timeout: int
    snapshots: tuple[TransitionAuditSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ActiveLocalAttempt:
    """Socket-facing immutable identity of one prepared local attempt."""

    command_id: str
    audit_id: str
    device_id: str
    attempt_number: int
    session_id: str
    ack_deadline_ms: int
    wire_frame: bytes
    write_outcome: AttemptWriteOutcome


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Exact transport ownership and bytes captured with protocol evidence."""

    direction: FrameDirection
    session_id: str
    device_id: str
    received_at_ms: int
    raw_frame: bytes


@dataclass(frozen=True, slots=True)
class AttemptWriteResult:
    """Writer-reported boundary outcome for one immutable attempt frame."""

    outcome: AttemptWriteOutcome
    started_at_ms: int
    drain_completed_at_ms: int | None
    error_text: str | None


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """Coordinator result for a delivery trigger."""

    disposition: DeliveryDisposition
    active_attempt: ActiveLocalAttempt | None
    close_connection: bool
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalResponseDecision:
    """Coordinator result for exact local ACK or NACK evidence."""

    disposition: LocalResponseDisposition
    command: TwinCommand | None
    next_attempt: ActiveLocalAttempt | None
    send_final_end: bool
    close_connection: bool
    confirmation: ConfirmedSetting | None = None
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class RegisteredEventToken:
    """Synchronous reservation of already-received event evidence."""

    token_id: str
    event: SettingEvent
    context: EvidenceContext


class LocalSettingWriter(Protocol):
    """Serialized BOX writer boundary used by the coordinator."""

    async def write_attempt(
        self,
        attempt: ActiveLocalAttempt,
        *,
        before_write: Callable[[], Awaitable[None]],
    ) -> AttemptWriteResult:
        """Commit write-start immediately before invoking the socket writer."""
