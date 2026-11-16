"""Validate the installation opt-in and phase calibration wire encoding."""

import pytest

from settings_constraints import PowerControlLimits, get_setting_constraint, validate_setting_value


TABLE = "tbl_invertor_prm1"


@pytest.mark.parametrize("key", ["P_CAL_R", "P_CAL_S", "P_CAL_T"])
def test_phase_calibration_is_read_only_without_explicit_opt_in(key: str) -> None:
    """An unrelated charge/export limit cannot enable calibration writes."""
    limits = PowerControlLimits(ac_charge_max_a=125, grid_export_max_w=5400)
    assert get_setting_constraint(TABLE, key, power_limits=limits) is None
    assert validate_setting_value(TABLE, key, 90, power_limits=limits)[0] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("P_CAL_R", 91),
        ("P_CAL_R", 99),
        ("P_CAL_S", 75),
        ("P_CAL_T", 75),
        ("P_CAL_R", 0),
        ("P_CAL_T", 65535),
    ],
)
def test_phase_calibration_accepts_values_representable_as_u16(key: str, value: int) -> None:
    """The proxy accepts a raw unsigned 16-bit value for an enabled phase."""
    limits = PowerControlLimits(phase_calibration_enabled=True)
    assert validate_setting_value(TABLE, key, value, power_limits=limits) == (True, value, "")
    assert validate_setting_value(TABLE, key, f"{value}.0", power_limits=limits) == (True, value, "")


@pytest.mark.parametrize(
    ("table", "key", "value"),
    [
        (TABLE, "P_CAL_R", -1),
        (TABLE, "P_CAL_T", 65536),
        (TABLE, "P_CAL_R", "90.000000000000001"),
        (TABLE, "P_CAL_R", False),
        (TABLE, "P_CAL_R", "off"),
        (TABLE, "P_CAL_R", float("nan")),
        (TABLE, "P_CAL_R", "Infinity"),
        ("tbl_invertor_prms", "P_CAL_R", 90),
        (TABLE, "P_CAL_X", 90),
    ],
)
def test_phase_calibration_rejects_malformed_commands(table: str, key: str, value: object) -> None:
    """Reject unknown controls and values that cannot be represented as U16."""
    limits = PowerControlLimits(phase_calibration_enabled=True)
    assert validate_setting_value(table, key, value, power_limits=limits)[0] is False
