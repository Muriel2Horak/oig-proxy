"""Tests for data validation and sensor config."""

# pylint: disable=protected-access
from tests.helpers import make_proxy
from models import SensorConfig


def test_validate_control_parameters_empty_value(tmp_path):
    """Test _validate_control_parameters with empty value."""
    proxy = make_proxy(tmp_path)

    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "")

    assert result["ok"] is True


def test_validate_control_parameters_whitespace_value(tmp_path):
    """Test _validate_control_parameters with whitespace value."""
    proxy = make_proxy(tmp_path)

    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "  ")

    assert result["ok"] is True


def test_validate_control_parameters_special_chars(tmp_path):
    """Test _validate_control_parameters with special characters."""
    proxy = make_proxy(tmp_path)

    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "test<>value")

    assert result["ok"] is True


def test_validate_control_parameters_different_table(tmp_path):
    """Test _validate_control_parameters with different table."""
    proxy = make_proxy(tmp_path)

    result = proxy._cs.validate_parameters("tbl_box_prms2", "SA", "1")

    assert result["ok"] is True


def test_sensor_config_creation(tmp_path):
    """Test SensorConfig creation."""
    config = SensorConfig(name="Grid", unit="kWh")

    assert config.name == "Grid"
    assert config.unit == "kWh"
