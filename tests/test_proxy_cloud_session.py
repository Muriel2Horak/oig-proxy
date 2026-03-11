"""Tests for cloud session helpers (now on CloudForwarder)."""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import cloud_forwarder as cf_module
from cloud_forwarder import CloudForwarder
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

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None

    def get_extra_info(self, name, default=None):
        if name == "peername":
            return ("127.0.0.1", 5710)
        if name == "socket":
            return None
        return default


class DummyReader:
    def __init__(self, data=b""):
        self._data = data
        self._read_count = 0

    async def read(self, _size):
        if self._read_count > 0:
            return b""  # EOF after first read
        self._read_count += 1
        return self._data


def _make_proxy_and_cf():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy._tc = MagicMock()
    proxy._active_box_peer = "peer"
    proxy._close_writer = AsyncMock()
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._hm.is_hybrid_mode = MagicMock(return_value=False)
    proxy._hm.should_try_cloud = MagicMock(return_value=True)
    proxy._hm.record_failure = MagicMock()
    proxy._hm.record_success = MagicMock()
    proxy._hm.force_offline_enabled = MagicMock(return_value=False)
    proxy._process_frame_offline = AsyncMock()
    proxy._respond_local_offline = AsyncMock()
    proxy._record_proxy_to_box_frame = MagicMock()
    proxy.stats = {"frames_forwarded": 0, "acks_local": 0, "acks_cloud": 0}
    proxy._sidecar_adapter = None

    cf = CloudForwarder.__new__(CloudForwarder)
    cf._proxy = proxy
    cf.connects = 0
    cf.disconnects = 0
    cf.timeouts = 0
    cf.errors = 0
    cf.session_connected = False
    cf.connected_since_epoch = None
    cf.peer = None
    cf.rx_buf = bytearray()
    proxy._cf = cf
    return proxy, cf


class SidecarAdapterStub:
    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.fail_count = 0
        self.activations = 0

    def record_failure(self, *, reason=None):
        self.fail_count += 1

    async def check_and_activate(self):
        if self.fail_count >= self.threshold and self.activations == 0:
            self.activations += 1
            return True
        return False

    def record_success(self):
        self.fail_count = 0


