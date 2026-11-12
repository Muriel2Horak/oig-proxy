"""Released control domains used by MQTT admission and Home Assistant discovery."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any


# Only keys that ALSO have a SETTING_CONSTRAINTS entry may be written via control.
# Range-less keys (grid voltage/frequency limits, P_CAL_*, OFFSET, GRID_PV_*, ...)
# are intentionally excluded until validated bounds exist, so they cannot be
# written unvalidated over MQTT control.
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
    "tbl_invertor_prm1": {"BUZ_MUT", "GEN_AC_SRC"},
}


@dataclass(frozen=True)
class SettingConstraint:
    """Describe the released numeric bounds and discovery step."""

    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    integer_only: bool = False


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


def is_setting_allowed(table: str, key: str) -> bool:
    """Require both a released control identity and a validation domain."""
    return key in CONTROL_WRITE_WHITELIST.get(table, ()) and (table, key) in SETTING_CONSTRAINTS


def _parse_number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, str):
        value = value.strip()
        if not value or len(value) > 64:
            return None
        lowered = value.lower()
        if lowered in {"on", "true"}:
            return Decimal(1)
        if lowered in {"off", "false"}:
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


def validate_setting_value(table: str, key: str, value: Any) -> tuple[bool, float | int | None, str]:
    """Validate a released command without rounding before the range check."""
    if not is_setting_allowed(table, key):
        return False, None, "unknown or unconstrained control"
    parsed = _parse_number(value)
    if parsed is None:
        return False, None, "value is not numeric"
    constraint = SETTING_CONSTRAINTS[(table, key)]
    integral = parsed == parsed.to_integral_value()
    if constraint.integer_only and not integral:
        return False, None, "value must be integer"

    if constraint.min_value is not None and parsed < constraint.min_value:
        return False, None, f"value below min ({constraint.min_value})"
    if constraint.max_value is not None and parsed > constraint.max_value:
        return False, None, f"value above max ({constraint.max_value})"

    return True, int(parsed) if integral else float(parsed), ""
