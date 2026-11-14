"""
Testy pro mqtt/client.py — MQTTClient.
"""
from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# sys.path nastaven v conftest.py
from mqtt.client import MQTTClient
from sensor.loader import SensorMapLoader
from settings_constraints import PowerControlLimits, is_setting_allowed, validate_setting_value


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


def load_released_sensor_map() -> SensorMapLoader:
    """Load the same metadata packaged with the add-on."""
    loader = SensorMapLoader(str(
        Path(__file__).resolve().parents[3] / "addon" / "oig-proxy" / "sensor_map.json"
    ))
    loader.load()
    return loader


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
    assert payload["object_id"] == "oig_local_dev01_tbl_batt_soc"
    assert payload["default_entity_id"] == "sensor.oig_local_dev01_tbl_batt_soc"
    assert payload["unit_of_measurement"] == "%"
    assert payload["device_class"] == "battery"
    assert payload["state_class"] == "measurement"
    assert "state_topic" in payload
    assert payload["state_topic"] == "oig_local/DEV01/tbl_batt/state"
    assert payload["value_template"] == "{{ value_json.get('SoC') }}"
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
    assert control_payload["object_id"] == "oig_local_dev01_tbl_batt_prms_bat_min_cfg"
    assert control_payload["default_entity_id"] == "number.oig_local_dev01_tbl_batt_prms_bat_min_cfg"
    assert control_payload["command_topic"] == "oig_local/DEV01/set/tbl_batt_prms/BAT_MIN"
    assert control_payload["entity_category"] == "config"
    assert control_payload["mode"] == "box"
    assert control_payload["min"] == 20
    assert control_payload["max"] == 100
    assert control_payload["step"] == 1


def test_send_discovery_creates_switch_for_zero_one_setting_constraint():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_box_prms",
        sensor_key="SA",
        sensor_name="Systém - Okamžité odeslání dat",
    )

    assert mock_paho.publish.call_count == 1
    control_topic = mock_paho.publish.call_args_list[0][0][0]
    control_payload = json.loads(mock_paho.publish.call_args_list[0][0][1])
    assert control_topic == "homeassistant/switch/oig_local_dev01_tbl_box_prms_sa_cfg/config"
    assert control_payload["default_entity_id"] == "switch.oig_local_dev01_tbl_box_prms_sa_cfg"
    assert control_payload["command_topic"] == "oig_local/DEV01/set/tbl_box_prms/SA"
    assert control_payload["payload_on"] == 1
    assert control_payload["payload_off"] == 0
    assert control_payload["state_on"] == 1
    assert control_payload["state_off"] == 0


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
    assert payload["object_id"] == "oig_local_dev01_tbl_invertor_prms_mode"
    assert "(value_json.get('MODE') | string)" in payload["value_template"]
    assert '"0": "Home 1"' in payload["value_template"]
    assert "value_json.get('MODE')" in payload["value_template"]


def test_send_discovery_creates_sensor_and_select_for_box_mode():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_box_prms",
        sensor_key="MODE",
        sensor_name="Režim",
        enum_map={"0": "Home 1", "1": "Home 2"},
    )

    assert mock_paho.publish.call_count == 2
    sensor_topic = mock_paho.publish.call_args_list[0][0][0]
    sensor_payload = json.loads(mock_paho.publish.call_args_list[0][0][1])
    control_topic = mock_paho.publish.call_args_list[1][0][0]
    control_payload = json.loads(mock_paho.publish.call_args_list[1][0][1])

    assert sensor_topic == "homeassistant/sensor/oig_local_dev01_tbl_box_prms_mode/config"
    assert sensor_payload["object_id"] == "oig_local_dev01_tbl_box_prms_mode"
    assert sensor_payload["default_entity_id"] == "sensor.oig_local_dev01_tbl_box_prms_mode"
    assert '"0": "Home 1"' in sensor_payload["value_template"]
    assert "value_json.get('MODE')" in sensor_payload["value_template"]

    assert control_topic == "homeassistant/select/oig_local_dev01_tbl_box_prms_mode_cfg/config"
    assert control_payload["object_id"] == "oig_local_dev01_tbl_box_prms_mode_cfg"
    assert control_payload["default_entity_id"] == "select.oig_local_dev01_tbl_box_prms_mode_cfg"
    assert control_payload["options"] == ["Home 1", "Home 2"]
    assert "value_json.get('MODE')" in control_payload["value_template"]
    assert '"Home 2": "1"' in control_payload["command_template"]


