"""Tests for frame building helpers in oig_frame module."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import re

import oig_frame


def test_build_getactual_frame_contains_todo():
    frame = oig_frame.build_getactual_frame()
    text = frame.decode("utf-8", errors="strict")
    assert "<ToDo>GetActual</ToDo>" in text
    assert "<Result>ACK</Result>" in text


def test_build_ack_only_frame_contains_ack():
    frame = oig_frame.build_ack_only_frame()
    text = frame.decode("utf-8", errors="strict")
    assert "<Result>ACK</Result>" in text
    assert "<ToDo>" not in text


def test_build_offline_ack_frame_end_variants():
    end_frame = oig_frame.build_offline_ack_frame("END").decode("utf-8", errors="strict")
    assert "<Result>END</Result>" in end_frame
    assert "<Time>" in end_frame
    assert "<UTCTime>" in end_frame

    isnewset_frame = oig_frame.build_offline_ack_frame("IsNewSet").decode("utf-8", errors="strict")
    assert "<Result>END</Result>" in isnewset_frame
    assert "<Time>" in isnewset_frame


def test_build_offline_ack_frame_weather_and_fw():
    weather_frame = oig_frame.build_offline_ack_frame("IsNewWeather").decode(
        "utf-8", errors="strict"
    )
    assert "<Result>END</Result>" in weather_frame
    assert "<Time>" not in weather_frame

    fw_frame = oig_frame.build_offline_ack_frame("IsNewFW").decode(
        "utf-8", errors="strict"
    )
    assert "<Result>END</Result>" in fw_frame


def test_build_offline_ack_frame_default_ack():
    default_frame = oig_frame.build_offline_ack_frame("tbl_actual").decode(
        "utf-8", errors="strict"
    )
    assert "<Result>ACK</Result>" in default_frame


def test_build_end_time_frame_includes_time_tags():
    frame = oig_frame.build_end_time_frame()
    text = frame.decode("utf-8", errors="strict")
    assert "<Result>END</Result>" in text
    assert re.search(r"<Time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}</Time>", text)
    assert re.search(r"<UTCTime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}</UTCTime>", text)
