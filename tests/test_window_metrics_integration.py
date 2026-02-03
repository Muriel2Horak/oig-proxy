"""Test window_metrics integration with send_telemetry."""

import sys
from pathlib import Path
import asyncio
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "addon" / "oig-proxy"))

from telemetry_client import TelemetryClient


def test_send_telemetry_includes_window_metrics():
    """Test that send_telemetry includes window_metrics in payload."""
    
    async def test():
        client = TelemetryClient("test_device", "1.5.0")
        
        # Add some window metrics
        client.track_event("test_event", "event details")
        client.track_state_change("box", "disconnected", "connected")
        
        # Mock the MQTT publish to capture payload
        published_payload = None
        
        def mock_publish(topic, payload, qos=1):
            nonlocal published_payload
            import json
            published_payload = json.loads(payload)
            mock_result = MagicMock()
            mock_result.rc = 0
            return mock_result
        
        with patch.object(client, '_ensure_connected', return_value=True):
            with patch.object(client, '_client') as mock_client:
                mock_client.publish = mock_publish
                
                # Send telemetry
                result = await client.send_telemetry({
                    "mode": "online",
                    "uptime_s": 100,
                })
        
        assert result is True
        assert published_payload is not None
        assert "window_metrics" in published_payload
        
        # Check window_metrics structure
        window_metrics = published_payload["window_metrics"]
        assert "logs" in window_metrics
        assert "tbl_events" in window_metrics
        assert "state_changes" in window_metrics
        
        # Check specific data
        assert len(window_metrics["tbl_events"]) == 1
        assert window_metrics["tbl_events"][0]["event"] == "test_event"
        assert window_metrics["tbl_events"][0]["details"] == "event details"
        
        assert len(window_metrics["state_changes"]) == 1
        assert window_metrics["state_changes"][0]["field"] == "box"
        assert window_metrics["state_changes"][0]["old"] == "disconnected"
        assert window_metrics["state_changes"][0]["new"] == "connected"
    
    asyncio.run(test())


def test_send_telemetry_clears_window_metrics():
    """Test that window_metrics are cleared after send_telemetry."""
    
    async def test():
        client = TelemetryClient("test_device", "1.5.0")
        
        # Add window metrics
        client.track_event("test_event", "details")
        
        with patch.object(client, '_ensure_connected', return_value=True):
            with patch.object(client, '_client') as mock_client:
                mock_result = MagicMock()
                mock_result.rc = 0
                mock_client.publish.return_value = mock_result
                
                # First send should include event
                await client.send_telemetry({"mode": "online"})
        
        # Check that window metrics are now empty
        metrics = client._window_metrics.get_window_metrics()
        assert len(metrics["tbl_events"]) == 0
        assert len(metrics["logs"]) == 0
        assert len(metrics["state_changes"]) == 0
    
    asyncio.run(test())


def test_send_telemetry_empty_window_metrics():
    """Test that send_telemetry works with empty window_metrics after clearing init logs."""
    
    async def test():
        client = TelemetryClient("test_device", "1.5.0")
        
        # Clear any init logs that were captured
        client._window_metrics.get_window_metrics()
        
        published_payload = None
        
        def mock_publish(topic, payload, qos=1):
            nonlocal published_payload
            import json
            published_payload = json.loads(payload)
            mock_result = MagicMock()
            mock_result.rc = 0
            return mock_result
        
        with patch.object(client, '_ensure_connected', return_value=True):
            with patch.object(client, '_client') as mock_client:
                mock_client.publish = mock_publish
                
                result = await client.send_telemetry({"mode": "online"})
        
        assert result is True
        assert published_payload is not None
        assert "window_metrics" in published_payload
        
        # Empty window metrics (no new events/logs after clearing init)
        window_metrics = published_payload["window_metrics"]
        assert window_metrics["logs"] == []
        assert window_metrics["tbl_events"] == []
        assert window_metrics["state_changes"] == []
    
    asyncio.run(test())
