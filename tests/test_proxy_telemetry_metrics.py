"""Tests for proxy telemetry metrics helpers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import time
from collections import Counter, deque
from unittest.mock import MagicMock

from models import ProxyMode
from telemetry_collector import TelemetryCollector


def _make_proxy_and_tc():
    mock_proxy = MagicMock()
    mock_proxy._hm = MagicMock()
    mock_proxy._hm.mode = ProxyMode.ONLINE
    mock_proxy._hm.configured_mode = "online"
    mock_proxy.device_id = "DEV1"
    mock_proxy._start_time = time.time() - 120
    mock_proxy.stats = {
        "frames_received": 3,
        "frames_forwarded": 2,
    }
    mock_proxy._set_commands_buffer = []
    mock_proxy._cf = MagicMock()
    mock_proxy._cf.connects = 1
    mock_proxy._cf.disconnects = 0
    mock_proxy._cf.timeouts = 0
    mock_proxy._cf.errors = 0
    mock_proxy._cf.session_connected = False
    mock_proxy._cf.connected_since_epoch = None
    mock_proxy.box_connected = False
    mock_proxy._active_box_peer = None
    mock_proxy.mqtt_publisher = MagicMock()
    mock_proxy.mqtt_publisher.is_ready = MagicMock(return_value=True)
    mock_proxy.mqtt_publisher.queue = MagicMock()
    mock_proxy.mqtt_publisher.queue.size = MagicMock(return_value=2)
    mock_proxy._box_connected_since_epoch = None
    mock_proxy._hm.state = None
    mock_proxy._hm.state_since_epoch = None
    mock_proxy._hm.last_offline_reason = None
    tc = TelemetryCollector(mock_proxy, interval_s=300)
    return mock_proxy, tc


def test_get_telemetry_logs_includes_and_clears():
    mock_proxy, tc = _make_proxy_and_tc()
    tc.logs.append({"msg": "a"})
    tc._flush_log_buffer = MagicMock(return_value=[{"msg": "a"}])

    logs = tc._get_telemetry_logs(debug_active=False, include_logs=True)
    assert logs == [{"msg": "a"}]
    tc._flush_log_buffer.assert_called_once()


def test_get_telemetry_logs_excludes_and_clears():
    mock_proxy, tc = _make_proxy_and_tc()
    tc.logs.append({"msg": "a"})
    tc._flush_log_buffer = MagicMock(return_value=[{"msg": "a"}])

    logs = tc._get_telemetry_logs(debug_active=True, include_logs=False)
    assert logs == []
    assert tc.logs == deque()
    assert tc.debug_windows_remaining == -1


def test_collect_and_clear_window_metrics_clears():
    mock_proxy, tc = _make_proxy_and_tc()
    tc.box_sessions.append({"a": 1})
    tc.cloud_sessions.append({"b": 2})
    tc.hybrid_sessions.append({"c": 3})
    tc.offline_events.append({"d": 4})
    tc.tbl_events.append({"e": 5})
    tc.error_context.append({"f": 6})
    tc.stats[("tbl", "cloud", "online")] = Counter(
        req_count=1,
        resp_ack=1,
        resp_end=0,
        resp_weather=0,
        resp_nack=0,
        resp_ack_getall=0,
        resp_ack_getactual=0,
        resp_other=0,
    )

    metrics = tc._collect_and_clear_window_metrics(logs=[{"msg": "x"}])
    assert metrics["box_sessions"] == [{"a": 1}]
    assert metrics["cloud_sessions"] == [{"b": 2}]
    assert metrics["offline_events"] == [{"d": 4}]
    assert tc.box_sessions == deque()
    assert tc.cloud_sessions == deque()
    assert tc.hybrid_sessions == deque()
    assert tc.offline_events == deque()
    assert tc.tbl_events == deque()
    assert tc.error_context == deque()


def test_collect_telemetry_metrics_basic():
    mock_proxy, tc = _make_proxy_and_tc()
    tc._get_box_connected_window_status = MagicMock(return_value=False)
    tc._get_cloud_online_window_status = MagicMock(return_value=False)
    tc._should_include_logs = MagicMock(return_value=True)
    tc._get_telemetry_logs = MagicMock(return_value=[{"msg": "x"}])
    tc._collect_and_clear_window_metrics = MagicMock(return_value={"window": True})
    tc._build_device_specific_metrics = MagicMock(return_value={"x": 1})

    metrics = tc.collect_metrics()
    assert metrics["frames_received"] == 3
    assert metrics["frames_forwarded"] == 2
    assert metrics["mqtt_ok"] is True
    assert metrics["mqtt_queue"] == 2
    assert metrics["window_metrics"] == {"window": True}
    assert metrics["x"] == 1


def test_record_box_and_cloud_session_end():
    mock_proxy, tc = _make_proxy_and_tc()
    mock_proxy._box_connected_since_epoch = time.time() - 5
    mock_proxy._cf.connected_since_epoch = time.time() - 2
    tc.cloud_ok_in_window = False

    tc.record_box_session_end(reason="disconnect", peer="1.2.3.4")
    assert len(tc.box_sessions) == 1
    assert mock_proxy._box_connected_since_epoch is None

    tc.record_cloud_session_end(reason="eof")
    assert len(tc.cloud_sessions) == 1
    assert mock_proxy._cf.connected_since_epoch is None


def test_record_offline_event_defaults():
    mock_proxy, tc = _make_proxy_and_tc()
    tc.record_offline_event(reason=None, local_ack=None)
    assert tc.offline_events[0]["reason"] == "unknown"
    assert tc.offline_events[0]["local_ack"] is False
