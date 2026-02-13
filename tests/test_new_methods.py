"""Tests for new methods added in telemetry/sonar PR."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio

from tests.helpers import make_proxy


def test_validate_event_loop_ready(tmp_path):
    """Test _validate_event_loop_ready method."""
    proxy = make_proxy(tmp_path)
    proxy._loop = None

    # Without loop
    assert proxy._cs.validate_loop_ready() is False

    # With loop
    proxy._loop = asyncio.new_event_loop()
    assert proxy._cs.validate_loop_ready() is True


def test_validate_control_parameters(tmp_path):
    """Test _validate_control_parameters method."""
    proxy = make_proxy(tmp_path)

    # Valid case
    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "1")
    assert result["ok"] is True

    # Not connected
    proxy.box_connected = False
    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "1")
    assert result["ok"] is False
    assert result["error"] == "box_not_connected"

    # Device ID AUTO
    proxy.box_connected = True
    proxy.device_id = "AUTO"
    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "1")
    assert result["ok"] is False
    assert result["error"] == "device_id_unknown"


def test_build_control_frame(tmp_path):
    """Test _build_control_frame method."""
    proxy = make_proxy(tmp_path)

    frame = proxy._cs.build_frame("tbl_box_prms", "SA", "1", "New")
    assert isinstance(frame, bytes)
    assert frame != b""
