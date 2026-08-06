"""Exact authorization constraints and canonical values for local Settings."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any, Iterator


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


@dataclass(frozen=True, slots=True)
class SettingConstraint:
    """Exact numeric authorization contract for one Setting target."""

    min_value: Decimal | None = None
    max_value: Decimal | None = None
    step: Decimal | None = None
    integer_only: bool = False
    boolean_aliases: bool = False


@dataclass(frozen=True, slots=True)
class SettingValueResult:
    """Canonical validation result consumed by local-setting transactions."""

    accepted: bool
    value_text: str | None
    reason: str

    def __iter__(self) -> Iterator[bool | Decimal | int | None | str]:
        """Keep legacy tuple-unpacking callers working during the API cutover."""
        normalized: Decimal | int | None = None
        if self.value_text is not None:
            parsed = Decimal(self.value_text)
            normalized = int(parsed) if parsed == parsed.to_integral_value() else parsed
        yield self.accepted
        yield normalized
        yield self.reason


def _constraint(
    minimum: str,
    maximum: str,
    step: str,
    *,
    boolean_aliases: bool = False,
) -> SettingConstraint:
    return SettingConstraint(
        Decimal(minimum),
        Decimal(maximum),
        Decimal(step),
        True,
        boolean_aliases,
    )


SETTING_CONSTRAINTS: dict[tuple[str, str], SettingConstraint] = {
    ("proxy_control", "PROXY_MODE"): _constraint("0", "2", "1"),
    ("tbl_batt_prms", "FMT_ON"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_batt_prms", "BAT_MIN"): _constraint("20", "100", "1"),
    ("tbl_batt_prms", "BAT_GL_MIN"): _constraint("0", "100", "1"),
    ("tbl_batt_prms", "BAT_AG_MIN"): _constraint("0", "100", "1"),
    ("tbl_box_prms", "MODE"): _constraint("0", "5", "1"),
    ("tbl_box_prms", "BAT_AC"): _constraint("0", "100", "1"),
    ("tbl_box_prms", "BAT_FORMAT"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_box_prms", "SA"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_box_prms", "RQRESET"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_boiler_prms", "ISON"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_boiler_prms", "MANUAL"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_boiler_prms", "SSR0"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_boiler_prms", "SSR1"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_boiler_prms", "SSR2"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_boiler_prms", "PRRTY"): _constraint("0", "2", "1"),
    ("tbl_boiler_prms", "P_SET"): _constraint("0", "10000", "100"),
    ("tbl_boiler_prms", "WD"): _constraint("0", "100000", "100"),
    ("tbl_boiler_prms", "ZONE1_S"): _constraint("0", "86399", "60"),
    ("tbl_boiler_prms", "ZONE1_E"): _constraint("0", "86399", "60"),
    ("tbl_boiler_prms", "ZONE2_S"): _constraint("0", "86399", "60"),
    ("tbl_boiler_prms", "ZONE2_E"): _constraint("0", "86399", "60"),
    ("tbl_boiler_prms", "ZONE3_S"): _constraint("0", "86399", "60"),
    ("tbl_boiler_prms", "ZONE3_E"): _constraint("0", "86399", "60"),
    ("tbl_boiler_prms", "ZONE4_S"): _constraint("0", "86399", "60"),
    ("tbl_boiler_prms", "ZONE4_E"): _constraint("0", "86399", "60"),
    ("tbl_invertor_prm1", "BUZ_MUT"): _constraint("0", "1", "1", boolean_aliases=True),
    ("tbl_invertor_prm1", "GEN_AC_SRC"): _constraint("0", "1", "1", boolean_aliases=True),
}


def is_setting_allowed(table: str, key: str) -> bool:
    """Return whether table/key is present in the control allowlist."""
    return key in CONTROL_WRITE_WHITELIST.get(table, set())


def canonical_decimal_text(value: Decimal) -> str:
    """Render finite Decimal as fixed canonical text without negative zero."""
    if not value.is_finite():
        raise ValueError("value must be finite")
    if value == 0:
        return "0"
    fixed = format(value, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


def _decimal_parts(value: Decimal) -> tuple[int, int]:
    """Return signed coefficient and base-10 exponent without using context."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    return (-coefficient if sign else coefficient), exponent


def _is_integral_decimal(value: Decimal) -> bool:
    """Return exact integrality from the Decimal tuple."""
    _sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        return False
    if not any(digits) or exponent >= 0:
        return True
    fractional_digits = -exponent
    if fractional_digits > len(digits):
        return False
    return not any(digits[-fractional_digits:])


