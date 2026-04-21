# pylint: disable=missing-module-docstring,missing-function-docstring,invalid-name
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any


def test_telemetry_collector_initializes() -> None:
    telemetry_collector = importlib.import_module("telemetry.collector")
    TelemetryCollector = telemetry_collector.TelemetryCollector
    collector = TelemetryCollector(
        interval_s=300,
        version="2.0.0",
        telemetry_enabled=True,
        telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
        telemetry_interval_s=300,
        device_id="test_id",
    )
    collector.init()
    assert collector.interval_s == 300
    assert collector.client is not None


def _make_collector(*, box_connected: bool) -> Any:
    telemetry_collector = importlib.import_module("telemetry.collector")
    TelemetryCollector = telemetry_collector.TelemetryCollector
    return TelemetryCollector(
        interval_s=300,
        version="2.0.0",
        telemetry_enabled=True,
        telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
        telemetry_interval_s=300,
        device_id="test_id",
        get_box_connected=lambda: box_connected,
    )


def _record_log(collector: Any, *, created: float, level: int, message: str, source: str = "test") -> None:
    record = logging.LogRecord(source, level, __file__, 1, message, (), None)
    record.created = created
    collector.record_log_entry(record)


def test_telemetry_topic_format() -> None:
    telemetry_client = importlib.import_module("telemetry.client")
    TelemetryClient = telemetry_client.TelemetryClient
    client = TelemetryClient(
        "test_id",
        "2.0.0",
        telemetry_enabled=True,
        telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
        telemetry_interval_s=300,
    )
    assert f"oig/telemetry/{client.device_id}" == "oig/telemetry/test_id"
    assert f"oig/events/{client.device_id}" == "oig/events/test_id"


def test_warning_burst_full_window(monkeypatch) -> None:
    telemetry_collector = importlib.import_module("telemetry.collector")
    current_time = 1_000.0
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: current_time)

    collector = _make_collector(box_connected=True)

    _record_log(collector, created=699.0, level=logging.INFO, message="too old")
    _record_log(collector, created=701.0, level=logging.INFO, message="inside rolling window")
    _record_log(collector, created=999.0, level=logging.WARNING, message="warning activates burst")

    logs = collector.collect_metrics()["window_metrics"]["logs"]

    assert [entry["message"] for entry in logs] == [
        "inside rolling window",
        "warning activates burst",
    ]


def test_log_window_warning_bursts_extend_by_two_windows(monkeypatch) -> None:
    telemetry_collector = importlib.import_module("telemetry.collector")
    current_time = {"value": 1_000.0}
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: current_time["value"])

    collector = _make_collector(box_connected=True)

    _record_log(collector, created=999.0, level=logging.WARNING, message="warning one")
    first_window_logs = collector.collect_metrics()["window_metrics"]["logs"]

    current_time["value"] = 1_100.0
    _record_log(collector, created=1_099.0, level=logging.WARNING, message="warning two")
    second_window_logs = collector.collect_metrics()["window_metrics"]["logs"]

    current_time["value"] = 1_200.0
    third_window_logs = collector.collect_metrics()["window_metrics"]["logs"]

    current_time["value"] = 1_300.0
    fourth_window_logs = collector.collect_metrics()["window_metrics"]["logs"]

    current_time["value"] = 1_390.0
    fifth_window_logs = collector.collect_metrics()["window_metrics"]["logs"]

    assert first_window_logs
    assert second_window_logs
    assert third_window_logs
    assert fourth_window_logs
    assert fifth_window_logs == []


