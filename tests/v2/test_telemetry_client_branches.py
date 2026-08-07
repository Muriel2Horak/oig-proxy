"""Defensive branch contracts for the isolated telemetry MQTT client."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import telemetry.client as telemetry_client


class _FailingConnection:
    def execute(self, *_args, **_kwargs):
        raise RuntimeError("database failure")

    def commit(self) -> None:
        raise RuntimeError("database failure")

    def close(self) -> None:
        raise RuntimeError("database failure")


class _MQTTClient:
    def __init__(self, *, connect_rc: int | None = None) -> None:
        self.on_connect = None
        self.on_disconnect = None
        self.connect_rc = connect_rc
        self.connected = False
        self.raise_on_publish = False
        self.raise_on_cleanup = False
        self.raise_on_reconnect = False
        self.published: list[tuple[str, str, int]] = []

    def connect(self, _host: str, _port: int, keepalive: int) -> None:
        assert keepalive == 60

    def loop_start(self) -> None:
        if self.connect_rc is not None and self.on_connect is not None:
            self.on_connect(self, None, None, self.connect_rc, None)

    def loop_stop(self) -> None:
        if self.raise_on_cleanup:
            raise RuntimeError("loop stop failure")

    def disconnect(self) -> None:
        if self.raise_on_cleanup:
            raise RuntimeError("disconnect failure")
        if self.on_disconnect is not None:
            self.on_disconnect(self, None, None, 0, None)

    def is_connected(self) -> bool:
        return self.connected

    def reconnect(self) -> None:
        if self.raise_on_reconnect:
            raise RuntimeError("reconnect failure")

    def publish(self, topic: str, message: str, qos: int):
        if self.raise_on_publish:
            raise RuntimeError("publish failure")
        self.published.append((topic, message, qos))
        return SimpleNamespace(rc=0)


def _bare_buffer(connection=None) -> telemetry_client.TelemetryBuffer:
    buffer = object.__new__(telemetry_client.TelemetryBuffer)
    buffer._db_path = Path("unused")
    buffer._conn = connection
    return buffer


def _enabled_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> telemetry_client.TelemetryClient:
    monkeypatch.setattr(telemetry_client, "MQTT_AVAILABLE", True)
    return telemetry_client.TelemetryClient(
        "branch-device",
        "2.2.0",
        telemetry_enabled=True,
        db_path=tmp_path / "telemetry-branches.db",
    )


def test_sqlite_initialization_without_indexes_and_pending_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    connection = telemetry_client._init_sqlite_db(
        str(tmp_path / "plain.db"),
        "CREATE TABLE sample (value INTEGER);",
    )
    connection.close()

    original = telemetry_client._init_sqlite_db

    def seeded(db_path: str, schema_sql: str, indexes_sql: str = ""):
        conn = original(db_path, schema_sql, indexes_sql)
        conn.execute(
            "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
            ("topic", json.dumps({"value": 1}), 1.0e20),
        )
        return conn

    monkeypatch.setattr(telemetry_client, "_init_sqlite_db", seeded)
    buffer = telemetry_client.TelemetryBuffer(tmp_path / "seeded.db")
    assert buffer.count() == 1
    buffer.close()


def test_buffer_defensive_absent_and_failure_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    absent = _bare_buffer()
    absent._cleanup()
    assert absent.store("topic", {}) is False
    assert absent.get_pending() == []
    absent.remove(1)
    assert absent.count() == 0
    absent.close()

    failing = _bare_buffer(_FailingConnection())
    failing._cleanup()
    assert failing.store("topic", {}) is False
    assert failing.get_pending() == []
    failing.remove(1)
    assert failing.count() == 0
    failing.close()
    assert failing._conn is not None

    monkeypatch.setattr(telemetry_client, "BUFFER_MAX_MESSAGES", 0)
    real = telemetry_client.TelemetryBuffer(tmp_path / "limit.db")
    real._cleanup = MagicMock()
    assert real.store("topic", {"value": 1}) is True
    real._cleanup.assert_called_once()
    real.close()


def test_buffer_removes_invalid_json_and_ignores_non_object_payload(tmp_path: Path) -> None:
    buffer = telemetry_client.TelemetryBuffer(tmp_path / "invalid.db")
    assert buffer._conn is not None
    buffer._conn.execute(
        "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
        ("list", "[]", 1.0),
    )
    buffer._conn.execute(
        "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
        ("invalid", "{", 2.0),
    )
    buffer._conn.commit()

    assert buffer.get_pending() == []
    assert buffer.count() == 1
    buffer.close()


def test_client_cleanup_and_creation_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _enabled_client(monkeypatch, tmp_path)
    client._cleanup_client()

    broken = _MQTTClient()
    broken.raise_on_cleanup = True
    client._client = broken
    client._connected = True
    client._cleanup_client()
    assert client._client is None
    assert client._connected is False

    monkeypatch.setattr(telemetry_client, "MQTT_AVAILABLE", False)
    assert client._create_client() is False

    monkeypatch.setattr(telemetry_client, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(telemetry_client, "import_module", MagicMock(side_effect=RuntimeError("import failure")))
    assert client._create_client() is False
    assert client._connect_backoff_s == 10.0
    client.disconnect()

    monkeypatch.setattr(
        telemetry_client,
        "_init_sqlite_db",
        MagicMock(side_effect=RuntimeError("initialization failure")),
    )
    failed_buffer = telemetry_client.TelemetryBuffer(tmp_path / "failed.db")
    assert failed_buffer._conn is None


def test_client_callback_without_version_and_nonzero_connect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created: list[_MQTTClient] = []

    def factory(**_kwargs):
        mqtt = _MQTTClient(connect_rc=1)
        created.append(mqtt)
        return mqtt

    module = SimpleNamespace(MQTTv311=4, Client=factory)
    monkeypatch.setattr(telemetry_client, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(telemetry_client, "import_module", lambda _name: module)
    monkeypatch.setattr(telemetry_client.time, "sleep", lambda _seconds: None)
    client = _enabled_client(monkeypatch, tmp_path)

    assert client._create_client() is False
    assert len(created) == 1
    assert client._client is None
    client.disconnect()


def test_connection_publish_and_flush_branch_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _enabled_client(monkeypatch, tmp_path)
    mqtt = _MQTTClient()
    client._client = mqtt
    client._connected = True
    assert client._ensure_connected() is True

    client._connected = False
    mqtt.connected = True
    monkeypatch.setattr(telemetry_client.time, "monotonic", lambda: 100.0)
    client._last_connect_attempt = 0.0
    assert client._ensure_connected() is True

    mqtt.connected = False
    client._connected = False
    client._last_connect_attempt = 0.0
    assert client._ensure_connected() is False

    client._ensure_connected = MagicMock(return_value=False)
    assert client._publish_sync("topic", {}) is False
    assert client._flush_buffer_sync() == 0

    client._ensure_connected = MagicMock(return_value=True)
    client._client = None
    assert client._publish_sync("topic", {}) is False
    assert client._flush_buffer_sync() == 0

    client._client = mqtt
    mqtt.raise_on_publish = True
    assert client._buffer is not None
    client._buffer.close()
    client._buffer = MagicMock()
    client._buffer.get_pending.return_value = [(1, "topic", {"value": 1})]
    assert client._flush_buffer_sync() == 0

    client._client = None
    assert client._flush_buffer_sync() == 0
    client.disconnect()


@pytest.mark.asyncio
async def test_send_paths_cover_missing_device_success_and_buffer_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _enabled_client(monkeypatch, tmp_path)
    client.device_id = ""
    assert await client.send_telemetry({}) is False
    assert await client.send_event("event") is False

    client.device_id = "branch-device"
    client._publish_sync = MagicMock(return_value=True)
    client._last_buffer_flush = telemetry_client.time.time()
    assert await client.send_telemetry({"metric": 1}) is True
    assert await client.send_event("event") is True

    client._publish_sync = MagicMock(return_value=False)
    assert client._buffer is not None
    client._buffer.store = MagicMock(side_effect=[True, False])
    assert await client.send_event("buffered") is True
    assert await client.send_event("lost") is False

    client._buffer.store = MagicMock(return_value=False)
    assert await client.send_telemetry({"lost": True}) is False
    client.disconnect()

    disabled = telemetry_client.TelemetryClient("", "2.2.0", telemetry_enabled=False)
    disabled.disconnect()
