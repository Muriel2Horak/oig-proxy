"""Tests for box session helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


class DummyReader:
    def __init__(self, data=b""):
        self._data = data

    async def read(self, _size):
        return self._data


class DummyWriter:
    def __init__(self):
        self.closed = False
        self.buffer = []

    def write(self, data):
        self.buffer.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._configured_mode = "online"
    proxy.stats = {"frames_received": 0, "frames_forwarded": 0, "acks_local": 0}
    proxy._active_box_peer = "peer"
    proxy._last_box_disconnect_reason = None
    proxy.publish_proxy_status = AsyncMock()
    return proxy


@pytest.mark.asyncio
async def test_read_box_bytes_timeout(monkeypatch):
    proxy = _make_proxy()

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    data = await proxy._read_box_bytes(DummyReader(), conn_id=1, idle_timeout_s=1.0)
    assert data is None
    assert proxy._last_box_disconnect_reason == "timeout"


@pytest.mark.asyncio
async def test_read_box_bytes_eof(monkeypatch):
    proxy = _make_proxy()

    async def fake_wait_for(coro, timeout):
        coro.close()
        return b""

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    data = await proxy._read_box_bytes(DummyReader(), conn_id=1, idle_timeout_s=1.0)
    assert data is None
    assert proxy._last_box_disconnect_reason == "eof"
    proxy.publish_proxy_status.assert_called_once()


@pytest.mark.asyncio
async def test_read_box_bytes_reset(monkeypatch):
    proxy = _make_proxy()

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise ConnectionResetError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    data = await proxy._read_box_bytes(DummyReader(), conn_id=1, idle_timeout_s=1.0)
    assert data is None
    assert proxy._last_box_disconnect_reason == "reset"


@pytest.mark.asyncio
async def test_read_box_bytes_ok(monkeypatch):
    proxy = _make_proxy()

    async def fake_wait_for(coro, timeout):
        coro.close()
        return b"<Frame>1</Frame>"

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    data = await proxy._read_box_bytes(DummyReader(), conn_id=1, idle_timeout_s=1.0)
    assert data == b"<Frame>1</Frame>"


def test_extract_device_and_table_isnew():
    proxy = _make_proxy()
    parsed = {"Result": "IsNewSet"}
    device_id, table_name = proxy._extract_device_and_table(parsed)
    assert device_id is None
    assert table_name == "IsNewSet"
    assert parsed["_table"] == "IsNewSet"


@pytest.mark.asyncio
async def test_fallback_offline_from_cloud_issue():
    proxy = _make_proxy()
    proxy.cloud_session_connected = True
    proxy._record_cloud_session_end = MagicMock()
    proxy._close_writer = AsyncMock()
    proxy._process_frame_offline = AsyncMock()

    await proxy._fallback_offline_from_cloud_issue(
        reason="cloud_error",
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_writer=DummyWriter(),
        note_cloud_failure=True,
        send_box_ack=True,
        conn_id=1,
    )

    proxy._record_cloud_session_end.assert_called_once()
    proxy._close_writer.assert_called_once()
    proxy._process_frame_offline.assert_called_once()
