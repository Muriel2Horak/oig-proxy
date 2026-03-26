"""Tests for sensor map state class validation."""

# pyright: reportMissingImports=false

import json

from pathlib import Path
import pytest


class TestSensorMapStateClass:
    """Test sensor map state_class values."""

    def test_batt_prms_state_class(self):
        """Test that battery parameter sensors have proper state_class."""
        sensor_map_path = Path(__file__).parent.parent.parent / "addon" / "oig-proxy" / "sensor_map.json"

        with open(sensor_map_path) as f:
            sensor_map = json.load(f)

        sensors = sensor_map.get("sensors", {})

        # Check specific sensors have state_class == "measurement"
        assert sensors["tbl_batt_prms:BAT_CU"]["state_class"] == "measurement"
        assert sensors["tbl_batt_prms:BAT_CI"]["state_class"] == "measurement"

        assert sensors["tbl_batt_prms:BAT_AG_MIN"]["state_class"] == "measurement"
        assert sensors["tbl_batt_prms:BAT_GL_MIN"]["state_class"] == "measurement"
        assert sensors["tbl_batt_prms:BAT_MIN"]["state_class"] == "measurement"

    def test_batt_prms_bat_di_is_not_binary(self):
        sensor_map_path = Path(__file__).parent.parent.parent / "addon" / "oig-proxy" / "sensor_map.json"

        with open(sensor_map_path) as f:
            sensor_map = json.load(f)

        sensors = sensor_map.get("sensors", {})
        bat_di = sensors["tbl_batt_prms:BAT_DI"]

        assert bat_di.get("is_binary") is False

    def test_measurable_non_binary_sensors_have_state_class(self):
        """Test that measurable non-binary sensors have a non-null state_class."""
        sensor_map_path = Path(__file__).parent.parent.parent / "addon" / "oig-proxy" / "sensor_map.json"

        with open(sensor_map_path) as f:
            sensor_map = json.load(f)

        sensors = sensor_map.get("sensors", {})

        # Check that measurable, non-binary sensors with units have state_class
        for sensor_key, sensor_data in sensors.items():
            if (
                sensor_data.get("sensor_type_category") == "measured"
                and not sensor_data.get("is_binary")
            ):
                if sensor_data.get("unit_of_measurement"):
                    assert sensor_data.get("state_class") is not None, (
                        f"Sensor {sensor_key} has unit_of_measurement "
                        f"but no state_class"
                    )
