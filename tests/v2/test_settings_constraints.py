"""Exact Decimal validation tests for locally authorized settings."""
# pylint: disable=missing-function-docstring

from decimal import (
    Clamped,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)

import pytest
import settings_constraints as settings_module

from settings_constraints import (
    CONTROL_WRITE_WHITELIST,
    SettingConstraint,
    SettingValueResult,
    canonical_decimal_text,
    validate_constraint_value,
    validate_setting_value,
)


def test_rejects_boolean_without_explicit_alias() -> None:
    result = validate_constraint_value(
        True,
        SettingConstraint(Decimal("0"), Decimal("2"), Decimal("1"), True),
    )
    assert result.accepted is False
    assert result.reason == "boolean alias is not allowed"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, "1"), (False, "0"), ("on", "1"), ("off", "0")],
)
def test_accepts_declared_boolean_aliases(raw: object, expected: str) -> None:
    constraint = SettingConstraint(
        Decimal("0"), Decimal("1"), Decimal("1"), True, True
    )
    assert validate_constraint_value(raw, constraint).value_text == expected


@pytest.mark.parametrize(("raw", "expected"), [(" ON ", "1"), ("Off", "0")])
def test_boolean_aliases_are_trimmed_and_case_insensitive(
    raw: str, expected: str
) -> None:
    constraint = SettingConstraint(
        Decimal("0"), Decimal("1"), Decimal("1"), True, True
    )
    assert validate_constraint_value(raw, constraint).value_text == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", "1"), ("TRUE", "1"), (" false ", "0")],
)
def test_declared_boolean_aliases_preserve_true_false_compatibility(
    raw: str, expected: str
) -> None:
    constraint = SettingConstraint(
        Decimal("0"), Decimal("1"), Decimal("1"), True, True
    )
    assert validate_constraint_value(raw, constraint).value_text == expected


@pytest.mark.parametrize("raw", ["yes", "no"])
def test_boolean_aliases_reject_undeclared_words(raw: str) -> None:
    constraint = SettingConstraint(
        Decimal("0"), Decimal("1"), Decimal("1"), True, True
    )
    assert validate_constraint_value(raw, constraint) == SettingValueResult(
        False, None, "value is not numeric"
    )


@pytest.mark.parametrize(
    "raw", ["NaN", "Infinity", "-Infinity", float("nan"), float("inf")]
)
def test_rejects_nan_and_infinity(raw: object) -> None:
    result = validate_setting_value("tbl_box_prms", "MODE", raw)
    assert result == SettingValueResult(False, None, "value must be finite")


@pytest.mark.parametrize(
    ("raw", "accepted"),
    [
        ("0", True),
        ("10000", True),
        ("-100", False),
        ("10001", False),
        ("50", False),
        ("100", True),
    ],
)
def test_enforces_range_and_step(raw: str, accepted: bool) -> None:
    assert validate_setting_value("tbl_boiler_prms", "P_SET", raw).accepted is accepted


def test_step_is_relative_to_declared_minimum() -> None:
    constraint = SettingConstraint(
        min_value=Decimal("0.1"),
        max_value=Decimal("1.0"),
        step=Decimal("0.2"),
    )
    assert validate_constraint_value("0.3", constraint).accepted is True
    assert validate_constraint_value("0.2", constraint).reason == "value is not aligned to step"


def test_step_alignment_is_exact_for_coefficients_larger_than_context_precision() -> None:
    constraint = SettingConstraint(step=Decimal("0.1"))

    assert validate_constraint_value(
        "123456789012345678901234567890.0",
        constraint,
    ) == SettingValueResult(
        True,
        "123456789012345678901234567890",
        "",
    )
    assert validate_constraint_value(
        "123456789012345678901234567890.05",
        constraint,
    ) == SettingValueResult(False, None, "value is not aligned to step")


@pytest.mark.parametrize("raw", ["1E+257", "1E-257"])
def test_rejects_compact_nonzero_exponents_beyond_work_limit(raw: str) -> None:
    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


def test_rejects_alignment_exponent_gap_before_integer_scaling() -> None:
    constraint = SettingConstraint(step=Decimal("1E+200"))

    assert validate_constraint_value("1E-200", constraint) == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


