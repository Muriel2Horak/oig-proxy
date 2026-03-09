"""Tests for Digital Twin State Machine and Invariants.

These tests verify the state machine transitions and invariants (INV-1, INV-2, INV-3)
defined in Task 1. Tests work with both TWIN_CLOUD_ALIGNED=True (cloud-aligned mode)
and TWIN_CLOUD_ALIGNED=False (legacy mode).

Verification:
  Legacy: TWIN_CLOUD_ALIGNED=false PYTHONPATH=addon/oig-proxy pytest tests/test_digital_twin.py -v
  Cloud:  TWIN_CLOUD_ALIGNED=true PYTHONPATH=addon/oig-proxy pytest tests/test_digital_twin.py -v
"""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable
# pylint: disable=too-many-lines

import asyncio
import json
import os
import time
from collections.abc import Callable

import pytest

import digital_twin as digital_twin_module
from config import TWIN_CLOUD_ALIGNED
from digital_twin import DigitalTwin, DigitalTwinConfig, TransitionError
from twin_state import (
    OnAckDTO,
    OnDisconnectDTO,
    OnTblEventDTO,
    PendingSettingState,
    QueueSettingDTO,
    SettingStage,
    TransactionResultDTO,
)
from twin_transaction import (
    InvariantViolationError,
    TransactionContext,
    TransactionValidator,
    generate_session_id,
    generate_tx_id,
)


# =============================================================================
# Mode Detection and Fixtures
# =============================================================================

CLOUD_ALIGNED = TWIN_CLOUD_ALIGNED


@pytest.fixture
def twin_mode():
    """Return current twin mode for parametrized tests."""
    return "cloud_aligned" if CLOUD_ALIGNED else "legacy"


@pytest.fixture
def skip_if_cloud_aligned():
    """Skip test if running in cloud-aligned mode."""
    if CLOUD_ALIGNED:
        pytest.skip("Test not applicable in cloud-aligned mode (no INV exceptions)")


@pytest.fixture
def skip_if_legacy():
    """Skip test if running in legacy mode."""
    if not CLOUD_ALIGNED:
        pytest.skip("Test only applicable in cloud-aligned mode")


# =============================================================================
# Test Fixtures
# =============================================================================


def make_queue_dto(
    tx_id: str | None = None,
    conn_id: int = 1,
    tbl_name: str = "tbl_box_prms",
    tbl_item: str = "MODE",
    new_value: str = "1",
) -> QueueSettingDTO:
    return QueueSettingDTO(
        tx_id=tx_id or generate_tx_id(),
        conn_id=conn_id,
        tbl_name=tbl_name,
        tbl_item=tbl_item,
        new_value=new_value,
    )


def make_on_ack_dto(
    tx_id: str,
    conn_id: int,
    ack: bool = True,
    delivered_conn_id: int | None = None,
) -> OnAckDTO:
    return OnAckDTO(
        tx_id=tx_id,
        conn_id=conn_id,
        ack=ack,
        delivered_conn_id=delivered_conn_id,
    )


# =============================================================================
# INV-1: Connection Ownership Invariant Tests
# =============================================================================


@pytest.mark.asyncio
class TestINV1ConnectionOwnership:
    """Tests for INV-1: ACK must arrive on same connection where setting was delivered."""

    async def test_ack_on_wrong_connection_raises_invariant_violation(self, skip_if_cloud_aligned):
        """
        LEGACY MODE ONLY:
        GIVEN: Setting delivered on conn_id=1
        WHEN: ACK arrives on conn_id=2
        THEN: InvariantViolationError is raised
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-1", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-1", conn_id=1)

        pending = await twin.deliver_pending_setting(tx_id="tx-1", conn_id=1)
        assert pending is not None
        assert pending.delivered_conn_id == 1

        ack_dto = make_on_ack_dto(tx_id="tx-1", conn_id=2, ack=True)

        with pytest.raises(InvariantViolationError) as exc_info:
            await twin.on_ack(ack_dto)

        assert "INV-1" in str(exc_info.value)

    async def test_ack_on_correct_connection_succeeds(self):
        """
        GIVEN: Setting delivered on conn_id=1
        WHEN: ACK arrives on conn_id=1
        THEN: ACK is processed successfully
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-2", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-2", conn_id=1)

        await twin.deliver_pending_setting(tx_id="tx-2", conn_id=1)

        ack_dto = make_on_ack_dto(tx_id="tx-2", conn_id=1, ack=True)

        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "box_ack"

    async def test_nack_on_wrong_connection_raises_invariant_violation(self, skip_if_cloud_aligned):
        """
        LEGACY MODE ONLY:
        GIVEN: Setting delivered on conn_id=1
        WHEN: NACK arrives on conn_id=2
        THEN: InvariantViolationError is raised
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-3", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-3", conn_id=1)

        await twin.deliver_pending_setting(tx_id="tx-3", conn_id=1)

        ack_dto = make_on_ack_dto(tx_id="tx-3", conn_id=2, ack=False)

        with pytest.raises(InvariantViolationError) as exc_info:
            await twin.on_ack(ack_dto)

        assert "INV-1" in str(exc_info.value)

    async def test_multiple_reconnects_ack_on_original_conn_only(self, skip_if_cloud_aligned):
        """
        LEGACY MODE ONLY:
        GIVEN: Setting delivered on conn_id=1
        WHEN: Multiple reconnections occur (conn_id changes to 2, 3, 4)
        THEN: Only ACK on conn_id=1 is accepted
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-4", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-4", conn_id=1)

        await twin.deliver_pending_setting(tx_id="tx-4", conn_id=1)

        for wrong_conn_id in [2, 3, 4]:
            ack_dto = make_on_ack_dto(tx_id="tx-4", conn_id=wrong_conn_id, ack=True)
            with pytest.raises(InvariantViolationError):
                await twin.on_ack(ack_dto)

        pending = await twin.get_inflight()
        assert pending is not None, "Pending should NOT be cleared by wrong-conn ACKs"

    async def test_ack_on_wrong_connection_returns_none_cloud_aligned(self):
        """
        CLOUD-ALIGNED MODE:
        GIVEN: Setting delivered on conn_id=1
        WHEN: ACK arrives on conn_id=2
        THEN: Returns None (no exception raised)
        """
        if not CLOUD_ALIGNED:
            pytest.skip("Test only for cloud-aligned mode")

        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-wrong-conn", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-wrong-conn", conn_id=1)

        pending = await twin.deliver_pending_setting(tx_id="tx-wrong-conn", conn_id=1)
        assert pending is not None
        assert pending.delivered_conn_id == 1

        ack_dto = make_on_ack_dto(tx_id="tx-wrong-conn", conn_id=2, ack=True)

        # In cloud-aligned mode, wrong conn_id returns None instead of raising
        result = await twin.on_ack(ack_dto)
        assert result is None

        # Verify pending state is unchanged
        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.tx_id == "tx-wrong-conn"


# =============================================================================
# INV-2: Session Transaction Invariant Tests
# =============================================================================


