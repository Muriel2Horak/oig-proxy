"""Transaction Context – Conn-bound transaction management for Cloud Digital Twin.

This module provides transaction context classes that bind transactions
to specific connections and sessions, enforcing the invariants defined
in Task 1:

- INV-1: Connection Ownership - ACK/NACK must arrive on same connection
- INV-2: Session Transaction - inflight must belong to current session
- INV-3: Timeout Task Ownership - timeout must validate tx_id identity

The TransactionContext captures the connection and session context at
creation time and provides methods to validate that operations are
performed in the correct context.

Usage:
    from twin_transaction import TransactionContext, TransactionValidator

    # Create context when starting a transaction
    ctx = TransactionContext(
        tx_id="tx-123",
        conn_id=1,
        session_id="session-abc",
    )

    # Validate before processing ACK
    if ctx.validate_conn_ownership(incoming_conn_id=2):
        # INV-1 satisfied - process ACK
        ...
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from twin_state import PendingSettingState, SettingStage


@dataclass
class TransactionContext:
    """Immutable transaction context binding tx_id to conn_id and session.

    This context is created when a transaction starts and captures all
    the identifying information needed to validate invariants throughout
    the transaction lifecycle.

    Attributes:
        tx_id: Unique transaction identifier
        conn_id: Connection identifier where transaction was initiated
        session_id: Session identifier for cross-session validation
        created_at_mono: Monotonic timestamp of creation (for timeout validation)
        delivered_conn_id: Connection where setting was delivered (for INV-1)
        stage_snapshot: Stage at creation time (for INV-3)
    """

    tx_id: str
    conn_id: int
    session_id: str
    created_at_mono: float = field(default_factory=time.monotonic)
    delivered_conn_id: int | None = None
    stage_snapshot: str | None = None

    def validate_conn_ownership(self, incoming_conn_id: int) -> bool:
        """Validate INV-1: Connection Ownership.

        The ACK/NACK must arrive on the same connection where the
        setting was delivered.

        Args:
            incoming_conn_id: Connection ID where ACK/NACK arrived

        Returns:
            True if invariant is satisfied, False otherwise
        """
        # If not yet delivered, check against initiation connection
        if self.delivered_conn_id is None:
            return incoming_conn_id == self.conn_id
        return incoming_conn_id == self.delivered_conn_id

    def validate_session(self, current_session_id: str) -> bool:
        """Validate INV-2: Session Transaction.

        The transaction must belong to the current session.

        Args:
            current_session_id: Current active session identifier

        Returns:
            True if invariant is satisfied, False otherwise
        """
        return self.session_id == current_session_id

    def validate_timeout_ownership(
        self,
        current_tx_id: str,
        current_stage: str | None = None,
    ) -> bool:
        """Validate INV-3: Timeout Task Ownership.

        The timeout handler must validate that the inflight is still
        the same transaction it was created for.

        Args:
            current_tx_id: Current inflight tx_id
            current_stage: Current inflight stage (optional)

        Returns:
            True if invariant is satisfied, False otherwise
        """
        if current_tx_id != self.tx_id:
            return False
        if current_stage is not None and self.stage_snapshot is not None:
            if current_stage != self.stage_snapshot:
                return False
        return True

    def with_delivered_conn(self, delivered_conn_id: int) -> "TransactionContext":
        """Create a new context with delivered_conn_id set.

        This is called when the setting is actually delivered to the BOX,
        updating the connection binding for INV-1 validation.

        Args:
            delivered_conn_id: Connection ID where setting was delivered

        Returns:
            New TransactionContext with delivered_conn_id set
        """
        return TransactionContext(
            tx_id=self.tx_id,
            conn_id=self.conn_id,
            session_id=self.session_id,
            created_at_mono=self.created_at_mono,
            delivered_conn_id=delivered_conn_id,
            stage_snapshot=self.stage_snapshot,
        )

    def with_stage(self, stage: str) -> "TransactionContext":
        """Create a new context with stage_snapshot set.

        This is called when the transaction stage changes, for INV-3 validation.

        Args:
            stage: Current stage of the transaction

        Returns:
            New TransactionContext with stage_snapshot set
        """
        return TransactionContext(
            tx_id=self.tx_id,
            conn_id=self.conn_id,
            session_id=self.session_id,
            created_at_mono=self.created_at_mono,
            delivered_conn_id=self.delivered_conn_id,
            stage_snapshot=stage,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "tx_id": self.tx_id,
            "conn_id": self.conn_id,
            "session_id": self.session_id,
            "created_at_mono": self.created_at_mono,
            "delivered_conn_id": self.delivered_conn_id,
            "stage_snapshot": self.stage_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransactionContext":
        """Create from dictionary."""
        return cls(
            tx_id=str(data.get("tx_id", "")),
            conn_id=int(data.get("conn_id", 0)),
            session_id=str(data.get("session_id", "")),
            created_at_mono=float(data.get("created_at_mono", time.monotonic())),
            delivered_conn_id=data.get("delivered_conn_id"),
            stage_snapshot=data.get("stage_snapshot"),
        )


class TransactionValidator:
    """Validator for transaction invariants.

    Provides static methods for validating the three core invariants
    (INV-1, INV-2, INV-3) using TransactionContext.

    This class is used by the DigitalTwin state machine to validate
    operations before executing them.
    """

    @staticmethod
    def validate_inv1(
        ctx: TransactionContext,
        incoming_conn_id: int,
    ) -> tuple[bool, str | None]:
        """Validate INV-1: Connection Ownership.

        Args:
            ctx: Transaction context
            incoming_conn_id: Connection ID where operation is occurring

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not ctx.validate_conn_ownership(incoming_conn_id):
            return (
                False,
                f"INV-1 violation: ACK on conn_id={incoming_conn_id} "
                f"but setting delivered on conn_id={ctx.delivered_conn_id or ctx.conn_id}",
            )
        return (True, None)

    @staticmethod
    def validate_inv2(
        ctx: TransactionContext,
        current_session_id: str,
    ) -> tuple[bool, str | None]:
        """Validate INV-2: Session Transaction.

        Args:
            ctx: Transaction context
            current_session_id: Current active session ID

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not ctx.validate_session(current_session_id):
            return (
                False,
                f"INV-2 violation: transaction session={ctx.session_id} "
                f"does not match current session={current_session_id}",
            )
        return (True, None)

    @staticmethod
    def validate_inv3(
        ctx: TransactionContext,
        current_tx_id: str,
        current_stage: str | None = None,
    ) -> tuple[bool, str | None]:
        """Validate INV-3: Timeout Task Ownership.

        Args:
            ctx: Transaction context (captured at timeout creation)
            current_tx_id: Current inflight tx_id
            current_stage: Current inflight stage (optional)

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not ctx.validate_timeout_ownership(current_tx_id, current_stage):
            return (
                False,
                f"INV-3 violation: timeout created for tx_id={ctx.tx_id} "
                f"but current tx_id={current_tx_id}",
            )
        return (True, None)

    @staticmethod
    def validate_all(
        ctx: TransactionContext,
        incoming_conn_id: int,
        current_session_id: str,
        current_tx_id: str,
        current_stage: str | None = None,
    ) -> tuple[bool, list[str]]:
        """Validate all three invariants.

        Args:
            ctx: Transaction context
            incoming_conn_id: Connection ID where operation is occurring
            current_session_id: Current active session ID
            current_tx_id: Current inflight tx_id
            current_stage: Current inflight stage (optional)

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors: list[str] = []

        ok1, err1 = TransactionValidator.validate_inv1(ctx, incoming_conn_id)
        if not ok1 and err1:
            errors.append(err1)

        ok2, err2 = TransactionValidator.validate_inv2(ctx, current_session_id)
        if not ok2 and err2:
            errors.append(err2)

        ok3, err3 = TransactionValidator.validate_inv3(ctx, current_tx_id, current_stage)
        if not ok3 and err3:
            errors.append(err3)

        return (len(errors) == 0, errors)


class InvariantViolationError(Exception):
    """Exception raised when a transaction invariant is violated."""

    def __init__(
        self,
        invariant: str,
        message: str,
        ctx: TransactionContext | None = None,
    ) -> None:
        self.invariant = invariant
        self.message = message
        self.ctx = ctx
        super().__init__(f"[{invariant}] {message}")


def generate_tx_id() -> str:
    """Generate a unique transaction ID."""
    return f"tx_{uuid.uuid4().hex[:16]}"


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"session_{uuid.uuid4().hex[:16]}"


__all__ = [
    "TransactionContext",
    "TransactionValidator",
    "InvariantViolationError",
    "generate_tx_id",
    "generate_session_id",
]
