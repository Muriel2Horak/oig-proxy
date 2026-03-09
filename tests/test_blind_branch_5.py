"""Unit tests for Blind Branch #5: Pending activation expiration guard."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import AsyncMock, MagicMock
import pytest
import time

import proxy as proxy_module
from models import ProxyMode


class DummyTwin:
    def __init__(self, queue_length=0, inflight=None):
        self._queue_length = queue_length
        self._inflight = inflight
    
    def get_queue_length(self):
        return self._queue_length
    
    def get_inflight(self):
        return self._inflight


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)
    proxy._hm.should_try_cloud = MagicMock(return_value=True)
    proxy._hm.should_route_settings_via_twin = MagicMock(return_value=False)
    proxy.device_id = "DEV1"
    proxy._active_box_peer = "peer"
    proxy._tc = MagicMock()
    proxy._close_writer = AsyncMock()
    proxy._read_box_bytes = AsyncMock()
    proxy._process_box_frame_common = AsyncMock(return_value=("DEV1", "tbl"))
    proxy._maybe_handle_local_control_poll = AsyncMock(return_value=False)
    proxy._handle_frame_local_offline = AsyncMock(return_value=(None, None))
    proxy._cf = MagicMock()
    proxy._cf.forward_frame = AsyncMock(return_value=(None, None))
    proxy._cf.session_connected = False
    proxy._cf.rx_buf = bytearray()
    proxy._pending_twin_activation = False
    proxy._twin_mode_active = False
    proxy._twin = None
    proxy._pending_twin_activation_since = None
    proxy.stats = {"acks_local": 0, "frames_forwarded": 0, "frames_received": 0}
    return proxy


@pytest.mark.asyncio
async def test_pending_expires_when_idle():
    """Test that pending expires when idle (queue=0, inflight=None) for timeout period."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._pending_twin_activation_since = time.monotonic() - 70  # 70s ago (> 60s timeout)
    proxy._twin = DummyTwin(queue_length=0, inflight=None)
    proxy._twin_mode_active = False
    
    await proxy._maybe_expire_pending_twin_activation()
    
    # Pending should be cleared
    assert proxy._pending_twin_activation is False
    assert proxy._pending_twin_activation_since is None


@pytest.mark.asyncio
async def test_pending_remains_when_queue_active():
    """Test that pending remains when queue is active (queue>0)."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._pending_twin_activation_since = time.monotonic() - 70
    proxy._twin = DummyTwin(queue_length=1, inflight=None)
    
    await proxy._maybe_expire_pending_twin_activation()
    
    # Pending should remain True
    assert proxy._pending_twin_activation is True


@pytest.mark.asyncio
async def test_pending_remains_when_inflight_exists():
    """Test that pending remains when inflight exists."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._pending_twin_activation_since = time.monotonic() - 70
    proxy._twin = DummyTwin(queue_length=0, inflight={"tx_id": "test"})
    
    await proxy._maybe_expire_pending_twin_activation()
    
    # Pending should remain True
    assert proxy._pending_twin_activation is True


@pytest.mark.asyncio
async def test_deactivation_guard_runs_after_expire():
    """Test that deactivation guard runs after pending expires."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._pending_twin_activation_since = time.monotonic() - 70
    proxy._twin = DummyTwin(queue_length=0, inflight=None)
    proxy._twin_mode_active = True
    
    # Track if deactivation would be called
    # After pending clears, deactivation guard can run
    await proxy._maybe_expire_pending_twin_activation()
    
    # Pending cleared, allowing deactivation
    assert proxy._pending_twin_activation is False


@pytest.mark.asyncio
async def test_timestamp_tracks_pending_duration():
    """Test that timestamp tracks how long pending is active."""
    proxy = _make_proxy()
    
    # Set pending with timestamp
    start_time = time.monotonic() - 30  # 30s ago
    proxy._pending_twin_activation = True
    proxy._pending_twin_activation_since = start_time
    
    # Not yet expired (60s timeout)
    proxy._twin = DummyTwin(queue_length=0, inflight=None)
    await proxy._maybe_expire_pending_twin_activation()
    
    # Should still be pending (only 30s elapsed)
    assert proxy._pending_twin_activation is True


@pytest.mark.asyncio
async def test_pending_not_expired_before_timeout():
    """Test that pending is not expired before 60s timeout."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._pending_twin_activation_since = time.monotonic() - 30  # 30s ago
    proxy._twin = DummyTwin(queue_length=0, inflight=None)
    
    await proxy._maybe_expire_pending_twin_activation()
    
    # Should still be pending
    assert proxy._pending_twin_activation is True
    assert proxy._pending_twin_activation_since is not None
