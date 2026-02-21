"""Tests for control queue helpers in proxy.py."""

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
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.quiet_task = None
    ctrl.mqtt_enabled = False
    ctrl.set_topic = ""
    ctrl.result_topic = ""
    ctrl.status_prefix = ""
    ctrl.qos = 1
    ctrl.retain = False
    ctrl.status_retain = False
    ctrl.log_enabled = False
    ctrl.log_path = ""
    ctrl.box_ready_s = 0.0
    ctrl.ack_timeout_s = 10.0
    ctrl.applied_timeout_s = 30.0
    ctrl.mode_quiet_s = 0.0
    ctrl.whitelist = {}
    ctrl.max_attempts = 2
    ctrl.retry_delay_s = 10
    ctrl.session_id = "test"
    ctrl.pending_path = ""
    ctrl.pending_keys = set()
    ctrl.retry_task = None
    ctrl.last_result = None
    ctrl.key_state = {}
    ctrl.post_drain_refresh_pending = False
    ctrl.publish_result = AsyncMock()
    ctrl.maybe_start_next = AsyncMock()
    ctrl.maybe_queue_post_drain_refresh = AsyncMock()
    proxy._ctrl = ctrl
    return proxy


@pytest.mark.asyncio
async def test_enqueue_control_tx():
    proxy = _make_proxy()
    tx = {"tx_id": "1"}
    canceled, dropped = await proxy._ctrl.enqueue_tx(tx, "key")
    assert canceled is None
    assert dropped == []
    assert list(proxy._ctrl.queue) == [tx]


@pytest.mark.asyncio
async def test_control_defer_inflight_none():
    proxy = _make_proxy()
    await proxy._ctrl.defer_inflight(reason="retry")
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_defer_inflight_max_attempts():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1", "_attempts": 2}
    await proxy._ctrl.defer_inflight(reason="timeout")
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.maybe_start_next.assert_called_once()
    proxy._ctrl.maybe_queue_post_drain_refresh.assert_called_once()
    assert proxy._ctrl.inflight is None


@pytest.mark.asyncio
async def test_control_defer_inflight_requeue():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1", "_attempts": 1}
    task = MagicMock(done=MagicMock(return_value=False))
    proxy._ctrl.ack_task = task
    await proxy._ctrl.defer_inflight(reason="retry")
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.maybe_start_next.assert_called_once()
    assert proxy._ctrl.inflight is None
    assert proxy._ctrl.queue[0]["stage"] == "deferred"
    assert proxy._ctrl.queue[0]["deferred_reason"] == "retry"
