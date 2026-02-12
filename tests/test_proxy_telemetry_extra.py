"""Additional coverage tests for proxy telemetry helpers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=use-implicit-booleaness-not-comparison

from collections import Counter, deque
import time
from unittest.mock import MagicMock

from models import ProxyMode
from telemetry_collector import TelemetryCollector


def _make_proxy_and_tc():
    mock_proxy = MagicMock()
    mock_proxy.mode = ProxyMode.ONLINE
    mock_proxy._configured_mode = "hybrid"
    mock_proxy._hybrid_state = "offline"
    mock_proxy._hybrid_state_since_epoch = time.time() - 120
    mock_proxy._hybrid_last_offline_reason = "cloud_failure"
    tc = TelemetryCollector(mock_proxy, interval_s=300)
    return mock_proxy, tc


def test_telemetry_response_kind_variants():
    assert TelemetryCollector._response_kind("<Result>Weather</Result>") == "resp_weather"
    assert TelemetryCollector._response_kind("<Result>END</Result>") == "resp_end"
    assert TelemetryCollector._response_kind("<Result>NACK</Result>") == "resp_nack"
    assert TelemetryCollector._response_kind(
        "<Result>ACK</Result><ToDo>GetAll</ToDo>"
    ) == "resp_ack_getall"
    assert TelemetryCollector._response_kind(
        "<Result>ACK</Result><ToDo>GetActual</ToDo>"
    ) == "resp_ack_getactual"
    assert TelemetryCollector._response_kind("<Result>ACK</Result>") == "resp_ack"


def test_telemetry_flush_stats():
    mock_proxy, tc = _make_proxy_and_tc()
    tc.stats[("tbl_actual", "cloud", "online")] = Counter(
        req_count=2,
        resp_ack=1,
        resp_end=1,
        resp_weather=0,
        resp_nack=0,
        resp_ack_getall=0,
        resp_ack_getactual=0,
        resp_other=0,
    )

    items = tc._flush_stats()
    assert len(items) == 1
    assert items[0]["table"] == "tbl_actual"
    assert items[0]["req_count"] == 2
    assert tc.stats == {}


def test_record_error_context():
    mock_proxy, tc = _make_proxy_and_tc()

    tc.record_error_context(event_type="cloud_timeout", details={"host": "x"})
    assert len(tc.error_context) == 1
    assert tc.error_context[0]["event_type"] == "cloud_timeout"


def test_record_tbl_event_and_parse_frame_dt():
    mock_proxy, tc = _make_proxy_and_tc()
    parsed = {
        "_dt": "2024-01-01 00:00:00",
        "Type": "INFO",
        "Confirm": "1",
        "Content": "Hello",
    }
    tc.record_tbl_event(parsed=parsed, device_id="DEV1")
    assert len(tc.tbl_events) == 1
    assert tc.tbl_events[0]["device_id"] == "DEV1"

    assert TelemetryCollector._parse_frame_dt("2024-01-01T00:00:00") is not None
    assert TelemetryCollector._parse_frame_dt("") is None


def test_collect_hybrid_sessions_active():
    mock_proxy, tc = _make_proxy_and_tc()
    sessions = tc._collect_hybrid_sessions()
    assert len(sessions) == 1
    assert sessions[0]["state"] == "offline"
