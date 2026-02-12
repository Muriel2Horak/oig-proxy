"""Tests for control marker and setting event handlers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._control_lock = asyncio.Lock()
    proxy._control_inflight = None
    proxy._control_publish_result = AsyncMock()
    proxy._control_finish_inflight = AsyncMock()
    proxy._control_quiet_task = None
    return proxy


@pytest.mark.asyncio
async def test_get_valid_tx_with_lock():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    assert await proxy._get_valid_tx_with_lock("1") == {"tx_id": "1"}
    assert await proxy._get_valid_tx_with_lock("2") is None


@pytest.mark.asyncio
async def test_handle_marker_frames_completed():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1", "stage": "box_ack"}

    await proxy._handle_marker_frames({"tx_id": "1"}, "END")
    proxy._control_publish_result.assert_called_once()
    proxy._control_finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_handle_marker_frames_no_tx_id():
    proxy = _make_proxy()
    await proxy._handle_marker_frames({}, "END")
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_handle_setting_event_control_applied(monkeypatch):
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }

    proxy._parse_setting_event = MagicMock(return_value=("tbl_box_prms", "SA", "0", "1"))

    await proxy._handle_setting_event_control(proxy._control_inflight, "event")
    proxy._control_publish_result.assert_called()
    proxy._control_finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_handle_setting_event_control_guard_paths():
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._parse_setting_event = MagicMock(return_value=None)

    await proxy._handle_setting_event_control(proxy._control_inflight, "event")
    proxy._control_publish_result.assert_not_called()

    proxy._parse_setting_event = MagicMock(return_value=("tbl_other", "SA", "0", "1"))
    await proxy._handle_setting_event_control(proxy._control_inflight, "event")
    proxy._control_publish_result.assert_not_called()

    proxy._parse_setting_event = MagicMock(return_value=("tbl_box_prms", "SA", "0", "2"))
    await proxy._handle_setting_event_control(proxy._control_inflight, "event")
    proxy._control_publish_result.assert_not_called()

    proxy._control_inflight = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": "1"}
    proxy._parse_setting_event = MagicMock(return_value=("tbl_box_prms", "SA", "0", "1"))
    await proxy._handle_setting_event_control(proxy._control_inflight, "event")
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_handle_setting_event_control_mode_sets_quiet(monkeypatch):
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
    }

    proxy._parse_setting_event = MagicMock(return_value=("tbl_box_prms", "MODE", "0", "1"))
    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._handle_setting_event_control(proxy._control_inflight, "event")
    proxy._control_publish_result.assert_called()
    assert proxy._control_quiet_task is dummy_task


@pytest.mark.asyncio
async def test_handle_invertor_ack_updates_timestamp(monkeypatch):
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "stage": "applied",
    }

    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._handle_invertor_ack(proxy._control_inflight, "Invertor ACK")
    assert "last_inv_ack_mono" in proxy._control_inflight
    assert proxy._control_quiet_task is dummy_task


@pytest.mark.asyncio
async def test_handle_invertor_ack_guard_paths():
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "stage": "sent_to_box",
    }

    await proxy._handle_invertor_ack(proxy._control_inflight, "nope")
    await proxy._handle_invertor_ack(
        {"tbl_name": "tbl_box_prms", "tbl_item": "SA"}, "Invertor ACK"
    )
    await proxy._handle_invertor_ack({"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}, "Invertor ACK")

    proxy._control_inflight = {"tx_id": "1", "tbl_name": "tbl_box_prms", "tbl_item": "MODE", "stage": "sent_to_box"}
    await proxy._handle_invertor_ack(proxy._control_inflight, "Invertor ACK")
    assert "last_inv_ack_mono" not in proxy._control_inflight


@pytest.mark.asyncio
async def test_control_observe_box_frame_paths():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    proxy._handle_marker_frames = AsyncMock()
    proxy._handle_setting_event_control = AsyncMock()
    proxy._handle_invertor_ack = AsyncMock()

    await proxy._control_observe_box_frame({"Content": "x"}, "END", "frame")
    proxy._handle_marker_frames.assert_called_once()

    await proxy._control_observe_box_frame({"Type": "Setting", "Content": "x"}, "tbl_events", "frame")
    proxy._handle_setting_event_control.assert_called_once()

    await proxy._control_observe_box_frame({"Type": "Info", "Content": "Invertor ACK"}, "tbl_events", "frame")
    proxy._handle_invertor_ack.assert_called_once()


@pytest.mark.asyncio
async def test_control_observe_box_frame_guard_paths():
    proxy = _make_proxy()
    proxy._control_inflight = None
    proxy._handle_marker_frames = AsyncMock()

    await proxy._control_observe_box_frame(None, "END", "frame")
    await proxy._control_observe_box_frame({"Content": "x"}, None, "frame")
    await proxy._control_observe_box_frame({"Content": 1}, "tbl_events", "frame")
    proxy._handle_marker_frames.assert_not_called()
