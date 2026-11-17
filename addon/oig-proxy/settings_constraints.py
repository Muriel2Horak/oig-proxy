"""Released control domains used by MQTT admission and Home Assistant discovery."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any


# Only keys that ALSO have a SETTING_CONSTRAINTS entry may be written via control.
# Range-less keys such as grid voltage/frequency limits and OFFSET are excluded.
# Installation-specific controls also require explicit settings below.
CONTROL_WRITE_WHITELIST: dict[str, set[str]] = {
    "proxy_control": {"PROXY_MODE"},
    "tbl_batt_prms": {"FMT_ON", "BAT_MIN", "BAT_GL_MIN", "BAT_AG_MIN"},
    "tbl_boiler_prms": {
        "ISON",
        "MANUAL",
        "SSR0",
        "SSR1",
        "SSR2",
        "PRRTY",
        "P_SET",
        "WD",
        "ZONE1_S",
        "ZONE1_E",
        "ZONE2_S",
        "ZONE2_E",
        "ZONE3_S",
        "ZONE3_E",
        "ZONE4_S",
        "ZONE4_E",
    },
    "tbl_box_prms": {"MODE", "BAT_AC", "BAT_FORMAT", "SA", "RQRESET"},
    "tbl_invertor_prm1": {
        "BUZ_MUT", "GEN_AC_SRC", "AAC_MAX_CHRG", "P_MAX_FEED_GRID", "P_CAL_R", "P_CAL_S", "P_CAL_T",
    },
    "tbl_invertor_prms": {"GRID_PV_OFF", "GRID_PV_ON", "TO_GRID"},
}

AC_CHARGE_CONTROL = ("tbl_invertor_prm1", "AAC_MAX_CHRG")
EXPORT_LIMIT_CONTROL = ("tbl_invertor_prm1", "P_MAX_FEED_GRID")
GRID_SWITCH_CONTROLS = frozenset({
    ("tbl_invertor_prms", "GRID_PV_OFF"),
    ("tbl_invertor_prms", "GRID_PV_ON"),
    ("tbl_invertor_prms", "TO_GRID"),
})


@dataclass(frozen=True)
class PowerControlLimits:
    """Installation limits and explicit opt-ins for optional BOX controls."""

    ac_charge_max_a: int = 0
    grid_export_max_w: int = 0
    phase_calibration_enabled: bool = False


@dataclass(frozen=True)
class SettingConstraint:
    """Describe the numeric envelope for a write."""

    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    integer_only: bool = False


PHASE_CALIBRATION_CONTROLS = frozenset(
    ("tbl_invertor_prm1", key) for key in ("P_CAL_R", "P_CAL_S", "P_CAL_T")
)
PHASE_CALIBRATION_WRITE_TABLE = "phase_calibration_write"
# The vendor workbook specifies U16 for all three calibration parameters.
_PHASE_CALIBRATION_U16 = SettingConstraint(0, 0xFFFF, 1, integer_only=True)
_NUMERIC_POWER_CONTROLS = frozenset({AC_CHARGE_CONTROL, EXPORT_LIMIT_CONTROL}) | PHASE_CALIBRATION_CONTROLS
POWER_CONTROLS = GRID_SWITCH_CONTROLS | _NUMERIC_POWER_CONTROLS


SETTING_CONSTRAINTS: dict[tuple[str, str], SettingConstraint] = {
    ("proxy_control", "PROXY_MODE"): SettingConstraint(min_value=0, max_value=2, step=1, integer_only=True),
    ("tbl_batt_prms", "FMT_ON"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_batt_prms", "BAT_MIN"): SettingConstraint(min_value=20, max_value=100, step=1, integer_only=True),
    ("tbl_batt_prms", "BAT_GL_MIN"): SettingConstraint(min_value=0, max_value=100, step=1, integer_only=True),
    ("tbl_batt_prms", "BAT_AG_MIN"): SettingConstraint(min_value=0, max_value=100, step=1, integer_only=True),
    ("tbl_box_prms", "MODE"): SettingConstraint(min_value=0, max_value=5, step=1, integer_only=True),
    ("tbl_box_prms", "BAT_AC"): SettingConstraint(min_value=0, max_value=100, step=1, integer_only=True),
    ("tbl_box_prms", "BAT_FORMAT"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_box_prms", "SA"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_box_prms", "RQRESET"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_boiler_prms", "ISON"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_boiler_prms", "MANUAL"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_boiler_prms", "SSR0"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_boiler_prms", "SSR1"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_boiler_prms", "SSR2"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_boiler_prms", "PRRTY"): SettingConstraint(min_value=0, max_value=2, step=1, integer_only=True),
    ("tbl_boiler_prms", "P_SET"): SettingConstraint(min_value=0, max_value=10000, step=100, integer_only=True),
    ("tbl_boiler_prms", "WD"): SettingConstraint(min_value=0, max_value=500000, step=100, integer_only=True),
    ("tbl_boiler_prms", "ZONE1_S"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_boiler_prms", "ZONE1_E"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_boiler_prms", "ZONE2_S"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_boiler_prms", "ZONE2_E"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_boiler_prms", "ZONE3_S"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_boiler_prms", "ZONE3_E"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_boiler_prms", "ZONE4_S"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_boiler_prms", "ZONE4_E"): SettingConstraint(min_value=0, max_value=86399, step=60, integer_only=True),
    ("tbl_invertor_prm1", "BUZ_MUT"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_invertor_prm1", "GEN_AC_SRC"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
}


def get_setting_constraint(
    table: str, key: str, *, power_limits: PowerControlLimits = PowerControlLimits(),
) -> SettingConstraint | None:
    """Resolve one allowed control and its configured validation domain."""
    if key not in CONTROL_WRITE_WHITELIST.get(table, ()):
        return None
    identity = (table, key)
    if identity in PHASE_CALIBRATION_CONTROLS:
        return _PHASE_CALIBRATION_U16 if power_limits.phase_calibration_enabled is True else None
    if identity == AC_CHARGE_CONTROL and 0 < power_limits.ac_charge_max_a <= 125:
        return SettingConstraint(0, power_limits.ac_charge_max_a, 1, integer_only=True)
    if 0 < power_limits.grid_export_max_w <= 0xFFFFFFFF:
        if identity == EXPORT_LIMIT_CONTROL:
            return SettingConstraint(1, power_limits.grid_export_max_w, 1, integer_only=True)
        if identity in GRID_SWITCH_CONTROLS:
            return SettingConstraint(0, 1, 1, integer_only=True)
    return SETTING_CONSTRAINTS.get(identity)


def is_setting_allowed(
    table: str, key: str, *, power_limits: PowerControlLimits = PowerControlLimits(),
) -> bool:
    """Require both a released control identity and a validation domain."""
    return get_setting_constraint(table, key, power_limits=power_limits) is not None


def _parse_number(value: Any, *, allow_boolean: bool = True) -> Decimal | None:
    if isinstance(value, bool):
        return Decimal(int(value)) if allow_boolean else None
    if isinstance(value, str):
        value = value.strip()
        if not value or len(value) > 64:
            return None
        lowered = value.lower()
        if allow_boolean and lowered in {"on", "true"}:
            return Decimal(1)
        if allow_boolean and lowered in {"off", "false"}:
            return Decimal(0)
    elif not isinstance(value, (int, float, Decimal)):
        return None

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def parse_numeric(value: Any) -> float | None:
    """Normalize legacy scalar inputs to a finite float."""
    parsed = _parse_number(value)
    if parsed is not None:
        result = float(parsed)
        if math.isfinite(result):
            return result
    return None


def validate_setting_value(
    table: str, key: str, value: Any, *, power_limits: PowerControlLimits = PowerControlLimits(),
) -> tuple[bool, float | int | None, str]:
    """Validate a released command without rounding before the range check."""
    constraint = get_setting_constraint(table, key, power_limits=power_limits)
    if constraint is None:
        return False, None, "unknown or unconstrained control"
    parsed = _parse_number(value, allow_boolean=(table, key) not in _NUMERIC_POWER_CONTROLS)
    if parsed is None:
        return False, None, "value is not numeric"
    integral = parsed == parsed.to_integral_value()
    if constraint.integer_only and not integral:
        return False, None, "value must be integer"

    if constraint.min_value is not None and parsed < constraint.min_value:
        return False, None, f"value below min ({constraint.min_value})"
    if constraint.max_value is not None and parsed > constraint.max_value:
        return False, None, f"value above max ({constraint.max_value})"
    return True, int(parsed) if integral else float(parsed), ""
