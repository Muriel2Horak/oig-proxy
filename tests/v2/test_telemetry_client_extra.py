"""Additional coverage tests for telemetry.client."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access,too-many-instance-attributes

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import telemetry.client as telemetry_client


class FakeMQTTClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.on_connect = None
        self.on_disconnect = None
        self.auto_connect = True
        self.connected = False
        self.publish_rc = 0
        self.publish_rcs: list[int] = []
        self.raise_on_publish = False
        self.raise_on_reconnect = False
        self.loop_start_calls = 0
        self.loop_stop_calls = 0
        self.disconnect_calls = 0
        self.reconnect_calls = 0
        self.published: list[tuple[str, dict[str, object], int]] = []

    def connect(self, host: str, port: int, keepalive: int = 60) -> None:
        # pylint: disable=attribute-defined-outside-init
        self.host = host
        self.port = port
        self.keepalive = keepalive

    def loop_start(self) -> None:
        self.loop_start_calls += 1
        if self.auto_connect and self.on_connect is not None:
            self.connected = True
            # pylint: disable=not-callable
            self.on_connect(self, None, None, 0, None)

    def loop_stop(self) -> None:
        self.loop_stop_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False
        if self.on_disconnect is not None:
            self.on_disconnect(self, None, None, 0, None)

    def is_connected(self) -> bool:
        return self.connected

    def reconnect(self) -> None:
        self.reconnect_calls += 1
        if self.raise_on_reconnect:
            raise RuntimeError("reconnect failed")
        self.connected = True

    def publish(self, topic: str, message: str, qos: int = 1):
        if self.raise_on_publish:
            raise RuntimeError("publish failed")
        self.published.append((topic, json.loads(message), qos))
        rc = self.publish_rcs.pop(0) if self.publish_rcs else self.publish_rc
        return SimpleNamespace(rc=rc)


def _install_fake_mqtt(monkeypatch: pytest.MonkeyPatch, *, auto_connect: bool = True) -> list[FakeMQTTClient]:
    created: list[FakeMQTTClient] = []

    def factory(**kwargs):
        client = FakeMQTTClient(**kwargs)
        client.auto_connect = auto_connect
        created.append(client)
        return client

    fake_mqtt = SimpleNamespace(
        MQTTv311="MQTTv311",
        CallbackAPIVersion=SimpleNamespace(VERSION2="v2"),
        Client=factory,
    )
    monkeypatch.setattr(telemetry_client, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(telemetry_client, "import_module", lambda _name: fake_mqtt)
    monkeypatch.setattr(telemetry_client.time, "sleep", lambda _seconds: None)
    return created


def test_get_instance_hash_uses_supervisor_token_and_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "secret-token")
    token_hash = telemetry_client._get_instance_hash()

    monkeypatch.delenv("SUPERVISOR_TOKEN")
    monkeypatch.setenv("HOSTNAME", "proxy-host")
    host_hash = telemetry_client._get_instance_hash()

    assert len(token_hash) == 32
    assert len(host_hash) == 32
    assert token_hash != host_hash


def test_telemetry_buffer_cleanup_invalid_json_and_close(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(telemetry_client, "BUFFER_MAX_MESSAGES", 2)
    monkeypatch.setattr(telemetry_client, "BUFFER_MAX_AGE_HOURS", 1)
    monkeypatch.setattr(telemetry_client.time, "time", lambda: 10_000.0)

    buffer = telemetry_client.TelemetryBuffer(db_path=tmp_path / "buffer.db")
    assert buffer.store("topic/1", {"value": 1}) is True

    assert buffer._conn is not None
    buffer._conn.execute(
        "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
        ("topic/old", json.dumps({"stale": True}), 1.0),
    )
    buffer._conn.execute(
        "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
        ("topic/bad", "{", 9_999.0),
    )
    buffer._conn.execute(
        "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
        ("topic/2", json.dumps({"value": 2}), 9_999.5),
    )
    buffer._conn.commit()

    buffer._cleanup()
    pending = buffer.get_pending(limit=10)

    assert [topic for _, topic, _ in pending] == ["topic/2", "topic/1"]
    assert buffer.count() == 2

    buffer.remove(pending[0][0])
    assert buffer.count() == 1

    buffer.close()
    assert buffer._conn is None
    assert buffer.count() == 0


def test_create_client_success_and_timeout_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    created = _install_fake_mqtt(monkeypatch)

    client = telemetry_client.TelemetryClient(
        "dev-1",
        "2.0.0",
        telemetry_enabled=True,
        db_path=tmp_path / "telemetry.db",
    )
    assert client._create_client() is True

    mqtt_client = created[0]
    assert client.is_ready is True
    assert mqtt_client.kwargs["callback_api_version"] == "v2"
    assert mqtt_client.kwargs["client_id"].startswith("oig-proxy-dev-1-")
    assert mqtt_client.host == "telemetry.muriel-cz.cz"
    assert mqtt_client.port == 1883

    mqtt_client.disconnect()
    assert client.is_ready is False

    _install_fake_mqtt(monkeypatch, auto_connect=False)
    timeout_client = telemetry_client.TelemetryClient(
        "dev-2",
        "2.0.0",
        telemetry_enabled=True,
        db_path=tmp_path / "telemetry-timeout.db",
    )
    assert timeout_client._create_client() is False
    assert timeout_client._client is None
    assert timeout_client._connect_backoff_s == 10.0


def test_ensure_connected_publish_and_flush_buffer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_mqtt(monkeypatch)
    client = telemetry_client.TelemetryClient(
        "dev-3",
        "2.0.0",
        telemetry_enabled=True,
        db_path=tmp_path / "telemetry-flush.db",
    )

    fake_client = FakeMQTTClient(client_id="x")
    fake_client.connected = False
    fake_client.raise_on_reconnect = True
    client._client = fake_client
    monkeypatch.setattr(client, "_create_client", lambda: False)
    monkeypatch.setattr(telemetry_client.time, "monotonic", lambda: 100.0)
    client._last_connect_attempt = 0.0
    client._connect_backoff_s = 5.0
    assert client._ensure_connected() is False
    assert client._client is None

    client._client = FakeMQTTClient(client_id="y")
    client._client.connected = True
    client._connected = False
    client._last_connect_attempt = 100.0
    client._connect_backoff_s = 60.0
    monkeypatch.setattr(telemetry_client.time, "monotonic", lambda: 110.0)
    assert client._ensure_connected() is False

    working_client = FakeMQTTClient(client_id="z")
    working_client.connected = True
    working_client.publish_rcs = [0, 0, 1]
    client._client = working_client
    client._connected = True
    assert client._buffer is not None
    assert client._buffer.store("oig/topic/1", {"value": 1}) is True
    assert client._buffer.store("oig/topic/2", {"value": 2}) is True

    assert client._publish_sync("oig/topic/live", {"value": 3}) is True
    assert client._flush_buffer_sync() == 1
    assert client._buffer.count() == 1

    working_client.raise_on_publish = True
    assert client._publish_sync("oig/topic/live", {"value": 4}) is False


@pytest.mark.asyncio
async def test_send_telemetry_send_event_wrappers_and_status_properties(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(telemetry_client, "MQTT_AVAILABLE", True)
    client = telemetry_client.TelemetryClient(
        "dev-4",
        "2.0.0",
        telemetry_enabled=True,
        db_path=tmp_path / "telemetry-send.db",
    )

    publish_calls: list[tuple[str, dict[str, object]]] = []
    client._publish_sync = lambda topic, payload: publish_calls.append((topic, payload)) or False
    assert client._buffer is not None
    assert await client.send_telemetry({"uptime_s": 12}) is True
    assert publish_calls[0][0] == "oig/telemetry/dev-4"
    assert client.get_buffer_count() == 1
    assert client.is_buffering is True

    client._publish_sync = lambda topic, payload: True
    client._flush_buffer_sync = MagicMock(return_value=1)
    client._last_buffer_flush = 0.0
    telemetry_result = await client.send_telemetry({"uptime_s": 13})
    assert telemetry_result is True
    client._flush_buffer_sync.assert_called_once()

    client._buffer.store = MagicMock(return_value=False)
    client._publish_sync = lambda topic, payload: False
    assert await client.send_event("event_type", {"flag": True}) is False

    client.send_event = AsyncMock(return_value=True)
    await client.event_error_cloud_timeout("cloud", 1.5)
    await client.event_error_cloud_disconnect("eof")
    await client.event_error_box_disconnect("1.2.3.4:5710")
    await client.event_error_crc("frame")
    await client.event_error_mqtt_local("broker", "boom")
    await client.event_warning_mode_fallback("online", "offline", "reason")
    await client.event_box_reconnect("1.2.3.4:5710")
    await client.event_cloud_reconnect()
    await client.event_startup()
    await client.event_shutdown()

    assert client.send_event.await_count == 10

    client.event_startup = AsyncMock(return_value=True)
    assert await client.provision() is True
    client.event_startup.assert_awaited_once()

    disabled = telemetry_client.TelemetryClient("", "2.0.0", telemetry_enabled=False)
    assert await disabled.send_telemetry({}) is False
    assert await disabled.send_event("x") is False

    client._connected = True
    assert client.is_ready is True
    client.disconnect()
    assert client._client is None
    assert client._buffer is not None
    assert client.get_buffer_count() == 0


def test_parse_mqtt_url_defaults_on_bad_port() -> None:
    assert telemetry_client.TelemetryClient._parse_mqtt_url("mqtt://host.example:1884") == ("host.example", 1884)
    assert telemetry_client.TelemetryClient._parse_mqtt_url("tcp://host.example:not-a-port") == ("host.example", 1883)
    assert telemetry_client.TelemetryClient._parse_mqtt_url("host.example") == ("host.example", 1883)
