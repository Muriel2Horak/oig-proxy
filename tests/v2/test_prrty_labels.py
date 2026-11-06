"""Regression coverage for the inverter solar-priority display labels."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from mqtt.client import MQTTClient
from sensor.loader import SensorMapLoader
from settings_constraints import is_setting_allowed


EXPECTED_LABELS = {
    "0": "Baterie, zátěž, síť",
    "1": "Zátěž, baterie, síť",
    "2": "Zátěž, síť, baterie",
}


def load_priority_metadata():
    """Read the shipped map through the production loader."""
    path = Path(__file__).resolve().parents[2] / "addon/oig-proxy/sensor_map.json"
    loader = SensorMapLoader(str(path))
    loader.load()
    return loader.lookup("tbl_invertor_prms", "PRRTY")


def test_inverter_priority_labels_and_help():
    """Keep raw codes stable and explain mode-specific priority resets."""
    metadata = load_priority_metadata()
    assert metadata["enum_map"] == EXPECTED_LABELS
    assert metadata["name_cs"] == "Střídač - Priorita toku"
    assert metadata["experimental"] is True
    assert "SEP" in metadata["description_cs"]
    assert "při každém přepnutí režimu" in metadata["description_cs"]
    assert "nejspíš vydrží jen do další změny režimu" in metadata["description_cs"]


def test_priority_discovery_preserves_identity_and_raw_state():
    """Labels change without authorizing writes or rewriting reported codes."""
    metadata = load_priority_metadata()
    client = MQTTClient(host="localhost", port=1883, namespace="oig_local")
    paho_client = MagicMock()
    paho_client.publish.return_value = MagicMock(rc=0)
    client._client = paho_client  # pylint: disable=protected-access
    client.connected = True

    assert client.send_discovery(
        device_id="TESTBOX", table="tbl_invertor_prms", sensor_key="PRRTY",
        sensor_name=metadata["name_cs"], enum_map=metadata["enum_map"],
    )
    topic, encoded = paho_client.publish.call_args.args
    discovery = json.loads(encoded)
    identity = "oig_local_testbox_tbl_invertor_prms_prrty"
    assert topic == f"homeassistant/sensor/{identity}/config"
    assert discovery["unique_id"] == identity
    assert discovery["default_entity_id"] == f"sensor.{identity}"
    assert discovery["state_topic"] == "oig_local/TESTBOX/tbl_invertor_prms/state"
    expected_map = json.dumps(EXPECTED_LABELS, ensure_ascii=False)
    assert discovery["value_template"] == (
        f"{{{{ ({expected_map}).get((value_json.get('PRRTY') | string), "
        "value_json.get('PRRTY')) }}"
    )
    assert "command_topic" not in discovery
    assert not is_setting_allowed("tbl_invertor_prms", "PRRTY")

    for raw in (0, 1, 2):
        assert client.publish_state("TESTBOX", "tbl_invertor_prms", {"PRRTY": raw})
        assert json.loads(paho_client.publish.call_args.args[1]) == {"PRRTY": raw}
