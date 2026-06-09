"""
Testy pro proxy/local_ack.py a protocol/frames.py — local ACK frame builders.
"""
from __future__ import annotations

import re


from proxy.local_ack import build_local_ack
from protocol.frames import (
    build_ack_only_frame,
    build_getactual_frame,
    build_end_time_frame,
)


# -----------------------------------------------------------------------------
# Frame builders (protocol/frames.py)
# -----------------------------------------------------------------------------

def test_build_ack_only_frame():
    """build_ack_only_frame() vrací ACK frame."""
    frame = build_ack_only_frame()
    assert b"<Result>ACK</Result>" in frame
    assert b"<CRC>" in frame
    assert b"</Frame>" in frame


def test_build_getactual_frame():
    """build_getactual_frame() vrací ACK frame s GetActual."""
    frame = build_getactual_frame()
    assert b"<Result>ACK</Result>" in frame
    assert b"<ToDo>GetActual</ToDo>" in frame
    assert b"<CRC>" in frame


def test_build_end_time_frame():
    """build_end_time_frame() vrací END frame s časem."""
    frame = build_end_time_frame()
    assert b"<Result>END</Result>" in frame
    assert b"<Time>" in frame
    assert b"<UTCTime>" in frame
    assert b"<ToDo>GetActual</ToDo>" in frame
    assert b"<CRC>" in frame


def test_frames_have_valid_crc():
    """Všechny framy mají validní CRC."""
    from protocol.crc import crc16_modbus

    frames = [
        build_ack_only_frame(),
        build_getactual_frame(),
        build_end_time_frame(),
    ]

    for frame in frames:
        # Extract inner content between <Frame> and </Frame>
        match = re.search(rb"<Frame>(.*)</Frame>", frame, re.DOTALL)
        assert match is not None, f"Frame missing <Frame> tags: {frame}"
        inner = match.group(1)
        # Extract CRC value
        crc_match = re.search(rb"<CRC>(\d+)</CRC>", inner)
        assert crc_match is not None, f"Frame missing <CRC> tag: {inner}"
        expected_crc = int(crc_match.group(1))
        # Calculate CRC from content without CRC tag
        content_without_crc = re.sub(rb"<CRC>\d+</CRC>", b"", inner)
        calculated_crc = crc16_modbus(content_without_crc)
        assert calculated_crc == expected_crc, f"CRC mismatch: {calculated_crc} != {expected_crc}"


# -----------------------------------------------------------------------------
# build_local_ack (proxy/local_ack.py)
# -----------------------------------------------------------------------------

def test_build_local_ack_tbl_actual():
    """build_local_ack('tbl_actual') vrací ACK s GetActual."""
    frame = build_local_ack("tbl_actual")
    assert b"<Result>ACK</Result>" in frame
    assert b"<ToDo>GetActual</ToDo>" in frame


def test_build_local_ack_tbl_invertor():
    """build_local_ack('tbl_invertor') vrací prostý ACK."""
    frame = build_local_ack("tbl_invertor")
    assert b"<Result>ACK</Result>" in frame
    assert b"<ToDo>" not in frame


def test_build_local_ack_tbl_any():
    """build_local_ack('tbl_*') vrací ACK."""
    for table in ["tbl_batt", "tbl_grid", "tbl_panels", "tbl_events"]:
        frame = build_local_ack(table)
        assert b"<Result>ACK</Result>" in frame, f"Expected ACK for {table}"


def test_build_local_ack_isnewset():
    """build_local_ack('IsNewSet') vrací END + čas + GetActual (jako cloud)."""
    frame = build_local_ack("IsNewSet")
    assert b"<Result>END</Result>" in frame
    assert b"<ToDo>GetActual</ToDo>" in frame


def test_build_local_ack_isnewweather():
    """build_local_ack('IsNewWeather') vrací END + GetActual (jako cloud)."""
    frame = build_local_ack("IsNewWeather")
    assert b"<Result>END</Result>" in frame
    assert b"<ToDo>GetActual</ToDo>" in frame


def test_build_local_ack_isnewfw():
    """build_local_ack('IsNewFW') vrací END + GetActual (jako cloud)."""
    frame = build_local_ack("IsNewFW")
    assert b"<Result>END</Result>" in frame
    assert b"<ToDo>GetActual</ToDo>" in frame


def test_build_local_ack_end():
    """build_local_ack('END') vrací END s časem."""
    frame = build_local_ack("END")
    assert b"<Result>END</Result>" in frame
    assert b"<Time>" in frame
    assert b"<UTCTime>" in frame


def test_build_local_ack_unknown():
    """build_local_ack('unknown') vrací ACK jako fallback."""
    frame = build_local_ack("unknown")
    assert b"<Result>ACK</Result>" in frame


def test_build_local_ack_all_frames_have_crc():
    """Všechny build_local_ack framy mají CRC."""
    tables = [
        "tbl_actual",
        "tbl_invertor",
        "IsNewSet",
        "IsNewWeather",
        "IsNewFW",
        "END",
        "unknown",
    ]
    for table in tables:
        frame = build_local_ack(table)
        assert b"<CRC>" in frame, f"Missing CRC for {table}"
        assert b"</Frame>" in frame, f"Missing </Frame> for {table}"
