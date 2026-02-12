"""Tests for critical paths in proxy.py."""

import asyncio
import time
import pytest

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
    return proxy


@pytest.mark.skip("requires full proxy initialization for telemetry")
def test_load_version_from_config_fallback(tmp_path):
    """Test _load_version_from_config fallback to default."""
    proxy = make_proxy(tmp_path)
    proxy.device_id = "DEV2"
    proxy._last_data_epoch = time.time()
    proxy._init_telemetry()

    # Should return default version when config not found
    assert proxy._telemetry_client is not None


def test_validate_control_parameters_device_id_auto(tmp_path):
    """Test _validate_control_parameters with AUTO device ID."""
    proxy = make_proxy(tmp_path)

    proxy.device_id = "AUTO"
    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is False
    assert result["error"] == "device_id_unknown"


def test_validate_control_parameters_valid(tmp_path):
    """Test _validate_control_parameters with valid parameters."""
    proxy = make_proxy(tmp_path)

    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is True


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