def _constraint_is_valid(constraint: SettingConstraint) -> bool:
    """Return whether all numeric constraint members are usable and finite."""
    values = (constraint.min_value, constraint.max_value, constraint.step)
    if any(
        value is not None
        and (not isinstance(value, Decimal) or not value.is_finite())
        for value in values
    ):
        return False
    if constraint.step is not None and _decimal_parts(constraint.step)[0] == 0:
        return False
    if (
        constraint.min_value is not None
        and constraint.max_value is not None
        and constraint.min_value > constraint.max_value
    ):
        return False
    return True


def _is_exact_step_aligned(
    value: Decimal,
    origin: Decimal,
    step: Decimal,
) -> bool:
    """Check min-origin step divisibility using only arbitrary-precision integers."""
    value_coefficient, value_exponent = _decimal_parts(value)
    origin_coefficient, origin_exponent = _decimal_parts(origin)
    step_coefficient, step_exponent = _decimal_parts(step)
    common_exponent = min(value_exponent, origin_exponent, step_exponent)
    value_integer = value_coefficient * 10 ** (value_exponent - common_exponent)
    origin_integer = origin_coefficient * 10 ** (origin_exponent - common_exponent)
    step_integer = step_coefficient * 10 ** (step_exponent - common_exponent)
    return (value_integer - origin_integer) % abs(step_integer) == 0


def _parse_decimal_text(  # pylint: disable=too-many-return-statements
    value: object,
    constraint: SettingConstraint,
) -> Decimal | None:
    """Parse supported input through textual Decimal conversion."""
    if isinstance(value, bool):
        if not constraint.boolean_aliases:
            return None
        return Decimal(int(value))
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if constraint.boolean_aliases:
            alias = {
                "on": Decimal(1),
                "true": Decimal(1),
                "off": Decimal(0),
                "false": Decimal(0),
            }.get(raw.lower())
            if alias is not None:
                return alias
        text = raw
    elif isinstance(value, (int, float, Decimal)):
        text = str(value)
    else:
        return None
    try:
        return Decimal(text)
    except (DecimalException, ValueError):
        return None


def validate_constraint_value(
    value: object,
    constraint: SettingConstraint,
) -> SettingValueResult:  # pylint: disable=too-many-return-statements
    """Validate and canonicalize one value against an exact constraint."""
    if isinstance(value, bool) and not constraint.boolean_aliases:
        return SettingValueResult(False, None, "boolean alias is not allowed")

    parsed = _parse_decimal_text(value, constraint)
    if parsed is None:
        return SettingValueResult(False, None, "value is not numeric")
    if not parsed.is_finite():
        return SettingValueResult(False, None, "value must be finite")
    try:
        constraint_valid = _constraint_is_valid(constraint)
    except (ArithmeticError, TypeError, ValueError):
        constraint_valid = False
    if not constraint_valid:
        return SettingValueResult(False, None, "setting constraint is invalid")
    if constraint.integer_only and not _is_integral_decimal(parsed):
        return SettingValueResult(False, None, "value must be integer")
    if constraint.min_value is not None and parsed < constraint.min_value:
        minimum = canonical_decimal_text(constraint.min_value)
        return SettingValueResult(False, None, f"value below min ({minimum})")
    if constraint.max_value is not None and parsed > constraint.max_value:
        maximum = canonical_decimal_text(constraint.max_value)
        return SettingValueResult(False, None, f"value above max ({maximum})")

    origin = constraint.min_value if constraint.min_value is not None else Decimal(0)
    if constraint.step is not None:
        try:
            aligned = _is_exact_step_aligned(parsed, origin, constraint.step)
        except (ArithmeticError, TypeError, ValueError):
            return SettingValueResult(False, None, "setting constraint is invalid")
        if not aligned:
            return SettingValueResult(False, None, "value is not aligned to step")

    return SettingValueResult(True, canonical_decimal_text(parsed), "")


def validate_setting_value(
    table: str,
    key: str,
    value: Any,
) -> SettingValueResult:
    """Authorize a target and validate its value using its concrete constraint."""
    if not is_setting_allowed(table, key):
        return SettingValueResult(False, None, "setting is not allowed")
    constraint = SETTING_CONSTRAINTS.get((table, key))
    if constraint is None:
        return SettingValueResult(False, None, "setting constraint is not defined")
    return validate_constraint_value(value, constraint)
