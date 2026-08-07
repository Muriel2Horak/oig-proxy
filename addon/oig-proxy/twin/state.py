"""Immutable durable twin transaction state and temporary queue compatibility."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


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


def _require_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


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


@dataclass
class TwinSetting:  # pylint: disable=too-many-instance-attributes
    """Legacy mutable queue record retained until the runtime cutover."""

    table: str
    key: str
    value: Any
    enqueued_at: float
    raw_text: str = ""
    audit_id: str = ""
    msg_id: int = 0
    id_set: int = 0
    confirm: str = "New"


class TwinQueue:
    """Legacy in-memory queue retained only for unchanged compatibility users."""

    def __init__(self) -> None:
        self._queue: dict[tuple[str, str], TwinSetting] = {}
        self._next_id_set = int(time.time())

    def _generate_msg_id(self) -> int:
        return secrets.randbelow(1_000_000) + 14_000_000

    def _generate_id_set(self) -> int:
        id_set = self._next_id_set
        self._next_id_set += 1
        if self._next_id_set > 9_999_999_999:
            self._next_id_set = int(time.time())
        return id_set

    def _generate_audit_id(self) -> str:
        now_epoch = int(time.time() * 1000)
        return f"aud_{now_epoch:014d}_{secrets.randbelow(1_000_000):06d}"

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def enqueue(
        self,
        table: str,
        key: str,
        value: Any,
        confirm: str = "New",
        audit_id: str = "",
        raw_text: str = "",
    ) -> None:
        """Insert or replace one legacy setting by table and key."""
        if not audit_id:
            audit_id = self._generate_audit_id()
        setting = TwinSetting(
            table=table,
            key=key,
            value=value,
            enqueued_at=time.time(),
            raw_text=raw_text,
            audit_id=audit_id,
            msg_id=self._generate_msg_id(),
            id_set=self._generate_id_set(),
            confirm=confirm,
        )
        self._queue[(table, key)] = setting

    def get_pending(self) -> list[TwinSetting]:
        """Return legacy settings in enqueue-time order."""
        return sorted(self._queue.values(), key=lambda setting: setting.enqueued_at)

    def acknowledge(self, table: str, key: str) -> bool:
        """Remove one legacy setting when present."""
        key_tuple = (table, key)
        if key_tuple in self._queue:
            del self._queue[key_tuple]
            return True
        return False

    def size(self) -> int:
        """Return the number of legacy queued settings."""
        return len(self._queue)

    def clear(self) -> None:
        """Remove all legacy queued settings."""
        self._queue.clear()

    def get(self, table: str, key: str) -> TwinSetting | None:
        """Return one legacy queued setting when present."""
        return self._queue.get((table, key))
