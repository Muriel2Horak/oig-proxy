"""Tests for window_metrics tracking in TelemetryClient."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "addon" / "oig-proxy"))

import pytest

from telemetry_client import TelemetryClient, WindowMetricsTracker


def test_window_metrics_tracker_add_log():
    """Test adding logs to WindowMetricsTracker."""
    tracker = WindowMetricsTracker()
    
    tracker.add_log("WARNING", "oig.proxy", "Test warning message")
    tracker.add_log("ERROR", "oig.mqtt", "Test error message")
    
    metrics = tracker.get_window_metrics()
    
    assert len(metrics["logs"]) == 2
    assert metrics["logs"][0]["level"] == "WARNING"
    assert metrics["logs"][0]["source"] == "oig.proxy"
    assert metrics["logs"][0]["message"] == "Test warning message"
    assert metrics["logs"][1]["level"] == "ERROR"
    assert metrics["logs"][1]["source"] == "oig.mqtt"


def test_window_metrics_tracker_add_event():
    """Test adding events to WindowMetricsTracker."""
    tracker = WindowMetricsTracker()
    
    tracker.add_event("mode_change", "online -> hybrid")
    tracker.add_event("box_connect", "")
    
    metrics = tracker.get_window_metrics()
    
    assert len(metrics["tbl_events"]) == 2
    assert metrics["tbl_events"][0]["event"] == "mode_change"
    assert metrics["tbl_events"][0]["details"] == "online -> hybrid"


def test_window_metrics_tracker_add_state_change():
    """Test adding state changes to WindowMetricsTracker."""
    tracker = WindowMetricsTracker()
    
    tracker.add_state_change("box", "disconnected", "connected")
    tracker.add_state_change("cloud", "connected", "disconnected")
    
    metrics = tracker.get_window_metrics()
    
    assert len(metrics["state_changes"]) == 2
    assert metrics["state_changes"][0]["field"] == "box"
    assert metrics["state_changes"][0]["old"] == "disconnected"
    assert metrics["state_changes"][0]["new"] == "connected"


def test_window_metrics_tracker_clear_after_get():
    """Test that get_window_metrics clears the buffer."""
    tracker = WindowMetricsTracker()
    
    tracker.add_log("INFO", "test", "message")
    tracker.add_event("test_event", "details")
    tracker.add_state_change("test_field", "old", "new")
    
    # First call should return data
    metrics1 = tracker.get_window_metrics()
    assert len(metrics1["logs"]) == 1
    assert len(metrics1["tbl_events"]) == 1
    assert len(metrics1["state_changes"]) == 1
    
    # Second call should return empty
    metrics2 = tracker.get_window_metrics()
    assert len(metrics2["logs"]) == 0
    assert len(metrics2["tbl_events"]) == 0
    assert len(metrics2["state_changes"]) == 0


def test_window_metrics_tracker_maxlen():
    """Test that deque maxlen limits buffer size."""
    tracker = WindowMetricsTracker()
    
    # Add more than WINDOW_METRICS_MAX_LOGS (50)
    for i in range(60):
        tracker.add_log("INFO", "test", f"message {i}")
    
    metrics = tracker.get_window_metrics()
    # Should only keep last 50
    assert len(metrics["logs"]) == 50
    assert metrics["logs"][0]["message"] == "message 10"
    assert metrics["logs"][-1]["message"] == "message 59"


def test_telemetry_client_track_event():
    """Test TelemetryClient.track_event() method."""
    client = TelemetryClient("test_device", "1.5.0")
    
    client.track_event("test_event", "test details")
    
    # Get window metrics from internal tracker
    metrics = client._window_metrics.get_window_metrics()
    assert len(metrics["tbl_events"]) == 1
    assert metrics["tbl_events"][0]["event"] == "test_event"
    assert metrics["tbl_events"][0]["details"] == "test details"


def test_telemetry_client_track_state_change():
    """Test TelemetryClient.track_state_change() method."""
    client = TelemetryClient("test_device", "1.5.0")
    
    client.track_state_change("box", "disconnected", "connected")
    
    # Get window metrics from internal tracker
    metrics = client._window_metrics.get_window_metrics()
    assert len(metrics["state_changes"]) == 1
    assert metrics["state_changes"][0]["field"] == "box"
    assert metrics["state_changes"][0]["old"] == "disconnected"
    assert metrics["state_changes"][0]["new"] == "connected"


def test_telemetry_client_disabled_no_tracking():
    """Test that tracking is skipped when telemetry is disabled."""
    # Create client with empty device_id (disabled)
    client = TelemetryClient("", "1.5.0")
    
    # These should not crash, just do nothing
    client.track_event("test_event", "details")
    client.track_state_change("field", "old", "new")
    
    # Should still have empty metrics
    metrics = client._window_metrics.get_window_metrics()
    assert len(metrics["tbl_events"]) == 0
    assert len(metrics["state_changes"]) == 0
