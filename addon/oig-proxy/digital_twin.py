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

# pylint: disable=C0302

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from config import MQTT_NAMESPACE, TWIN_CLOUD_ALIGNED
from oig_frame import infer_table_name
from oig_frame import build_end_time_frame, build_frame
from oig_parser import OIGDataParser
from twin_state import (
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
    from mqtt_publisher import MQTTPublisher

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


class TwinMQTTHandler:
    """Handler for MQTT-based twin commands."""

    # pylint: disable=R0903

    def __init__(
        self,
        *,
        twin: "DigitalTwin",
        mqtt_publisher: "MQTTPublisher",
        qos: int = 1,
    ) -> None:
        self._twin = twin
        self._mqtt_publisher = mqtt_publisher
        self._twin.attach_mqtt_publisher(mqtt_publisher)
        self._qos = qos
        self._loop: asyncio.AbstractEventLoop | None = None
        self.set_topic = f"{MQTT_NAMESPACE}/+/+/set"
        self.on_mqtt_message = self._default_on_mqtt_message

    def setup_mqtt(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set up MQTT message handler with event loop.

        Args:
            loop: asyncio event loop for scheduling coroutines.
        """
        self._loop = loop

        def _handler(
            topic: str,
            payload: bytes,
            _qos: int,
            _retain: bool,
        ) -> None:
            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self.on_mqtt_message(topic=topic, payload=payload),
                self._loop,
            )

        self._mqtt_publisher.add_message_handler(
            topic=self.set_topic,
            handler=_handler,
            qos=self._qos,
        )
        logger.info("TWIN_MQTT: MQTT enabled (set=%s)", self.set_topic)

    async def _default_on_mqtt_message(self, *, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) != 4 or parts[3] != "set":
            logger.error("TWIN_MQTT: Invalid topic format: %s", topic)
            return

        topic_tbl_name = parts[1].strip()
        topic_tbl_item = parts[2].strip()

        try:
            data = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.error("TWIN_MQTT: Invalid JSON payload on %s: %s", topic, exc)
            return

        if not isinstance(data, dict):
            logger.error("TWIN_MQTT: JSON payload must be object on %s", topic)
            return

        if "new_value" not in data:
            logger.error("TWIN_MQTT: Missing new_value field on %s", topic)
            return

        tx_id_raw = str(data.get("tx_id") or "").strip()
        tx_id = tx_id_raw or generate_tx_id()

        tbl_name = str(data.get("tbl_name") or topic_tbl_name).strip() or topic_tbl_name
        tbl_item = str(data.get("tbl_item") or topic_tbl_item).strip() or topic_tbl_item

        dto = QueueSettingDTO(
            tx_id=tx_id,
            conn_id=0,
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=str(data.get("new_value")),
            confirm=str(data.get("confirm") or "New"),
            request_key=(
                str(data.get("request_key")).strip()
                if data.get("request_key") is not None
                else None
            ),
            received_at=(
                str(data.get("received_at")).strip()
                if data.get("received_at") is not None
                else None
            ),
        )

        result = await self._twin.queue_setting(dto)
        logger.info(
            "TWIN_MQTT: Queued %s/%s=%s tx_id=%s conn_id=%s",
            dto.tbl_name,
            dto.tbl_item,
            dto.new_value,
            result.tx_id,
            dto.conn_id,
        )


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

    # pylint: disable=R0902,R0904

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
        self._mqtt_publisher: MQTTPublisher | None = None
        self._cloud_forwarder: Callable[[str], Awaitable[None]] | None = None
        self._cloud_available_checker: Callable[[], bool] | None = None
        self._parser = OIGDataParser()
        self._last_result: dict[str, Any] | None = None
        self._active_conn_id: int | None = None
        self._state_topic = f"{MQTT_NAMESPACE}/oig_proxy/twin_state/state"

    def attach_mqtt_publisher(self, mqtt_publisher: MQTTPublisher) -> None:
        """Attach MQTT publisher to the twin."""
        self._mqtt_publisher = mqtt_publisher

    def attach_cloud_forwarder(
        self,
        forwarder: Callable[[str], Awaitable[None]] | None,
        *,
        availability_checker: Callable[[], bool] | None = None,
    ) -> None:
        """Attach cloud forwarder and availability checker."""
        self._cloud_forwarder = forwarder
        self._cloud_available_checker = availability_checker

    @staticmethod
    def _parse_table(frame: str) -> str | None:
        return infer_table_name(frame)

    async def _handle_is_new_set(self, conn_id: int) -> PollResponseDTO:
        return await self.on_poll(tx_id=None, conn_id=conn_id, table_name="IsNewSet")

    def _cloud_available(self) -> bool:
        if self._cloud_available_checker is not None:
            try:
                return bool(self._cloud_available_checker())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("TWIN: cloud availability check failed: %s", exc)
                return False
        return self._cloud_forwarder is not None

    async def _forward_to_cloud(self, frame: str) -> None:
        if self._cloud_forwarder is None:
            return
        try:
            await self._cloud_forwarder(frame)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("TWIN: cloud forward failed: %s", exc)

    async def _publish_to_mqtt(self, frame: str) -> None:
        if self._mqtt_publisher is None:
            return

        parsed = self._parser.parse_xml_frame(frame)
        table_name = self._parse_table(frame)
        if not parsed:
            parsed = {
                "_table": table_name or "raw_frame",
                "raw_frame": frame,
            }
        elif table_name and "_table" not in parsed:
            parsed["_table"] = table_name

        try:
            await self._mqtt_publisher.publish_data(parsed)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("TWIN: mqtt publish failed: %s", exc)

    async def handle_frame(
        self,
        frame: str,
        conn_id: int,
    ) -> PollResponseDTO | None:
        """Process incoming frame and return response."""
        table_name = self._parse_table(frame)

        if table_name == "IsNewSet":
            return await self._handle_is_new_set(conn_id)

        if self._cloud_available():
            await self._forward_to_cloud(frame)

        await self._publish_to_mqtt(frame)
        return None

    def _serialize_inflight(self) -> dict[str, Any] | None:
        if self._inflight is None:
            return None
        return {
            "tx_id": self._inflight.tx_id,
            "tbl_name": self._inflight.tbl_name,
            "tbl_item": self._inflight.tbl_item,
            "new_value": self._inflight.new_value,
            "stage": self._inflight.stage.value,
            "conn_id": (
                self._inflight.delivered_conn_id
                if self._inflight.delivered_conn_id is not None
                else self._inflight.conn_id
            ),
            "timestamp": get_timestamp(),
        }

    def _store_last_result(
        self,
        result: TransactionResultDTO,
        *,
        tbl_name: str | None,
        tbl_item: str | None,
        new_value: str | None,
    ) -> None:
        self._last_result = {
            "tx_id": result.tx_id,
            "status": result.status,
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": new_value,
            "timestamp": result.timestamp or get_timestamp(),
            "error": result.error,
        }

    async def _publish_state(self) -> None:
        if self._mqtt_publisher is None:
            return

        payload = {
            "queue_length": len(self._queue),
            "inflight": self._serialize_inflight(),
            "last_result": self._last_result,
            "session_active": self._active_conn_id is not None,
            "mode": "twin",
        }

        try:
            await self._mqtt_publisher.publish_raw(
                topic=self._state_topic,
                payload=json.dumps(payload),
                qos=1,
                retain=True,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("TWIN: state publish failed: %s", exc)

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
            self._create_pending_state_for_queue(tx_id, dto)
            self._enqueue_setting_dto(dto)
            self._log_queued_setting(dto, tx_id)
            result = self._build_accepted_result(tx_id=tx_id, conn_id=dto.conn_id)
            self._store_last_result(
                result,
                tbl_name=dto.tbl_name,
                tbl_item=dto.tbl_item,
                new_value=dto.new_value,
            )
            await self._publish_state()
            return result

    def _create_pending_state_for_queue(
        self,
        tx_id: str,
        dto: QueueSettingDTO,
    ) -> PendingSettingState:
        return PendingSettingState(
            tx_id=tx_id,
            conn_id=dto.conn_id,
            tbl_name=dto.tbl_name,
            tbl_item=dto.tbl_item,
            new_value=dto.new_value,
            confirm=dto.confirm,
        )

    def _enqueue_setting_dto(self, dto: QueueSettingDTO) -> None:
        self._queue.append(dto)

    def _log_queued_setting(self, dto: QueueSettingDTO, tx_id: str) -> None:
        logger.debug(
            "TWIN: Queued Setting %s/%s=%s tx_id=%s conn_id=%s",
            dto.tbl_name,
            dto.tbl_item,
            dto.new_value,
            tx_id,
            dto.conn_id,
        )

    def _build_accepted_result(self, *, tx_id: str, conn_id: int) -> TransactionResultDTO:
        return TransactionResultDTO(
            tx_id=tx_id,
            conn_id=conn_id,
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
        _ = tx_id
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

            logger.debug(
                "TWIN: Started inflight Setting %s/%s tx_id=%s conn_id=%s",
                pending.tbl_name,
                pending.tbl_item,
                pending.tx_id,
                conn_id,
            )

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
            return self._finish_inflight_locked(
                tx_id=tx_id,
                conn_id=conn_id,
                success=success,
                detail=detail,
            )

    def _finish_inflight_locked(
        self,
        *,
        tx_id: str,
        conn_id: int,
        success: bool,
        detail: str | None = None,
    ) -> TransactionResultDTO | None:
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

        logger.debug(
            "TWIN: Finished inflight tx_id=%s conn_id=%s success=%s status=%s",
            tx_id,
            conn_id,
            success,
            status,
        )

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
            inflight = self._get_matching_inflight_for_ack(dto)
            if inflight is None:
                return None

            delivered_conn_id = self._resolve_delivered_conn_id(inflight)
            self._log_cloud_aligned_ack_received(dto, delivered_conn_id)

            if not self._is_cloud_aligned_ack_conn_valid(
                dto=dto,
                delivered_conn_id=delivered_conn_id,
                inflight=inflight,
            ):
                return None

            pending = self._apply_cloud_aligned_ack_state(dto, inflight)
            if dto.ack:
                self._schedule_applied_timeout_after_ack()
                return await self._finalize_cloud_ack_success(dto, pending)

            return await self._finalize_cloud_ack_nack(dto, pending)

    def _get_matching_inflight_for_ack(self, dto: OnAckDTO) -> PendingSettingState | None:
        inflight = self._inflight
        if inflight is None:
            return None
        if inflight.tx_id != dto.tx_id:
            return None
        return inflight

    def _apply_cloud_aligned_ack_state(
        self,
        dto: OnAckDTO,
        inflight: PendingSettingState,
    ) -> PendingSettingState:
        pending = inflight.mark_ack_received(dto.ack)
        self._inflight = pending
        self._update_pending_simple_after_ack(dto, pending)
        self._cancel_ack_task()
        return pending

    @staticmethod
    def _resolve_delivered_conn_id(inflight: PendingSettingState) -> int:
        return (
            inflight.delivered_conn_id
            if inflight.delivered_conn_id is not None
            else inflight.conn_id
        )

    def _log_cloud_aligned_ack_received(self, dto: OnAckDTO, delivered_conn_id: int) -> None:
        logger.debug(
            "TWIN: ACK received for tx_id=%s conn_id=%s delivered_conn_id=%s ack=%s",
            dto.tx_id,
            dto.conn_id,
            delivered_conn_id,
            dto.ack,
        )

    def _is_cloud_aligned_ack_conn_valid(
        self,
        *,
        dto: OnAckDTO,
        delivered_conn_id: int,
        inflight: PendingSettingState,
    ) -> bool:
        if delivered_conn_id == dto.conn_id:
            return True

        logger.info(
            "TWIN: ACK ignored — conn_id mismatch "
            "(delivered=%s, ack=%s, %s/%s)",
            delivered_conn_id,
            dto.conn_id,
            inflight.tbl_name,
            inflight.tbl_item,
        )
        return False

    def _update_pending_simple_after_ack(
        self,
        dto: OnAckDTO,
        pending: PendingSettingState,
    ) -> None:
        if dto.ack:
            self._pending_simple = {
                "tx_id": dto.tx_id,
                "conn_id": dto.conn_id,
                "tbl_name": pending.tbl_name,
                "tbl_item": pending.tbl_item,
                "status": "ack_received",
                "timestamp": get_timestamp(),
            }
            return

        self._pending_simple.clear()

    def _schedule_applied_timeout_after_ack(self) -> None:
        ctx = self._inflight_ctx
        if ctx:
            self._applied_task = asyncio.create_task(self._applied_timeout_handler(ctx))

    async def _finalize_cloud_ack_success(
        self,
        dto: OnAckDTO,
        pending: PendingSettingState,
    ) -> TransactionResultDTO:
        result = TransactionResultDTO(
            tx_id=dto.tx_id,
            conn_id=dto.conn_id,
            status="box_ack",
            timestamp=get_timestamp(),
        )
        self._store_last_result(
            result,
            tbl_name=pending.tbl_name,
            tbl_item=pending.tbl_item,
            new_value=pending.new_value,
        )
        await self._publish_state()
        return result

    async def _finalize_cloud_ack_nack(
        self,
        dto: OnAckDTO,
        pending: PendingSettingState,
    ) -> TransactionResultDTO:
        self._finish_inflight_locked(
            tx_id=dto.tx_id,
            conn_id=dto.conn_id,
            success=False,
            detail="box_nack",
        )
        result = TransactionResultDTO(
            tx_id=dto.tx_id,
            conn_id=dto.conn_id,
            status="error",
            error="box_nack",
            timestamp=get_timestamp(),
        )
        self._store_last_result(
            result,
            tbl_name=pending.tbl_name,
            tbl_item=pending.tbl_item,
            new_value=pending.new_value,
        )
        await self._publish_state()
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

            logger.debug(
                "TWIN: Legacy ACK received for tx_id=%s conn_id=%s ack=%s stage=%s",
                dto.tx_id,
                dto.conn_id,
                dto.ack,
                new_pending.stage.value,
            )

            self._cancel_ack_task()

            if dto.ack:
                self._applied_task = asyncio.create_task(self._applied_timeout_handler(ctx))
                result = TransactionResultDTO(
                    tx_id=dto.tx_id,
                    conn_id=dto.conn_id,
                    status="box_ack",
                    timestamp=get_timestamp(),
                )
                self._store_last_result(
                    result,
                    tbl_name=new_pending.tbl_name,
                    tbl_item=new_pending.tbl_item,
                    new_value=new_pending.new_value,
                )
                await self._publish_state()
                return result
            result = TransactionResultDTO(
                tx_id=dto.tx_id,
                conn_id=dto.conn_id,
                status="error",
                error="box_nack",
                timestamp=get_timestamp(),
            )
            self._finish_inflight_locked(
                tx_id=dto.tx_id,
                conn_id=dto.conn_id,
                success=False,
                detail="box_nack",
            )
            self._store_last_result(
                result,
                tbl_name=new_pending.tbl_name,
                tbl_item=new_pending.tbl_item,
                new_value=new_pending.new_value,
            )
            await self._publish_state()
            return result

    async def validate_ack_conn_ownership(
        self,
        tx_id: str,
        conn_id: int,
        delivered_conn_id: int | None,
    ) -> bool:
        """Validate INV-1: Connection Ownership invariant."""
        _ = tx_id
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
        sa_dto: QueueSettingDTO | None = None
        result: TransactionResultDTO | None = None
        async with self._lock:
            processed = await self._process_tbl_event_locked(dto)
            if processed is None:
                return None
            result, sa_dto = processed

        if sa_dto is not None and result is not None:
            await self.queue_setting(sa_dto)
            logger.info(
                "TWIN: Auto-queued SA command after applied tx_id=%s source=%s/%s",
                result.tx_id,
                dto.tbl_name,
                dto.tbl_item,
            )

        return result

    async def _process_tbl_event_locked(
        self,
        dto: OnTblEventDTO,
    ) -> tuple[TransactionResultDTO, QueueSettingDTO | None] | None:
        if not self._matches_inflight_tbl_event(dto):
            return None

        try:
            new_pending = self._mark_inflight_applied()
            result = self._build_applied_result(new_pending)
            self._finish_inflight_locked(
                tx_id=new_pending.tx_id,
                conn_id=dto.conn_id,
                success=True,
                detail="applied",
            )
            self._store_last_result(
                result,
                tbl_name=new_pending.tbl_name,
                tbl_item=new_pending.tbl_item,
                new_value=new_pending.new_value,
            )
            await self._publish_state()
            sa_dto = self._build_auto_sa_queue_dto(new_pending)
            return result, sa_dto
        except Exception:  # pylint: disable=broad-exception-caught
            if self._inflight is not None:
                self._finish_inflight_locked(
                    tx_id=self._inflight.tx_id,
                    conn_id=dto.conn_id,
                    success=False,
                    detail="tbl_event_error",
                )
            raise

    def _matches_inflight_tbl_event(self, dto: OnTblEventDTO) -> bool:
        if self._inflight is None:
            return False

        if not dto.is_setting_event():
            return False

        return (
            dto.tbl_name == self._inflight.tbl_name
            and dto.tbl_item == self._inflight.tbl_item
        )

    def _mark_inflight_applied(self) -> PendingSettingState:
        if self._inflight is None:
            raise TransitionError("Cannot apply tbl_event without inflight")

        new_pending = self._inflight.mark_applied()
        self._inflight = new_pending

        if self._inflight_ctx:
            self._inflight_ctx = self._inflight_ctx.with_stage(new_pending.stage.value)

        return new_pending

    @staticmethod
    def _build_applied_result(pending: PendingSettingState) -> TransactionResultDTO:
        return TransactionResultDTO(
            tx_id=pending.tx_id,
            conn_id=pending.conn_id,
            status="applied",
            timestamp=get_timestamp(),
        )

    def _build_auto_sa_queue_dto(
        self,
        pending: PendingSettingState,
    ) -> QueueSettingDTO | None:
        is_sa_setting = pending.tbl_name == "tbl_box_prms" and pending.tbl_item == "SA"
        if is_sa_setting:
            return None

        sa_dto = QueueSettingDTO(
            tx_id=generate_tx_id(),
            conn_id=pending.conn_id,
            tbl_name="tbl_box_prms",
            tbl_item="SA",
            new_value="1",
        )
        object.__setattr__(sa_dto, "auto_generated", True)
        return sa_dto

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
            self._active_conn_id = None
            self._collect_disconnect_results(dto, results)
            self._clear_inflight_after_disconnect()
            self._store_disconnect_last_result(results)
            await self._publish_state()

        return results

    def _collect_disconnect_results(
        self,
        dto: OnDisconnectDTO,
        results: list[TransactionResultDTO],
    ) -> None:
        inflight = self._inflight
        if inflight is None or inflight.delivered_conn_id is None:
            return

        tx_id = inflight.tx_id
        self._move_inflight_to_replay_if_needed(inflight)
        results.append(self._build_disconnect_result(tx_id=tx_id, conn_id=dto.conn_id))

    def _move_inflight_to_replay_if_needed(self, inflight: PendingSettingState) -> None:
        tx_id = inflight.tx_id
        if tx_id in self._completed_tx_ids:
            logger.info(
                "TWIN: Transaction %s already completed, skipping replay",
                tx_id,
            )
            return

        dto_entry = QueueSettingDTO(
            tx_id=tx_id,
            conn_id=inflight.conn_id,
            tbl_name=inflight.tbl_name,
            tbl_item=inflight.tbl_item,
            new_value=inflight.new_value,
            confirm=inflight.confirm,
        )
        entry = ReplayEntry(
            dto=dto_entry,
            delivered_at_mono=inflight.delivered_at_mono,
            replay_count=inflight.replay_count,
            original_conn_id=inflight.delivered_conn_id,
            last_error="disconnect",
        )
        self._replay_buffer.append(entry)
        logger.info(
            "TWIN: Transaction %s moved to replay buffer (replay_count=%d)",
            tx_id,
            inflight.replay_count,
        )

    @staticmethod
    def _build_disconnect_result(*, tx_id: str, conn_id: int) -> TransactionResultDTO:
        return TransactionResultDTO(
            tx_id=tx_id,
            conn_id=conn_id,
            status="error",
            error="disconnect",
            detail="moved_to_replay_buffer",
            timestamp=get_timestamp(),
        )

    def _clear_inflight_after_disconnect(self) -> None:
        if self._inflight is None:
            return

        self._inflight = None
        self._inflight_ctx = None
        self._cancel_timeout_tasks()

    def _store_disconnect_last_result(self, results: list[TransactionResultDTO]) -> None:
        if not results:
            return

        latest = results[-1]
        self._store_last_result(
            latest,
            tbl_name=None,
            tbl_item=None,
            new_value=None,
        )

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
        logger.debug(
            "TWIN: Poll received tx_id=%s conn_id=%s table_name=%s",
            tx_id,
            conn_id,
            table_name,
        )

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
            logger.debug(
                "TWIN: IsNewSet poll handling conn_id=%s queue_len=%d inflight=%s",
                conn_id,
                len(self._queue),
                self._inflight is not None,
            )

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

        logger.debug(
            "TWIN: Building delivery response conn_id=%s tx_id=%s tbl=%s/%s",
            conn_id,
            new_pending.tx_id,
            new_pending.tbl_name,
            new_pending.tbl_item,
        )

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
        _ = tx_id
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
        _ = conn_id
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
            self._prepare_reconnect_session(conn_id)
            entries_to_queue = self._collect_reconnect_entries(conn_id, results)
            self._enqueue_reconnect_entries(entries_to_queue)
            self._store_reconnect_last_result(results)
            await self._publish_state()

        return results

    def _prepare_reconnect_session(self, conn_id: int) -> None:
        self._active_conn_id = conn_id
        self.session_id = generate_session_id()
        logger.info("TWIN: Regenerated session_id on reconnect: %s", self.session_id)

    def _collect_reconnect_entries(
        self,
        conn_id: int,
        results: list[TransactionResultDTO],
    ) -> list[tuple[QueueSettingDTO, int]]:
        entries_to_queue: list[tuple[QueueSettingDTO, int]] = []

        while self._replay_buffer:
            entry = self._replay_buffer.popleft()

            if self._should_skip_completed_replay(entry):
                continue

            if self._has_exceeded_replay_attempts(entry):
                results.append(self._build_max_replay_exceeded_result(entry, conn_id))
                continue

            entries_to_queue.append((entry.dto, entry.replay_count + 1))

        return entries_to_queue

    def _should_skip_completed_replay(self, entry: ReplayEntry) -> bool:
        if entry.dto.tx_id not in self._completed_tx_ids:
            return False

        logger.info(
            "TWIN: Skipping replay of completed transaction %s",
            entry.dto.tx_id,
        )
        return True

    def _has_exceeded_replay_attempts(self, entry: ReplayEntry) -> bool:
        if entry.replay_count < self.config.max_replay_attempts:
            return False

        logger.warning(
            "TWIN: Transaction %s exceeded max replay attempts (%d)",
            entry.dto.tx_id,
            entry.replay_count,
        )
        return True

    @staticmethod
    def _build_max_replay_exceeded_result(
        entry: ReplayEntry,
        conn_id: int,
    ) -> TransactionResultDTO:
        return TransactionResultDTO(
            tx_id=entry.dto.tx_id,
            conn_id=conn_id,
            status="error",
            error="max_replay_exceeded",
            detail=f"replay_count={entry.replay_count}",
            timestamp=get_timestamp(),
        )

    def _enqueue_reconnect_entries(self, entries: list[tuple[QueueSettingDTO, int]]) -> None:
        for dto, replay_count in entries:
            self._queue.append(dto)
            self._replay_tx_counts[dto.tx_id] = replay_count
            logger.info(
                "TWIN: Transaction %s queued for replay (attempt %d)",
                dto.tx_id,
                replay_count,
            )

    def _store_reconnect_last_result(self, results: list[TransactionResultDTO]) -> None:
        if not results:
            return

        latest = results[-1]
        self._store_last_result(
            latest,
            tbl_name=None,
            tbl_item=None,
            new_value=None,
        )

    async def get_replay_buffer_length(self) -> int:
        """Return the length of the replay buffer."""
        return len(self._replay_buffer)

    async def get_replay_buffer_snapshot(self) -> Sequence[ReplayEntry]:
        """Return a snapshot of the replay buffer."""
        return list(self._replay_buffer)

    def is_tx_completed(self, tx_id: str) -> bool:
        """Check if a transaction is completed."""
        return tx_id in self._completed_tx_ids

    async def restore_from_snapshot(
        self,
        snapshot: SnapshotDTO,
    ) -> None:
        """Restore state from a snapshot."""

    # ------------------------------------------------------------------
    # Timeout Handlers with INV-3 Validation
    # ------------------------------------------------------------------

    async def _ack_timeout_handler(self, ctx: TransactionContext) -> None:
        """Handle ACK timeout with INV-3 validation."""
        logger.debug(
            "TWIN: ACK timeout started for tx_id=%s conn_id=%s timeout=%.1fs",
            ctx.tx_id,
            ctx.conn_id,
            self.config.ack_timeout_s,
        )
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

            timed_out = self._inflight
            logger.warning(
                "TWIN: ACK timeout for tx_id=%s (%s/%s), finalizing as error",
                timed_out.tx_id,
                timed_out.tbl_name,
                timed_out.tbl_item,
            )

            errored = timed_out.mark_error()
            self._inflight = errored
            self._pending_simple.clear()
            self._finish_inflight_locked(
                tx_id=errored.tx_id,
                conn_id=ctx.conn_id,
                success=False,
                detail="ack_timeout",
            )

            result = TransactionResultDTO(
                tx_id=errored.tx_id,
                conn_id=ctx.conn_id,
                status="error",
                error="ack_timeout",
                detail="ack_not_received_within_timeout",
                timestamp=get_timestamp(),
            )
            self._store_last_result(
                result,
                tbl_name=errored.tbl_name,
                tbl_item=errored.tbl_item,
                new_value=errored.new_value,
            )
            await self._publish_state()

    async def _applied_timeout_handler(self, ctx: TransactionContext) -> None:
        """Handle applied timeout with INV-3 validation."""
        logger.debug(
            "TWIN: Applied timeout started for tx_id=%s conn_id=%s timeout=%.1fs",
            ctx.tx_id,
            ctx.conn_id,
            self.config.applied_timeout_s,
        )
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

            if self._inflight.stage in (
                SettingStage.APPLIED,
                SettingStage.COMPLETED,
                SettingStage.ERROR,
            ):
                return

            self._inflight = self._inflight.mark_error()
            self._finish_inflight_locked(
                tx_id=ctx.tx_id,
                conn_id=ctx.conn_id,
                success=False,
                detail="applied_timeout",
            )

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
    "TwinMQTTHandler",
    "TransitionError",
    "ReplayEntry",
]