def test_released_box_mode_keeps_its_entity_ids_topics_and_six_options():
    """Use shipped metadata to verify the two MODE entities and the released domain."""
    loader = load_released_sensor_map()
    mode = loader.lookup("tbl_box_prms", "MODE")
    assert mode is not None
    client = make_client(namespace="custom")
    broker = inject_mock_paho(client)

    assert client.send_discovery(
        device_id="BOX01", table="tbl_box_prms", sensor_key="MODE",
        sensor_name="Režim", enum_map=mode["enum_map"],
    )

    publications = {
        args[0]: json.loads(args[1]) for args, _kwargs in broker.publish.call_args_list
    }
    state_id = "custom_box01_tbl_box_prms_mode"
    control_id = state_id + "_cfg"
    assert set(publications) == {
        f"homeassistant/sensor/{state_id}/config", f"homeassistant/select/{control_id}/config",
    }
    control = publications[f"homeassistant/select/{control_id}/config"]
    assert control["unique_id"] == control_id
    assert control["default_entity_id"] == "select.oig_local_box01_tbl_box_prms_mode_cfg"
    assert control["state_topic"] == "custom/BOX01/tbl_box_prms/state"
    assert control["command_topic"] == "custom/BOX01/set/tbl_box_prms/MODE"
    assert control["options"] == ["Home 1", "Home 2", "Home 3", "Home UPS", "Home 5", "Home 6"]
    assert control["optimistic"] is False
    assert control["retain"] is False
    for value in range(6):
        assert validate_setting_value("tbl_box_prms", "MODE", str(value)) == (True, value, "")
    for value in (-1, 6, 1.5, "5.0000000000000001", "NaN", "Infinity", ""):
        assert validate_setting_value("tbl_box_prms", "MODE", value)[0] is False


def test_inverter_priority_preserves_reviewed_read_only_labels():
    """The reviewed PRRTY labels accompany observations without enabling commands."""
    metadata = load_released_sensor_map().lookup("tbl_invertor_prms", "PRRTY")
    assert metadata is not None
    assert metadata["enum_map"] == {
        "0": "Baterie, zátěž, síť",
        "1": "Zátěž, baterie, síť",
        "2": "Riziko nabíjení ze sítě a vypnutí přetoků",
    }
    assert is_setting_allowed("tbl_invertor_prms", "PRRTY") is False


@pytest.mark.parametrize(
    ("table", "key", "value"),
    [("tbl_invertor_prm1", "AAC_MAX_CHRG", 100),
     ("tbl_invertor_prms", "PRRTY", 1),
     ("tbl_invertor_prm1", "P_MAX_FEED_GRID", 2000),
     ("tbl_invertor_prms", "GRID_PV_OFF", 1),
     ("tbl_invertor_prms", "GRID_PV_ON", 1),
     ("tbl_invertor_prms", "TO_GRID", 1)],
)
def test_unreleased_setting_is_observable_without_command_discovery(table, key, value):
    """Unconfigured power controls and inverter PRRTY stay read-only."""
    client = make_client()
    broker = inject_mock_paho(client)

    assert client.send_discovery(device_id="BOX01", table=table, sensor_key=key, sensor_name=key)

    broker.publish.assert_called_once()
    topic, raw = broker.publish.call_args.args
    assert topic == f"homeassistant/sensor/oig_local_box01_{table}_{key.lower()}/config"
    assert "command_topic" not in json.loads(raw)
    assert is_setting_allowed(table, key) is False
    assert validate_setting_value(table, key, value)[0] is False