@pytest.mark.parametrize("raw", ["0E+1000000", "0E-1000000"])
def test_normalizes_compact_extreme_zero_before_alignment(
    monkeypatch,
    raw: str,
) -> None:
    def unexpected_alignment(*_args: object) -> bool:
        raise AssertionError("zero must not enter exponent scaling")

    monkeypatch.setattr(
        settings_module,
        "_is_exact_step_aligned",
        unexpected_alignment,
    )
    try:
        result = validate_setting_value("tbl_boiler_prms", "P_SET", raw)
    except AssertionError as error:
        pytest.fail(str(error))

    assert result == SettingValueResult(True, "0", "")


def _install_tuple_materialization_probe(
    monkeypatch: pytest.MonkeyPatch,
    *forbidden: Decimal,
) -> list[Decimal]:
    materialized: list[Decimal] = []

    def materialize(value: Decimal):
        if any(value is candidate for candidate in forbidden):
            raise AssertionError("oversized Decimal tuple must not materialize")
        materialized.append(value)
        return Decimal.as_tuple(value)

    monkeypatch.setattr(
        settings_module,
        "_materialize_decimal_tuple",
        materialize,
        raising=False,
    )
    return materialized


def test_oversized_exact_decimal_rejects_before_tuple_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported = Decimal("1")
    oversized = Decimal("9" * 10_000)
    materialized = _install_tuple_materialization_probe(
        monkeypatch,
        oversized,
    )

    try:
        oversized_result = validate_constraint_value(
            oversized,
            SettingConstraint(),
        )
    except AssertionError as error:
        pytest.fail(str(error))
    supported_result = validate_constraint_value(supported, SettingConstraint())

    assert oversized_result == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )
    assert all(value is not oversized for value in materialized)
    assert any(value is supported for value in materialized)
    assert supported_result == SettingValueResult(True, "1", "")


def test_canonical_decimal_rejects_oversize_before_tuple_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported = Decimal("1")
    oversized = Decimal("9" * 10_000)
    materialized = _install_tuple_materialization_probe(
        monkeypatch,
        oversized,
    )

    try:
        with pytest.raises(ValueError, match="numeric value exceeds work limits"):
            canonical_decimal_text(oversized)
    except AssertionError as error:
        pytest.fail(str(error))

    assert canonical_decimal_text(supported) == "1"
    assert all(value is not oversized for value in materialized)
    assert any(value is supported for value in materialized)


@pytest.mark.parametrize("member", ["min_value", "max_value", "step"])
def test_oversized_exact_decimal_constraint_rejects_before_tuple_materialization(
    monkeypatch: pytest.MonkeyPatch,
    member: str,
) -> None:
    supported = Decimal("1")
    oversized = Decimal("9" * 10_000)
    materialized = _install_tuple_materialization_probe(
        monkeypatch,
        oversized,
    )
    supported_constraint = SettingConstraint(**{member: supported})
    oversized_constraint = SettingConstraint(**{member: oversized})

    try:
        supported_result = validate_constraint_value(
            Decimal("1"),
            supported_constraint,
        )
        oversized_result = validate_constraint_value(
            Decimal("1"),
            oversized_constraint,
        )
    except AssertionError as error:
        pytest.fail(str(error))

    assert supported_result == SettingValueResult(True, "1", "")
    assert any(value is supported for value in materialized)
    assert oversized_result == SettingValueResult(
        False, None, "setting constraint is invalid"
    )
    assert all(value is not oversized for value in materialized)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("1" + "0" * 295 + "E-295"), "1"),
        (Decimal("-1" + "0" * 295 + "E-295"), "-1"),
        (Decimal("0." + "1" + "0" * 295), "0.1"),
        (Decimal((0, (1,) + (0,) * 295, -295)), "1"),
    ],
    ids=["positive", "negative", "fractional", "tuple-constructed"],
)
def test_every_supported_296_digit_decimal_shape_reaches_exact_tuple_check(
    monkeypatch: pytest.MonkeyPatch,
    raw: Decimal,
    expected: str,
) -> None:
    materialized = _install_tuple_materialization_probe(monkeypatch)

    result = validate_constraint_value(raw, SettingConstraint())

    assert any(value is raw for value in materialized)
    assert result == SettingValueResult(True, expected, "")


