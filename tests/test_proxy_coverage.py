"""Tests for critical paths in proxy.py - simple, focused on coverage."""

import asyncio
import time
import pytest
from unittest.mock import MagicMock

# pylint: disable=protected-access
import proxy as proxy_module
from models import ProxyMode


class MockMQTTPublisher:
    def __init__(self):
        self.device_id = None
        self._cache = {}
    
    def set_cached_payload(self, topic, payload):
        self._cache[topic] = payload
    
    def get_cached_payload(self, topic):
        return self._cache.get(topic)
    
    def state_topic(self, device_id, table_name):
        return f"oig_local/{device_id}/{table_name}/state"


def make_proxy(tmp_path):
    """Create minimal proxy object for testing."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy._last_data_epoch = time.time()
    proxy.box_connected = True
    proxy._loop = None
    proxy._telemetry_box_seen_in_window = False
    proxy._telemetry_cloud_ok_in_window = False
    proxy._telemetry_cloud_failed_in_window = False
    proxy._telemetry_cloud_eof_short_in_window = False
    proxy.cloud_session_connected = False
    proxy._telemetry_debug_windows_remaining = 0
    proxy.mqtt_publisher = MockMQTTPublisher()
    proxy._last_values = {}
    proxy._control_key_state = {}
    proxy._update_cached_value = lambda **kwargs: None
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


def test_get_box_connected_window_status(tmp_path, monkeypatch):
    """Test _get_box_connected_window_status returns correct status."""
    proxy = make_proxy(tmp_path)
    proxy._telemetry_box_seen_in_window = False
    
    # Box connected
    proxy.box_connected = True
    assert proxy._get_box_connected_window_status() is True
    assert proxy._telemetry_box_seen_in_window is False
    
    # Box not connected but seen in window
    proxy.box_connected = False
    proxy._telemetry_box_seen_in_window = True
    assert proxy._get_box_connected_window_status() is True


def test_should_include_telemetry_logs(tmp_path):
    """Test _should_include_telemetry_logs returns correct decision."""
    proxy = make_proxy(tmp_path)
    
    # Debug active - always include
    proxy._telemetry_debug_windows_remaining = 1
    assert proxy._should_include_telemetry_logs(True, True) is True
    
    # Box not connected - include logs
    assert proxy._should_include_telemetry_logs(False, False) is True
    
    # Debug inactive and box connected - don't include
    proxy._telemetry_debug_windows_remaining = 0
    assert proxy._should_include_telemetry_logs(False, True) is False


def test_get_cloud_online_window_status(tmp_path):
    """Test _get_cloud_online_window_status returns correct status."""
    proxy = make_proxy(tmp_path)
    
    # Cloud OK in window
    proxy._telemetry_cloud_ok_in_window = True
    assert proxy._get_cloud_online_window_status() is True
    assert proxy._telemetry_cloud_ok_in_window is False
    
    # Cloud failed in window
    proxy._telemetry_cloud_failed_in_window = True
    assert proxy._get_cloud_online_window_status() is False
    assert proxy._telemetry_cloud_failed_in_window is False
    
    # Cloud connected but no OK or failure
    proxy.cloud_session_connected = True
    assert proxy._get_cloud_online_window_status() is True
    
    # Cloud not connected
    proxy.cloud_session_connected = False
    assert proxy._get_cloud_online_window_status() is False


def test_validate_mqtt_state_device(tmp_path, monkeypatch):
    """Test _validate_mqtt_state_device returns correct result."""
    proxy = make_proxy(tmp_path)
    
    # Matching device ID
    proxy.mqtt_publisher.device_id = "DEV1"
    assert proxy._validate_mqtt_state_device("DEV1") is True
    
    # Mismatched device ID
    assert proxy._validate_mqtt_state_device("DEV2") is False
    
    # AUTO device ID
    proxy.mqtt_publisher.device_id = "AUTO"
    assert proxy._validate_mqtt_state_device("DEV1") is False


def test_parse_mqtt_state_payload_valid(tmp_path):
    """Test _parse_mqtt_state_payload with valid JSON."""
    proxy = make_proxy(tmp_path)
    
    payload = proxy._parse_mqtt_state_payload('{"key": "value"}')
    assert payload is not None
    assert payload["key"] == "value"


def test_parse_mqtt_state_payload_invalid(tmp_path):
    """Test _parse_mqtt_state_payload with invalid JSON."""
    proxy = make_proxy(tmp_path)
    
    payload = proxy._parse_mqtt_state_payload('not valid json')
    assert payload is None
    
    payload = proxy._parse_mqtt_state_payload('["array", "not", "dict"]')
    assert payload is None


def test_build_device_specific_metrics(tmp_path):
    """Test _build_device_specific_metrics returns expected structure."""
    proxy = make_proxy(tmp_path)
    
    metrics = proxy._build_device_specific_metrics("DEV1")
    
    assert "isnewfw_fw" in metrics
    assert "isnewset_lat" in metrics
    assert "tbl_box_tmlastcall" in metrics
    assert "isnewweather_loadedon" in metrics
    assert "tbl_box_strnght" in metrics
    assert "tbl_invertor_prms_model" in metrics
