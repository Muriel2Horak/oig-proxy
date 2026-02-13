"""Tests for MQTT state and control helpers in proxy.py."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=consider-using-with

import asyncio
import json
import tempfile
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import proxy as proxy_module
from control_pipeline import ControlPipeline
from models import ProxyMode
from mqtt_state_cache import MqttStateCache


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._prms_tables = {}
    proxy._prms_device_id = None
    proxy._ctrl = MagicMock()
    proxy._ctrl.queue = deque()
    proxy._ctrl.inflight = None
    proxy._ctrl.ack_task = None
    proxy._ctrl.applied_task = None
    proxy._ctrl.quiet_task = None
    proxy._ctrl.retry_task = None
    proxy._ctrl.last_result = None
    proxy._ctrl.key_state = {}
    proxy._ctrl.pending_keys = set()
    proxy._ctrl.post_drain_refresh_pending = False
    proxy._ctrl.qos = 1
    proxy._ctrl.retain = False
    proxy._ctrl.status_retain = False
    proxy._ctrl.result_topic = "oig/control/result"
    proxy._ctrl.status_prefix = "oig/control/status"
    proxy._ctrl.log_enabled = False
    proxy._ctrl.log_path = tempfile.NamedTemporaryFile(delete=False).name
    proxy._ctrl.pending_path = tempfile.NamedTemporaryFile(delete=False).name
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.device_id = "DEV1"
    proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
    proxy.mqtt_publisher.set_cached_payload = MagicMock(return_value=None)
    proxy._mode_value = None
    proxy._mode_device_id = None
    msc = MqttStateCache.__new__(MqttStateCache)
    msc._proxy = proxy
    msc.last_values = {}
    msc.table_cache = {}
    msc.cache_device_id = None
    proxy._msc = msc
    proxy.publish_proxy_status = AsyncMock()
    return proxy


def _attach_real_ctrl(proxy, pending_path=None):
    """Replace proxy._ctrl with a real ControlPipeline for tests that call its methods."""
    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.mqtt_enabled = True
    ctrl.set_topic = "oig/control/set"
    ctrl.result_topic = "oig/control/result"
    ctrl.status_prefix = "oig/control/status"
    ctrl.qos = 1
    ctrl.retain = False
    ctrl.status_retain = False
    ctrl.log_enabled = False
    ctrl.log_path = tempfile.NamedTemporaryFile(delete=False).name
    ctrl.box_ready_s = 10.0
    ctrl.ack_timeout_s = 30.0
    ctrl.applied_timeout_s = 60.0
    ctrl.mode_quiet_s = 30.0
    ctrl.whitelist = {}
    ctrl.max_attempts = 5
    ctrl.retry_delay_s = 120.0
    ctrl.session_id = "test-session"
    ctrl.pending_path = pending_path or tempfile.NamedTemporaryFile(delete=False).name
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
    proxy._ctrl = ctrl
    return ctrl


def test_parse_mqtt_state_topic_valid(monkeypatch):
    monkeypatch.setattr(proxy_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()
    assert proxy._msc.parse_topic("oig_local/DEV1/tbl_actual/state") == (
        "DEV1",
        "tbl_actual",
    )


def test_parse_mqtt_state_topic_invalid(monkeypatch):
    monkeypatch.setattr(proxy_module, "MQTT_NAMESPACE", "oig_local")
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
    assert proxy._prms_tables["tbl_box_prms"]["MODE"] == "1"
    assert proxy._prms_device_id == "DEV1"


@pytest.mark.asyncio
async def test_handle_mqtt_state_message(monkeypatch):
    monkeypatch.setattr(proxy_module, "MQTT_NAMESPACE", "oig_local")
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


@pytest.mark.asyncio
async def test_control_publish_result_basic():
    proxy = _make_proxy()
    _attach_real_ctrl(proxy)
    tx = {
        "tx_id": "123",
        "request_key": "tbl_box_prms/SA/1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }

    await proxy._ctrl.publish_result(tx=tx, status="accepted")
    assert proxy.mqtt_publisher.publish_raw.called
    assert proxy._ctrl.last_result["status"] == "accepted"


def test_control_result_key_state():
    assert ControlPipeline.result_key_state("accepted", None) == "queued"
    assert ControlPipeline.result_key_state("completed", "noop_already_set") is None
    assert ControlPipeline.result_key_state("error", None) == "error"


@pytest.mark.asyncio
async def test_control_publish_key_status():
    proxy = _make_proxy()
    _attach_real_ctrl(proxy)
    tx = {"request_key": "tbl_box_prms/SA/1"}
    await proxy._ctrl.publish_key_status(tx=tx, state="queued", detail=None)
    assert proxy.mqtt_publisher.publish_raw.called
    assert "tbl_box_prms/SA/1" in proxy._ctrl.key_state


def test_control_load_pending_keys(tmp_path):
    proxy = _make_proxy()
    pending_path = str(tmp_path / "pending.json")
    _attach_real_ctrl(proxy, pending_path=pending_path)
    with open(pending_path, "w", encoding="utf-8") as fh:
        json.dump(["tbl_box_prms/SA/1"], fh)

    keys = proxy._ctrl.load_pending_keys()
    assert keys == {"tbl_box_prms/SA/1"}


def test_control_update_pending_keys(tmp_path):
    proxy = _make_proxy()
    pending_path = str(tmp_path / "pending.json")
    _attach_real_ctrl(proxy, pending_path=pending_path)

    proxy._ctrl.update_pending_keys(request_key="tbl_box_prms/SA/1", state="queued")
    assert "tbl_box_prms/SA/1" in proxy._ctrl.pending_keys

    proxy._ctrl.update_pending_keys(request_key="tbl_box_prms/SA/1", state="done")
    assert "tbl_box_prms/SA/1" not in proxy._ctrl.pending_keys