def test_log_window_overflow(monkeypatch) -> None:
    telemetry_collector = importlib.import_module("telemetry.collector")
    current_time = 1_000.0
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: current_time)

    collector = _make_collector(box_connected=False)

    for idx in range(5_003):
        _record_log(
            collector,
            created=900.0 + (idx / 100.0),
            level=logging.INFO,
            message=f"message {idx}",
        )

    logs = collector.collect_metrics()["window_metrics"]["logs"]

    assert len(logs) == 5_001
    assert logs[0]["message"] == "message 3"
    assert logs[-2]["message"] == "message 5002"
    assert logs[-1] == {
        "timestamp": "1970-01-01 00:16:40",
        "level": "WARNING",
        "message": "log buffer truncated",
        "source": "telemetry.collector",
        "synthetic": True,
        "dropped_count": 3,
    }


def test_sqlite_buffer_path_and_payload_structure(tmp_path: Path) -> None:
    telemetry_client = importlib.import_module("telemetry.client")
    TelemetryBuffer = telemetry_client.TelemetryBuffer
    db_path = tmp_path / "telemetry_buffer.db"
    buffer = TelemetryBuffer(db_path=db_path)
    payload = {
        "device_id": "test_id",
        "instance_hash": "abc",
        "version": "2.0.0",
        "timestamp": "2026-01-01T00:00:00Z",
        "interval_s": 300,
        "uptime_s": 1,
        "mode": "online",
        "configured_mode": "online",
        "box_connected": True,
        "box_peer": "127.0.0.1",
        "frames_received": 1,
        "frames_forwarded": 1,
        "cloud_connects": 1,
        "cloud_disconnects": 0,
        "cloud_timeouts": 0,
        "cloud_errors": 0,
        "cloud_online": True,
        "mqtt_ok": True,
        "mqtt_queue": 0,
        "set_commands": [],
        "window_metrics": {
            "box_sessions": [],
            "cloud_sessions": [],
            "hybrid_sessions": [],
            "offline_events": [],
            "tbl_events": [],
            "error_context": [],
            "stats": [],
            "logs": [],
            "settings_audit": [],
        },
        "nack_reasons": {},
        "conn_mismatch_drops": 0,
        "cloud_gap_histogram": {
            "lt_60s": 0,
            "60_120s": 0,
            "120_300s": 0,
            "300_600s": 0,
            "gt_600s": 0,
        },
        "pairing_confidence": {"high": 0, "medium": 0, "low": 0},
        "frame_directions": {
            "box_to_proxy": 0,
            "cloud_to_proxy": 0,
            "proxy_to_box": 0,
        },
        "signal_distribution": {},
        "end_frames": {"received": 0, "sent": 0, "time_since_last_s": None},
    }
    assert buffer.store("oig/telemetry/test_id", payload)
    assert db_path.exists()
    pending = buffer.get_pending(limit=1)
    assert len(pending) == 1
    _, topic, stored_payload = pending[0]
    assert topic == "oig/telemetry/test_id"
    assert set(stored_payload.keys()) == set(payload.keys())


def test_setting_window_capture(monkeypatch) -> None:
    telemetry_collector = importlib.import_module("telemetry.collector")
    settings_audit_mod = importlib.import_module("telemetry.settings_audit")
    current_time = {"value": 1_000.0}
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: current_time["value"])

    collector = _make_collector(box_connected=True)

    _record_log(collector, created=850.0, level=logging.INFO, message="pre-window log")

    record = settings_audit_mod.SettingsAuditRecord(
        audit_id="aud_001",
        device_id="test",
        table="tbl_test",
        key="test_key",
        step=settings_audit_mod.SettingStep.INCOMING,
        result=settings_audit_mod.SettingResult.PENDING,
    )
    collector.record_setting_audit_step(record)

    metrics1 = collector.collect_metrics()
    logs1 = metrics1["window_metrics"]["logs"]
    assert any(entry["message"] == "pre-window log" for entry in logs1)
    assert len(metrics1["window_metrics"]["settings_audit"]) == 1
    assert metrics1["window_metrics"]["settings_audit"][0]["audit_id"] == "aud_001"

    current_time["value"] = 1_100.0
    _record_log(collector, created=1_100.0, level=logging.INFO, message="post-window log")

    current_time["value"] = 1_300.0
    metrics2 = collector.collect_metrics()
    logs2 = metrics2["window_metrics"]["logs"]
    assert any(entry["message"] == "post-window log" for entry in logs2)

    current_time["value"] = 1_600.0
    metrics3 = collector.collect_metrics()
    logs3 = metrics3["window_metrics"]["logs"]
    assert not any(entry["message"] == "post-window log" for entry in logs3)


