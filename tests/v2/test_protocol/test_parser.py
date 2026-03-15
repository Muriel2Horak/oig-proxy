"""Testy pro protocol/parser.py (OIG Proxy v2)."""
from protocol.parser import parse_xml_frame


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
