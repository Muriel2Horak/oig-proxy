"""Exact Decimal validation tests for locally authorized settings."""
# pylint: disable=missing-function-docstring

from decimal import Decimal

import pytest

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


@pytest.mark.parametrize("raw", ["1.5", Decimal("2.0001"), "1E-100"])
def test_integer_constraint_rejects_fractional_decimal_extremes(raw: object) -> None:
    result = validate_constraint_value(raw, SettingConstraint(integer_only=True))
    assert result == SettingValueResult(False, None, "value must be integer")


def test_extreme_exponent_is_rejected_by_range_before_step_arithmetic() -> None:
    result = validate_setting_value("tbl_boiler_prms", "P_SET", "1E+100000")
    assert result == SettingValueResult(False, None, "value above max (10000)")


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