@pytest.mark.parametrize(
    ("table", "key", "ro_component", "cfg_component", "low", "high"),
    [
        ("tbl_invertor_prm1", "AAC_MAX_CHRG", "sensor", "number", 0, 125),
        ("tbl_invertor_prm1", "P_MAX_FEED_GRID", "sensor", "number", 1, 5400),
        ("tbl_invertor_prms", "GRID_PV_OFF", "binary_sensor", "switch", 0, 1),
        ("tbl_invertor_prms", "GRID_PV_ON", "binary_sensor", "switch", 0, 1),
        ("tbl_invertor_prms", "TO_GRID", "binary_sensor", "switch", 0, 1),
    ],
)
def test_opted_in_power_controls_keep_private_ids_and_confirmed_state(
    table, key, ro_component, cfg_component, low, high,
):
    limits = PowerControlLimits(ac_charge_max_a=125, grid_export_max_w=5400)
    metadata = load_released_sensor_map().lookup(table, key)
    assert metadata is not None
    client = make_client(power_limits=limits)
    broker = inject_mock_paho(client)

    assert client.send_discovery(
        device_id="BOX01", table=table, sensor_key=key, sensor_name=key,
        is_binary=metadata.get("is_binary", False),
        unit=metadata.get("unit_of_measurement", ""),
    )

    publications = {
        args[0]: json.loads(args[1]) for args, _kwargs in broker.publish.call_args_list
    }
    state_id = f"oig_local_box01_{table}_{key.lower()}"
    control_id = f"{state_id}_cfg"
    ro = publications[f"homeassistant/{ro_component}/{state_id}/config"]
    cfg = publications[f"homeassistant/{cfg_component}/{control_id}/config"]
    assert len(publications) == 2
    assert ro["unique_id"] == state_id
    assert "command_topic" not in ro
    assert cfg["unique_id"] == control_id
    assert ro["state_topic"] == cfg["state_topic"] == f"oig_local/BOX01/{table}/state"
    assert ro["value_template"] == cfg["value_template"]
    assert cfg["command_topic"] == f"oig_local/BOX01/set/{table}/{key}"
    assert cfg["retain"] is False and cfg["optimistic"] is False
    if cfg_component == "number":
        assert (cfg["min"], cfg["max"], cfg["step"]) == (low, high, 1)
        for value in (True, False, "on", "false"):
            assert not validate_setting_value(table, key, value, power_limits=limits)[0]
    else:
        assert (cfg["payload_off"], cfg["payload_on"]) == (0, 1)
    for value in (low, high):
        assert validate_setting_value(table, key, f"{value}.0", power_limits=limits) == (True, value, "")
    for value in (low - 1, high + 1, f"{high}.0000000000000001", "NaN"):
        assert not validate_setting_value(table, key, value, power_limits=limits)[0]


@pytest.mark.parametrize(("key", "allowed"), [
    ("P_CAL_R", [0, 50, 75, 90]), ("P_CAL_S", [90]), ("P_CAL_T", [90]),
])
def test_phase_cfg_discovery_keeps_number_ids_and_reports_allowed_values(key, allowed):
    client = make_client(power_limits=PowerControlLimits(phase_calibration_enabled=True))
    broker = inject_mock_paho(client)
    table = "tbl_invertor_prm1"
    assert client.send_discovery(device_id="BOX01", table=table, sensor_key=key, sensor_name=key)

    ro, cfg = [json.loads(call.args[1]) for call in broker.publish.call_args_list]
    base = f"oig_local_box01_{table}_{key.lower()}"
    assert ro["unique_id"] == base and cfg["unique_id"] == base + "_cfg"
    assert cfg["default_entity_id"] == f"number.{base}_cfg"
    assert cfg["state_topic"] == ro["state_topic"] == cfg["json_attributes_topic"]
    assert cfg["value_template"] == ro["value_template"]
    assert json.loads(cfg["json_attributes_template"]) == {"allowed_values": allowed}
    # A prior directly observed 75 must remain readable on all three cfg numbers.
    assert cfg["min"] <= 75 <= cfg["max"]
    assert cfg["command_topic"] == f"oig_local/BOX01/set/{table}/{key}"


