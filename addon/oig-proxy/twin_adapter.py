"""Twin Adapter – Interface Contract for Cloud Digital Twin Integration.

This module defines the abstract interface (Protocol) that twin adapters
must implement. The contract enforces tx_id and conn_id tracking for
proper transaction lifecycle management.

Design Goals:
1. Clear separation between interface contract and implementation
2. Explicit tx_id + conn_id parameters for all operations
3. Type-safe DTOs for all data transfers
4. Support for invariant validation (INV-1, INV-2, INV-3)

Related Invariants (from Task 1):
- INV-1: Connection Ownership - ACK/NACK must arrive on same connection
- INV-2: Session Transaction - inflight must belong to current session
- INV-3: Timeout Task Ownership - timeout must validate tx_id identity

Usage:
    from twin_adapter import TwinAdapterProtocol
    from twin_state import QueueSettingDTO, OnAckDTO

    class MyTwinAdapter(TwinAdapterProtocol):
        async def queue_setting(self, dto: QueueSettingDTO) -> TransactionResultDTO:
            # Implementation here
            ...
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from twin_state import (
    AckResult,
    OnAckDTO,
    OnDisconnectDTO,
    OnTblEventDTO,
    PendingSettingState,
    PollResponseDTO,
    QueueSettingDTO,
    SettingStage,
    SnapshotDTO,
    TransactionResultDTO,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


@runtime_checkable
class TwinAdapterProtocol(Protocol):
    """Protocol defining the Cloud Digital Twin adapter interface.

    All methods must accept tx_id and conn_id (either directly or via DTO)
    for proper transaction and connection tracking.

    Implementations MUST:
    1. Validate INV-1 (Connection Ownership) in on_ack()
    2. Validate INV-2 (Session Transaction) in all state modifications
    3. Validate INV-3 (Timeout Task Ownership) in timeout handlers
    """

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def queue_setting(
        self,
        dto: QueueSettingDTO,
    ) -> TransactionResultDTO:
        """Queue a new setting command.

        Args:
            dto: QueueSettingDTO with tx_id, conn_id, and setting details

        Returns:
            TransactionResultDTO with status (accepted, error)

        Invariants:
            - tx_id must be unique within the session
            - conn_id tracks which connection initiated the request
        """

    @abstractmethod
    async def get_queue_length(self) -> int:
        """Get the current queue length.

        Returns:
            Number of pending commands in the queue
        """

    @abstractmethod
    async def get_queue_snapshot(self) -> Sequence[QueueSettingDTO]:
        """Get a snapshot of all queued commands.

        Returns:
            Sequence of QueueSettingDTO for all queued commands
        """

    # ------------------------------------------------------------------
    # Inflight operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_inflight(self) -> PendingSettingState | None:
        """Get the current inflight transaction state.

        Returns:
            PendingSettingState if there's an inflight command, None otherwise
        """

    @abstractmethod
    async def start_inflight(
        self,
        tx_id: str,
        conn_id: int,
    ) -> PendingSettingState | None:
        """Start processing the next command in the queue.

        Args:
            tx_id: Expected tx_id of the command to start
            conn_id: Current connection identifier

        Returns:
            PendingSettingState if a command was started, None if queue empty

        Invariants:
            - INV-2: tx_id must match the next queued command
            - conn_id must match the current active connection
        """

    @abstractmethod
    async def finish_inflight(
        self,
        tx_id: str,
        conn_id: int,
        *,
        success: bool,
        detail: str | None = None,
    ) -> TransactionResultDTO | None:
        """Finish the current inflight transaction.

        Args:
            tx_id: Transaction identifier to finish
            conn_id: Connection identifier
            success: Whether the transaction succeeded
            detail: Optional detail message

        Returns:
            TransactionResultDTO with final status, None if no inflight

        Invariants:
            - INV-2: tx_id must match current inflight
            - INV-3: Must be called by the task that owns the transaction
        """

    # ------------------------------------------------------------------
    # ACK handling
    # ------------------------------------------------------------------

    @abstractmethod
    async def on_ack(
        self,
        dto: OnAckDTO,
    ) -> TransactionResultDTO | None:
        """Handle ACK/NACK response from BOX.

        Args:
            dto: OnAckDTO with tx_id, conn_id, and ACK result

        Returns:
            TransactionResultDTO with updated status, None if no matching tx

        Invariants:
            - INV-1: dto.conn_id must match dto.delivered_conn_id
            - INV-2: tx_id must match current inflight
        """

    @abstractmethod
    async def validate_ack_conn_ownership(
        self,
        tx_id: str,
        conn_id: int,
        delivered_conn_id: int | None,
    ) -> bool:
        """Validate INV-1: Connection Ownership invariant.

        Args:
            tx_id: Transaction identifier
            conn_id: Connection where ACK arrived
            delivered_conn_id: Connection where setting was delivered

        Returns:
            True if validation passes, False otherwise
        """

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    @abstractmethod
    async def on_tbl_event(
        self,
        dto: OnTblEventDTO,
    ) -> TransactionResultDTO | None:
        """Handle tbl_events from BOX.

        Args:
            dto: OnTblEventDTO with event details

        Returns:
            TransactionResultDTO if event affects inflight, None otherwise

        Used for:
            - Detecting Setting events (value applied confirmation)
            - Detecting Invertor ACK events
        """

    @abstractmethod
    async def on_disconnect(
        self,
        dto: OnDisconnectDTO,
    ) -> Sequence[TransactionResultDTO]:
        """Handle BOX disconnect event.

        Args:
            dto: OnDisconnectDTO with disconnect details

        Returns:
            Sequence of TransactionResultDTO for any affected transactions

        Invariants:
            - Clears pending state if setting was delivered but not ACKed
            - Preserves pending state if setting was not yet delivered
        """

    # ------------------------------------------------------------------
    # Poll operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def on_poll(
        self,
        tx_id: str | None,
        conn_id: int,
        table_name: str | None,
    ) -> PollResponseDTO:
        """Handle poll request (IsNewSet, IsNewWeather, IsNewFW).

        Args:
            tx_id: Optional transaction identifier
            conn_id: Connection identifier
            table_name: Poll table name

        Returns:
            PollResponseDTO with response data
        """

    @abstractmethod
    async def deliver_pending_setting(
        self,
        tx_id: str | None,
        conn_id: int,
    ) -> PendingSettingState | None:
        """Deliver pending setting on next poll.

        Args:
            tx_id: Optional expected tx_id
            conn_id: Connection where setting will be delivered

        Returns:
            PendingSettingState if setting was delivered, None otherwise

        Side effects:
            - Sets delivered_conn_id on pending state (for INV-1)
            - Sets sent_at timestamp
        """

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_snapshot(
        self,
        conn_id: int | None = None,
    ) -> SnapshotDTO:
        """Get a complete state snapshot.

        Args:
            conn_id: Optional connection identifier to filter

        Returns:
            SnapshotDTO with complete state information
        """

    @abstractmethod
    async def get_pending_state(
        self,
        tx_id: str | None,
        conn_id: int | None,
    ) -> PendingSettingState | None:
        """Get pending setting state.

        Args:
            tx_id: Optional transaction identifier
            conn_id: Optional connection identifier

        Returns:
            PendingSettingState if found, None otherwise
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def clear_all(self) -> None:
        """Clear all state (queue, inflight, pending)."""

    @abstractmethod
    async def restore_from_snapshot(
        self,
        snapshot: SnapshotDTO,
    ) -> None:
        """Restore state from a snapshot.

        Args:
            snapshot: SnapshotDTO with state to restore
        """


