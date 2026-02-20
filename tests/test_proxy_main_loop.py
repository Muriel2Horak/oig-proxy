"""Tests for main box connection loop and offline handling."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
import cloud_forwarder as cf_module
import control_settings as cs_module
import control_pipeline as ctrl_module
from cloud_forwarder import CloudForwarder
from models import ProxyMode


class DummyWriter:
    def __init__(self):
        self.buffer = []

    def write(self, data):
        self.buffer.append(data)

    async def drain(self):
        return None


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)
    proxy.device_id = "DEV1"
    proxy._active_box_peer = "peer"
    proxy._tc = MagicMock()
    proxy._close_writer = AsyncMock()
    proxy._read_box_bytes = AsyncMock()
    proxy._process_box_frame_common = AsyncMock(return_value=("DEV1", "tbl"))
    proxy._cs = MagicMock()
    proxy._cs.maybe_handle_ack = MagicMock(return_value=False)
    proxy._cf = MagicMock()
    proxy._cf.handle_frame_offline_mode = AsyncMock(return_value=(None, None))
    proxy._cf.forward_frame = AsyncMock(return_value=(None, None))
    proxy._cf.session_connected = False
    proxy._cf.rx_buf = bytearray()
    return proxy


@pytest.mark.asyncio
async def test_process_frame_offline_sends_ack(monkeypatch):
    proxy = _make_proxy()
    proxy.stats = {"acks_local": 0}
    monkeypatch.setattr(proxy_module, "build_offline_ack_frame", lambda _tbl: b"ACK")
    writer = DummyWriter()

    await proxy._process_frame_offline(
        _frame_bytes=b"x",
        table_name="tbl",
        _device_id="DEV1",
        box_writer=writer,
        send_ack=True,
        conn_id=1,
    )

    assert writer.buffer == [b"ACK"]
    assert proxy.stats["acks_local"] == 1


@pytest.mark.asyncio
async def test_handle_frame_offline_mode_closes_cloud():
    proxy = _make_proxy()
    proxy.stats = {"acks_local": 0}
    proxy._process_frame_offline = AsyncMock()

    cf = CloudForwarder.__new__(CloudForwarder)
    cf._proxy = proxy
    cf.connects = 0
    cf.disconnects = 0
    cf.timeouts = 0
    cf.errors = 0
    cf.session_connected = True
    cf.connected_since_epoch = None
    cf.peer = None
    cf.rx_buf = bytearray()
    proxy._cf = cf

    reader, writer = await cf.handle_frame_offline_mode(
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        conn_id=1,
        box_writer=DummyWriter(),
        cloud_writer=DummyWriter(),
    )

    assert (reader, writer) == (None, None)
    proxy._tc.record_cloud_session_end.assert_called_once()
    proxy._process_frame_offline.assert_called_once()


@pytest.mark.asyncio
async def test_handle_box_connection_offline_path():
    proxy = _make_proxy()

    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._cf.handle_frame_offline_mode.assert_called_once()
    proxy._cf.forward_frame.assert_not_called()


@pytest.mark.asyncio
async def test_handle_box_connection_hybrid_no_cloud():
    proxy = _make_proxy()
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.HYBRID)
    proxy._hm.should_try_cloud = MagicMock(return_value=False)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._cf.handle_frame_offline_mode.assert_called_once()


@pytest.mark.asyncio
async def test_handle_box_connection_online_path():
    proxy = _make_proxy()
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._cf.forward_frame.assert_called_once()


@pytest.mark.asyncio
async def test_handle_box_connection_processing_exception():
    proxy = _make_proxy()
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    proxy._process_box_frame_common = AsyncMock(side_effect=RuntimeError("boom"))

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._cf.forward_frame.assert_not_called()


# =============================================================================
# RED tests for disconnect cleanup and pending timeout cancellation
# These tests are expected to FAIL, demonstrating the bug where disconnect
# does not properly clean up pending settings state.
# =============================================================================



def _make_proxy_with_real_control_settings():
    """Create proxy with real ControlSettings and ControlPipeline objects."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.OFFLINE
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)
    proxy.device_id = "DEV1"
    proxy._active_box_peer = "peer"
    proxy._active_box_writer = None
    proxy._box_conn_lock = asyncio.Lock()
    proxy._conn_seq = 0
    proxy._loop = None
    proxy._last_data_epoch = None
    proxy._tc = MagicMock()
    proxy._close_writer = AsyncMock()
    proxy._read_box_bytes = AsyncMock()
    proxy._process_box_frame_common = AsyncMock(return_value=("DEV1", "tbl"))
    proxy._cf = MagicMock()
    proxy._cf.handle_frame_offline_mode = AsyncMock(return_value=(None, None))
    proxy._cf.forward_frame = AsyncMock(return_value=(None, None))
    proxy._cf.session_connected = False
    proxy._cf.rx_buf = bytearray()
    proxy.stats = {"acks_local": 0}
    proxy.box_connected = False
    proxy.mqtt_publisher = MagicMock()
    proxy._msc = MagicMock()
    proxy._msc.last_values = {}
    proxy._mp = MagicMock()
    proxy._mp.prms_tables = {}

    # Real ControlSettings object
    proxy._cs = cs_module.ControlSettings.__new__(cs_module.ControlSettings)
    proxy._cs._proxy = proxy
    proxy._cs.pending = None
    proxy._cs.pending_frame = None
    proxy._cs.set_commands_buffer = []

    proxy._ctrl = _make_real_ctrl(proxy)
    return proxy


