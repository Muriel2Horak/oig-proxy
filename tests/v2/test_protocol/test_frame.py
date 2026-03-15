"""Testy pro protocol/frame.py (OIG Proxy v2)."""
from protocol.frame import (
    build_frame,
    parse_frame,
    extract_frame_from_buffer,
    infer_table_name,
    infer_device_id,
    build_ack_frame,
    build_getactual_frame,
    build_end_time_frame,
)
from protocol.crc import crc16_modbus, strip_crc_tag


def test_build_frame_contains_crc():
    """build_frame musí obsahovat CRC tag."""
    frame = build_frame("<Result>ACK</Result>")
    assert "<CRC>" in frame
    assert "</CRC>" in frame


def test_build_frame_wraps_in_frame_tags():
    """build_frame musí mít <Frame>...</Frame>."""
    frame = build_frame("<Result>ACK</Result>")
    assert frame.startswith("<Frame>")
    assert "</Frame>" in frame


def test_build_frame_crlf_default():
    """Výchozí build_frame přidává \\r\\n."""
    frame = build_frame("<Result>ACK</Result>")
    assert frame.endswith("\r\n")


def test_build_frame_no_crlf():
    frame = build_frame("<Result>ACK</Result>", add_crlf=False)
    assert not frame.endswith("\r\n")


def test_build_and_parse_roundtrip():
    """Round-trip: build_frame → parse_frame vrátí původní inner xml."""
    inner = "<Result>ACK</Result>"
    frame_str = build_frame(inner)
    frame_bytes = frame_str.encode("utf-8")
    parsed = parse_frame(frame_bytes)
    assert parsed is not None
    assert parsed.decode("utf-8") == inner


def test_parse_frame_invalid_returns_none():
    assert parse_frame(b"not a frame") is None


def test_extract_frame_from_buffer_basic():
    """extract_frame_from_buffer extrahuje frame a odstraní ho z bufferu."""
    frame_str = build_frame("<Result>ACK</Result>")
    buf = bytearray(frame_str.encode("utf-8"))
    frame = extract_frame_from_buffer(buf)
    assert frame is not None
    assert b"<Frame>" in frame
    assert len(buf) == 0  # Buffer byl konzumován


def test_extract_frame_from_buffer_partial():
    """Neúplný frame vrátí None."""
    buf = bytearray(b"<Frame><Result>ACK</Result>")
    assert extract_frame_from_buffer(buf) is None


def test_extract_frame_from_buffer_multiple():
    """Více framů v bufferu – extrahuje je postupně."""
    frame1 = build_frame("<Result>ACK</Result>").encode("utf-8")
    frame2 = build_frame("<TblName>tbl_actual</TblName>").encode("utf-8")
    buf = bytearray(frame1 + frame2)

    f1 = extract_frame_from_buffer(buf)
    assert f1 is not None
    assert b"ACK" in f1

    f2 = extract_frame_from_buffer(buf)
    assert f2 is not None
    assert b"tbl_actual" in f2

    assert len(buf) == 0


def test_infer_table_name_tblname():
    xml = "<TblName>tbl_actual</TblName><ID_Device>123</ID_Device>"
    assert infer_table_name(xml) == "tbl_actual"


def test_infer_table_name_result():
    xml = "<Result>ACK</Result>"
    assert infer_table_name(xml) == "ACK"


def test_infer_table_name_none():
    assert infer_table_name("<foo>bar</foo>") is None


def test_infer_device_id():
    xml = "<ID_Device>42</ID_Device>"
    assert infer_device_id(xml) == "42"


def test_infer_device_id_none():
    assert infer_device_id("<foo>bar</foo>") is None


def test_build_ack_frame():
    frame = build_ack_frame()
    assert isinstance(frame, bytes)
    assert b"ACK" in frame


def test_build_getactual_frame():
    frame = build_getactual_frame()
    assert b"GetActual" in frame


def test_build_end_time_frame():
    frame = build_end_time_frame()
    assert b"END" in frame
    assert b"Time>" in frame