@pytest.mark.asyncio
class TestINV2SessionTransaction:
    """Tests for INV-2: Transaction must belong to current session."""

    async def test_ack_from_old_session_rejected(self, skip_if_cloud_aligned):
        """
        LEGACY MODE ONLY:
        GIVEN: Transaction created in session_A
        WHEN: Session changes to session_B
        THEN: ACK for old transaction is rejected (InvariantViolationError)
        """
        twin = DigitalTwin(session_id="session_A")

        dto = make_queue_dto(tx_id="tx-session-test", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-session-test", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-session-test", conn_id=1)

        twin.session_id = "session_B"

        ack_dto = make_on_ack_dto(tx_id="tx-session-test", conn_id=1, ack=True)

        with pytest.raises(InvariantViolationError) as exc_info:
            await twin.on_ack(ack_dto)

        assert "INV-2" in str(exc_info.value)

    async def test_ack_from_old_session_returns_none_cloud_aligned(self):
        """
        CLOUD-ALIGNED MODE:
        GIVEN: Transaction created in session_A
        WHEN: Session changes to session_B
        THEN: ACK still processes (cloud-aligned doesn't validate session)
        """
        if not CLOUD_ALIGNED:
            pytest.skip("Test only for cloud-aligned mode")

        twin = DigitalTwin(session_id="session_A")

        dto = make_queue_dto(tx_id="tx-session-cloud", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-session-cloud", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-session-cloud", conn_id=1)

        twin.session_id = "session_B"

        ack_dto = make_on_ack_dto(tx_id="tx-session-cloud", conn_id=1, ack=True)

        # In cloud-aligned mode, session validation is skipped
        result = await twin.on_ack(ack_dto)
        assert result is not None
        assert result.status == "box_ack"

    async def test_finish_inflight_validates_session(self):
        """
        GIVEN: Transaction in old session
        WHEN: finish_inflight called after session change
        THEN: ValueError is raised (INV-2 violation)
        """
        twin = DigitalTwin(session_id="session_original")

        dto = make_queue_dto(tx_id="tx-finish-test", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-finish-test", conn_id=1)

        twin.session_id = "session_new"

        with pytest.raises(ValueError) as exc_info:
            await twin.finish_inflight("tx-finish-test", conn_id=1, success=True)

        assert "INV-2" in str(exc_info.value)


# =============================================================================
# INV-3: Timeout Task Ownership Invariant Tests
# =============================================================================


@pytest.mark.asyncio
class TestINV3TimeoutTaskOwnership:
    """Tests for INV-3: Timeout handlers must validate tx_id identity."""

    async def test_ack_timeout_validates_tx_id(self):
        """
        GIVEN: TX1 starts with ack_timeout
        WHEN: TX1 completes and TX2 starts before timeout fires
        THEN: Old timeout should NOT affect TX2
        """
        config = DigitalTwinConfig(ack_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto1 = make_queue_dto(tx_id="tx-timeout-1", conn_id=1)
        await twin.queue_setting(dto1)
        await twin.start_inflight("tx-timeout-1", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-timeout-1", conn_id=1)

        ctx1 = twin._inflight_ctx

        await twin.finish_inflight("tx-timeout-1", conn_id=1, success=True)

        dto2 = make_queue_dto(tx_id="tx-timeout-2", conn_id=1)
        await twin.queue_setting(dto2)
        await twin.start_inflight("tx-timeout-2", conn_id=1)

        await asyncio.sleep(0.1)

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.tx_id == "tx-timeout-2"
        assert pending.stage not in (SettingStage.ERROR, SettingStage.DEFERRED)

    async def test_applied_timeout_validates_tx_id(self):
        """
        GIVEN: TX1 in applied stage with applied_timeout
        WHEN: TX1 completes and TX2 starts before timeout fires
        THEN: Old timeout should NOT affect TX2
        """
        config = DigitalTwinConfig(applied_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto1 = make_queue_dto(tx_id="tx-applied-1", conn_id=1)
        await twin.queue_setting(dto1)
        await twin.start_inflight("tx-applied-1", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-applied-1", conn_id=1)

        await twin.on_ack(make_on_ack_dto(tx_id="tx-applied-1", conn_id=1, ack=True))

        twin._inflight = twin._inflight.mark_applied()
        ctx1 = twin._inflight_ctx

        await twin.finish_inflight("tx-applied-1", conn_id=1, success=True)

        dto2 = make_queue_dto(tx_id="tx-applied-2", conn_id=1)
        await twin.queue_setting(dto2)
        await twin.start_inflight("tx-applied-2", conn_id=1)

        await asyncio.sleep(0.1)

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.tx_id == "tx-applied-2"


# =============================================================================
# State Machine Transition Tests
# =============================================================================


class TestPendingSettingStateTransitions:
    """Tests for PendingSettingState atomic transitions."""

    def test_valid_transition_accepted_to_sent_to_box(self):
        pending = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.ACCEPTED,
        )

        new_pending = pending.mark_delivered(delivered_conn_id=1)

        assert new_pending.stage == SettingStage.SENT_TO_BOX
        assert new_pending.delivered_conn_id == 1

    def test_valid_transition_sent_to_box_to_box_ack(self):
        pending = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.SENT_TO_BOX,
        )

        new_pending = pending.mark_ack_received(ack=True)

        assert new_pending.stage == SettingStage.BOX_ACK

    def test_valid_transition_sent_to_box_to_error_on_nack(self):
        pending = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.SENT_TO_BOX,
        )

        new_pending = pending.mark_ack_received(ack=False)

        assert new_pending.stage == SettingStage.ERROR

    def test_valid_transition_box_ack_to_applied(self):
        pending = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.BOX_ACK,
        )

        new_pending = pending.mark_applied()

        assert new_pending.stage == SettingStage.APPLIED

    def test_valid_transition_applied_to_completed(self):
        pending = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.APPLIED,
        )

        new_pending = pending.mark_completed()

        assert new_pending.stage == SettingStage.COMPLETED

    def test_invalid_transition_raises_error(self):
        pending = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.COMPLETED,
        )

        with pytest.raises(ValueError):
            pending.transition_to(SettingStage.SENT_TO_BOX, validate_from={SettingStage.ACCEPTED})

    def test_can_transition_to_returns_correct_values(self):
        pending_accepted = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.ACCEPTED,
        )

        assert pending_accepted.can_transition_to(SettingStage.SENT_TO_BOX) is True
        assert pending_accepted.can_transition_to(SettingStage.BOX_ACK) is False
        assert pending_accepted.can_transition_to(SettingStage.ERROR) is True

    def test_is_terminal_returns_correct_values(self):
        pending_completed = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.COMPLETED,
        )

        pending_error = PendingSettingState(
            tx_id="tx-2",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.ERROR,
        )

        pending_active = PendingSettingState(
            tx_id="tx-3",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.SENT_TO_BOX,
        )

        assert pending_completed.is_terminal() is True
        assert pending_error.is_terminal() is True
        assert pending_active.is_terminal() is False

    def test_validate_conn_ownership(self):
        pending = PendingSettingState(
            tx_id="tx-1",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.SENT_TO_BOX,
            delivered_conn_id=1,
        )

        assert pending.validate_conn_ownership(1) is True
        assert pending.validate_conn_ownership(2) is False

        pending_no_delivery = PendingSettingState(
            tx_id="tx-2",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.ACCEPTED,
        )

        assert pending_no_delivery.validate_conn_ownership(1) is True
        assert pending_no_delivery.validate_conn_ownership(2) is True


# =============================================================================
# TransactionContext Tests
# =============================================================================


class TestTransactionContext:
    """Tests for TransactionContext invariant validation."""

    def test_validate_conn_ownership_matching(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            delivered_conn_id=1,
        )

        assert ctx.validate_conn_ownership(1) is True
        assert ctx.validate_conn_ownership(2) is False

    def test_validate_conn_ownership_not_delivered(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            delivered_conn_id=None,
        )

        assert ctx.validate_conn_ownership(1) is True
        assert ctx.validate_conn_ownership(2) is False

    def test_validate_session(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
        )

        assert ctx.validate_session("session-1") is True
        assert ctx.validate_session("session-2") is False

    def test_validate_timeout_ownership(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            stage_snapshot="sent_to_box",
        )

        assert ctx.validate_timeout_ownership("tx-1", "sent_to_box") is True
        assert ctx.validate_timeout_ownership("tx-2", "sent_to_box") is False
        assert ctx.validate_timeout_ownership("tx-1", "box_ack") is False

    def test_with_delivered_conn(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
        )

        new_ctx = ctx.with_delivered_conn(5)

        assert new_ctx.delivered_conn_id == 5
        assert ctx.delivered_conn_id is None

    def test_with_stage(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
        )

        new_ctx = ctx.with_stage("sent_to_box")

        assert new_ctx.stage_snapshot == "sent_to_box"
        assert ctx.stage_snapshot is None


