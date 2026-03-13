"""
Testy pro mqtt/client.py — MQTTClient.
"""
from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# sys.path nastaven v conftest.py
from mqtt.client import MQTTClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client(**kwargs) -> MQTTClient:
    defaults = dict(
        host="127.0.0.1",
        port=1883,
        username="user",
        password="pass",
        namespace="oig_local",
        qos=1,
        state_retain=True,
    )
    defaults.update(kwargs)
    return MQTTClient(**defaults)


def inject_mock_paho(client: MQTTClient, connected: bool = True) -> MagicMock:
    """Vloží mock paho klient a nastaví connected stav."""
    mock_paho = MagicMock()
    mock_paho.publish.return_value = MagicMock(rc=0)
    client._client = mock_paho
    client.connected = connected
    return mock_paho


# ---------------------------------------------------------------------------
# is_ready()
# ---------------------------------------------------------------------------

def test_is_ready_false_when_not_connected():
    c = make_client()
    assert c.is_ready() is False


def test_is_ready_false_when_client_none():
    c = make_client()
    c.connected = True
    c._client = None
    assert c.is_ready() is False


def test_is_ready_true_when_client_and_connected():
    c = make_client()
    inject_mock_paho(c, connected=True)
    assert c.is_ready() is True


# ---------------------------------------------------------------------------
# publish_state()
# ---------------------------------------------------------------------------

def test_publish_state_topic_format():
    """Topic je {namespace}/{device_id}/{table}/state."""
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    result = c.publish_state("DEV01", "tbl_invertor", {"P": 100})

    assert result is True
    assert mock_paho.publish.call_count >= 1
    topic = mock_paho.publish.call_args[0][0]
    assert topic == "oig_local/DEV01/tbl_invertor/state"


def test_publish_state_payload_is_json():
    """Payload je validní JSON s klíčem P."""
    c = make_client()
    mock_paho = inject_mock_paho(c)

    c.publish_state("DEV01", "tbl_batt", {"SoC": 85, "V": 52.1})

    payload_str = mock_paho.publish.call_args[0][1]
    parsed = json.loads(payload_str)
    assert parsed["SoC"] == 85
    assert parsed["V"] == pytest.approx(52.1)


def test_publish_state_returns_false_when_not_ready():
    """publish_state vrátí False pokud není připojeno."""
    c = make_client()
    # c.connected = False, c._client = None
    result = c.publish_state("DEV01", "tbl_batt", {"SoC": 85})
    assert result is False
    assert c.publish_failed == 1


def test_publish_state_increments_success_counter():
    c = make_client()
    inject_mock_paho(c)

    c.publish_state("DEV01", "tbl_x", {"k": "v"})
    assert c.publish_count == 1
    assert c.publish_success == 1
    assert c.publish_failed == 0


def test_publish_state_increments_failed_counter_on_rc_error():
    """Pokud paho.publish vrátí rc != 0, publish_failed se zvýší."""
    c = make_client()
    mock_paho = inject_mock_paho(c)
    mock_paho.publish.return_value = MagicMock(rc=4)

    result = c.publish_state("DEV01", "tbl_x", {"k": "v"})
    assert result is False
    assert c.publish_failed == 1


def test_publish_state_handles_exception():
    """Pokud paho.publish vyhodí výjimku, publish_failed se zvýší."""
    c = make_client()
    mock_paho = inject_mock_paho(c)
    mock_paho.publish.side_effect = RuntimeError("broker down")

    result = c.publish_state("DEV01", "tbl_x", {"k": "v"})
    assert result is False
    assert c.publish_failed == 1


def test_publish_state_uses_qos_and_retain():
    """publish_state předává správné qos a retain."""
    c = make_client(qos=0, state_retain=False)
    mock_paho = inject_mock_paho(c)

    c.publish_state("DEV01", "tbl_x", {"k": "v"})

    kwargs = mock_paho.publish.call_args[1]
    assert kwargs["qos"] == 0
    assert kwargs["retain"] is False


# ---------------------------------------------------------------------------
# send_discovery()
# ---------------------------------------------------------------------------

def test_send_discovery_topic_format():
    """Discovery topic je homeassistant/sensor/{unique_id}/config."""
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_invertor",
        sensor_key="P",
        sensor_name="Power",
    )

    topic = mock_paho.publish.call_args[0][0]
    assert topic == "homeassistant/sensor/oig_local_dev01_tbl_invertor_p/config"


def test_send_discovery_payload_structure():
    """Discovery payload obsahuje požadované klíče."""
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_batt",
        sensor_key="SoC",
        sensor_name="Battery SoC",
        unit="%",
        device_class="battery",
        state_class="measurement",
    )

    payload = json.loads(mock_paho.publish.call_args[0][1])
    assert payload["name"] == "Battery SoC"
    assert payload["unit_of_measurement"] == "%"
    assert payload["device_class"] == "battery"
    assert payload["state_class"] == "measurement"
    assert "state_topic" in payload
    assert payload["state_topic"] == "oig_local/DEV01/tbl_batt/state"
    assert "availability" in payload
    assert payload["availability"][0]["topic"] == "oig_local/DEV01/availability"
    assert "device" in payload
    assert "Střídač (DEV01)" in payload["device"]["name"]


