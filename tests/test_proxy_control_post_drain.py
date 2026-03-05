"""Tests for post-drain control refresh helpers."""

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
    ctrl.log_path = "/tmp/control.log"
    ctrl.box_ready_s = 15.0
    ctrl.ack_timeout_s = 30.0
    ctrl.applied_timeout_s = 60.0
    ctrl.mode_quiet_s = 120.0
    ctrl.whitelist = {"tbl_box_prms": {"SA", "MODE"}, "tbl_invertor_prm1": set()}
    ctrl.max_attempts = 5
    ctrl.retry_delay_s = 120.0
    ctrl.session_id = "test-session"
    ctrl.pending_path = "/tmp/pending.json"
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
    ctrl.enqueue_internal_sa = AsyncMock()
    proxy._ctrl = ctrl
    return proxy


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_skip_no_tx():
    proxy = _make_proxy()
    await proxy._ctrl.maybe_queue_post_drain_refresh(last_tx=None)
    proxy._ctrl.enqueue_internal_sa.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_skip_sa():
    proxy = _make_proxy()
    last_tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA"}
    await proxy._ctrl.maybe_queue_post_drain_refresh(last_tx=last_tx)
    proxy._ctrl.enqueue_internal_sa.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_skip_inflight():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    proxy._ctrl.post_drain_refresh_pending = True
    last_tx = {"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}
    await proxy._ctrl.maybe_queue_post_drain_refresh(last_tx=last_tx)
    proxy._ctrl.enqueue_internal_sa.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_enqueue():
    proxy = _make_proxy()
    proxy._ctrl.post_drain_refresh_pending = True
    last_tx = {"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}
    await proxy._ctrl.maybe_queue_post_drain_refresh(last_tx=last_tx)
    proxy._ctrl.enqueue_internal_sa.assert_called_once_with(reason="queue_drained")


@pytest.mark.asyncio
async def test_control_enqueue_internal_sa():
    proxy = _make_proxy()
    proxy._ctrl.enqueue_internal_sa = ControlPipeline.enqueue_internal_sa
    proxy._ctrl.build_request_key = MagicMock(return_value="tbl_box_prms:SA")
    proxy._ctrl.publish_result = AsyncMock()
    proxy._ctrl.maybe_start_next = AsyncMock()
    proxy._ctrl.inflight = None

    await proxy._ctrl.enqueue_internal_sa(proxy._ctrl, reason="queue_drained")
    assert proxy._ctrl.queue
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.maybe_start_next.assert_called_once()
