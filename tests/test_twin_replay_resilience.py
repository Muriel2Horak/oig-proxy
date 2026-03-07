"""Replay Resilience Tests for Twin Inflight Queue.

Task 15: Comprehensive tests for disconnect/reconnect survival.

Tests cover:
1. Replay buffer: transactions moved to buffer on disconnect
2. Duplicate prevention: completed transactions not replayed
3. State recovery: transactions replayed on reconnect
4. Max replay attempts: exceeded attempts result in error

Verification:
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_replay_resilience.py --maxfail=1
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_replay_resilience.py -k replay_buffer --maxfail=1
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_replay_resilience.py -k duplicate --maxfail=1
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_replay_resilience.py -k reconnect --maxfail=1
"""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable

import pytest

from digital_twin import DigitalTwin, DigitalTwinConfig, ReplayEntry
from twin_state import (
    OnAckDTO,
    OnDisconnectDTO,
    OnTblEventDTO,
    QueueSettingDTO,
    SettingStage,
)
from twin_transaction import generate_tx_id


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
) -> OnAckDTO:
    return OnAckDTO(
        tx_id=tx_id,
        conn_id=conn_id,
        ack=ack,
    )


def make_tbl_event_dto(
    tx_id: str | None,
    conn_id: int,
    tbl_name: str,
    tbl_item: str,
    new_value: str,
) -> OnTblEventDTO:
    return OnTblEventDTO(
        tx_id=tx_id,
        conn_id=conn_id,
        event_type="Setting",
        tbl_name=tbl_name,
        tbl_item=tbl_item,
        new_value=new_value,
    )


# =============================================================================
# REPLAY BUFFER TESTS
# =============================================================================


@pytest.mark.asyncio
class TestReplayBuffer:
    """Tests for replay buffer behavior on disconnect."""

    async def test_delivered_but_unacked_moved_to_replay_buffer(self):
        """
        GIVEN: Setting delivered but no ACK yet
        WHEN: BOX disconnects
        THEN: Transaction moved to replay buffer (not lost)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-replay-1"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))

        await twin.on_poll(tx_id=None, conn_id=5, table_name="IsNewSet")

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.delivered_conn_id == 5

        disconnect_dto = OnDisconnectDTO(tx_id=None, conn_id=5)
        results = await twin.on_disconnect(disconnect_dto)

        assert len(results) == 1
        assert results[0].status == "error"
        assert results[0].error == "disconnect"
        assert results[0].detail == "moved_to_replay_buffer"

        assert await twin.get_replay_buffer_length() == 1
        assert await twin.get_inflight() is None

    async def test_not_delivered_transaction_not_in_replay_buffer(self):
        """
        GIVEN: Setting queued but not yet delivered
        WHEN: BOX disconnects
        THEN: Transaction NOT in replay buffer (stays in queue)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-not-delivered"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))

        disconnect_dto = OnDisconnectDTO(tx_id=None, conn_id=5)
        results = await twin.on_disconnect(disconnect_dto)

        assert len(results) == 0
        assert await twin.get_replay_buffer_length() == 0
        assert await twin.get_queue_length() == 1

    async def test_multiple_disconnects_accumulate_replay_buffer(self):
        """
        GIVEN: Multiple transactions delivered then disconnected
        WHEN: Each disconnect occurs
        THEN: All transactions accumulate in replay buffer
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        for i in range(3):
            tx_id = f"tx-multi-{i}"
            await twin.queue_setting(make_queue_dto(tx_id=tx_id))
            await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
            await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        assert await twin.get_replay_buffer_length() == 3

    async def test_replay_entry_contains_original_details(self):
        """
        GIVEN: Transaction moved to replay buffer
        WHEN: Inspecting replay entry
        THEN: Entry contains original transaction details
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-entry-details"
        dto = make_queue_dto(tx_id=tx_id, tbl_name="tbl_invertor_prm1", tbl_item="AAC_MAX_CHRG", new_value="42")
        await twin.queue_setting(dto)

        await twin.on_poll(tx_id=None, conn_id=10, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=10))

        snapshot = await twin.get_replay_buffer_snapshot()
        assert len(snapshot) == 1

        entry = snapshot[0]
        assert entry.dto.tx_id == tx_id
        assert entry.dto.tbl_name == "tbl_invertor_prm1"
        assert entry.dto.tbl_item == "AAC_MAX_CHRG"
        assert entry.dto.new_value == "42"
        assert entry.delivered_at_mono is not None
        assert entry.original_conn_id == 10
        assert entry.last_error == "disconnect"


