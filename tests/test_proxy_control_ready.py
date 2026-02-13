"""Tests for control readiness and validation helpers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=no-member

import json
import time
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
    proxy.box_connected = True
    proxy._box_connected_since_epoch = time.time() - 60
    proxy._last_data_epoch = time.time()

    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.box_ready_s = 5
    ctrl.qos = 1
    ctrl.retain = False
    ctrl.result_topic = "oig/control/result"
    proxy._ctrl = ctrl

    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
    return proxy


def test_control_is_box_ready_ok():
    proxy = _make_proxy()
    ok, reason = proxy._ctrl.is_box_ready()
    assert ok is True
    assert reason is None


def test_control_is_box_ready_not_connected():
    proxy = _make_proxy()
    proxy.box_connected = False
    ok, reason = proxy._ctrl.is_box_ready()
    assert ok is False
    assert reason == "box_not_connected"


def test_control_is_box_ready_device_unknown():
    proxy = _make_proxy()
    proxy.device_id = "AUTO"
    ok, reason = proxy._ctrl.is_box_ready()
    assert ok is False
    assert reason == "device_id_unknown"


def test_control_is_box_ready_not_ready_time():
    proxy = _make_proxy()
    proxy._box_connected_since_epoch = time.time()
    ok, reason = proxy._ctrl.is_box_ready()
    assert ok is False
    assert reason == "box_not_ready"


def test_control_is_box_ready_not_sending():
    proxy = _make_proxy()
    proxy._last_data_epoch = time.time() - 120
    ok, reason = proxy._ctrl.is_box_ready()
    assert ok is False
    assert reason == "box_not_sending_data"


@pytest.mark.asyncio
async def test_validate_control_request_bad_json():
    proxy = _make_proxy()
    result = await proxy._ctrl.validate_request(b"{bad json}")
    assert result is None
    proxy.mqtt_publisher.publish_raw.assert_called_once()


@pytest.mark.asyncio
async def test_validate_control_request_ok():
    proxy = _make_proxy()
    payload = json.dumps({"tx_id": "1"}).encode("utf-8")
    result = await proxy._ctrl.validate_request(payload)
    assert result == {"tx_id": "1"}


def test_update_cached_value_updates_mode(monkeypatch):
    proxy = _make_proxy()
    proxy._last_values = {}
    proxy._table_cache = {}
    proxy._mode_value = None
    proxy._mode_device_id = None
    proxy._prms_device_id = None
    proxy.device_id = "DEV1"

    with pytest.MonkeyPatch.context() as m:
        m.setattr(proxy_module, "save_mode_state", MagicMock())
        proxy._update_cached_value(
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            raw_value="2",
            update_mode=True,
        )
        assert proxy._mode_value == 2
        assert proxy._mode_device_id == "DEV1"
        proxy_module.save_mode_state.assert_called_once()


def test_update_cached_value_skips_invalid_mode(monkeypatch):
    proxy = _make_proxy()
    proxy._last_values = {}
    proxy._table_cache = {}
    proxy._mode_value = None

    with pytest.MonkeyPatch.context() as m:
        m.setattr(proxy_module, "save_mode_state", MagicMock())
        proxy._update_cached_value(
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            raw_value="9",
            update_mode=True,
        )
        assert proxy._mode_value is None
        proxy_module.save_mode_state.assert_not_called()
