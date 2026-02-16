"""Tests for proxy control flow helper methods."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=consider-using-with

import asyncio
import tempfile
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from control_pipeline import ControlPipeline
from control_settings import ControlSettings
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
    proxy.publish_proxy_status = AsyncMock()

    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.queue = deque()
    ctrl.inflight = None
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.quiet_task = None
    ctrl.pending_keys = set()
    ctrl.post_drain_refresh_pending = False
    ctrl.status_prefix = "oig/control/status"
    ctrl.qos = 1
    ctrl.retain = False
    ctrl.status_retain = False
    ctrl.result_topic = "oig/control/result"
    ctrl.log_enabled = False
    ctrl.log_path = tempfile.NamedTemporaryFile(delete=False).name
    ctrl.pending_path = tempfile.NamedTemporaryFile(delete=False).name
    ctrl.mqtt_enabled = False
    ctrl.set_topic = ""
    ctrl.box_ready_s = 0.0
    ctrl.ack_timeout_s = 10.0
    ctrl.applied_timeout_s = 30.0
    ctrl.mode_quiet_s = 0.0
    ctrl.whitelist = {}
    ctrl.max_attempts = 5
    ctrl.retry_delay_s = 120.0
    ctrl.session_id = "test"
    ctrl.lock = asyncio.Lock()
    ctrl.retry_task = None
    ctrl.last_result = None
    ctrl.key_state = {}
    proxy._ctrl = ctrl
    cs = ControlSettings.__new__(ControlSettings)
    cs._proxy = proxy
    cs.pending = None
    cs.pending_frame = None
    cs.set_commands_buffer = []
    proxy._cs = cs
    return proxy


def test_control_drop_post_drain_sa_locked():
    proxy = _make_proxy()
    proxy._ctrl.queue = deque([
        {"tx_key": "post_drain_sa_refresh"},
        {"tx_key": "other"},
    ])
    removed = proxy._ctrl.drop_post_drain_sa_locked()
    assert removed == [{"tx_key": "post_drain_sa_refresh"}]
    assert list(proxy._ctrl.queue) == [{"tx_key": "other"}]


def test_control_cancel_post_drain_sa_inflight_locked():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "_internal": "post_drain_sa",
    }
    proxy._ctrl.ack_task = MagicMock(done=MagicMock(return_value=False))
    proxy._ctrl.applied_task = MagicMock(done=MagicMock(return_value=True))
    proxy._ctrl.quiet_task = MagicMock(done=MagicMock(return_value=False))

    tx = proxy._ctrl.cancel_post_drain_sa_inflight_locked()
    assert tx is not None
    assert proxy._ctrl.inflight is None
    assert proxy._ctrl.ack_task is None
    assert proxy._ctrl.quiet_task is None


def test_control_cancel_post_drain_sa_inflight_locked_noop():
    proxy = _make_proxy()
    proxy._ctrl.inflight = {"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}
    assert proxy._ctrl.cancel_post_drain_sa_inflight_locked() is None


def test_parse_setting_event():
    content = "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]"
    result = ControlSettings.parse_setting_event(content)
    assert result == ("tbl_invertor_prm1", "AAC_MAX_CHRG", "50.0", "120.0")
    assert ControlSettings.parse_setting_event("invalid") is None


@pytest.mark.asyncio
async def test_control_publish_restart_errors():
    proxy = _make_proxy()
    proxy._ctrl.pending_keys = {"tbl_box_prms/SA/1"}
    proxy._ctrl.publish_result = AsyncMock()
    proxy._ctrl.store_pending_keys = MagicMock()

    await proxy._ctrl.publish_restart_errors()

    proxy._ctrl.publish_result.assert_called_once()
    assert proxy._ctrl.pending_keys == set()


@pytest.mark.asyncio
async def test_handle_setting_event_records_and_publishes():
    proxy = _make_proxy()
    proxy._cs.set_commands_buffer = []
    proxy._ctrl.publish_setting_event_state = AsyncMock()

    await proxy._cs.handle_setting_event(
        parsed={"Type": "Setting", "Content": "Remotely : tbl_box_prms / SA: [0]->[1]"},
        table_name="tbl_events",
        device_id="DEV1",
    )

    assert proxy._cs.set_commands_buffer
    proxy._ctrl.publish_setting_event_state.assert_called_once()