def test_send_discovery_uses_device_mapping_and_via_device():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_batt",
        sensor_key="BAT_MIN",
        sensor_name="Baterie - Minimum",
        device_mapping="battery",
    )

    payload = json.loads(mock_paho.publish.call_args[0][1])
    assert payload["device"]["identifiers"] == ["oig_local_DEV01_battery"]
    assert payload["device"]["name"] == "Baterie (DEV01)"
    assert payload["device"]["via_device"] == "oig_local_DEV01_inverter"


def test_send_discovery_creates_number_command_entity_for_whitelisted_setting():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_batt_prms",
        sensor_key="BAT_MIN",
        sensor_name="Baterie - Minimum pro vybijeni",
        unit="%",
        device_mapping="battery",
    )

    assert mock_paho.publish.call_count == 1
    control_topic = mock_paho.publish.call_args_list[0][0][0]
    control_payload = json.loads(mock_paho.publish.call_args_list[0][0][1])
    assert control_topic == "homeassistant/number/oig_local_dev01_tbl_batt_prms_bat_min_cfg/config"
    assert control_payload["command_topic"] == "oig_local/DEV01/set/tbl_batt_prms/BAT_MIN"
    assert control_payload["entity_category"] == "config"
    assert control_payload["min"] == 20
    assert control_payload["max"] == 100
    assert control_payload["step"] == 1


def test_send_discovery_uses_enum_value_template_for_sensor_state():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_invertor_prms",
        sensor_key="MODE",
        sensor_name="Režim",
        enum_map={"0": "Home 1", "1": "Home 2"},
    )

    payload = json.loads(mock_paho.publish.call_args_list[0][0][1])
    assert "(value_json.MODE | string)" in payload["value_template"]
    assert '"0": "Home 1"' in payload["value_template"]


def test_send_discovery_keeps_raw_template_for_control_number_entity_with_enum():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_box_prms",
        sensor_key="MODE",
        sensor_name="Režim",
        enum_map={"0": "Home 1", "1": "Home 2"},
    )

    assert mock_paho.publish.call_count == 1
    control_payload = json.loads(mock_paho.publish.call_args_list[0][0][1])
    assert control_payload["value_template"] == "{{ value_json.MODE }}"


def test_send_discovery_creates_switch_entity_for_binary_setting():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_invertor_prm1",
        sensor_key="BUZ_MUT",
        sensor_name="Střídač - Ztlumit bzučák",
        is_binary=True,
        device_mapping="inverter",
    )

    assert mock_paho.publish.call_count == 1
    topic = mock_paho.publish.call_args_list[0][0][0]
    payload = json.loads(mock_paho.publish.call_args_list[0][0][1])
    assert topic == "homeassistant/switch/oig_local_dev01_tbl_invertor_prm1_buz_mut_cfg/config"
    assert payload["payload_on"] == 1
    assert payload["payload_off"] == 0
    assert payload["state_on"] == 1
    assert payload["state_off"] == 0


def test_send_discovery_deduplication():
    """Opakované volání send_discovery pro stejný sensor posílá jen jednou."""
    c = make_client()
    mock_paho = inject_mock_paho(c)

    c.send_discovery(device_id="DEV01", table="t", sensor_key="k", sensor_name="K")
    c.send_discovery(device_id="DEV01", table="t", sensor_key="k", sensor_name="K")

    assert mock_paho.publish.call_count == 1


def test_send_discovery_returns_false_when_not_ready():
    c = make_client()
    # Není připojeno
    result = c.send_discovery(device_id="DEV01", table="t", sensor_key="k", sensor_name="K")
    assert result is False


def test_send_discovery_clears_dedup_on_reconnect():
    """Po on_connect() je _discovery_sent vymazán (simulace reconnectu)."""
    c = make_client()
    mock_paho = inject_mock_paho(c)

    c.send_discovery(device_id="DEV01", table="t", sensor_key="k", sensor_name="K")
    assert mock_paho.publish.call_count == 1

    # Simulujeme reconnect — on_connect vymaže cache
    mock_paho2 = MagicMock()
    mock_paho2._oig_device_id = "DEV01"
    mock_paho2.publish.return_value = MagicMock(rc=0)
    c._on_connect(mock_paho2, None, None, 0)

    # Znovu injektujeme mock do c._client
    inject_mock_paho(c)

    c.send_discovery(device_id="DEV01", table="t", sensor_key="k", sensor_name="K")
    assert c._client.publish.call_count == 1  # Opět posláno