def test_box_mode_discovery_retries_failed_select_without_republishing_sensor():
    """Retry the command entity after the matching state entity was accepted."""
    client = make_client()
    broker = inject_mock_paho(client)
    broker.publish.side_effect = [
        SimpleNamespace(rc=0), SimpleNamespace(rc=4), SimpleNamespace(rc=0),
    ]
    arguments = {
        "device_id": "BOX01", "table": "tbl_box_prms", "sensor_key": "MODE",
        "sensor_name": "Režim", "enum_map": {"0": "Home 1", "1": "Home 2"},
    }

    assert client.send_discovery(**arguments) is False
    assert client.send_discovery(**arguments) is True
    assert [args[0].split("/")[1] for args, _kwargs in broker.publish.call_args_list] == [
        "sensor", "select", "select",
    ]


def test_send_discovery_creates_select_for_proxy_mode_enum_setting():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="proxy_control",
        sensor_key="PROXY_MODE",
        sensor_name="Proxy - Režim",
        enum_map={"0": "online", "1": "hybrid", "2": "offline"},
    )

    assert mock_paho.publish.call_count == 1
    control_topic = mock_paho.publish.call_args_list[0][0][0]
    control_payload = json.loads(mock_paho.publish.call_args_list[0][0][1])
    assert control_topic == "homeassistant/select/oig_local_dev01_proxy_control_proxy_mode_cfg/config"
    assert control_payload["object_id"] == "oig_local_dev01_proxy_control_proxy_mode_cfg"
    assert control_payload["default_entity_id"] == "select.oig_local_dev01_proxy_control_proxy_mode_cfg"
    assert control_payload["options"] == ["online", "hybrid", "offline"]
    assert "value_json.get('PROXY_MODE')" in control_payload["value_template"]
    assert '"hybrid": "1"' in control_payload["command_template"]


def test_send_discovery_plain_sensor_value_template_is_missing_key_safe():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_actual",
        sensor_key="LoadedOn",
        sensor_name="Loaded On",
    )

    payload = json.loads(mock_paho.publish.call_args[0][1])
    assert payload["value_template"] == "{{ value_json.get('LoadedOn') }}"


def test_send_discovery_enum_sensor_value_template_is_missing_key_safe():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_actual",
        sensor_key="MODE",
        sensor_name="Mode",
        enum_map={"0": "Home 1", "1": "Home 2"},
    )

    payload = json.loads(mock_paho.publish.call_args[0][1])
    assert "value_json.get('MODE')" in payload["value_template"]


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
    assert payload["object_id"] == "oig_local_dev01_tbl_invertor_prm1_buz_mut_cfg"
    assert payload["default_entity_id"] == "switch.oig_local_dev01_tbl_invertor_prm1_buz_mut_cfg"
    assert payload["payload_on"] == 1
    assert payload["payload_off"] == 0
    assert payload["state_on"] == 1
    assert payload["state_off"] == 0


def test_send_discovery_creates_binary_sensor_default_entity_id():
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_batt_prms",
        sensor_key="BAT_DI",
        sensor_name="Battery DI",
        is_binary=True,
    )

    payload = json.loads(mock_paho.publish.call_args[0][1])
    assert payload["object_id"] == "oig_local_dev01_tbl_batt_prms_bat_di"
    assert payload["default_entity_id"] == "binary_sensor.oig_local_dev01_tbl_batt_prms_bat_di"


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

    # Reconnect callbacks must belong to the newly active client.
    mock_paho2 = inject_mock_paho(c)
    mock_paho2._oig_device_id = "DEV01"
    c._on_connect(mock_paho2, None, {"session present": 0}, 0)
    mock_paho2.publish.reset_mock()

    c.send_discovery(device_id="DEV01", table="t", sensor_key="k", sensor_name="K")
    mock_paho2.publish.assert_called_once()


def test_build_object_id_normalizes_non_alnum():
    assert MQTTClient._build_object_id("DEV-01", "tbl.box", "A+B") == "oig_local_dev_01_tbl_box_a_b"
    assert (
        MQTTClient._build_object_id("DEV-01", "tbl.box", "A+B", is_control=True)
        == "oig_local_dev_01_tbl_box_a_b_cfg"
    )


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