class TestTransactionValidator:
    """Tests for TransactionValidator static methods."""

    def test_validate_inv1_success(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            delivered_conn_id=1,
        )

        ok, err = TransactionValidator.validate_inv1(ctx, 1)

        assert ok is True
        assert err is None

    def test_validate_inv1_failure(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            delivered_conn_id=1,
        )

        ok, err = TransactionValidator.validate_inv1(ctx, 2)

        assert ok is False
        assert "INV-1" in err

    def test_validate_inv2_success(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
        )

        ok, err = TransactionValidator.validate_inv2(ctx, "session-1")

        assert ok is True
        assert err is None

    def test_validate_inv2_failure(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
        )

        ok, err = TransactionValidator.validate_inv2(ctx, "session-2")

        assert ok is False
        assert "INV-2" in err

    def test_validate_inv3_success(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            stage_snapshot="sent_to_box",
        )

        ok, err = TransactionValidator.validate_inv3(ctx, "tx-1", "sent_to_box")

        assert ok is True
        assert err is None

    def test_validate_inv3_failure_tx_id(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
        )

        ok, err = TransactionValidator.validate_inv3(ctx, "tx-2")

        assert ok is False
        assert "INV-3" in err

    def test_validate_all_success(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            delivered_conn_id=1,
            stage_snapshot="sent_to_box",
        )

        ok, errors = TransactionValidator.validate_all(
            ctx,
            incoming_conn_id=1,
            current_session_id="session-1",
            current_tx_id="tx-1",
            current_stage="sent_to_box",
        )

        assert ok is True
        assert len(errors) == 0

    def test_validate_all_multiple_failures(self):
        ctx = TransactionContext(
            tx_id="tx-1",
            conn_id=1,
            session_id="session-1",
            delivered_conn_id=1,
        )

        ok, errors = TransactionValidator.validate_all(
            ctx,
            incoming_conn_id=2,
            current_session_id="session-2",
            current_tx_id="tx-2",
        )

        assert ok is False
        assert len(errors) >= 2


# =============================================================================
# DigitalTwin State Machine Tests
# =============================================================================


