"""Defensive branch contracts for the local MQTT adapter."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mqtt import client as mqtt_module
from mqtt.client import MQTTClient
from settings_constraints import SettingConstraint


def _client(**overrides) -> MQTTClient:
    options = {
        "host": "127.0.0.1",
        "port": 1883,
        "username": "user",
        "password": "password",
        "namespace": "oig_local",
        "qos": 1,
        "state_retain": True,
        "control_enabled": True,
    }
    options.update(overrides)
    return MQTTClient(**options)


def _ready(client: MQTTClient) -> MagicMock:
    paho = MagicMock()
    paho.publish.return_value = SimpleNamespace(rc=0)
    client._client = paho
    client.connected = True
    return paho


def test_connect_none_timeout_disconnect_and_cleanup_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    client.disconnect()
    monkeypatch.setattr(mqtt_module, "PAHO_AVAILABLE", True)
    monkeypatch.setattr(client, "_create_client", lambda _device_id: None)
    assert client.connect("DEV") is False

    paho = MagicMock()
    monkeypatch.setattr(client, "_create_client", lambda _device_id: paho)
    monotonic = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(mqtt_module.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(mqtt_module.time, "sleep", lambda _seconds: None)
    assert client.connect("DEV", timeout=0.1) is False
    assert client._client is None

    broken = MagicMock()
    broken.loop_stop.side_effect = RuntimeError("cleanup failure")
    client._client = broken
    client.connected = True
    client.disconnect()
    assert client._client is None
    assert client.connected is False


def test_create_client_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(username="")
    monkeypatch.setattr(mqtt_module, "_paho_mqtt", None)
    assert client._create_client("DEV") is None

    created: list[MagicMock] = []

    def factory(**kwargs):
        result = MagicMock()
        result.kwargs = kwargs
        created.append(result)
        return result

    paho_without_callback_version = SimpleNamespace(Client=factory)
    monkeypatch.setattr(mqtt_module, "_paho_mqtt", paho_without_callback_version)
    bare = client._create_client("DEV")
    assert bare is created[-1]
    assert created[-1].kwargs == {"client_id": "oig_local_DEV_v2", "protocol": 4}
    created[-1].username_pw_set.assert_not_called()

    paho_with_callback_version = SimpleNamespace(
        MQTTv311=4,
        CallbackAPIVersion=SimpleNamespace(VERSION1="v1"),
        Client=factory,
    )
    monkeypatch.setattr(mqtt_module, "_paho_mqtt", paho_with_callback_version)
    authenticated = _client()._create_client("DEV")
    assert authenticated is created[-1]
    assert created[-1].kwargs["callback_api_version"] == "v1"
    created[-1].username_pw_set.assert_called_once_with("user", "password")


def test_connection_callbacks_cover_restore_failure_and_unexpected_disconnect() -> None:
    client = _client(control_enabled=True)
    paho = MagicMock()
    paho.publish.return_value = SimpleNamespace(rc=0)
    paho.subscribe.return_value = (4, 1)
    client._subscriptions["oig_local/DEV/set/#"] = MagicMock()
    client._connect_device_id = "DEV"

    client._on_connect(paho, None, None, 0)
    assert client.connected is True
    client._on_disconnect(paho, None, 1)
    assert client.connected is False


def test_publish_state_handles_disappearing_client_and_availability_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(client, "is_ready", lambda: True)
    assert client.publish_state("DEV", "table", {}) is False

    paho = _ready(client)
    paho.publish.side_effect = [RuntimeError("availability"), SimpleNamespace(rc=0)]
    assert client.publish_state("DEV", "table", {"value": 1}) is True
    assert "DEV" not in client._availability_online_sent

    paho.publish.side_effect = None
    paho.publish.return_value = SimpleNamespace(rc=0)
    client._availability_online_sent.add("KNOWN")
    assert client.publish_state("KNOWN", "table", {"value": 2}) is True


def test_discovery_defensive_and_optional_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "is_ready", lambda: True)
    assert client.send_discovery(device_id="DEV", table="table", sensor_key="KEY", sensor_name="Name") is False

    paho = _ready(client)
    paho.publish.return_value = SimpleNamespace(rc=1)
    assert client.send_discovery(device_id="DEV", table="table", sensor_key="KEY", sensor_name="Name") is False

    paho.publish.return_value = SimpleNamespace(rc=0)
    assert client.send_discovery(
        device_id="DEV",
        table="table",
        sensor_key="KEY2",
        sensor_name="Name",
        icon="mdi:test",
        entity_category="diagnostic",
        device_mapping="battery",
    ) is True
    payload = paho.publish.call_args.args[1]
    assert '"icon": "mdi:test"' in payload
    assert '"entity_category": "diagnostic"' in payload
    assert "via_device" in payload


def test_setting_discovery_short_circuits_and_propagates_control_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = next(iter(mqtt_module.CONTROL_WRITE_WHITELIST))
    sensor_key = next(iter(mqtt_module.CONTROL_WRITE_WHITELIST[table]))
    client = _client(control_enabled=False)
    _ready(client)
    unique_id = f"{client.namespace}_DEV_{table}_{sensor_key}".lower()
    client._discovery_sent.update({unique_id, f"{unique_id}_cfg"})
    assert client.send_discovery(
        device_id="DEV", table=table, sensor_key=sensor_key, sensor_name="Setting"
    ) is True

    client._discovery_sent.clear()
    client._discovery_sent.add(f"{unique_id}_cfg")
    assert client.send_discovery(
        device_id="DEV", table=table, sensor_key=sensor_key, sensor_name="Setting"
    ) is True

    client._discovery_sent.clear()
    monkeypatch.setattr(client, "_publish_control_components", lambda **_kwargs: False)
    assert client.send_discovery(
        device_id="DEV", table=table, sensor_key=sensor_key, sensor_name="Setting"
    ) is False


def test_number_setting_without_constraint_and_discovery_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = next(iter(mqtt_module.CONTROL_WRITE_WHITELIST))
    sensor_key = next(iter(mqtt_module.CONTROL_WRITE_WHITELIST[table]))
    client = _client(control_enabled=True)
    paho = _ready(client)
    monkeypatch.setattr(mqtt_module, "SETTING_CONSTRAINTS", {})
    assert client.send_discovery(
        device_id="DEV",
        table=table,
        sensor_key=sensor_key,
        sensor_name="Setting",
        unit="W",
        device_class="power",
    ) is True

    client._discovery_sent.clear()
    monkeypatch.setattr(
        mqtt_module,
        "SETTING_CONSTRAINTS",
        {(table, sensor_key): SettingConstraint()},
    )
    assert client.send_discovery(
        device_id="DEV",
        table=table,
        sensor_key=sensor_key,
        sensor_name="Unbounded setting",
    ) is True

    paho.publish.side_effect = RuntimeError("discovery failure")
    assert client.send_discovery(
        device_id="DEV", table="table", sensor_key="OTHER", sensor_name="Other"
    ) is False


def test_control_components_and_tombstones_cover_failure_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    assert client.publish_control_discovery_tombstones("DEV") is False
    assert client._publish_control_components(
        control_unique_id="cfg", active_component=None, active_payload=None
    ) is False
    monkeypatch.setattr(client, "is_ready", lambda: True)
    assert client.publish_control_discovery_tombstones("DEV") is False

    paho = _ready(client)
    paho.publish.side_effect = [SimpleNamespace(rc=1), RuntimeError("publish failure"), SimpleNamespace(rc=0)]
    assert client._publish_control_components(
        control_unique_id="cfg", active_component="number", active_payload={"name": "Number"}
    ) is False

    results = iter((SimpleNamespace(rc=1), RuntimeError("tombstone failure")))

    def publish(*_args, **_kwargs):
        value = next(results, SimpleNamespace(rc=0))
        if isinstance(value, Exception):
            raise value
        return value

    paho.publish.side_effect = publish
    assert client.publish_control_discovery_tombstones({"unknown", "bad device", "DEV"}) is False


def test_identity_enum_and_topic_helpers_cover_all_shapes() -> None:
    client = _client()
    client._remember_device_id("unknown")
    assert client._all_known_device_ids == set()
    assert client._is_safe_device_id(123) is False
    assert client._is_safe_device_id("unknown") is False
    assert client._is_safe_device_id("bad device") is False
    assert client._is_safe_device_id("DEV.1:2") is True
    assert client._is_binary_control_constraint(None) is False
    assert client._ordered_enum_items(None) == []
    assert client._ordered_enum_items({"b": "B", "a": "A"}) == [("a", "A"), ("b", "B")]
    assert client._is_select_control("table", "key", None) is False
    assert client._topic_matches("a/b", "a/b") is True
    assert client._topic_matches("a/#", "a/b/c") is True
    assert client._topic_matches("a/+/c", "a/b/c") is True
    assert client._topic_matches("a/b", "a") is False
    assert client._topic_matches("a/b", "a/b/c") is False


def test_subscription_failure_matrix_and_unmatched_message(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    callback = MagicMock()
    assert client.subscribe("topic", callback) is False

    monkeypatch.setattr(client, "is_ready", lambda: True)
    assert client.subscribe("topic", callback) is False
    assert client.unsubscribe("topic") is False

    paho = _ready(client)
    paho.subscribe.side_effect = RuntimeError("subscribe failure")
    assert client.subscribe("topic", callback) is False

    paho.unsubscribe.side_effect = [(0, 1), (4, 2), RuntimeError("unsubscribe failure")]
    assert client.unsubscribe("one") is True
    assert client.unsubscribe("two") is False
    assert client.unsubscribe("three") is False

    client._subscriptions = {"a/+/c": callback, "x/#": callback}
    client._on_message(None, None, SimpleNamespace(topic="a/b/d", payload=b"x", retain=False))
    callback.assert_not_called()
