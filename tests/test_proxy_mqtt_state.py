"""Tests for MQTT state and control helpers in proxy.py."""

import asyncio
import json
import tempfile
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._prms_tables = {}
    proxy._prms_device_id = None
    proxy._control_queue = deque()
    proxy._control_inflight = None
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_quiet_task = None
    proxy._control_retry_task = None
    proxy._control_last_result = None
    proxy._control_key_state = {}
    proxy._control_pending_keys = set()
    proxy._control_post_drain_refresh_pending = False
    proxy._control_qos = 1
    proxy._control_retain = False
    proxy._control_status_retain = False
    proxy._control_result_topic = "oig/control/result"
    proxy._control_status_prefix = "oig/control/status"
    proxy._control_log_enabled = False
    proxy._control_log_path = tempfile.NamedTemporaryFile(delete=False).name
    proxy._control_pending_path = tempfile.NamedTemporaryFile(delete=False).name
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.device_id = "DEV1"
    proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
    proxy.mqtt_publisher.set_cached_payload = MagicMock(return_value=None)
    proxy._update_cached_value = MagicMock()
    proxy._should_persist_table = MagicMock(return_value=True)
    proxy.publish_proxy_status = AsyncMock()
    return proxy


def test_parse_mqtt_state_topic_valid(monkeypatch):
    monkeypatch.setattr(proxy_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()
    assert proxy._parse_mqtt_state_topic("oig_local/DEV1/tbl_actual/state") == (
        "DEV1",
        "tbl_actual",
    )


def test_parse_mqtt_state_topic_invalid(monkeypatch):
    monkeypatch.setattr(proxy_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()
    assert proxy._parse_mqtt_state_topic("oig_local/DEV1/tbl_actual") == (None, None)
    assert proxy._parse_mqtt_state_topic("wrong/DEV1/tbl_actual/state") == (None, None)
    assert proxy._parse_mqtt_state_topic("oig_local/DEV1/tbl_actual/wrong") == (None, None)


def test_validate_mqtt_state_device(monkeypatch):
    proxy = _make_proxy()
    proxy.mqtt_publisher.device_id = "DEV1"
    assert proxy._validate_mqtt_state_device("DEV1") is True
    assert proxy._validate_mqtt_state_device("DEV2") is False
    proxy.mqtt_publisher.device_id = None
    assert proxy._validate_mqtt_state_device("DEV1") is True
    proxy.device_id = "AUTO"
    assert proxy._validate_mqtt_state_device("DEV1") is False


def test_parse_mqtt_state_payload():
    proxy = _make_proxy()
    assert proxy._parse_mqtt_state_payload("not json") is None
    assert proxy._parse_mqtt_state_payload("[1,2]") is None
    payload = proxy._parse_mqtt_state_payload('{"MODE": "1"}')
    assert payload == {"MODE": "1"}


def test_transform_mqtt_state_values(monkeypatch):
    proxy = _make_proxy()
    proxy._mqtt_state_to_raw_value = MagicMock(side_effect=lambda **kwargs: kwargs["value"])
    payload = {"MODE": "1", "_ignore": "x"}
    values = proxy._transform_mqtt_state_values(payload, "tbl_box_prms")
    assert values == {"MODE": "1"}
    proxy._update_cached_value.assert_called_once()


@pytest.mark.asyncio
async def test_persist_mqtt_state_values(monkeypatch):
    proxy = _make_proxy()
    with patch("proxy.save_prms_state") as save_state:
        await proxy._persist_mqtt_state_values("tbl_box_prms", {"MODE": "1"}, "DEV1")
    save_state.assert_called_once()
    assert proxy._prms_tables["tbl_box_prms"]["MODE"] == "1"
    assert proxy._prms_device_id == "DEV1"


@pytest.mark.asyncio
async def test_handle_mqtt_state_message(monkeypatch):
    monkeypatch.setattr(proxy_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()
    proxy._parse_mqtt_state_payload = MagicMock(return_value={"MODE": "1"})
    proxy._transform_mqtt_state_values = MagicMock(return_value={"MODE": "1"})
    proxy._persist_mqtt_state_values = AsyncMock()

    await proxy._handle_mqtt_state_message(
        topic="oig_local/DEV1/tbl_box_prms/state",
        payload_text='{"MODE": "1"}',
        retain=False,
    )

    assert proxy.mqtt_publisher.set_cached_payload.called
    assert proxy._persist_mqtt_state_values.called


@pytest.mark.asyncio
async def test_control_publish_result_basic():
    proxy = _make_proxy()
    tx = {
        "tx_id": "123",
        "request_key": "tbl_box_prms/SA/1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }

    await proxy._control_publish_result(tx=tx, status="accepted")
    assert proxy.mqtt_publisher.publish_raw.called
    assert proxy._control_last_result["status"] == "accepted"


def test_control_result_key_state():
    assert proxy_module.OIGProxy._control_result_key_state("accepted", None) == "queued"
    assert proxy_module.OIGProxy._control_result_key_state("completed", "noop_already_set") is None
    assert proxy_module.OIGProxy._control_result_key_state("error", None) == "error"


@pytest.mark.asyncio
async def test_control_publish_key_status():
    proxy = _make_proxy()
    tx = {"request_key": "tbl_box_prms/SA/1"}
    await proxy._control_publish_key_status(tx=tx, state="queued", detail=None)
    assert proxy.mqtt_publisher.publish_raw.called
    assert "tbl_box_prms/SA/1" in proxy._control_key_state


def test_control_load_pending_keys(tmp_path):
    proxy = _make_proxy()
    proxy._control_pending_path = str(tmp_path / "pending.json")
    with open(proxy._control_pending_path, "w", encoding="utf-8") as fh:
        json.dump(["tbl_box_prms/SA/1"], fh)

    keys = proxy._control_load_pending_keys()
    assert keys == {"tbl_box_prms/SA/1"}


def test_control_update_pending_keys(tmp_path):
    proxy = _make_proxy()
    proxy._control_pending_path = str(tmp_path / "pending.json")
    proxy._control_pending_keys = set()

    proxy._control_update_pending_keys(request_key="tbl_box_prms/SA/1", state="queued")
    assert "tbl_box_prms/SA/1" in proxy._control_pending_keys

    proxy._control_update_pending_keys(request_key="tbl_box_prms/SA/1", state="done")
    assert "tbl_box_prms/SA/1" not in proxy._control_pending_keys
