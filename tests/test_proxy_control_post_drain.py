"""Tests for post-drain control refresh helpers."""

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
    proxy._control_lock = asyncio.Lock()
    proxy._control_queue = deque()
    proxy._control_inflight = None
    proxy._control_post_drain_refresh_pending = False
    proxy._control_enqueue_internal_sa = AsyncMock()
    return proxy


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_skip_no_tx():
    proxy = _make_proxy()
    await proxy._control_maybe_queue_post_drain_refresh(last_tx=None)
    proxy._control_enqueue_internal_sa.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_skip_sa():
    proxy = _make_proxy()
    last_tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA"}
    await proxy._control_maybe_queue_post_drain_refresh(last_tx=last_tx)
    proxy._control_enqueue_internal_sa.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_skip_inflight():
    proxy = _make_proxy()
    proxy._control_inflight = {"tx_id": "1"}
    proxy._control_post_drain_refresh_pending = True
    last_tx = {"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}
    await proxy._control_maybe_queue_post_drain_refresh(last_tx=last_tx)
    proxy._control_enqueue_internal_sa.assert_not_called()


@pytest.mark.asyncio
async def test_control_maybe_queue_post_drain_refresh_enqueue():
    proxy = _make_proxy()
    proxy._control_post_drain_refresh_pending = True
    last_tx = {"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}
    await proxy._control_maybe_queue_post_drain_refresh(last_tx=last_tx)
    proxy._control_enqueue_internal_sa.assert_called_once_with(reason="queue_drained")


@pytest.mark.asyncio
async def test_control_enqueue_internal_sa():
    proxy = _make_proxy()
    proxy._control_enqueue_internal_sa = proxy_module.OIGProxy._control_enqueue_internal_sa
    proxy._control_build_request_key = MagicMock(return_value="tbl_box_prms:SA")
    proxy._control_publish_result = AsyncMock()
    proxy._control_maybe_start_next = AsyncMock()
    proxy._control_inflight = None

    await proxy._control_enqueue_internal_sa(proxy, reason="queue_drained")
    assert proxy._control_queue
    proxy._control_publish_result.assert_called_once()
    proxy._control_maybe_start_next.assert_called_once()
