"""Tests for new methods added in telemetry/sonar PR."""

import asyncio
import time

# pylint: disable=protected-access
import proxy as proxy_module
from models import ProxyMode


def test_validate_event_loop_ready(tmp_path):
    """Test _validate_event_loop_ready method."""
    proxy = proxy_module.OIGProxy("DEV1")
    proxy._loop = None

    # Without loop
    assert proxy._validate_event_loop_ready() is False

    # With loop
    proxy._loop = asyncio.new_event_loop()
    assert proxy._validate_event_loop_ready() is True


def test_validate_control_parameters(tmp_path):
    """Test _validate_control_parameters method."""
    proxy = proxy_module.OIGProxy("DEV1")
    proxy._last_data_epoch = time.time()
    proxy.box_connected = True

    # Valid case
    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")
    assert result["ok"] is True

    # Not connected
    proxy.box_connected = False
    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")
    assert result["ok"] is False
    assert result["error"] == "box_not_connected"

    # Device ID AUTO
    proxy.box_connected = True
    proxy.device_id = "AUTO"
    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")
    assert result["ok"] is False
    assert result["error"] == "device_id_unknown"


def test_build_control_frame(tmp_path):
    """Test _build_control_frame method."""
    proxy = proxy_module.OIGProxy("DEV1")

    frame = proxy._build_control_frame("tbl_box_prms", "SA", "1", "New")
    assert isinstance(frame, bytes)
    assert frame != b""
