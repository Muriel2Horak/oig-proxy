# pylint: disable=missing-module-docstring,missing-function-docstring
# pylint: disable=protected-access,redefined-outer-name,wrong-import-position
import hashlib
import os
import sys

import pytest

# pyright: reportMissingImports=false

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "addon", "oig-proxy")
    ),
)

from protocol.crc import crc16_modbus  # noqa: E402
from protocol.frame import (  # noqa: E402
    AssembledFrame,
    FrameDirection,
    FrameValidationError,
    ValidatedFrame,
    validate_frame,
)
from twin.ack_parser import (  # noqa: E402
    SettingEvent,
    SettingResponse,
    derive_event_evidence_id,
    parse_box_ack,
    parse_setting_event,
    parse_setting_event_content,
    parse_setting_response,
    parse_tbl_events_ack,
)
from twin import ack_parser as ack_parser_module  # noqa: E402


def make_validated(inner: bytes, *, received_at_ms: int = 1) -> ValidatedFrame:
    crc = crc16_modbus(inner)
    raw = b"<Frame>" + inner + f"<CRC>{crc:05d}</CRC>".encode("ascii") + b"</Frame>\r\n"
    return ValidatedFrame(raw, received_at_ms, inner, crc, crc)


@pytest.fixture
def validated_ack() -> ValidatedFrame:
    return make_validated(
        b"<Result>ACK</Result><Reason>Setting</Reason>"
        b"<Rdt>06.08.2026 10:12:00</Rdt>"
    )


@pytest.fixture
def validated_event() -> ValidatedFrame:
    return make_validated(
        b"<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
        b"<ID_Set>42</ID_Set><DT>06.08.2026 10:12:01</DT>"
        b"<Type>Setting</Type>"
        b"<Content>Remotely : tbl_box_prms / MODE: [1]-&gt;[2]</Content>"
    )


def test_parse_box_ack_minimal_ack() -> None:
    parsed = parse_box_ack(b"<Result>ACK</Result>")
    assert parsed == {"result": "ACK"}


def test_parse_box_ack_end_with_details() -> None:
    xml = (
        b"<Result>END</Result><TblName>tbl_set</TblName><ToDo>T_Room</ToDo>"
        b"<DT>2026-03-12 12:00:00</DT>"
    )
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
    # NACK is now a valid parsed result (used for settings audit NACK tracking)
    assert parse_box_ack(b"<Result>NACK</Result>") == {"result": "NACK"}


def test_parse_box_ack_requires_result() -> None:
    assert parse_box_ack(b"<Reason>Setting</Reason>") is None


@pytest.mark.parametrize(
    "content",
    (
        "Remote",
        "Remotely tbl/item: [1]->[2]",
        "Remotely : tbl/item [1]->[2]",
        "Remotely : tbl/item: value",
        "Remotely : tbl[bad]/item: [1]->[2]",
    ),
)
def test_parse_setting_event_content_rejects_incomplete_grammar(content: str) -> None:
    assert parse_setting_event_content(content) is None


def test_parse_setting_event_content_rejects_missing_suffix() -> None:
    assert parse_setting_event_content("Remotely") is None


def test_parse_setting_event_content_rejects_non_text() -> None:
    assert parse_setting_event_content(None) is None  # type: ignore[arg-type]


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


def test_parse_tbl_events_ack_rejects_wrong_table() -> None:
    assert parse_tbl_events_ack({"_table": "tbl_actual", "Type": "Setting"}) is None


def test_parse_setting_ack_returns_reason_rdt_and_exact_sha256(
    validated_ack: ValidatedFrame,
) -> None:
    response = parse_setting_response(
        validated_ack, direction=FrameDirection.BOX_TO_PROXY
    )

    assert response == SettingResponse(
        result="ACK",
        reason="Setting",
        rdt_text="06.08.2026 10:12:00",
        fingerprint=hashlib.sha256(validated_ack.raw).hexdigest(),
    )


def test_parse_setting_nack_preserves_wc_diagnostic_reason() -> None:
    frame = make_validated(
        b"<Result>NACK</Result><Reason>WC</Reason>"
        b"<Rdt>06.08.2026 10:12:00</Rdt>"
    )

    response = parse_setting_response(frame, direction=FrameDirection.BOX_TO_PROXY)

    assert response == SettingResponse(
        result="NACK",
        reason="WC",
        rdt_text="06.08.2026 10:12:00",
        fingerprint=hashlib.sha256(frame.raw).hexdigest(),
    )


