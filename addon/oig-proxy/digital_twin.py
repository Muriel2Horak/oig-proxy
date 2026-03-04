"""Digital Twin – Conn-bound Transaction State Machine for Cloud Digital Twin.

This module implements the core state machine for the Cloud Digital Twin,
enforcing the three key invariants defined in Task 1:

- INV-1: Connection Ownership - ACK/NACK must arrive on same connection
- INV-2: Session Transaction - inflight must belong to current session
- INV-3: Timeout Task Ownership - timeout must validate tx_id identity

The DigitalTwin class manages the transaction lifecycle with conn-bound
validation at every state transition.

Usage:
    from digital_twin import DigitalTwin
    from twin_state import QueueSettingDTO, OnAckDTO

    twin = DigitalTwin(session_id="session-123")

    # Queue a setting
    result = await twin.queue_setting(QueueSettingDTO(...))

    # Handle ACK with INV-1 validation
    result = await twin.on_ack(OnAckDTO(conn_id=1, ...))
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from config import TWIN_CLOUD_ALIGNED
from oig_frame import build_end_time_frame, build_frame
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
    get_timestamp,
)
from twin_transaction import (
    InvariantViolationError,
    TransactionContext,
    TransactionValidator,
    generate_session_id,
    generate_tx_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


@dataclass
class DigitalTwinConfig:
    """Configuration for DigitalTwin."""

    ack_timeout_s: float = 30.0
    applied_timeout_s: float = 60.0
    max_attempts: int = 5
    retry_delay_s: float = 120.0
    device_id: str = "AUTO"  # Device ID for frame building
    max_replay_attempts: int = 3  # Max replay attempts on reconnect
    replay_delay_s: float = 1.0  # Delay before replay on reconnect


@dataclass(frozen=True)
class ReplayEntry:
    """Entry in the replay buffer for disconnected transactions.

    Tracks a transaction that was in-flight when disconnect occurred
    and needs to be replayed on reconnection.
    """
    dto: QueueSettingDTO
    delivered_at_mono: float | None  # Was setting delivered before disconnect?
    replay_count: int = 0  # Number of replay attempts
    original_conn_id: int | None = None  # Original connection ID
    last_error: str | None = None  # Last error reason


class DigitalTwin:
    """Conn-bound Transaction State Machine.

    This class implements the Cloud Digital Twin state machine with
    full invariant enforcement. All operations validate connection
    and session ownership before executing.

    Invariants enforced:
        INV-1: Connection Ownership in on_ack()
        INV-2: Session Transaction in all state modifications
        INV-3: Timeout Task Ownership in timeout handlers
    """

    def __init__(
        self,
        session_id: str | None = None,
        config: DigitalTwinConfig | None = None,
    ) -> None:
        self.session_id = session_id or generate_session_id()
        self.config = config or DigitalTwinConfig()

        self._queue: deque[QueueSettingDTO] = deque()
        self._inflight: PendingSettingState | None = None
        self._inflight_ctx: TransactionContext | None = None
        self._lock = asyncio.Lock()
        self._ack_task: asyncio.Task[Any] | None = None
        self._applied_task: asyncio.Task[Any] | None = None
        
        # Simplified queue for cloud-aligned mode (single-item dict, like ControlSettings.pending)
        self._pending_simple: dict = {}
        
        self._replay_buffer: deque[ReplayEntry] = deque()
        self._completed_tx_ids: set[str] = set()
        self._replay_tx_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Queue Operations
    # ------------------------------------------------------------------

    async def queue_setting(
        self,
        dto: QueueSettingDTO,
    ) -> TransactionResultDTO:
        """Queue a new setting command.

        Creates a new transaction context bound to the provided conn_id
        and session.

        Args:
            dto: QueueSettingDTO with tx_id, conn_id, and setting details

        Returns:
            TransactionResultDTO with status (accepted, error)
        """
        async with self._lock:
            tx_id = dto.tx_id or generate_tx_id()

            pending = PendingSettingState(
                tx_id=tx_id,
                conn_id=dto.conn_id,
                tbl_name=dto.tbl_name,
                tbl_item=dto.tbl_item,
                new_value=dto.new_value,
                confirm=dto.confirm,
            )

            self._queue.append(dto)

            return TransactionResultDTO(
                tx_id=tx_id,
                conn_id=dto.conn_id,
                status="accepted",
                timestamp=get_timestamp(),
            )

    async def get_queue_length(self) -> int:
        """Get the current queue length."""
        return len(self._queue)

    async def get_queue_snapshot(self) -> Sequence[QueueSettingDTO]:
        """Get a snapshot of all queued commands."""
        return list(self._queue)

    # ------------------------------------------------------------------
    # Inflight Operations
    # ------------------------------------------------------------------

    async def get_inflight(self) -> PendingSettingState | None:
        """Get the current inflight transaction state."""
        return self._inflight

    async def start_inflight(
        self,
        tx_id: str,
        conn_id: int,
    ) -> PendingSettingState | None:
        """Start processing the next command in the queue.

        Creates a TransactionContext bound to the current session.

        Args:
            tx_id: Expected tx_id of the command to start
            conn_id: Current connection identifier

        Returns:
            PendingSettingState if a command was started, None if queue empty
        """
        async with self._lock:
            if self._inflight is not None:
                return None

            if not self._queue:
                return None

            dto = self._queue.popleft()

            pending = PendingSettingState(
                tx_id=dto.tx_id,
                conn_id=dto.conn_id,
                tbl_name=dto.tbl_name,
                tbl_item=dto.tbl_item,
                new_value=dto.new_value,
                confirm=dto.confirm,
                stage=SettingStage.ACCEPTED,
            )

            ctx = TransactionContext(
                tx_id=pending.tx_id,
                conn_id=conn_id,
                session_id=self.session_id,
                stage_snapshot=SettingStage.ACCEPTED.value,
            )

            self._inflight = pending
            self._inflight_ctx = ctx

            return pending

    async def finish_inflight(
        self,
        tx_id: str,
        conn_id: int,
        *,
        success: bool,
        detail: str | None = None,
    ) -> TransactionResultDTO | None:
        """Finish the current inflight transaction.

        Validates INV-2 (session) before clearing.

        Args:
            tx_id: Transaction identifier to finish
            conn_id: Connection identifier
            success: Whether the transaction succeeded
            detail: Optional detail message

        Returns:
            TransactionResultDTO with final status, None if no inflight
        """
        async with self._lock:
            if self._inflight is None:
                return None

            if self._inflight.tx_id != tx_id:
                return None

            if self._inflight_ctx:
                ok, err = TransactionValidator.validate_inv2(
                    self._inflight_ctx, self.session_id
                )
                if not ok:
                    raise ValueError(f"INV-2 violation in finish_inflight: {err}")

            status = "completed" if success else "error"
            result = TransactionResultDTO(
                tx_id=tx_id,
                conn_id=conn_id,
                status=status,
                detail=detail,
                timestamp=get_timestamp(),
            )

            if success:
                self._completed_tx_ids.add(tx_id)
                self._prune_completed_tx_ids()

            self._inflight = None
            self._inflight_ctx = None
            self._cancel_timeout_tasks()

            return result

    def _prune_completed_tx_ids(self, max_size: int = 1000) -> None:
        if len(self._completed_tx_ids) > max_size:
            self._completed_tx_ids = set(list(self._completed_tx_ids)[-max_size // 2:])

    # ------------------------------------------------------------------
    # ACK Handling with INV-1 Validation
    # ------------------------------------------------------------------

    async def on_ack(
        self,
        dto: OnAckDTO,
    ) -> TransactionResultDTO | None:
        """Handle ACK/NACK response.

        Routes to cloud-aligned or legacy validation based on TWIN_CLOUD_ALIGNED flag.

        Args:
            dto: OnAckDTO with tx_id, conn_id, and ACK result

        Returns:
            TransactionResultDTO with updated status, None if no matching tx
        """
        if TWIN_CLOUD_ALIGNED:
            return await self._on_ack_cloud_aligned(dto)
        else:
            return await self._on_ack_legacy(dto)

    async def _on_ack_cloud_aligned(
        self,
        dto: OnAckDTO,
    ) -> TransactionResultDTO | None:
        """Handle ACK/NACK with simplified conn_id validation (cloud-aligned mode).

        Follows the ControlSettings pattern: basic conn_id check only,
        no INV-1/2/3 validation. Uses _pending_simple dict for state tracking.

        Args:
            dto: OnAckDTO with tx_id, conn_id, and ACK result

        Returns:
            TransactionResultDTO with updated status, None if no matching tx
        """
        async with self._lock:
            if self._inflight is None:
                return None

            if self._inflight.tx_id != dto.tx_id:
                return None

            delivered_conn_id = (
                self._inflight.delivered_conn_id
                if self._inflight.delivered_conn_id is not None
                else self._inflight.conn_id
            )

            if delivered_conn_id != dto.conn_id:
                logger.info(
                    "TWIN: ACK ignored — conn_id mismatch "
                    "(delivered=%s, ack=%s, %s/%s)",
                    delivered_conn_id,
                    dto.conn_id,
                    self._inflight.tbl_name,
                    self._inflight.tbl_item,
                )
                return None

            new_pending = self._inflight.mark_ack_received(dto.ack)
            self._inflight = new_pending

            if dto.ack:
                self._pending_simple = {
                    "tx_id": dto.tx_id,
                    "conn_id": dto.conn_id,
                    "tbl_name": self._inflight.tbl_name,
                    "tbl_item": self._inflight.tbl_item,
                    "status": "ack_received",
                    "timestamp": get_timestamp(),
                }
            else:
                self._pending_simple.clear()

            self._cancel_ack_task()

            if dto.ack:
                ctx = self._inflight_ctx
                if ctx:
                    self._ack_task = asyncio.create_task(
                        self._applied_timeout_handler(ctx)
                    )
                return TransactionResultDTO(
                    tx_id=dto.tx_id,
                    conn_id=dto.conn_id,
                    status="box_ack",
                    timestamp=get_timestamp(),
                )
            else:
                result = TransactionResultDTO(
                    tx_id=dto.tx_id,
                    conn_id=dto.conn_id,
                    status="error",
                    error="box_nack",
                    timestamp=get_timestamp(),
                )
                self._inflight = None
                self._inflight_ctx = None
                return result

    async def _on_ack_legacy(
        self,
        dto: OnAckDTO,
    ) -> TransactionResultDTO | None:
        """Handle ACK/NACK response with full INV-1/2/3 validation (legacy mode).

        INVARIANT INV-1: The ACK must arrive on the same connection
        where the setting was delivered.

        Args:
            dto: OnAckDTO with tx_id, conn_id, and ACK result

        Returns:
            TransactionResultDTO with updated status, None if no matching tx

        Raises:
            InvariantViolationError: If INV-1 is violated
        """
        async with self._lock:
            if self._inflight is None:
                return None

            if self._inflight.tx_id != dto.tx_id:
                return None

            ctx = self._inflight_ctx
            if ctx is None:
                return None

            ok, err = TransactionValidator.validate_inv1(ctx, dto.conn_id)
            if not ok:
                raise InvariantViolationError("INV-1", err or "Connection mismatch", ctx)

            ok, err = TransactionValidator.validate_inv2(ctx, self.session_id)
            if not ok:
                raise InvariantViolationError("INV-2", err or "Session mismatch", ctx)

            new_pending = self._inflight.mark_ack_received(dto.ack)
            self._inflight = new_pending
            self._inflight_ctx = ctx.with_stage(new_pending.stage.value)

            self._cancel_ack_task()

            if dto.ack:
                self._ack_task = asyncio.create_task(self._applied_timeout_handler(ctx))
                return TransactionResultDTO(
                    tx_id=dto.tx_id,
                    conn_id=dto.conn_id,
                    status="box_ack",
                    timestamp=get_timestamp(),
                )
            else:
                result = TransactionResultDTO(
                    tx_id=dto.tx_id,
                    conn_id=dto.conn_id,
                    status="error",
                    error="box_nack",
                    timestamp=get_timestamp(),
                )
                self._inflight = None
                self._inflight_ctx = None
                return result

    async def validate_ack_conn_ownership(
        self,
        tx_id: str,
        conn_id: int,
        delivered_conn_id: int | None,
    ) -> bool:
        """Validate INV-1: Connection Ownership invariant."""
        if delivered_conn_id is None:
            return True
        return conn_id == delivered_conn_id

    # ------------------------------------------------------------------
    # Event Handling
    # ------------------------------------------------------------------

    async def on_tbl_event(
        self,
        dto: OnTblEventDTO,
    ) -> TransactionResultDTO | None:
        """Handle tbl_events from BOX."""
        async with self._lock:
            if self._inflight is None:
                return None

            if not dto.is_setting_event():
                return None

            if dto.tbl_name != self._inflight.tbl_name:
                return None

            if dto.tbl_item != self._inflight.tbl_item:
                return None

            new_pending = self._inflight.mark_applied()
            self._inflight = new_pending

            if self._inflight_ctx:
                self._inflight_ctx = self._inflight_ctx.with_stage(
                    new_pending.stage.value
                )

            return TransactionResultDTO(
                tx_id=self._inflight.tx_id,
                conn_id=self._inflight.conn_id,
                status="applied",
                timestamp=get_timestamp(),
            )

    async def on_disconnect(
        self,
        dto: OnDisconnectDTO,
    ) -> Sequence[TransactionResultDTO]:
        """Handle BOX disconnect event.

        Delivered but unacked transactions are moved to replay buffer
        for potential re-delivery on reconnect.
        """
        results: list[TransactionResultDTO] = []

        async with self._lock:
            if self._inflight is not None:
                if self._inflight.delivered_conn_id is not None:
                    tx_id = self._inflight.tx_id
                    
                    if tx_id not in self._completed_tx_ids:
                        dto_entry = QueueSettingDTO(
                            tx_id=tx_id,
                            conn_id=self._inflight.conn_id,
                            tbl_name=self._inflight.tbl_name,
                            tbl_item=self._inflight.tbl_item,
                            new_value=self._inflight.new_value,
                            confirm=self._inflight.confirm,
                        )
                        entry = ReplayEntry(
                            dto=dto_entry,
                            delivered_at_mono=self._inflight.delivered_at_mono,
                            replay_count=self._inflight.replay_count,
                            original_conn_id=self._inflight.delivered_conn_id,
                            last_error="disconnect",
                        )
                        self._replay_buffer.append(entry)
                        logger.info(
                            "TWIN: Transaction %s moved to replay buffer (replay_count=%d)",
                            tx_id,
                            self._inflight.replay_count,
                        )
                    else:
                        logger.info(
                            "TWIN: Transaction %s already completed, skipping replay",
                            tx_id,
                        )

                    result = TransactionResultDTO(
                        tx_id=tx_id,
                        conn_id=dto.conn_id,
                        status="error",
                        error="disconnect",
                        detail="moved_to_replay_buffer",
                        timestamp=get_timestamp(),
                    )
                    results.append(result)

                self._inflight = None
                self._inflight_ctx = None
                self._cancel_timeout_tasks()

        return results

    # ------------------------------------------------------------------
    # Poll Operations
    # ------------------------------------------------------------------

    async def on_poll(
        self,
        tx_id: str | None,
        conn_id: int,
        table_name: str | None,
    ) -> PollResponseDTO:
        """Handle poll request with IsNewSet delivery.

        Poll-Driven Queue Delivery:
        - On IsNewSet poll: deliver pending setting if available
        - On other polls or idle: return END

        No unsolicited command push is allowed.
        """
        if table_name != "IsNewSet":
            return PollResponseDTO(
                tx_id=tx_id,
                conn_id=conn_id,
                table_name=table_name,
                ack=True,
                frame_data=None,
            )

        return await self._deliver_on_is_new_set(tx_id, conn_id)

    async def _deliver_on_is_new_set(
        self,
        tx_id: str | None,
        conn_id: int,
    ) -> PollResponseDTO:
        """Deliver pending setting on IsNewSet poll.

        This implements the poll-driven delivery:
        1. If queue has items and no inflight, start inflight
        2. If inflight pending, deliver the setting frame
        3. If idle (no pending), return END

        Args:
            tx_id: Optional transaction ID
            conn_id: Current connection ID

        Returns:
            PollResponseDTO with frame_data (setting) or END frame
        """
        async with self._lock:
            if self._inflight is None and self._queue:
                dto = self._queue.popleft()
                replay_count = self._replay_tx_counts.pop(dto.tx_id, 0)
                pending = PendingSettingState(
                    tx_id=dto.tx_id,
                    conn_id=dto.conn_id,
                    tbl_name=dto.tbl_name,
                    tbl_item=dto.tbl_item,
                    new_value=dto.new_value,
                    confirm=dto.confirm,
                    stage=SettingStage.ACCEPTED,
                    replay_count=replay_count,
                )
                ctx = TransactionContext(
                    tx_id=pending.tx_id,
                    conn_id=conn_id,
                    session_id=self.session_id,
                    stage_snapshot=SettingStage.ACCEPTED.value,
                )
                self._inflight = pending
                self._inflight_ctx = ctx

            if self._inflight is not None:
                return self._build_delivery_response(conn_id)

            end_frame = build_end_time_frame().decode("utf-8", errors="strict")
            return PollResponseDTO(
                tx_id=tx_id,
                conn_id=conn_id,
                table_name="IsNewSet",
                ack=True,
                frame_data=end_frame,
            )

    def _build_delivery_response(self, conn_id: int) -> PollResponseDTO:
        """Build delivery response with setting frame.

        Updates delivered_conn_id for INV-1 validation.
        Starts ACK timeout after delivery.

        Args:
            conn_id: Current connection ID for delivery

        Returns:
            PollResponseDTO with setting frame data
        """
        if self._inflight is None:
            end_frame = build_end_time_frame().decode("utf-8", errors="strict")
            return PollResponseDTO(
                tx_id=None,
                conn_id=conn_id,
                table_name="IsNewSet",
                ack=True,
                frame_data=end_frame,
            )

        if self._inflight.stage not in (SettingStage.ACCEPTED, SettingStage.SENT_TO_BOX):
            end_frame = build_end_time_frame().decode("utf-8", errors="strict")
            return PollResponseDTO(
                tx_id=None,
                conn_id=conn_id,
                table_name="IsNewSet",
                ack=True,
                frame_data=end_frame,
            )

        frame_data = self._build_setting_frame(self._inflight)

        new_pending = self._inflight.mark_delivered(conn_id)
        self._inflight = new_pending

        if self._inflight_ctx:
            self._inflight_ctx = self._inflight_ctx.with_delivered_conn(conn_id)
            self._cancel_ack_task()
            self._ack_task = asyncio.create_task(
                self._ack_timeout_handler(self._inflight_ctx)
            )

        return PollResponseDTO(
            tx_id=new_pending.tx_id,
            conn_id=conn_id,
            table_name="IsNewSet",
            ack=True,
            frame_data=frame_data,
        )

    def _build_setting_frame(self, pending: PendingSettingState) -> str:
        """Build a Setting frame from pending state.

        Uses the same frame structure as control_settings.py.

        Args:
            pending: The pending setting to build frame for

        Returns:
            Frame string with CRC
        """
        msg_id = pending.msg_id or secrets.randbelow(90_000_000) + 10_000_000
        id_set = pending.id_set or int(time.time())
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)

        inner = (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{self.config.device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{now_local.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
            f"<NewValue>{pending.new_value}</NewValue>"
            f"<Confirm>{pending.confirm}</Confirm>"
            f"<TblName>{pending.tbl_name}</TblName>"
            f"<TblItem>{pending.tbl_item}</TblItem>"
            "<ID_Server>5</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
            f"<ver>{secrets.randbelow(90000) + 10000:05d}</ver>"
        )
        return build_frame(inner, add_crlf=True)

    async def deliver_pending_setting(
        self,
        tx_id: str | None,
        conn_id: int,
    ) -> PendingSettingState | None:
        """Deliver pending setting on next poll.

        Updates delivered_conn_id for INV-1 validation.
        """
        async with self._lock:
            if self._inflight is None:
                return None

            new_pending = self._inflight.mark_delivered(conn_id)
            self._inflight = new_pending

            if self._inflight_ctx:
                self._inflight_ctx = self._inflight_ctx.with_delivered_conn(conn_id)
                self._ack_task = asyncio.create_task(
                    self._ack_timeout_handler(self._inflight_ctx)
                )

            return new_pending

    # ------------------------------------------------------------------
    # State Snapshot
    # ------------------------------------------------------------------

    async def get_snapshot(
        self,
        conn_id: int | None = None,
    ) -> SnapshotDTO:
        """Get a complete state snapshot."""
        return SnapshotDTO(
            tx_id=self._inflight.tx_id if self._inflight else None,
            conn_id=conn_id,
            session_id=self.session_id,
            queue_length=len(self._queue),
            has_inflight=self._inflight is not None,
            inflight_stage=self._inflight.stage.value if self._inflight else None,
            inflight_tbl_name=self._inflight.tbl_name if self._inflight else None,
            inflight_tbl_item=self._inflight.tbl_item if self._inflight else None,
            inflight_new_value=self._inflight.new_value if self._inflight else None,
            inflight_delivered_conn_id=(
                self._inflight.delivered_conn_id if self._inflight else None
            ),
            replay_buffer_length=len(self._replay_buffer),
            completed_tx_count=len(self._completed_tx_ids),
            timestamp=get_timestamp(),
        )

    async def get_pending_state(
        self,
        tx_id: str | None,
        conn_id: int | None,
    ) -> PendingSettingState | None:
        """Get pending setting state."""
        if self._inflight is None:
            return None
        if tx_id is not None and self._inflight.tx_id != tx_id:
            return None
        return self._inflight

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def clear_all(self) -> None:
        """Clear all state."""
        async with self._lock:
            self._queue.clear()
            self._replay_buffer.clear()
            self._completed_tx_ids.clear()
            self._replay_tx_counts.clear()
            self._inflight = None
            self._inflight_ctx = None
            self._cancel_timeout_tasks()

    # ------------------------------------------------------------------
    # Replay Resilience
    # ------------------------------------------------------------------

    async def on_reconnect(
        self,
        conn_id: int,
    ) -> Sequence[TransactionResultDTO]:
        """Handle BOX reconnect event.

        Moves replay buffer items back to main queue for re-delivery.
        Skips transactions that have already been completed (dedup).
        Regenerates session_id to invalidate stale transactions.
        """
        results: list[TransactionResultDTO] = []

        async with self._lock:
            self.session_id = generate_session_id()
            logger.info("TWIN: Regenerated session_id on reconnect: %s", self.session_id)
            
            entries_to_queue: list[tuple[QueueSettingDTO, int]] = []
            
            while self._replay_buffer:
                entry = self._replay_buffer.popleft()

                if entry.dto.tx_id in self._completed_tx_ids:
                    logger.info(
                        "TWIN: Skipping replay of completed transaction %s",
                        entry.dto.tx_id,
                    )
                    continue

                if entry.replay_count >= self.config.max_replay_attempts:
                    logger.warning(
                        "TWIN: Transaction %s exceeded max replay attempts (%d)",
                        entry.dto.tx_id,
                        entry.replay_count,
                    )
                    result = TransactionResultDTO(
                        tx_id=entry.dto.tx_id,
                        conn_id=conn_id,
                        status="error",
                        error="max_replay_exceeded",
                        detail=f"replay_count={entry.replay_count}",
                        timestamp=get_timestamp(),
                    )
                    results.append(result)
                    continue

                entries_to_queue.append((entry.dto, entry.replay_count + 1))

            for dto, replay_count in entries_to_queue:
                self._queue.append(dto)
                self._replay_tx_counts[dto.tx_id] = replay_count
                logger.info(
                    "TWIN: Transaction %s queued for replay (attempt %d)",
                    dto.tx_id,
                    replay_count,
                )

        return results

    async def get_replay_buffer_length(self) -> int:
        return len(self._replay_buffer)

    async def get_replay_buffer_snapshot(self) -> Sequence[ReplayEntry]:
        return list(self._replay_buffer)

    def is_tx_completed(self, tx_id: str) -> bool:
        return tx_id in self._completed_tx_ids

    async def restore_from_snapshot(
        self,
        snapshot: SnapshotDTO,
    ) -> None:
        """Restore state from a snapshot."""
        pass

    # ------------------------------------------------------------------
    # Timeout Handlers with INV-3 Validation
    # ------------------------------------------------------------------

    async def _ack_timeout_handler(self, ctx: TransactionContext) -> None:
        """Handle ACK timeout with INV-3 validation."""
        await asyncio.sleep(self.config.ack_timeout_s)

        async with self._lock:
            if self._inflight is None:
                return

            ok, err = TransactionValidator.validate_inv3(
                ctx,
                self._inflight.tx_id,
                self._inflight.stage.value,
            )
            if not ok:
                logger.debug("INV-3 validation in ack_timeout: %s", err)
                return

            if self._inflight.stage not in (SettingStage.SENT_TO_BOX, SettingStage.ACCEPTED):
                return

            self._inflight = self._inflight.mark_deferred()

    async def _applied_timeout_handler(self, ctx: TransactionContext) -> None:
        """Handle applied timeout with INV-3 validation."""
        await asyncio.sleep(self.config.applied_timeout_s)

        async with self._lock:
            if self._inflight is None:
                return

            ok, err = TransactionValidator.validate_inv3(
                ctx,
                self._inflight.tx_id,
                self._inflight.stage.value,
            )
            if not ok:
                logger.debug("INV-3 validation in applied_timeout: %s", err)
                return

            if self._inflight.stage in (SettingStage.APPLIED, SettingStage.COMPLETED, SettingStage.ERROR):
                return

            self._inflight = self._inflight.mark_error()

    def _cancel_timeout_tasks(self) -> None:
        """Cancel all timeout tasks."""
        self._cancel_ack_task()
        if self._applied_task and not self._applied_task.done():
            self._applied_task.cancel()
        self._applied_task = None

    def _cancel_ack_task(self) -> None:
        """Cancel ACK timeout task."""
        if self._ack_task and not self._ack_task.done():
            self._ack_task.cancel()
        self._ack_task = None


__all__ = [
    "DigitalTwin",
    "DigitalTwinConfig",
    "TransitionError",
    "ReplayEntry",
]
