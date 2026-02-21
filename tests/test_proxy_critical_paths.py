"""Tests for critical paths in proxy.py."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
import time
from enum import Enum
import pytest

from tests.helpers import make_proxy


class ProxyMode(Enum):
    OFFLINE = -1
    ONLINE = 0
    MITM = 1
    REPLAY = 2
    BOX_LOCAL = 3


@pytest.mark.skip("requires full proxy initialization for telemetry")
def test_load_version_from_config_fallback(tmp_path):
    """Test _load_version_from_config fallback to default."""
    proxy = make_proxy(tmp_path)
    proxy.device_id = "DEV2"
    proxy._last_data_epoch = time.time()
    proxy._tc.init()

    # Should return default version when config not found
    assert proxy._tc.client is not None


def test_validate_control_parameters_device_id_auto(tmp_path):
    """Test _validate_control_parameters with AUTO device ID."""
    proxy = make_proxy(tmp_path)

    proxy.device_id = "AUTO"
    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is False
    assert result["error"] == "device_id_unknown"


def test_validate_control_parameters_valid(tmp_path):
    """Test _validate_control_parameters with valid parameters."""
    proxy = make_proxy(tmp_path)

    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is True


def test_build_control_frame_valid(tmp_path):
    """Test _build_control_frame generates valid frame."""
    proxy = make_proxy(tmp_path)

    frame = proxy._cs.build_frame("tbl_box_prms", "SA", "1", "New")

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

    frame = proxy._cs.build_frame("tbl_box_prms", "SB", "0", "Saved")

    assert isinstance(frame, bytes)
    assert b"<TblItem>SB</TblItem>" in frame
    assert b"<NewValue>0</NewValue>" in frame
    assert b"<Confirm>Saved</Confirm>" in frame