# ------------------------------------------------------------------
# Contract validation helpers
# ------------------------------------------------------------------

def validate_tx_id(tx_id: str | None) -> bool:
    """Validate that tx_id is a non-empty string."""
    if tx_id is None:
        return False
    return len(str(tx_id).strip()) > 0


def validate_conn_id(conn_id: int | None) -> bool:
    """Validate that conn_id is a non-negative integer."""
    if conn_id is None:
        return False
    return isinstance(conn_id, int) and conn_id >= 0


def validate_invariant_inv1(
    conn_id: int,
    delivered_conn_id: int | None,
) -> bool:
    """Validate INV-1: Connection Ownership invariant.

    The ACK/NACK must arrive on the same connection where the
    setting was delivered.

    Args:
        conn_id: Connection where ACK arrived
        delivered_conn_id: Connection where setting was delivered

    Returns:
        True if invariant is satisfied, False otherwise
    """
    if delivered_conn_id is None:
        # Legacy mode - no validation
        return True
    return conn_id == delivered_conn_id


def validate_invariant_inv2(
    tx_id: str,
    inflight_tx_id: str | None,
    session_id: str,
    inflight_session_id: str | None,
) -> bool:
    """Validate INV-2: Session Transaction invariant.

    The inflight transaction must belong to the current session.

    Args:
        tx_id: Transaction identifier being validated
        inflight_tx_id: Current inflight tx_id
        session_id: Current session identifier
        inflight_session_id: Session that owns the inflight

    Returns:
        True if invariant is satisfied, False otherwise
    """
    if inflight_tx_id is None:
        return True
    if tx_id != inflight_tx_id:
        return False
    if inflight_session_id is not None and session_id != inflight_session_id:
        return False
    return True


def validate_invariant_inv3(
    tx_id: str,
    expected_tx_id: str,
    stage: SettingStage,
    expected_stage: SettingStage | None = None,
) -> bool:
    """Validate INV-3: Timeout Task Ownership invariant.

    The timeout handler must validate that the inflight is still
    the same transaction it was created for.

    Args:
        tx_id: Current inflight tx_id
        expected_tx_id: tx_id when timeout was scheduled
        stage: Current inflight stage
        expected_stage: Expected stage (optional)

    Returns:
        True if invariant is satisfied, False otherwise
    """
    if tx_id != expected_tx_id:
        return False
    if expected_stage is not None and stage != expected_stage:
        return False
    return True


# ------------------------------------------------------------------
# Re-export DTOs for convenience
# ------------------------------------------------------------------

__all__ = [
    # Protocol
    "TwinAdapterProtocol",
    # DTOs
    "QueueSettingDTO",
    "PollResponseDTO",
    "OnAckDTO",
    "OnTblEventDTO",
    "OnDisconnectDTO",
    "PendingSettingState",
    "SnapshotDTO",
    "TransactionResultDTO",
    # Enums
    "AckResult",
    "SettingStage",
    # Validation helpers
    "validate_tx_id",
    "validate_conn_id",
    "validate_invariant_inv1",
    "validate_invariant_inv2",
    "validate_invariant_inv3",
]