def test_297_digit_decimal_rejects_through_exact_tuple_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Decimal("1" + "0" * 296 + "E-296")
    materialized = _install_tuple_materialization_probe(monkeypatch)

    result = validate_constraint_value(raw, SettingConstraint())

    assert any(value is raw for value in materialized)
    assert result == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


def test_decimal_prefilter_rejects_at_calibrated_slack_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack = getattr(
        settings_module,
        "_DECIMAL_TUPLE_PREFILTER_SLACK_DIGITS",
        None,
    )
    assert type(slack) is int  # pylint: disable=unidiomatic-typecheck
    assert 1 <= slack <= settings_module.MAX_DECIMAL_PREFILTER_SLACK_DIGITS
    last_tuple_checked = Decimal(
        "9" * (settings_module.MAX_DECIMAL_REPRESENTATION_DIGITS + slack)
    )
    first_prefiltered = Decimal(
        "9" * (settings_module.MAX_DECIMAL_REPRESENTATION_DIGITS + slack + 1)
    )
    materialized = _install_tuple_materialization_probe(
        monkeypatch,
        first_prefiltered,
    )

    assert validate_constraint_value(
        last_tuple_checked,
        SettingConstraint(),
    ) == SettingValueResult(False, None, "numeric value exceeds work limits")
    try:
        first_result = validate_constraint_value(
            first_prefiltered,
            SettingConstraint(),
        )
    except AssertionError as error:
        pytest.fail(str(error))

    assert any(value is last_tuple_checked for value in materialized)
    assert all(value is not first_prefiltered for value in materialized)
    assert first_result == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


def test_decimal_prefilter_fails_closed_without_trustworthy_size_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Decimal("1")
    materialized = _install_tuple_materialization_probe(monkeypatch, raw)
    monkeypatch.setattr(
        settings_module,
        "_decimal_backing_size",
        lambda _value: None,
        raising=False,
    )

    try:
        result = validate_constraint_value(raw, SettingConstraint())
    except AssertionError as error:
        pytest.fail(str(error))

    assert not materialized
    assert result == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


def test_rejects_129_significant_digit_numeric_text() -> None:
    raw = "9" * 129

    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


_NEGATIVE_128_DIGIT_INTEGER = "-" + "9" * 128
_POSITIVE_128_DIGIT_INTEGER = "9" * 128
_FRACTION_WITH_127_DIGITS = "0." + "1" * 127
_CANONICAL_TRAILING_ZERO_INTEGER = "1" + "0" * 128


@pytest.mark.parametrize(
    "raw",
    [
        _NEGATIVE_128_DIGIT_INTEGER,
        int(_NEGATIVE_128_DIGIT_INTEGER),
        Decimal(_NEGATIVE_128_DIGIT_INTEGER),
    ],
    ids=["canonical-str", "built-in-int", "built-in-decimal"],
)
def test_negative_128_digit_integer_boundary_is_type_invariant(raw: object) -> None:
    constraint = SettingConstraint(
        min_value=Decimal(_NEGATIVE_128_DIGIT_INTEGER),
        max_value=Decimal(0),
        step=Decimal(1),
        integer_only=True,
    )

    assert validate_constraint_value(raw, constraint) == SettingValueResult(
        True, _NEGATIVE_128_DIGIT_INTEGER, ""
    )


@pytest.mark.parametrize(
    "raw",
    [
        "+" + _POSITIVE_128_DIGIT_INTEGER,
        int(_POSITIVE_128_DIGIT_INTEGER),
        Decimal(_POSITIVE_128_DIGIT_INTEGER),
    ],
    ids=["signed-canonical-str", "built-in-int", "built-in-decimal"],
)
def test_positive_sign_at_128_digit_boundary_is_type_invariant(raw: object) -> None:
    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        True, _POSITIVE_128_DIGIT_INTEGER, ""
    )


@pytest.mark.parametrize(
    "raw",
    [_FRACTION_WITH_127_DIGITS, Decimal(_FRACTION_WITH_127_DIGITS)],
    ids=["canonical-str", "built-in-decimal"],
)
def test_127_digit_fraction_boundary_is_type_invariant(raw: object) -> None:
    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        True, _FRACTION_WITH_127_DIGITS, ""
    )


