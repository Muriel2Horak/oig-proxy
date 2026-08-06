"""Testy pro protocol/parser.py (OIG Proxy v2)."""
# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
import pytest

from protocol.crc import crc16_modbus
from protocol.frame import ValidatedFrame
from protocol.parser import FrameMetadata, parse_frame_metadata, parse_xml_frame


def validated_frame(inner: bytes) -> ValidatedFrame:
    crc = crc16_modbus(inner)
    raw = b"<Frame>" + inner + f"<CRC>{crc:05d}</CRC>".encode("ascii") + b"</Frame>\r\n"
    return ValidatedFrame(
        raw=raw,
        received_at_ms=1,
        inner_without_crc=inner,
        transmitted_crc=crc,
        computed_crc=crc,
    )


def test_parse_tbl_actual_basic():
    xml = (
        "<TblName>tbl_actual</TblName>"
        "<ID_Device>123</ID_Device>"
        "<ENBL>1</ENBL>"
        "<VOLT>230</VOLT>"
    )
    result = parse_xml_frame(xml)
    assert result["_table"] == "tbl_actual"
    assert result["_device_id"] == "123"
    assert result["ENBL"] == 1
    assert result["VOLT"] == 230


def test_parse_float_conversion():
    xml = "<TblName>tbl_ac_out</TblName><ID_Device>1</ID_Device><P_AC>1.5</P_AC>"
    result = parse_xml_frame(xml)
    assert result["P_AC"] == 1.5
    assert isinstance(result["P_AC"], float)


def test_parse_int_conversion():
    xml = "<TblName>tbl_box</TblName><ID_Device>1</ID_Device><MODE>2</MODE>"
    result = parse_xml_frame(xml)
    assert result["MODE"] == 2
    assert isinstance(result["MODE"], int)


def test_parse_string_value():
    xml = "<TblName>tbl_actual</TblName><ID_Device>1</ID_Device><FW>1.0.0</FW>"
    result = parse_xml_frame(xml)
    assert result["FW"] == "1.0.0"


def test_parse_skip_fields_not_in_result():
    """Fieldy jako TblName, ID_Device, CRC, DT se nesmí objevit jako klíče."""
    xml = (
        "<TblName>tbl_actual</TblName>"
        "<ID_Device>1</ID_Device>"
        "<CRC>12345</CRC>"
        "<DT>2024-01-01 00:00:00</DT>"
        "<ID_Set>99</ID_Set>"
        "<ENBL>1</ENBL>"
    )
    result = parse_xml_frame(xml)
    assert "TblName" not in result
    assert "ID_Device" not in result
    assert "CRC" not in result
    assert "DT" not in result
    assert "ID_Set" not in result
    assert "ENBL" in result


def test_parse_dt_in_metadata():
    """DT je přítomný jako _dt v metadatech."""
    xml = "<TblName>tbl_actual</TblName><ID_Device>1</ID_Device><DT>2024-01-15</DT>"
    result = parse_xml_frame(xml)
    assert result["_dt"] == "2024-01-15"


def test_parse_subd_zero_passes():
    """SubD=0 je aktivní banka – musí projít."""
    xml = (
        "<TblName>tbl_batt_prms</TblName>"
        "<ID_Device>1</ID_Device>"
        "<ID_SubD>0</ID_SubD>"
        "<CAP>100</CAP>"
    )
    result = parse_xml_frame(xml)
    assert result != {}
    assert result.get("CAP") == 100


def test_parse_subd_nonzero_returns_empty():
    """SubD=1 nebo 2 jsou neaktivní banky – musí vrátit prázdný dict."""
    for subd in (1, 2):
        xml = (
            f"<TblName>tbl_batt_prms</TblName>"
            f"<ID_Device>1</ID_Device>"
            f"<ID_SubD>{subd}</ID_SubD>"
            f"<CAP>100</CAP>"
        )
        result = parse_xml_frame(xml)
        assert result == {}, f"SubD={subd} mělo vrátit prázdný dict"


