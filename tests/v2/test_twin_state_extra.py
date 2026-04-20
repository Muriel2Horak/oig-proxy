"""Additional coverage tests for twin state and ack parsing."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from __future__ import annotations

import re
from datetime import datetime

import pytest

import twin.state as twin_state
from twin.ack_parser import parse_tbl_events_ack


class _FrozenDateTime:
    @staticmethod
    def now(tz=None):
        if tz is None:
            return datetime(2026, 3, 12, 12, 34, 56)
        return datetime(2026, 3, 12, 11, 34, 56, tzinfo=twin_state.timezone.utc)


def test_build_setting_xml_generates_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(twin_state, "datetime", _FrozenDateTime)
    monkeypatch.setattr(twin_state.secrets, "randbelow", lambda limit: 12345)

    setting = twin_state.TwinSetting("tbl_set", "MODE", 7, enqueued_at=1.0)
    xml = setting.build_setting_xml("dev-1", id_set=200, msg_id=10000001, confirm="Ack")

    assert "<ID>10000001</ID>" in xml
    assert "<ID_Device>dev-1</ID_Device>" in xml
    assert "<ID_Set>200</ID_Set>" in xml
    assert "<DT>12.03.2026 12:34:56</DT>" in xml
    assert "<TSec>2026-03-12 11:34:56</TSec>" in xml
    assert "<Confirm>Ack</Confirm>" in xml
    assert xml.endswith("</Frame>")

    crc_text = re.search(r"<CRC>(\d+)</CRC>", xml)
    assert crc_text is not None
    without_crc = xml.split("<CRC>", maxsplit=1)[0]
    assert int(crc_text.group(1)) == setting._calculate_crc16(without_crc)


def test_queue_id_generation_audit_id_and_explicit_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = twin_state.TwinQueue()
    queue._next_id_set = 999_999_999

    assert queue._generate_id_set() == 999_999_999
    assert queue._next_id_set == 100_000_000

    monkeypatch.setattr(twin_state.time, "time", lambda: 1_700_000_000.123)
    monkeypatch.setattr(twin_state.secrets, "randbelow", lambda limit: 42)

    audit_id = queue._generate_audit_id()
    assert audit_id == "aud_01700000000123_000042"

    queue.enqueue("tbl_set", "MODE", 1, confirm="Ack", audit_id="audit-explicit")
    setting = queue.get("tbl_set", "MODE")
    assert setting is not None
    assert setting.audit_id == "audit-explicit"
    assert setting.confirm == "Ack"
    assert setting.msg_id == 10000042
    assert setting.id_set == 100_000_000


def test_parse_tbl_events_ack_rejects_non_string_and_non_matching_content() -> None:
    assert parse_tbl_events_ack({"_table": "tbl_events", "Type": "Setting", "Content": 123}) is None
    assert parse_tbl_events_ack(
        {"_table": "tbl_events", "Type": "Setting", "Content": "Remotely : malformed"}
    ) is None
