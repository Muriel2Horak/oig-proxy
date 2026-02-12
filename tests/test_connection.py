"""Tests for connection handling and box communication."""

import asyncio

# pylint: disable=protected-access
import proxy as proxy_module
from models import ProxyMode


def make_proxy(tmp_path):
    """Create minimal proxy object for testing."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy._last_data_epoch = 0
    proxy.box_connected = True
    return proxy


def test_handle_connection_basic(tmp_path):
    """Test handle_connection basic flow."""
    from tests.fixtures.dummy import DummyReader, DummyWriter

    proxy = make_proxy(tmp_path)

    reader = DummyReader([b"<Frame></Frame>"])
    writer = DummyWriter()

    asyncio.run(proxy.handle_connection(reader, writer))

    assert proxy.box_connected is True


def test_handle_connection_empty_frame(tmp_path):
    """Test handle_connection with empty frame."""
    from tests.fixtures.dummy import DummyReader, DummyWriter

    proxy = make_proxy(tmp_path)

    reader = DummyReader([b""])
    writer = DummyWriter()

    asyncio.run(proxy.handle_connection(reader, writer))


def test_read_box_bytes_timeout(tmp_path):
    """Test _read_box_bytes timeout scenario."""
    proxy = make_proxy(tmp_path)
    proxy._last_data_epoch = 0

    from tests.fixtures.dummy import DummyReader

    reader = DummyReader([b"<Frame></Frame>"])

    result = asyncio.run(proxy._read_box_bytes(
        reader,
        conn_id=1,
        idle_timeout_s=0.01
    ))

    # Should handle gracefully with timeout
    assert result is not None
