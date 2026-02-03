# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,line-too-long
import asyncio
from collections import deque
from datetime import datetime, timezone

import pytest

import proxy as proxy_module
from models import ProxyMode


class DummyMQTT:
    def __init__(self):
        self.device_id = "DEV1"
        self._ready = True
        self._queue = type("Q", (), {"size": lambda self: 5})()

    def is_ready(self):
        return self._ready

    @property
    def queue(self):
        return self._queue


def _make_telemetry_proxy(tmp_path):
    """Create a minimal proxy for telemetry testing."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy._configured_mode = "online"
    proxy._active_box_peer = "1.2.3.4:5678"
    proxy._hybrid_in_offline = False
    proxy._start_time = 1000.0
    proxy.mqtt_publisher = DummyMQTT()
    proxy.box_connected = False
    proxy.cloud_session_connected = False
    proxy.stats = {
        "frames_received": 100,
        "frames_forwarded": 95,
    }
    proxy.cloud_connects = 10
    proxy.cloud_disconnects = 2
    proxy.cloud_timeouts = 1
    proxy.cloud_errors = 3
    proxy._set_commands_buffer = []
    proxy._tbl_events_buffer = []
    proxy._log_buffer = deque(maxlen=100)
    proxy._state_changes_buffer = []
    return proxy


def test_track_state_change_box():
    """Test tracking BOX connection state changes."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._state_changes_buffer = []

    proxy._track_state_change("box", True)
    proxy._track_state_change("box", False)

    assert len(proxy._state_changes_buffer) == 2
    assert proxy._state_changes_buffer[0]["entity"] == "box"
    assert proxy._state_changes_buffer[0]["state"] == "connected"
    assert proxy._state_changes_buffer[1]["state"] == "disconnected"
    assert "timestamp" in proxy._state_changes_buffer[0]


def test_track_state_change_cloud():
    """Test tracking cloud connection state changes."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._state_changes_buffer = []

    proxy._track_state_change("cloud", True)
    proxy._track_state_change("cloud", False)

    assert len(proxy._state_changes_buffer) == 2
    assert proxy._state_changes_buffer[0]["entity"] == "cloud"
    assert proxy._state_changes_buffer[1]["entity"] == "cloud"


def test_set_box_connected_tracks_changes():
    """Test _set_box_connected tracks state changes."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.box_connected = False
    proxy._state_changes_buffer = []

    # Change from False to True
    proxy._set_box_connected(True)
    assert proxy.box_connected is True
    assert len(proxy._state_changes_buffer) == 1
    assert proxy._state_changes_buffer[0]["entity"] == "box"
    assert proxy._state_changes_buffer[0]["state"] == "connected"

    # No change - should not track
    proxy._set_box_connected(True)
    assert len(proxy._state_changes_buffer) == 1  # Still 1

    # Change from True to False
    proxy._set_box_connected(False)
    assert proxy.box_connected is False
    assert len(proxy._state_changes_buffer) == 2
    assert proxy._state_changes_buffer[1]["state"] == "disconnected"


