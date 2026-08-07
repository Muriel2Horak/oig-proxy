"""Tests for the deterministic Setting frame serializer."""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import importlib

import pytest


frames = importlib.import_module("protocol.frames")


class _HostileReplaceText(str):
    def replace(self, _old: str, _new: str, _count: int = -1) -> str:
        return "\ud800"


class _HostileWireInt(int):
    def __format__(self, _format_spec: str) -> str:
        return "1</ID><Injected>yes</Injected><ID>"

    def __str__(self) -> str:
        return "1</ID><Injected>yes</Injected><ID>"


def independent_modbus_crc(data: bytes) -> int:
    """Return CRC-16/MODBUS without using production CRC helpers."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _setting_frame(**overrides: object):
    values = {
        "device_id": "123456",
        "table_name": "tbl_box_prms",
        "item_name": "MODE",
        "value_text": "2",
        "wire_id": 14000001,
        "wire_id_set": 1786000000,
        "wire_dt": "06.08.2026 10:11:12",
        "tsec_text": "2026-08-06 08:11:13",
        "ver_text": "00042",
    }
    values.update(overrides)
    return frames.build_setting_frame(**values)


def test_build_setting_frame_matches_exact_golden_bytes() -> None:
    rendered = frames.build_setting_frame(
        device_id="123456",
        table_name="tbl_box_prms",
        item_name="MODE",
        value_text="2",
        wire_id=14000001,
        wire_id_set=1786000000,
        wire_dt="06.08.2026 10:11:12",
        tsec_text="2026-08-06 08:11:13",
        ver_text="00042",
    )
    expected = (
        b"<Frame><ID>14000001</ID><ID_Device>123456</ID_Device>"
        b"<ID_Set>1786000000</ID_Set><ID_SubD>0</ID_SubD>"
        b"<DT>06.08.2026 10:11:12</DT><NewValue>2</NewValue>"
        b"<Confirm>New</Confirm><TblName>tbl_box_prms</TblName>"
        b"<TblItem>MODE</TblItem><ID_Server>9</ID_Server>"
        b"<mytimediff>0</mytimediff><Reason>Setting</Reason>"
        b"<TSec>2026-08-06 08:11:13</TSec><ver>00042</ver>"
        b"<CRC>63234</CRC></Frame>\r\n"
    )

    assert rendered.wire_frame == expected
    inner = expected[len(b"<Frame>"): expected.index(b"<CRC>")]
    assert independent_modbus_crc(inner) == 63234
    assert rendered.crc_text == "63234"
    assert rendered.wire_length == len(expected)


def test_build_setting_frame_escapes_dynamic_text_once() -> None:
    rendered = frames.build_setting_frame(
        device_id="A&amp;B",
        table_name="tbl_box&prms",
        item_name='MO"DE',
        value_text="1 < 2 & 3 > 2's",
        wire_id=1,
        wire_id_set=2,
        wire_dt="06.08.2026 10:11:12",
        tsec_text="2026-08-06 08:11:13",
        ver_text="00001",
    )

    assert b"<ID_Device>A&amp;amp;B</ID_Device>" in rendered.wire_frame
    assert b"<TblName>tbl_box&amp;prms</TblName>" in rendered.wire_frame
    assert b"<TblItem>MO&quot;DE</TblItem>" in rendered.wire_frame
    assert b"<NewValue>1 &lt; 2 &amp; 3 &gt; 2&#x27;s</NewValue>" in rendered.wire_frame


def test_build_setting_frame_normalizes_str_subclass_before_escaping() -> None:
    try:
        rendered = _setting_frame(value_text=_HostileReplaceText("safe"))
    except UnicodeError as error:
        pytest.fail(f"raw codec error escaped: {type(error).__name__}")

    assert b"<NewValue>safe</NewValue>" in rendered.wire_frame


def test_build_setting_frame_normalizes_post_escape_encoding_failure(monkeypatch) -> None:
    monkeypatch.setattr(frames, "escape_xml_text", lambda _text: "\ud800")

    with pytest.raises(
        ValueError,
        match="dynamic Setting text is not valid XML 1.0",
    ):
        _setting_frame()


@pytest.mark.parametrize("bad_text", ["bad\x00value", "bad\x1fvalue", "bad\ud800value"])
def test_build_setting_frame_rejects_forbidden_xml_characters(bad_text: str) -> None:
    with pytest.raises(ValueError, match="dynamic Setting text is not valid XML 1.0"):
        _setting_frame(value_text=bad_text)


@pytest.mark.parametrize(
    "valid_text",
    ["tab\tline\nreturn\r", "\ud7ff\ue000", "\ufffd", "\U00010000", "\U0010ffff"],
)
def test_build_setting_frame_accepts_xml_character_boundaries(valid_text: str) -> None:
    assert _setting_frame(value_text=valid_text).wire_frame


@pytest.mark.parametrize("bad_text", ["\ufffe", "\uffff"])
def test_build_setting_frame_rejects_xml_noncharacters(bad_text: str) -> None:
    with pytest.raises(ValueError, match="dynamic Setting text is not valid XML 1.0"):
        _setting_frame(value_text=bad_text)


@pytest.mark.parametrize("ver_text", ["00000", "00001", "65535"])
def test_build_setting_frame_accepts_five_digit_uint16_versions(ver_text: str) -> None:
    assert _setting_frame(ver_text=ver_text).wire_frame


@pytest.mark.parametrize(
    "ver_text",
    [
        "1",
        "0001",
        "000001",
        "-0001",
        "12a34",
        "65536",
        "99999",
        "１２３４５",
        12345,
    ],
)
def test_build_setting_frame_rejects_invalid_versions(ver_text: object) -> None:
    with pytest.raises(
        ValueError,
        match="ver_text must be a zero-padded uint16 decimal",
    ):
        _setting_frame(ver_text=ver_text)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wire_id", True),
        ("wire_id", -1),
        ("wire_id", 1.0),
        ("wire_id_set", False),
        ("wire_id_set", -1),
        ("wire_id_set", "2"),
    ],
)
def test_build_setting_frame_rejects_invalid_wire_ids(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a non-negative integer"):
        _setting_frame(**{field: value})


@pytest.mark.parametrize("field", ["wire_id", "wire_id_set"])
def test_build_setting_frame_rejects_hostile_int_subclasses(field: str) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a non-negative integer"):
        _setting_frame(**{field: _HostileWireInt(1)})


def test_build_setting_frame_accepts_zero_and_large_integer_wire_ids() -> None:
    rendered = _setting_frame(wire_id=0, wire_id_set=10**30)

    assert b"<ID>0</ID>" in rendered.wire_frame
    assert b"<ID_Set>1000000000000000000000000000000</ID_Set>" in rendered.wire_frame


def test_xml_text_helpers_match_xml_1_0_and_escape_contract() -> None:
    assert frames.is_xml_1_0_text("\t\n\r \U00010000") is True
    assert frames.is_xml_1_0_text("\x00") is False
    assert frames.escape_xml_text('A&B<"\'') == "A&amp;B&lt;&quot;&#x27;"
