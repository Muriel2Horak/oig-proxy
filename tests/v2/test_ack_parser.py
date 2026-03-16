import os
import sys

# pyright: reportMissingImports=false

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "addon", "oig-proxy-v2")))

from twin.ack_parser import parse_box_ack, parse_tbl_events_ack


def test_parse_box_ack_minimal_ack() -> None:
    parsed = parse_box_ack(b"<Result>ACK</Result>")
    assert parsed == {"result": "ACK"}


def test_parse_box_ack_end_with_details() -> None:
    xml = b"<Result>END</Result><TblName>tbl_set</TblName><ToDo>T_Room</ToDo><DT>2026-03-12 12:00:00</DT>"
    parsed = parse_box_ack(xml)
    assert parsed == {
        "result": "END",
        "table": "tbl_set",
        "todo": "T_Room",
        "timestamp": "2026-03-12 12:00:00",
    }


def test_parse_box_ack_with_reason() -> None:
    xml = b"<Result>ACK</Result><Reason>Setting</Reason>"
    parsed = parse_box_ack(xml)
    assert parsed == {"result": "ACK", "reason": "Setting"}


def test_parse_box_ack_non_ack_returns_none() -> None:
    assert parse_box_ack(b"<Result>NACK</Result>") is None


def test_parse_tbl_events_ack_setting_event() -> None:
    parsed = parse_tbl_events_ack(
        {
            "_table": "tbl_events",
            "Type": "Setting",
            "Content": "Remotely : tbl_box_prms / MODE: [3]->[0]",
        }
    )
    assert parsed == {"table": "tbl_box_prms", "key": "MODE", "value": "0"}


def test_parse_tbl_events_ack_non_setting_returns_none() -> None:
    assert parse_tbl_events_ack({"_table": "tbl_events", "Type": "Factory"}) is None
