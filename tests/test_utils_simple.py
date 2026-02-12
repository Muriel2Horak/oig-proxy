"""Tests for utility functions in utils.py."""

import pytest
import utils
from tests.helpers import make_proxy


def test_sensor_config_defaults():
    """Test SensorConfig default values."""
    config = utils.SensorConfig(
        sensor_type="electricity"
    )

    assert config.sensor_type == "electricity"
    assert config.port is None


def test_sensor_config_with_port():
    """Test SensorConfig with port."""
    config = utils.SensorConfig(
        sensor_type="electricity",
        port="/dev/ttyUSB0"
    )

    assert config.port == "/dev/ttyUSB0"


def test_sensor_config_with_baudrate():
    """Test SensorConfig with baudrate."""
    config = utils.SensorConfig(
        sensor_type="electricity",
        baudrate=9600
    )

    assert config.baudrate == 9600
