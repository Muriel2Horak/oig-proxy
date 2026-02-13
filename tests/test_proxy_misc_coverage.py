# pylint: disable=missing-module-docstring,missing-function-docstring,protected-access
# pylint: disable=too-few-public-methods,unused-argument,missing-class-docstring
# pylint: disable=use-implicit-booleaness-not-comparison
import asyncio
import logging
import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from hybrid_mode import HybridModeManager
from models import ProxyMode
from telemetry_collector import TelemetryCollector


def _make_proxy() -> proxy_module.OIGProxy:
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "AUTO"
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
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
    proxy._status_task = None
    proxy._full_refresh_task = None
    proxy._telemetry_task = None
    proxy._control_api = None
    proxy._tc = MagicMock()
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

    created = []

    def _fake_create_task(coro):
        created.append(coro)
        coro.close()
        return MagicMock(done=lambda: False)

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)
    monkeypatch.setattr(proxy_module, "TELEMETRY_ENABLED", False)

    proxy._start_background_tasks()
    assert len(created) == 2
    proxy._tc.init.assert_not_called()


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
    proxy._hm = HybridModeManager(proxy)
    proxy._hm.configured_mode = "hybrid"
    proxy._hm.in_offline = True
    proxy._hm.retry_interval = 10.0
    proxy._hm.last_offline_time = 100.0

    monkeypatch.setattr(time, "time", lambda: 105.0)
    assert proxy._hm.should_try_cloud() is False

    monkeypatch.setattr(time, "time", lambda: 111.0)
    assert proxy._hm.should_try_cloud() is True


@pytest.mark.asyncio
async def test_publish_mode_if_ready_defers_without_device_id():
    proxy = _make_proxy()
    proxy._mp.mode_value = 1
    proxy.device_id = "AUTO"
    proxy._mp.mode_device_id = None

    await proxy._publish_mode_if_ready(reason="test")
    proxy.mqtt_publisher.publish_data.assert_not_called()


@pytest.mark.asyncio
async def test_publish_mode_if_ready_logs_reason():
    proxy = _make_proxy()
    proxy._mp.mode_value = 2
    proxy.device_id = "DEV1"

    await proxy._publish_mode_if_ready(reason="startup")
    proxy.mqtt_publisher.publish_data.assert_called_once()


@pytest.mark.asyncio
async def test_handle_mode_update_invalid_values(monkeypatch):
    proxy = _make_proxy()
    proxy._publish_mode_if_ready = AsyncMock()
    import mode_persistence as mp_module
    monkeypatch.setattr(mp_module, "save_mode_state", MagicMock())

    await proxy._handle_mode_update("bad", "DEV1", "source")
    await proxy._handle_mode_update(9, "DEV1", "source")
    proxy._publish_mode_if_ready.assert_not_called()


def test_telemetry_record_request_trims_queue():
    mock_proxy = MagicMock()
    mock_proxy._hm = MagicMock()
    mock_proxy._hm.mode = ProxyMode.ONLINE
    tc = TelemetryCollector(mock_proxy, interval_s=300)
    tc.record_request(None, 1)
    for _ in range(1001):
        tc.record_request("tbl_actual", 1)
    assert len(tc.req_pending[1]) == 1000


def test_telemetry_record_response_mode_variants():
    mock_proxy = MagicMock()
    mock_proxy.mode = "HYBRID"
    tc = TelemetryCollector(mock_proxy, interval_s=300)
    tc.record_response("<Result>ACK</Result>", source="cloud", conn_id=1)
    assert ("unmatched", "cloud", "hybrid") in tc.stats

    mock_proxy.mode = None
    mock_proxy._mp.mode_value = 3
    tc.record_response("<Result>ACK</Result>", source="cloud", conn_id=2)
    assert ("unmatched", "cloud", "offline") in tc.stats


def test_telemetry_flush_stats_empty():
    mock_proxy = MagicMock()
    tc = TelemetryCollector(mock_proxy, interval_s=300)
    items = tc._flush_stats()
    assert items == []


def test_record_error_context_fallback():
    mock_proxy = MagicMock()
    tc = TelemetryCollector(mock_proxy, interval_s=300)

    tc.record_error_context(event_type="test", details={"bad": {1}})
    assert "detail" in tc.error_context[0]["details"]


def test_record_log_entry_skips_and_handles_exception():
    mock_proxy = MagicMock()
    tc = TelemetryCollector(mock_proxy, interval_s=300)
    tc.log_error = True
    record = logging.LogRecord("test", logging.INFO, "file", 1, "msg", None, None)
    tc.record_log_entry(record)
    assert tc.logs == deque()

    tc.log_error = False
    tc.debug_windows_remaining = 1
    bad_record = logging.LogRecord("test", logging.INFO, "file", 1, "msg", None, None)
    bad_record.getMessage = MagicMock(side_effect=RuntimeError("boom"))
    tc.record_log_entry(bad_record)
    assert tc.log_error is False
