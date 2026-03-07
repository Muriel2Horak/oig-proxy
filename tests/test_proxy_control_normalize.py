"""Tests for value coercion and normalization in ControlPipeline."""

import importlib

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


def test_coerce_bool_and_numeric_strings():
    """Test coerce_value converts bool and numeric strings to proper types."""
    assert ControlPipeline.coerce_value("true") is True
    assert ControlPipeline.coerce_value("false") is False
    assert ControlPipeline.coerce_value("2") == 2
    assert ControlPipeline.coerce_value("2.5") == 2.5


def test_coerce_value_keeps_non_numeric_text():
    """Test coerce_value preserves non-numeric text strings."""
    assert ControlPipeline.coerce_value("MODE") == "MODE"