def test_send_discovery_for_table_skips_internal_keys():
    """send_discovery_for_table přeskočí klíče začínající _."""
    c = make_client()
    mock_paho = inject_mock_paho(c)

    c.send_discovery_for_table("DEV01", "tbl_x", {
        "P": 100,
        "_raw": "skip",
        "_internal": True,
        "Q": 50,
    })

    # Jen P a Q — ne _raw a _internal
    calls = mock_paho.publish.call_args_list
    topics = [call[0][0] for call in calls]
    assert any("_p/config" in t for t in topics)
    assert any("_q/config" in t for t in topics)
    assert not any("_raw" in t for t in topics)
    assert not any("_internal" in t for t in topics)


# ---------------------------------------------------------------------------
# on_connect / on_disconnect callbacks
# ---------------------------------------------------------------------------

def test_on_connect_sets_connected_true():
    c = make_client()
    mock_paho = MagicMock()
    mock_paho._oig_device_id = "DEV01"
    mock_paho.publish.return_value = MagicMock(rc=0)
    c._client = mock_paho

    c._on_connect(mock_paho, None, None, rc=0)

    assert c.connected is True


def test_on_connect_publishes_availability_online():
    """on_connect publikuje availability=online."""
    c = make_client(namespace="oig_local")
    mock_paho = MagicMock()
    mock_paho._oig_device_id = "DEV01"
    mock_paho.publish.return_value = MagicMock(rc=0)
    c._client = mock_paho

    c._on_connect(mock_paho, None, None, rc=0)

    mock_paho.publish.assert_called_once_with(
        "oig_local/DEV01/availability", "online", retain=True, qos=1
    )


def test_on_connect_rc_nonzero_sets_connected_false():
    c = make_client()
    mock_paho = MagicMock()
    c._client = mock_paho

    c._on_connect(mock_paho, None, None, rc=5)

    assert c.connected is False


def test_on_disconnect_sets_connected_false():
    c = make_client()
    inject_mock_paho(c, connected=True)

    c._on_disconnect(None, None, rc=0)

    assert c.connected is False


# ---------------------------------------------------------------------------
# health_check_loop()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_loop_reconnects_when_disconnected():
    """health_check_loop zavolá connect() pokud connected=False."""
    c = make_client()
    c.connected = False

    reconnect_calls = []

    def fake_connect(device_id: str) -> bool:
        reconnect_calls.append(device_id)
        c.connected = True
        return True

    c.connect = fake_connect

    # Zkrátíme interval na minimum pro test
    c.HEALTH_CHECK_INTERVAL = 0.01

    async def run_one_iteration():
        # Necháme proběhnout jen jednu iteraci (sleep + check)
        task = asyncio.create_task(c.health_check_loop("DEV01"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await run_one_iteration()
    assert reconnect_calls == ["DEV01"]


@pytest.mark.asyncio
async def test_health_check_loop_does_not_reconnect_when_connected():
    """health_check_loop nevolá connect() pokud je již připojeno."""
    c = make_client()
    inject_mock_paho(c, connected=True)

    reconnect_calls = []
    original_connect = c.connect

    def fake_connect(device_id: str) -> bool:
        reconnect_calls.append(device_id)
        return True

    c.connect = fake_connect
    c.HEALTH_CHECK_INTERVAL = 0.01

    task = asyncio.create_task(c.health_check_loop("DEV01"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert reconnect_calls == []


# ---------------------------------------------------------------------------
# connect() — unit test s mock paho
# ---------------------------------------------------------------------------

def test_connect_returns_false_when_paho_unavailable():
    """Pokud paho není dostupné, connect() vrátí False."""
    c = make_client()
    with patch("mqtt.client.PAHO_AVAILABLE", False):
        result = c.connect("DEV01")
    assert result is False


def test_connect_returns_false_on_os_error():
    """Pokud paho.connect() vyhodí OSError, vrátí False."""
    c = make_client()
    mock_paho_client = MagicMock()
    mock_paho_client.connect.side_effect = OSError("refused")
    mock_paho_client.loop_start = MagicMock()

    with patch("mqtt.client.PAHO_AVAILABLE", True), \
         patch.object(c, "_create_client", return_value=mock_paho_client):
        result = c.connect("DEV01", timeout=0.1)

    assert result is False


def test_connect_returns_true_when_on_connect_fires():
    """connect() vrátí True pokud on_connect callback nastaví connected=True."""
    c = make_client()
    mock_paho_client = MagicMock()
    mock_paho_client._oig_device_id = "DEV01"

    def fake_loop_start():
        # Simulujeme okamžitý on_connect callback
        c.connected = True

    mock_paho_client.connect = MagicMock()
    mock_paho_client.loop_start = MagicMock(side_effect=fake_loop_start)

    with patch("mqtt.client.PAHO_AVAILABLE", True), \
         patch.object(c, "_create_client", return_value=mock_paho_client):
        result = c.connect("DEV01", timeout=0.5)

    assert result is True
