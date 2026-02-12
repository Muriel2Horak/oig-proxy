"""Tests for new methods added in telemetry/sonar PR."""

import asyncio
import threading
import time

# pylint: disable=protected-access
import proxy as proxy_module
from models import ProxyMode


def make_proxy(tmp_path):
    """Create minimal proxy object for testing."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy._last_data_epoch = time.time()
    proxy.box_connected = True
    proxy._loop = None
    return proxy


def test_validate_event_loop_ready(tmp_path):
    """Test _validate_event_loop_ready method."""
    proxy = make_proxy(tmp_path)
    proxy._loop = None

    # Without loop
    assert proxy._validate_event_loop_ready() is False

    # With running loop
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    try:
        proxy._loop = loop
        assert proxy._validate_event_loop_ready() is True
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()
        proxy._loop = None


def test_validate_control_parameters(tmp_path):
    """Test _validate_control_parameters method."""
    proxy = make_proxy(tmp_path)
    proxy._last_data_epoch = time.time()

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
    proxy._last_data_epoch = time.time()
    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")
    assert result["ok"] is False
    assert result["error"] == "device_id_unknown"


def test_build_control_frame(tmp_path):
    """Test _build_control_frame method."""
    proxy = make_proxy(tmp_path)

    frame = proxy._build_control_frame("tbl_box_prms", "SA", "1", "New")
    assert isinstance(frame, bytes)
    assert frame != b""
