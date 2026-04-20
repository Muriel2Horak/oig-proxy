"""Additional coverage tests for telemetry.collector."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import telemetry.collector as telemetry_collector
from telemetry.settings_audit import SettingResult, SettingStep, SettingsAuditRecord


def _make_collector(**overrides):
    kwargs = {
        "interval_s": 60,
        "version": "2.0.0",
        "telemetry_enabled": True,
        "telemetry_mqtt_broker": "telemetry.muriel-cz.cz:1883",
        "telemetry_interval_s": 60,
        "device_id": "dev-1",
    }
    kwargs.update(overrides)
    return telemetry_collector.TelemetryCollector(**kwargs)


def _record_log(collector, *, created: float, level: int, message: str) -> None:
    record = logging.LogRecord("test.logger", level, __file__, 1, message, (), None)
    record.created = created
    collector.record_log_entry(record)


def test_update_device_id_parse_frame_dt_and_version_loading(tmp_path: Path) -> None:
    collector = _make_collector()
    collector.client = SimpleNamespace(device_id="old")

    collector.update_device_id("new-device")
    assert collector._device_id == "new-device"
    assert collector.client.device_id == "new-device"

    assert collector._parse_frame_dt(None) is None
    assert collector._parse_frame_dt("   ") is None
    assert collector._parse_frame_dt("not-a-date") is None
    assert collector._parse_frame_dt("2026-03-12 12:00:00") == "2026-03-12T12:00:00Z"
    assert collector._parse_frame_dt("2026-03-12T12:00:00+01:00") == "2026-03-12T11:00:00Z"

    config_path = tmp_path / "config.json"
    config_path.write_text('{"version": "9.9.9"}', encoding="utf-8")
    assert collector._load_version_from_config(config_path) == "9.9.9"

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{", encoding="utf-8")
    assert collector._load_version_from_config(bad_path) == "unknown"
    assert collector._load_version_from_config(tmp_path / "missing.json") == "unknown"


def test_response_helpers_request_queue_and_timeout_stats() -> None:
    collector = _make_collector(get_mode=lambda: "hybrid")

    assert collector._response_kind("<Result>Weather</Result>") == "resp_weather"
    assert collector._response_kind("<Result>END</Result>") == "resp_end"
    assert collector._response_kind("<Result>NACK</Result>") == "resp_nack"
    assert collector._response_kind("<Result>ACK</Result><ToDo>GetAll</ToDo>") == "resp_ack_getall"
    assert collector._response_kind("<Result>ACK</Result><ToDo>GetActual</ToDo>") == "resp_ack_getactual"
    assert collector._response_kind("<Result>ACK</Result>") == "resp_ack"
    assert collector._response_kind("garbage") == "resp_other"
    assert collector._extract_nack_reason("<Reason> Access denied </Reason>") == "Access denied"
    assert collector._extract_nack_reason("<Result>NACK</Result>") == "unknown"

    collector.record_request(None, 1)
    for idx in range(1002):
        collector.record_request(f"tbl_{idx}", 7)
    assert len(collector.req_pending[7]) == 1000

    collector.record_request("tbl_set", 1)
    collector.record_response("<Result>NACK</Result><Reason>Denied</Reason>", source="cloud", conn_id=1)
    collector.record_timeout(conn_id=99)

    stats = collector._flush_stats()
    assert {item["table"] for item in stats} == {"tbl_set", "unmatched"}
    cloud_stat = next(item for item in stats if item["table"] == "tbl_set")
    timeout_stat = next(item for item in stats if item["table"] == "unmatched")

    assert cloud_stat["mode"] == "hybrid"
    assert cloud_stat["resp_nack"] == 1
    assert timeout_stat["response_source"] == "timeout"
    assert timeout_stat["resp_other"] == 1
    assert collector.nack_reasons["Denied"] == 1
    assert collector.cloud_ok_in_window is True
    assert collector.cloud_failed_in_window is True


def test_record_context_sessions_tbl_events_and_window_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: 200.0)
    collector = _make_collector()

    _record_log(collector, created=199.0, level=logging.INFO, message="inside context")
    collector.record_error_context(event_type="error_event", details={"bad": {1, 2, 3}})
    collector.record_tbl_event(parsed={"_dt": "2026-03-12 12:00:00", "Type": "Setting", "Confirm": "Yes", "Content": "Changed"}, device_id="dev-1")
    collector.record_tbl_event(parsed={"Type": "Other", "Confirm": "No", "Content": "Fallback"}, device_id=None)
    collector.record_box_session_end(connected_since_epoch=None, reason="noop", peer=None)
    collector.record_box_session_end(connected_since_epoch=180.0, reason="eof", peer="1.2.3.4:5710")
    collector.record_cloud_session_end(connected_since_epoch=199.5, reason="eof")
    collector.record_hybrid_state_end(state=None, state_since_epoch=100.0, ended_at=200.0, mode="hybrid")
    collector.record_hybrid_state_end(state="online", state_since_epoch=150.0, ended_at=200.0, mode="hybrid", reason="done")
    collector.record_offline_event(reason=None, local_ack=None, mode="offline")

    window = collector._collect_and_clear_window_metrics(logs=[])

    assert window["error_context"][0]["event_type"] == "error_event"
    assert json.loads(window["error_context"][0]["details"]) == {"detail": "{'bad': {1, 2, 3}}"}
    assert len(window["tbl_events"]) == 2
    assert window["tbl_events"][0]["event_time"] == "2026-03-12T12:00:00Z"
    assert window["box_sessions"][0]["peer"] == "1.2.3.4:5710"
    assert window["cloud_sessions"][0]["duration_s"] == 0
    assert window["hybrid_sessions"][0]["reason"] == "done"
    assert window["offline_events"][0]["reason"] == "unknown"
    assert collector.cloud_eof_short_in_window is True
    assert not collector.box_sessions
    assert not collector.cloud_sessions
    assert not collector.hybrid_sessions
    assert not collector.offline_events
    assert not collector.tbl_events
    assert not collector.error_context
    assert not collector.settings_audit


def test_cached_state_value_device_specific_metrics_and_collect_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = {
        "oig_local/dev-1/IsNewSet/state": '{"LAT": 48}',
        "oig_local/dev-1/IsNewFW/state": '{"fw": "1.2.3"}',
        "oig_local/dev-1/tbl_box/state": '{"tmlastcall": 123, "STRNGHT": 77}',
        "oig_local/dev-1/IsNewWeather/state": '"cached-weather"',
        "oig_local/dev-1/tbl_invertor_prms/state": "{",
    }
    mqtt_publisher = SimpleNamespace(
        get_cached_payload=lambda topic: cache.get(topic),
        is_ready=lambda: True,
    )
    collector = _make_collector(
        mqtt_publisher=mqtt_publisher,
        get_mode=lambda: "invalid-mode",
        get_configured_mode=lambda: "hybrid",
        get_box_connected=lambda: True,
        get_box_peer=lambda: "peer-1",
        get_uptime_s=lambda: 42,
        get_frames_received=lambda: 7,
        get_frames_forwarded=lambda: 5,
        get_cloud_connects=lambda: 3,
        get_cloud_disconnects=lambda: 2,
        get_cloud_timeouts=lambda: 1,
        get_cloud_errors=lambda: 4,
        get_cloud_session_connected=lambda: False,
        consume_set_commands=lambda: [{"table": "tbl_set"}],
    )
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: 1_000.0)

    assert collector._cached_state_value("dev-1", "isnewset", "lat") == 48
    assert collector._cached_state_value("dev-1", "tbl_box", "strnght") == 77
    assert collector._cached_state_value("dev-1", "isnewweather", "loadedon") == "cached-weather"
    assert collector._cached_state_value("dev-1", "tbl_missing", "x") is None
    assert _make_collector(mqtt_publisher=object())._cached_state_value("dev-1", "tbl_box", "x") is None

    collector.box_seen_in_window = True
    collector.record_nack_reason("Denied")
    collector.record_conn_mismatch()
    for gap in (30, 90, 200, 500, 700):
        collector.record_cloud_gap(gap)
    for confidence in (0.95, 0.6, 0.2):
        collector.record_pairing_confidence(confidence)
    for direction in ("box_to_proxy", "cloud_to_proxy", "proxy_to_box"):
        collector.record_frame_direction(direction)
    collector.record_signal_class("ack")
    collector.record_signal_class("ack")
    collector.record_end_frame(sent=False)
    collector.record_end_frame(sent=True)
    collector.last_end_frame_time = 990.0

    metrics = collector.collect_metrics()
    assert metrics["mode"] == "offline"
    assert metrics["configured_mode"] == "hybrid"
    assert metrics["box_connected"] is True
    assert metrics["box_peer"] == "peer-1"
    assert metrics["frames_received"] == 7
    assert metrics["cloud_online"] is False
    assert metrics["mqtt_ok"] is True
    assert metrics["set_commands"] == [{"table": "tbl_set"}]
    assert metrics["nack_reasons"] == {"Denied": 1}
    assert metrics["conn_mismatch_drops"] == 1
    assert metrics["cloud_gap_histogram"] == {
        "lt_60s": 1,
        "60_120s": 1,
        "120_300s": 1,
        "300_600s": 1,
        "gt_600s": 1,
    }
    assert metrics["pairing_confidence"] == {"high": 1, "medium": 1, "low": 1}
    assert metrics["frame_directions"] == {"box_to_proxy": 1, "cloud_to_proxy": 1, "proxy_to_box": 1}
    assert metrics["signal_distribution"] == {"ack": 2}
    assert metrics["end_frames"] == {"received": 1, "sent": 1, "time_since_last_s": 10}
    assert metrics["isnewfw_fw"] == "1.2.3"
    assert metrics["isnewset_lat"] == 48
    assert metrics["tbl_box_tmlastcall"] == 123
    assert metrics["isnewweather_loadedon"] == "cached-weather"
    assert metrics["tbl_box_strnght"] == 77
    assert metrics["tbl_invertor_prms_model"] == "{"

    reset_metrics = collector.collect_metrics()
    assert reset_metrics["nack_reasons"] == {}
    assert reset_metrics["conn_mismatch_drops"] == 0
    assert reset_metrics["cloud_gap_histogram"] == {
        "lt_60s": 0,
        "60_120s": 0,
        "120_300s": 0,
        "300_600s": 0,
        "gt_600s": 0,
    }
    assert reset_metrics["pairing_confidence"] == {"high": 0, "medium": 0, "low": 0}
    assert reset_metrics["frame_directions"] == {"box_to_proxy": 0, "cloud_to_proxy": 0, "proxy_to_box": 0}
    assert reset_metrics["signal_distribution"] == {}


def test_setting_burst_helpers_and_overflow_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = {"value": 1_000.0}
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: current_time["value"])
    collector = _make_collector(get_box_connected=lambda: True)
    collector.log_max = 2

    _record_log(collector, created=999.0, level=logging.INFO, message="first")
    _record_log(collector, created=999.5, level=logging.INFO, message="second")
    _record_log(collector, created=1_000.0, level=logging.WARNING, message="third")
    collector.record_setting_audit_step(
        SettingsAuditRecord(
            audit_id="aud-1",
            device_id="dev-1",
            table="tbl_set",
            key="MODE",
            step=SettingStep.INCOMING,
            result=SettingResult.PENDING,
        )
    )

    metrics_1 = collector.collect_metrics()
    assert collector.setting_burst_current_active is True
    assert collector.setting_burst_next_windows_remaining == 0
    assert metrics_1["window_metrics"]["settings_audit"][0]["audit_id"] == "aud-1"
    assert metrics_1["window_metrics"]["logs"][-1]["synthetic"] is True
    assert metrics_1["window_metrics"]["logs"][-1]["dropped_count"] == 1

    current_time["value"] = 1_060.0
    metrics_2 = collector.collect_metrics()
    assert collector.setting_burst_current_active is False
    assert metrics_2["window_metrics"]["logs"]

    current_time["value"] = 1_120.0
    metrics_3 = collector.collect_metrics()
    assert metrics_3["window_metrics"]["logs"] == []


@pytest.mark.asyncio
async def test_fire_event_and_loop_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks: set[asyncio.Task[None]] = set()
    collector = _make_collector(get_background_tasks=lambda: tasks)
    collector.client = SimpleNamespace(
        device_id="",
        event_error_cloud_timeout=AsyncMock(return_value=None),
        event_shutdown=AsyncMock(return_value=None),
        provision=AsyncMock(return_value=None),
        send_telemetry=AsyncMock(return_value=None),
    )

    collector.fire_event("error_cloud_timeout", cloud_host="cloud", timeout_s=3.0)
    collector.fire_event("shutdown")
    await asyncio.sleep(0)
    assert collector.client.event_error_cloud_timeout.await_count == 1
    assert collector.client.event_shutdown.await_count == 1
    assert len(collector.error_context) == 1

    sleep_calls = {"count": 0}

    async def fake_sleep(_seconds: float) -> None:
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(telemetry_collector.asyncio, "sleep", fake_sleep)
    collector.collect_metrics = MagicMock(return_value={"metric": True})

    with pytest.raises(asyncio.CancelledError):
        await collector.loop()

    assert collector.client.device_id == "dev-1"
    assert collector.client.provision.await_count == 1
    assert collector.client.send_telemetry.await_count == 2


def test_init_handles_telemetry_client_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = _make_collector()
    monkeypatch.setattr(telemetry_collector, "TelemetryClient", MagicMock(side_effect=RuntimeError("boom")))
    collector.init()
    assert collector.client is None