@pytest.mark.parametrize(
    "raw",
    [
        _CANONICAL_TRAILING_ZERO_INTEGER,
        int(_CANONICAL_TRAILING_ZERO_INTEGER),
        Decimal("1E+128"),
    ],
    ids=["canonical-str", "built-in-int", "built-in-decimal"],
)
def test_trailing_zero_integer_semantics_are_type_invariant(raw: object) -> None:
    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        True, _CANONICAL_TRAILING_ZERO_INTEGER, ""
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-1", "-1"),
        (-1, "-1"),
        (Decimal("-1"), "-1"),
        (-1.0, "-1"),
        ("0.5", "0.5"),
        (Decimal("0.5"), "0.5"),
        (0.5, "0.5"),
    ],
    ids=[
        "integer-str",
        "integer-int",
        "integer-decimal",
        "integer-exact-float",
        "fraction-str",
        "fraction-decimal",
        "fraction-exact-float",
    ],
)
def test_exactly_representable_float_boundaries_match_exact_types(
    raw: object,
    expected: str,
) -> None:
    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        True, expected, ""
    )


@pytest.mark.parametrize(
    "raw",
    [
        "9" * 129,
        int("9" * 129),
        Decimal("9" * 129),
        "-" + "9" * 129,
        -int("9" * 129),
        Decimal("-" + "9" * 129),
    ],
    ids=[
        "positive-str",
        "positive-int",
        "positive-decimal",
        "negative-str",
        "negative-int",
        "negative-decimal",
    ],
)
def test_129_significant_digit_boundary_is_type_invariant(raw: object) -> None:
    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


def test_bounded_whitespace_and_exponent_syntax_are_accepted() -> None:
    raw = " " * 16 + "+1E+127" + "\t" * 16

    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        True, "1" + "0" * 127, ""
    )


@pytest.mark.parametrize("raw", [" " * 17 + "1", "1" + "\t" * 17])
def test_rejects_whitespace_beyond_the_bounded_allowance(raw: str) -> None:
    assert validate_constraint_value(raw, SettingConstraint()) == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


class _HostilePreprocessingText(str):
    def __len__(self) -> int:
        raise AssertionError("virtual length must not run")

    def __str__(self) -> str:
        raise AssertionError("virtual string conversion must not run")

    def strip(self, _chars: str | None = None) -> str:
        raise AssertionError("virtual strip must not run")

    def lstrip(self, _chars: str | None = None) -> str:
        raise AssertionError("virtual left strip must not run")

    def rstrip(self, _chars: str | None = None) -> str:
        raise AssertionError("virtual right strip must not run")

    def lower(self) -> str:
        raise AssertionError("virtual lower must not run")


def test_oversized_switch_text_rejects_before_alias_preprocessing() -> None:
    constraint = SettingConstraint(boolean_aliases=True)
    raw = _HostilePreprocessingText(" " * 400 + "ON")

    try:
        result = validate_constraint_value(raw, constraint)
    except AssertionError as error:
        pytest.fail(str(error))

    assert result == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


def test_string_subclass_uses_non_virtual_bounded_preprocessing() -> None:
    constraint = SettingConstraint(boolean_aliases=True)
    raw = _HostilePreprocessingText(" ON ")

    try:
        result = validate_constraint_value(raw, constraint)
    except AssertionError as error:
        pytest.fail(str(error))

    assert result == SettingValueResult(True, "1", "")


class _HugeDecimalWithoutStringConversion(Decimal):  # pylint: disable=too-few-public-methods
    def __str__(self) -> str:
        raise AssertionError("oversized Decimal must be rejected before string conversion")


def test_large_decimal_subclass_fails_closed_before_string_conversion() -> None:
    raw = _HugeDecimalWithoutStringConversion("9" * 129)

    try:
        result = validate_constraint_value(raw, SettingConstraint())
    except AssertionError as error:
        pytest.fail(str(error))

    assert result == SettingValueResult(
        False, None, "value is not numeric"
    )


class _HugeIntWithoutStringConversion(int):
    def __str__(self) -> str:
        raise AssertionError("oversized int must be rejected before string conversion")


