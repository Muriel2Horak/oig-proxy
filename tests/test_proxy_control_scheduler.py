"""Tests for control scheduling helpers."""

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
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._control_lock = asyncio.Lock()
    proxy._control_queue = deque()
    proxy._control_inflight = None
    proxy._control_retry_task = None
    proxy._control_is_box_ready = MagicMock(return_value=(True, None))
    proxy._control_start_inflight = AsyncMock()
    return proxy


@pytest.mark.asyncio
async def test_control_maybe_start_next_empty_queue():
    proxy = _make_proxy()
    await proxy._control_maybe_start_next()
    proxy._control_start_inflight.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_start_next_inflight_exists():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    proxy._control_queue.append({"tx_id": "2"})
    await proxy._control_maybe_start_next()
    assert proxy._control_inflight["tx_id"] == "1"
    proxy._control_start_inflight.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_start_next_schedules_retry():
    proxy = _make_proxy()
    proxy._control_queue.append({"tx_id": "1", "next_attempt_at": 10})
    proxy._control_schedule_retry = AsyncMock()

    with pytest.MonkeyPatch.context() as m:
        m.setattr(proxy_module.time, "monotonic", lambda: 0.0)
        await proxy._control_maybe_start_next()

    proxy._control_schedule_retry.assert_called_once()
    proxy._control_start_inflight.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_start_next_pops_queue():
    proxy = _make_proxy()
    proxy._control_queue.append({"tx_id": "1"})
    await proxy._control_maybe_start_next()
    assert proxy._control_inflight["tx_id"] == "1"
    proxy._control_start_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_schedule_retry_immediate():
    proxy = _make_proxy()
    proxy._control_maybe_start_next = AsyncMock()
    await proxy._control_schedule_retry(0)
    proxy._control_maybe_start_next.assert_called_once()


@pytest.mark.asyncio
async def test_control_schedule_retry_task_exists():
    proxy = _make_proxy()
    proxy._control_retry_task = MagicMock()
    proxy._control_retry_task.done.return_value = False
    await proxy._control_schedule_retry(1)
    proxy._control_retry_task.done.assert_called_once()
