"""Additional coverage tests for twin state and ack parsing."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access,too-few-public-methods

from __future__ import annotations

import pytest

import twin.state as twin_state
from twin.ack_parser import parse_tbl_events_ack


def test_queue_id_generation_audit_id_and_explicit_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = twin_state.TwinQueue()
    queue._next_id_set = 1_700_000_000

    assert queue._generate_id_set() == 1_700_000_000
    assert queue._next_id_set == 1_700_000_001

    monkeypatch.setattr(twin_state.time, "time", lambda: 1_700_000_000.123)
    monkeypatch.setattr(twin_state.secrets, "randbelow", lambda limit: 42)

    audit_id = queue._generate_audit_id()
    assert audit_id == "aud_01700000000123_000042"

    queue.enqueue("tbl_set", "MODE", 1, confirm="Ack", audit_id="audit-explicit")
    setting = queue.get("tbl_set", "MODE")
    assert setting is not None
    assert setting.audit_id == "audit-explicit"
    assert setting.confirm == "Ack"
    assert setting.msg_id == 14000042
    assert setting.id_set == 1_700_000_001


def test_parse_tbl_events_ack_rejects_non_string_and_non_matching_content() -> None:
    assert parse_tbl_events_ack({"_table": "tbl_events", "Type": "Setting", "Content": 123}) is None
    assert parse_tbl_events_ack(
        {"_table": "tbl_events", "Type": "Setting", "Content": "Remotely : malformed"}
    ) is None
