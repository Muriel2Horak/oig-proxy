import importlib.util
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "addon" / "oig-proxy" / "local_oig_crc.py"
_SPEC = importlib.util.spec_from_file_location("local_oig_crc", _MODULE_PATH)
assert _SPEC and _SPEC.loader
local_oig_crc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(local_oig_crc)


def test_reflect_bits_handles_normal_and_zero_width() -> None:
    assert local_oig_crc._reflect_bits(0b10110000, 8) == 0b00001101
    assert local_oig_crc._reflect_bits(0xA5, 0) == 0


def test_crc16_table_modbus_is_cached_and_well_formed() -> None:
    local_oig_crc._crc16_table_modbus.cache_clear()

    first = local_oig_crc._crc16_table_modbus()
    second = local_oig_crc._crc16_table_modbus()
    info = local_oig_crc._crc16_table_modbus.cache_info()

    assert first is second
    assert len(first) == 256
    assert all(isinstance(v, int) and 0 <= v <= 0xFFFF for v in first)
    assert info.hits >= 1
    assert info.misses == 1


def test_crc16_modbus_known_vectors() -> None:
    assert local_oig_crc.crc16_modbus(b"123456789") == 0x4B37
    assert local_oig_crc.crc16_modbus(b"") == 0xFFFF


def test_frame_inner_bytes_extracts_and_falls_back() -> None:
    framed = b"<Frame><A>1</A></Frame>\r\n"
    unframed = b"<A>1</A>"

    assert local_oig_crc.frame_inner_bytes(framed) == b"<A>1</A>"
    assert local_oig_crc.frame_inner_bytes(unframed) == unframed


def test_compute_frame_crc_strips_crc_tag_inside_frame() -> None:
    raw = b"<Frame><Result>ACK</Result><CRC>99999</CRC></Frame>\r\n"
    expected = local_oig_crc.crc16_modbus(b"<Result>ACK</Result>")

    assert local_oig_crc.compute_frame_crc(raw) == expected


def test_compute_frame_crc_without_frame_wrapper_still_strips_crc() -> None:
    raw = b"<Result>END</Result><CRC>00001</CRC>"
    expected = local_oig_crc.crc16_modbus(b"<Result>END</Result>")

    assert local_oig_crc.compute_frame_crc(raw) == expected


def test_build_frame_replaces_existing_crc_and_adds_crlf_by_default() -> None:
    frame = local_oig_crc.build_frame("<Result>ACK</Result><CRC>54321</CRC>")

    assert frame.startswith("<Frame><Result>ACK</Result><CRC>")
    assert frame.endswith("</Frame>\r\n")
    assert "<CRC>54321</CRC>" not in frame

    crc_value = local_oig_crc.compute_frame_crc(frame.encode("utf-8"))
    assert f"<CRC>{crc_value:05d}</CRC>" in frame


def test_build_frame_without_crlf_and_with_non_ascii() -> None:
    frame = local_oig_crc.build_frame("<Text>Čau</Text>", add_crlf=False)

    assert frame == "<Frame><Text>Čau</Text><CRC>{:05d}</CRC></Frame>".format(
        local_oig_crc.crc16_modbus("<Text>Čau</Text>".encode("utf-8"))
    )


def test_build_frame_raises_on_invalid_unicode_surrogate() -> None:
    with pytest.raises(UnicodeEncodeError):
        local_oig_crc.build_frame("\ud800")