def _make_real_ctrl(proxy):
    """Create a minimal real ControlPipeline for timeout-tracking tests."""
    ctrl = ctrl_module.ControlPipeline.__new__(ctrl_module.ControlPipeline)
    ctrl._proxy = proxy
    ctrl.lock = asyncio.Lock()
    ctrl.inflight = None
    ctrl.queue = deque()
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.ack_timeout_s = 10.0
    ctrl.applied_timeout_s = 30.0
    ctrl.quiet_task = None
    ctrl.retry_task = None
    ctrl.max_attempts = 5
    ctrl.retry_delay_s = 120.0
    ctrl.last_result = None
    ctrl.key_state = {}
    ctrl.pending_keys = set()
    ctrl.post_drain_refresh_pending = False
    ctrl.mqtt_enabled = False
    ctrl.whitelist = {"tbl_box_prms": {"MODE"}}
    return ctrl


@pytest.mark.asyncio
async def test_disconnect_cancels_stale_pending_timeout():
    """
    RED TEST: Demonstrates that pending setting state is NOT cleaned up on disconnect.

    Scenario:
    1. Set up pending setting with active timeout on conn_id=1
    2. Simulate disconnect of conn_id=1
    3. Simulate reconnect as conn_id=2
    4. Assert: stale pending state from conn_id=1 is cleaned up

    Expected: FAIL - pending and pending_frame are NOT cleaned up on disconnect,
    allowing stale state to affect new connection.
    """
    proxy = _make_proxy_with_real_control_settings()

    # Simulate conn_id=1 connecting
    proxy._conn_seq = 1
    writer1 = DummyWriter()
    proxy._active_box_writer = writer1
    proxy.box_connected = True

    # Queue a pending setting on conn_id=1 (simulating send_to_box behavior)
    proxy._cs.pending = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
        "id": 12345,
        "id_set": 67890,
        "tx_id": "test-tx-1",
    }
    proxy._cs.pending_frame = b"<Setting>...</Setting>\r\n"

    # Simulate disconnect of conn_id=1
    proxy._active_box_writer = None
    proxy.box_connected = False

    # Call cleanup method that runs in handle_connection finally block
    proxy._cs.clear_pending_on_disconnect()

    # Simulate reconnect as conn_id=2
    proxy._conn_seq = 2
    writer2 = DummyWriter()
    proxy._active_box_writer = writer2
    proxy.box_connected = True

    # RED: This assertion should FAIL because pending state is NOT cleaned up
    # The stale pending from conn_id=1 should be cleared on disconnect
    assert proxy._cs.pending is None, (
        "FAIL: Stale pending dict from conn_id=1 still exists after disconnect. "
        "Expected: pending=None. "
        f"Actual: pending={proxy._cs.pending}"
    )

    # RED: This assertion should FAIL because pending_frame is NOT cleaned up
    assert proxy._cs.pending_frame is None, (
        "FAIL: Stale pending_frame from conn_id=1 still exists after disconnect. "
        "Expected: pending_frame=None. "
        f"Actual: pending_frame={proxy._cs.pending_frame}"
    )


@pytest.mark.asyncio
async def test_disconnect_cleanup_without_reconnect():
    """
    RED TEST: Demonstrates that disconnect without reconnect does NOT clean up pending state.

    Scenario:
    1. Set up pending setting on conn_id=1
    2. Simulate disconnect (no reconnect)
    3. Assert: pending state is cleaned up, no orphan state remains

    Expected: FAIL - pending and pending_frame are NOT cleaned up,
    leaving orphan state that could affect future connections.
    """
    proxy = _make_proxy_with_real_control_settings()

    # Simulate conn_id=1 connecting
    proxy._conn_seq = 1
    writer1 = DummyWriter()
    proxy._active_box_writer = writer1
    proxy.box_connected = True

    # Queue a pending setting on conn_id=1 (simulating send_to_box behavior)
    proxy._cs.pending = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "2",
        "id": 11111,
        "id_set": 22222,
        "tx_id": "test-tx-2",
    }
    proxy._cs.pending_frame = b"<Setting>...</Setting>\r\n"

    # Store reference to verify it's the same state
    original_pending = proxy._cs.pending
    original_frame = proxy._cs.pending_frame

    # Simulate disconnect (no reconnect)
    proxy._active_box_writer = None
    proxy.box_connected = False

    # Call cleanup method that runs in handle_connection finally block
    proxy._cs.clear_pending_on_disconnect()

    # RED: This assertion should FAIL because pending state is NOT cleaned up
    assert proxy._cs.pending is None, (
        "FAIL: Orphan pending dict exists after disconnect without reconnect. "
        "Expected: pending=None. "
        f"Actual: pending={proxy._cs.pending} (same object: {proxy._cs.pending is original_pending})"
    )

    # RED: This assertion should FAIL because pending_frame is NOT cleaned up
    assert proxy._cs.pending_frame is None, (
        "FAIL: Orphan pending_frame exists after disconnect without reconnect. "
        "Expected: pending_frame=None. "
        f"Actual: pending_frame exists (same object: {proxy._cs.pending_frame is original_frame})"
    )


