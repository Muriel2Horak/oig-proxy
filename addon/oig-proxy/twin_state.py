"""Twin State – Typed DTOs for Cloud Digital Twin Interface Contract.

This module defines the data transfer objects (DTOs) used in the
twin adapter interface contract. All DTOs enforce tx_id and conn_id
fields for proper transaction and connection tracking.

Related Invariants (from Task 1):
- INV-1: Connection Ownership - ACK/NACK must arrive on same connection
- INV-2: Session Transaction - inflight must belong to current session
- INV-3: Timeout Task Ownership - timeout must validate tx_id identity
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AckResult(str, Enum):
    """Result of ACK handling."""
    ACK = "ACK"
    NACK = "NACK"
    END = "END"
    TIMEOUT = "TIMEOUT"
    DISCONNECT = "DISCONNECT"


class SettingStage(str, Enum):
    """Stage of a setting transaction."""
    ACCEPTED = "accepted"
    SENT_TO_BOX = "sent_to_box"
    BOX_ACK = "box_ack"
    APPLIED = "applied"
    COMPLETED = "completed"
    ERROR = "error"
    DEFERRED = "deferred"


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
class QueueSettingDTO:
    """DTO for queuing a setting command.

    Used when enqueueing a new setting to be sent to the BOX.
    All fields are required for proper transaction tracking.
    """
    tx_id: str
    conn_id: int
    tbl_name: str
    tbl_item: str
    new_value: str
    confirm: str = "New"
    request_key: str | None = None
    received_at: str | None = None
    raw_frame: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "tbl_name": self.tbl_name,
            "tbl_item": self.tbl_item,
            "new_value": self.new_value,
            "confirm": self.confirm,
            "request_key": self.request_key,
            "received_at": self.received_at,
            "raw_frame": self.raw_frame,
        }


@dataclass(frozen=True)
class PollResponseDTO:
    """DTO for poll response data.

    Represents the response from a poll operation (IsNewSet, IsNewWeather, etc.)
    """
    tx_id: str | None
    conn_id: int
    table_name: str | None
    ack: bool
    frame_data: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "table_name": self.table_name,
            "ack": self.ack,
            "frame_data": self.frame_data,
            "error": self.error,
        }


@dataclass(frozen=True)
class OnAckDTO:
    """DTO for ACK/NACK handling.

    Enforces INV-1: Connection Ownership - the conn_id must match
    the connection where the setting was delivered.

    Attributes:
        tx_id: Transaction identifier for matching
        conn_id: Connection identifier for ownership validation
        ack: True for ACK, False for NACK
        delivered_conn_id: The conn_id where setting was delivered
                           (for INV-1 validation)
    """
    tx_id: str
    conn_id: int
    ack: bool
    delivered_conn_id: int | None = None
    result: AckResult = AckResult.ACK
    timestamp: str | None = None

    def validate_conn_ownership(self) -> bool:
        """Validate INV-1: Connection Ownership invariant.

        Returns True if conn_id matches delivered_conn_id.
        If delivered_conn_id is None, validation passes (legacy mode).
        """
        if self.delivered_conn_id is None:
            return True
        return self.conn_id == self.delivered_conn_id

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "ack": self.ack,
            "delivered_conn_id": self.delivered_conn_id,
            "result": self.result.value,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
class OnTblEventDTO:
    """DTO for tbl_events handling.

    Used when processing Setting events from tbl_events table.
    """
    tx_id: str | None
    conn_id: int
    event_type: str
    content: str | None = None
    tbl_name: str | None = None
    tbl_item: str | None = None
    old_value: str | None = None
    new_value: str | None = None

    def is_setting_event(self) -> bool:
        """Check if this is a Setting event."""
        return self.event_type == "Setting"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "event_type": self.event_type,
            "content": self.content,
            "tbl_name": self.tbl_name,
            "tbl_item": self.tbl_item,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


@dataclass(frozen=True)
class OnDisconnectDTO:
    """DTO for disconnect handling.

    Used when a BOX disconnect is detected to clean up state.
    """
    tx_id: str | None
    conn_id: int
    session_id: str | None = None
    reason: str = "disconnect"
    timestamp: str | None = None
    pending_tx_id: str | None = None
    pending_delivered: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "session_id": self.session_id,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "pending_tx_id": self.pending_tx_id,
            "pending_delivered": self.pending_delivered,
        }


# pylint: disable=too-many-instance-attributes
@dataclass
class PendingSettingState:
    """Mutable state for a pending setting.

    Tracks the lifecycle of a setting from queue to ACK/NACK.
    Mirrors the structure from control_settings.py pending dict.
    """
    tx_id: str
    conn_id: int
    tbl_name: str
    tbl_item: str
    new_value: str
    confirm: str = "New"
    msg_id: int | None = None
    id_set: int | None = None
    stage: SettingStage = SettingStage.ACCEPTED
    sent_at: float | None = None
    delivered_conn_id: int | None = None
    delivered_at_mono: float | None = None
    attempts: int = 0
    max_attempts: int = 5
    replay_count: int = 0
    raw_frame: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "tbl_name": self.tbl_name,
            "tbl_item": self.tbl_item,
            "new_value": self.new_value,
            "confirm": self.confirm,
            "id": self.msg_id,
            "id_set": self.id_set,
            "stage": self.stage.value,
            "sent_at": self.sent_at,
            "delivered_conn_id": self.delivered_conn_id,
            "delivered_at_mono": self.delivered_at_mono,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "replay_count": self.replay_count,
            "raw_frame": self.raw_frame,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingSettingState":
        """Create from dictionary."""
        stage_str = data.get("stage", "accepted")
        try:
            stage = SettingStage(stage_str)
        except ValueError:
            stage = SettingStage.ACCEPTED

        return cls(
            tx_id=str(data.get("tx_id", "")),
            conn_id=int(data.get("conn_id", 0)),
            tbl_name=str(data.get("tbl_name", "")),
            tbl_item=str(data.get("tbl_item", "")),
            new_value=str(data.get("new_value", "")),
            confirm=str(data.get("confirm", "New")),
            msg_id=data.get("id") or data.get("msg_id"),
            id_set=data.get("id_set"),
            stage=stage,
            sent_at=data.get("sent_at"),
            delivered_conn_id=data.get("delivered_conn_id"),
            delivered_at_mono=data.get("delivered_at_mono"),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 5)),
            replay_count=int(data.get("replay_count", 0)),
            raw_frame=data.get("raw_frame"),
        )

    # ------------------------------------------------------------------
    # Atomic State Transitions
    # ------------------------------------------------------------------

    def transition_to(
        self,
        new_stage: SettingStage,
        *,
        delivered_conn_id: int | None = None,
        validate_from: set[SettingStage] | None = None,
    ) -> "PendingSettingState":
        """Atomically transition to a new stage.

        This method creates a new PendingSettingState with the updated stage,
        enforcing valid state transitions. Use this instead of directly
        modifying the stage attribute.

        Args:
            new_stage: The target stage to transition to
            delivered_conn_id: Optional connection ID where setting was delivered
            validate_from: Optional set of valid source stages (if None, all allowed)

        Returns:
            New PendingSettingState with updated stage

        Raises:
            ValueError: If transition is invalid (wrong source stage)

        Valid transitions:
            ACCEPTED → SENT_TO_BOX (when delivered)
            SENT_TO_BOX → BOX_ACK (when ACK received)
            SENT_TO_BOX → ERROR (when NACK received)
            BOX_ACK → APPLIED (when Setting event observed)
            APPLIED → COMPLETED (when quiet window passes)
            Any → ERROR (on failure)
            Any → DEFERRED (on retry)
        """
        if validate_from is not None and self.stage not in validate_from:
            raise ValueError(
                f"Invalid state transition: cannot transition from "
                f"{self.stage.value} to {new_stage.value}"
            )

        return PendingSettingState(
            tx_id=self.tx_id,
            conn_id=self.conn_id,
            tbl_name=self.tbl_name,
            tbl_item=self.tbl_item,
            new_value=self.new_value,
            confirm=self.confirm,
            msg_id=self.msg_id,
            id_set=self.id_set,
            stage=new_stage,
            sent_at=self.sent_at,
            delivered_conn_id=delivered_conn_id or self.delivered_conn_id,
            delivered_at_mono=self.delivered_at_mono,
            attempts=self.attempts,
            max_attempts=self.max_attempts,
            replay_count=self.replay_count,
        )

    def mark_delivered(
        self,
        delivered_conn_id: int,
        delivered_at_mono: float | None = None,
    ) -> "PendingSettingState":
        """Mark setting as delivered on a specific connection.

        This is an atomic operation that transitions the state to SENT_TO_BOX
        and records the delivery connection for INV-1 validation.

        Args:
            delivered_conn_id: Connection ID where setting was delivered
            delivered_at_mono: Monotonic timestamp of delivery (defaults to now)

        Returns:
            New PendingSettingState with delivery info recorded
        """
        import time

        return PendingSettingState(
            tx_id=self.tx_id,
            conn_id=self.conn_id,
            tbl_name=self.tbl_name,
            tbl_item=self.tbl_item,
            new_value=self.new_value,
            confirm=self.confirm,
            msg_id=self.msg_id,
            id_set=self.id_set,
            stage=SettingStage.SENT_TO_BOX,
            sent_at=self.sent_at,
            delivered_conn_id=delivered_conn_id,
            delivered_at_mono=delivered_at_mono or time.monotonic(),
            attempts=self.attempts,
            max_attempts=self.max_attempts,
            replay_count=self.replay_count,
        )

    def mark_ack_received(
        self,
        ack: bool,
    ) -> "PendingSettingState":
        """Mark ACK/NACK as received.

        This is an atomic operation that transitions to BOX_ACK or ERROR
        based on the ACK result.

        Args:
            ack: True for ACK, False for NACK

        Returns:
            New PendingSettingState with updated stage
        """
        new_stage = SettingStage.BOX_ACK if ack else SettingStage.ERROR
        return self.transition_to(
            new_stage,
            validate_from={SettingStage.SENT_TO_BOX, SettingStage.ACCEPTED},
        )

    def mark_applied(self) -> "PendingSettingState":
        """Mark setting as applied (Setting event observed).

        Returns:
            New PendingSettingState with APPLIED stage
        """
        return self.transition_to(
            SettingStage.APPLIED,
            validate_from={SettingStage.BOX_ACK, SettingStage.SENT_TO_BOX},
        )

    def mark_completed(self) -> "PendingSettingState":
        """Mark transaction as completed.

        Returns:
            New PendingSettingState with COMPLETED stage
        """
        return self.transition_to(
            SettingStage.COMPLETED,
            validate_from={SettingStage.APPLIED, SettingStage.BOX_ACK},
        )

    def mark_error(self) -> "PendingSettingState":
        """Mark transaction as failed.

        Returns:
            New PendingSettingState with ERROR stage
        """
        return self.transition_to(SettingStage.ERROR)

    def mark_deferred(self, increment_attempts: bool = True) -> "PendingSettingState":
        """Mark transaction as deferred for retry.

        Args:
            increment_attempts: Whether to increment attempt counter

        Returns:
            New PendingSettingState with DEFERRED stage
        """
        return PendingSettingState(
            tx_id=self.tx_id,
            conn_id=self.conn_id,
            tbl_name=self.tbl_name,
            tbl_item=self.tbl_item,
            new_value=self.new_value,
            confirm=self.confirm,
            msg_id=self.msg_id,
            id_set=self.id_set,
            stage=SettingStage.DEFERRED,
            sent_at=self.sent_at,
            delivered_conn_id=self.delivered_conn_id,
            delivered_at_mono=self.delivered_at_mono,
            attempts=self.attempts + (1 if increment_attempts else 0),
            max_attempts=self.max_attempts,
            replay_count=self.replay_count,
        )

    def validate_conn_ownership(self, incoming_conn_id: int) -> bool:
        """Validate INV-1: Connection Ownership.

        Check if an operation on incoming_conn_id is allowed for this
        pending setting, which was delivered on delivered_conn_id.

        Args:
            incoming_conn_id: Connection ID where operation is occurring

        Returns:
            True if invariant is satisfied, False otherwise
        """
        # If not yet delivered, any connection is valid
        if self.delivered_conn_id is None:
            return True
        return incoming_conn_id == self.delivered_conn_id

    def is_terminal(self) -> bool:
        """Check if the transaction is in a terminal state.

        Returns:
            True if the transaction is completed or errored
        """
        return self.stage in (SettingStage.COMPLETED, SettingStage.ERROR)

    def can_transition_to(self, new_stage: SettingStage) -> bool:
        """Check if a transition to new_stage is valid.

        Args:
            new_stage: The target stage

        Returns:
            True if the transition is valid
        """
        valid_transitions: dict[SettingStage, set[SettingStage]] = {
            SettingStage.ACCEPTED: {
                SettingStage.SENT_TO_BOX,
                SettingStage.ERROR,
                SettingStage.DEFERRED,
            },
            SettingStage.SENT_TO_BOX: {
                SettingStage.BOX_ACK,
                SettingStage.ERROR,
                SettingStage.DEFERRED,
            },
            SettingStage.BOX_ACK: {
                SettingStage.APPLIED,
                SettingStage.COMPLETED,
                SettingStage.ERROR,
            },
            SettingStage.APPLIED: {
                SettingStage.COMPLETED,
                SettingStage.ERROR,
            },
            SettingStage.DEFERRED: {
                SettingStage.SENT_TO_BOX,
                SettingStage.ERROR,
            },
            SettingStage.COMPLETED: set(),  # Terminal
            SettingStage.ERROR: set(),  # Terminal
        }

        allowed = valid_transitions.get(self.stage, set())
        return new_stage in allowed


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
class SnapshotDTO:
    """DTO for state snapshots.

    Provides a complete snapshot of the twin adapter state.
    """
    tx_id: str | None  # Current inflight tx_id
    conn_id: int | None  # Current active conn_id
    session_id: str | None = None
    queue_length: int = 0
    has_inflight: bool = False
    inflight_stage: str | None = None
    inflight_tbl_name: str | None = None
    inflight_tbl_item: str | None = None
    inflight_new_value: str | None = None
    inflight_delivered_conn_id: int | None = None
    pending_keys: list[str] = field(default_factory=list)
    replay_buffer_length: int = 0
    completed_tx_count: int = 0
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "session_id": self.session_id,
            "queue_length": self.queue_length,
            "has_inflight": self.has_inflight,
            "inflight_stage": self.inflight_stage,
            "inflight_tbl_name": self.inflight_tbl_name,
            "inflight_tbl_item": self.inflight_tbl_item,
            "inflight_new_value": self.inflight_new_value,
            "inflight_delivered_conn_id": self.inflight_delivered_conn_id,
            "pending_keys": self.pending_keys,
            "replay_buffer_length": self.replay_buffer_length,
            "completed_tx_count": self.completed_tx_count,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
class TransactionResultDTO:
    """DTO for transaction results.

    Used to report the outcome of a transaction.
    """
    tx_id: str
    conn_id: int
    status: str
    error: str | None = None
    detail: str | None = None
    tbl_name: str | None = None
    tbl_item: str | None = None
    new_value: str | None = None
    old_value: str | None = None
    attempts: int | None = None
    timestamp: str | None = None

    def is_success(self) -> bool:
        """Check if the result indicates success."""
        return self.status in ("completed", "applied", "box_ack")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for compatibility with existing code."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "status": self.status,
            "error": self.error,
            "detail": self.detail,
            "tbl_name": self.tbl_name,
            "tbl_item": self.tbl_item,
            "new_value": self.new_value,
            "old_value": self.old_value,
            "attempts": self.attempts,
            "timestamp": self.timestamp,
        }


def get_timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.utcnow().isoformat() + "Z"
