"""Exact authorization constraints and canonical values for local Settings."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any, Iterator


# Synchronous control validation runs on the MQTT/event-loop path. These limits
# sit far above every current allowlisted value (largest: 100000) and the
# 30-digit exact-arithmetic regression while bounding parsing, integer scaling,
# and fixed-format output to a few hundred digits.
MAX_RAW_NUMERIC_TEXT_LENGTH = 128
MAX_DECIMAL_COEFFICIENT_DIGITS = 128
MAX_DECIMAL_EXPONENT = 256
MAX_DECIMAL_ALIGNMENT_GAP = 256
MAX_CANONICAL_DECIMAL_TEXT_LENGTH = 256
MAX_INTEGER_BIT_LENGTH = 426
NUMERIC_WORK_LIMIT_REASON = "numeric value exceeds work limits"


class _NumericWorkLimitError(ValueError):
    """Raised before numeric work would exceed the synchronous budget."""


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
    _ensure_decimal_work_bounds(value, require_canonical=True)
    fixed = format(value, "f")
    canonical = fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    if len(canonical) > MAX_CANONICAL_DECIMAL_TEXT_LENGTH:
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    return canonical


def _canonical_decimal_text_length(value: Decimal) -> int:
    """Return canonical fixed-text length without allocating that text."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    if not any(digits):
        return 1

    digit_count = len(digits)
    if exponent < 0:
        trailing_zeros = 0
        for digit in reversed(digits):
            if digit != 0 or trailing_zeros == -exponent:
                break
            trailing_zeros += 1
        digit_count -= trailing_zeros
        exponent += trailing_zeros

    sign_length = int(bool(sign))
    if exponent >= 0:
        return sign_length + digit_count + exponent
    point_position = digit_count + exponent
    if point_position > 0:
        return sign_length + digit_count + 1
    return sign_length + 2 + (-point_position) + digit_count


def _ensure_decimal_work_bounds(
    value: Decimal,
    *,
    require_canonical: bool = False,
) -> None:
    """Reject a Decimal tuple before coefficient, exponent, or format work."""
    _sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    if len(digits) > MAX_DECIMAL_COEFFICIENT_DIGITS:
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    if not any(digits):
        return
    if abs(exponent) > MAX_DECIMAL_EXPONENT:
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    if (
        require_canonical
        and _canonical_decimal_text_length(value)
        > MAX_CANONICAL_DECIMAL_TEXT_LENGTH
    ):
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)


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
    for bound in (constraint.min_value, constraint.max_value):
        if bound is not None:
            _ensure_decimal_work_bounds(bound, require_canonical=True)
    if constraint.step is not None:
        _ensure_decimal_work_bounds(constraint.step)
        if _decimal_parts(constraint.step)[0] <= 0:
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
    if (
        max(value_exponent, origin_exponent, step_exponent)
        - min(value_exponent, origin_exponent, step_exponent)
        > MAX_DECIMAL_ALIGNMENT_GAP
    ):
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    common_exponent = min(value_exponent, origin_exponent, step_exponent)
    value_integer = value_coefficient * 10 ** (value_exponent - common_exponent)
    origin_integer = origin_coefficient * 10 ** (origin_exponent - common_exponent)
    step_integer = step_coefficient * 10 ** (step_exponent - common_exponent)
    return (value_integer - origin_integer) % abs(step_integer) == 0


def _parse_decimal_text(  # pylint: disable=too-many-branches,too-many-return-statements
    value: object,
    constraint: SettingConstraint,
) -> Decimal | None:
    """Parse supported input through textual Decimal conversion."""
    if isinstance(value, bool):
        if not constraint.boolean_aliases:
            return None
        return Decimal(int(value))
    if isinstance(value, str):
        raw = str.__str__(value).strip()
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
        if len(raw) > MAX_RAW_NUMERIC_TEXT_LENGTH:
            raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
        text = raw
    elif isinstance(value, Decimal):
        if value.is_finite():
            _ensure_decimal_work_bounds(value)
        sign, digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):
            return value
        return Decimal((sign, digits, exponent))
    elif isinstance(value, int):
        if int.bit_length(value) > MAX_INTEGER_BIT_LENGTH:
            raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
        text = str(int(value))
        if len(text) > MAX_RAW_NUMERIC_TEXT_LENGTH:
            raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    elif isinstance(value, float):
        text = str(float(value))
    else:
        return None
    try:
        parsed = Decimal(text)
    except (DecimalException, ValueError):
        return None
    if parsed.is_finite():
        _ensure_decimal_work_bounds(parsed)
    return parsed


# pylint: disable=too-many-branches,too-many-return-statements
def validate_constraint_value(
    value: object,
    constraint: SettingConstraint,
) -> SettingValueResult:
    """Validate and canonicalize one value against an exact constraint."""
    if isinstance(value, bool) and not constraint.boolean_aliases:
        return SettingValueResult(False, None, "boolean alias is not allowed")

    try:
        parsed = _parse_decimal_text(value, constraint)
    except _NumericWorkLimitError:
        return SettingValueResult(False, None, NUMERIC_WORK_LIMIT_REASON)
    if parsed is None:
        return SettingValueResult(False, None, "value is not numeric")
    if not parsed.is_finite():
        return SettingValueResult(False, None, "value must be finite")
    if parsed.is_zero():
        parsed = Decimal(0)
    try:
        _ensure_decimal_work_bounds(parsed, require_canonical=True)
    except _NumericWorkLimitError:
        return SettingValueResult(False, None, NUMERIC_WORK_LIMIT_REASON)
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
    if origin.is_zero():
        origin = Decimal(0)
    if constraint.step is not None:
        if parsed == origin:
            aligned = True
        else:
            try:
                aligned = _is_exact_step_aligned(parsed, origin, constraint.step)
            except _NumericWorkLimitError:
                return SettingValueResult(False, None, NUMERIC_WORK_LIMIT_REASON)
            except (ArithmeticError, TypeError, ValueError):
                return SettingValueResult(False, None, "setting constraint is invalid")
        if not aligned:
            return SettingValueResult(False, None, "value is not aligned to step")

    try:
        value_text = canonical_decimal_text(parsed)
    except _NumericWorkLimitError:
        return SettingValueResult(False, None, NUMERIC_WORK_LIMIT_REASON)
    except (ArithmeticError, TypeError, ValueError):
        if parsed.is_finite():
            return SettingValueResult(False, None, "setting constraint is invalid")
        return SettingValueResult(False, None, "value must be finite")
    return SettingValueResult(True, value_text, "")
# pylint: enable=too-many-branches,too-many-return-statements


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
