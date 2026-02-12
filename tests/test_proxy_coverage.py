"""Tests for critical paths in proxy.py - simple, focused on coverage."""

import asyncio
import time
import pytest

# pylint: disable=protected-access
import proxy as proxy_module
import models # from models import ProxyMode


def make_proxy(tmp_path):
    """Create minimal proxy object for testing."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy._last_data_epoch = time.time()
    proxy.box_connected = True
    return proxy


def test_get_current_timestamp_format(tmp_path):
    """Test _get_current_timestamp returns valid ISO format."""
    timestamp = proxy_module.OIGProxy._get_current_timestamp()

    # Should be in ISO 8601 format
    assert isinstance(timestamp, str)
    assert len(timestamp) > 0
    # Format should be YYYY-MM-DDTHH:MM:SS.ssssssZ
    assert "T" in timestamp
    assert timestamp.endswith("Z")


def test_get_current_timestamp_unique(tmp_path):
    """Test _get_current_timestamp returns different values."""
    ts1 = proxy_module.OIGProxy._get_current_timestamp()

    import time
    time.sleep(0.001)

    ts2 = proxy_module.OIGProxy._get_current_timestamp()

    # Timestamps should be slightly different (within 1ms)
    assert ts1 != ts2


def test_constants_defined(tmp_path):
    """Test all string constants are properly defined."""
    assert hasattr(proxy_module.OIGProxy, "_RESULT_ACK")
    assert hasattr(proxy_module.OIGProxy, "_RESULT_END")
    assert hasattr(proxy_module.OIGProxy, "_TIME_OFFSET")
    assert hasattr(proxy_module.OIGProxy, "_POST_DRAIN_SA_KEY")

    assert proxy_module.OIGProxy._RESULT_ACK == "<Result>ACK</Result>"
    assert proxy_module.OIGProxy._RESULT_END == "<Result>END</Result>"
    assert proxy_module.OIGProxy._TIME_OFFSET == "+00:00"
    assert proxy_module.OIGProxy._POST_DRAIN_SA_KEY == "post_drain_sa_refresh"


def test_build_control_frame_valid(tmp_path):
    """Test _build_control_frame generates valid frame."""
    proxy = make_proxy(tmp_path)

    frame = proxy._build_control_frame("tbl_box_prms", "SA", "1", "New")

    assert isinstance(frame, bytes)
    assert len(frame) > 0
    assert b"<ID>" in frame
    assert b"<ID_Device>DEV1</ID_Device>" in frame
    assert b"<TblName>tbl_box_prms</TblName>" in frame
    assert b"<TblItem>SA</TblItem>" in frame
    assert b"<NewValue>1</NewValue>" in frame
    assert b"<Confirm>New</Confirm>" in frame


def test_build_control_frame_different_values(tmp_path):
    """Test _build_control_frame with different parameter values."""
    proxy = make_proxy(tmp_path)

    frame = proxy._build_control_frame("tbl_box_prms", "SB", "0", "Saved")

    assert isinstance(frame, bytes)
    assert b"<TblItem>SB</TblItem>" in frame
    assert b"<NewValue>0</NewValue>" in frame
    assert b"<Confirm>Saved</Confirm>" in frame


def test_validate_control_parameters_valid(tmp_path):
    """Test _validate_control_parameters with valid parameters."""
    proxy = make_proxy(tmp_path)

    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is True


def test_validate_control_parameters_box_not_connected(tmp_path):
    """Test _validate_control_parameters when box not connected."""
    proxy = make_proxy(tmp_path)
    proxy.box_connected = False

    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is False
    assert result["error"] == "box_not_connected"


def test_validate_event_loop_ready(tmp_path):
    """Test _validate_event_loop_ready method."""
    proxy = make_proxy(tmp_path)

    # Without loop
    assert proxy._validate_event_loop_ready() is False

    # With loop
    proxy._loop = asyncio.new_event_loop()
    assert proxy._validate_event_loop_ready() is True
