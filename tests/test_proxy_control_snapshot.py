"""Tests for control state publishing."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=no-member,use-implicit-booleaness-not-comparison,wrong-import-position

from unittest.mock import AsyncMock, MagicMock

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._prms_device_id = None
    proxy._prms_tables = {}
    proxy._table_cache = {}
    proxy._should_persist_table = MagicMock(return_value=True)
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.device_id = "DEV1"
    proxy.mqtt_publisher.state_topic = MagicMock(return_value="oig/state")
    proxy.mqtt_publisher.set_cached_payload = MagicMock()
    proxy.mqtt_publisher.publish_raw = AsyncMock()
    return proxy


import pytest


@pytest.mark.asyncio
async def test_publish_setting_event_state(monkeypatch):
    proxy = _make_proxy()
    proxy._control_coerce_value = MagicMock(return_value="1")
    proxy._control_map_optimistic_value = MagicMock(return_value="ON")
    proxy._prms_tables["tbl_box_prms"] = {"MODE": "0"}
    proxy.mqtt_publisher.get_cached_payload = MagicMock(return_value=None)
    proxy.mqtt_publisher.map_data_for_publish = MagicMock(return_value=({"MODE": "ON"}, 1))

    await proxy._publish_setting_event_state(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="1",
        device_id="DEV1",
        source="tbl_events",
    )

    proxy.mqtt_publisher.set_cached_payload.assert_called_once()
    proxy.mqtt_publisher.publish_raw.assert_called_once()
    proxy.mqtt_publisher.map_data_for_publish.assert_called_once()
