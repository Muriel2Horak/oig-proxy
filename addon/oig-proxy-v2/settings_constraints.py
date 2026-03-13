from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CONTROL_WRITE_WHITELIST: dict[str, set[str]] = {
    "tbl_batt_prms": {"FMT_ON", "BAT_MIN", "BAT_GL_MIN", "BAT_AG_MIN"},
    "tbl_boiler_prms": {"ISON", "MANUAL", "SSR0", "SSR1", "SSR2", "OFFSET"},
    "tbl_box_prms": {"MODE", "BAT_AC", "BAT_FORMAT", "SA", "RQRESET"},
    "tbl_invertor_prms": {"GRID_PV_ON", "GRID_PV_OFF", "TO_GRID", "PRLL_OUT", "P_ADJ_STRT"},
    "tbl_invertor_prm1": {
        "AAC_MAX_CHRG",
        "A_MAX_CHRG",
        "V_MIN_AC",
        "V_MAX_AC",
        "F_MIN_AC",
        "F_MAX_AC",
        "V_CHRG",
        "V_CHAR_FLO",
        "V_CUT_GRID",
        "V_RE_GRID",
        "A_MAX_DIS_HYB",
        "P_CAL_R",
        "P_CAL_S",
        "P_CAL_T",
        "BUZ_MUT",
        "GEN_AC_SRC",
    },
}


@dataclass(frozen=True)
class SettingConstraint:
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    integer_only: bool = False
    allowed_values: tuple[float, ...] | None = None


SETTING_CONSTRAINTS: dict[tuple[str, str], SettingConstraint] = {
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
    ("tbl_invertor_prm1", "BUZ_MUT"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
    ("tbl_invertor_prm1", "GEN_AC_SRC"): SettingConstraint(min_value=0, max_value=1, step=1, integer_only=True),
}


def is_setting_allowed(table: str, key: str) -> bool:
    return key in CONTROL_WRITE_WHITELIST.get(table, set())


def parse_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def validate_setting_value(table: str, key: str, value: Any) -> tuple[bool, float | int | None, str]:
    parsed = parse_numeric(value)
    if parsed is None:
        return False, None, "value is not numeric"

    c = SETTING_CONSTRAINTS.get((table, key))
    if c is None:
        if float(parsed).is_integer():
            return True, int(parsed), ""
        return True, parsed, ""

    if c.integer_only and not float(parsed).is_integer():
        return False, None, "value must be integer"

    if c.min_value is not None and parsed < c.min_value:
        return False, None, f"value below min ({c.min_value})"
    if c.max_value is not None and parsed > c.max_value:
        return False, None, f"value above max ({c.max_value})"

    if c.allowed_values is not None and parsed not in c.allowed_values:
        return False, None, "value not in allowed set"

    out: float | int = int(parsed) if c.integer_only or float(parsed).is_integer() else parsed
    return True, out, ""
