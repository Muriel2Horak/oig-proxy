"""Tests for fail-closed local-control configuration."""
# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison

from __future__ import annotations

import pytest

from config import Config


CONTROL_ENVIRONMENT = (
    "CONTROL_MQTT_ENABLED",
    "CONTROL_ACK_TIMEOUT_S",
    "CONTROL_EVENT_TIMEOUT_S",
    "CONTROL_COMMAND_TTL_S",
    "CONTROL_MAX_ATTEMPTS",
    "CLOUD_ACK_TIMEOUT",
    "TWIN_DB_PATH",
    "CLOUD_DIALOG_TIMEOUT_S",
)


@pytest.fixture(autouse=True)
def clear_control_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep control parsing tests independent from the caller environment."""
    for name in CONTROL_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def test_control_defaults_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONTROL_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    config = Config()

    assert config.control_mqtt_enabled is False
    assert config.control_ack_timeout_s == 30.0
    assert config.control_event_timeout_s == 300.0
    assert config.control_command_ttl_s == 900.0
    assert config.control_max_attempts == 8
    assert config.twin_db_path == "/data/twin_queue.db"
    assert config.cloud_dialog_timeout_s == 30.0
    assert config.startup_warnings == ()


@pytest.mark.parametrize("raw", ["", "yes", "on", "2", "invalid"])
def test_invalid_control_gate_is_disabled_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("CONTROL_MQTT_ENABLED", raw)

    config = Config()

    assert config.control_mqtt_enabled is False
    assert config.startup_warnings == (
        "invalid CONTROL_MQTT_ENABLED; local control remains disabled",
    )


@pytest.mark.parametrize(
    "raw",
    ["1", " true ", "TRUE"],
)
def test_control_gate_only_enables_normalized_true_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("CONTROL_MQTT_ENABLED", raw)

    config = Config()

    assert config.control_mqtt_enabled is True
    assert config.startup_warnings == ()


@pytest.mark.parametrize(
    "raw",
    ["0", " false ", "FALSE"],
)
def test_control_gate_normalizes_explicit_false_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("CONTROL_MQTT_ENABLED", raw)

    config = Config()

    assert config.control_mqtt_enabled is False
    assert config.startup_warnings == ()


@pytest.mark.parametrize(
    ("new", "legacy", "expected", "warnings"),
    [
        ("12", "99", 12.0, ()),
        (None, "44", 44.0, ("CLOUD_ACK_TIMEOUT is deprecated for local control",)),
        ("bad", "44", 30.0, ("invalid CONTROL_ACK_TIMEOUT_S; using 30",)),
        (None, "bad", 30.0, ("invalid CLOUD_ACK_TIMEOUT; using 30",)),
        ("0", None, 1.0, ()),
    ],
)
def test_control_ack_timeout_precedence(
    monkeypatch: pytest.MonkeyPatch,
    new: str | None,
    legacy: str | None,
    expected: float,
    warnings: tuple[str, ...],
) -> None:
    for name, value in (("CONTROL_ACK_TIMEOUT_S", new), ("CLOUD_ACK_TIMEOUT", legacy)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    config = Config()

    assert config.control_ack_timeout_s == expected
    assert config.cloud_ack_timeout == expected
    assert config.startup_warnings == warnings


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-Infinity"])
@pytest.mark.parametrize(
    ("name", "default"),
    [
        ("CONTROL_ACK_TIMEOUT_S", 30.0),
        ("CONTROL_EVENT_TIMEOUT_S", 300.0),
        ("CONTROL_COMMAND_TTL_S", 900.0),
        ("CLOUD_DIALOG_TIMEOUT_S", 30.0),
    ],
)
def test_non_finite_lifecycle_timeout_uses_default_once(
    monkeypatch: pytest.MonkeyPatch, name: str, default: float, raw: str
) -> None:
    monkeypatch.setenv(name, raw)

    config = Config()

    assert getattr(config, name.lower()) == default
    assert config.startup_warnings == (f"invalid {name}; using {default:g}",)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 1),
        ("1", 1),
        ("8", 8),
        ("9", 8),
        (" 2 ", 2),
        ("2.5", 8),
        ("0x2", 8),
        ("x", 8),
    ],
)
def test_control_max_attempts_bounds(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv("CONTROL_MAX_ATTEMPTS", raw)

    assert Config().control_max_attempts == expected