def test_large_int_subclass_fails_closed_before_decimal_text_conversion() -> None:
    raw = _HugeIntWithoutStringConversion(10**200)

    try:
        result = validate_constraint_value(raw, SettingConstraint())
    except AssertionError as error:
        pytest.fail(str(error))

    assert result == SettingValueResult(
        False, None, "value is not numeric"
    )


class _HostileDecimal(Decimal):
    def is_finite(self) -> bool:
        raise AssertionError("virtual Decimal.is_finite must not run")

    def as_tuple(self):
        raise AssertionError("virtual Decimal.as_tuple must not run")


class _HostileInt(int):
    def __int__(self) -> int:
        raise AssertionError("virtual int conversion must not run")

    def __str__(self) -> str:
        raise AssertionError("virtual int string conversion must not run")


class _HostileFloat(float):
    def __float__(self) -> float:
        raise AssertionError("virtual float conversion must not run")

    def __str__(self) -> str:
        raise AssertionError("virtual float string conversion must not run")


@pytest.mark.parametrize(
    "raw",
    [_HostileDecimal("1"), _HostileInt(1), _HostileFloat(1.0)],
    ids=["decimal-subclass", "int-subclass", "float-subclass"],
)
def test_numeric_subclasses_fail_closed_without_virtual_hooks(raw: object) -> None:
    try:
        result = validate_constraint_value(raw, SettingConstraint())
    except AssertionError as error:
        pytest.fail(str(error))

    assert result == SettingValueResult(False, None, "value is not numeric")


def test_decimal_subclass_constraint_fails_closed_without_virtual_hooks() -> None:
    constraint = SettingConstraint(min_value=_HostileDecimal("0"))

    try:
        result = validate_constraint_value("1", constraint)
    except AssertionError as error:
        pytest.fail(str(error))

    assert result == SettingValueResult(
        False, None, "setting constraint is invalid"
    )


def test_canonical_decimal_text_rejects_output_beyond_work_limit() -> None:
    with pytest.raises(ValueError, match="numeric value exceeds work limits"):
        canonical_decimal_text(Decimal("1E+256"))


def test_step_alignment_ignores_precision_exponent_and_trap_context() -> None:
    with localcontext() as context:
        context.prec = 1
        context.Emax = 1
        context.Emin = -1
        for signal in (
            Clamped,
            DivisionByZero,
            Inexact,
            InvalidOperation,
            Overflow,
            Rounded,
            Subnormal,
            Underflow,
        ):
            context.traps[signal] = True
        try:
            result = validate_setting_value("tbl_boiler_prms", "P_SET", "100")
        except DecimalException as error:
            pytest.fail(f"Decimal context signal escaped: {type(error).__name__}")

    assert result == SettingValueResult(True, "100", "")


def test_fractional_min_origin_step_is_exact_under_hostile_context() -> None:
    constraint = SettingConstraint(
        min_value=Decimal("1E-100"),
        step=Decimal("2E-100"),
    )

    with localcontext() as context:
        context.prec = 2
        context.Emax = 2
        context.Emin = -2
        context.traps[Underflow] = True
        context.traps[Subnormal] = True
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        try:
            aligned = validate_constraint_value("7E-100", constraint)
            neighbor = validate_constraint_value("8E-100", constraint)
        except DecimalException as error:
            pytest.fail(f"Decimal context signal escaped: {type(error).__name__}")

    assert aligned.accepted is True
    assert neighbor == SettingValueResult(False, None, "value is not aligned to step")


@pytest.mark.parametrize(
    "constraint",
    [
        SettingConstraint(step=Decimal("0")),
        SettingConstraint(step=Decimal("NaN")),
        SettingConstraint(min_value=Decimal("sNaN")),
        SettingConstraint(max_value=Decimal("Infinity")),
    ],
)
def test_malformed_constraint_returns_stable_rejection(
    constraint: SettingConstraint,
) -> None:
    with localcontext() as context:
        context.traps[InvalidOperation] = True
        try:
            result = validate_constraint_value("1", constraint)
        except DecimalException as error:
            pytest.fail(f"Decimal context signal escaped: {type(error).__name__}")

    assert result == SettingValueResult(False, None, "setting constraint is invalid")


