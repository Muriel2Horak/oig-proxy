"""Tests for proxy cloud session helpers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


class DummyWriter:
    def __init__(self):
        self.closed = False
        self.buffer = []

    def is_closing(self):
        return self.closed

    def write(self, data):
        self.buffer.append(data)

    async def drain(self):
        return None


class DummyReader:
    def __init__(self, data=b""):
        self._data = data
        self._read_count = 0

    async def read(self, _size):
        if self._read_count > 0:
            return b""  # EOF after first read
        self._read_count += 1
        return self._data


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy.cloud_connects = 0
    proxy.cloud_errors = 0
    proxy.cloud_timeouts = 0
    proxy.cloud_disconnects = 0
    proxy.cloud_session_connected = False
    proxy._cloud_connected_since_epoch = None
    proxy._cloud_peer = None
    proxy._telemetry_cloud_failed_in_window = False
    proxy._telemetry_cloud_ok_in_window = False
    proxy._active_box_peer = "peer"
    proxy._record_cloud_session_end = MagicMock()
    proxy._close_writer = AsyncMock()
    proxy._telemetry_fire_event = MagicMock()
    proxy._telemetry_record_timeout = MagicMock()
    proxy._hybrid_record_failure = MagicMock()
    proxy._fallback_offline_from_cloud_issue = AsyncMock(return_value=(None, None))
    proxy._telemetry_record_response = MagicMock()
    proxy._hybrid_record_success = MagicMock()
    proxy._is_hybrid_mode = MagicMock(return_value=False)
    proxy._build_end_time_frame = MagicMock(return_value=b"<Result>END</Result>")
    proxy._cloud_rx_buf = bytearray()
    proxy.stats = {"frames_forwarded": 0, "acks_local": 0, "acks_cloud": 0}
    return proxy


@pytest.mark.asyncio
async def test_ensure_cloud_connected_skip_when_offline(monkeypatch):
    proxy = _make_proxy()
    proxy._should_try_cloud = MagicMock(return_value=False)
    proxy.cloud_session_connected = True

    reader, writer, attempted = await proxy._ensure_cloud_connected(
        None, DummyWriter(), conn_id=1, table_name="tbl", connect_timeout_s=1.0
    )

    assert (reader, writer, attempted) == (None, None, False)
    proxy._record_cloud_session_end.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_cloud_connected_reuse_writer(monkeypatch):
    proxy = _make_proxy()
    proxy._should_try_cloud = MagicMock(return_value=True)
    writer = DummyWriter()
    reader = DummyReader()

    out_reader, out_writer, attempted = await proxy._ensure_cloud_connected(
        reader, writer, conn_id=1, table_name=None, connect_timeout_s=1.0
    )

    assert out_reader is reader
    assert out_writer is writer
    assert attempted is False


@pytest.mark.asyncio
async def test_ensure_cloud_connected_success(monkeypatch):
    proxy = _make_proxy()
    proxy._should_try_cloud = MagicMock(return_value=True)

    dummy_reader = DummyReader()
    dummy_writer = DummyWriter()

    async def fake_open_connection(_host, _port):
        return dummy_reader, dummy_writer

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(proxy_module, "resolve_cloud_host", lambda _host: "host")
    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    reader, writer, attempted = await proxy._ensure_cloud_connected(
        None, None, conn_id=1, table_name="tbl", connect_timeout_s=1.0
    )

    assert attempted is True
    assert reader is dummy_reader
    assert writer is dummy_writer
    assert proxy.cloud_connects == 1
    assert proxy.cloud_session_connected is True


@pytest.mark.asyncio
async def test_ensure_cloud_connected_failure(monkeypatch):
    proxy = _make_proxy()
    proxy._should_try_cloud = MagicMock(return_value=True)

    async def fake_open_connection(_host, _port):
        raise RuntimeError("fail")

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(proxy_module, "resolve_cloud_host", lambda _host: "host")
    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    reader, writer, attempted = await proxy._ensure_cloud_connected(
        None, None, conn_id=1, table_name="tbl", connect_timeout_s=1.0
    )

    assert (reader, writer) == (None, None)
    assert attempted is True
    assert proxy.cloud_errors == 1


@pytest.mark.asyncio
async def test_handle_cloud_connection_failed_hybrid(monkeypatch):
    proxy = _make_proxy()
    proxy._is_hybrid_mode = MagicMock(return_value=True)

    await proxy._handle_cloud_connection_failed(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_writer=None,
        cloud_attempted=True,
    )

    proxy._fallback_offline_from_cloud_issue.assert_called_once()


@pytest.mark.asyncio
async def test_handle_cloud_connection_failed_non_hybrid():
    proxy = _make_proxy()
    proxy._is_hybrid_mode = MagicMock(return_value=False)

    result = await proxy._handle_cloud_connection_failed(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_writer=None,
        cloud_attempted=False,
    )

    assert result == (None, None)
    proxy._telemetry_record_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_handle_cloud_eof_hybrid():
    proxy = _make_proxy()
    proxy._is_hybrid_mode = MagicMock(return_value=True)

    await proxy._handle_cloud_eof(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_writer=DummyWriter(),
    )

    assert proxy.cloud_disconnects == 1
    proxy._fallback_offline_from_cloud_issue.assert_called_once()


@pytest.mark.asyncio
async def test_handle_cloud_timeout_hybrid_end(monkeypatch):
    proxy = _make_proxy()
    proxy._is_hybrid_mode = MagicMock(return_value=True)
    box_writer = DummyWriter()

    reader, writer = await proxy._handle_cloud_timeout(
        conn_id=1,
        table_name="END",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=box_writer,
        cloud_reader=DummyReader(),
        cloud_writer=DummyWriter(),
    )

    assert reader is not None
    assert writer is not None
    assert proxy.stats["acks_local"] == 1


@pytest.mark.asyncio
async def test_handle_cloud_timeout_non_hybrid(monkeypatch):
    proxy = _make_proxy()
    proxy._is_hybrid_mode = MagicMock(return_value=False)
    proxy.cloud_session_connected = True

    result = await proxy._handle_cloud_timeout(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_reader=DummyReader(),
        cloud_writer=DummyWriter(),
    )

    assert result == (None, None)
    proxy._record_cloud_session_end.assert_called_once()
    proxy._telemetry_record_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_send_frame_to_cloud_ok(monkeypatch):
    proxy = _make_proxy()
    ack_frame = b"<Frame>ACK</Frame>\r\n"
    reader = DummyReader(data=ack_frame)
    writer = DummyWriter()

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    ack_data, _ = await proxy._send_frame_to_cloud(
        frame_bytes=b"x",
        cloud_writer=writer,
        cloud_reader=reader,
        table_name="tbl",
        conn_id=1,
    )

    assert ack_data == ack_frame
    assert proxy.stats["frames_forwarded"] == 1
    assert proxy._telemetry_cloud_ok_in_window is True


@pytest.mark.asyncio
async def test_send_frame_to_cloud_eof(monkeypatch):
    proxy = _make_proxy()
    reader = DummyReader(data=b"")
    writer = DummyWriter()

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(EOFError):
        await proxy._send_frame_to_cloud(
            frame_bytes=b"x",
            cloud_writer=writer,
            cloud_reader=reader,
            table_name="tbl",
            conn_id=1,
        )
