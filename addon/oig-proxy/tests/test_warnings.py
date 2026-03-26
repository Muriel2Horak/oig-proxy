"""Testy pro sensor/warnings.py"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sensor.warnings import decode_warnings  # noqa: E402


def test_decode_warnings_single_bit():
    """Test: decode_warnings(256, [{"bit": 8, "key": "ERR_PV"}]) → ["ERR_PV"]"""
    warnings_list = [{"bit": 8, "key": "ERR_PV"}]
    result = decode_warnings(256, warnings_list)
    assert result == ["ERR_PV"]


def test_decode_warnings_zero_value():
    """Test: decode_warnings(0, [...]) → []"""
    warnings_list = [{"bit": 8, "key": "ERR_PV"}, {"bit": 4, "key": "ERR_BATT"}]
    result = decode_warnings(0, warnings_list)
    assert result == []


def test_decode_warnings_multiple_bits():
    """Test multiple bits set."""
    warnings_list = [
        {"bit": 8, "key": "ERR_PV"},
        {"bit": 4, "key": "ERR_BATT"},
        {"bit": 2, "key": "ERR_GRID"},
    ]
    # 256 (bit 8) + 16 (bit 4) = 272
    result = decode_warnings(272, warnings_list)
    assert set(result) == {"ERR_PV", "ERR_BATT"}


def test_decode_warnings_empty_list():
    """Test empty warnings list."""
    result = decode_warnings(256, [])
    assert result == []


def test_decode_warnings_missing_keys():
    """Test warnings with missing bit or key."""
    warnings_list = [
        {"bit": 8, "key": "ERR_PV"},
        {"key": "ERR_BATT"},  # missing bit
        {"bit": 2},  # missing key
    ]
    result = decode_warnings(256, warnings_list)
    assert result == ["ERR_PV"]


def test_decode_warnings_real_scenario():
    """Test with realistic warnings_3f data from sensor_map.json."""
    warnings_list = [
        {"bit": 8, "key": "ERR_PV", "remark": "Solar input 1 loss"},
        {"bit": 4, "key": "ERR_PV", "remark": "Solar input 2 loss"},
        {"bit": 16, "key": "ERR_BATT", "remark": "Battery under"},
        {"bit": 8, "key": "ERR_BATT", "remark": "Battery Low"},
    ]
    # Bit 8 set (256) - both ERR_PV and ERR_BATT have bit 8
    result = decode_warnings(256, warnings_list)
    assert set(result) == {"ERR_PV", "ERR_BATT"}

    # Bits 8 and 16 set (256 + 16 = 272) - ERR_PV and ERR_BATT
    result = decode_warnings(272, warnings_list)
    assert set(result) == {"ERR_PV", "ERR_BATT"}


def test_decode_warnings_bit_order():
    """Test that bit order doesn't matter - lower bits first."""
    warnings_list = [
        {"bit": 0, "key": "ERR_A"},
        {"bit": 1, "key": "ERR_B"},
        {"bit": 2, "key": "ERR_C"},
    ]
    # Only bit 0 set
    result = decode_warnings(1, warnings_list)
    assert result == ["ERR_A"]

    # Bits 0 and 2 set
    result = decode_warnings(5, warnings_list)
    assert set(result) == {"ERR_A", "ERR_C"}