def test_parse_setting_response_rejects_cloud_direction(
    validated_ack: ValidatedFrame,
) -> None:
    assert (
        parse_setting_response(
            validated_ack, direction=FrameDirection.CLOUD_TO_PROXY
        )
        is None
    )


def test_parse_setting_response_rejects_non_ack_result() -> None:
    frame = make_validated(b"<Result>END</Result><Reason>Setting</Reason>")

    assert parse_setting_response(frame, direction=FrameDirection.BOX_TO_PROXY) is None


def test_parse_setting_response_rejects_duplicate_and_nested_result() -> None:
    duplicate = make_validated(b"<Result>ACK</Result><Result>ACK</Result>")
    nested = make_validated(b"<Wrapper><Result>ACK</Result></Wrapper>")

    assert parse_setting_response(duplicate, direction=FrameDirection.BOX_TO_PROXY) is None
    assert parse_setting_response(nested, direction=FrameDirection.BOX_TO_PROXY) is None


@pytest.mark.parametrize(
    "result_xml",
    (
        b'<Result source="box">ACK</Result>',
        b"<Result>ACK<!--note--></Result>",
        b"<Result>ACK<?notice x?></Result>",
        b"<Result><Value>ACK</Value></Result>",
    ),
)
def test_parse_setting_response_rejects_non_simple_result(result_xml: bytes) -> None:
    frame = make_validated(result_xml + b"<Reason>Setting</Reason>")

    assert parse_setting_response(frame, direction=FrameDirection.BOX_TO_PROXY) is None


def test_parse_setting_response_treats_cdata_as_simple_xml_text() -> None:
    frame = make_validated(
        b"<Result><![CDATA[ACK]]></Result><Reason><![CDATA[Setting]]></Reason>"
    )

    response = parse_setting_response(frame, direction=FrameDirection.BOX_TO_PROXY)

    assert response is not None
    assert response.result == "ACK"
    assert response.reason == "Setting"


def test_parse_setting_event_returns_strict_old_and_new_values(
    validated_event: ValidatedFrame,
) -> None:
    event = parse_setting_event(validated_event, direction=FrameDirection.BOX_TO_PROXY)

    assert event is not None
    assert event == SettingEvent(
        evidence_id=derive_event_evidence_id(
            "box-7",
            42,
            "06.08.2026 10:12:01",
            "Remotely : tbl_box_prms / MODE: [1]->[2]",
        ),
        device_id="box-7",
        event_id_set=42,
        device_dt="06.08.2026 10:12:01",
        content_text="Remotely : tbl_box_prms / MODE: [1]->[2]",
        table_name="tbl_box_prms",
        item_name="MODE",
        old_value_text="1",
        new_value_text="2",
    )


def test_parse_setting_event_rejects_cloud_direction(
    validated_event: ValidatedFrame,
) -> None:
    assert (
        parse_setting_event(
            validated_event, direction=FrameDirection.CLOUD_TO_PROXY
        )
        is None
    )


@pytest.mark.parametrize(
    "content",
    (
        "prefix Remotely : tbl_box_prms / MODE: [1]->[2]",
        "Remotely : tbl_box_prms / MODE: [1]->[2] suffix",
        "Remotely : tbl_box_prms / MODE: [1]->[2]\ntrailer",
        "Remotely : tbl_box_prms / MODE: [1] => [2]",
        "Remotely : / MODE: [1]->[2]",
        "Remotely : tbl_box_prms / : [1]->[2]",
    ),
)
def test_parse_setting_event_requires_full_anchored_content(content: str) -> None:
    escaped = content.replace(">", "&gt;")
    frame = make_validated(
        (
            "<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
            "<ID_Set>42</ID_Set><DT>06.08.2026 10:12:01</DT>"
            f"<Type>Setting</Type><Content>{escaped}</Content>"
        ).encode("utf-8")
    )

    assert parse_setting_event(frame, direction=FrameDirection.BOX_TO_PROXY) is None


