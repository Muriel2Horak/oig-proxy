"""Tests for critical paths in proxy.py - simple, focused on coverage."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import asyncio
import time
import pytest
from unittest.mock import MagicMock
import oig_frame
import proxy as proxy_module
from models import ProxyMode
from telemetry_collector import TelemetryCollector


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
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._last_data_epoch = time.time()
    proxy.box_connected = True
    proxy._loop = None
    proxy.cloud_session_connected = False
    proxy.mqtt_publisher = MockMQTTPublisher()
    proxy._last_values = {}
    proxy._control_key_state = {}
    proxy._update_cached_value = lambda **kwargs: None
    proxy._tc = MagicMock()
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
    # Constants are now in oig_frame module but still accessible via OIGProxy
    assert oig_frame.RESULT_ACK == "<Result>ACK</Result>"
    assert oig_frame.RESULT_END == "<Result>END</Result>"
    # Backward compatibility aliases on OIGProxy
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
    mock_proxy = MagicMock()
    mock_proxy.box_connected = True
    tc = TelemetryCollector(mock_proxy, interval_s=300)
    tc.box_seen_in_window = False

    # Box connected
    assert tc._get_box_connected_window_status() is True
    assert tc.box_seen_in_window is False

    # Box not connected but seen in window
    mock_proxy.box_connected = False
    tc.box_seen_in_window = True
    assert tc._get_box_connected_window_status() is True


def test_should_include_telemetry_logs(tmp_path):
    """Test _should_include_logs returns correct decision."""
    # Debug active - always include
    assert TelemetryCollector._should_include_logs(True, True) is True

    # Box not connected - include logs
    assert TelemetryCollector._should_include_logs(False, False) is True

    # Debug inactive and box connected - don't include
    assert TelemetryCollector._should_include_logs(False, True) is False


def test_get_cloud_online_window_status(tmp_path):
    """Test _get_cloud_online_window_status returns correct status."""
    mock_proxy = MagicMock()
    mock_proxy.cloud_session_connected = False
    tc = TelemetryCollector(mock_proxy, interval_s=300)

    # Cloud OK in window
    tc.cloud_ok_in_window = True
    assert tc._get_cloud_online_window_status() is True
    assert tc.cloud_ok_in_window is False

    # Cloud failed in window
    tc.cloud_failed_in_window = True
    assert tc._get_cloud_online_window_status() is False
    assert tc.cloud_failed_in_window is False

    # Cloud connected but no OK or failure
    mock_proxy.cloud_session_connected = True
    assert tc._get_cloud_online_window_status() is True

    # Cloud not connected
    mock_proxy.cloud_session_connected = False
    assert tc._get_cloud_online_window_status() is False


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
    mock_proxy_for_tc = MagicMock()
    mock_proxy_for_tc.mqtt_publisher = proxy.mqtt_publisher
    tc = TelemetryCollector(mock_proxy_for_tc, interval_s=300)

    metrics = tc._build_device_specific_metrics("DEV1")

    assert "isnewfw_fw" in metrics
    assert "isnewset_lat" in metrics
    assert "tbl_box_tmlastcall" in metrics
    assert "isnewweather_loadedon" in metrics
    assert "tbl_box_strnght" in metrics
    assert "tbl_invertor_prms_model" in metrics
