"""Tests for control ack handling and value coercion."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode, SensorConfig


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._control_lock = asyncio.Lock()
    proxy._control_inflight = None
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_publish_result = AsyncMock()
    proxy._control_finish_inflight = AsyncMock()
    return proxy


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_missing_tx():
    proxy = _make_proxy()
    await proxy._control_on_box_setting_ack(tx_id=None, ack=True)
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_mismatch():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    await proxy._control_on_box_setting_ack(tx_id="2", ack=True)
    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_nack():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    await proxy._control_on_box_setting_ack(tx_id="1", ack=False)
    proxy._control_publish_result.assert_called_once()
    proxy._control_finish_inflight.assert_called_once()


@pytest.mark.asyncio
async def test_control_on_box_setting_ack_success(monkeypatch):
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    dummy_task = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return dummy_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy._control_on_box_setting_ack(tx_id="1", ack=True)
    proxy._control_publish_result.assert_called_once()
    assert proxy._control_applied_task is dummy_task


def test_control_coerce_value():
    assert proxy_module.OIGProxy._control_coerce_value(None) is None
    assert proxy_module.OIGProxy._control_coerce_value(True) is True
    assert proxy_module.OIGProxy._control_coerce_value("true") is True
    assert proxy_module.OIGProxy._control_coerce_value("false") is False
    assert proxy_module.OIGProxy._control_coerce_value("10") == 10
    assert proxy_module.OIGProxy._control_coerce_value("-3") == -3
    assert proxy_module.OIGProxy._control_coerce_value("3.5") == 3.5
    assert proxy_module.OIGProxy._control_coerce_value("abc") == "abc"


def test_control_map_optimistic_value(monkeypatch):
    proxy = _make_proxy()

    cfg = SensorConfig(name="Mode", unit="", options=["OFF", "ON"])
    monkeypatch.setattr(proxy_module, "get_sensor_config", lambda *_a, **_k: (cfg, "x"))

    assert proxy._control_map_optimistic_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", value="1"
    ) == "ON"
    assert proxy._control_map_optimistic_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", value="0"
    ) == "OFF"
