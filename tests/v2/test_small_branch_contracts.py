"""Focused contracts for small defensive branches used by release coverage."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from protocol.crc import crc16_modbus
from protocol.frame import ValidatedFrame
from protocol.frames import build_setting_frame, czech_local_datetime_from_utc
from protocol import parser as parser_module
from protocol.parser import parse_direct_text, parse_frame_metadata
from proxy.mode import ModeManager
from sensor.loader import SensorMapLoader
from sensor.warnings import decode_warning_details, decode_warnings
from settings_constraints import (
    SettingConstraint,
    SettingValueResult,
    canonical_decimal_text,
    validate_constraint_value,
)


def _validated(inner: bytes) -> ValidatedFrame:
    crc = crc16_modbus(inner)
    raw = b"<Frame>" + inner + f"<CRC>{crc:05d}</CRC>".encode("ascii") + b"</Frame>\r\n"
    return ValidatedFrame(raw, 1, inner, crc, crc)


def test_warning_decoders_handle_empty_and_incomplete_definitions() -> None:
    assert decode_warnings(1, []) == []
    assert decode_warning_details(1, []) == []
    incomplete = [{"bit": None, "key": "bad"}, {"bit": 0}]
    assert decode_warnings(1, incomplete) == []
    assert decode_warning_details(1, incomplete) == []


def test_sensor_iterator_rejects_every_malformed_shape() -> None:
    loader = SensorMapLoader("unused")
    loader._data = {"sensors": []}
    assert loader.iter_sensors() == []

    loader._data = {
        "sensors": {
            1: {"name": "non-string key"},
            "table:key": "non-object metadata",
            "missing_separator": {},
            ":missing-table": {},
            "missing-key:": {},
            "valid:key": {"name": "valid"},
        }
    }
    assert loader.iter_sensors() == [("valid", "key", {"name": "valid"})]


@pytest.mark.asyncio
async def test_mode_transition_callbacks_reasonless_retry_and_invalid_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ModeManager(
        SimpleNamespace(proxy_mode="hybrid", hybrid_fail_threshold=1, hybrid_retry_interval=1)
    )
    transitions: list[tuple[str, float, str | None]] = []
    manager.on_hybrid_transition = lambda *args: transitions.append(args)
    moments = iter((10.0, 11.0, 12.0))
    monkeypatch.setattr("proxy.mode.time.time", lambda: next(moments))

    manager.record_failure("first")
    manager.record_failure()
    manager.record_success()

    assert [transition[0] for transition in transitions] == ["offline", "online"]
    assert await manager.apply_configured_mode("invalid") is False


def test_czech_time_handles_naive_and_aware_inputs() -> None:
    naive = czech_local_datetime_from_utc(datetime(2026, 1, 1, 12, 0))
    aware = czech_local_datetime_from_utc(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))
    assert naive.hour == 13
    assert aware.hour == 14


def test_setting_frame_rejects_non_string_dynamic_value() -> None:
    with pytest.raises(ValueError, match="valid XML"):
        build_setting_frame(
            device_id="DEV",
            table_name="table",
            item_name="item",
            value_text=1,  # type: ignore[arg-type]
            wire_id=1,
            wire_id_set=2,
            wire_dt="2026-01-01 00:00:00",
            tsec_text="1",
            ver_text="00001",
        )


def test_parser_rejects_a_mutated_validated_frame_before_xml_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _validated(b"<Result>ACK</Result><ID>1</ID>")
    monkeypatch.setattr(
        parser_module,
        "validate_frame",
        lambda _assembled: SimpleNamespace(validated=None),
    )
    assert parse_frame_metadata(frame) is None
    assert parse_direct_text(frame, "Result") is None


def test_constraint_helpers_fail_closed_for_absent_and_unsupported_values() -> None:
    assert tuple(SettingValueResult(False, None, "rejected")) == (
        False,
        None,
        "rejected",
    )
    with pytest.raises(ValueError, match="exact Decimal"):
        canonical_decimal_text(1)  # type: ignore[arg-type]

    constraint = SettingConstraint()
    assert validate_constraint_value("", constraint).reason == "value is not numeric"
    assert (
        validate_constraint_value(1 << 852, constraint).reason
        == "numeric value exceeds work limits"
    )
    assert validate_constraint_value(object(), constraint).reason == "value is not numeric"