# =============================================================================
# DUPLICATE PREVENTION TESTS
# =============================================================================


@pytest.mark.asyncio
class TestDuplicatePrevention:
    """Tests for duplicate delivery prevention on replay."""

    async def test_completed_transaction_not_replayed(self):
        """
        GIVEN: Transaction completed before disconnect
        WHEN: Same tx_id re-queued, delivered, then disconnected
        THEN: Transaction NOT added to replay buffer (already completed)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-completed"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))

        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_ack(make_ack_dto(tx_id=tx_id, conn_id=1))
        await twin.on_tbl_event(
            make_tbl_event_dto(tx_id=tx_id, conn_id=1, tbl_name="tbl_box_prms", tbl_item="MODE", new_value="1")
        )
        await twin.finish_inflight(tx_id, conn_id=1, success=True)

        assert twin.is_tx_completed(tx_id)

        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        assert await twin.get_replay_buffer_length() == 0

        results = await twin.on_reconnect(conn_id=2)

        assert len(results) == 0
        assert await twin.get_queue_length() == 0

    async def test_nacked_transaction_can_be_replayed(self):
        """
        GIVEN: Transaction NACKed (not completed successfully)
        WHEN: Transaction re-queued and disconnected
        THEN: Transaction CAN be replayed (not in completed set)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-nacked"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))

        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_ack(make_ack_dto(tx_id=tx_id, conn_id=1, ack=False))

        assert not twin.is_tx_completed(tx_id)

        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        assert await twin.get_replay_buffer_length() == 1

        results = await twin.on_reconnect(conn_id=2)

        assert len(results) == 0
        assert await twin.get_queue_length() == 1

    async def test_completed_tx_id_tracking(self):
        """
        GIVEN: Multiple transactions completed
        WHEN: Checking completed status
        THEN: Only successfully completed transactions tracked
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_success = "tx-success"
        tx_error = "tx-error"

        await twin.queue_setting(make_queue_dto(tx_id=tx_success))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_ack(make_ack_dto(tx_id=tx_success, conn_id=1))
        await twin.finish_inflight(tx_success, conn_id=1, success=True)

        assert twin.is_tx_completed(tx_success)

        await twin.queue_setting(make_queue_dto(tx_id=tx_error))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_ack(make_ack_dto(tx_id=tx_error, conn_id=1))
        await twin.finish_inflight(tx_error, conn_id=1, success=False)

        assert not twin.is_tx_completed(tx_error)


# =============================================================================
# STATE RECOVERY (RECONNECT) TESTS
# =============================================================================


@pytest.mark.asyncio
class TestStateRecovery:
    """Tests for state recovery after reconnect."""

    async def test_reconnect_moves_replay_to_queue(self):
        """
        GIVEN: Transactions in replay buffer
        WHEN: BOX reconnects
        THEN: Transactions moved to main queue for delivery
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-reconnect-1"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        assert await twin.get_replay_buffer_length() == 1
        assert await twin.get_queue_length() == 0

        results = await twin.on_reconnect(conn_id=2)

        assert len(results) == 0
        assert await twin.get_replay_buffer_length() == 0
        assert await twin.get_queue_length() == 1

    async def test_replayed_transaction_delivered_on_new_connection(self):
        """
        GIVEN: Transaction replayed after reconnect
        WHEN: New poll arrives
        THEN: Transaction delivered on NEW connection
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-new-conn"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        await twin.on_reconnect(conn_id=2)

        response = await twin.on_poll(tx_id=None, conn_id=2, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response.frame_data

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.delivered_conn_id == 2

    async def test_replay_count_incremented(self):
        """
        GIVEN: Transaction in replay buffer
        WHEN: Reconnected and disconnected again
        THEN: Replay count incremented each cycle
        """
        config = DigitalTwinConfig(device_id="12345", max_replay_attempts=5)
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-replay-count"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        snapshot = await twin.get_replay_buffer_snapshot()
        assert snapshot[0].replay_count == 0

        await twin.on_reconnect(conn_id=2)
        await twin.on_poll(tx_id=None, conn_id=2, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=2))

        snapshot = await twin.get_replay_buffer_snapshot()
        assert snapshot[0].replay_count == 1

        await twin.on_reconnect(conn_id=3)
        await twin.on_poll(tx_id=None, conn_id=3, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=3))

        snapshot = await twin.get_replay_buffer_snapshot()
        assert snapshot[0].replay_count == 2

    async def test_full_roundtrip_after_reconnect(self):
        """
        GIVEN: Transaction disconnected mid-way, then reconnected
        WHEN: Full roundtrip completes after reconnect
        THEN: Transaction reaches COMPLETED stage
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-full-reconnect"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))

        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        await twin.on_reconnect(conn_id=2)

        response = await twin.on_poll(tx_id=None, conn_id=2, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response.frame_data

        await twin.on_ack(make_ack_dto(tx_id=tx_id, conn_id=2))
        await twin.on_tbl_event(
            make_tbl_event_dto(tx_id=tx_id, conn_id=2, tbl_name="tbl_box_prms", tbl_item="MODE", new_value="1")
        )
        result = await twin.finish_inflight(tx_id, conn_id=2, success=True)

        assert result.status == "completed"
        assert twin.is_tx_completed(tx_id)


# =============================================================================
# MAX REPLAY ATTEMPTS TESTS
# =============================================================================


@pytest.mark.asyncio
class TestMaxReplayAttempts:
    """Tests for max replay attempts enforcement."""

    async def test_exceed_max_replay_returns_error(self):
        """
        GIVEN: Transaction replayed max_attempts times
        WHEN: Another reconnect occurs
        THEN: Error returned, transaction not re-queued
        """
        config = DigitalTwinConfig(device_id="12345", max_replay_attempts=2)
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-max-replay"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        for i in range(config.max_replay_attempts):
            await twin.on_reconnect(conn_id=i + 2)
            await twin.on_poll(tx_id=None, conn_id=i + 2, table_name="IsNewSet")
            await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=i + 2))

        results = await twin.on_reconnect(conn_id=99)

        assert len(results) == 1
        assert results[0].status == "error"
        assert results[0].error == "max_replay_exceeded"
        assert await twin.get_queue_length() == 0

    async def test_successful_completion_resets_replay_count(self):
        """
        GIVEN: Transaction replayed once then completed
        WHEN: New transaction with same tx_id later disconnects
        THEN: New transaction starts with fresh replay count
        """
        config = DigitalTwinConfig(device_id="12345", max_replay_attempts=5)
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-reset-replay"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        await twin.on_reconnect(conn_id=2)

        await twin.on_poll(tx_id=None, conn_id=2, table_name="IsNewSet")
        await twin.on_ack(make_ack_dto(tx_id=tx_id, conn_id=2))
        await twin.finish_inflight(tx_id, conn_id=2, success=True)

        assert twin.is_tx_completed(tx_id)


