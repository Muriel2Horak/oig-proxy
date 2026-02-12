"""Tests for main box connection loop and offline handling."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
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
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._active_box_peer = "peer"
    proxy._telemetry_force_logs_this_window = False
    proxy.cloud_session_connected = False
    proxy._record_cloud_session_end = MagicMock()
    proxy._close_writer = AsyncMock()
    proxy._read_box_bytes = AsyncMock()
    proxy._process_box_frame_common = AsyncMock(return_value=("DEV1", "tbl"))
    proxy._maybe_handle_local_setting_ack = MagicMock(return_value=False)
    proxy._get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)
    proxy._handle_frame_offline_mode = AsyncMock(return_value=(None, None))
    proxy._forward_frame_online = AsyncMock(return_value=(None, None))
    proxy._cloud_rx_buf = bytearray()
    return proxy


@pytest.mark.asyncio
async def test_process_frame_offline_sends_ack():
    proxy = _make_proxy()
    proxy.stats = {"acks_local": 0}
    proxy._build_offline_ack_frame = MagicMock(return_value=b"ACK")
    proxy._telemetry_record_response = MagicMock()
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
    proxy.cloud_session_connected = True
    proxy._handle_frame_offline_mode = proxy_module.OIGProxy._handle_frame_offline_mode

    reader, writer = await proxy._handle_frame_offline_mode(
        proxy,
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        conn_id=1,
        box_writer=DummyWriter(),
        cloud_writer=DummyWriter(),
    )

    assert (reader, writer) == (None, None)
    proxy._close_writer.assert_called_once()
    proxy._record_cloud_session_end.assert_called_once()
    proxy._process_frame_offline.assert_called_once()


@pytest.mark.asyncio
async def test_handle_box_connection_offline_path():
    proxy = _make_proxy()

    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    proxy._get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._handle_frame_offline_mode.assert_called_once()
    proxy._forward_frame_online.assert_not_called()


@pytest.mark.asyncio
async def test_handle_box_connection_hybrid_no_cloud():
    proxy = _make_proxy()
    proxy._get_current_mode = AsyncMock(return_value=ProxyMode.HYBRID)
    proxy._should_try_cloud = MagicMock(return_value=False)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._handle_frame_offline_mode.assert_called_once()


@pytest.mark.asyncio
async def test_handle_box_connection_online_path():
    proxy = _make_proxy()
    proxy._get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._forward_frame_online.assert_called_once()


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

    proxy._forward_frame_online.assert_not_called()