@pytest.mark.parametrize(
    "inner",
    (
        b"<TblName>tbl_events</TblName><ID_Set>42</ID_Set>"
        b"<DT>2026-08-06</DT><Type>Setting</Type>"
        b"<Content>Remotely: tbl/a:[1]-&gt;[2]</Content>",
        b"<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
        b"<DT>2026-08-06</DT><Type>Setting</Type>"
        b"<Content>Remotely: tbl/a:[1]-&gt;[2]</Content>",
        b"<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
        b"<ID_Set>42</ID_Set><Type>Setting</Type>"
        b"<Content>Remotely: tbl/a:[1]-&gt;[2]</Content>",
    ),
)
def test_parse_setting_event_rejects_missing_evidence_identity_fields(
    inner: bytes,
) -> None:
    frame = make_validated(inner)

    assert parse_setting_event(frame, direction=FrameDirection.BOX_TO_PROXY) is None


@pytest.mark.parametrize(
    "prefix",
    (
        b"<TblName>tbl_actual</TblName><Type>Setting</Type>",
        b"<TblName>tbl_events</TblName><Type>Factory</Type>",
        b"<Wrapper><TblName>tbl_events</TblName><Type>Setting</Type></Wrapper>",
    ),
)
def test_parse_setting_event_rejects_wrong_or_nested_event_routing(prefix: bytes) -> None:
    frame = make_validated(
        prefix
        + b"<ID_Device>box-7</ID_Device><ID_Set>42</ID_Set><DT>2026-08-06</DT>"
        + b"<Content>Remotely: tbl/a:[1]-&gt;[2]</Content>"
    )

    assert parse_setting_event(frame, direction=FrameDirection.BOX_TO_PROXY) is None


def test_parse_setting_event_rejects_duplicate_device_dt() -> None:
    frame = make_validated(
        b"<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
        b"<ID_Set>42</ID_Set><DT>first</DT><DT>second</DT>"
        b"<Type>Setting</Type>"
        b"<Content>Remotely: tbl/a:[1]-&gt;[2]</Content>"
    )

    assert parse_setting_event(frame, direction=FrameDirection.BOX_TO_PROXY) is None


@pytest.mark.parametrize(
    ("field", "field_xml"),
    (
        ("DT", b'<DT source="box">2026-08-06</DT>'),
        ("DT", b"<DT>2026-08-06<!--note--></DT>"),
        ("DT", b"<DT>2026-08-06<?notice x?></DT>"),
        ("DT", b"<DT><Value>2026-08-06</Value></DT>"),
        (
            "Content",
            b'<Content source="box">Remotely: tbl/a:[1]-&gt;[2]</Content>',
        ),
        (
            "Content",
            b"<Content>Remotely: tbl/a:[1]-&gt;[2]<!--note--></Content>",
        ),
        (
            "Content",
            b"<Content>Remotely: tbl/a:[1]-&gt;[2]<?notice x?></Content>",
        ),
        (
            "Content",
            b"<Content><Value>Remotely: tbl/a:[1]-&gt;[2]</Value></Content>",
        ),
    ),
)
def test_parse_setting_event_rejects_non_simple_evidence_field(
    field: str, field_xml: bytes
) -> None:
    dt = field_xml if field == "DT" else b"<DT>2026-08-06</DT>"
    content = (
        field_xml
        if field == "Content"
        else b"<Content>Remotely: tbl/a:[1]-&gt;[2]</Content>"
    )
    frame = make_validated(
        b"<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
        b"<ID_Set>42</ID_Set>"
        + dt
        + b"<Type>Setting</Type>"
        + content
    )

    assert parse_setting_event(frame, direction=FrameDirection.BOX_TO_PROXY) is None


def test_parse_setting_event_treats_cdata_as_simple_xml_text() -> None:
    frame = make_validated(
        b"<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
        b"<ID_Set>42</ID_Set><DT><![CDATA[2026-08-06]]></DT>"
        b"<Type>Setting</Type>"
        b"<Content><![CDATA[Remotely: tbl/a:[1]->[2]]]></Content>"
    )

    event = parse_setting_event(frame, direction=FrameDirection.BOX_TO_PROXY)

    assert event is not None
    assert event.device_dt == "2026-08-06"
    assert event.content_text == "Remotely: tbl/a:[1]->[2]"


def test_setting_event_content_parser_has_linear_inspection_bound() -> None:
    content = "Remotely:" + " " * 65_536 + "X"
    parser_type = ack_parser_module._SettingEventContentParser
    parser = parser_type(content)

    assert parser.parse() is None
    assert 0 < parser.inspected_character_count <= 4 * len(content)


