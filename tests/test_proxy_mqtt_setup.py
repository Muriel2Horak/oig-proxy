"""Tests for MQTT setup helpers in proxy.py."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=useless-return

from unittest.mock import MagicMock

import pytest

import proxy as proxy_module
from control_pipeline import ControlPipeline
from models import ProxyMode
import mqtt_state_cache as msc_module
from mqtt_state_cache import MqttStateCache


class DummyLoop:
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._loop = DummyLoop()
    proxy._ctrl = MagicMock()
    proxy._ctrl.set_topic = "oig/control/set"
    proxy._ctrl.result_topic = "oig/control/result"
    proxy._ctrl.qos = 1
    proxy._ctrl.mqtt_enabled = True
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.device_id = "DEV1"
    proxy.mqtt_publisher.add_message_handler = MagicMock()
    msc = MqttStateCache.__new__(MqttStateCache)
    msc._proxy = proxy
    msc.last_values = {}
    msc.table_cache = {}
    msc.cache_device_id = None
    proxy._msc = msc
    proxy._ctrl.on_mqtt_message = MagicMock()
    return proxy


def test_setup_mqtt_state_cache_skips_without_loop():
    proxy = _make_proxy()
    proxy._loop = None
    proxy._msc.setup()
    proxy.mqtt_publisher.add_message_handler.assert_not_called()


def test_setup_mqtt_state_cache_skips_auto_device():
    proxy = _make_proxy()
    proxy.mqtt_publisher.device_id = "AUTO"
    proxy._msc.setup()
    proxy.mqtt_publisher.add_message_handler.assert_not_called()


def test_setup_mqtt_state_cache_registers_handler(monkeypatch):
    monkeypatch.setattr(msc_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()

    proxy._msc.setup()

    proxy.mqtt_publisher.add_message_handler.assert_called_once()
    args, kwargs = proxy.mqtt_publisher.add_message_handler.call_args
    assert kwargs["topic"] == "oig_local/DEV1/+/state"
    assert kwargs["qos"] == 1
    assert proxy._msc.cache_device_id == "DEV1"


def test_setup_mqtt_state_cache_handler_decodes(monkeypatch):
    monkeypatch.setattr(msc_module, "MQTT_NAMESPACE", "oig_local")
    proxy = _make_proxy()

    proxy._msc.setup()
    _, kwargs = proxy.mqtt_publisher.add_message_handler.call_args
    handler = kwargs["handler"]

    def fake_create_task(coro):
        coro.close()
        return None

    monkeypatch.setattr(proxy_module.asyncio, "create_task", fake_create_task)

    handler("oig_local/DEV1/tbl/state", b"\xff", 1, False)
    assert proxy._loop.calls


def test_setup_control_mqtt_registers_handler(monkeypatch):
    proxy = _make_proxy()

    # Replace MagicMock _ctrl with a real ControlPipeline for setup_mqtt() call
    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.set_topic = "oig/control/set"
    ctrl.result_topic = "oig/control/result"
    ctrl.qos = 1
    ctrl.mqtt_enabled = True
    proxy._ctrl = ctrl

    def fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        return None

    monkeypatch.setattr(proxy_module.asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    proxy._ctrl.setup_mqtt()

    proxy.mqtt_publisher.add_message_handler.assert_called_once()
    args, kwargs = proxy.mqtt_publisher.add_message_handler.call_args
    assert kwargs["topic"] == "oig/control/set"
    assert kwargs["qos"] == 1
