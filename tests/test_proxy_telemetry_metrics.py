"""Tests for proxy telemetry metrics helpers."""

import time
from collections import Counter, deque
from unittest.mock import MagicMock

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy._configured_mode = "online"
    proxy.device_id = "DEV1"
    proxy._start_time = time.time() - 120
    proxy._telemetry_interval_s = 300
    proxy._telemetry_logs = deque()
    proxy._telemetry_log_window_s = 60
    proxy._telemetry_debug_windows_remaining = 0
    proxy._telemetry_force_logs_this_window = False
    proxy._telemetry_box_seen_in_window = False
    proxy._telemetry_cloud_ok_in_window = False
    proxy._telemetry_cloud_failed_in_window = False
    proxy._telemetry_cloud_eof_short_in_window = False
    proxy._telemetry_box_sessions = deque()
    proxy._telemetry_cloud_sessions = deque()
    proxy._telemetry_hybrid_sessions = deque()
    proxy._telemetry_offline_events = deque()
    proxy._telemetry_tbl_events = deque()
    proxy._telemetry_error_context = deque()
    proxy._telemetry_stats = {}
    proxy._set_commands_buffer = []
    proxy.stats = {
        "frames_received": 3,
        "frames_forwarded": 2,
    }
    proxy.cloud_connects = 1
    proxy.cloud_disconnects = 0
    proxy.cloud_timeouts = 0
    proxy.cloud_errors = 0
    proxy.cloud_session_connected = False
    proxy.box_connected = False
    proxy._active_box_peer = None
    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.is_ready = MagicMock(return_value=True)
    proxy.mqtt_publisher.queue = MagicMock()
    proxy.mqtt_publisher.queue.size = MagicMock(return_value=2)
    proxy._build_device_specific_metrics = MagicMock(return_value={"x": 1})
    proxy._utc_iso = MagicMock(return_value="2024-01-01T00:00:00Z")
    return proxy


def test_get_telemetry_logs_includes_and_clears():
    proxy = _make_proxy()
    proxy._telemetry_logs.append({"msg": "a"})
    proxy._flush_log_buffer = MagicMock(return_value=[{"msg": "a"}])

    logs = proxy._get_telemetry_logs(debug_active=False, include_logs=True)
    assert logs == [{"msg": "a"}]
    proxy._flush_log_buffer.assert_called_once()


def test_get_telemetry_logs_excludes_and_clears():
    proxy = _make_proxy()
    proxy._telemetry_logs.append({"msg": "a"})
    proxy._flush_log_buffer = MagicMock(return_value=[{"msg": "a"}])

    logs = proxy._get_telemetry_logs(debug_active=True, include_logs=False)
    assert logs == []
    assert proxy._telemetry_logs == deque()
    assert proxy._telemetry_debug_windows_remaining == -1


def test_collect_and_clear_window_metrics_clears():
    proxy = _make_proxy()
    proxy._telemetry_box_sessions.append({"a": 1})
    proxy._telemetry_cloud_sessions.append({"b": 2})
    proxy._telemetry_hybrid_sessions.append({"c": 3})
    proxy._telemetry_offline_events.append({"d": 4})
    proxy._telemetry_tbl_events.append({"e": 5})
    proxy._telemetry_error_context.append({"f": 6})
    proxy._telemetry_stats[("tbl", "cloud", "online")] = Counter(
        req_count=1,
        resp_ack=1,
        resp_end=0,
        resp_weather=0,
        resp_nack=0,
        resp_ack_getall=0,
        resp_ack_getactual=0,
        resp_other=0,
    )

    metrics = proxy._collect_and_clear_window_metrics(logs=[{"msg": "x"}])
    assert metrics["box_sessions"] == [{"a": 1}]
    assert metrics["cloud_sessions"] == [{"b": 2}]
    assert metrics["offline_events"] == [{"d": 4}]
    assert proxy._telemetry_box_sessions == deque()
    assert proxy._telemetry_cloud_sessions == deque()
    assert proxy._telemetry_hybrid_sessions == deque()
    assert proxy._telemetry_offline_events == deque()
    assert proxy._telemetry_tbl_events == deque()
    assert proxy._telemetry_error_context == deque()


def test_collect_telemetry_metrics_basic():
    proxy = _make_proxy()
    proxy._get_box_connected_window_status = MagicMock(return_value=False)
    proxy._get_cloud_online_window_status = MagicMock(return_value=False)
    proxy._should_include_telemetry_logs = MagicMock(return_value=True)
    proxy._get_telemetry_logs = MagicMock(return_value=[{"msg": "x"}])
    proxy._collect_and_clear_window_metrics = MagicMock(return_value={"window": True})

    metrics = proxy._collect_telemetry_metrics()
    assert metrics["frames_received"] == 3
    assert metrics["frames_forwarded"] == 2
    assert metrics["mqtt_ok"] is True
    assert metrics["mqtt_queue"] == 2
    assert metrics["window_metrics"] == {"window": True}
    assert metrics["x"] == 1


def test_record_box_and_cloud_session_end():
    proxy = _make_proxy()
    proxy._box_connected_since_epoch = time.time() - 5
    proxy._cloud_connected_since_epoch = time.time() - 2
    proxy._telemetry_cloud_ok_in_window = False

    proxy._record_box_session_end(reason="disconnect", peer="1.2.3.4")
    assert len(proxy._telemetry_box_sessions) == 1
    assert proxy._box_connected_since_epoch is None

    proxy._record_cloud_session_end(reason="eof")
    assert len(proxy._telemetry_cloud_sessions) == 1
    assert proxy._cloud_connected_since_epoch is None


def test_record_offline_event_defaults():
    proxy = _make_proxy()
    proxy._record_offline_event(reason=None, local_ack=None)
    assert proxy._telemetry_offline_events[0]["reason"] == "unknown"
    assert proxy._telemetry_offline_events[0]["local_ack"] is False
