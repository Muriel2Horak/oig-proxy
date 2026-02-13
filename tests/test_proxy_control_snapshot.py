"""Tests for control state publishing."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=no-member,use-implicit-booleaness-not-comparison,wrong-import-position,too-many-statements

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import proxy as proxy_module
from control_pipeline import ControlPipeline
from models import ProxyMode


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
    proxy._msc = MagicMock()
    proxy._msc.table_cache = {}
    proxy._msc.last_values = {}
    proxy._msc.cache_device_id = None
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.device_id = "DEV1"
    proxy.mqtt_publisher.state_topic = MagicMock(return_value="oig/state")
    proxy.mqtt_publisher.set_cached_payload = MagicMock()
    proxy.mqtt_publisher.publish_raw = AsyncMock()

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

    proxy._ctrl = ctrl
    return proxy


import pytest


@pytest.mark.asyncio
async def test_publish_setting_event_state(monkeypatch):
    proxy = _make_proxy()
    proxy._ctrl.coerce_value = MagicMock(return_value="1")
    proxy._ctrl.map_optimistic_value = MagicMock(return_value="ON")
    proxy._mp.prms_tables["tbl_box_prms"] = {"MODE": "0"}
    proxy.mqtt_publisher.get_cached_payload = MagicMock(return_value=None)
    proxy.mqtt_publisher.map_data_for_publish = MagicMock(return_value=({"MODE": "ON"}, 1))

    await proxy._ctrl.publish_setting_event_state(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="1",
        device_id="DEV1",
        source="tbl_events",
    )

    proxy.mqtt_publisher.set_cached_payload.assert_called_once()
    proxy.mqtt_publisher.publish_raw.assert_called_once()
    proxy.mqtt_publisher.map_data_for_publish.assert_called_once()
