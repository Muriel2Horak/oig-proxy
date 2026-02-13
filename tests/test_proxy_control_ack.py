"""Tests for control ack handling and value coercion."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
import control_pipeline as ctrl_module
from control_pipeline import ControlPipeline
from models import ProxyMode, SensorConfig


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
    ctrl.publish_result = AsyncMock()
    ctrl.finish_inflight = AsyncMock()
    proxy._ctrl = ctrl
    return proxy


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_missing_tx():
    proxy = _make_proxy()
    await proxy._ctrl.on_box_setting_ack(tx_id=None, ack=True)
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_mismatch():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    await proxy._ctrl.on_box_setting_ack(tx_id="2", ack=True)
    proxy._ctrl.publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_nack():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    await proxy._ctrl.on_box_setting_ack(tx_id="1", ack=False)
    proxy._ctrl.publish_result.assert_called_once()
    proxy._ctrl.finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_success(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tx_id": "1"}
    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._ctrl.on_box_setting_ack(tx_id="1", ack=True)
    proxy._ctrl.publish_result.assert_called_once()
    assert proxy._ctrl.applied_task is dummy_task


def test_control_coerce_value():
    assert ControlPipeline.coerce_value(None) is None
    assert ControlPipeline.coerce_value(True) is True
    assert ControlPipeline.coerce_value("true") is True
    assert ControlPipeline.coerce_value("false") is False
    assert ControlPipeline.coerce_value("10") == 10
    assert ControlPipeline.coerce_value("-3") == -3
    assert ControlPipeline.coerce_value("3.5") == 3.5
    assert ControlPipeline.coerce_value("abc") == "abc"


def test_control_map_optimistic_value(monkeypatch):
    proxy = _make_proxy()

    cfg = SensorConfig(name="Mode", unit="", options=["OFF", "ON"])
    monkeypatch.setattr(ctrl_module, "get_sensor_config", lambda *_a, **_k: (cfg, "x"))

    assert proxy._ctrl.map_optimistic_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", value="1"
    ) == "ON"
    assert proxy._ctrl.map_optimistic_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", value="0"
    ) == "OFF"
