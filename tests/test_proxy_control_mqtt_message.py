"""Tests for control MQTT message handling."""

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
    ctrl.maybe_start_next = AsyncMock()
    ctrl.validate_request = AsyncMock(return_value={"tx_id": "1"})
    ctrl.build_tx = MagicMock(return_value={
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
        "_canon": "1",
    })
    ctrl.check_whitelist_and_normalize = AsyncMock(return_value=(True, None))
    ctrl.handle_duplicate_or_noop = AsyncMock(return_value=False)
    ctrl.enqueue_tx = AsyncMock(return_value=(None, []))

    proxy._ctrl = ctrl
    return proxy


@pytest.mark.asyncio
async def test_control_on_mqtt_message_missing_fields():
    proxy = _make_proxy()
    proxy._ctrl.build_tx = MagicMock(return_value=None)

    await proxy._ctrl.on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.maybe_start_next.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_mqtt_message_not_allowed():
    proxy = _make_proxy()
    proxy._ctrl.check_whitelist_and_normalize = AsyncMock(return_value=(False, "not_allowed"))

    await proxy._ctrl.on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_mqtt_message_duplicate():
    proxy = _make_proxy()
    proxy._ctrl.handle_duplicate_or_noop = AsyncMock(return_value=True)

    await proxy._ctrl.on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._ctrl.enqueue_tx.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_mqtt_message_success():
    proxy = _make_proxy()

    await proxy._ctrl.on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._ctrl.enqueue_tx.assert_called_once()
    proxy._ctrl.publish_result.assert_called_with(
        tx=proxy._ctrl.build_tx.return_value, status="accepted"
    )
    proxy._ctrl.maybe_start_next.assert_called_once()
