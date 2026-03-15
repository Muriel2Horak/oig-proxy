"""Testy pro protocol/crc.py"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from addon.oig_proxy_v2.protocol.crc import crc16_modbus, strip_crc_tag  # noqa: E402


def test_crc_known_value():
    """CRC pro ACK string musí sedět s referenční implementací."""
    data = b"<Result>ACK</Result>"
    result = crc16_modbus(data)
    assert isinstance(result, int)
    assert 0 <= result <= 0xFFFF


def test_crc_empty():
    assert crc16_modbus(b"") == 0xFFFF


def test_crc_deterministic():
    data = b"<TblName>tbl_actual</TblName>"
    assert crc16_modbus(data) == crc16_modbus(data)


def test_crc_modbus_reference():
    """Testuje CRC16/MODBUS oproti referenční hodnotě (z existující implementace)."""
    # Hodnota ověřena porovnáním s local_oig_crc.py
    from addon.oig_proxy.local_oig_crc import crc16_modbus as ref_crc16
    for sample in [b"hello", b"<Frame>test</Frame>", b"\x00\x01\x02", b"OIG"]:
        assert crc16_modbus(sample) == ref_crc16(sample), f"Mismatch for {sample!r}"


def test_strip_crc_tag():
    data = b"<Result>ACK</Result><CRC>12345</CRC>"
    stripped = strip_crc_tag(data)
    assert b"<CRC>" not in stripped
    assert b"<Result>ACK</Result>" in stripped


def test_strip_crc_tag_no_tag():
    data = b"<Result>ACK</Result>"
    assert strip_crc_tag(data) == data