def test_overlapping_setting_windows(monkeypatch) -> None:
    telemetry_collector = importlib.import_module("telemetry.collector")
    settings_audit_mod = importlib.import_module("telemetry.settings_audit")
    current_time = {"value": 1_000.0}
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: current_time["value"])

    collector = _make_collector(box_connected=True)

    record1 = settings_audit_mod.SettingsAuditRecord(
        audit_id="aud_001",
        device_id="test",
        table="tbl_test",
        key="key_a",
        step=settings_audit_mod.SettingStep.INCOMING,
        result=settings_audit_mod.SettingResult.PENDING,
    )
    collector.record_setting_audit_step(record1)

    record2 = settings_audit_mod.SettingsAuditRecord(
        audit_id="aud_002",
        device_id="test",
        table="tbl_test",
        key="key_b",
        step=settings_audit_mod.SettingStep.INCOMING,
        result=settings_audit_mod.SettingResult.PENDING,
    )
    collector.record_setting_audit_step(record2)

    _record_log(collector, created=999.0, level=logging.INFO, message="overlap log")

    metrics1 = collector.collect_metrics()
    audit_ids = {r["audit_id"] for r in metrics1["window_metrics"]["settings_audit"]}
    assert audit_ids == {"aud_001", "aud_002"}
    assert metrics1["window_metrics"]["logs"]

    current_time["value"] = 1_100.0
    _record_log(collector, created=1_100.0, level=logging.INFO, message="post-overlap log")
    current_time["value"] = 1_300.0
    metrics2 = collector.collect_metrics()
    assert metrics2["window_metrics"]["settings_audit"] == []
    assert any(entry["message"] == "post-overlap log" for entry in metrics2["window_metrics"]["logs"])

    current_time["value"] = 1_600.0
    metrics3 = collector.collect_metrics()
    assert metrics3["window_metrics"]["settings_audit"] == []
    assert metrics3["window_metrics"]["logs"] == []


def test_warning_and_setting_burst_overlap(monkeypatch) -> None:
    telemetry_collector = importlib.import_module("telemetry.collector")
    settings_audit_mod = importlib.import_module("telemetry.settings_audit")
    current_time = {"value": 1_000.0}
    monkeypatch.setattr(telemetry_collector.time, "time", lambda: current_time["value"])

    collector = _make_collector(box_connected=True)

    _record_log(collector, created=999.0, level=logging.WARNING, message="warning one")

    record = settings_audit_mod.SettingsAuditRecord(
        audit_id="aud_003",
        device_id="test",
        table="tbl_test",
        key="key_c",
        step=settings_audit_mod.SettingStep.INCOMING,
        result=settings_audit_mod.SettingResult.PENDING,
    )
    collector.record_setting_audit_step(record)

    metrics1 = collector.collect_metrics()
    logs1 = metrics1["window_metrics"]["logs"]
    assert any(entry["message"] == "warning one" for entry in logs1)
    assert len(metrics1["window_metrics"]["settings_audit"]) == 1

    current_time["value"] = 1_300.0
    _record_log(collector, created=1_300.0, level=logging.INFO, message="during burst")
    metrics2 = collector.collect_metrics()
    logs2 = metrics2["window_metrics"]["logs"]
    assert any(entry["message"] == "during burst" for entry in logs2)

    current_time["value"] = 1_600.0
    metrics3 = collector.collect_metrics()
    logs3 = metrics3["window_metrics"]["logs"]
    assert logs3 == []
