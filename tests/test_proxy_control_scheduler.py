"""Tests for control scheduling helpers."""

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
    ctrl.lock = asyncio.Lock()
    ctrl.queue = deque()
    ctrl.inflight = None
    ctrl.retry_task = None
    ctrl.is_box_ready = MagicMock(return_value=(True, None))
    ctrl.start_inflight = AsyncMock()
    proxy._ctrl = ctrl
    return proxy


@pytest.mark.asyncio
async def test_control_maybe_start_next_empty_queue():
    proxy = _make_proxy()
    await proxy._ctrl.maybe_start_next()
    proxy._ctrl.start_inflight.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_start_next_inflight_exists():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    proxy._ctrl.queue.append({"tx_id": "2"})
    await proxy._ctrl.maybe_start_next()
    assert proxy._ctrl.inflight["tx_id"] == "1"
    proxy._ctrl.start_inflight.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_start_next_schedules_retry():
    proxy = _make_proxy()
    proxy._ctrl.queue.append({"tx_id": "1", "next_attempt_at": 10})
    proxy._ctrl.schedule_retry = AsyncMock()

    with pytest.MonkeyPatch.context() as m:
        m.setattr(proxy_module.time, "monotonic", lambda: 0.0)
        await proxy._ctrl.maybe_start_next()

    proxy._ctrl.schedule_retry.assert_called_once()
    proxy._ctrl.start_inflight.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_start_next_pops_queue():
    proxy = _make_proxy()
    proxy._ctrl.queue.append({"tx_id": "1"})
    await proxy._ctrl.maybe_start_next()
    assert proxy._ctrl.inflight["tx_id"] == "1"
    proxy._ctrl.start_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_schedule_retry_immediate():
    proxy = _make_proxy()
    proxy._ctrl.maybe_start_next = AsyncMock()
    await proxy._ctrl.schedule_retry(0)
    proxy._ctrl.maybe_start_next.assert_called_once()


@pytest.mark.asyncio
async def test_control_schedule_retry_task_exists():
    proxy = _make_proxy()
    proxy._ctrl.retry_task = MagicMock()
    proxy._ctrl.retry_task.done.return_value = False
    await proxy._ctrl.schedule_retry(1)
    proxy._ctrl.retry_task.done.assert_called_once()
