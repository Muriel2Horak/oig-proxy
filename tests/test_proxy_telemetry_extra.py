"""Additional coverage tests for proxy telemetry helpers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=use-implicit-booleaness-not-comparison

from collections import Counter, deque
import time

import proxy as proxy_module
from models import ProxyMode


def _make_proxy_stub():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy._telemetry_stats = {}
    proxy._telemetry_error_context = deque()
    proxy._telemetry_tbl_events = deque()
    proxy._telemetry_logs = deque()
    proxy._telemetry_log_window_s = 60
    proxy._telemetry_log_max = 1000
    proxy._telemetry_hybrid_sessions = deque()
    proxy._configured_mode = "hybrid"
    proxy._hybrid_state = "offline"
    proxy._hybrid_state_since_epoch = time.time() - 120
    proxy._hybrid_last_offline_reason = "cloud_failure"
    return proxy


def test_telemetry_response_kind_variants():
    proxy = _make_proxy_stub()
    assert proxy._telemetry_response_kind("<Result>Weather</Result>") == "resp_weather"
    assert proxy._telemetry_response_kind("<Result>END</Result>") == "resp_end"
    assert proxy._telemetry_response_kind("<Result>NACK</Result>") == "resp_nack"
    assert proxy._telemetry_response_kind(
        "<Result>ACK</Result><ToDo>GetAll</ToDo>"
    ) == "resp_ack_getall"
    assert proxy._telemetry_response_kind(
        "<Result>ACK</Result><ToDo>GetActual</ToDo>"
    ) == "resp_ack_getactual"
    assert proxy._telemetry_response_kind("<Result>ACK</Result>") == "resp_ack"


def test_telemetry_flush_stats():
    proxy = _make_proxy_stub()
    proxy._telemetry_stats[("tbl_actual", "cloud", "online")] = Counter(
        req_count=2,
        resp_ack=1,
        resp_end=1,
        resp_weather=0,
        resp_nack=0,
        resp_ack_getall=0,
        resp_ack_getactual=0,
        resp_other=0,
    )

    items = proxy._telemetry_flush_stats()
    assert len(items) == 1
    assert items[0]["table"] == "tbl_actual"
    assert items[0]["req_count"] == 2
    assert proxy._telemetry_stats == {}


def test_record_error_context():
    proxy = _make_proxy_stub()
    proxy._snapshot_logs = lambda: [{"message": "log"}]

    proxy._record_error_context(event_type="cloud_timeout", details={"host": "x"})
    assert len(proxy._telemetry_error_context) == 1
    assert proxy._telemetry_error_context[0]["event_type"] == "cloud_timeout"


def test_record_tbl_event_and_parse_frame_dt():
    proxy = _make_proxy_stub()
    parsed = {
        "_dt": "2024-01-01 00:00:00",
        "Type": "INFO",
        "Confirm": "1",
        "Content": "Hello",
    }
    proxy._record_tbl_event(parsed=parsed, device_id="DEV1")
    assert len(proxy._telemetry_tbl_events) == 1
    assert proxy._telemetry_tbl_events[0]["device_id"] == "DEV1"

    assert proxy._parse_frame_dt("2024-01-01T00:00:00") is not None
    assert proxy._parse_frame_dt("") is None


def test_collect_hybrid_sessions_active():
    proxy = _make_proxy_stub()
    sessions = proxy._collect_hybrid_sessions()
    assert len(sessions) == 1
    assert sessions[0]["state"] == "offline"
