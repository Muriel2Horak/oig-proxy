# pylint: disable=missing-module-docstring,missing-function-docstring,invalid-name
from __future__ import annotations

import importlib
from pathlib import Path


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