def test_setting_event_content_parser_preserves_exact_capture_semantics() -> None:
    content = "Remotely :  tbl_box_prms  /  MODE  : [ old ]->[ new ]"
    parser_type = ack_parser_module._SettingEventContentParser
    parser = parser_type(content)

    assert parser.parse() == ("tbl_box_prms", "MODE", " old ", " new ")
    assert parser.inspected_character_count <= 4 * len(content)


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        ("Remotely\u2003:\t table:part / item/name : []->[]", ("table:part", "item/name", "", "")),
        ("Remotely: tbl/item: [[old]->[new\nvalue]", ("tbl", "item", "[old", "new\nvalue")),
    ),
)
def test_setting_event_content_parser_preserves_full_grammar_edges(
    content: str, expected: tuple[str, str, str, str]
) -> None:
    parser_type = ack_parser_module._SettingEventContentParser
    parser = parser_type(content)

    assert parser.parse() == expected
    assert parser.inspected_character_count <= 4 * len(content)


@pytest.mark.parametrize(
    "content",
    (
        "Remotely: tbl/item: [old",
        "Remotely: tbl/item: [old]->[new",
        "Remotely: tbl/item: [old] ->[new]",
        "Remotely: tbl/item: [old]->[new] trailing",
        "Remotely: tbl[bad/item: [old]->[new]",
    ),
)
def test_setting_event_content_parser_rejects_linear_near_misses(content: str) -> None:
    parser_type = ack_parser_module._SettingEventContentParser
    parser = parser_type(content)

    assert parser.parse() is None
    assert parser.inspected_character_count <= 4 * len(content)


def test_invalid_crc_cannot_produce_a_validated_ack_or_event() -> None:
    valid = make_validated(
        b"<Result>ACK</Result><Reason>Setting</Reason>"
        b"<TblName>tbl_events</TblName><ID_Device>box-7</ID_Device>"
        b"<ID_Set>42</ID_Set><DT>2026-08-06</DT><Type>Setting</Type>"
        b"<Content>Remotely: tbl/a:[1]-&gt;[2]</Content>"
    )
    damaged = AssembledFrame(valid.raw.replace(b"Setting", b"SettinX", 1), 1)

    validation = validate_frame(damaged)

    assert validation.validated is None
    assert validation.error is FrameValidationError.CRC_MISMATCH


def test_derive_event_evidence_id_changes_when_any_component_changes() -> None:
    baseline = ("box-7", 42, "2026-08-06 10:12:01", "Remotely: tbl/a:[1]->[2]")
    baseline_id = derive_event_evidence_id(*baseline)
    variants = (
        ("box-8", baseline[1], baseline[2], baseline[3]),
        (baseline[0], 43, baseline[2], baseline[3]),
        (baseline[0], baseline[1], "2026-08-06 10:12:02", baseline[3]),
        (baseline[0], baseline[1], baseline[2], "Remotely: tbl/a:[1]->[3]"),
    )

    assert len(baseline_id) == 64
    assert all(derive_event_evidence_id(*variant) != baseline_id for variant in variants)


def test_derive_event_evidence_id_uses_unambiguous_nul_delimiters() -> None:
    left = derive_event_evidence_id("ab", 1, "c", "d")
    right = derive_event_evidence_id("a", 1, "bc", "d")

    assert left != right
    expected = hashlib.sha256(b"ab\x001\x00c\x00d").hexdigest()
    assert left == expected


@pytest.mark.parametrize("field_index", (0, 2, 3))
def test_derive_event_evidence_id_rejects_nul_in_text_fields(field_index: int) -> None:
    fields: list[object] = ["box-7", 42, "2026-08-06", "content"]
    fields[field_index] = f"{fields[field_index]}\x00injected"

    with pytest.raises(ValueError, match="NUL"):
        derive_event_evidence_id(
            fields[0],  # type: ignore[arg-type]
            fields[1],  # type: ignore[arg-type]
            fields[2],  # type: ignore[arg-type]
            fields[3],  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "event_id_set",
    (True, 1.0, -1, 9_223_372_036_854_775_808),
)
def test_derive_event_evidence_id_rejects_non_contract_integer(
    event_id_set: object,
) -> None:
    with pytest.raises(ValueError, match="event_id_set"):
        derive_event_evidence_id(
            "box-7",
            event_id_set,  # type: ignore[arg-type]
            "2026-08-06",
            "content",
        )
