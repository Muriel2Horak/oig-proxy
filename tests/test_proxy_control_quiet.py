"""Tests for control quiet window and inflight finish helpers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._control_lock = asyncio.Lock()
    proxy._control_inflight = None
    proxy._control_queue = deque()
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_quiet_task = None
    proxy._control_mode_quiet_s = 1
    proxy._control_publish_result = AsyncMock()
    proxy._control_maybe_start_next = AsyncMock()
    proxy._control_maybe_queue_post_drain_refresh = AsyncMock()
    return proxy


@pytest.mark.asyncio
async def test_control_quiet_wait_no_inflight():
    proxy = _make_proxy()
    await proxy._control_quiet_wait()
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_quiet_wait_not_applied():
    proxy = _make_proxy()
    proxy._control_inflight = {"stage": "sent_to_box"}
    await proxy._control_quiet_wait()
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_quiet_wait_complete(monkeypatch):
    proxy = _make_proxy()
    proxy._control_inflight = {
        "stage": "applied",
        "applied_at_mono": 0.0,
    }

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(proxy_module.time, "monotonic", lambda: 10.0)

    await proxy._control_quiet_wait()
    proxy._control_publish_result.assert_called_once()
    proxy._control_maybe_start_next.assert_called_once()
    proxy._control_maybe_queue_post_drain_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_control_finish_inflight_cancels_tasks():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    task = MagicMock(done=MagicMock(return_value=False))
    proxy._control_ack_task = task

    await proxy._control_finish_inflight()
    assert proxy._control_inflight is None
    task.cancel.assert_called_once()
    proxy._control_maybe_start_next.assert_called_once()
    proxy._control_maybe_queue_post_drain_refresh.assert_called_once()
