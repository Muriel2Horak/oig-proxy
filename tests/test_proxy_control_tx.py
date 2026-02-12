"""Tests for control TX normalization and duplicate handling."""

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
    proxy._control_whitelist = {"tbl_box_prms": {"SA", "MODE"}}
    proxy._control_key_state = {}
    proxy._control_lock = asyncio.Lock()
    proxy._control_inflight = None
    proxy._control_queue = deque()
    proxy._last_values = {}
    proxy._control_publish_result = AsyncMock()
    proxy._control_normalize_value = MagicMock(return_value=("1", None))
    return proxy


def test_build_control_tx_ok():
    proxy = _make_proxy()
    data = {"tx_id": "1", "tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": 1}
    tx = proxy._build_control_tx(data)
    assert tx is not None
    assert tx["tx_id"] == "1"
    assert tx["tbl_name"] == "tbl_box_prms"
    assert tx["tbl_item"] == "SA"
    assert tx["new_value"] == 1
    assert tx["confirm"] == "New"
    assert tx["_attempts"] == 0


def test_build_control_tx_missing_fields():
    proxy = _make_proxy()
    assert proxy._build_control_tx({"tx_id": ""}) is None
    assert proxy._build_control_tx({"tx_id": "1", "tbl_name": "", "tbl_item": "SA"}) is None
    assert proxy._build_control_tx({"tx_id": "1", "tbl_name": "tbl", "tbl_item": ""}) is None
    assert proxy._build_control_tx({"tx_id": "1", "tbl_name": "tbl", "tbl_item": "SA"}) is None


@pytest.mark.asyncio
async def test_check_whitelist_and_normalize_not_allowed():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SB", "new_value": "1"}
    ok, err = await proxy._check_whitelist_and_normalize(tx)
    assert ok is False
    assert err == "not_allowed"
    proxy._control_publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_check_whitelist_and_normalize_invalid_value():
    proxy = _make_proxy()
    proxy._control_normalize_value = MagicMock(return_value=(None, "bad_value"))
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": "x"}
    ok, err = await proxy._check_whitelist_and_normalize(tx)
    assert ok is False
    assert err == "bad_value"
    proxy._control_publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_check_whitelist_and_normalize_ok():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "new_value": "1"}
    ok, err = await proxy._check_whitelist_and_normalize(tx)
    assert ok is True
    assert err is None
    assert tx["new_value"] == "1"
    assert tx["_canon"] == "1"


@pytest.mark.asyncio
async def test_handle_duplicate_or_noop_active_state():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "_canon": "1"}
    proxy._control_key_state["key"] = {"state": "queued"}
    handled = await proxy._handle_duplicate_or_noop(tx, "key")
    assert handled is True
    proxy._control_publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_handle_duplicate_or_noop_noop_value():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "_canon": "1"}
    proxy._last_values[("tbl_box_prms", "SA")] = "1"
    handled = await proxy._handle_duplicate_or_noop(tx, "key")
    assert handled is True
    proxy._control_publish_result.assert_called_once()


@pytest.mark.asyncio
async def test_handle_duplicate_or_noop_false():
    proxy = _make_proxy()
    tx = {"tbl_name": "tbl_box_prms", "tbl_item": "SA", "_canon": "2"}
    proxy._last_values[("tbl_box_prms", "SA")] = "1"
    handled = await proxy._handle_duplicate_or_noop(tx, "key")
    assert handled is False
