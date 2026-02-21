"""Tests for proxy telemetry helper methods."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=use-implicit-booleaness-not-comparison

from collections import Counter, deque
from unittest.mock import MagicMock

from models import ProxyMode
from telemetry_collector import TelemetryCollector


def _make_proxy_and_tc():
    mock_proxy = MagicMock()
    mock_proxy._hm = MagicMock()
    mock_proxy._hm.mode = ProxyMode.ONLINE
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
    assert TelemetryCollector._response_kind("<Result>OTHER</Result>") == "resp_other"


def test_telemetry_flush_stats_clears():
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


def test_record_error_context_serializes():
    mock_proxy, tc = _make_proxy_and_tc()
    tc.record_error_context(event_type="cloud_timeout", details={"host": "x"})
    assert len(tc.error_context) == 1
    item = tc.error_context[0]
    assert item["event_type"] == "cloud_timeout"
    assert "host" in item["details"]


def test_record_tbl_event_uses_dt_or_now():
    mock_proxy, tc = _make_proxy_and_tc()
    # Monkey-patch _parse_frame_dt on the instance is not needed; it's a static method.
    # Instead, we supply a valid datetime string.
    tc.record_tbl_event(
        parsed={"_dt": "2024-01-01 01:00:00", "Type": "INFO"},
        device_id="DEV1",
    )
    assert tc.tbl_events[0]["event_time"] == "2024-01-01T01:00:00Z"

    tc.record_tbl_event(parsed={"Type": "WARN"}, device_id=None)
    # When _dt is missing, _parse_frame_dt returns None, so _utc_iso() is used
    assert tc.tbl_events[1]["event_time"] is not None


def test_parse_frame_dt_variants():
    assert TelemetryCollector._parse_frame_dt(None) is None
    assert TelemetryCollector._parse_frame_dt("") is None
    assert TelemetryCollector._parse_frame_dt("not-a-date") is None
    assert (
        TelemetryCollector._parse_frame_dt("2024-01-01 12:00:00")
        == "2024-01-01T12:00:00Z"
    )
    assert (
        TelemetryCollector._parse_frame_dt("2024-01-01T12:00:00")
        == "2024-01-01T12:00:00Z"
    )