def test_reconnect_and_confirmed_replay_do_not_announce_box_availability():
    """Reconnect and historical state replay cannot establish current BOX liveness."""
    c = make_client(namespace="oig_local")
    mock_paho = MagicMock()
    mock_paho._oig_device_id = "DEV01"
    mock_paho.publish.return_value = MagicMock(rc=0)
    c._client = mock_paho

    c._on_connect(mock_paho, None, None, rc=0)

    assert c.publish_confirmed_state("DEV01", "tbl_box_prms", {"IP": ""})
    assert not any(args[0].endswith("/availability") for args, _ in mock_paho.publish.call_args_list)
    topic, payload = mock_paho.publish.call_args.args
    assert topic == "oig_local/DEV01/tbl_box_prms/state"
    assert json.loads(payload) == {"IP": ""}


def test_home_assistant_birth_reopens_discovery_without_issuing_control():
    """HA can rebuild discovery after birth without generating a setting request."""
    client = make_client()
    broker = inject_mock_paho(client)
    recovery = MagicMock()
    control = MagicMock()
    broker.subscribe.return_value = (0, 1)
    client._on_connect(broker, None, {"session present": 0}, 0)
    broker.publish.reset_mock()
    client.set_recovery_callback(recovery)
    assert client.subscribe("oig/+/control/set", control)
    discovery = {"device_id": "BOX01", "table": "tbl_actual", "sensor_key": "P", "sensor_name": "Power"}
    assert client.send_discovery(**discovery)
    generation = client.discovery_generation

    client._on_message(broker, None, SimpleNamespace(topic="homeassistant/status", payload=b"online", dup=True))

    recovery.assert_called_once_with()
    control.assert_not_called()
    assert client.discovery_generation == generation + 1
    assert client.send_discovery(**discovery)
    assert broker.publish.call_count == 2


@pytest.mark.parametrize(
    ("subscription", "topic"),
    [
        ("oig/+/control/set", "oig/BOX01/control/set"),
        ("oig_local/+/set/#", "oig_local/BOX01/set/tbl_box_prms/MODE"),
        ("oig_local/oig_proxy/set/proxy_control/PROXY_MODE", "oig_local/oig_proxy/set/proxy_control/PROXY_MODE"),
    ],
)
def test_retained_and_duplicate_control_messages_never_reach_callback(subscription, topic):
    """Only a fresh command can invoke the control callback on either supported route."""
    client = make_client()
    broker = inject_mock_paho(client)
    broker.subscribe.return_value = (0, 1)
    callback = MagicMock()
    client._on_connect(broker, None, {"session present": 0}, 0)
    assert client.subscribe(subscription, callback)
    broker.subscribe.assert_called_once_with(subscription, qos=0)
    client._on_subscribe(broker, None, 1, (0,))

    for retain, dup in [(True, False), (False, True), (True, True)]:
        client._on_message(broker, None, SimpleNamespace(topic=topic, payload=b"1", retain=retain, dup=dup, qos=0))
    callback.assert_not_called()
    client._on_message(broker, None, SimpleNamespace(topic=topic, payload=b"1", retain=False, dup=False, qos=0))
    callback.assert_called_once_with(topic, b"1")


def test_mqtt_client_requests_clean_session():
    """Paho uses MQTT 3.1.1 without a persistent command backlog."""
    client = make_client()
    broker = MagicMock()
    with patch("mqtt.client._paho_mqtt.Client", return_value=broker) as create:
        assert client._create_client("BOX01") is broker
    assert create.call_args.kwargs["clean_session"] is True
    assert broker.on_subscribe == client._on_subscribe


