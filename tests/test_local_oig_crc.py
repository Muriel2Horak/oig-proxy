"""Tests for local_oig_crc module."""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=import-error,wrong-import-position,import-outside-toplevel

import sys
from pathlib import Path
import importlib.util

# Add addon to path
sys.path.insert(0, str(Path(__file__).parent.parent / "addon" / "oig-proxy"))

if importlib.util.find_spec("local_oig_crc") is None:
    import pytest

    pytest.skip(
        "local_oig_crc is not available in this environment",
        allow_module_level=True)

import local_oig_crc  # noqa: E402


def test_reflect_bits_8():
    assert local_oig_crc._reflect_bits(0b10000000, 8) == 0b00000001
    assert local_oig_crc._reflect_bits(0b00000001, 8) == 0b10000000
    assert local_oig_crc._reflect_bits(0b10101010, 8) == 0b01010101


def test_reflect_bits_16():
    assert local_oig_crc._reflect_bits(0x8000, 16) == 0x0001
    assert local_oig_crc._reflect_bits(0x0001, 16) == 0x8000


def test_reflect_bits_zero():
    assert local_oig_crc._reflect_bits(0, 8) == 0
    assert local_oig_crc._reflect_bits(0, 16) == 0


def test_crc16_table_modbus():
    table = local_oig_crc._crc16_table_modbus()
    assert isinstance(table, tuple)
    assert len(table) == 256
    assert all(0 <= x <= 0xFFFF for x in table)


def test_crc16_table_cached():
    table1 = local_oig_crc._crc16_table_modbus()
    table2 = local_oig_crc._crc16_table_modbus()
    assert table1 is table2


def test_crc16_modbus_empty():
    result = local_oig_crc.crc16_modbus(b"")
    assert result == 0xFFFF


def test_crc16_modbus_known_value():
    result = local_oig_crc.crc16_modbus(b"123456789")
    assert result == 0x4B37


def test_crc16_modbus_single_byte():
    result = local_oig_crc.crc16_modbus(b"\x00")
    assert 0 <= result <= 0xFFFF


def test_crc16_modbus_xml():
    data = b"<ID_Device>2206237016</ID_Device>"
    result = local_oig_crc.crc16_modbus(data)
    assert isinstance(result, int)


def test_frame_inner_bytes_complete():
    frame = b"<Frame><ID_Device>123</ID_Device></Frame>\r\n"
    result = local_oig_crc.frame_inner_bytes(frame)
    assert result == b"<ID_Device>123</ID_Device>"


def test_frame_inner_bytes_no_crlf():
    frame = b"<Frame><ID_Device>123</ID_Device></Frame>"
    result = local_oig_crc.frame_inner_bytes(frame)
    assert result == b"<ID_Device>123</ID_Device>"


def test_frame_inner_bytes_lf_only():
    frame = b"<Frame><ID_Device>123</ID_Device></Frame>\n"
    result = local_oig_crc.frame_inner_bytes(frame)
    assert result == b"<ID_Device>123</ID_Device>"


def test_frame_inner_bytes_multiline():
    frame = b"<Frame><A>test</A>\n<B>data</B></Frame>\r\n"
    result = local_oig_crc.frame_inner_bytes(frame)
    assert result == b"<A>test</A>\n<B>data</B>"


def test_frame_inner_bytes_fallback():
    data = b"<ID_Device>123</ID_Device>"
    result = local_oig_crc.frame_inner_bytes(data)
    assert result == data


def test_frame_inner_bytes_empty():
    frame = b"<Frame></Frame>"
    result = local_oig_crc.frame_inner_bytes(frame)
    assert result == b""


def test_compute_frame_crc_simple():
    frame = b"<Frame><ID_Device>123</ID_Device></Frame>\r\n"
    result = local_oig_crc.compute_frame_crc(frame)
    assert 0 <= result <= 0xFFFF


def test_compute_frame_crc_strips_existing():
    frame = b"<Frame><ID_Device>123</ID_Device><CRC>12345</CRC></Frame>\r\n"
    result = local_oig_crc.compute_frame_crc(frame)
    frame_without = b"<Frame><ID_Device>123</ID_Device></Frame>\r\n"
    expected = local_oig_crc.compute_frame_crc(frame_without)
    assert result == expected


def test_compute_frame_crc_no_wrapper():
    data = b"<ID_Device>123</ID_Device>"
    result = local_oig_crc.compute_frame_crc(data)
    assert isinstance(result, int)


def test_compute_frame_crc_consistent():
    frame = b"<Frame><Test>data</Test></Frame>"
    crc1 = local_oig_crc.compute_frame_crc(frame)
    crc2 = local_oig_crc.compute_frame_crc(frame)
    assert crc1 == crc2


def test_build_frame_simple():
    inner = "<ID_Device>123</ID_Device>"
    result = local_oig_crc.build_frame(inner)
    assert result.startswith("<Frame>")
    assert result.endswith("\r\n")
    assert "<CRC>" in result
    assert "<ID_Device>123</ID_Device>" in result


def test_build_frame_no_crlf():
    inner = "<ID_Device>123</ID_Device>"
    result = local_oig_crc.build_frame(inner, add_crlf=False)
    assert result.startswith("<Frame>")
    assert result.endswith("</Frame>")
    assert not result.endswith("\r\n")


def test_build_frame_crc_format():
    inner = "<Test>data</Test>"
    result = local_oig_crc.build_frame(inner)
    import re
    match = re.search(r"<CRC>(\d{5})</CRC>", result)
    assert match is not None
    assert len(match.group(1)) == 5


def test_build_frame_removes_existing_crc():
    inner = "<Test>data</Test><CRC>99999</CRC>"
    result = local_oig_crc.build_frame(inner)
    assert result.count("<CRC>") == 1
    assert result.count("</CRC>") == 1


def test_build_frame_multiline():
    inner = "<A>test</A>\n<B>data</B>"
    result = local_oig_crc.build_frame(inner)
    assert "<A>test</A>" in result
    assert "<B>data</B>" in result


def test_build_frame_empty():
    inner = ""
    result = local_oig_crc.build_frame(inner)
    assert result.startswith("<Frame>")
    assert "<CRC>" in result


def test_build_frame_round_trip():
    inner = "<ID_Device>2206237016</ID_Device>"
    frame_str = local_oig_crc.build_frame(inner)
    frame_bytes = frame_str.encode("utf-8")
    computed_crc = local_oig_crc.compute_frame_crc(frame_bytes)
    import re
    match = re.search(rb"<CRC>(\d+)</CRC>", frame_bytes)
    embedded_crc = int(match.group(1))
    assert computed_crc == embedded_crc


def test_build_frame_unicode():
    inner = "<Message>Teplota: 25°C</Message>"
    result = local_oig_crc.build_frame(inner)
    assert "Teplota: 25°C" in result
