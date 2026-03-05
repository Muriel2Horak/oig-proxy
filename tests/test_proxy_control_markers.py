"""Tests for control marker and setting event handlers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from control_pipeline import ControlPipeline
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"

    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.mqtt_enabled = False
    ctrl.set_topic = "oig/control/set"
    ctrl.result_topic = "oig/control/result"
    ctrl.status_prefix = "oig/control/status"
    ctrl.qos = 1
    ctrl.retain = False
    ctrl.status_retain = False
    ctrl.log_enabled = False
    ctrl.log_path = ""
    ctrl.box_ready_s = 0.0
    ctrl.ack_timeout_s = 0.0
    ctrl.applied_timeout_s = 0.0
    ctrl.mode_quiet_s = 0.0
    ctrl.whitelist = {}
    ctrl.max_attempts = 5
    ctrl.retry_delay_s = 120.0
    ctrl.session_id = "sess"
    ctrl.pending_path = ""
    ctrl.pending_keys = set()
    ctrl.queue = deque()
    ctrl.inflight = None
    ctrl.lock = asyncio.Lock()
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.quiet_task = None
    ctrl.retry_task = None
    ctrl.last_result = None
    ctrl.key_state = {}
    ctrl.post_drain_refresh_pending = False
    # intentional mocks
    ctrl.publish_result = AsyncMock()
    ctrl.finish_inflight = AsyncMock()

    proxy._ctrl = ctrl
    proxy._cs = MagicMock()
    return proxy


@pytest.mark.asyncio
async def test_get_valid_tx_with_lock():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    assert await proxy._ctrl.get_valid_tx_with_lock("1") == {"tx_id": "1"}
    assert await proxy._ctrl.get_valid_tx_with_lock("2") is None


@pytest.mark.asyncio
async def test_handle_marker_frames_completed():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1", "stage": "box_ack"}

    await proxy._ctrl.handle_marker_frames({"tx_id": "1"}, "END")
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_handle_marker_frames_no_tx_id():
    proxy = _make_proxy()
    await proxy._ctrl.handle_marker_frames({}, "END")
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_handle_setting_event_control_applied(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }

    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_box_prms", "SA", "0", "1"))

    await proxy._ctrl.handle_setting_event_control(proxy._ctrl.inflight, "event")
    proxy._ctrl.publish_result.assert_called()
    proxy._ctrl.finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_handle_setting_event_control_guard_paths():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._cs.parse_setting_event = MagicMock(return_value=None)

    await proxy._ctrl.handle_setting_event_control(proxy._ctrl.inflight, "event")
    proxy._ctrl.publish_result.assert_not_called()

    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_other", "SA", "0", "1"))
    await proxy._ctrl.handle_setting_event_control(proxy._ctrl.inflight, "event")
    proxy._ctrl.publish_result.assert_not_called()

    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_box_prms", "SA", "0", "2"))
    await proxy._ctrl.handle_setting_event_control(proxy._ctrl.inflight, "event")
    proxy._ctrl.publish_result.assert_not_called()

    proxy._ctrl.inflight = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": "1"}
    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_box_prms", "SA", "0", "1"))
    await proxy._ctrl.handle_setting_event_control(proxy._ctrl.inflight, "event")
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_handle_setting_event_control_mode_sets_quiet(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
    }

    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_box_prms", "MODE", "0", "1"))
    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._ctrl.handle_setting_event_control(proxy._ctrl.inflight, "event")
    proxy._ctrl.publish_result.assert_called()
    assert proxy._ctrl.quiet_task is dummy_task


@pytest.mark.asyncio
async def test_handle_invertor_ack_updates_timestamp(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
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

    await proxy._ctrl.handle_invertor_ack(proxy._ctrl.inflight, "Invertor ACK")
    assert "last_inv_ack_mono" in proxy._ctrl.inflight
    assert proxy._ctrl.quiet_task is dummy_task


@pytest.mark.asyncio
async def test_handle_invertor_ack_guard_paths():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "stage": "sent_to_box",
    }

    await proxy._ctrl.handle_invertor_ack(proxy._ctrl.inflight, "nope")
    await proxy._ctrl.handle_invertor_ack(
        {"tbl_name": "tbl_box_prms", "tbl_item": "SA"}, "Invertor ACK"
    )
    await proxy._ctrl.handle_invertor_ack({"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}, "Invertor ACK")

    proxy._ctrl.inflight = {"tx_id": "1", "tbl_name": "tbl_box_prms", "tbl_item": "MODE", "stage": "sent_to_box"}
    await proxy._ctrl.handle_invertor_ack(proxy._ctrl.inflight, "Invertor ACK")
    assert "last_inv_ack_mono" not in proxy._ctrl.inflight


@pytest.mark.asyncio
async def test_control_observe_box_frame_paths():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    proxy._ctrl.handle_marker_frames = AsyncMock()
    proxy._ctrl.handle_setting_event_control = AsyncMock()
    proxy._ctrl.handle_invertor_ack = AsyncMock()

    await proxy._ctrl.observe_box_frame({"Content": "x"}, "END", "frame")
    proxy._ctrl.handle_marker_frames.assert_called_once()

    await proxy._ctrl.observe_box_frame({"Type": "Setting", "Content": "x"}, "tbl_events", "frame")
    proxy._ctrl.handle_setting_event_control.assert_called_once()

    await proxy._ctrl.observe_box_frame({"Type": "Info", "Content": "Invertor ACK"}, "tbl_events", "frame")
    proxy._ctrl.handle_invertor_ack.assert_called_once()


@pytest.mark.asyncio
async def test_control_observe_box_frame_guard_paths():
    proxy = _make_proxy()
    proxy._ctrl.inflight = None
    proxy._ctrl.handle_marker_frames = AsyncMock()

    await proxy._ctrl.observe_box_frame(None, "END", "frame")
    await proxy._ctrl.observe_box_frame({"Content": "x"}, None, "frame")
    await proxy._ctrl.observe_box_frame({"Content": 1}, "tbl_events", "frame")
    proxy._ctrl.handle_marker_frames.assert_not_called()
