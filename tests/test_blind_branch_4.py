"""Unit tests for Blind Branch #4: Mid-session twin activation."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import AsyncMock, MagicMock
import pytest

import proxy as proxy_module
from models import ProxyMode


class DummyTwin:
    def __init__(self, queue_length=0):
        self._queue_length = queue_length

    async def get_queue_length(self):
        return self._queue_length

    async def get_inflight(self):
        return None


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
async def test_activation_called_during_session_when_pending_and_queue():
    """Test that activation is called during session when pending=True and queue>0."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._twin = DummyTwin(queue_length=1)
    proxy._twin_mode_active = False
    
    # Call activation check
    await proxy._activate_session_twin_mode_if_needed(conn_id=1)
    
    # Should activate twin mode
    assert proxy._twin_mode_active is True
    assert proxy._pending_twin_activation is False


@pytest.mark.asyncio
async def test_twin_mode_active_set_after_activation():
    """Test that _twin_mode_active=True after activation."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._twin = DummyTwin(queue_length=1)
    proxy._twin_mode_active = False
    
    await proxy._activate_session_twin_mode_if_needed(conn_id=1)
    
    assert proxy._twin_mode_active is True


@pytest.mark.asyncio
async def test_activation_not_called_when_queue_empty():
    """Test that activation is NOT called when queue is empty."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._twin = DummyTwin(queue_length=0)
    proxy._twin_mode_active = False
    
    await proxy._activate_session_twin_mode_if_needed(conn_id=1)
    
    # Should NOT activate
    assert proxy._twin_mode_active is False
    assert proxy._pending_twin_activation is True


@pytest.mark.asyncio
async def test_activation_not_called_when_pending_false():
    """Test that activation is NOT called when pending=False."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = False
    proxy._twin = DummyTwin(queue_length=1)
    proxy._twin_mode_active = False
    
    await proxy._activate_session_twin_mode_if_needed(conn_id=1)
    
    # Should NOT activate
    assert proxy._twin_mode_active is False


@pytest.mark.asyncio
async def test_activation_works_during_active_session():
    """Test that activation works even during an active session."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._twin = DummyTwin(queue_length=1)
    proxy._twin_mode_active = False
    
    # Simulate active session
    await proxy._activate_session_twin_mode_if_needed(conn_id=1)
    
    # Should still activate
    assert proxy._twin_mode_active is True


@pytest.mark.asyncio
async def test_pending_cleared_after_activation():
    """Test that pending flag is cleared after activation."""
    proxy = _make_proxy()
    proxy._pending_twin_activation = True
    proxy._twin = DummyTwin(queue_length=1)
    proxy._pending_twin_activation_since = 12345.0
    
    await proxy._activate_session_twin_mode_if_needed(conn_id=1)
    
    assert proxy._pending_twin_activation is False
    assert proxy._pending_twin_activation_since is None
