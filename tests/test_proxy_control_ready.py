"""Tests for control readiness and validation helpers."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy.box_connected = True
    proxy._box_connected_since_epoch = time.time() - 60
    proxy._control_box_ready_s = 5
    proxy._last_data_epoch = time.time()
    proxy._control_qos = 1
    proxy._control_retain = False
    proxy._control_result_topic = "oig/control/result"
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
    return proxy


def test_control_is_box_ready_ok():
    proxy = _make_proxy()
    ok, reason = proxy._control_is_box_ready()
    assert ok is True
    assert reason is None


def test_control_is_box_ready_not_connected():
    proxy = _make_proxy()
    proxy.box_connected = False
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_connected"


def test_control_is_box_ready_device_unknown():
    proxy = _make_proxy()
    proxy.device_id = "AUTO"
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "device_id_unknown"


def test_control_is_box_ready_not_ready_time():
    proxy = _make_proxy()
    proxy._box_connected_since_epoch = time.time()
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_ready"


def test_control_is_box_ready_not_sending():
    proxy = _make_proxy()
    proxy._last_data_epoch = time.time() - 120
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_sending_data"


@pytest.mark.asyncio
async def test_validate_control_request_bad_json():
    proxy = _make_proxy()
    result = await proxy._validate_control_request(b"{bad json}")
    assert result is None
    proxy.mqtt_publisher.publish_raw.assert_called_once()


@pytest.mark.asyncio
async def test_validate_control_request_ok():
    proxy = _make_proxy()
    payload = json.dumps({"tx_id": "1"}).encode("utf-8")
    result = await proxy._validate_control_request(payload)
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