@pytest.mark.parametrize(
    "step",
    [
        Decimal("-1"),
        Decimal("-0.1"),
        Decimal("-1E-100"),
        Decimal("-0"),
        Decimal("-0E+1000000"),
    ],
)
def test_rejects_every_non_positive_step_under_hostile_context(step: Decimal) -> None:
    with localcontext() as context:
        context.prec = 1
        context.Emax = 1
        context.Emin = -1
        context.traps[InvalidOperation] = True
        context.traps[Overflow] = True
        context.traps[Underflow] = True
        try:
            result = validate_constraint_value("2", SettingConstraint(step=step))
        except DecimalException as error:
            pytest.fail(f"Decimal context signal escaped: {type(error).__name__}")

    assert result == SettingValueResult(False, None, "setting constraint is invalid")


@pytest.mark.parametrize("raw", ["1.5", Decimal("2.0001"), "1E-100"])
def test_integer_constraint_rejects_fractional_decimal_extremes(raw: object) -> None:
    result = validate_constraint_value(raw, SettingConstraint(integer_only=True))
    assert result == SettingValueResult(False, None, "value must be integer")


def test_extreme_exponent_is_rejected_by_work_limit_before_range_arithmetic() -> None:
    result = validate_setting_value("tbl_boiler_prms", "P_SET", "1E+100000")
    assert result == SettingValueResult(
        False, None, "numeric value exceeds work limits"
    )


def test_canonicalizes_decimal_without_exponent() -> None:
    assert canonical_decimal_text(Decimal("1.2300E+3")) == "1230"
    assert canonical_decimal_text(Decimal("1E-7")) == "0.0000001"
    assert canonical_decimal_text(Decimal("-0.000")) == "0"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_canonicalization_rejects_non_finite_values(value: Decimal) -> None:
    with pytest.raises(ValueError, match="value must be finite"):
        canonical_decimal_text(value)


def test_float_is_parsed_from_its_decimal_text_not_binary_expansion() -> None:
    constraint = SettingConstraint(
        min_value=Decimal("0"),
        max_value=Decimal("1"),
        step=Decimal("0.1"),
    )
    assert validate_constraint_value(0.3, constraint) == SettingValueResult(
        True, "0.3", ""
    )


@pytest.mark.parametrize(
    ("table", "key"),
    [("tbl_box_prms", "UNKNOWN"), ("tbl_set", "T_Room")],
)
def test_rejects_target_without_allowlist_and_constraint(
    table: str, key: str
) -> None:
    assert validate_setting_value(table, key, "1") == SettingValueResult(
        False, None, "setting is not allowed"
    )


def test_rejects_allowlisted_target_without_concrete_constraint(monkeypatch) -> None:
    monkeypatch.setitem(CONTROL_WRITE_WHITELIST, "temporary", {"MISSING"})
    assert validate_setting_value("temporary", "MISSING", "1") == SettingValueResult(
        False, None, "setting constraint is not defined"
    )


def test_selector_rejects_boolean_aliases() -> None:
    assert validate_setting_value("tbl_box_prms", "MODE", True) == SettingValueResult(
        False, None, "boolean alias is not allowed"
    )
    assert validate_setting_value("tbl_box_prms", "MODE", "on") == SettingValueResult(
        False, None, "value is not numeric"
    )


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("tbl_batt_prms", "FMT_ON"),
        ("tbl_box_prms", "BAT_FORMAT"),
        ("tbl_box_prms", "SA"),
        ("tbl_box_prms", "RQRESET"),
        ("tbl_boiler_prms", "ISON"),
        ("tbl_boiler_prms", "MANUAL"),
        ("tbl_boiler_prms", "SSR0"),
        ("tbl_boiler_prms", "SSR1"),
        ("tbl_boiler_prms", "SSR2"),
        ("tbl_invertor_prm1", "BUZ_MUT"),
        ("tbl_invertor_prm1", "GEN_AC_SRC"),
    ],
)
def test_current_zero_one_switch_targets_accept_boolean_aliases(
    table: str, key: str
) -> None:
    assert validate_setting_value(table, key, "on").value_text == "1"