@pytest.mark.asyncio
class TestDigitalTwinStateMachine:
    """Tests for DigitalTwin state machine lifecycle."""

    async def test_queue_setting_creates_transaction(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-queue-test")
        result = await twin.queue_setting(dto)

        assert result.status == "accepted"
        assert result.tx_id == "tx-queue-test"

        queue_len = await twin.get_queue_length()
        assert queue_len == 1

    async def test_start_inflight_moves_from_queue(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-inflight-test")
        await twin.queue_setting(dto)

        pending = await twin.start_inflight("tx-inflight-test", conn_id=1)

        assert pending is not None
        assert pending.tx_id == "tx-inflight-test"
        assert pending.stage == SettingStage.ACCEPTED

        queue_len = await twin.get_queue_length()
        assert queue_len == 0

    async def test_deliver_pending_sets_delivered_conn_id(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-deliver-test")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-deliver-test", conn_id=1)

        pending = await twin.deliver_pending_setting("tx-deliver-test", conn_id=1)

        assert pending is not None
        assert pending.delivered_conn_id == 1
        assert pending.stage == SettingStage.SENT_TO_BOX

    async def test_on_ack_transitions_to_box_ack(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-ack-test")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-ack-test", conn_id=1)
        await twin.deliver_pending_setting("tx-ack-test", conn_id=1)

        ack_dto = make_on_ack_dto(tx_id="tx-ack-test", conn_id=1, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "box_ack"

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.stage == SettingStage.BOX_ACK

    async def test_on_nack_transitions_to_error(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-nack-test")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-nack-test", conn_id=1)
        await twin.deliver_pending_setting("tx-nack-test", conn_id=1)

        ack_dto = make_on_ack_dto(tx_id="tx-nack-test", conn_id=1, ack=False)
        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "error"
        assert result.error == "box_nack"

        pending = await twin.get_inflight()
        assert pending is None

    async def test_on_tbl_event_transitions_to_applied(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-event-test", tbl_name="tbl_box_prms", tbl_item="MODE")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-event-test", conn_id=1)
        await twin.deliver_pending_setting("tx-event-test", conn_id=1)

        ack_dto = make_on_ack_dto(tx_id="tx-event-test", conn_id=1, ack=True)
        await twin.on_ack(ack_dto)

        event_dto = OnTblEventDTO(
            tx_id="tx-event-test",
            conn_id=1,
            event_type="Setting",
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
        )
        result = await twin.on_tbl_event(event_dto)

        assert result is not None
        assert result.status == "applied"

        pending = await twin.get_inflight()
        assert pending is None

    async def test_finish_inflight_clears_state(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-finish-test")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-finish-test", conn_id=1)

        result = await twin.finish_inflight("tx-finish-test", conn_id=1, success=True)

        assert result is not None
        assert result.status == "completed"

        pending = await twin.get_inflight()
        assert pending is None

    async def test_on_disconnect_clears_delivered_pending(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-disconnect-test")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-disconnect-test", conn_id=1)
        await twin.deliver_pending_setting("tx-disconnect-test", conn_id=1)

        disconnect_dto = OnDisconnectDTO(
            tx_id=None,
            conn_id=1,
            session_id="test-session",
        )
        results = await twin.on_disconnect(disconnect_dto)

        assert len(results) == 1
        assert results[0].error == "disconnect"

        pending = await twin.get_inflight()
        assert pending is None

    async def test_get_snapshot_returns_current_state(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-snapshot-test")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-snapshot-test", conn_id=1)

        snapshot = await twin.get_snapshot(conn_id=1)

        assert snapshot.session_id == "test-session"
        assert snapshot.queue_length == 0
        assert snapshot.has_inflight is True
        assert snapshot.tx_id == "tx-snapshot-test"

    async def test_clear_all_resets_state(self):
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-clear-test")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-clear-test", conn_id=1)

        await twin.clear_all()

        queue_len = await twin.get_queue_length()
        assert queue_len == 0

        pending = await twin.get_inflight()
        assert pending is None


# =============================================================================
# Cloud-Aligned Mode Tests
# =============================================================================


@pytest.mark.asyncio
class TestCloudAlignedMode:
    """Tests specific to cloud-aligned mode behavior."""

    async def test_cloud_aligned_pending_simple_dict_updated(self):
        """
        CLOUD-ALIGNED MODE:
        GIVEN: Setting queued, started, delivered, and ACKed
        WHEN: ACK is received
        THEN: _pending_simple dict is updated with transaction state
        """
        if not CLOUD_ALIGNED:
            pytest.skip("Test only for cloud-aligned mode")

        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-pending-simple", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-pending-simple", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-pending-simple", conn_id=1)

        # Verify _pending_simple is empty before ACK
        assert not twin._pending_simple

        ack_dto = make_on_ack_dto(tx_id="tx-pending-simple", conn_id=1, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "box_ack"

        # Verify _pending_simple is populated after ACK
        assert twin._pending_simple["tx_id"] == "tx-pending-simple"
        assert twin._pending_simple["conn_id"] == 1
        assert twin._pending_simple["status"] == "ack_received"

    async def test_cloud_aligned_nack_clears_pending_simple(self):
        """
        CLOUD-ALIGNED MODE:
        GIVEN: Setting queued, started, delivered
        WHEN: NACK is received
        THEN: _pending_simple dict is cleared
        """
        if not CLOUD_ALIGNED:
            pytest.skip("Test only for cloud-aligned mode")

        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-nack-clear", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-nack-clear", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-nack-clear", conn_id=1)

        # Manually populate _pending_simple to simulate prior state
        twin._pending_simple = {"tx_id": "tx-nack-clear", "status": "test"}

        nack_dto = make_on_ack_dto(tx_id="tx-nack-clear", conn_id=1, ack=False)
        result = await twin.on_ack(nack_dto)

        assert result is not None
        assert result.status == "error"
        assert result.error == "box_nack"

        # Verify _pending_simple is cleared after NACK
        assert not twin._pending_simple

    async def test_cloud_aligned_basic_conn_id_validation(self):
        """
        CLOUD-ALIGNED MODE:
        GIVEN: Setting delivered on conn_id=1
        WHEN: ACK arrives on different conn_id
        THEN: Returns None (silently ignored)
        """
        if not CLOUD_ALIGNED:
            pytest.skip("Test only for cloud-aligned mode")

        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-conn-val", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-conn-val", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-conn-val", conn_id=1)

        # ACK on wrong conn_id returns None
        ack_dto = make_on_ack_dto(tx_id="tx-conn-val", conn_id=99, ack=True)
        result = await twin.on_ack(ack_dto)
        assert result is None

        # ACK on correct conn_id succeeds
        ack_dto = make_on_ack_dto(tx_id="tx-conn-val", conn_id=1, ack=True)
        result = await twin.on_ack(ack_dto)
        assert result is not None
        assert result.status == "box_ack"


class _StubMQTTPublisher:
    def __init__(self):
        self.handlers: list[tuple[str, Callable[[str, bytes, int, bool], None], int]] = []
        self.published_data: list[dict] = []
        self.published_raw: list[tuple[str, str, int, bool]] = []
        self.fail_publish_data = False
        self.fail_publish_raw = False

    def add_message_handler(self, topic: str, handler, qos: int) -> None:
        self.handlers.append((topic, handler, qos))

    async def publish_data(self, payload: dict) -> None:
        if self.fail_publish_data:
            raise RuntimeError("publish_data failed")
        self.published_data.append(payload)

    async def publish_raw(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        if self.fail_publish_raw:
            raise RuntimeError("publish_raw failed")
        self.published_raw.append((topic, payload, qos, retain))


@pytest.mark.asyncio
class TestDigitalTwinAdditionalCoverage:
    async def test_twin_mqtt_handler_setup_and_message_variants(self, monkeypatch):
        twin = DigitalTwin(session_id="test-session")
        publisher = _StubMQTTPublisher()
        handler = digital_twin_module.TwinMQTTHandler(
            twin=twin,
            mqtt_publisher=publisher,
            qos=2,
        )

        assert twin._mqtt_publisher is publisher
        assert handler.set_topic.endswith("/set")

        loop = asyncio.get_running_loop()
        handler.setup_mqtt(loop)
        assert publisher.handlers and publisher.handlers[0][2] == 2

        scheduled: list[str] = []

        def fake_run_coroutine_threadsafe(coro, _loop):
            scheduled.append("ok")
            coro.close()

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
        cb = publisher.handlers[0][1]
        cb(f"{digital_twin_module.MQTT_NAMESPACE}/tbl/item/set", b"{}", 1, False)
        assert scheduled == ["ok"]

        await handler.on_mqtt_message(topic="bad/topic", payload=b"{}")
        await handler.on_mqtt_message(
            topic=f"{digital_twin_module.MQTT_NAMESPACE}/tbl/item/set",
            payload=b"{",
        )
        await handler.on_mqtt_message(
            topic=f"{digital_twin_module.MQTT_NAMESPACE}/tbl/item/set",
            payload=json.dumps([1, 2]).encode(),
        )
        await handler.on_mqtt_message(
            topic=f"{digital_twin_module.MQTT_NAMESPACE}/tbl/item/set",
            payload=json.dumps({"tx_id": "x"}).encode(),
        )

        monkeypatch.setattr(digital_twin_module, "generate_tx_id", lambda: "generated-tx")
        await handler.on_mqtt_message(
            topic=f"{digital_twin_module.MQTT_NAMESPACE}/tbl_topic/item_topic/set",
            payload=json.dumps(
                {
                    "new_value": 11,
                    "tbl_name": "",
                    "tbl_item": "",
                    "confirm": "",
                    "request_key": " req ",
                    "received_at": " ts ",
                }
            ).encode(),
        )

        snapshot = await twin.get_queue_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0].tx_id == "generated-tx"
        assert snapshot[0].tbl_name == "tbl_topic"
        assert snapshot[0].tbl_item == "item_topic"
        assert snapshot[0].request_key == "req"
        assert snapshot[0].received_at == "ts"

        handler._loop = None
        cb("x/y/z/set", b"{}", 1, False)

    async def test_cloud_availability_forwarding_and_publish_helpers(self):
        twin = DigitalTwin(session_id="test-session")

        forwarded: list[str] = []

        async def forwarder(frame: str) -> None:
            forwarded.append(frame)

        twin.attach_cloud_forwarder(forwarder, availability_checker=lambda: True)
        assert twin._cloud_available() is True

        twin.attach_cloud_forwarder(None, availability_checker=lambda: False)
        assert twin._cloud_available() is False

        def broken_checker():
            raise RuntimeError("boom")

        twin.attach_cloud_forwarder(forwarder, availability_checker=broken_checker)
        assert twin._cloud_available() is False

        twin.attach_cloud_forwarder(None, availability_checker=None)
        await twin._forward_to_cloud("<Frame>none</Frame>")
        assert not forwarded

        twin.attach_cloud_forwarder(forwarder, availability_checker=None)
        assert twin._cloud_available() is True

        await twin._forward_to_cloud("<Frame>a</Frame>")
        assert forwarded == ["<Frame>a</Frame>"]

        async def failing_forwarder(_frame: str) -> None:
            raise RuntimeError("f")

        twin.attach_cloud_forwarder(failing_forwarder)
        await twin._forward_to_cloud("<Frame>b</Frame>")

        twin2 = DigitalTwin(session_id="test-session-2")
        await twin2._publish_to_mqtt("<Frame><ID_SubD>1</ID_SubD></Frame>")

        pub = _StubMQTTPublisher()
        twin.attach_mqtt_publisher(pub)
        await twin._publish_to_mqtt("<Frame><ID_SubD>1</ID_SubD></Frame>")
        assert pub.published_data

        twin._parser.parse_xml_frame = lambda _frame: {"x": 1}
        await twin._publish_to_mqtt("<Frame><TblName>tbl_patch</TblName></Frame>")

        await twin._publish_to_mqtt("<Frame><X>1</X></Frame>")

        pub.fail_publish_data = True
        await twin._publish_to_mqtt("<Frame><TblName>T</TblName></Frame>")

    async def test_handle_frame_parse_publish_state_and_serialization(self):
        twin = DigitalTwin(session_id="test-session")
        pub = _StubMQTTPublisher()
        twin.attach_mqtt_publisher(pub)

        frames: list[str] = []

        async def cloud_push(frame: str) -> None:
            frames.append(frame)

        twin.attach_cloud_forwarder(cloud_push, availability_checker=lambda: True)

        response = await twin.handle_frame("<Frame><TblName>IsNewSet</TblName></Frame>", conn_id=7)
        assert response is not None and response.table_name == "IsNewSet"

        response2 = await twin.handle_frame("<Frame><TblName>tbl_x</TblName></Frame>", conn_id=7)
        assert response2 is None
        assert frames

        assert twin._serialize_inflight() is None

        dto = make_queue_dto(tx_id="tx-ser")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-ser", conn_id=7)
        await twin.deliver_pending_setting("tx-ser", conn_id=7)
        ser = twin._serialize_inflight()
        assert ser is not None and ser["tx_id"] == "tx-ser"

        await twin._publish_state()
        assert pub.published_raw
        pub.fail_publish_raw = True
        await twin._publish_state()

    async def test_start_finish_and_prune_edge_cases(self):
        twin = DigitalTwin(session_id="test-session")

        assert await twin.start_inflight("nope", conn_id=1) is None
        assert await twin.finish_inflight("nope", conn_id=1, success=True) is None

        dto = make_queue_dto(tx_id="tx-a")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-a", conn_id=1)
        assert await twin.finish_inflight("other", conn_id=1, success=True) is None

        twin._completed_tx_ids = {f"tx-{i}" for i in range(1201)}
        twin._prune_completed_tx_ids(max_size=1000)
        assert len(twin._completed_tx_ids) <= 1000

    async def test_ack_dispatch_and_cloud_aligned_private_helpers(self, monkeypatch):
        twin = DigitalTwin(session_id="test-session")

        monkeypatch.setattr(digital_twin_module, "TWIN_CLOUD_ALIGNED", True)

        assert await twin.on_ack(make_on_ack_dto("tx-none", conn_id=1, ack=True)) is None

        assert twin._get_matching_inflight_for_ack(make_on_ack_dto("x", 1, True)) is None

        dto = make_queue_dto(tx_id="tx-cloud", conn_id=3)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-cloud", conn_id=3)
        await twin.deliver_pending_setting("tx-cloud", conn_id=3)

        assert twin._get_matching_inflight_for_ack(make_on_ack_dto("x", 3, True)) is None

        r_none = await twin.on_ack(make_on_ack_dto("tx-cloud", conn_id=99, ack=True))
        assert r_none is None

        inflight = await twin.get_inflight()
        assert inflight is not None
        assert twin._resolve_delivered_conn_id(inflight) == 3
        twin._log_cloud_aligned_ack_received(make_on_ack_dto("tx-cloud", 3, True), 3)
        assert twin._is_cloud_aligned_ack_conn_valid(
            dto=make_on_ack_dto("tx-cloud", 3, True),
            delivered_conn_id=3,
            inflight=inflight,
        )
        assert not twin._is_cloud_aligned_ack_conn_valid(
            dto=make_on_ack_dto("tx-cloud", 77, True),
            delivered_conn_id=3,
            inflight=inflight,
        )

        twin._update_pending_simple_after_ack(make_on_ack_dto("tx-cloud", 3, True), inflight)
        assert twin._pending_simple["status"] == "ack_received"
        twin._update_pending_simple_after_ack(make_on_ack_dto("tx-cloud", 3, False), inflight)
        assert not twin._pending_simple

        r_ok = await twin.on_ack(make_on_ack_dto("tx-cloud", conn_id=3, ack=True))
        assert r_ok is not None and r_ok.status == "box_ack"

        if twin._inflight_ctx is not None:
            twin._schedule_applied_timeout_after_ack()
            assert twin._applied_task is not None
            twin._cancel_timeout_tasks()

        twin._inflight = None
        twin._inflight_ctx = None
        dto2 = make_queue_dto(tx_id="tx-cloud-nack", conn_id=4)
        await twin.queue_setting(dto2)
        await twin.start_inflight("tx-cloud-nack", conn_id=4)
        await twin.deliver_pending_setting("tx-cloud-nack", conn_id=4)
        r_nack = await twin.on_ack(make_on_ack_dto("tx-cloud-nack", conn_id=4, ack=False))
        assert r_nack is not None and r_nack.error == "box_nack"

        monkeypatch.setattr(digital_twin_module, "TWIN_CLOUD_ALIGNED", CLOUD_ALIGNED)

    async def test_legacy_ack_none_paths_and_conn_validation(self, monkeypatch):
        monkeypatch.setattr(digital_twin_module, "TWIN_CLOUD_ALIGNED", False)
        twin = DigitalTwin(session_id="test-session")

        assert await twin.on_ack(make_on_ack_dto("missing", conn_id=1, ack=True)) is None

        dto = make_queue_dto(tx_id="tx-leg-none", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-leg-none", conn_id=1)
        assert await twin.on_ack(make_on_ack_dto("wrong", conn_id=1, ack=True)) is None

        twin._inflight_ctx = None
        assert await twin.on_ack(make_on_ack_dto("tx-leg-none", conn_id=1, ack=True)) is None

        assert await twin.validate_ack_conn_ownership("x", 1, None) is True
        assert await twin.validate_ack_conn_ownership("x", 2, 1) is False

        monkeypatch.setattr(digital_twin_module, "TWIN_CLOUD_ALIGNED", CLOUD_ALIGNED)

    async def test_tbl_event_disconnect_poll_and_delivery_edge_paths(self):
        twin = DigitalTwin(session_id="test-session")

        assert await twin.on_tbl_event(
            OnTblEventDTO(tx_id=None, conn_id=1, event_type="Info", tbl_name="a", tbl_item="b")
        ) is None

        dto = make_queue_dto(tx_id="tx-tbl", conn_id=1, tbl_name="tbl_box_prms", tbl_item="MODE")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-tbl", conn_id=1)
        assert await twin.on_tbl_event(
            OnTblEventDTO(tx_id="tx-tbl", conn_id=1, event_type="Info", tbl_name="tbl_box_prms", tbl_item="MODE")
        ) is None
        assert await twin.on_tbl_event(
            OnTblEventDTO(tx_id="tx-tbl", conn_id=1, event_type="Setting", tbl_name="x", tbl_item="y")
        ) is None

        twin._inflight = PendingSettingState(
            tx_id="tx-sa",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="SA",
            new_value="1",
            stage=SettingStage.BOX_ACK,
        )
        assert twin._build_auto_sa_queue_dto(twin._inflight) is None

        twin._inflight = None
        with pytest.raises(TransitionError):
            twin._mark_inflight_applied()

        results = await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1, session_id="s"))
        assert results == []
        twin._store_disconnect_last_result([])
        twin._clear_inflight_after_disconnect()

        poll = await twin.on_poll(tx_id="x", conn_id=1, table_name="Other")
        assert poll.frame_data is None and poll.ack is True

        dto2 = make_queue_dto(tx_id="tx-poll-new", conn_id=1)
        await twin.queue_setting(dto2)
        poll2 = await twin._deliver_on_is_new_set(tx_id="tx-poll-new", conn_id=1)
        assert poll2.frame_data is not None

        twin._inflight = None
        end_resp = twin._build_delivery_response(conn_id=1)
        assert end_resp.frame_data is not None

        twin._inflight = PendingSettingState(
            tx_id="tx-invalid-stage",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.BOX_ACK,
        )
        end_resp2 = twin._build_delivery_response(conn_id=1)
        assert end_resp2.frame_data is not None

        twin._inflight = None
        assert await twin.deliver_pending_setting("none", conn_id=1) is None

        assert await twin.get_pending_state("x", 1) is None
        twin._inflight = PendingSettingState(
            tx_id="tx-pending",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
        )
        assert await twin.get_pending_state("wrong", 1) is None
        assert await twin.get_pending_state("tx-pending", 1) is not None

    async def test_reconnect_and_replay_branches_and_helpers(self):
        cfg = DigitalTwinConfig(max_replay_attempts=2)
        twin = DigitalTwin(session_id="test-session", config=cfg)

        twin._store_reconnect_last_result([])

        completed_dto = make_queue_dto(tx_id="tx-completed", conn_id=1)
        twin._completed_tx_ids.add("tx-completed")
        twin._replay_buffer.append(
            digital_twin_module.ReplayEntry(
                dto=completed_dto,
                delivered_at_mono=time.monotonic(),
                replay_count=0,
                original_conn_id=1,
                last_error="disconnect",
            )
        )

        exceeded_dto = make_queue_dto(tx_id="tx-exceeded", conn_id=1)
        twin._replay_buffer.append(
            digital_twin_module.ReplayEntry(
                dto=exceeded_dto,
                delivered_at_mono=time.monotonic(),
                replay_count=2,
                original_conn_id=1,
                last_error="disconnect",
            )
        )

        replay_dto = make_queue_dto(tx_id="tx-replay", conn_id=1)
        twin._replay_buffer.append(
            digital_twin_module.ReplayEntry(
                dto=replay_dto,
                delivered_at_mono=time.monotonic(),
                replay_count=1,
                original_conn_id=1,
                last_error="disconnect",
            )
        )

        results = await twin.on_reconnect(conn_id=99)
        assert any(r.error == "max_replay_exceeded" for r in results)
        assert await twin.get_queue_length() >= 1
        assert twin.is_tx_completed("tx-completed") is True
        assert isinstance(await twin.get_replay_buffer_snapshot(), list)
        assert isinstance(await twin.get_replay_buffer_length(), int)

        twin._inflight = PendingSettingState(
            tx_id="tx-done",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.SENT_TO_BOX,
            delivered_conn_id=1,
            delivered_at_mono=time.monotonic(),
        )
        twin._completed_tx_ids.add("tx-done")
        dres = await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1, session_id="s"))
        assert dres and dres[0].error == "disconnect"

    async def test_timeout_handlers_and_cancel_tasks(self):
        cfg = DigitalTwinConfig(ack_timeout_s=0.0, applied_timeout_s=0.0)
        twin = DigitalTwin(session_id="test-session", config=cfg)

        ctx_missing = TransactionContext(
            tx_id="tx-missing",
            conn_id=1,
            session_id="test-session",
            stage_snapshot=SettingStage.SENT_TO_BOX.value,
        )
        await twin._ack_timeout_handler(ctx_missing)

        twin._inflight = PendingSettingState(
            tx_id="tx-ack-timeout",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.SENT_TO_BOX,
            delivered_conn_id=1,
        )
        await twin._ack_timeout_handler(
            TransactionContext(
                tx_id="tx-ack-timeout",
                conn_id=1,
                session_id="test-session",
                stage_snapshot=SettingStage.SENT_TO_BOX.value,
            )
        )
        assert twin._inflight is None

        twin._inflight = PendingSettingState(
            tx_id="tx-ack-stage-guard",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.BOX_ACK,
        )
        await twin._ack_timeout_handler(
            TransactionContext(
                tx_id="tx-ack-stage-guard",
                conn_id=1,
                session_id="test-session",
                stage_snapshot=SettingStage.BOX_ACK.value,
            )
        )
        assert twin._inflight.stage == SettingStage.BOX_ACK

        twin._inflight = PendingSettingState(
            tx_id="tx-ack-inv3",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.SENT_TO_BOX,
        )
        await twin._ack_timeout_handler(
            TransactionContext(
                tx_id="tx-other",
                conn_id=1,
                session_id="test-session",
                stage_snapshot=SettingStage.SENT_TO_BOX.value,
            )
        )
        assert twin._inflight.stage == SettingStage.SENT_TO_BOX

        twin._inflight = None
        await twin._applied_timeout_handler(
            TransactionContext(
                tx_id="x",
                conn_id=1,
                session_id="test-session",
                stage_snapshot=SettingStage.BOX_ACK.value,
            )
        )

        twin._inflight = PendingSettingState(
            tx_id="tx-applied-timeout",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.BOX_ACK,
        )
        await twin._applied_timeout_handler(
            TransactionContext(
                tx_id="tx-applied-timeout",
                conn_id=1,
                session_id="test-session",
                stage_snapshot=SettingStage.BOX_ACK.value,
            )
        )
        assert twin._inflight is None

        twin._inflight = PendingSettingState(
            tx_id="tx-terminal",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.APPLIED,
        )
        await twin._applied_timeout_handler(
            TransactionContext(
                tx_id="tx-terminal",
                conn_id=1,
                session_id="test-session",
                stage_snapshot=SettingStage.APPLIED.value,
            )
        )
        assert twin._inflight.stage == SettingStage.APPLIED

        twin._inflight = PendingSettingState(
            tx_id="tx-applied-inv3",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            stage=SettingStage.BOX_ACK,
        )
        await twin._applied_timeout_handler(
            TransactionContext(
                tx_id="tx-other",
                conn_id=1,
                session_id="test-session",
                stage_snapshot=SettingStage.BOX_ACK.value,
            )
        )
        assert twin._inflight.stage == SettingStage.BOX_ACK


