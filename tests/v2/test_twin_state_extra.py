"""Additional coverage tests for twin state and ack parsing."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access,too-few-public-methods

from __future__ import annotations

from twin.ack_parser import parse_tbl_events_ack


def test_parse_tbl_events_ack_rejects_non_string_and_non_matching_content() -> None:
    assert parse_tbl_events_ack({"_table": "tbl_events", "Type": "Setting", "Content": 123}) is None
    assert parse_tbl_events_ack(
        {"_table": "tbl_events", "Type": "Setting", "Content": "Remotely : malformed"}
    ) is None
