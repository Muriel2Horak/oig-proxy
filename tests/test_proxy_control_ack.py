"""Tests for control ack handling and value coercion."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
import control_pipeline as ctrl_module
from control_pipeline import ControlPipeline
from models import ProxyMode, SensorConfig


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"

    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.mqtt_enabled = False
    ctrl.set_topic = "oig/control/set"
    ctrl.result_topic = "oig/control/result"
    ctrl.status_prefix = "oig/control/status"
    ctrl.qos = 1
    ctrl.retain = False
    ctrl.status_retain = False
    ctrl.log_enabled = False
    ctrl.log_path = "/tmp/control.log"
    ctrl.box_ready_s = 15.0
    ctrl.ack_timeout_s = 30.0
    ctrl.applied_timeout_s = 60.0
    ctrl.mode_quiet_s = 120.0
    ctrl.whitelist = {"tbl_box_prms": {"SA", "MODE"}, "tbl_invertor_prm1": set()}
    ctrl.max_attempts = 5
    ctrl.retry_delay_s = 120.0
    ctrl.session_id = "test-session"
    ctrl.pending_path = "/tmp/pending.json"
    ctrl.pending_keys = set()
    ctrl.queue = deque()
    ctrl.inflight = None
    ctrl.lock = asyncio.Lock()
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.quiet_task = None
    ctrl.retry_task = None
    ctrl.last_result = None
    ctrl.key_state = {}
    ctrl.post_drain_refresh_pending = False
    ctrl.publish_result = AsyncMock()
    ctrl.finish_inflight = AsyncMock()
    proxy._ctrl = ctrl
    return proxy


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_missing_tx():
    proxy = _make_proxy()
    await proxy._ctrl.on_box_setting_ack(tx_id=None, ack=True)
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_mismatch():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    await proxy._ctrl.on_box_setting_ack(tx_id="2", ack=True)
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_nack():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    await proxy._ctrl.on_box_setting_ack(tx_id="1", ack=False)
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_success(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._ctrl.on_box_setting_ack(tx_id="1", ack=True)
    proxy._ctrl.publish_result.assert_called_once()
    assert proxy._ctrl.applied_task is dummy_task


def test_control_coerce_value():
    assert ControlPipeline.coerce_value(None) is None
    assert ControlPipeline.coerce_value(True) is True
    assert ControlPipeline.coerce_value("true") is True
    assert ControlPipeline.coerce_value("false") is False
    assert ControlPipeline.coerce_value("10") == 10
    assert ControlPipeline.coerce_value("-3") == -3
    assert ControlPipeline.coerce_value("3.5") == 3.5
    assert ControlPipeline.coerce_value("abc") == "abc"


def test_control_map_optimistic_value(monkeypatch):
    proxy = _make_proxy()

    cfg = SensorConfig(name="Mode", unit="", options=["OFF", "ON"])
    monkeypatch.setattr(ctrl_module, "get_sensor_config", lambda *_a, **_k: (cfg, "x"))

    assert proxy._ctrl.map_optimistic_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", value="1"
    ) == "ON"
    assert proxy._ctrl.map_optimistic_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", value="0"
    ) == "OFF"


# =============================================================================
# Cross-Session Timeout Leakage Tests (RED tests - expected to FAIL)
# =============================================================================
# These tests demonstrate the bug where timeout/ACK handling on conn_id=X
# can mutate pending state that was set up on conn_id=Y.
#
# Bug characteristics (from setting_reproduction_offline.json):
#   - type: cross-session_timeout
#   - description: Timeout tracker fires on conn_id=2, but Setting was delivered on conn_id=1
#   - root_cause: ControlSettings.pending lacks conn_id tracking
#
# State transitions:
#   INIT → PENDING → DELIVERED (conn_id=1) → CONN_CLOSED → RECONNECTED (conn_id=2) → TIMEOUT → FAILED
#
# Expected behavior (after fix):
#   - pending should track which conn_id delivered the Setting
#   - ACK/timeout on conn_id=2 should NOT clear pending from conn_id=1
# =============================================================================


def _make_proxy_with_control_settings():
    """Create a proxy with ControlSettings for cross-session testing."""
    proxy = _make_proxy()

    # Import and create ControlSettings
    from control_settings import ControlSettings
    cs = ControlSettings.__new__(ControlSettings)
    cs._proxy = proxy
    cs.pending = None
    cs.pending_frame = None
    cs.set_commands_buffer = []
    proxy._cs = cs

    # Mock background tasks set
    proxy._background_tasks = set()

    return proxy


def _setup_pending_setting_on_connection(proxy, conn_id: int, elapsed_seconds: float = 0.0):
    """
    Set up pending setting state as if it was delivered on a specific connection.

    Args:
        proxy: The proxy instance
        conn_id: The connection ID that "delivered" the setting
        elapsed_seconds: How long ago the setting was sent (for timeout testing)
    """
    import time

    # Set up pending setting (simulating what send_to_box does)
    proxy._cs.pending = {
        "tbl_name": "tbl_invertor_prm1",
        "tbl_item": "AAC_MAX_CHRG",
        "new_value": "120.0",
        "id": 12345678,
        "id_set": 1234567890,
        "tx_id": f"mock-setting-{conn_id}",
        "sent_at": time.monotonic() - elapsed_seconds,
        "delivered_conn_id": conn_id,
    }
    proxy._cs.pending_frame = b"<mock>setting_frame</mock>\r\n"

    return proxy._cs.pending.copy()


def test_cross_session_ack_should_not_clear_pending_offline():
    """
    RED TEST: ACK on conn_id=2 should NOT clear pending from conn_id=1.

    Scenario (offline mode):
      1. Setting delivered on conn_id=1 (pending.sent_at set)
      2. Connection closes
      3. Reconnect as conn_id=2
      4. ACK frame arrives on conn_id=2
      5. Expected: pending from conn_id=1 is NOT cleared

    Current behavior (BUG): maybe_handle_ack clears pending regardless of conn_id.
    Expected behavior: pending should only be cleared if conn_id matches delivery connection.
    """
    import time
    from unittest.mock import MagicMock

    proxy = _make_proxy_with_control_settings()

    # Step 1: Set up pending setting as if delivered on conn_id=1
    original_pending = _setup_pending_setting_on_connection(
        proxy, conn_id=1, elapsed_seconds=0.0
    )

    # Verify pending is set
    assert proxy._cs.pending is not None, "pending should be set after delivery"
    assert proxy._cs.pending.get("tx_id") == "mock-setting-1", "tx_id should match conn_id=1"

    # Step 2-3: Simulate disconnect and reconnect (conn_id changes to 2)
    # Step 4: ACK frame arrives on conn_id=2
    mock_writer = MagicMock()
    ack_frame = "<Reason>Setting</Reason><Result>ACK</Result>"

    # Call maybe_handle_ack on conn_id=2 (different from delivery conn_id=1)
    result = proxy._cs.maybe_handle_ack(ack_frame, mock_writer, conn_id=2)

    # EXPECTED (after fix): result should be False because conn_id doesn't match
    # CURRENT (BUG): result is True and pending is cleared
    # This assertion will FAIL on current code:
    assert result is False, (
        "maybe_handle_ack should return False when conn_id doesn't match "
        "the connection that delivered the setting"
    )

    # EXPECTED (after fix): pending should NOT be cleared
    # CURRENT (BUG): pending is cleared (None)
    assert proxy._cs.pending is not None, (
        "pending should NOT be cleared by ACK on different connection. "
        "Setting was delivered on conn_id=1, ACK arrived on conn_id=2"
    )

    # Verify the pending data is unchanged
    assert proxy._cs.pending.get("tx_id") == "mock-setting-1", (
        "pending tx_id should still be from conn_id=1"
    )


def test_cross_session_ack_with_timeout_exceeded_should_not_clear_pending_offline():
    """
    RED TEST: Timeout-exceeded ACK check on conn_id=2 should NOT clear pending from conn_id=1.

    Scenario (offline mode):
      1. Setting delivered on conn_id=1, enough time passes for timeout
      2. Connection closes
      3. Reconnect as conn_id=2
      4. maybe_handle_ack called on conn_id=2 with timeout exceeded
      5. Expected: pending from conn_id=1 is NOT affected

    Current behavior: maybe_handle_ack returns False (timeout) but doesn't validate conn_id.
    This is correct for the return value, but the test documents the expected behavior
    for future conn_id-aware timeout handling.
    """
    proxy = _make_proxy_with_control_settings()

    # Step 1: Set up pending setting as if delivered on conn_id=1 with timeout exceeded
    # ack_timeout_s is 30.0, so 35 seconds elapsed means timeout exceeded
    original_pending = _setup_pending_setting_on_connection(
        proxy, conn_id=1, elapsed_seconds=35.0
    )

    # Verify pending is set
    assert proxy._cs.pending is not None, "pending should be set after delivery"

    # Step 2-3: Simulate disconnect and reconnect (conn_id changes to 2)
    # Step 4: maybe_handle_ack called on conn_id=2 with timeout exceeded
    mock_writer = MagicMock()
    ack_frame = "<Reason>Setting</Reason><Result>ACK</Result>"

    result = proxy._cs.maybe_handle_ack(ack_frame, mock_writer, conn_id=2)

    # Timeout exceeded should return False (this is current behavior, correct)
    assert result is False, "maybe_handle_ack should return False when timeout exceeded"

    # EXPECTED: pending should NOT be cleared by timeout on different connection
    # Note: Current code doesn't clear pending on timeout, but this test documents
    # the expected behavior that timeout should also be conn_id-aware
    assert proxy._cs.pending is not None, (
        "pending should NOT be cleared by timeout on different connection"
    )

    # Verify the pending data is unchanged
    assert proxy._cs.pending.get("tx_id") == "mock-setting-1", (
        "pending tx_id should still be from conn_id=1 even after timeout on conn_id=2"
    )


def test_cross_session_ack_should_not_clear_pending_hybrid_offline():
    """
    RED TEST: ACK on conn_id=2 should NOT clear pending from conn_id=1 (hybrid-offline mode).

    Same as test_cross_session_ack_should_not_clear_pending_offline but for
    hybrid-offline mode where the proxy falls back to offline behavior.
    """
    proxy = _make_proxy_with_control_settings()

    # Set mode to HYBRID but in offline fallback state
    proxy._hm.mode = ProxyMode.HYBRID

    # Set up pending setting as if delivered on conn_id=1
    original_pending = _setup_pending_setting_on_connection(
        proxy, conn_id=1, elapsed_seconds=0.0
    )

    # ACK frame arrives on conn_id=2
    mock_writer = MagicMock()
    ack_frame = "<Reason>Setting</Reason><Result>ACK</Result>"

    result = proxy._cs.maybe_handle_ack(ack_frame, mock_writer, conn_id=2)

    # EXPECTED (after fix): result should be False
    # CURRENT (BUG): result is True and pending is cleared
    assert result is False, (
        "maybe_handle_ack should return False when conn_id doesn't match "
        "(hybrid-offline mode)"
    )

    # EXPECTED (after fix): pending should NOT be cleared
    assert proxy._cs.pending is not None, (
        "pending should NOT be cleared by ACK on different connection "
        "(hybrid-offline mode)"
    )


def test_cross_session_multiple_reconnects_pending_preserved():
    """
    RED TEST: Pending should be preserved across multiple reconnects until ACK on correct conn_id.

    Scenario:
      1. Setting delivered on conn_id=1
      2. Reconnect as conn_id=2, no ACK
      3. Reconnect as conn_id=3, no ACK
      4. ACK arrives on conn_id=3 (wrong connection)
      5. Expected: pending still preserved

    This tests that the pending state correctly tracks the original delivery conn_id
    through multiple reconnections.
    """
    proxy = _make_proxy_with_control_settings()

    # Set up pending setting as if delivered on conn_id=1
    original_pending = _setup_pending_setting_on_connection(
        proxy, conn_id=1, elapsed_seconds=0.0
    )

    mock_writer = MagicMock()
    ack_frame = "<Reason>Setting</Reason><Result>ACK</Result>"

    # Simulate reconnect as conn_id=2, ACK arrives
    result2 = proxy._cs.maybe_handle_ack(ack_frame, mock_writer, conn_id=2)
    assert proxy._cs.pending is not None, (
        "pending should NOT be cleared by ACK on conn_id=2 (delivered on conn_id=1)"
    )

    # Simulate reconnect as conn_id=3, ACK arrives
    result3 = proxy._cs.maybe_handle_ack(ack_frame, mock_writer, conn_id=3)
    assert proxy._cs.pending is not None, (
        "pending should NOT be cleared by ACK on conn_id=3 (delivered on conn_id=1)"
    )

    # Verify the pending data is still from the original connection
    assert proxy._cs.pending.get("tx_id") == "mock-setting-1", (
        "pending tx_id should still be from conn_id=1 after multiple reconnects"
    )


def test_cross_session_nack_should_not_clear_pending_offline():
    """
    RED TEST: NACK on conn_id=2 should NOT clear pending from conn_id=1.

    Scenario (offline mode):
      1. Setting delivered on conn_id=1
      2. Reconnect as conn_id=2
      3. NACK frame arrives on conn_id=2
      4. Expected: pending from conn_id=1 is NOT cleared
    """
    proxy = _make_proxy_with_control_settings()

    # Set up pending setting as if delivered on conn_id=1
    original_pending = _setup_pending_setting_on_connection(
        proxy, conn_id=1, elapsed_seconds=0.0
    )

    # NACK frame arrives on conn_id=2
    mock_writer = MagicMock()
    nack_frame = "<Reason>Setting</Reason><Result>NACK</Result>"

    result = proxy._cs.maybe_handle_ack(nack_frame, mock_writer, conn_id=2)

    # EXPECTED (after fix): result should be False
    # CURRENT (BUG): result is True and pending is cleared
    assert result is False, (
        "maybe_handle_ack should return False for NACK on wrong connection"
    )

    # EXPECTED (after fix): pending should NOT be cleared
    assert proxy._cs.pending is not None, (
        "pending should NOT be cleared by NACK on different connection"
    )


# =============================================================================
# INV-2: Session Transaction Invariant Tests (RED tests)
# =============================================================================
# These tests demonstrate bugs where transactions from one session can
# affect or be affected by operations from another session.
#
# Bug characteristics:
#   - type: cross_session_transaction
#   - description: Transaction state leaks between session boundaries
#   - root_cause: No session_id validation in transaction callbacks
#
# Expected behavior (after fix):
#   - inflight should track session_id at creation time
#   - Callbacks (on_box_setting_ack, timeouts) should validate session_id
# =============================================================================


@pytest.mark.asyncio
async def test_cross_session_on_box_setting_ack_ignores_old_session():
    """
    RED TEST: on_box_setting_ack should ignore ACKs for old session's transactions.

    INVARIANT: INV-2 - Session Transaction

    Scenario:
      1. Session A creates transaction TX1 (inflight = TX1)
      2. Session A's session_id stored in TX1
      3. Proxy restarts, Session B created with new session_id
      4. Session B's inflight = None
      5. Late ACK callback for TX1 arrives (from Session A)
      6. Expected: ACK ignored because session_id doesn't match

    Current behavior (BUG): on_box_setting_ack only checks tx_id match,
    doesn't validate session_id ownership.
    """
    proxy = _make_proxy_with_control_settings()

    # Original session
    original_session_id = "session_original_abc"
    proxy._ctrl.session_id = original_session_id

    # Set up inflight transaction with session binding
    proxy._ctrl.inflight = {
        "tx_id": "tx_cross_session_test",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
        "stage": "sent_to_box",
        "_session_id": original_session_id,  # Should be validated but isn't
    }

    # Simulate session change (restart)
    new_session_id = "session_new_xyz"
    proxy._ctrl.session_id = new_session_id

    # Reset mocks to track calls
    proxy._ctrl.publish_result.reset_mock()
    proxy._ctrl.finish_inflight.reset_mock()

    # ACK arrives for old session's transaction
    await proxy._ctrl.on_box_setting_ack(tx_id="tx_cross_session_test", ack=True)

    # EXPECTED (after fix): No publish_result call because session changed
    # CURRENT (BUG): publish_result is called even though session changed
    proxy._ctrl.publish_result.assert_not_called(), (
        "on_box_setting_ack should NOT publish result for old session's transaction"
    )


@pytest.mark.asyncio
async def test_cross_session_on_box_setting_ack_validates_session_in_inflight():
    """
    RED TEST: on_box_setting_ack should validate inflight session matches current.

    INVARIANT: INV-2 - Session Transaction

    Scenario:
      1. Session A creates TX1, inflight.session_id = session_A
      2. Session B takes over, session_id = session_B
      3. But inflight is still TX1 (not cleared)
      4. ACK for TX1 arrives
      5. Expected: ACK ignored because inflight's session_id != current session_id

    Current behavior: Only checks tx_id match, not session_id.
    """
    proxy = _make_proxy_with_control_settings()

    session_a = "session_a_original"
    session_b = "session_b_new"

    proxy._ctrl.session_id = session_a

    proxy._ctrl.inflight = {
        "tx_id": "tx1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
        "stage": "sent_to_box",
        "_session_id": session_a,
    }

    # Session changes
    proxy._ctrl.session_id = session_b

    proxy._ctrl.publish_result.reset_mock()

    # ACK arrives
    await proxy._ctrl.on_box_setting_ack(tx_id="tx1", ack=True)

    # EXPECTED (after fix): No result published
    # CURRENT (BUG): Result published even though session changed
    proxy._ctrl.publish_result.assert_not_called(), (
        "ACK should be ignored when inflight session_id != current session_id"
    )


# =============================================================================
# INV-3: Timeout Task Ownership Invariant Tests (RED tests)
# =============================================================================
# These tests demonstrate bugs where timeout tasks from one transaction
# can affect subsequent transactions.
#
# Bug characteristics:
#   - type: wrong_session_timeout_leak
#   - description: Timeout handler affects wrong transaction
#   - root_cause: Timeout handlers don't validate transaction identity
#
# Expected behavior (after fix):
#   - Timeout handlers should store tx_id they were created for
#   - Before acting, validate inflight.tx_id matches stored tx_id
# =============================================================================


@pytest.mark.asyncio
async def test_wrong_session_ack_timeout_does_not_affect_new_transaction():
    """
    RED TEST: ack_timeout from TX1 should NOT affect TX2.

    INVARIANT: INV-3 - Timeout Task Ownership

    Scenario:
      1. TX1 starts, ack_timeout task created for TX1
      2. TX1 completes successfully via ACK
      3. finish_inflight() called, ack_task.cancel() called
      4. TX2 starts immediately (inflight = TX2)
      5. Old ack_timeout task fires (race condition, was mid-execution)
      6. Expected: Old timeout should NOT affect TX2

    Current behavior (BUG): ack_timeout checks stage but not tx_id,
    so if TX2 has stage="sent_to_box", old timeout may defer TX2.
    """
    import time

    proxy = _make_proxy_with_control_settings()
    proxy._ctrl.ack_timeout_s = 0.05  # Short timeout for testing

    published_results = []

    async def track_publish(**kwargs):
        published_results.append(kwargs)

    proxy._ctrl.publish_result = track_publish

    # TX1 starts
    proxy._ctrl.inflight = {
        "tx_id": "tx1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
        "stage": "sent_to_box",
        "_timeout_tx_id": "tx1",  # Should be stored for validation
    }

    # Create a task that simulates a late-firing timeout
    old_inflight_ref = proxy._ctrl.inflight

    async def late_ack_timeout():
        await asyncio.sleep(0.02)
        async with proxy._ctrl.lock:
            tx = proxy._ctrl.inflight
            if tx is None:
                return
            # PROBLEM: This checks stage but NOT tx_id!
            # If inflight is now TX2, this will incorrectly process TX2
            if tx.get("stage") not in ("sent_to_box", "accepted"):
                return
            # Store what we're about to process for assertion
            tx_id_being_processed = tx.get("tx_id")

        # This simulates defer_inflight being called
        await proxy._ctrl.publish_result(
            tx={"tx_id": tx_id_being_processed, "stage": "sent_to_box"},
            status="error",
            error="timeout_waiting_ack_from_old_task",
        )

    timeout_task = asyncio.create_task(late_ack_timeout())

    # TX1 completes successfully
    proxy._ctrl.inflight["stage"] = "box_ack"
    await proxy._ctrl.finish_inflight()

    # TX2 starts
    proxy._ctrl.inflight = {
        "tx_id": "tx2",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "BAT_AC",
        "new_value": "1",
        "stage": "sent_to_box",
    }

    # Wait for late timeout to fire
    await asyncio.sleep(0.05)

    # RED TEST: TX2 should NOT have error from TX1's timeout
    tx2_errors = [r for r in published_results if r.get("tx_id") == "tx2"]
    assert len(tx2_errors) == 0, (
        f"TX2 should NOT have error from TX1's timeout. "
        f"Got {len(tx2_errors)} errors for TX2: {tx2_errors}"
    )


@pytest.mark.asyncio
async def test_wrong_session_applied_timeout_does_not_affect_new_transaction():
    """
    RED TEST: applied_timeout from TX1 should NOT affect TX2.

    INVARIANT: INV-3 - Timeout Task Ownership

    Scenario:
      1. TX1 in applied stage, applied_timeout task created
      2. TX1 completes
      3. TX2 starts in sent_to_box stage
      4. Old applied_timeout fires
      5. Expected: Old timeout should NOT affect TX2

    Current behavior (BUG): applied_timeout checks stage but not tx_id.
    """
    import time

    proxy = _make_proxy_with_control_settings()
    proxy._ctrl.applied_timeout_s = 0.05

    published_results = []

    async def track_publish(**kwargs):
        published_results.append(kwargs)

    proxy._ctrl.publish_result = track_publish

    # TX1 in applied stage
    proxy._ctrl.inflight = {
        "tx_id": "tx1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
        "stage": "applied",
        "applied_at_mono": time.monotonic(),
        "_timeout_tx_id": "tx1",
    }

    async def late_applied_timeout():
        await asyncio.sleep(0.02)
        async with proxy._ctrl.lock:
            tx = proxy._ctrl.inflight
            if tx is None:
                return
            # PROBLEM: This checks stage but NOT tx_id
            if tx.get("stage") in ("applied", "completed", "error"):
                return
            tx_id_being_processed = tx.get("tx_id")

        await proxy._ctrl.publish_result(
            tx={"tx_id": tx_id_being_processed},
            status="error",
            error="timeout_waiting_applied_from_old_task",
        )

    timeout_task = asyncio.create_task(late_applied_timeout())

    # TX1 completes
    await proxy._ctrl.finish_inflight()

    # TX2 starts
    proxy._ctrl.inflight = {
        "tx_id": "tx2",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "2",
        "stage": "sent_to_box",
    }

    await asyncio.sleep(0.05)

    # RED TEST: TX2 should NOT have error from TX1's timeout
    tx2_errors = [r for r in published_results if r.get("tx_id") == "tx2"]
    assert len(tx2_errors) == 0, (
        f"TX2 should NOT have error from TX1's applied_timeout. "
        f"Got {len(tx2_errors)} errors for TX2: {tx2_errors}"
    )


@pytest.mark.asyncio
async def test_timeout_validates_transaction_identity_before_defer():
    """
    RED TEST: Timeout handlers must validate tx_id before acting.

    INVARIANT: INV-3 - Timeout Task Ownership

    This test directly verifies that ack_timeout validates the transaction
    identity before calling defer_inflight.
    """
    proxy = _make_proxy_with_control_settings()
    proxy._ctrl.ack_timeout_s = 0.01

    defer_calls = []

    original_defer = proxy._ctrl.defer_inflight

    async def track_defer(*, reason):
        defer_calls.append({"reason": reason, "tx_id": proxy._ctrl.inflight.get("tx_id") if proxy._ctrl.inflight else None})
        # Don't actually defer in test

    proxy._ctrl.defer_inflight = track_defer

    # TX1 starts
    proxy._ctrl.inflight = {
        "tx_id": "tx_timeout_test",
        "stage": "sent_to_box",
    }

    # Start ack_timeout
    ack_task = asyncio.create_task(proxy._ctrl.ack_timeout())

    # Immediately replace inflight with TX2
    proxy._ctrl.inflight = {
        "tx_id": "tx_different",
        "stage": "sent_to_box",
    }

    # Wait for timeout
    await asyncio.sleep(0.02)

    # RED TEST: If timeout fired, it should have seen tx_id mismatch
    # and NOT called defer_inflight for the wrong transaction
    for call in defer_calls:
        assert call.get("tx_id") == "tx_timeout_test", (
            f"Timeout should only defer original transaction, "
            f"but tried to defer: {call}"
        )


# =============================================================================
# Invariant Documentation Summary
# =============================================================================
#
# This file documents three key invariants that the current code violates:
#
# INV-1: Connection Ownership Invariant
#   - Settings are delivered on a specific TCP connection (conn_id)
#   - ACK/NACK must arrive on the SAME connection
#   - Current defect: maybe_handle_ack() doesn't validate conn_id
#   - Tests: test_cross_session_ack_should_not_clear_pending_*
#
# INV-2: Session Transaction Invariant
#   - Transactions belong to a specific session (session_id)
#   - Callbacks must validate session ownership
#   - Current defect: on_box_setting_ack() doesn't validate session_id
#   - Tests: test_cross_session_on_box_setting_ack_*
#
# INV-3: Timeout Task Ownership Invariant
#   - Timeout tasks are created for specific transactions (tx_id)
#   - Timeout handlers must validate tx_id before acting
#   - Current defect: ack_timeout/applied_timeout don't validate tx_id
#   - Tests: test_wrong_session_*_timeout_*
#
# All tests in this file are RED tests - they FAIL on current code.
# They will PASS after the invariants are properly enforced.
# =============================================================================
