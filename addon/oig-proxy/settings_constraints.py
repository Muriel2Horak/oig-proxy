"""Exact authorization constraints and canonical values for local Settings."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal, DecimalException, DecimalTuple
from typing import Any, Iterator


# Synchronous control validation runs on the MQTT/event-loop path. The raw
# ceiling is a syntax budget: a 256-character canonical value, eight characters
# for sign/decimal/exponent syntax, and at most 16 whitespace characters on each
# side. Semantic limits apply equally after parsing every exact built-in type.
MAX_CANONICAL_DECIMAL_TEXT_LENGTH = 256
MAX_RAW_NUMERIC_SYNTAX_OVERHEAD = 8
MAX_NUMERIC_WHITESPACE_PER_SIDE = 16
MAX_RAW_NUMERIC_TEXT_LENGTH = (
    MAX_CANONICAL_DECIMAL_TEXT_LENGTH
    + MAX_RAW_NUMERIC_SYNTAX_OVERHEAD
    + 2 * MAX_NUMERIC_WHITESPACE_PER_SIDE
)
MAX_DECIMAL_REPRESENTATION_DIGITS = MAX_RAW_NUMERIC_TEXT_LENGTH
# CPython's C Decimal stores several base-10 digits per allocation word. A
# trusted size threshold may therefore admit a short plateau past the exact
# tuple limit. Capacity can exceed logical digits after arithmetic; such an
# overallocated value fails closed because reading its logical length would
# materialize the coefficient. Calibration must find the next size increase.
MAX_DECIMAL_PREFILTER_SLACK_DIGITS = 32
MAX_DECIMAL_COEFFICIENT_DIGITS = 128
MAX_DECIMAL_EXPONENT = 256
MAX_DECIMAL_ALIGNMENT_GAP = 256
# Every semantically admissible integer has at most 256 decimal magnitude
# digits. Bit length 851 also admits the first 257-digit boundary for a bounded
# exact tuple check, without rendering the integer to text.
MAX_INTEGER_BIT_LENGTH = 851
NUMERIC_WORK_LIMIT_REASON = "numeric value exceeds work limits"


class _NumericWorkLimitError(ValueError):
    """Raised before numeric work would exceed the synchronous budget."""


def _decimal_backing_size(value: Decimal) -> int | None:
    """Return CPython Decimal backing bytes without inspecting its coefficient."""
    try:
        size = Decimal.__sizeof__(value)
    except (ArithmeticError, AttributeError, TypeError, ValueError):
        return None
    if type(size) is not int or size <= 0:  # pylint: disable=unidiomatic-typecheck
        return None
    return size


def _decimal_size_sentinel(digit_count: int) -> Decimal:
    """Build one bounded exact Decimal used only during import calibration."""
    return Decimal((0, (9,) * digit_count, 0))


def _calibrate_decimal_tuple_prefilter(  # pylint: disable=too-many-return-statements
) -> tuple[int | None, int | None]:
    """Calibrate a conservative O(1) gate for Decimal tuple materialization."""
    if sys.implementation.name != "cpython":
        return None, None
    try:
        digits = (9,) * MAX_DECIMAL_REPRESENTATION_DIGITS
        trailing_zeros = (1,) + (0,) * (
            MAX_DECIMAL_REPRESENTATION_DIGITS - 1
        )
        supported_shapes = (
            Decimal((0, digits, 0)),
            Decimal((1, digits, 0)),
            Decimal((0, digits, -MAX_DECIMAL_REPRESENTATION_DIGITS)),
            Decimal(
                (
                    0,
                    trailing_zeros,
                    -(MAX_DECIMAL_REPRESENTATION_DIGITS - 1),
                )
            ),
        )
        supported_sizes = [
            _decimal_backing_size(value) for value in supported_shapes
        ]
        exact_boundary_size = _decimal_backing_size(
            _decimal_size_sentinel(MAX_DECIMAL_REPRESENTATION_DIGITS + 1)
        )
        baseline_size = _decimal_backing_size(Decimal(0))
    except (ArithmeticError, TypeError, ValueError):
        return None, None

    if (
        baseline_size is None
        or exact_boundary_size is None
        or any(size is None for size in supported_sizes)
    ):
        return None, None
    concrete_sizes = [size for size in supported_sizes if size is not None]
    if (
        not concrete_sizes
        or len(set(concrete_sizes)) != 1
        or concrete_sizes[0] <= baseline_size
        or exact_boundary_size != concrete_sizes[0]
    ):
        return None, None

    size_limit = concrete_sizes[0]
    probe_sizes: list[int] = [exact_boundary_size]
    for extra_digits in range(2, MAX_DECIMAL_PREFILTER_SLACK_DIGITS + 2):
        size = _decimal_backing_size(
            _decimal_size_sentinel(
                MAX_DECIMAL_REPRESENTATION_DIGITS + extra_digits
            )
        )
        if size is None:
            return None, None
        probe_sizes.append(size)
    if any(
        later < earlier
        for earlier, later in zip(probe_sizes, probe_sizes[1:])
    ):
        return None, None

    first_growth = next(
        (
            extra_digits
            for extra_digits, size in enumerate(probe_sizes, start=1)
            if size > size_limit
        ),
        None,
    )
    if first_growth is None:
        return None, None
    return size_limit, first_growth - 1


(
    _DECIMAL_TUPLE_PREFILTER_SIZE_LIMIT,
    _DECIMAL_TUPLE_PREFILTER_SLACK_DIGITS,
) = _calibrate_decimal_tuple_prefilter()


def _materialize_decimal_tuple(value: Decimal) -> DecimalTuple:
    """Materialize a Decimal tuple only after the backing-size guard."""
    return Decimal.as_tuple(value)


def _bounded_decimal_tuple(value: Decimal) -> DecimalTuple:
    """Return an exact bounded tuple, failing closed before proportional work."""
    size = _decimal_backing_size(value)
    if (
        _DECIMAL_TUPLE_PREFILTER_SIZE_LIMIT is None
        or size is None
        or size > _DECIMAL_TUPLE_PREFILTER_SIZE_LIMIT
    ):
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    value_tuple = _materialize_decimal_tuple(value)
    if len(value_tuple.digits) > MAX_DECIMAL_REPRESENTATION_DIGITS:
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    return value_tuple


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
    if type(value) is not Decimal:  # pylint: disable=unidiomatic-typecheck
        raise ValueError("value must be an exact Decimal")
    if not value.is_finite():
        raise ValueError("value must be finite")
    value = _normalize_decimal_for_semantics(value, require_canonical=True)
    if value.is_zero():
        return "0"
    fixed = format(value, "f")
    canonical = fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    if len(canonical) > MAX_CANONICAL_DECIMAL_TEXT_LENGTH:
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    return canonical


def _canonical_decimal_text_length(value: Decimal) -> int:
    """Return canonical fixed-text length without allocating that text."""
    sign, digits, exponent = _bounded_decimal_tuple(value)
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


def _normalize_decimal_for_semantics(
    value: Decimal,
    *,
    require_canonical: bool = False,
) -> Decimal:
    """Return a representation-invariant Decimal within semantic work limits."""
    sign, digits, exponent = _bounded_decimal_tuple(value)
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    if not any(digits):
        return Decimal(0)

    trailing_zeros = 0
    for digit in reversed(digits):
        if digit != 0:
            break
        trailing_zeros += 1
    if trailing_zeros:
        digits = digits[:-trailing_zeros]
        exponent += trailing_zeros

    if len(digits) > MAX_DECIMAL_COEFFICIENT_DIGITS:
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    if abs(exponent) > MAX_DECIMAL_EXPONENT:
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    normalized = Decimal((sign, digits, exponent))
    if (
        require_canonical
        and _canonical_decimal_text_length(normalized)
        > MAX_CANONICAL_DECIMAL_TEXT_LENGTH
    ):
        raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
    return normalized


def _decimal_parts(value: Decimal) -> tuple[int, int]:
    """Return signed coefficient and base-10 exponent without using context."""
    sign, digits, exponent = _bounded_decimal_tuple(value)
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    return (-coefficient if sign else coefficient), exponent


def _is_integral_decimal(value: Decimal) -> bool:
    """Return exact integrality from the Decimal tuple."""
    _sign, digits, exponent = _bounded_decimal_tuple(value)
    if not isinstance(exponent, int):
        return False
    if not any(digits) or exponent >= 0:
        return True
    fractional_digits = -exponent
    if fractional_digits > len(digits):
        return False
    return not any(digits[-fractional_digits:])


def _normalized_constraint_members(
    constraint: SettingConstraint,
) -> tuple[Decimal | None, Decimal | None, Decimal | None] | None:
    """Return bounded exact constraint members, or None when malformed."""
    values = (constraint.min_value, constraint.max_value, constraint.step)
    if any(
        value is not None
        and (
            type(value) is not Decimal  # pylint: disable=unidiomatic-typecheck
            or not value.is_finite()
        )
        for value in values
    ):
        return None
    minimum = (
        _normalize_decimal_for_semantics(
            constraint.min_value,
            require_canonical=True,
        )
        if constraint.min_value is not None
        else None
    )
    maximum = (
        _normalize_decimal_for_semantics(
            constraint.max_value,
            require_canonical=True,
        )
        if constraint.max_value is not None
        else None
    )
    step = (
        _normalize_decimal_for_semantics(constraint.step)
        if constraint.step is not None
        else None
    )
    if step is not None and _decimal_parts(step)[0] <= 0:
        return None
    if (
        minimum is not None
        and maximum is not None
        and minimum > maximum
    ):
        return None
    return minimum, maximum, step


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
        total_length = str.__len__(value)
        if total_length > MAX_RAW_NUMERIC_TEXT_LENGTH:
            raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
        text = str.__str__(value)
        left_stripped = str.lstrip(text)
        right_stripped = str.rstrip(text)
        if (
            total_length - len(left_stripped) > MAX_NUMERIC_WHITESPACE_PER_SIDE
            or total_length - len(right_stripped)
            > MAX_NUMERIC_WHITESPACE_PER_SIDE
        ):
            raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
        raw = str.strip(text)
        if not raw:
            return None
        if constraint.boolean_aliases:
            alias = {
                "on": Decimal(1),
                "true": Decimal(1),
                "off": Decimal(0),
                "false": Decimal(0),
            }.get(str.lower(raw))
            if alias is not None:
                return alias
        text = raw
    elif type(value) is Decimal:  # pylint: disable=unidiomatic-typecheck
        if value.is_finite():
            return _normalize_decimal_for_semantics(value)
        return value
    elif type(value) is int:  # pylint: disable=unidiomatic-typecheck
        if int.bit_length(value) > MAX_INTEGER_BIT_LENGTH:
            raise _NumericWorkLimitError(NUMERIC_WORK_LIMIT_REASON)
        return _normalize_decimal_for_semantics(Decimal(value))
    elif type(value) is float:  # pylint: disable=unidiomatic-typecheck
        text = str(value)
    elif isinstance(value, (Decimal, int, float)):
        return None
    else:
        return None
    try:
        parsed = Decimal(text)
    except (DecimalException, ValueError):
        return None
    if parsed.is_finite():
        return _normalize_decimal_for_semantics(parsed)
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
    try:
        parsed = _normalize_decimal_for_semantics(parsed, require_canonical=True)
    except _NumericWorkLimitError:
        return SettingValueResult(False, None, NUMERIC_WORK_LIMIT_REASON)
    try:
        normalized_constraint = _normalized_constraint_members(constraint)
    except (ArithmeticError, TypeError, ValueError):
        normalized_constraint = None
    if normalized_constraint is None:
        return SettingValueResult(False, None, "setting constraint is invalid")
    minimum, maximum, step = normalized_constraint
    if constraint.integer_only and not _is_integral_decimal(parsed):
        return SettingValueResult(False, None, "value must be integer")
    if minimum is not None and parsed < minimum:
        minimum_text = canonical_decimal_text(minimum)
        return SettingValueResult(False, None, f"value below min ({minimum_text})")
    if maximum is not None and parsed > maximum:
        maximum_text = canonical_decimal_text(maximum)
        return SettingValueResult(False, None, f"value above max ({maximum_text})")

    origin = minimum if minimum is not None else Decimal(0)
    if step is not None:
        if parsed == origin:
            aligned = True
        else:
            try:
                aligned = _is_exact_step_aligned(parsed, origin, step)
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
