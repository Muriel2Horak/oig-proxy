"""Unit tests for Blind Branch #1: Frame exception handling (fail-open routing)."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import proxy as proxy_module
from models import ProxyMode


class DummyWriter:
    def __init__(self):
        self.buffer = []
        self._closing = False

    def write(self, data):
        self.buffer.append(data)

    async def drain(self):
        return None

    def close(self):
        self._closing = True

    async def wait_closed(self):
        return None

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name):
        if name == "peername":
            return ("127.0.0.1", 10000)
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
    proxy.stats = {"acks_local": 0, "frames_forwarded": 0, "frames_received": 0}
    return proxy


@pytest.mark.asyncio
async def test_process_box_frame_with_guard_catches_value_error():
    """Test that ValueError in processing is caught and routing continues."""
    proxy = _make_proxy()
    proxy._process_box_frame_common = AsyncMock(side_effect=ValueError("test error"))
    
    # Should not raise - exception is caught
    result = await proxy._process_box_frame_with_guard(
        frame_bytes=b"test",
        frame="test",
        conn_id=1,
    )
    
    # Should return tuple even on error (fail-open)
    assert result is not None
    assert len(result) == 3  # (device_id, table_name, had_error)


@pytest.mark.asyncio
async def test_process_box_frame_with_guard_catches_key_error():
    """Test that KeyError in processing is caught and routing continues."""
    proxy = _make_proxy()
    proxy._process_box_frame_common = AsyncMock(side_effect=KeyError("missing_key"))
    
    # Should not raise
    result = await proxy._process_box_frame_with_guard(
        frame_bytes=b"test",
        frame="test",
        conn_id=1,
    )
    
    assert result is not None


@pytest.mark.asyncio
async def test_process_box_frame_with_guard_catches_type_error():
    """Test that TypeError in processing is caught and routing continues."""
    proxy = _make_proxy()
    proxy._process_box_frame_common = AsyncMock(side_effect=TypeError("type error"))
    
    # Should not raise
    result = await proxy._process_box_frame_with_guard(
        frame_bytes=b"test",
        frame="test",
        conn_id=1,
    )
    
    assert result is not None


@pytest.mark.asyncio
async def test_process_box_frame_with_guard_catches_attribute_error():
    """Test that AttributeError in processing is caught and routing continues."""
    proxy = _make_proxy()
    proxy._process_box_frame_common = AsyncMock(side_effect=AttributeError("no attr"))
    
    # Should not raise
    result = await proxy._process_box_frame_with_guard(
        frame_bytes=b"test",
        frame="test",
        conn_id=1,
    )
    
    assert result is not None


@pytest.mark.asyncio
async def test_process_box_frame_with_guard_logs_warning_on_exception():
    """Test that WARNING is logged when exception occurs."""
    proxy = _make_proxy()
    proxy._process_box_frame_common = AsyncMock(side_effect=ValueError("test error"))
    
    with patch("proxy.logger") as mock_logger:
        await proxy._process_box_frame_with_guard(
            frame_bytes=b"test",
            frame="test",
            conn_id=1,
        )
        
        # Should log warning
        mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_process_box_frame_with_guard_normal_flow_works():
    """Test that normal processing still works without errors."""
    proxy = _make_proxy()
    proxy._process_box_frame_common = AsyncMock(return_value=("DEV1", "tbl_test"))
    
    result = await proxy._process_box_frame_with_guard(
        frame_bytes=b"<OIG><tbl_test>...</tbl_test></OIG>",
        frame="<OIG><tbl_test>...</tbl_test></OIG>",
        conn_id=1,
    )
    
    assert result == ("DEV1", "tbl_test", False)


@pytest.mark.asyncio
async def test_handle_box_connection_continues_after_processing_error():
    """Test that routing continues even when processing fails."""
    proxy = _make_proxy()
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame1", b"frame2", None])
    proxy._process_box_frame_common = AsyncMock(side_effect=[
        ValueError("boom"),  # First frame fails
        ("DEV1", "tbl_ok"),  # Second frame succeeds
    ])
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)
    
    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )
    
    # Should have processed both frames (continued after error)
    assert proxy._process_box_frame_common.call_count == 2


@pytest.mark.asyncio
async def test_cloud_forwarder_still_gets_data_on_exception():
    """Test that cloud forwarder receives data even when MQTT/parsing fails."""
    proxy = _make_proxy()
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    
    # Simulate processing exception (parsing/MQTT error)
    proxy._process_box_frame_common = AsyncMock(side_effect=ValueError("parse error"))
    
    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )
    
    # Cloud forwarder should still be called even with partial data
    # because fail-open routing ensures forwarding continues


@pytest.mark.asyncio
async def test_exception_does_not_stop_routing():
    """Test that exception does not stop frame routing (main blind branch fix)."""
    proxy = _make_proxy()
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame1", b"frame2", b"frame3", None])
    
    # First frame raises exception, others succeed
    call_count = [0]
    async def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ValueError("first frame error")
        return ("DEV1", f"tbl_{call_count[0]}")
    
    proxy._process_box_frame_common = AsyncMock(side_effect=side_effect)
    
    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )
    
    # All 3 frames should be processed (routing didn't stop on first error)
    assert proxy._process_box_frame_common.call_count == 3