@pytest.mark.asyncio
async def test_disconnect_cancels_inflight_timeout_tasks():
    """
    RED TEST: Demonstrates that timeout tasks are NOT properly cancelled on disconnect.

    Scenario:
    1. Set up inflight command with active ack_task timeout
    2. Simulate disconnect
    3. Assert: timeout task is cancelled

    Expected: FAIL - ack_task may not be cancelled on disconnect,
    allowing stale timeout callbacks to fire.
    """
    proxy = _make_proxy_with_real_control_settings()

    # Set up inflight command
    proxy._ctrl.inflight = {
        "tx_id": "test-tx-3",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "3",
        "stage": "sent_to_box",
        "_attempts": 1,
    }

    # Create an ack_task that would fire after disconnect
    async def _mock_ack_timeout():
        await asyncio.sleep(0.1)
        # This would modify state after disconnect - BAD!

    proxy._ctrl.ack_task = asyncio.create_task(_mock_ack_timeout())

    # Simulate disconnect and call cleanup
    proxy.box_connected = False
    await proxy._ctrl.note_box_disconnect()

    # Give event loop a chance to process
    await asyncio.sleep(0)

    # RED: This assertion should FAIL because ack_task is NOT cancelled
    assert proxy._ctrl.ack_task is None or proxy._ctrl.ack_task.done(), (
        "FAIL: ack_task still exists and is not done after disconnect. "
        "Expected: ack_task=None or ack_task.done()=True. "
        f"Actual: ack_task.done()={proxy._ctrl.ack_task.done() if proxy._ctrl.ack_task else 'N/A'}"
    )

    # Clean up
    if proxy._ctrl.ack_task and not proxy._ctrl.ack_task.done():
        proxy._ctrl.ack_task.cancel()
        try:
            await proxy._ctrl.ack_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_stale_pending_affects_new_connection():
    """
    RED TEST: Demonstrates that stale pending state can affect a new connection.

    Scenario:
    1. Queue pending setting on conn_id=1 with sent_at timestamp
    2. Disconnect conn_id=1
    3. Reconnect as conn_id=2
    4. When new poll arrives on conn_id=2, stale pending could be delivered

    Expected: FAIL - stale pending_frame is delivered to conn_id=2,
    causing cross-session state corruption.
    """
    proxy = _make_proxy_with_real_control_settings()

    # Simulate conn_id=1 with pending setting that was delivered (sent_at set)
    proxy._conn_seq = 1
    writer1 = DummyWriter()
    proxy._active_box_writer = writer1
    proxy.box_connected = True

    proxy._cs.pending = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "4",
        "id": 33333,
        "id_set": 44444,
        "tx_id": "test-tx-4",
        "sent_at": 1000.0,  # Simulate already delivered
    }
    proxy._cs.pending_frame = b"<Setting from conn1>...</Setting>\r\n"

    # Disconnect conn_id=1
    proxy._active_box_writer = None
    proxy.box_connected = False

    # Call cleanup method that runs in handle_connection finally block
    proxy._cs.clear_pending_on_disconnect()

    # Reconnect as conn_id=2
    proxy._conn_seq = 2
    writer2 = DummyWriter()
    proxy._active_box_writer = writer2
    proxy.box_connected = True

    # Simulate a new poll arriving on conn_id=2 (IsNewSet poll)
    # In _process_frame_offline, this would check pending_frame and deliver it

    # RED: pending_frame should be None, but it's not!
    # If _process_frame_offline is called, it would deliver the stale frame
    # to conn_id=2, which is wrong!

    assert proxy._cs.pending_frame is None, (
        "FAIL: Stale pending_frame from conn_id=1 can be delivered to conn_id=2. "
        "This causes cross-session state corruption. "
        f"pending_frame={proxy._cs.pending_frame}"
    )

    assert proxy._cs.pending is None, (
        "FAIL: Stale pending dict from conn_id=1 still tracks state for conn_id=2. "
        f"pending={proxy._cs.pending}"
    )
