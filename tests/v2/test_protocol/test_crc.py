"""Testy pro protocol/crc.py (OIG Proxy v2)."""
# sys.path je nastaven v conftest.py – importujeme přímo z v2 addon
from protocol.crc import crc16_modbus, strip_crc_tag
from tests.v2.local_oig_crc import crc16_modbus as ref_crc16  # z v1 (cross-reference)


def test_crc_known_range():
    """CRC musí být 16-bitový int."""
    result = crc16_modbus(b"<Result>ACK</Result>")
    assert isinstance(result, int)
    assert 0 <= result <= 0xFFFF


def test_crc_empty():
    """Prázdný input = 0xFFFF (init hodnota)."""
    assert crc16_modbus(b"") == 0xFFFF


def test_crc_deterministic():
    """Stejný input vždy stejný výsledek."""
    data = b"<TblName>tbl_actual</TblName>"
    assert crc16_modbus(data) == crc16_modbus(data)


def test_crc_matches_v1_reference():
    """v2 CRC musí být identické s v1 referenční implementací."""
    samples = [
        b"hello",
        b"<Frame>test</Frame>",
        b"\x00\x01\x02",
        b"OIG",
        b"<Result>ACK</Result><ToDo>GetActual</ToDo>",
    ]
    for sample in samples:
        assert crc16_modbus(sample) == ref_crc16(sample), (
            f"CRC mismatch for {sample!r}"
        )


def test_strip_crc_tag_removes_tag():
    data = b"<Result>ACK</Result><CRC>12345</CRC>"
    stripped = strip_crc_tag(data)
    assert b"<CRC>" not in stripped
    assert b"<Result>ACK</Result>" in stripped


def test_strip_crc_tag_no_tag_unchanged():
    data = b"<Result>ACK</Result>"
    assert strip_crc_tag(data) == data


def test_strip_crc_tag_five_digit():
    """CRC tag musí mít přesně 5 číslic (OIG protokol)."""
    data = b"something<CRC>00001</CRC>rest"
    stripped = strip_crc_tag(data)
    assert stripped == b"somethingrest"
