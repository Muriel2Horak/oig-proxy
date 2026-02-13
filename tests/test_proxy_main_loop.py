"""Tests for main box connection loop and offline handling."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
import cloud_forwarder as cf_module
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
    proxy._maybe_handle_local_setting_ack = MagicMock(return_value=False)
    proxy._cf = MagicMock()
    proxy._cf.handle_frame_offline_mode = AsyncMock(return_value=(None, None))
    proxy._cf.forward_frame = AsyncMock(return_value=(None, None))
    proxy._cf.session_connected = False
    proxy._cf.rx_buf = bytearray()
    return proxy


@pytest.mark.asyncio
async def test_process_frame_offline_sends_ack():
    proxy = _make_proxy()
    proxy.stats = {"acks_local": 0}
    proxy._build_offline_ack_frame = MagicMock(return_value=b"ACK")
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
