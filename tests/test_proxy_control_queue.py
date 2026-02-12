"""Tests for control queue helpers in proxy.py."""

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
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_quiet_task = None
    proxy._control_cancel_post_drain_sa_inflight_locked = MagicMock(return_value=None)
    proxy._control_drop_post_drain_sa_locked = MagicMock(return_value=[])
    proxy._control_publish_result = AsyncMock()
    proxy._control_maybe_start_next = AsyncMock()
    proxy._control_maybe_queue_post_drain_refresh = AsyncMock()
    proxy._control_max_attempts = 2
    proxy._control_retry_delay_s = 10
    return proxy


@pytest.mark.asyncio
async def test_enqueue_control_tx():
    proxy = _make_proxy()
    tx = {"tx_id": "1"}
    canceled, dropped = await proxy._enqueue_control_tx(tx, "key")
    assert canceled is None
    assert dropped == []
    assert list(proxy._control_queue) == [tx]


@pytest.mark.asyncio
async def test_control_defer_inflight_none():
    proxy = _make_proxy()
    await proxy._control_defer_inflight(reason="retry")
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_defer_inflight_max_attempts():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1", "_attempts": 2}
    await proxy._control_defer_inflight(reason="timeout")
    proxy._control_publish_result.assert_called_once()
    proxy._control_maybe_start_next.assert_called_once()
    proxy._control_maybe_queue_post_drain_refresh.assert_called_once()
    assert proxy._control_inflight is None


@pytest.mark.asyncio
async def test_control_defer_inflight_requeue():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1", "_attempts": 1}
    task = MagicMock(done=MagicMock(return_value=False))
    proxy._control_ack_task = task
    await proxy._control_defer_inflight(reason="retry")
    proxy._control_publish_result.assert_called_once()
    proxy._control_maybe_start_next.assert_called_once()
    assert proxy._control_inflight is None
    assert proxy._control_queue[0]["stage"] == "deferred"
    assert proxy._control_queue[0]["deferred_reason"] == "retry"
