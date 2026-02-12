"""Tests for data validation and sensor config."""

import pytest

# pylint: disable=protected-access
import proxy as proxy_module
from models import ProxyMode
from tests.helpers import make_proxy


def test_validate_control_parameters_empty_value(tmp_path):
    """Test _validate_control_parameters with empty value."""
    proxy = make_proxy(tmp_path)

    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "")

    assert result["ok"] is True


def test_validate_control_parameters_whitespace_value(tmp_path):
    """Test _validate_control_parameters with whitespace value."""
    proxy = make_proxy(tmp_path)

    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "  ")

    assert result["ok"] is True


def test_validate_control_parameters_special_chars(tmp_path):
    """Test _validate_control_parameters with special characters."""
    proxy = make_proxy(tmp_path)

    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "test<>value")

    assert result["ok"] is True


def test_validate_control_parameters_different_table(tmp_path):
    """Test _validate_control_parameters with different table."""
    proxy = make_proxy(tmp_path)

    result = proxy._validate_control_parameters("tbl_box_prms2", "SA", "1")

    assert result["ok"] is True


def test_sensor_config_creation(tmp_path):
    """Test SensorConfig creation."""
    from utils import SensorConfig

    config = SensorConfig(
        sensor_type="electricity",
        port="/dev/ttyUSB0"
    )

    assert config.sensor_type == "electricity"
    assert config.port == "/dev/ttyUSB0"
