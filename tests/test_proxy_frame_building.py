"""Tests for proxy frame building helpers."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import re

import proxy as proxy_module


def test_build_getactual_frame_contains_todo():
    frame = proxy_module.OIGProxy._build_getactual_frame()
    text = frame.decode("utf-8", errors="strict")
    assert "<ToDo>GetActual</ToDo>" in text
    assert "<Result>ACK</Result>" in text


def test_build_ack_only_frame_contains_ack():
    frame = proxy_module.OIGProxy._build_ack_only_frame()
    text = frame.decode("utf-8", errors="strict")
    assert "<Result>ACK</Result>" in text
    assert "<ToDo>" not in text


def test_build_offline_ack_frame_end_variants():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)

    end_frame = proxy._build_offline_ack_frame("END").decode("utf-8", errors="strict")
    assert "<Result>END</Result>" in end_frame
    assert "<Time>" in end_frame
    assert "<UTCTime>" in end_frame

    isnewset_frame = proxy._build_offline_ack_frame("IsNewSet").decode("utf-8", errors="strict")
    assert "<Result>END</Result>" in isnewset_frame
    assert "<Time>" in isnewset_frame


def test_build_offline_ack_frame_weather_and_fw():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)

    weather_frame = proxy._build_offline_ack_frame("IsNewWeather").decode(
        "utf-8", errors="strict"
    )
    assert "<Result>END</Result>" in weather_frame
    assert "<Time>" not in weather_frame

    fw_frame = proxy._build_offline_ack_frame("IsNewFW").decode(
        "utf-8", errors="strict"
    )
    assert "<Result>END</Result>" in fw_frame


def test_build_offline_ack_frame_default_ack():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)

    default_frame = proxy._build_offline_ack_frame("tbl_actual").decode(
        "utf-8", errors="strict"
    )
    assert "<Result>ACK</Result>" in default_frame


def test_build_end_time_frame_includes_time_tags():
    frame = proxy_module.OIGProxy._build_end_time_frame()
    text = frame.decode("utf-8", errors="strict")
    assert "<Result>END</Result>" in text
    assert re.search(r"<Time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}</Time>", text)
    assert re.search(r"<UTCTime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}</UTCTime>", text)
