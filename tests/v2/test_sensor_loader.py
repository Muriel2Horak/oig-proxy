"""Tests for sensor loader."""
# pylint: disable=missing-function-docstring

# pyright: reportMissingImports=false

import json
import os
import tempfile

import pytest

from sensor.loader import SensorMapLoader


class TestSensorMapLoader:
    """Test SensorMapLoader class."""

    def test_load_existing_file(self):
        """Test loading an existing sensor map file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            test_data = {
                "sensors": {
                    "tbl_actual:Temp": {
                        "name": "Box Temperature",
                        "unit_of_measurement": "°C",
                    }
                }
            }
            json.dump(test_data, f)
            temp_path = f.name

        try:
            loader = SensorMapLoader(temp_path)
            loader.load()
            assert loader.sensor_count() == 1
        finally:
            os.unlink(temp_path)

    def test_lookup_found(self):
        """Test successful sensor lookup."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            test_data = {
                "sensors": {
                    "tbl_actual:Temp": {
                        "name": "Box Temperature",
                        "unit_of_measurement": "°C",
                        "device_class": "temperature",
                    }
                }
            }
            json.dump(test_data, f)
            temp_path = f.name

        try:
            loader = SensorMapLoader(temp_path)
            loader.load()
            result = loader.lookup("tbl_actual", "Temp")
            assert result is not None
            assert result["name"] == "Box Temperature"
            assert result["unit_of_measurement"] == "°C"
        finally:
            os.unlink(temp_path)

    def test_lookup_not_found(self):
        """Test lookup for non-existent sensor."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            test_data = {"sensors": {}}
            json.dump(test_data, f)
            temp_path = f.name

        try:
            loader = SensorMapLoader(temp_path)
            loader.load()
            result = loader.lookup("tbl_actual", "NonExistent")
            assert result is None
        finally:
            os.unlink(temp_path)

    def test_missing_file(self):
        """Test loading non-existent file doesn't crash."""
        loader = SensorMapLoader("/nonexistent/path/sensor_map.json")
        loader.load()
        assert loader.sensor_count() == 0
        assert loader.lookup("tbl_actual", "Temp") is None

    def test_get_warnings(self):
        """Test getting warnings_3f for a sensor."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            test_data = {
                "sensors": {
                    "tbl_actual:Err": {
                        "name": "Error",
                        "warnings_3f": [
                            {"bit": 1, "remark": "Warning 1"},
                            {"bit": 2, "remark": "Warning 2"},
                        ],
                    }
                }
            }
            json.dump(test_data, f)
            temp_path = f.name

        try:
            loader = SensorMapLoader(temp_path)
            loader.load()
            warnings = loader.get_warnings("tbl_actual", "Err")
            assert len(warnings) == 2
            assert warnings[0]["bit"] == 1
        finally:
            os.unlink(temp_path)

    def test_get_warnings_not_found(self):
        """Test get_warnings for non-existent sensor."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            test_data = {"sensors": {}}
            json.dump(test_data, f)
            temp_path = f.name

        try:
            loader = SensorMapLoader(temp_path)
            loader.load()
            warnings = loader.get_warnings("tbl_actual", "NonExistent")
            assert warnings == []
        finally:
            os.unlink(temp_path)

    def test_sensor_count_empty(self):
        """Test sensor count with empty map."""
        loader = SensorMapLoader("/nonexistent/path.json")
        loader.load()
        assert loader.sensor_count() == 0

    def test_sensor_count_with_data(self):
        """Test sensor count with data."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            test_data = {
                "sensors": {
                    "tbl_actual:Temp": {"name": "Temp"},
                    "tbl_actual:Humid": {"name": "Humid"},
                    "tbl_actual:Press": {"name": "Press"},
                }
            }
            json.dump(test_data, f)
            temp_path = f.name

        try:
            loader = SensorMapLoader(temp_path)
            loader.load()
            assert loader.sensor_count() == 3
        finally:
            os.unlink(temp_path)

    def test_iter_sensors_returns_table_key_metadata(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            test_data = {
                "sensors": {
                    "tbl_actual:Temp": {"name": "Teplota"},
                    "tbl_batt:BAT_V": {"name": "Napeti"},
                }
            }
            json.dump(test_data, f)
            temp_path = f.name

        try:
            loader = SensorMapLoader(temp_path)
            loader.load()
            items = loader.iter_sensors()
            assert ("tbl_actual", "Temp", {"name": "Teplota"}) in items
            assert ("tbl_batt", "BAT_V", {"name": "Napeti"}) in items
        finally:
            os.unlink(temp_path)
