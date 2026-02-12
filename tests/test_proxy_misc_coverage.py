# pylint: disable=missing-module-docstring,missing-function-docstring,protected-access
# pylint: disable=too-few-public-methods,unused-argument,missing-class-docstring
# pylint: disable=use-implicit-booleaness-not-comparison
import asyncio
import logging
import time
from collections import defaultdict, deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


def _make_proxy() -> proxy_module.OIGProxy:
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "AUTO"
    proxy.mode = ProxyMode.ONLINE
    proxy._mode_value = None
    proxy._mode_device_id = None
    proxy._status_task = None
    proxy._full_refresh_task = None
    proxy._telemetry_task = None
    proxy._control_api = None
    proxy._telemetry_logs = deque()
    proxy._telemetry_log_window_s = 60
    proxy._telemetry_log_max = 1000
    proxy._telemetry_log_error = False
    proxy._telemetry_debug_windows_remaining = 0
    proxy._telemetry_force_logs_this_window = False
    proxy._telemetry_req_pending = defaultdict(deque)
    proxy._telemetry_stats = {}
    proxy._telemetry_error_context = deque()
    proxy._telemetry_cloud_ok_in_window = False
    proxy._telemetry_cloud_failed_in_window = False
    proxy._telemetry_box_seen_in_window = False
    proxy._background_tasks = set()
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.publish_data = AsyncMock()
    return proxy


def test_initialize_control_api_skips_when_disabled(monkeypatch):
    proxy = _make_proxy()
    monkeypatch.setattr(proxy_module, "CONTROL_API_PORT", 0)
    proxy._initialize_control_api()
    assert proxy._control_api is None


def test_initialize_control_api_handles_exception(monkeypatch):
    proxy = _make_proxy()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(proxy_module, "CONTROL_API_PORT", 123)
    monkeypatch.setattr(proxy_module, "ControlAPIServer", _boom)
    proxy._initialize_control_api()
    assert proxy._control_api is None


def test_start_background_tasks_skips_telemetry(monkeypatch):
    proxy = _make_proxy()
    async def _status_loop():
        return None

    async def _full_refresh():
        return None

    proxy._proxy_status_loop = _status_loop
    proxy._full_refresh_loop = _full_refresh
    proxy._init_telemetry = MagicMock()

    created = []

    def _fake_create_task(coro):
        created.append(coro)
        coro.close()
        return MagicMock(done=lambda: False)

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)
    monkeypatch.setattr(proxy_module, "TELEMETRY_ENABLED", False)

    proxy._start_background_tasks()
    assert len(created) == 2
    proxy._init_telemetry.assert_not_called()


@pytest.mark.asyncio
async def test_local_getactual_loop_disabled():
    proxy = _make_proxy()
    proxy._local_getactual_enabled = False

    class DummyWriter:
        def is_closing(self):
            return False

    await proxy._local_getactual_loop(DummyWriter(), conn_id=1)


@pytest.mark.asyncio
async def test_local_getactual_loop_waits_until_close():
    proxy = _make_proxy()
    proxy._local_getactual_enabled = True
    proxy.box_connected = False
    proxy._local_getactual_interval_s = 0

    class DummyWriter:
        def __init__(self):
            self.calls = 0

        def is_closing(self):
            self.calls += 1
            return self.calls > 1

    await proxy._local_getactual_loop(DummyWriter(), conn_id=1)


def test_should_try_cloud_hybrid_retry(monkeypatch):
    proxy = _make_proxy()
    proxy._configured_mode = "hybrid"
    proxy._hybrid_in_offline = True
    proxy._hybrid_retry_interval = 10.0
    proxy._hybrid_last_offline_time = 100.0

    monkeypatch.setattr(time, "time", lambda: 105.0)
    assert proxy._should_try_cloud() is False

    monkeypatch.setattr(time, "time", lambda: 111.0)
    assert proxy._should_try_cloud() is True


@pytest.mark.asyncio
async def test_publish_mode_if_ready_defers_without_device_id():
    proxy = _make_proxy()
    proxy._mode_value = 1
    proxy.device_id = "AUTO"
    proxy._mode_device_id = None

    await proxy._publish_mode_if_ready(reason="test")
    proxy.mqtt_publisher.publish_data.assert_not_called()


@pytest.mark.asyncio
async def test_publish_mode_if_ready_logs_reason():
    proxy = _make_proxy()
    proxy._mode_value = 2
    proxy.device_id = "DEV1"

    await proxy._publish_mode_if_ready(reason="startup")
    proxy.mqtt_publisher.publish_data.assert_called_once()


@pytest.mark.asyncio
async def test_handle_mode_update_invalid_values(monkeypatch):
    proxy = _make_proxy()
    proxy._publish_mode_if_ready = AsyncMock()
    monkeypatch.setattr(proxy_module, "save_mode_state", MagicMock())

    await proxy._handle_mode_update("bad", "DEV1", "source")
    await proxy._handle_mode_update(9, "DEV1", "source")
    proxy._publish_mode_if_ready.assert_not_called()


def test_telemetry_record_request_trims_queue():
    proxy = _make_proxy()
    proxy._telemetry_record_request(None, 1)
    for _ in range(1001):
        proxy._telemetry_record_request("tbl_actual", 1)
    assert len(proxy._telemetry_req_pending[1]) == 1000


def test_telemetry_record_response_mode_variants():
    proxy = _make_proxy()
    proxy.mode = "HYBRID"
    proxy._telemetry_record_response("<Result>ACK</Result>", source="cloud", conn_id=1)
    assert ("unmatched", "cloud", "hybrid") in proxy._telemetry_stats

    proxy.mode = None
    proxy._mode_value = 3
    proxy._telemetry_record_response("<Result>ACK</Result>", source="cloud", conn_id=2)
    assert ("unmatched", "cloud", "offline") in proxy._telemetry_stats


def test_telemetry_flush_stats_empty():
    proxy = _make_proxy()
    items = proxy._telemetry_flush_stats()
    assert items == []


def test_record_error_context_fallback():
    proxy = _make_proxy()
    proxy._utc_iso = MagicMock(return_value="2024-01-01T00:00:00Z")
    proxy._snapshot_logs = MagicMock(return_value=[])

    proxy._record_error_context(event_type="test", details={"bad": {1}})
    assert "detail" in proxy._telemetry_error_context[0]["details"]


def test_record_log_entry_skips_and_handles_exception():
    proxy = _make_proxy()
    proxy._telemetry_log_error = True
    record = logging.LogRecord("test", logging.INFO, "file", 1, "msg", None, None)
    proxy._record_log_entry(record)
    assert proxy._telemetry_logs == deque()

    proxy._telemetry_log_error = False
    proxy._telemetry_debug_windows_remaining = 1
    bad_record = logging.LogRecord("test", logging.INFO, "file", 1, "msg", None, None)
    bad_record.getMessage = MagicMock(side_effect=RuntimeError("boom"))
    proxy._record_log_entry(bad_record)
    assert proxy._telemetry_log_error is False
