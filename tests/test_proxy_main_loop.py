"""Tests for main box connection loop and offline routing."""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import AsyncMock, MagicMock

import pytest

import control_pipeline as ctrl_module
import control_settings as cs_module
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
async def test_respond_local_offline_sends_ack(monkeypatch):
    proxy = _make_proxy()
    monkeypatch.setattr(proxy_module, "build_offline_ack_frame", lambda _tbl: b"ACK")
    writer = DummyWriter()

    await proxy._respond_local_offline(
        b"x",
        "tbl",
        "DEV1",
        writer,
        send_ack=True,
        conn_id=1,
    )

    assert writer.buffer == [b"ACK"]
    assert proxy.stats["acks_local"] == 1


@pytest.mark.asyncio
async def test_handle_frame_local_offline_closes_cloud(monkeypatch):
    proxy = _make_proxy()
    proxy._cf.session_connected = True
    cloud_writer = DummyWriter()
    box_writer = DummyWriter()
    monkeypatch.setattr(proxy_module, "build_offline_ack_frame", lambda _tbl: b"ACK")

    reader, writer = await proxy_module.OIGProxy._handle_frame_local_offline(
        proxy,
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        conn_id=1,
        box_writer=box_writer,
        cloud_writer=cloud_writer,
    )

    assert (reader, writer) == (None, None)
    proxy._close_writer.assert_awaited_once()
    assert box_writer.buffer == [b"ACK"]


@pytest.mark.asyncio
async def test_handle_box_connection_offline_path_uses_local_handler():
    proxy = _make_proxy()
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._handle_frame_local_offline.assert_called_once()
    proxy._cf.forward_frame.assert_not_called()


@pytest.mark.asyncio
async def test_handle_box_connection_hybrid_no_cloud_uses_local_handler():
    proxy = _make_proxy()
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.HYBRID)
    proxy._hm.should_try_cloud = MagicMock(return_value=False)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._handle_frame_local_offline.assert_called_once()
    proxy._cf.forward_frame.assert_not_called()


@pytest.mark.asyncio
async def test_handle_box_connection_online_path_uses_cloud_forwarder():
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
async def test_handle_box_connection_processing_error_is_guarded():
    proxy = _make_proxy()
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    # _process_box_frame_with_guard catches ValueError and continues safely.
    proxy._process_box_frame_common = AsyncMock(side_effect=ValueError("boom"))

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._cf.forward_frame.assert_not_called()


@pytest.mark.asyncio
async def test_control_pipeline_note_box_disconnect_is_noop():
    ctrl = ctrl_module.ControlPipeline.__new__(ctrl_module.ControlPipeline)
    ctrl.inflight = {"tx_id": "tx-1"}

    await ctrl.note_box_disconnect()

    assert ctrl.inflight == {"tx_id": "tx-1"}


@pytest.mark.asyncio
async def test_control_settings_queue_setting_requires_twin():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy._twin = None
    cs = cs_module.ControlSettings(proxy)

    result = await cs.queue_setting(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="1",
        confirm="New",
    )

    assert result == {"ok": False, "error": "twin_unavailable"}
