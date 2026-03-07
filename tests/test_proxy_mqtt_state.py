"""Tests for MQTT state and control helpers in proxy.py."""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=consider-using-with

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import proxy as proxy_module
from control_pipeline import ControlPipeline
from models import ProxyMode
import mqtt_state_cache as msc_module
from mqtt_state_cache import MqttStateCache


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    from mode_persistence import ModePersistence
    mp = ModePersistence.__new__(ModePersistence)
    mp._proxy = proxy
    mp.mode_value = None
    mp.mode_device_id = None
    mp.mode_pending_publish = False
    mp.prms_tables = {}
    mp.prms_pending_publish = False
    mp.prms_device_id = None
    proxy._mp = mp
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.device_id = "DEV1"
    proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
    proxy.mqtt_publisher.set_cached_payload = MagicMock(return_value=None)
    msc = MqttStateCache.__new__(MqttStateCache)
    msc._proxy = proxy
    msc.last_values = {}
    msc.table_cache = {}
    msc.cache_device_id = None
    proxy._msc = msc
    proxy.publish_proxy_status = AsyncMock()
    return proxy


def test_parse_mqtt_state_topic_valid(monkeypatch):
    monkeypatch.setattr(msc_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()
    assert proxy._msc.parse_topic("oig_local/DEV1/tbl_actual/state") == (
        "DEV1",
        "tbl_actual",
    )


def test_parse_mqtt_state_topic_invalid(monkeypatch):
    monkeypatch.setattr(msc_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()
    assert proxy._msc.parse_topic("oig_local/DEV1/tbl_actual") == (None, None)
    assert proxy._msc.parse_topic("wrong/DEV1/tbl_actual/state") == (None, None)
    assert proxy._msc.parse_topic("oig_local/DEV1/tbl_actual/wrong") == (None, None)


def test_validate_mqtt_state_device(monkeypatch):
    proxy = _make_proxy()
    proxy.mqtt_publisher.device_id = "DEV1"
    assert proxy._msc.validate_device("DEV1") is True
    assert proxy._msc.validate_device("DEV2") is False
    proxy.mqtt_publisher.device_id = None
    assert proxy._msc.validate_device("DEV1") is True
    proxy.device_id = "AUTO"
    assert proxy._msc.validate_device("DEV1") is False


def test_parse_mqtt_state_payload():
    proxy = _make_proxy()
    assert proxy._msc.parse_payload("not json") is None
    assert proxy._msc.parse_payload("[1,2]") is None
    payload = proxy._msc.parse_payload('{"MODE": "1"}')
    assert payload == {"MODE": "1"}


def test_transform_mqtt_state_values(monkeypatch):
    proxy = _make_proxy()
    proxy._msc.to_raw_value = MagicMock(side_effect=lambda **kwargs: kwargs["value"])
    payload = {"MODE": "1", "_ignore": "x"}
    values = proxy._msc.transform_values(payload, "tbl_box_prms")
    assert values == {"MODE": "1"}
    assert proxy._msc.last_values.get(("tbl_box_prms", "MODE")) == "1"


@pytest.mark.asyncio
async def test_persist_mqtt_state_values(monkeypatch):
    proxy = _make_proxy()
    with patch("mqtt_state_cache.save_prms_state") as save_state:
        await proxy._msc.persist_values("tbl_box_prms", {"MODE": "1"}, "DEV1")
    save_state.assert_called_once()
    assert proxy._mp.prms_tables["tbl_box_prms"]["MODE"] == "1"
    assert proxy._mp.prms_device_id == "DEV1"


@pytest.mark.asyncio
async def test_handle_mqtt_state_message(monkeypatch):
    monkeypatch.setattr(msc_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()
    proxy._msc.parse_payload = MagicMock(return_value={"MODE": "1"})
    proxy._msc.transform_values = MagicMock(return_value={"MODE": "1"})
    proxy._msc.persist_values = AsyncMock()

    await proxy._msc.handle_message(
        topic="oig_local/DEV1/tbl_box_prms/state",
        payload_text='{"MODE": "1"}',
        retain=False,
    )

    assert proxy.mqtt_publisher.set_cached_payload.called
    assert proxy._msc.persist_values.called


def test_control_pipeline_format_helpers():
    tx = {
        "tx_id": "123",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
        "stage": "queued",
    }
    result = {
        "tx_id": "123",
        "status": "accepted",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }

    assert ControlPipeline.format_tx(tx) == "tbl_box_prms/SA=1 (queued) tx=123"
    assert ControlPipeline.format_result(result) == "accepted tbl_box_prms/SA=1 tx=123"


def test_control_pipeline_append_to_log(tmp_path):
    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl.log_path = str(tmp_path / "control.log")

    ctrl.append_to_log("line-1\n")

    with open(ctrl.log_path, encoding="utf-8") as fh:
        assert fh.read() == "line-1\n"


@pytest.mark.asyncio
async def test_control_pipeline_async_methods_noop():
    ctrl = ControlPipeline(object())

    await ctrl.publish_restart_errors()
    await ctrl.note_box_disconnect()
    await ctrl.observe_box_frame({}, None, "")
    await ctrl.maybe_start_next()
