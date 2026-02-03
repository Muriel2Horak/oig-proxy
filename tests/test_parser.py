# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import pytest

from parser import OIGDataParser


def test_parse_xml_frame_basic():
    frame = (
        "<Frame>"
        "<TblName>tbl_box</TblName>"
        "<ID_Device>123</ID_Device>"
        "<DT>2025-01-01 00:00:00</DT>"
        "<Foo>1</Foo>"
        "<Bar>2.5</Bar>"
        "<Baz>text</Baz>"
        "</Frame>"
    )
    parsed = OIGDataParser.parse_xml_frame(frame)

    assert parsed["_table"] == "tbl_box"
    assert parsed["_device_id"] == "123"
    assert parsed["_dt"] == "2025-01-01 00:00:00"
    assert parsed["Foo"] == 1
    assert parsed["Bar"] == pytest.approx(2.5)
    assert parsed["Baz"] == "text"


def test_parse_xml_frame_ignores_subd():
    frame = (
        "<Frame>"
        "<TblName>tbl_batt_prms</TblName>"
        "<ID_SubD>1</ID_SubD>"
        "<Foo>1</Foo>"
        "</Frame>"
    )
    assert OIGDataParser.parse_xml_frame(frame) == {}


def test_parse_xml_frame_subd_zero_keeps_data():
    frame = (
        "<Frame>"
        "<TblName>tbl_batt_prms</TblName>"
        "<ID_SubD>0</ID_SubD>"
        "<Foo>1</Foo>"
        "</Frame>"
    )
    parsed = OIGDataParser.parse_xml_frame(frame)
    assert parsed["_table"] == "tbl_batt_prms"
    assert parsed["Foo"] == 1


def test_parse_xml_frame_missing_table():
    frame = "<Frame><ID_Device>123</ID_Device><Foo>5</Foo></Frame>"
    parsed = OIGDataParser.parse_xml_frame(frame)
    assert "_table" not in parsed
    assert parsed["_device_id"] == "123"
    assert parsed["Foo"] == 5


def test_parse_mode_from_event():
    content = "Remotely : tbl_box_prms / MODE: [0]->[3]"
    assert OIGDataParser.parse_mode_from_event(content) == 3
    assert OIGDataParser.parse_mode_from_event("no match") is None