def test_parse_empty_xml():
    result = parse_xml_frame("")
    assert isinstance(result, dict)


def test_parse_no_table_no_device():
    """XML bez TblName a ID_Device stále parsuje ostatní fieldy."""
    xml = "<ENBL>1</ENBL><VOLT>230</VOLT>"
    result = parse_xml_frame(xml)
    assert result.get("ENBL") == 1
    assert "_table" not in result
    assert "_device_id" not in result


def test_parse_frame_metadata_extracts_direct_routing_fields() -> None:
    frame = validated_frame(
        b"<Result>IsNewSet</Result>"
        b"<TblName>tbl_box_prms</TblName>"
        b"<ID_Device>box-7</ID_Device>"
        b"<Reason>Setting</Reason>"
        b"<ToDo>Set</ToDo>"
        b"<Rdt>06.08.2026 10:12:00</Rdt>"
        b"<ID>12</ID><ID_Set>34</ID_Set>"
        b"<TblItem>MODE</TblItem><NewValue>2</NewValue>"
        b"<Type>Setting</Type><Content>content</Content>"
    )

    metadata = parse_frame_metadata(frame)

    assert metadata == FrameMetadata(
        result="IsNewSet",
        table_name="tbl_box_prms",
        device_id="box-7",
        reason="Setting",
        todo="Set",
        rdt="06.08.2026 10:12:00",
        message_id=12,
        id_set=34,
        item_name="MODE",
        new_value="2",
        event_type="Setting",
        content="content",
    )
    assert metadata.is_isnewset is True


def test_parse_frame_metadata_ignores_nested_lookalike_routing_fields() -> None:
    frame = validated_frame(
        b"<Wrapper><Result>ACK</Result><ID_Set>9</ID_Set></Wrapper>"
        b"<TblName>tbl_events</TblName>"
    )

    metadata = parse_frame_metadata(frame)

    assert metadata is not None
    assert metadata.result is None
    assert metadata.id_set is None
    assert metadata.table_name == "tbl_events"


@pytest.mark.parametrize(
    ("tag", "value"),
    [
        ("Result", "ACK"),
        ("TblName", "tbl_events"),
        ("ID_Device", "box-7"),
        ("Reason", "Setting"),
        ("ToDo", "Set"),
        ("Rdt", "06.08.2026 10:12:00"),
        ("ID", "1"),
        ("ID_Set", "2"),
        ("TblItem", "MODE"),
        ("NewValue", "2"),
        ("Type", "Setting"),
        ("Content", "event"),
    ],
)
def test_parse_frame_metadata_rejects_every_duplicate_direct_routing_field(
    tag: str, value: str
) -> None:
    repeated = f"<{tag}>{value}</{tag}><{tag}>{value}</{tag}>".encode("utf-8")

    assert parse_frame_metadata(validated_frame(repeated)) is None


@pytest.mark.parametrize(
    "value",
    (
        "",
        "-1",
        "+1",
        " 1",
        "1 ",
        "1.0",
        "1_000",
        "9223372036854775808",
        "99999999999999999999999999999999999999999999999999",
    ),
)
@pytest.mark.parametrize("tag", ("ID", "ID_Set"))
def test_parse_frame_metadata_fails_closed_on_invalid_integer_fields(
    tag: str, value: str
) -> None:
    inner = f"<{tag}>{value}</{tag}>".encode("ascii")

    assert parse_frame_metadata(validated_frame(inner)) is None


def test_parse_frame_metadata_accepts_zero_and_signed_64_bit_maximum() -> None:
    metadata = parse_frame_metadata(
        validated_frame(b"<ID>0</ID><ID_Set>9223372036854775807</ID_Set>")
    )

    assert metadata is not None
    assert metadata.message_id == 0
    assert metadata.id_set == 9_223_372_036_854_775_807


def test_parse_frame_metadata_rejects_complex_direct_routing_value() -> None:
    frame = validated_frame(b"<Result><Value>ACK</Value></Result>")

    assert parse_frame_metadata(frame) is None
