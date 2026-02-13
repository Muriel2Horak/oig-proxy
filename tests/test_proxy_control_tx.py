"""Tests for control TX normalization and duplicate handling."""

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
    proxy._last_values = {}

    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.whitelist = {"tbl_box_prms": {"SA", "MODE"}}
    ctrl.key_state = {}
    ctrl.lock = asyncio.Lock()
    ctrl.inflight = None
    ctrl.queue = deque()
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
    ctrl.max_attempts = 5
    ctrl.retry_delay_s = 120.0
    ctrl.session_id = "test"
    ctrl.pending_path = ""
    ctrl.pending_keys = set()
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.quiet_task = None
    ctrl.retry_task = None
    ctrl.last_result = None
    ctrl.post_drain_refresh_pending = False
    ctrl.publish_result = AsyncMock()
    proxy._ctrl = ctrl
    return proxy


def test_build_control_tx_ok():
    proxy = _make_proxy()
    data = {"tx_id": "1", "tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": 1}
    tx = proxy._ctrl.build_tx(data)
    assert tx is not None
    assert tx["tx_id"] == "1"
    assert tx["tbl_name"] == "tbl_box_prms"
    assert tx["tbl_item"] == "SA"
    assert tx["new_value"] == 1
    assert tx["confirm"] == "New"
    assert tx["_attempts"] == 0


def test_build_control_tx_missing_fields():
    proxy = _make_proxy()
    assert proxy._ctrl.build_tx({"tx_id": ""}) is None
    assert proxy._ctrl.build_tx({"tx_id": "1", "tbl_name": "", "tbl_item": "SA"}) is None
    assert proxy._ctrl.build_tx({"tx_id": "1", "tbl_name": "tbl", "tbl_item": ""}) is None
    assert proxy._ctrl.build_tx({"tx_id": "1", "tbl_name": "tbl", "tbl_item": "SA"}) is None


@pytest.mark.asyncio
async def test_check_whitelist_and_normalize_not_allowed():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SB", "new_value": "1"}
    ok, err = await proxy._ctrl.check_whitelist_and_normalize(tx)
    assert ok is False
    assert err == "not_allowed"
    proxy._ctrl.publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_check_whitelist_and_normalize_invalid_value():
    proxy = _make_proxy()
    proxy._ctrl.normalize_value = MagicMock(return_value=(None, "bad_value"))
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": "x"}
    ok, err = await proxy._ctrl.check_whitelist_and_normalize(tx)
    assert ok is False
    assert err == "bad_value"
    proxy._ctrl.publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_check_whitelist_and_normalize_ok():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": "1"}
    ok, err = await proxy._ctrl.check_whitelist_and_normalize(tx)
    assert ok is True
    assert err is None
    assert tx["new_value"] == "1"
    assert tx["_canon"] == "1"


@pytest.mark.asyncio
async def test_handle_duplicate_or_noop_active_state():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "_canon": "1"}
    proxy._ctrl.key_state["key"] = {"state": "queued"}
    handled = await proxy._ctrl.handle_duplicate_or_noop(tx, "key")
    assert handled is True
    proxy._ctrl.publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_handle_duplicate_or_noop_noop_value():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "_canon": "1"}
    proxy._last_values[("tbl_box_prms", "SA")] = "1"
    handled = await proxy._ctrl.handle_duplicate_or_noop(tx, "key")
    assert handled is True
    proxy._ctrl.publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_handle_duplicate_or_noop_false():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "_canon": "2"}
    proxy._last_values[("tbl_box_prms", "SA")] = "1"
    handled = await proxy._ctrl.handle_duplicate_or_noop(tx, "key")
    assert handled is False