# =============================================================================
# SNAPSHOT INTEGRATION TESTS
# =============================================================================


@pytest.mark.asyncio
class TestSnapshotIntegration:
    """Tests for snapshot including replay buffer info."""

    async def test_snapshot_includes_replay_buffer_length(self):
        """
        GIVEN: Transactions in replay buffer
        WHEN: Getting snapshot
        THEN: Snapshot includes replay_buffer_length
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        for i in range(3):
            tx_id = f"tx-snap-{i}"
            await twin.queue_setting(make_queue_dto(tx_id=tx_id))
            await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
            await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        snapshot = await twin.get_snapshot(conn_id=1)

        assert snapshot.replay_buffer_length == 3

    async def test_snapshot_includes_completed_tx_count(self):
        """
        GIVEN: Multiple completed transactions
        WHEN: Getting snapshot
        THEN: Snapshot includes completed_tx_count
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        for i in range(5):
            tx_id = f"tx-done-{i}"
            await twin.queue_setting(make_queue_dto(tx_id=tx_id))
            await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
            await twin.on_ack(make_ack_dto(tx_id=tx_id, conn_id=1))
            await twin.finish_inflight(tx_id, conn_id=1, success=True)

        snapshot = await twin.get_snapshot(conn_id=1)

        assert snapshot.completed_tx_count == 5

    async def test_clear_all_clears_replay_buffer(self):
        """
        GIVEN: Transactions in replay buffer and completed set
        WHEN: clear_all called
        THEN: All state cleared including replay buffer
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_id = "tx-clear"
        await twin.queue_setting(make_queue_dto(tx_id=tx_id))
        await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        await twin.on_reconnect(conn_id=2)
        await twin.on_poll(tx_id=None, conn_id=2, table_name="IsNewSet")
        await twin.on_ack(make_ack_dto(tx_id=tx_id, conn_id=2))
        await twin.finish_inflight(tx_id, conn_id=2, success=True)

        await twin.queue_setting(make_queue_dto(tx_id="tx-clear-2"))
        await twin.on_poll(tx_id=None, conn_id=2, table_name="IsNewSet")
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=2))

        await twin.clear_all()

        assert await twin.get_replay_buffer_length() == 0
        assert await twin.get_queue_length() == 0
        assert len(twin._completed_tx_ids) == 0


# =============================================================================
# EDGE CASES
# =============================================================================


@pytest.mark.asyncio
class TestReplayEdgeCases:
    """Tests for edge cases in replay resilience."""

    async def test_reconnect_with_empty_buffer(self):
        """
        GIVEN: Empty replay buffer
        WHEN: BOX reconnects
        THEN: No error, empty results
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        results = await twin.on_reconnect(conn_id=1)

        assert len(results) == 0
        assert await twin.get_queue_length() == 0

    async def test_disconnect_with_no_inflight(self):
        """
        GIVEN: No inflight transaction
        WHEN: BOX disconnects
        THEN: No error, nothing in replay buffer
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        results = await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        assert len(results) == 0
        assert await twin.get_replay_buffer_length() == 0

    async def test_multiple_transactions_replayed_in_order(self):
        """
        GIVEN: Multiple transactions in replay buffer
        WHEN: BOX reconnects
        THEN: All transactions replayed in FIFO order
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        tx_ids = ["tx-order-1", "tx-order-2", "tx-order-3"]
        for tx_id in tx_ids:
            await twin.queue_setting(make_queue_dto(tx_id=tx_id))
            await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
            await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        assert await twin.get_replay_buffer_length() == 3

        await twin.on_reconnect(conn_id=2)

        assert await twin.get_queue_length() == 3

        for expected_tx_id in tx_ids:
            response = await twin.on_poll(tx_id=None, conn_id=2, table_name="IsNewSet")
            assert "<Reason>Setting</Reason>" in response.frame_data

            pending = await twin.get_inflight()
            assert pending.tx_id == expected_tx_id

            await twin.on_ack(make_ack_dto(tx_id=expected_tx_id, conn_id=2))
            await twin.finish_inflight(expected_tx_id, conn_id=2, success=True)
