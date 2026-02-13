"""Tests for control inflight handling and timeouts."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
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
    proxy.box_connected = True

    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.lock = asyncio.Lock()
    ctrl.inflight = None
    ctrl.queue = []
    ctrl.max_attempts = 2
    ctrl.retry_delay_s = 10
    ctrl.ack_timeout_s = 0
    ctrl.applied_timeout_s = 0
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.quiet_task = None
    ctrl.publish_result = AsyncMock()
    ctrl.finish_inflight = AsyncMock()
    ctrl.defer_inflight = AsyncMock()
    ctrl.maybe_start_next = AsyncMock()
    ctrl.maybe_queue_post_drain_refresh = AsyncMock()
    proxy._ctrl = ctrl

    proxy._cs = MagicMock()
    proxy._cs.send_to_box = AsyncMock()
    return proxy


@pytest.mark.asyncio
async def test_control_start_inflight_no_tx():
    proxy = _make_proxy()
    await proxy._ctrl.start_inflight()
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_start_inflight_too_many_attempts():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "_attempts": 2,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }

    await proxy._ctrl.start_inflight()
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_start_inflight_defer_on_box_not_connected():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "_attempts": 0,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._cs.send_to_box = AsyncMock(return_value={"ok": False, "error": "box_not_connected"})

    await proxy._ctrl.start_inflight()
    proxy._ctrl.defer_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_start_inflight_error_send_failed():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "_attempts": 0,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._cs.send_to_box = AsyncMock(return_value={"ok": False, "error": "send_failed"})

    await proxy._ctrl.start_inflight()
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_start_inflight_success_sets_ack_task(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tx_id": "1",
        "_attempts": 0,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._cs.send_to_box = AsyncMock(return_value={"ok": True, "id": "A", "id_set": "B"})

    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._ctrl.start_inflight()

    assert proxy._ctrl.inflight["stage"] == "sent_to_box"
    assert proxy._ctrl.inflight["_attempts"] == 1
    proxy._ctrl.publish_result.assert_called_once()
    assert proxy._ctrl.ack_task is dummy_task


@pytest.mark.asyncio
async def test_control_ack_timeout_defers_when_disconnected(monkeypatch):
    proxy = _make_proxy()
    proxy.box_connected = False
    proxy._ctrl.inflight = {"stage": "sent_to_box"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await proxy._ctrl.ack_timeout()

    proxy._ctrl.defer_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_ack_timeout_defers_for_timeout(monkeypatch):
    proxy = _make_proxy()
    proxy.box_connected = True
    proxy._ctrl.inflight = {"stage": "sent_to_box"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await proxy._ctrl.ack_timeout()

    proxy._ctrl.defer_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_applied_timeout_error(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"stage": "box_ack"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await proxy._ctrl.applied_timeout()

    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.finish_inflight.assert_called_once()
