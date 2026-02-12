"""Tests for control inflight handling and timeouts."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy.box_connected = True
    proxy._control_lock = asyncio.Lock()
    proxy._control_inflight = None
    proxy._control_queue = []
    proxy._control_max_attempts = 2
    proxy._control_retry_delay_s = 10
    proxy._control_ack_timeout_s = 0
    proxy._control_applied_timeout_s = 0
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_quiet_task = None
    proxy._control_publish_result = AsyncMock()
    proxy._control_finish_inflight = AsyncMock()
    proxy._control_defer_inflight = AsyncMock()
    proxy._control_maybe_start_next = AsyncMock()
    proxy._control_maybe_queue_post_drain_refresh = AsyncMock()
    proxy._send_setting_to_box = AsyncMock()
    return proxy


@pytest.mark.asyncio
async def test_control_start_inflight_no_tx():
    proxy = _make_proxy()
    await proxy._control_start_inflight()
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_start_inflight_too_many_attempts():
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "_attempts": 2,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }

    await proxy._control_start_inflight()
    proxy._control_publish_result.assert_called_once()
    proxy._control_finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_start_inflight_defer_on_box_not_connected():
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "_attempts": 0,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._send_setting_to_box = AsyncMock(return_value={"ok": False, "error": "box_not_connected"})

    await proxy._control_start_inflight()
    proxy._control_defer_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_start_inflight_error_send_failed():
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "_attempts": 0,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._send_setting_to_box = AsyncMock(return_value={"ok": False, "error": "send_failed"})

    await proxy._control_start_inflight()
    proxy._control_publish_result.assert_called_once()
    proxy._control_finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_start_inflight_success_sets_ack_task(monkeypatch):
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tx_id": "1",
        "_attempts": 0,
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    proxy._send_setting_to_box = AsyncMock(return_value={"ok": True, "id": "A", "id_set": "B"})

    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._control_start_inflight()

    assert proxy._control_inflight["stage"] == "sent_to_box"
    assert proxy._control_inflight["_attempts"] == 1
    proxy._control_publish_result.assert_called_once()
    assert proxy._control_ack_task is dummy_task


@pytest.mark.asyncio
async def test_control_ack_timeout_defers_when_disconnected(monkeypatch):
    proxy = _make_proxy()
    proxy.box_connected = False
    proxy._control_inflight = {"stage": "sent_to_box"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await proxy._control_ack_timeout()

    proxy._control_defer_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_ack_timeout_defers_for_timeout(monkeypatch):
    proxy = _make_proxy()
    proxy.box_connected = True
    proxy._control_inflight = {"stage": "sent_to_box"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await proxy._control_ack_timeout()

    proxy._control_defer_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_applied_timeout_error(monkeypatch):
    proxy = _make_proxy()
    proxy._control_inflight = {"stage": "box_ack"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await proxy._control_applied_timeout()

    proxy._control_publish_result.assert_called_once()
    proxy._control_finish_inflight.assert_called_once()