def test_control_dispatch_requires_clean_active_connection_and_matching_suback():
    """Old clients, resumed sessions and ungranted subscriptions cannot deliver a command."""
    client = make_client(qos=2)
    old_broker = inject_mock_paho(client)
    old_broker.subscribe.return_value = (0, 1)
    callback = MagicMock()
    status = MagicMock()
    topic_filter = "oig/+/control/set"
    message = SimpleNamespace(topic="oig/BOX01/control/set", payload=b"1", retain=False, dup=False, qos=0)
    client._on_connect(old_broker, None, {"session present": 0}, 0)
    assert client.subscribe(topic_filter, callback)
    old_broker.subscribe.assert_called_once_with(topic_filter, qos=0)
    client._on_message(old_broker, None, message)
    callback.assert_not_called()
    client._on_subscribe(old_broker, None, 1, (1,))
    client._on_message(old_broker, None, message)
    callback.assert_not_called()
    client._on_disconnect(old_broker, None, 0)

    active_broker = inject_mock_paho(client)
    active_broker.subscribe.return_value = (0, 2)
    assert client.subscribe("read/status", status)
    client._on_connect(active_broker, None, {"session present": 1}, 0)
    assert all(args[0] == "read/status" for args, _kwargs in active_broker.subscribe.call_args_list)
    assert all(kwargs == {"qos": 2} for _args, kwargs in active_broker.subscribe.call_args_list)
    client._on_message(active_broker, None, message)
    callback.assert_not_called()

    active_broker.subscribe.reset_mock()
    active_broker.subscribe.side_effect = [(0, 3), (0, 4)]
    client._on_connect(active_broker, None, {"session present": 0}, 0)
    assert [(args[0], kwargs["qos"]) for args, kwargs in active_broker.subscribe.call_args_list] == [
        (topic_filter, 0), ("read/status", 2),
    ]
    client._on_subscribe(old_broker, None, 3, (0,))
    client._on_subscribe(active_broker, None, 4, (0,))
    client._on_message(active_broker, None, message)
    callback.assert_not_called()

    client._on_subscribe(active_broker, None, 3, (0,))
    client._on_message(old_broker, None, message)
    client._on_message(active_broker, None, SimpleNamespace(**(vars(message) | {"qos": 1})))
    callback.assert_not_called()
    client._on_message(active_broker, None, message)
    callback.assert_called_once_with(message.topic, message.payload)


def test_on_connect_removes_legacy_unvalidated_number_discovery():
    """Reconnect removes retained number entities that are no longer writable."""
    c = make_client(namespace="oig_local")
    mock_paho = MagicMock()
    mock_paho._oig_device_id = "DEV01"
    mock_paho.publish.return_value = MagicMock(rc=0)
    c._client = mock_paho

    c._on_connect(mock_paho, None, None, rc=0)

    tombstones = {
        publish_call.args[0]
        for publish_call in mock_paho.publish.call_args_list
        if len(publish_call.args) >= 2 and publish_call.args[1] == ""
    }
    assert tombstones == {
        "homeassistant/number/oig_local_dev01_tbl_invertor_prm1_v_max_ac_cfg/config",
        "homeassistant/number/oig_local_dev01_tbl_invertor_prm1_v_min_ac_cfg/config",
        "homeassistant/number/oig_local_dev01_tbl_invertor_prm1_a_max_dis_hyb_cfg/config",
        "homeassistant/binary_sensor/oig_local_dev01_tbl_batt_prms_bat_di/config",
        "homeassistant/sensor/oig_local_dev01_proxy_control_proxy_mode/config",
    }
    for publish_call in mock_paho.publish.call_args_list:
        if publish_call.args[0] in tombstones:
            assert publish_call.kwargs == {"retain": True, "qos": 1}


def test_boiler_hdo_energy_number_range_accepts_live_value():
    """WD discovery range includes the 500 kWh value reported by the box."""
    c = make_client(namespace="oig_local")
    mock_paho = inject_mock_paho(c)

    c.send_discovery(
        device_id="DEV01",
        table="tbl_boiler_prms",
        sensor_key="WD",
        sensor_name="Bojler - Energie pro HDO",
        unit="Wh",
    )

    payload = json.loads(mock_paho.publish.call_args.args[1])
    assert payload["max"] >= 500_000


def test_on_connect_rc_nonzero_sets_connected_false():
    c = make_client()
    mock_paho = MagicMock()
    c._client = mock_paho

    c._on_connect(mock_paho, None, None, rc=5)

    assert c.connected is False


def test_on_disconnect_sets_connected_false():
    c = make_client()
    broker = inject_mock_paho(c, connected=True)

    c._on_disconnect(broker, None, rc=0)

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
