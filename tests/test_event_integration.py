"""Integration tests for event-driven sensor updates end-to-end flow.

Tests the full flow: Box event -> Proxy -> MQTT -> Verification
"""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught
# pylint: disable=consider-using-with,too-many-instance-attributes

import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import proxy as proxy_module
from control_pipeline import ControlPipeline
from control_settings import ControlSettings
from models import ProxyMode


class TestEventIntegration:
    """Integration tests for event-driven sensor updates."""

    @pytest.fixture
    def mock_proxy(self):
        # pylint: disable=too-many-statements
        """Create a mock proxy with all necessary components."""
        proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
        proxy._hm = MagicMock()
        proxy._hm.mode = ProxyMode.ONLINE
        proxy.device_id = "TEST_DEVICE_123"
        proxy._last_data_epoch = time.time()
        proxy.box_connected = True
        proxy._active_box_peer = ("192.168.1.100", 12345)
        proxy._active_box_writer = MagicMock()
        proxy._box_conn_lock = asyncio.Lock()
        proxy._loop = asyncio.new_event_loop()
        proxy._background_tasks = set()

        # Mock MQTT publisher
        proxy.mqtt_publisher = MagicMock()
        proxy.mqtt_publisher.publish_raw = AsyncMock(return_value=True)
        proxy.mqtt_publisher.device_id = "TEST_DEVICE_123"
        proxy.mqtt_publisher.state_topic = MagicMock(
            return_value="oig_local/TEST_DEVICE_123/tbl_box_prms/state"
        )
        proxy.mqtt_publisher.get_cached_payload = MagicMock(return_value=None)
        proxy.mqtt_publisher.set_cached_payload = MagicMock()
        proxy.mqtt_publisher.map_data_for_publish = MagicMock(
            return_value=({"MODE": 0}, {})
        )

        # Mock MSC (MqttStateCoordinator)
        proxy._msc = MagicMock()
        proxy._msc.table_cache = {}
        proxy._msc.last_values = {}

        # Mock MP (MqttPublisher)
        proxy._mp = MagicMock()
        proxy._mp.prms_tables = {}

        # Mock Twin
        proxy._twin = None

        # Create ControlPipeline
        ctrl = ControlPipeline.__new__(ControlPipeline)
        ctrl._proxy = proxy
        ctrl.queue = deque()
        ctrl.inflight = None
        ctrl.ack_task = None
        ctrl.applied_task = None
        ctrl.quiet_task = None
        ctrl.pending_keys = set()
        ctrl.post_drain_refresh_pending = False
        ctrl.status_prefix = "oig/control/status"
        ctrl.qos = 1
        ctrl.retain = False
        ctrl.status_retain = False
        ctrl.result_topic = "oig/control/result"
        ctrl.log_enabled = False
        ctrl.log_path = "/tmp/test_control.log"
        ctrl.pending_path = "/tmp/test_pending.json"
        ctrl.mqtt_enabled = False
        ctrl.set_topic = ""
        ctrl.box_ready_s = 0.0
        ctrl.ack_timeout_s = 10.0
        ctrl.applied_timeout_s = 30.0
        ctrl.mode_quiet_s = 0.0
        ctrl.whitelist = {}
        ctrl.max_attempts = 5
        ctrl.retry_delay_s = 120.0
        ctrl.session_id = "test_session"
        ctrl.lock = asyncio.Lock()
        ctrl.retry_task = None
        ctrl.last_result = None
        ctrl.key_state = {}
        proxy._ctrl = ctrl

        # Create ControlSettings
        cs = ControlSettings.__new__(ControlSettings)
        cs._proxy = proxy
        cs.pending = None
        cs.pending_frame = None
        cs.set_commands_buffer = []
        proxy._cs = cs

        return proxy

    @pytest.mark.asyncio
    async def test_mode_change_event_flow(self, mock_proxy):
        """Test MODE change event from tbl_box_prms is processed end-to-end."""
        # Arrange
        start_time = time.monotonic()
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()

        parsed = {
            "Type": "Setting",
            "Content": "Remotely : tbl_box_prms / MODE: [3]->[0]",
        }

        # Act
        await mock_proxy._cs.handle_setting_event(
            parsed=parsed,
            table_name="tbl_events",
            device_id="TEST_DEVICE_123",
        )

        elapsed = time.monotonic() - start_time

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 1
        assert mock_proxy._cs.set_commands_buffer[0]["key"] == "tbl_box_prms:MODE"
        assert mock_proxy._cs.set_commands_buffer[0]["value"] == "0"
        assert mock_proxy._cs.set_commands_buffer[0]["result"] == "applied"
        assert mock_proxy._cs.set_commands_buffer[0]["source"] == "tbl_events"

        # Verify publish_setting_event_state was called with correct parameters
        mock_proxy._ctrl.publish_setting_event_state.assert_called_once()
        call_kwargs = mock_proxy._ctrl.publish_setting_event_state.call_args.kwargs
        assert call_kwargs["tbl_name"] == "tbl_box_prms"
        assert call_kwargs["tbl_item"] == "MODE"
        assert call_kwargs["new_value"] == "0"
        assert call_kwargs["device_id"] == "TEST_DEVICE_123"
        assert call_kwargs["source"] == "tbl_events"

        # Timing assertion: should complete in less than 5 seconds
        assert elapsed < 5.0, f"Event processing took {elapsed:.2f}s, expected < 5s"

    @pytest.mark.asyncio
    async def test_manual_change_event_flow(self, mock_proxy):
        """Test MANUAL change event from tbl_boiler_prms is processed end-to-end."""
        # Arrange
        start_time = time.monotonic()
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()

        parsed = {
            "Type": "Setting",
            "Content": "Remotely : tbl_boiler_prms / MANUAL: [0]->[1]",
        }

        # Act
        await mock_proxy._cs.handle_setting_event(
            parsed=parsed,
            table_name="tbl_events",
            device_id="TEST_DEVICE_123",
        )

        elapsed = time.monotonic() - start_time

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 1
        assert mock_proxy._cs.set_commands_buffer[0]["key"] == "tbl_boiler_prms:MANUAL"
        assert mock_proxy._cs.set_commands_buffer[0]["value"] == "1"
        assert mock_proxy._cs.set_commands_buffer[0]["result"] == "applied"
        assert mock_proxy._cs.set_commands_buffer[0]["source"] == "tbl_events"

        # Verify publish_setting_event_state was called with correct parameters
        mock_proxy._ctrl.publish_setting_event_state.assert_called_once()
        call_kwargs = mock_proxy._ctrl.publish_setting_event_state.call_args.kwargs
        assert call_kwargs["tbl_name"] == "tbl_boiler_prms"
        assert call_kwargs["tbl_item"] == "MANUAL"
        assert call_kwargs["new_value"] == "1"
        assert call_kwargs["device_id"] == "TEST_DEVICE_123"
        assert call_kwargs["source"] == "tbl_events"

        # Timing assertion: should complete in less than 5 seconds
        assert elapsed < 5.0, f"Event processing took {elapsed:.2f}s, expected < 5s"

    @pytest.mark.asyncio
    async def test_publish_setting_event_state_integration(self, mock_proxy):
        """Test publish_setting_event_state publishes to MQTT correctly."""
        # Arrange
        start_time = time.monotonic()

        # Act
        await mock_proxy._ctrl.publish_setting_event_state(
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="0",
            device_id="TEST_DEVICE_123",
            source="tbl_events",
        )

        elapsed = time.monotonic() - start_time

        # Assert
        mock_proxy.mqtt_publisher.publish_raw.assert_called_once()
        call_args = mock_proxy.mqtt_publisher.publish_raw.call_args

        # Verify topic
        assert "tbl_box_prms/state" in call_args.kwargs["topic"]

        # Verify payload contains the updated value
        import json

        payload = json.loads(call_args.kwargs["payload"])
        assert "MODE" in payload

        # Verify retain flag
        assert call_args.kwargs["retain"] is True

        # Timing assertion
        assert elapsed < 5.0, f"Publish took {elapsed:.2f}s, expected < 5s"

    @pytest.mark.asyncio
    async def test_non_setting_event_ignored(self, mock_proxy):
        """Test that non-Setting events are ignored."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()
        mock_proxy._cs.set_commands_buffer.clear()

        parsed = {
            "Type": "Info",  # Not a Setting type
            "Content": "Some info message",
        }

        # Act
        await mock_proxy._cs.handle_setting_event(
            parsed=parsed,
            table_name="tbl_events",
            device_id="TEST_DEVICE_123",
        )

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 0
        mock_proxy._ctrl.publish_setting_event_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_events_table_ignored(self, mock_proxy):
        """Test that events from non-tbl_events tables are ignored."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()
        mock_proxy._cs.set_commands_buffer.clear()

        parsed = {
            "Type": "Setting",
            "Content": "Remotely : tbl_box_prms / MODE: [3]->[0]",
        }

        # Act - send from wrong table
        await mock_proxy._cs.handle_setting_event(
            parsed=parsed,
            table_name="tbl_box_prms",  # Not tbl_events
            device_id="TEST_DEVICE_123",
        )

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 0
        mock_proxy._ctrl.publish_setting_event_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_event_content_ignored(self, mock_proxy):
        """Test that invalid event content is ignored gracefully."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()
        mock_proxy._cs.set_commands_buffer.clear()

        parsed = {
            "Type": "Setting",
            "Content": "Invalid content without proper format",
        }

        # Act
        await mock_proxy._cs.handle_setting_event(
            parsed=parsed,
            table_name="tbl_events",
            device_id="TEST_DEVICE_123",
        )

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 0
        mock_proxy._ctrl.publish_setting_event_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_events_sequential(self, mock_proxy):
        """Test processing multiple events sequentially."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()
        mock_proxy._cs.set_commands_buffer.clear()

        events = [
            {"Type": "Setting", "Content": "Remotely : tbl_box_prms / MODE: [3]->[0]"},
            {"Type": "Setting", "Content": "Remotely : tbl_boiler_prms / MANUAL: [0]->[1]"},
            {"Type": "Setting", "Content": "Remotely : tbl_box_prms / MODE: [0]->[3]"},
        ]

        start_time = time.monotonic()

        # Act
        for event in events:
            await mock_proxy._cs.handle_setting_event(
                parsed=event,
                table_name="tbl_events",
                device_id="TEST_DEVICE_123",
            )

        elapsed = time.monotonic() - start_time

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 3
        assert mock_proxy._ctrl.publish_setting_event_state.call_count == 3

        # Verify all events were recorded
        keys = [cmd["key"] for cmd in mock_proxy._cs.set_commands_buffer]
        assert "tbl_box_prms:MODE" in keys
        assert "tbl_boiler_prms:MANUAL" in keys

        # Timing: all events should complete in less than 5 seconds total
        assert elapsed < 5.0, f"Multiple events took {elapsed:.2f}s, expected < 5s"

    @pytest.mark.asyncio
    async def test_event_with_float_values(self, mock_proxy):
        """Test event processing with float values."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()

        parsed = {
            "Type": "Setting",
            "Content": "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]",
        }

        # Act
        await mock_proxy._cs.handle_setting_event(
            parsed=parsed,
            table_name="tbl_events",
            device_id="TEST_DEVICE_123",
        )

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 1
        assert mock_proxy._cs.set_commands_buffer[0]["key"] == "tbl_invertor_prm1:AAC_MAX_CHRG"
        assert mock_proxy._cs.set_commands_buffer[0]["value"] == "120.0"

        mock_proxy._ctrl.publish_setting_event_state.assert_called_once()
        call_kwargs = mock_proxy._ctrl.publish_setting_event_state.call_args.kwargs
        assert call_kwargs["tbl_name"] == "tbl_invertor_prm1"
        assert call_kwargs["tbl_item"] == "AAC_MAX_CHRG"
        assert call_kwargs["new_value"] == "120.0"

    @pytest.mark.asyncio
    async def test_empty_content_ignored(self, mock_proxy):
        """Test that events with empty content are ignored."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()
        mock_proxy._cs.set_commands_buffer.clear()

        parsed = {
            "Type": "Setting",
            "Content": "",  # Empty content
        }

        # Act
        await mock_proxy._cs.handle_setting_event(
            parsed=parsed,
            table_name="tbl_events",
            device_id="TEST_DEVICE_123",
        )

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 0
        mock_proxy._ctrl.publish_setting_event_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_parsed_ignored(self, mock_proxy):
        """Test that None parsed data is ignored."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()
        mock_proxy._cs.set_commands_buffer.clear()

        # Act
        await mock_proxy._cs.handle_setting_event(
            parsed=None,
            table_name="tbl_events",
            device_id="TEST_DEVICE_123",
        )

        # Assert
        assert len(mock_proxy._cs.set_commands_buffer) == 0
        mock_proxy._ctrl.publish_setting_event_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_timing_benchmark(self, mock_proxy):
        """Benchmark event processing timing."""
        # Arrange
        mock_proxy._ctrl.publish_setting_event_state = AsyncMock()

        timings = []
        num_iterations = 10

        for _ in range(num_iterations):
            mock_proxy._cs.set_commands_buffer.clear()
            start = time.perf_counter()

            await mock_proxy._cs.handle_setting_event(
                parsed={
                    "Type": "Setting",
                    "Content": "Remotely : tbl_box_prms / MODE: [3]->[0]",
                },
                table_name="tbl_events",
                device_id="TEST_DEVICE_123",
            )

            elapsed = time.perf_counter() - start
            timings.append(elapsed)

        # Calculate statistics
        avg_time = sum(timings) / len(timings)
        max_time = max(timings)
        min_time = min(timings)

        # Assert
        assert avg_time < 0.1, f"Average time {avg_time:.4f}s too high"
        assert max_time < 0.5, f"Max time {max_time:.4f}s too high"

        print(f"\nTiming benchmark ({num_iterations} iterations):")
        print(f"  Average: {avg_time:.4f}s")
        print(f"  Min: {min_time:.4f}s")
        print(f"  Max: {max_time:.4f}s")