@pytest.mark.asyncio
async def test_ensure_cloud_connected_skip_when_offline(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.should_try_cloud = MagicMock(return_value=False)
    cf.session_connected = True

    reader, writer, attempted = await cf.ensure_connected(
        None, DummyWriter(), conn_id=1, table_name="tbl", connect_timeout_s=1.0
    )

    assert (reader, writer, attempted) == (None, None, False)
    proxy._tc.record_cloud_session_end.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_cloud_connected_reuse_writer(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.should_try_cloud = MagicMock(return_value=True)
    writer = DummyWriter()
    reader = DummyReader()

    out_reader, out_writer, attempted = await cf.ensure_connected(
        reader, writer, conn_id=1, table_name=None, connect_timeout_s=1.0
    )

    assert out_reader is reader
    assert out_writer is writer
    assert attempted is False


@pytest.mark.asyncio
async def test_ensure_cloud_connected_success(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.should_try_cloud = MagicMock(return_value=True)

    dummy_reader = DummyReader()
    dummy_writer = DummyWriter()

    async def fake_open_connection(_host, _port):
        return dummy_reader, dummy_writer

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(cf_module, "resolve_cloud_host", lambda _host: "host")
    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    reader, writer, attempted = await cf.ensure_connected(
        None, None, conn_id=1, table_name="tbl", connect_timeout_s=1.0
    )

    assert attempted is True
    assert reader is dummy_reader
    assert writer is dummy_writer
    assert cf.connects == 1
    assert cf.session_connected is True


@pytest.mark.asyncio
async def test_ensure_cloud_connected_failure(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.should_try_cloud = MagicMock(return_value=True)

    async def fake_open_connection(_host, _port):
        raise RuntimeError("fail")

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(cf_module, "resolve_cloud_host", lambda _host: "host")
    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    reader, writer, attempted = await cf.ensure_connected(
        None, None, conn_id=1, table_name="tbl", connect_timeout_s=1.0
    )

    assert (reader, writer) == (None, None)
    assert attempted is True
    assert cf.errors == 1


@pytest.mark.asyncio
async def test_handle_cloud_connection_failed_hybrid(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.is_hybrid_mode = MagicMock(return_value=True)
    monkeypatch.setattr(cf_module, "LEGACY_FALLBACK", True)

    await cf.handle_connection_failed(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_writer=None,
        cloud_attempted=True,
    )

    proxy._respond_local_offline.assert_called_once()


@pytest.mark.asyncio
async def test_handle_cloud_connection_failed_non_hybrid():
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.is_hybrid_mode = MagicMock(return_value=False)

    result = await cf.handle_connection_failed(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_writer=None,
        cloud_attempted=False,
    )

    assert result == (None, None)
    proxy._tc.record_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_handle_cloud_eof_hybrid(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.is_hybrid_mode = MagicMock(return_value=True)
    monkeypatch.setattr(cf_module, "LEGACY_FALLBACK", True)

    await cf.handle_eof(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_writer=DummyWriter(),
    )

    assert cf.disconnects == 1
    proxy._respond_local_offline.assert_called_once()


@pytest.mark.asyncio
async def test_handle_cloud_timeout_hybrid_end(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.is_hybrid_mode = MagicMock(return_value=True)
    box_writer = DummyWriter()

    reader, writer = await cf.handle_timeout(
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
    proxy._record_proxy_to_box_frame.assert_called_once()


@pytest.mark.asyncio
async def test_handle_cloud_timeout_non_hybrid(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    proxy._hm.is_hybrid_mode = MagicMock(return_value=False)
    cf.session_connected = True

    result = await cf.handle_timeout(
        conn_id=1,
        table_name="tbl",
        frame_bytes=b"x",
        device_id="DEV1",
        box_writer=DummyWriter(),
        cloud_reader=DummyReader(),
        cloud_writer=DummyWriter(),
    )

    assert result == (None, None)
    proxy._tc.record_cloud_session_end.assert_called_once()
    proxy._tc.record_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_send_frame_to_cloud_ok(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    ack_frame = b"<Frame>ACK</Frame>\r\n"
    reader = DummyReader(data=ack_frame)
    writer = DummyWriter()

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    ack_data, _ = await cf.send_frame(
        frame_bytes=b"x",
        cloud_writer=writer,
        cloud_reader=reader,
        table_name="tbl",
        conn_id=1,
    )

    assert ack_data == ack_frame
    assert proxy.stats["frames_forwarded"] == 1
    assert proxy._tc.cloud_ok_in_window is True


@pytest.mark.asyncio
async def test_send_frame_end_does_not_wait_for_ack(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    reader = DummyReader(data=b"<Frame>ACK</Frame>\r\n")
    writer = DummyWriter()

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    ack_data, ack_required = await cf.send_frame(
        frame_bytes=b"<Frame>END</Frame>",
        cloud_writer=writer,
        cloud_reader=reader,
        table_name="END",
        conn_id=1,
    )

    assert ack_data is None
    assert ack_required is False
    assert reader._read_count == 0
    assert proxy.stats["frames_forwarded"] == 1
    proxy._tc.record_end_frame.assert_called_once_with(sent=True)


@pytest.mark.asyncio
async def test_send_frame_to_cloud_eof(monkeypatch):
    proxy, cf = _make_proxy_and_cf()
    reader = DummyReader(data=b"")
    writer = DummyWriter()

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(EOFError):
        await cf.send_frame(
            frame_bytes=b"x",
            cloud_writer=writer,
            cloud_reader=reader,
            table_name="tbl",
            conn_id=1,
        )


@pytest.mark.asyncio
async def test_sidecar_activation_triggers_on_third_consecutive_fail_event():
    proxy, cf = _make_proxy_and_cf()
    proxy._sidecar_adapter = SidecarAdapterStub(threshold=3)

    await cf.note_failure(reason="connect_failed", local_ack=False)
    assert proxy._sidecar_adapter.activations == 0
    await cf.note_failure(reason="ack_timeout", local_ack=False)
    assert proxy._sidecar_adapter.activations == 0
    await cf.note_failure(reason="connect_failed", local_ack=False)
    assert proxy._sidecar_adapter.activations == 1


@pytest.mark.asyncio
async def test_sidecar_failure_counter_resets_on_success():
    proxy, cf = _make_proxy_and_cf()
    proxy._sidecar_adapter = SidecarAdapterStub(threshold=3)

    await cf.note_failure(reason="connect_failed", local_ack=False)
    await cf.note_failure(reason="ack_timeout", local_ack=False)
    assert proxy._sidecar_adapter.fail_count == 2
    cf.note_success()
    assert proxy._sidecar_adapter.fail_count == 0
    await cf.note_failure(reason="connect_failed", local_ack=False)
    assert proxy._sidecar_adapter.activations == 0