# =============================================================================
# ACK Timeout Recovery Tests
# =============================================================================


@pytest.mark.asyncio
class TestACKTimeoutRecovery:
    """Tests for ACK timeout recovery (Task 3).

    Verifies that ACK timeout properly transitions to terminal state
    and doesn't leave items stuck in DEFERRED state.
    """

    async def test_ack_timeout_transitions_to_terminal_state_not_deferred(self):
        """
        GIVEN: Setting delivered and ACK timeout fires
        WHEN: Timeout occurs
        THEN: Item transitions to ERROR terminal state (not DEFERRED)
        """
        config = DigitalTwinConfig(ack_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-timeout-terminal", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-timeout-terminal", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-timeout-terminal", conn_id=1)

        # Wait for timeout to fire
        await asyncio.sleep(0.1)

        # Verify inflight is cleared (terminal state reached)
        pending = await twin.get_inflight()
        assert pending is None, "Timeout should clear inflight (terminal state)"

    async def test_ack_timeout_marks_item_as_error(self):
        """
        GIVEN: Setting delivered and ACK timeout fires
        WHEN: Timeout occurs
        THEN: Item is marked as error (not requeued)
        """
        config = DigitalTwinConfig(ack_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-timeout-error", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-timeout-error", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-timeout-error", conn_id=1)

        # Wait for timeout to fire
        await asyncio.sleep(0.1)

        # Verify the last result indicates error
        last_result = twin._last_result
        assert last_result is not None
        assert last_result["status"] == "error"
        assert last_result["error"] == "ack_timeout"

    async def test_queue_proceeds_after_ack_timeout(self):
        """
        GIVEN: Setting delivered, ACK timeout fires, new setting queued
        WHEN: New setting is started after timeout
        THEN: Queue can proceed with new transaction
        """
        config = DigitalTwinConfig(ack_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        # First transaction that will timeout
        dto1 = make_queue_dto(tx_id="tx-timeout-1", conn_id=1)
        await twin.queue_setting(dto1)
        await twin.start_inflight("tx-timeout-1", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-timeout-1", conn_id=1)

        # Wait for timeout to fire
        await asyncio.sleep(0.1)

        # Verify inflight is cleared
        pending = await twin.get_inflight()
        assert pending is None, "First transaction should be cleared after timeout"

        # Queue and start a new transaction
        dto2 = make_queue_dto(tx_id="tx-timeout-2", conn_id=1)
        await twin.queue_setting(dto2)
        pending2 = await twin.start_inflight("tx-timeout-2", conn_id=1)

        assert pending2 is not None, "New transaction should start successfully"
        assert pending2.tx_id == "tx-timeout-2"

    async def test_deliver_pending_does_not_block_on_deferred_state(self):
        """
        GIVEN: Setting in SENT_TO_BOX stage (not DEFERRED)
        WHEN: deliver_pending_setting is called
        THEN: It completes immediately (doesn't block)
        """
        config = DigitalTwinConfig(ack_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-deliver-test", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-deliver-test", conn_id=1)

        # deliver_pending_setting should complete immediately
        start_time = time.monotonic()
        pending = await twin.deliver_pending_setting(tx_id="tx-deliver-test", conn_id=1)
        elapsed = time.monotonic() - start_time

        assert pending is not None
        assert elapsed < 0.01, "deliver_pending_setting should not block"

    async def test_ack_timeout_clears_inflight_completely(self):
        """
        GIVEN: Setting delivered and ACK timeout fires
        WHEN: Timeout occurs
        THEN: _inflight is None (completely cleared, not stuck in any state)
        """
        config = DigitalTwinConfig(ack_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-timeout-clear", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-timeout-clear", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-timeout-clear", conn_id=1)

        # Wait for timeout to fire
        await asyncio.sleep(0.1)

        # Verify _inflight is completely cleared
        assert twin._inflight is None, "inflight should be completely cleared"
        assert twin._inflight_ctx is None, "inflight_ctx should be cleared"

    async def test_ack_timeout_cancels_pending_timeout_task(self):
        """
        GIVEN: Setting delivered and ACK timeout scheduled
        WHEN: ACK arrives before timeout fires
        THEN: Timeout task is cancelled
        """
        config = DigitalTwinConfig(ack_timeout_s=0.5)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-timeout-cancel", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-timeout-cancel", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-timeout-cancel", conn_id=1)

        # Verify ACK task is scheduled
        assert twin._ack_task is not None

        # Send ACK before timeout
        ack_dto = make_on_ack_dto(tx_id="tx-timeout-cancel", conn_id=1, ack=True)
        await twin.on_ack(ack_dto)

        # Timeout task should be cancelled
        assert twin._ack_task is None, "ACK task should be cancelled after ACK"

    async def test_ack_timeout_does_not_affect_different_tx_id(self):
        """
        GIVEN: TX1 delivered, TX2 started before TX1 timeout fires
        WHEN: TX1 timeout fires but TX2 is now inflight
        THEN: TX1 timeout is ignored (INV-3 validation)
        """
        config = DigitalTwinConfig(ack_timeout_s=0.05)
        twin = DigitalTwin(session_id="test-session", config=config)

        # TX1 - will timeout
        dto1 = make_queue_dto(tx_id="tx-1", conn_id=1)
        await twin.queue_setting(dto1)
        await twin.start_inflight("tx-1", conn_id=1)
        await twin.deliver_pending_setting(tx_id="tx-1", conn_id=1)

        # Complete TX1 before timeout fires
        await twin.finish_inflight("tx-1", conn_id=1, success=True)

        # TX2 - starts before TX1 timeout fires
        dto2 = make_queue_dto(tx_id="tx-2", conn_id=1)
        await twin.queue_setting(dto2)
        await twin.start_inflight("tx-2", conn_id=1)

        # Wait for what would have been TX1's timeout
        await asyncio.sleep(0.1)

        # TX2 should still be inflight (not affected by TX1 timeout)
        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.tx_id == "tx-2"


# =============================================================================
# RED Tests - Expected to FAIL
# =============================================================================
# These tests verify behaviors that are not yet fully implemented.
# They will FAIL until the complete integration is done.
# =============================================================================


@pytest.mark.asyncio
class TestREDExpectedFailures:
    """RED tests that should FAIL until full implementation is complete."""

    async def test_full_lifecycle_with_all_invariants(self):
        """
        RED TEST: Full transaction lifecycle with all invariant validations.

        This test exercises the complete state machine flow and should
        FAIL until all invariants are properly enforced throughout.
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-full-lifecycle")
        result = await twin.queue_setting(dto)
        assert result.status == "accepted"

        pending = await twin.start_inflight("tx-full-lifecycle", conn_id=1)
        assert pending is not None

        delivered = await twin.deliver_pending_setting("tx-full-lifecycle", conn_id=1)
        assert delivered.delivered_conn_id == 1

        ack_result = await twin.on_ack(
            make_on_ack_dto(tx_id="tx-full-lifecycle", conn_id=1, ack=True)
        )
        assert ack_result.status == "box_ack"

        event_result = await twin.on_tbl_event(
            OnTblEventDTO(
                tx_id="tx-full-lifecycle",
                conn_id=1,
                event_type="Setting",
                tbl_name="tbl_box_prms",
                tbl_item="MODE",
                new_value="1",
            )
        )
        assert event_result.status == "applied"

        finish_result = await twin.finish_inflight(
            "tx-full-lifecycle", conn_id=1, success=True
        )

        assert finish_result is None

        pending = await twin.get_inflight()
        assert pending is None, "RED: Expected pending to be None after completion"

    async def test_concurrent_transactions_isolated(self):
        """
        RED TEST: Concurrent transactions should be isolated.

        This test verifies that multiple transactions can be processed
        without interfering with each other. Should FAIL until proper
        isolation is implemented.
        """
        twin = DigitalTwin(session_id="test-session")

        dto1 = make_queue_dto(tx_id="tx-concurrent-1", conn_id=1)
        dto2 = make_queue_dto(tx_id="tx-concurrent-2", conn_id=1)

        await twin.queue_setting(dto1)
        await twin.queue_setting(dto2)

        pending1 = await twin.start_inflight("tx-concurrent-1", conn_id=1)
        assert pending1 is not None

        pending2 = await twin.start_inflight("tx-concurrent-2", conn_id=1)

        assert pending2 is None, "RED: Second start_inflight should return None when inflight exists"

    async def test_digital_twin_implements_twin_adapter_protocol(self):
        """
        RED TEST: DigitalTwin must implement TwinAdapterProtocol.

        This test verifies that DigitalTwin implements all required methods
        from TwinAdapterProtocol. Should FAIL until all methods are implemented.
        """
        from twin_adapter import TwinAdapterProtocol

        twin = DigitalTwin(session_id="test-session")

        assert isinstance(twin, TwinAdapterProtocol), (
            "RED: DigitalTwin must implement TwinAdapterProtocol"
        )

    async def test_on_poll_returns_pending_setting_frame(self):
        """
        RED TEST: on_poll must return frame data for pending settings.

        This test verifies that on_poll returns the actual frame data
        when there's a pending setting to deliver. Should FAIL until
        frame building is integrated.
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-poll-frame")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-poll-frame", conn_id=1)

        response = await twin.on_poll(tx_id="tx-poll-frame", conn_id=1, table_name="IsNewSet")

        assert response.frame_data is not None, (
            "RED: on_poll must return frame_data when pending setting exists"
        )

    @pytest.mark.xfail(reason="RED tests - implementation not complete")
    async def test_restore_from_snapshot_rebuilds_state(self):
        """
        RED TEST: restore_from_snapshot must rebuild complete state.

        This test verifies that state can be restored from a snapshot.
        Should FAIL until restore_from_snapshot is fully implemented.
        """
        from twin_state import SnapshotDTO

        twin1 = DigitalTwin(session_id="test-session-1")

        dto = make_queue_dto(tx_id="tx-restore-test")
        await twin1.queue_setting(dto)
        await twin1.start_inflight("tx-restore-test", conn_id=1)

        snapshot = await twin1.get_snapshot()

        twin2 = DigitalTwin(session_id="test-session-2")
        await twin2.restore_from_snapshot(snapshot)

        restored_snapshot = await twin2.get_snapshot()

        assert restored_snapshot.has_inflight is True, (
            "RED: restore_from_snapshot must restore inflight state"
        )
        assert restored_snapshot.tx_id == "tx-restore-test", (
            "RED: restore_from_snapshot must restore tx_id"
        )


# =============================================================================
# Inflight Finalization Tests (Task 9)
# =============================================================================


@pytest.mark.asyncio
class TestInflightFinalization:
    """Tests verifying inflight is released on all terminal paths.

    These tests verify that _inflight is None after each terminal state
    transition: APPLIED, ERROR, COMPLETED, ACK handlers, and timeouts.
    """

    async def test_inflight_released_on_applied_state(self):
        """
        GIVEN: Transaction in BOX_ACK stage
        WHEN: tbl_event received (applied)
        THEN: inflight is None after transition
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-applied-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-applied-release", conn_id=1)
        await twin.deliver_pending_setting("tx-applied-release", conn_id=1)

        # Transition to BOX_ACK
        ack_dto = make_on_ack_dto(tx_id="tx-applied-release", conn_id=1, ack=True)
        await twin.on_ack(ack_dto)

        # Verify inflight still exists in BOX_ACK stage
        inflight_before = await twin.get_inflight()
        assert inflight_before is not None
        assert inflight_before.stage == SettingStage.BOX_ACK

        # Receive tbl_event to transition to APPLIED
        event_dto = OnTblEventDTO(
            tx_id="tx-applied-release",
            conn_id=1,
            event_type="Setting",
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
        )
        await twin.on_tbl_event(event_dto)

        # Verify inflight is None after APPLIED
        inflight_after = await twin.get_inflight()
        assert inflight_after is None

    async def test_inflight_released_on_error_state(self):
        """
        GIVEN: Transaction in SENT_TO_BOX stage
        WHEN: NACK received
        THEN: inflight is None after ERROR transition
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-error-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-error-release", conn_id=1)
        await twin.deliver_pending_setting("tx-error-release", conn_id=1)

        # Verify inflight exists before NACK
        inflight_before = await twin.get_inflight()
        assert inflight_before is not None
        assert inflight_before.stage == SettingStage.SENT_TO_BOX

        # Receive NACK to transition to ERROR
        nack_dto = make_on_ack_dto(tx_id="tx-error-release", conn_id=1, ack=False)
        await twin.on_ack(nack_dto)

        # Verify inflight is None after ERROR
        inflight_after = await twin.get_inflight()
        assert inflight_after is None

    async def test_inflight_released_on_completed_state(self):
        """
        GIVEN: Transaction in any stage
        WHEN: finish_inflight called with success=True
        THEN: inflight is None after COMPLETED
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-completed-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-completed-release", conn_id=1)

        # Verify inflight exists before finish
        inflight_before = await twin.get_inflight()
        assert inflight_before is not None
        assert inflight_before.stage == SettingStage.ACCEPTED

        # Call finish_inflight with success
        await twin.finish_inflight("tx-completed-release", conn_id=1, success=True)

        # Verify inflight is None after COMPLETED
        inflight_after = await twin.get_inflight()
        assert inflight_after is None

    async def test_inflight_released_in_ack_handler_on_ack(self):
        """
        GIVEN: Transaction delivered to BOX
        WHEN: ACK received in on_ack handler
        THEN: inflight remains (in BOX_ACK) but can be finished
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-ack-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-ack-release", conn_id=1)
        await twin.deliver_pending_setting("tx-ack-release", conn_id=1)

        # Receive ACK
        ack_dto = make_on_ack_dto(tx_id="tx-ack-release", conn_id=1, ack=True)
        result = await twin.on_ack(ack_dto)

        # Verify ACK processed successfully
        assert result is not None
        assert result.status == "box_ack"

        # Inflight should still exist in BOX_ACK stage (waiting for applied)
        inflight_after = await twin.get_inflight()
        assert inflight_after is not None
        assert inflight_after.stage == SettingStage.BOX_ACK

        # Now finish the inflight (simulating applied)
        await twin.finish_inflight("tx-ack-release", conn_id=1, success=True)

        # Verify inflight is None after finalization
        inflight_final = await twin.get_inflight()
        assert inflight_final is None

    async def test_inflight_released_in_ack_handler_on_nack(self):
        """
        GIVEN: Transaction delivered to BOX
        WHEN: NACK received in on_ack handler
        THEN: inflight is None immediately after NACK
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-nack-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-nack-release", conn_id=1)
        await twin.deliver_pending_setting("tx-nack-release", conn_id=1)

        # Verify inflight exists before NACK
        inflight_before = await twin.get_inflight()
        assert inflight_before is not None

        # Receive NACK
        nack_dto = make_on_ack_dto(tx_id="tx-nack-release", conn_id=1, ack=False)
        result = await twin.on_ack(nack_dto)

        # Verify NACK processed and inflight is None
        assert result is not None
        assert result.status == "error"
        assert result.error == "box_nack"

        inflight_after = await twin.get_inflight()
        assert inflight_after is None

    async def test_inflight_released_on_ack_timeout(self):
        """
        GIVEN: Transaction delivered but no ACK received within timeout
        WHEN: ACK timeout fires
        THEN: inflight is None after timeout handling
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-timeout-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-timeout-release", conn_id=1)
        await twin.deliver_pending_setting("tx-timeout-release", conn_id=1)

        # Verify inflight exists before timeout
        inflight_before = await twin.get_inflight()
        assert inflight_before is not None

        # Directly call the timeout handler with proper context
        ctx = TransactionContext(
            tx_id="tx-timeout-release",
            conn_id=1,
            session_id="test-session",
            stage_snapshot=SettingStage.SENT_TO_BOX.value,
        )
        await twin._ack_timeout_handler(ctx)

        # Verify inflight is None after timeout
        inflight_after = await twin.get_inflight()
        assert inflight_after is None

    async def test_inflight_released_on_applied_timeout(self):
        """
        GIVEN: Transaction in BOX_ACK stage but no tbl_event within timeout
        WHEN: Applied timeout fires
        THEN: inflight is None after timeout handling
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-applied-timeout-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-applied-timeout-release", conn_id=1)
        await twin.deliver_pending_setting("tx-applied-timeout-release", conn_id=1)

        # Transition to BOX_ACK
        ack_dto = make_on_ack_dto(tx_id="tx-applied-timeout-release", conn_id=1, ack=True)
        await twin.on_ack(ack_dto)

        # Verify inflight exists in BOX_ACK stage
        inflight_before = await twin.get_inflight()
        assert inflight_before is not None
        assert inflight_before.stage == SettingStage.BOX_ACK

        # Directly call the applied timeout handler with proper context
        ctx = TransactionContext(
            tx_id="tx-applied-timeout-release",
            conn_id=1,
            session_id="test-session",
            stage_snapshot=SettingStage.BOX_ACK.value,
        )
        await twin._applied_timeout_handler(ctx)

        # Verify inflight is None after applied timeout
        inflight_after = await twin.get_inflight()
        assert inflight_after is None

    async def test_inflight_released_on_disconnect(self):
        """
        GIVEN: Transaction delivered to BOX
        WHEN: Disconnect occurs
        THEN: inflight is None after disconnect handling
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-disconnect-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-disconnect-release", conn_id=1)
        await twin.deliver_pending_setting("tx-disconnect-release", conn_id=1)

        # Verify inflight exists before disconnect
        inflight_before = await twin.get_inflight()
        assert inflight_before is not None

        # Disconnect
        disconnect_dto = OnDisconnectDTO(
            tx_id=None,
            conn_id=1,
            session_id="test-session",
        )
        await twin.on_disconnect(disconnect_dto)

        # Verify inflight is None after disconnect
        inflight_after = await twin.get_inflight()
        assert inflight_after is None

    async def test_inflight_ctx_also_cleared_on_finalization(self):
        """
        GIVEN: Transaction in progress with context
        WHEN: finish_inflight called
        THEN: both _inflight and _inflight_ctx are None
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-ctx-release", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-ctx-release", conn_id=1)

        # Verify both exist before finish
        assert twin._inflight is not None
        assert twin._inflight_ctx is not None

        # Finish the inflight
        await twin.finish_inflight("tx-ctx-release", conn_id=1, success=True)

        # Verify both are None after finalization
        assert twin._inflight is None
        assert twin._inflight_ctx is None

    async def test_timeout_tasks_cancelled_on_finalization(self):
        """
        GIVEN: Transaction with timeout tasks scheduled
        WHEN: finish_inflight called
        THEN: timeout tasks are cancelled
        """
        config = DigitalTwinConfig(ack_timeout_s=30.0, applied_timeout_s=60.0)
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-task-cancel", conn_id=1)
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-task-cancel", conn_id=1)
        await twin.deliver_pending_setting("tx-task-cancel", conn_id=1)

        # Verify timeout tasks are scheduled
        assert twin._ack_task is not None
        assert not twin._ack_task.done()

        # Finish the inflight
        await twin.finish_inflight("tx-task-cancel", conn_id=1, success=True)

        # Verify timeout tasks are cancelled
        assert twin._ack_task is None
        assert twin._applied_task is None
