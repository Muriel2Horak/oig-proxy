"""Validate the installation opt-in and exact phase calibration domains."""

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
    ("key", "allowed", "display_range"),
    [
        ("P_CAL_R", {0, 50, 75, 90}, (0, 90, 5)),
        ("P_CAL_S", {90}, (90, 90, 1)),
        ("P_CAL_T", {90}, (90, 90, 1)),
    ],
)
def test_phase_calibration_reports_and_accepts_only_qualified_values(
    key: str, allowed: set[int], display_range: tuple[int, int, int],
) -> None:
    """Discovery receives the exact set in addition to its numeric envelope."""
    limits = PowerControlLimits(phase_calibration_enabled=True)
    constraint = get_setting_constraint(TABLE, key, power_limits=limits)
    assert constraint is not None and constraint.integer_only
    assert constraint.allowed_values == allowed
    assert (constraint.min_value, constraint.max_value, constraint.step) == display_range
    for value in allowed:
        assert validate_setting_value(TABLE, key, value, power_limits=limits) == (True, value, "")
        assert validate_setting_value(TABLE, key, f"{value}.0", power_limits=limits) == (True, value, "")


@pytest.mark.parametrize(
    ("table", "key", "value"),
    [
        (TABLE, "P_CAL_R", -1),
        (TABLE, "P_CAL_R", 5),
        (TABLE, "P_CAL_R", 55),
        (TABLE, "P_CAL_R", 89),
        (TABLE, "P_CAL_R", 91),
        (TABLE, "P_CAL_R", "90.000000000000001"),
        (TABLE, "P_CAL_R", False),
        (TABLE, "P_CAL_R", "off"),
        (TABLE, "P_CAL_R", float("nan")),
        (TABLE, "P_CAL_R", "Infinity"),
        (TABLE, "P_CAL_S", 75),
        (TABLE, "P_CAL_T", 75),
        (TABLE, "P_CAL_T", 65535),
        ("tbl_invertor_prms", "P_CAL_R", 90),
        (TABLE, "P_CAL_X", 90),
    ],
)
def test_phase_calibration_rejects_unqualified_commands(table: str, key: str, value: object) -> None:
    """A display step, device encoding or observed baseline cannot grant permission."""
    limits = PowerControlLimits(phase_calibration_enabled=True)
    assert validate_setting_value(table, key, value, power_limits=limits)[0] is False
