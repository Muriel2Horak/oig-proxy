"""Tests for proxy telemetry helper methods."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

from collections import Counter, deque
from unittest.mock import MagicMock

import proxy as proxy_module
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode = ProxyMode.ONLINE
    proxy._telemetry_stats = {}
    proxy._telemetry_error_context = deque()
    proxy._telemetry_tbl_events = deque()
    proxy._telemetry_logs = deque()
    proxy._snapshot_logs = MagicMock(return_value=[{"message": "log"}])
    proxy._utc_iso = MagicMock(return_value="2024-01-01T00:00:00Z")
    return proxy


def test_telemetry_response_kind_variants():
    proxy = _make_proxy()
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
    assert proxy._telemetry_response_kind("<Result>OTHER</Result>") == "resp_other"


def test_telemetry_flush_stats_clears():
    proxy = _make_proxy()
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


def test_record_error_context_serializes():
    proxy = _make_proxy()
    proxy._record_error_context(event_type="cloud_timeout", details={"host": "x"})
    assert len(proxy._telemetry_error_context) == 1
    item = proxy._telemetry_error_context[0]
    assert item["event_type"] == "cloud_timeout"
    assert "host" in item["details"]


def test_record_tbl_event_uses_dt_or_now():
    proxy = _make_proxy()
    proxy._parse_frame_dt = MagicMock(return_value="2024-01-01T01:00:00Z")
    proxy._record_tbl_event(
        parsed={"_dt": "2024-01-01 01:00:00", "Type": "INFO"},
        device_id="DEV1",
    )
    assert proxy._telemetry_tbl_events[0]["event_time"] == "2024-01-01T01:00:00Z"

    proxy._parse_frame_dt = MagicMock(return_value=None)
    proxy._record_tbl_event(parsed={"Type": "WARN"}, device_id=None)
    assert proxy._telemetry_tbl_events[1]["event_time"] == "2024-01-01T00:00:00Z"


def test_parse_frame_dt_variants():
    assert proxy_module.OIGProxy._parse_frame_dt(None) is None
    assert proxy_module.OIGProxy._parse_frame_dt("") is None
    assert proxy_module.OIGProxy._parse_frame_dt("not-a-date") is None
    assert (
        proxy_module.OIGProxy._parse_frame_dt("2024-01-01 12:00:00")
        == "2024-01-01T12:00:00Z"
    )
    assert (
        proxy_module.OIGProxy._parse_frame_dt("2024-01-01T12:00:00")
        == "2024-01-01T12:00:00Z"
    )
