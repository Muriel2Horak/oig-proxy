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
