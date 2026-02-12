"""Additional coverage tests for telemetry_client."""
# pylint: disable=missing-function-docstring,protected-access,exec-used,unspecified-encoding

from types import ModuleType
from pathlib import Path
from unittest.mock import MagicMock, patch

import telemetry_client


def test_ensure_connected_reconnect_succeeds_sets_connected(monkeypatch):
    """When _client exists but _connected is False, and is_connected() returns True,
    _ensure_connected should set _connected=True and return True."""
    client = telemetry_client.TelemetryClient("DEV1", "1.0.0")
    mock_client = MagicMock()
    mock_client.is_connected.return_value = True
    client._client = mock_client
    client._connected = False

    assert client._ensure_connected() is True
    assert client._connected is True


def test_ensure_connected_reconnect_exception_triggers_cleanup():
    """When reconnect() raises, _cleanup_client is called and _client becomes None,
    then _create_client is called."""
    client = telemetry_client.TelemetryClient("DEV1", "1.0.0")
    mock_client = MagicMock()
    mock_client.is_connected.return_value = False
    mock_client.reconnect.side_effect = RuntimeError("boom")
    client._client = mock_client
    client._connected = False
    client._create_client = MagicMock(return_value=False)

    assert client._ensure_connected() is False
    # _cleanup_client should have been called (loop_stop + disconnect on old client)
    mock_client.loop_stop.assert_called_once()
    mock_client.disconnect.assert_called_once()
    # After cleanup, _client is None → _create_client is called
    client._create_client.assert_called_once()


def test_ensure_connected_cleanup_exception_is_safe():
    """Even if loop_stop raises during cleanup, _ensure_connected should not crash."""
    client = telemetry_client.TelemetryClient("DEV1", "1.0.0")
    mock_client = MagicMock()
    mock_client.is_connected.return_value = False
    mock_client.reconnect.side_effect = RuntimeError("reconnect failed")
    mock_client.loop_stop.side_effect = RuntimeError("loop_stop boom")
    client._client = mock_client
    client._connected = False
    client._create_client = MagicMock(return_value=False)

    assert client._ensure_connected() is False
    assert client._client is None


def test_import_success_branch_exec(monkeypatch):
    source = Path(telemetry_client.__file__).read_text()

    fake_paho = ModuleType("paho")
    fake_mqtt = ModuleType("paho.mqtt")
    fake_client = ModuleType("paho.mqtt.client")
    fake_client.MQTTv311 = 4
    fake_paho.mqtt = fake_mqtt
    fake_mqtt.client = fake_client

    monkeypatch.setitem(__import__("sys").modules, "paho", fake_paho)
    monkeypatch.setitem(__import__("sys").modules, "paho.mqtt", fake_mqtt)
    monkeypatch.setitem(__import__("sys").modules, "paho.mqtt.client", fake_client)

    globs: dict = {
        "__name__": "telemetry_client_with_mqtt",
        "__file__": telemetry_client.__file__,
    }
    exec(compile(source, telemetry_client.__file__, "exec"), globs)

    assert globs["MQTT_AVAILABLE"] is True
    assert globs["mqtt"] is not None
