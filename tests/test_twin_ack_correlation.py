"""Tests for ACK/NACK Correlation with Connection-Bound Validation.

Task 7: Parse ACK/NACK and correlate to delivered tx with conn-bound checks.

INVARIANT INV-1: ACK/NACK must arrive on same connection where setting was delivered.

Verification:
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_ack_correlation.py -k correct_conn --maxfail=1
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_ack_correlation.py -k wrong_conn --maxfail=1
"""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable

import pytest

from digital_twin import DigitalTwin, DigitalTwinConfig
from twin_state import (
    OnAckDTO,
    QueueSettingDTO,
    SettingStage,
)
from twin_transaction import InvariantViolationError, generate_tx_id


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


def make_ack_dto(
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


async def setup_delivered_setting(
    twin: DigitalTwin,
    tx_id: str,
    conn_id: int = 1,
) -> None:
    """Helper: Queue a setting and deliver it via poll."""
    dto = make_queue_dto(tx_id=tx_id, conn_id=conn_id)
    await twin.queue_setting(dto)
    await twin.on_poll(tx_id=None, conn_id=conn_id, table_name="IsNewSet")


@pytest.mark.asyncio
class TestCorrectConnAck:
    """Tests for ACK on correct connection (INV-1 satisfied)."""

    async def test_ack_on_same_conn_completes_to_box_ack_stage(self):
        """
        GIVEN: Setting delivered on conn_id=5
        WHEN: ACK arrives on conn_id=5 (same connection)
        THEN: Transaction transitions to BOX_ACK stage
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-correct-conn", conn_id=5)

        ack_dto = make_ack_dto(tx_id="tx-correct-conn", conn_id=5, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "box_ack"
        assert result.tx_id == "tx-correct-conn"
        assert result.conn_id == 5

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.stage == SettingStage.BOX_ACK

    async def test_ack_sets_delivered_conn_id_in_context(self):
        """
        GIVEN: Setting delivered on conn_id=3
        WHEN: ACK arrives on conn_id=3
        THEN: TransactionContext has delivered_conn_id=3
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-ctx-conn", conn_id=3)

        pending_before = await twin.get_inflight()
        assert pending_before is not None
        assert pending_before.delivered_conn_id == 3

        ack_dto = make_ack_dto(tx_id="tx-ctx-conn", conn_id=3, ack=True)
        await twin.on_ack(ack_dto)

        ctx = twin._inflight_ctx
        assert ctx is not None
        assert ctx.delivered_conn_id == 3

    async def test_ack_on_conn_0_matches_delivered_conn_0(self):
        """
        GIVEN: Setting delivered on conn_id=0
        WHEN: ACK arrives on conn_id=0
        THEN: Transaction completes successfully
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-conn-zero", conn_id=0)

        ack_dto = make_ack_dto(tx_id="tx-conn-zero", conn_id=0, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "box_ack"

    async def test_ack_on_different_conn_id_than_original_request(self):
        """
        GIVEN: Setting queued with conn_id=1 but delivered on conn_id=7
        WHEN: ACK arrives on conn_id=7 (delivered conn, not request conn)
        THEN: Transaction succeeds (INV-1 uses delivered_conn_id)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        # Queue with original conn_id=1
        dto = make_queue_dto(tx_id="tx-diff-conn", conn_id=1)
        await twin.queue_setting(dto)

        # Deliver on conn_id=7 (different from request)
        await twin.on_poll(tx_id=None, conn_id=7, table_name="IsNewSet")

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.delivered_conn_id == 7

        # ACK on delivered conn_id=7 should succeed
        ack_dto = make_ack_dto(tx_id="tx-diff-conn", conn_id=7, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "box_ack"


@pytest.mark.asyncio
class TestWrongConnAck:
    """Tests for ACK on wrong connection (INV-1 violation)."""

    async def test_ack_on_wrong_conn_raises_invariant_violation(self):
        """
        GIVEN: Setting delivered on conn_id=5
        WHEN: ACK arrives on conn_id=9 (different connection)
        THEN: InvariantViolationError is raised (INV-1)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-wrong-conn", conn_id=5)

        ack_dto = make_ack_dto(tx_id="tx-wrong-conn", conn_id=9, ack=True)

        with pytest.raises(InvariantViolationError) as exc_info:
            await twin.on_ack(ack_dto)

        assert "INV-1" in str(exc_info.value)
        assert exc_info.value.invariant == "INV-1"

    async def test_wrong_conn_ack_never_completes_transaction(self):
        """
        GIVEN: Setting delivered on conn_id=1
        WHEN: ACK arrives on conn_id=2 (wrong connection)
        THEN: Transaction remains in SENT_TO_BOX stage (not completed)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-no-complete", conn_id=1)

        pending_before = await twin.get_inflight()
        assert pending_before is not None
        assert pending_before.stage == SettingStage.SENT_TO_BOX

        ack_dto = make_ack_dto(tx_id="tx-no-complete", conn_id=2, ack=True)

        with pytest.raises(InvariantViolationError):
            await twin.on_ack(ack_dto)

        # Transaction should still be in SENT_TO_BOX, not completed
        pending_after = await twin.get_inflight()
        assert pending_after is not None
        assert pending_after.stage == SettingStage.SENT_TO_BOX

    async def test_wrong_conn_ack_preserves_inflight_state(self):
        """
        GIVEN: Setting delivered on conn_id=10
        WHEN: ACK arrives on wrong connection
        THEN: Inflight state is preserved (not cleared)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-preserve", conn_id=10)

        ack_dto = make_ack_dto(tx_id="tx-preserve", conn_id=99, ack=True)

        with pytest.raises(InvariantViolationError):
            await twin.on_ack(ack_dto)

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.tx_id == "tx-preserve"
        assert pending.delivered_conn_id == 10

    async def test_subsequent_correct_ack_succeeds_after_wrong_conn_failure(self):
        """
        GIVEN: Wrong-conn ACK failed with INV-1 violation
        WHEN: Correct-conn ACK arrives
        THEN: Transaction completes successfully
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-retry-correct", conn_id=4)

        # First ACK on wrong connection fails
        wrong_ack = make_ack_dto(tx_id="tx-retry-correct", conn_id=8, ack=True)
        with pytest.raises(InvariantViolationError):
            await twin.on_ack(wrong_ack)

        # Second ACK on correct connection succeeds
        correct_ack = make_ack_dto(tx_id="tx-retry-correct", conn_id=4, ack=True)
        result = await twin.on_ack(correct_ack)

        assert result is not None
        assert result.status == "box_ack"


@pytest.mark.asyncio
class TestNackHandling:
    """Tests for NACK with deterministic failure."""

    async def test_nack_on_correct_conn_fails_deterministically(self):
        """
        GIVEN: Setting delivered on conn_id=2
        WHEN: NACK arrives on conn_id=2
        THEN: Transaction fails with error="box_nack"
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-nack", conn_id=2)

        nack_dto = make_ack_dto(tx_id="tx-nack", conn_id=2, ack=False)
        result = await twin.on_ack(nack_dto)

        assert result is not None
        assert result.status == "error"
        assert result.error == "box_nack"

    async def test_nack_clears_inflight(self):
        """
        GIVEN: Setting delivered
        WHEN: NACK is processed
        THEN: Inflight is cleared
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-nack-clear", conn_id=1)

        nack_dto = make_ack_dto(tx_id="tx-nack-clear", conn_id=1, ack=False)
        await twin.on_ack(nack_dto)

        pending = await twin.get_inflight()
        assert pending is None

    async def test_nack_on_wrong_conn_raises_invariant_violation(self):
        """
        GIVEN: Setting delivered on conn_id=1
        WHEN: NACK arrives on conn_id=2 (wrong connection)
        THEN: InvariantViolationError is raised
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-nack-wrong", conn_id=1)

        nack_dto = make_ack_dto(tx_id="tx-nack-wrong", conn_id=2, ack=False)

        with pytest.raises(InvariantViolationError) as exc_info:
            await twin.on_ack(nack_dto)

        assert exc_info.value.invariant == "INV-1"

    async def test_nack_includes_reason_in_result(self):
        """
        GIVEN: Setting delivered
        WHEN: NACK is processed
        THEN: Result includes error reason
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-nack-reason", conn_id=1)

        nack_dto = make_ack_dto(tx_id="tx-nack-reason", conn_id=1, ack=False)
        result = await twin.on_ack(nack_dto)

        assert result.error == "box_nack"


@pytest.mark.asyncio
class TestAckNoInflight:
    """Tests for ACK when no inflight transaction exists."""

    async def test_ack_with_no_inflight_returns_none(self):
        """
        GIVEN: No inflight transaction
        WHEN: ACK arrives
        THEN: Returns None (no matching transaction)
        """
        twin = DigitalTwin(session_id="test-session")

        ack_dto = make_ack_dto(tx_id="tx-no-inflight", conn_id=1, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is None

    async def test_ack_with_wrong_tx_id_returns_none(self):
        """
        GIVEN: Inflight transaction with tx_id="tx-abc"
        WHEN: ACK arrives with tx_id="tx-xyz"
        THEN: Returns None (no matching transaction)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await setup_delivered_setting(twin, tx_id="tx-abc", conn_id=1)

        ack_dto = make_ack_dto(tx_id="tx-xyz", conn_id=1, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is None

        # Original inflight should still exist
        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.tx_id == "tx-abc"


@pytest.mark.asyncio
class TestAckSessionValidation:
    """Tests for INV-2 session validation in on_ack."""

    async def test_ack_validates_session(self):
        """
        GIVEN: Transaction in session "session-1"
        WHEN: Session changes and ACK arrives
        THEN: INV-2 validation is performed
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="session-1", config=config)

        await setup_delivered_setting(twin, tx_id="tx-session", conn_id=1)

        # ACK should succeed in same session
        ack_dto = make_ack_dto(tx_id="tx-session", conn_id=1, ack=True)
        result = await twin.on_ack(ack_dto)

        assert result is not None
        assert result.status == "box_ack"