def test_set_cloud_connected_tracks_changes():
    """Test _set_cloud_connected tracks state changes."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.cloud_session_connected = False
    proxy._state_changes_buffer = []

    proxy._set_cloud_connected(True)
    assert proxy.cloud_session_connected is True
    assert len(proxy._state_changes_buffer) == 1
    assert proxy._state_changes_buffer[0]["entity"] == "cloud"
    assert proxy._state_changes_buffer[0]["state"] == "connected"

    # No change
    proxy._set_cloud_connected(True)
    assert len(proxy._state_changes_buffer) == 1

    # Disconnect
    proxy._set_cloud_connected(False)
    assert len(proxy._state_changes_buffer) == 2
    assert proxy._state_changes_buffer[1]["state"] == "disconnected"


def test_collect_telemetry_metrics_basic(tmp_path, monkeypatch):
    """Test _collect_telemetry_metrics returns correct structure."""
    proxy = _make_telemetry_proxy(tmp_path)

    # Mock time.time() to return a known value
    monkeypatch.setattr("time.time", lambda: 1300.0)

    metrics = proxy._collect_telemetry_metrics()

    assert metrics["uptime_s"] == 300  # 1300 - 1000
    assert metrics["mode"] == "online"  # ProxyMode.ONLINE.value
    assert metrics["configured_mode"] == "online"
    assert metrics["box_connected"] is False
    assert metrics["box_peer"] == "1.2.3.4:5678"
    assert metrics["frames_received"] == 100
    assert metrics["frames_forwarded"] == 95
    assert metrics["cloud_connects"] == 10
    assert metrics["cloud_disconnects"] == 2
    assert metrics["cloud_timeouts"] == 1
    assert metrics["cloud_errors"] == 3
    assert metrics["cloud_online"] is True  # not _hybrid_in_offline
    assert metrics["mqtt_ok"] is True
    assert metrics["mqtt_queue"] == 5
    assert metrics["set_commands"] == []
    assert "window_metrics" in metrics
    assert "tbl_events" in metrics["window_metrics"]
    assert "logs" in metrics["window_metrics"]
    assert "state_changes" in metrics["window_metrics"]


def test_collect_telemetry_clears_buffers(tmp_path, monkeypatch):
    """Test that collect_telemetry_metrics clears all buffers."""
    proxy = _make_telemetry_proxy(tmp_path)
    monkeypatch.setattr("time.time", lambda: 1300.0)

    # Populate buffers
    proxy._set_commands_buffer.append({"key": "test", "value": "1"})
    proxy._tbl_events_buffer.append({"type": "Setting", "content": "test event"})
    proxy._log_buffer.append({"level": "WARNING", "message": "test warning"})
    proxy._state_changes_buffer.append({"entity": "box", "state": "connected"})

    metrics = proxy._collect_telemetry_metrics()

    # Check buffers were included
    assert len(metrics["set_commands"]) == 1
    assert len(metrics["window_metrics"]["tbl_events"]) == 1
    assert len(metrics["window_metrics"]["logs"]) == 1
    assert len(metrics["window_metrics"]["state_changes"]) == 1

    # Check buffers were cleared
    assert len(proxy._set_commands_buffer) == 0
    assert len(proxy._tbl_events_buffer) == 0
    assert len(proxy._log_buffer) == 0
    assert len(proxy._state_changes_buffer) == 0


def test_tbl_events_buffer_captures_events(tmp_path):
    """Test that tbl_events are captured into buffer."""
    proxy = _make_telemetry_proxy(tmp_path)

    class DummyParser:
        @staticmethod
        def parse_mode_from_event(content):
            return None

    proxy.parser = DummyParser()
    proxy.mode_lock = asyncio.Lock()

    parsed = {
        "Type": "Setting",
        "Confirm": "Y",
        "Content": "tbl_box_prms.MODE from 2 to 1"
    }

    async def run():
        await proxy._maybe_process_mode(parsed, "tbl_events", "DEV1")

    asyncio.run(run())

    assert len(proxy._tbl_events_buffer) == 1
    event = proxy._tbl_events_buffer[0]
    assert event["type"] == "Setting"
    assert event["confirm"] == "Y"
    assert "MODE" in event["content"]
    assert "timestamp" in event
    # Check it's ISO format (either Z or +00:00 suffix)
    assert event["timestamp"].endswith(("Z", "+00:00"))


def test_log_buffer_maxlen():
    """Test that log buffer respects maxlen."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._log_buffer = deque(maxlen=10)

    # Add more than maxlen
    for i in range(15):
        proxy._log_buffer.append({"level": "WARNING", "message": f"warning_{i}"})

    # Should only keep last 10
    assert len(proxy._log_buffer) == 10
    assert proxy._log_buffer[-1]["message"] == "warning_14"
    assert proxy._log_buffer[0]["message"] == "warning_5"


def test_state_changes_timeline():
    """Test state changes create proper timeline."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.box_connected = False
    proxy.cloud_session_connected = False
    proxy._state_changes_buffer = []

    # Simulate connection flapping
    proxy._set_box_connected(True)
    proxy._set_cloud_connected(True)
    proxy._set_box_connected(False)
    proxy._set_box_connected(True)
    proxy._set_cloud_connected(False)

    assert len(proxy._state_changes_buffer) == 5
    timeline = proxy._state_changes_buffer

    assert timeline[0]["entity"] == "box" and timeline[0]["state"] == "connected"
    assert timeline[1]["entity"] == "cloud" and timeline[1]["state"] == "connected"
    assert timeline[2]["entity"] == "box" and timeline[2]["state"] == "disconnected"
    assert timeline[3]["entity"] == "box" and timeline[3]["state"] == "connected"
    assert timeline[4]["entity"] == "cloud" and timeline[4]["state"] == "disconnected"

    # All should have timestamps
    for change in timeline:
        assert "timestamp" in change
        # ISO format with timezone (either Z or +00:00)
        assert change["timestamp"].endswith(("Z", "+00:00"))
