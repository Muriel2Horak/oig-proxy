"""Tests for control MQTT message handling."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy.device_id = "DEV1"
    proxy._control_lock = asyncio.Lock()
    proxy._control_inflight = None
    proxy._control_queue = deque()
    proxy._control_publish_result = AsyncMock()
    proxy._control_maybe_start_next = AsyncMock()
    proxy._validate_control_request = AsyncMock(return_value={"tx_id": "1"})
    proxy._build_control_tx = MagicMock(return_value={
        "tx_id": "1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
        "_canon": "1",
    })
    proxy._check_whitelist_and_normalize = AsyncMock(return_value=(True, None))
    proxy._handle_duplicate_or_noop = AsyncMock(return_value=False)
    proxy._enqueue_control_tx = AsyncMock(return_value=(None, []))
    return proxy


@pytest.mark.asyncio
async def test_control_on_mqtt_message_missing_fields():
    proxy = _make_proxy()
    proxy._build_control_tx = MagicMock(return_value=None)

    await proxy._control_on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._control_publish_result.assert_called_once()
    proxy._control_maybe_start_next.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_mqtt_message_not_allowed():
    proxy = _make_proxy()
    proxy._check_whitelist_and_normalize = AsyncMock(return_value=(False, "not_allowed"))

    await proxy._control_on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._control_publish_result.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_mqtt_message_duplicate():
    proxy = _make_proxy()
    proxy._handle_duplicate_or_noop = AsyncMock(return_value=True)

    await proxy._control_on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._enqueue_control_tx.assert_not_called()


@pytest.mark.asyncio
async def test_control_on_mqtt_message_success():
    proxy = _make_proxy()

    await proxy._control_on_mqtt_message(topic="t", payload=b"{}", retain=False)

    proxy._enqueue_control_tx.assert_called_once()
    proxy._control_publish_result.assert_called_with(
        tx=proxy._build_control_tx.return_value, status="accepted"
    )
    proxy._control_maybe_start_next.assert_called_once()
