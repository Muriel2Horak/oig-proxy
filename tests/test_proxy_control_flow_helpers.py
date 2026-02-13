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
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._control_queue = deque()
    proxy._control_inflight = None
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_quiet_task = None
    proxy._control_pending_keys = set()
    proxy._control_post_drain_refresh_pending = False
    proxy._control_status_prefix = "oig/control/status"
    proxy._control_qos = 1
    proxy._control_retain = False
    proxy._control_status_retain = False
    proxy._control_result_topic = "oig/control/result"
    proxy._control_log_enabled = False
    proxy._control_log_path = tempfile.NamedTemporaryFile(delete=False).name
    proxy._control_pending_path = tempfile.NamedTemporaryFile(delete=False).name
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
    proxy.publish_proxy_status = AsyncMock()
    return proxy


def test_control_drop_post_drain_sa_locked():
    proxy = _make_proxy()
    proxy._control_queue = deque([
        {"tx_key": "post_drain_sa_refresh"},
        {"tx_key": "other"},
    ])
    removed = proxy._control_drop_post_drain_sa_locked()
    assert removed == [{"tx_key": "post_drain_sa_refresh"}]
    assert list(proxy._control_queue) == [{"tx_key": "other"}]


def test_control_cancel_post_drain_sa_inflight_locked():
    proxy = _make_proxy()
    proxy._control_inflight = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "_internal": "post_drain_sa",
    }
    proxy._control_ack_task = MagicMock(done=MagicMock(return_value=False))
    proxy._control_applied_task = MagicMock(done=MagicMock(return_value=True))
    proxy._control_quiet_task = MagicMock(done=MagicMock(return_value=False))

    tx = proxy._control_cancel_post_drain_sa_inflight_locked()
    assert tx is not None
    assert proxy._control_inflight is None
    assert proxy._control_ack_task is None
    assert proxy._control_quiet_task is None


def test_control_cancel_post_drain_sa_inflight_locked_noop():
    proxy = _make_proxy()
    proxy._control_inflight = {"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}
    assert proxy._control_cancel_post_drain_sa_inflight_locked() is None


def test_parse_setting_event():
    content = "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]"
    result = proxy_module.OIGProxy._parse_setting_event(content)
    assert result == ("tbl_invertor_prm1", "AAC_MAX_CHRG", "50.0", "120.0")
    assert proxy_module.OIGProxy._parse_setting_event("invalid") is None


@pytest.mark.asyncio
async def test_control_publish_restart_errors():
    proxy = _make_proxy()
    proxy._control_pending_keys = {"tbl_box_prms/SA/1"}
    proxy._control_publish_result = AsyncMock()
    proxy._control_store_pending_keys = MagicMock()

    await proxy._control_publish_restart_errors()

    proxy._control_publish_result.assert_called_once()
    assert proxy._control_pending_keys == set()


@pytest.mark.asyncio
async def test_handle_setting_event_records_and_publishes():
    proxy = _make_proxy()
    proxy._set_commands_buffer = []
    proxy._publish_setting_event_state = AsyncMock()

    await proxy._handle_setting_event(
        parsed={"Type": "Setting", "Content": "Remotely : tbl_box_prms / SA: [0]->[1]"},
        table_name="tbl_events",
        device_id="DEV1",
    )

    assert proxy._set_commands_buffer
    proxy._publish_setting_event_state.assert_called_once()
