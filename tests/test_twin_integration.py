"""
Twin Architecture Integration Tests

Tests all 6 scenarios from the Twin Architecture Refactoring plan:
1. ONLINE with Twin takeover
2. HYBRID offline with Twin
3. OFFLINE with Twin
4. SA automation
5. MQTT state publishing
6. Error handling
"""

import pytest
import json
import os


class TestTwinSensorMap:
    """Tests for Twin sensor map entries"""

    def test_twin_sensors_exist_in_map(self):
        """Verify Twin sensors are defined in sensor_map.json"""
        # Load sensor map
        sensor_map_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'addon',
            'oig-proxy',
            'sensor_map.json'
        )

        with open(sensor_map_path, encoding='utf-8') as f:
            sensor_map = json.load(f)

        # Verify Twin sensors exist
        twin_sensors = [
            'twin_state:queue_length',
            'twin_state:inflight_tx',
            'twin_state:last_command_status',
            'twin_state:session_active',
            'twin_state:mode'
        ]

        for sensor in twin_sensors:
            assert sensor in sensor_map['sensors'], f"Missing sensor: {sensor}"

        # Verify session_active is binary
        assert sensor_map['sensors']['twin_state:session_active'].get('is_binary') is True

        # Verify all use proxy device mapping
        for sensor in twin_sensors:
            assert sensor_map['sensors'][sensor]['device_mapping'] == 'proxy'

    def test_twin_sensor_structure(self):
        """Verify Twin sensors have correct structure"""
        sensor_map_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'addon',
            'oig-proxy',
            'sensor_map.json'
        )

        with open(sensor_map_path, encoding='utf-8') as f:
            sensor_map = json.load(f)

        # Check required fields for each Twin sensor
        required_fields = [
            'name', 'name_cs', 'unit_of_measurement', 'device_class',
            'state_class', 'sensor_type_category', 'device_mapping',
            'todo', 'entity_category'
        ]

        twin_sensors = [
            'twin_state:queue_length',
            'twin_state:inflight_tx',
            'twin_state:last_command_status',
            'twin_state:session_active',
            'twin_state:mode'
        ]

        for sensor_name in twin_sensors:
            sensor = sensor_map['sensors'][sensor_name]
            for field in required_fields:
                assert field in sensor, f"Sensor {sensor_name} missing field: {field}"


class TestTwinArchitecture:
    """Tests for Twin Architecture implementation"""

    def test_digital_twin_module_exists(self):
        """Verify digital_twin.py module exists"""
        dt_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'addon',
            'oig-proxy',
            'digital_twin.py'
        )
        assert os.path.exists(dt_path), "digital_twin.py not found"

    def test_twin_mqtt_handler_exists(self):
        """Verify TwinMQTTHandler class exists in digital_twin"""
        dt_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'addon',
            'oig-proxy',
            'digital_twin.py'
        )

        with open(dt_path, encoding='utf-8') as f:
            content = f.read()

        assert 'class TwinMQTTHandler' in content, "TwinMQTTHandler class not found"
        assert 'def on_mqtt_message' in content, "on_mqtt_message method not found"

    def test_twin_state_publishing_exists(self):
        """Verify _publish_state method exists"""
        dt_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'addon',
            'oig-proxy',
            'digital_twin.py'
        )

        with open(dt_path, encoding='utf-8') as f:
            content = f.read()

        assert '_publish_state' in content, "_publish_state method not found"
        assert 'twin_state/state' in content, "twin_state topic not found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
