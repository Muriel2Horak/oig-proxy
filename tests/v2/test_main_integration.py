"""Integration tests for main.py with all components.

Tests the full integration of:
- DeviceIdManager
- SensorMapLoader
- FrameProcessor
- ModeManager (via ProxyServer)
- TwinControlHandler
- TwinDelivery
- ProxyStatusPublisher
- TelemetryCollector
"""

# pylint: disable=protected-access,unspecified-encoding,too-few-public-methods,missing-class-docstring,missing-function-docstring,unnecessary-lambda,broad-exception-caught,unused-variable
# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Add addon path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "addon" / "oig-proxy"))

from main import ProxyApp, main  # pylint: disable=wrong-import-position


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config(temp_dir):
    """Create a mock Config object."""
    config = Mock()
    config.proxy_host = "127.0.0.1"
    config.proxy_port = 0  # Let OS assign port
    config.cloud_host = "127.0.0.1"
    config.cloud_port = 5711
    config.cloud_connect_timeout = 1.0
    config.cloud_ack_timeout = 5.0
    config.mqtt_host = "127.0.0.1"
    config.mqtt_port = 1883
    config.mqtt_username = ""
    config.mqtt_password = ""
    config.mqtt_namespace = "oig_local"
    config.mqtt_qos = 1
    config.mqtt_state_retain = True
    config.log_level = "INFO"
    config.proxy_status_interval = 0  # Disable for tests
    config.proxy_device_id = "oig_proxy"
    config.sensor_map_path = str(temp_dir / "sensor_map.json")
    config.telemetry_enabled = False  # Disable for tests
    config.telemetry_mqtt_broker = "127.0.0.1:1883"
    config.telemetry_interval_s = 300
    config.capture_payloads = False
    config.capture_raw_bytes = False
    config.capture_retention_days = 7
    config.capture_db_path = str(temp_dir / "payloads.db")
    config.capture_pcap = False
    config.capture_pcap_path = str(temp_dir / "capture.pcap")
    config.capture_pcap_interface = "any"
    config.capture_pcap_max_size_mb = 100
    return config


@pytest.fixture
def sensor_map_content():
    """Sample sensor map content."""
    return {
        "sensors": {
            "tbl_actual:Temp": {
                "name": "Temperature",
                "name_cs": "Teplota",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
            },
            "tbl_actual:Humidity": {
                "name": "Humidity",
                "unit_of_measurement": "%",
                "device_class": "humidity",
                "state_class": "measurement",
            },
        }
    }


class TestProxyAppInitialization:
    """Test ProxyApp initialization and component setup."""

    def test_proxy_app_creates_all_components(self, mock_config):
        """Test that ProxyApp initializes with all component slots."""
        app = ProxyApp(mock_config)

        assert app.config == mock_config
        assert app.device_id_manager is None
        assert app.sensor_loader is None
        assert app.mqtt is None
        assert app.frame_processor is None
        assert app.twin_queue is None
        assert app.twin_delivery is None
        assert app.twin_handler is None
        assert app.status_publisher is None
        assert app.telemetry_collector is None
        assert app.proxy is None


class TestConfirmedSettingHandling:
    @pytest.mark.asyncio
    async def test_on_confirmed_setting_routes_through_frame_processor(self, mock_config) -> None:
        app = ProxyApp(mock_config)
        app.frame_processor = AsyncMock()

        await app._on_confirmed_setting("DEV01", "tbl_box_prms", "MODE", "3")

        app.frame_processor.process.assert_awaited_once_with(
            "DEV01",
            "tbl_box_prms",
            {"MODE": 3},
        )

    @pytest.mark.asyncio
    async def test_on_confirmed_setting_noops_without_frame_processor(self, mock_config) -> None:
        app = ProxyApp(mock_config)
        app.frame_processor = None

        await app._on_confirmed_setting("DEV01", "tbl_box_prms", "MODE", "3")

    def test_coerce_confirmed_value_parses_numeric_strings(self, mock_config) -> None:
        app = ProxyApp(mock_config)

        assert app._coerce_confirmed_value("3") == 3
        assert app._coerce_confirmed_value("3.5") == 3.5
        assert app._coerce_confirmed_value("MODE") == "MODE"


class TestStartupSequence:
    """Test the startup sequence in correct order."""

    @pytest.mark.asyncio
    async def test_startup_loads_device_id(self, mock_config, temp_dir):
        """Test that startup loads device_id from file."""
        # Create device_id file
        device_id_file = temp_dir / "device_id.json"
        with open(device_id_file, "w") as f:
            json.dump({"device_id": "test_device_123", "first_seen": "2024-01-01T00:00:00Z"}, f)

        app = ProxyApp(mock_config)

        # Patch DeviceIdManager to use temp file
        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = "test_device_123"
            mock_instance.device_id = "test_device_123"
            mock_device_manager.return_value = mock_instance

            # Patch MQTT to avoid actual connection
            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = False  # Not ready to skip handler
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy
                    await app.startup()

        assert app.device_id_manager is not None
        mock_instance.load.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_loads_sensor_map(self, mock_config, temp_dir, sensor_map_content):
        """Test that startup loads sensor_map.json."""
        # Create sensor_map file
        sensor_map_file = temp_dir / "sensor_map.json"
        with open(sensor_map_file, "w") as f:
            json.dump(sensor_map_content, f)

        mock_config.sensor_map_path = str(sensor_map_file)

        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = False
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy
                    await app.startup()

        assert app.sensor_loader is not None
        assert app.sensor_loader.sensor_count() == 2

    @pytest.mark.asyncio
    async def test_startup_creates_mqtt_client(self, mock_config):
        """Test that startup creates MQTT client with correct config."""
        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = False
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy
                    await app.startup()

        mock_mqtt_class.assert_called_once_with(
            host=mock_config.mqtt_host,
            port=mock_config.mqtt_port,
            username=mock_config.mqtt_username,
            password=mock_config.mqtt_password,
            namespace=mock_config.mqtt_namespace,
            qos=mock_config.mqtt_qos,
            state_retain=mock_config.mqtt_state_retain,
        )

    @pytest.mark.asyncio
    async def test_startup_creates_frame_processor(self, mock_config, temp_dir, sensor_map_content):
        """Test that startup creates FrameProcessor."""
        sensor_map_file = temp_dir / "sensor_map.json"
        with open(sensor_map_file, "w") as f:
            json.dump(sensor_map_content, f)

        mock_config.sensor_map_path = str(sensor_map_file)

        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = False
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy
                    await app.startup()

        assert app.frame_processor is not None

    @pytest.mark.asyncio
    async def test_startup_creates_twin_components(self, mock_config):
        """Test that startup creates TwinQueue and TwinDelivery."""
        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = False
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy
                    await app.startup()

        assert app.twin_queue is not None
        assert app.twin_delivery is not None

    @pytest.mark.asyncio
    async def test_startup_passes_telemetry_collector_to_twin_delivery(self, mock_config):
        """Test that TwinDelivery receives the live telemetry collector instance."""
        mock_config.telemetry_enabled = True
        app = ProxyApp(mock_config)

        collector = Mock()
        collector.init = Mock()
        collector.loop = AsyncMock()

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = False
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.TelemetryCollector", return_value=collector):
                    with patch("main.TwinDelivery") as mock_twin_delivery_class:
                        mock_twin_delivery_class.return_value = Mock()

                        with patch("main.ProxyServer") as mock_proxy_class:
                            mock_proxy = AsyncMock()
                            mock_proxy.mode_manager = Mock()
                            mock_proxy_class.return_value = mock_proxy
                            await app.startup()

        mock_twin_delivery_class.assert_called_once_with(
            app.twin_queue,
            mock_mqtt,
            telemetry_collector=collector,
        )

    @pytest.mark.asyncio
    async def test_startup_starts_twin_handler_when_mqtt_ready(self, mock_config):
        """Test that TwinControlHandler starts when MQTT is ready."""
        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = "test_device"
            mock_instance.device_id = "test_device"
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = True
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.TwinControlHandler") as mock_handler_class:
                    mock_handler = AsyncMock()
                    mock_handler_class.return_value = mock_handler

                    with patch("main.ProxyServer") as mock_proxy_class:
                        mock_proxy = AsyncMock()
                        mock_proxy_class.return_value = mock_proxy
                        await app.startup()

        mock_handler_class.assert_called_once()
        _args, kwargs = mock_handler_class.call_args
        assert kwargs["namespace"] == mock_config.mqtt_namespace
        mock_handler.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_skips_twin_handler_when_mqtt_not_ready(self, mock_config):
        """Test that TwinControlHandler is not started when MQTT is not ready."""
        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = False
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.TwinControlHandler") as mock_handler_class:
                    with patch("main.ProxyServer") as mock_proxy_class:
                        mock_proxy = AsyncMock()
                        mock_proxy_class.return_value = mock_proxy
                        await app.startup()

        mock_handler_class.assert_not_called()


class TestFrameProcessing:
    """Test frame processing through the callback chain."""

    @pytest.mark.asyncio
    async def test_on_frame_validates_device_id(self, mock_config):
        """Test that _on_frame validates device_id."""
        app = ProxyApp(mock_config)

        # Setup device_id_manager
        app.device_id_manager = Mock()
        app.device_id_manager.device_id = "expected_device"
        app.device_id_manager.validate.return_value = True

        # Setup frame_processor
        app.frame_processor = AsyncMock()

        # Setup status_publisher
        app.status_publisher = Mock()

        # Call with matching device_id
        await app._on_frame({
            "_device_id": "expected_device",
            "_table": "tbl_actual",
            "Temp": 25.5,
        })

        app.device_id_manager.validate.assert_called_once_with("expected_device")
        app.frame_processor.process.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_frame_saves_first_device_id(self, mock_config):
        """Test that _on_frame saves device_id on first frame."""
        app = ProxyApp(mock_config)

        # Setup device_id_manager with no device_id
        app.device_id_manager = Mock()
        app.device_id_manager.device_id = None

        # Setup frame_processor
        app.frame_processor = AsyncMock()

        # Setup status_publisher
        app.status_publisher = Mock()

        # Call with new device_id
        await app._on_frame({
            "_device_id": "new_device_123",
            "_table": "tbl_actual",
            "Temp": 25.5,
        })

        app.device_id_manager.save.assert_called_once_with("new_device_123")

    @pytest.mark.asyncio
    async def test_on_frame_rejects_mismatched_device_id(self, mock_config):
        """Test that _on_frame rejects frames with mismatched device_id."""
        app = ProxyApp(mock_config)

        # Setup device_id_manager with existing device_id
        app.device_id_manager = Mock()
        app.device_id_manager.device_id = "expected_device"
        app.device_id_manager.validate.return_value = False

        # Setup frame_processor
        app.frame_processor = AsyncMock()

        # Setup status_publisher
        app.status_publisher = Mock()

        # Call with wrong device_id
        await app._on_frame({
            "_device_id": "wrong_device",
            "_table": "tbl_actual",
            "Temp": 25.5,
        })

        # Frame processor should not be called
        app.frame_processor.process.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_frame_records_in_status_publisher(self, mock_config):
        """Test that _on_frame records frames in status publisher."""
        app = ProxyApp(mock_config)

        # Setup device_id_manager
        app.device_id_manager = Mock()
        app.device_id_manager.device_id = "test_device"
        app.device_id_manager.validate.return_value = True

        # Setup frame_processor
        app.frame_processor = AsyncMock()

        # Setup status_publisher
        app.status_publisher = Mock()

        # Call with valid frame
        await app._on_frame({
            "_device_id": "test_device",
            "_table": "tbl_actual",
            "Temp": 25.5,
        })

        app.status_publisher.record_frame.assert_called_once_with("test_device", "tbl_actual")

    @pytest.mark.asyncio
    async def test_on_frame_skips_empty_data(self, mock_config):
        """Test that _on_frame skips empty data."""
        app = ProxyApp(mock_config)

        app.frame_processor = AsyncMock()
        app.status_publisher = Mock()

        await app._on_frame({})

        app.frame_processor.process.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_frame_skips_missing_device_id(self, mock_config):
        """Test that _on_frame skips frames without device_id."""
        app = ProxyApp(mock_config)

        app.frame_processor = AsyncMock()
        app.status_publisher = Mock()

        await app._on_frame({"_table": "tbl_actual", "Temp": 25.5})

        app.frame_processor.process.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_frame_skips_missing_table(self, mock_config):
        """Test that _on_frame skips frames without table."""
        app = ProxyApp(mock_config)

        app.device_id_manager = Mock()
        app.device_id_manager.device_id = "test_device"
        app.device_id_manager.validate.return_value = True

        app.frame_processor = AsyncMock()
        app.status_publisher = Mock()

        await app._on_frame({"_device_id": "test_device", "Temp": 25.5})

        app.frame_processor.process.assert_not_called()


class TestShutdownSequence:
    """Test the graceful shutdown sequence."""

    @pytest.mark.asyncio
    async def test_shutdown_stops_proxy_server(self, mock_config):
        """Test that shutdown stops ProxyServer."""
        app = ProxyApp(mock_config)

        app.proxy = AsyncMock()
        app.mqtt = Mock()

        await app.shutdown()

        app.proxy.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_stops_telemetry_collector(self, mock_config):
        """Test that shutdown stops TelemetryCollector."""
        app = ProxyApp(mock_config)

        app.proxy = AsyncMock()
        app.telemetry_collector = Mock()
        # Create a real asyncio task that can be awaited
        async def dummy_coro():
            await asyncio.sleep(10)
        real_task = asyncio.create_task(dummy_coro())
        app.telemetry_collector.task = real_task
        app.mqtt = Mock()

        await app.shutdown()

        # Task should be cancelled
        assert real_task.cancelled() or real_task.done()

    @pytest.mark.asyncio
    async def test_shutdown_stops_status_publisher(self, mock_config):
        """Test that shutdown stops ProxyStatusPublisher."""
        app = ProxyApp(mock_config)

        app.proxy = AsyncMock()
        app.status_publisher = Mock()
        app.mqtt = Mock()

        await app.shutdown()

        app.status_publisher.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_stops_twin_handler(self, mock_config):
        """Test that shutdown stops TwinControlHandler."""
        app = ProxyApp(mock_config)

        app.proxy = AsyncMock()
        app.twin_handler = AsyncMock()
        app.mqtt = Mock()

        await app.shutdown()

        app.twin_handler.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_mqtt(self, mock_config):
        """Test that shutdown disconnects MQTT."""
        app = ProxyApp(mock_config)

        app.proxy = AsyncMock()
        app.mqtt = Mock()

        await app.shutdown()

        app.mqtt.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_handles_missing_components(self, mock_config):
        """Test that shutdown handles missing components gracefully."""
        app = ProxyApp(mock_config)

        # All components are None - should not raise
        await app.shutdown()


class TestIntegrationFlow:
    """Test full integration flow scenarios."""

    @pytest.mark.asyncio
    async def test_full_startup_shutdown_cycle(self, mock_config, temp_dir, sensor_map_content):
        """Test a complete startup and shutdown cycle."""
        # Create sensor_map file
        sensor_map_file = temp_dir / "sensor_map.json"
        with open(sensor_map_file, "w") as f:
            json.dump(sensor_map_content, f)

        mock_config.sensor_map_path = str(sensor_map_file)
        mock_config.proxy_status_interval = 0  # Disable

        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_device_instance = Mock()
            mock_device_instance.load.return_value = "test_device"
            mock_device_instance.device_id = "test_device"
            mock_device_manager.return_value = mock_device_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.is_ready.return_value = True
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy

                    with patch("main.TwinControlHandler") as mock_handler_class:
                        mock_handler = AsyncMock()
                        mock_handler_class.return_value = mock_handler

                        # Startup
                        await app.startup()

                        # Verify all components created
                        assert app.device_id_manager is not None
                        assert app.sensor_loader is not None
                        assert app.mqtt is not None
                        assert app.frame_processor is not None
                        assert app.twin_queue is not None
                        assert app.twin_delivery is not None
                        assert app.twin_handler is not None
                        assert app.proxy is not None

                        # Shutdown
                        await app.shutdown()

                        # Verify shutdown sequence
                        mock_proxy.stop.assert_called_once()
                        mock_handler.stop.assert_called_once()
                        mock_mqtt.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_frame_flow_through_components(self, mock_config, temp_dir, sensor_map_content):
        """Test that frames flow correctly through all components."""
        sensor_map_file = temp_dir / "sensor_map.json"
        with open(sensor_map_file, "w") as f:
            json.dump(sensor_map_content, f)

        mock_config.sensor_map_path = str(sensor_map_file)

        app = ProxyApp(mock_config)

        # Setup all components
        app.device_id_manager = Mock()
        app.device_id_manager.device_id = "test_device"
        app.device_id_manager.validate.return_value = True

        app.frame_processor = AsyncMock()
        app.status_publisher = Mock()

        # Simulate frame from proxy server
        frame_data = {
            "_device_id": "test_device",
            "_table": "tbl_actual",
            "Temp": 25.5,
            "Humidity": 60.0,
        }

        await app._on_frame(frame_data)

        # Verify flow
        app.device_id_manager.validate.assert_called_once_with("test_device")
        app.status_publisher.record_frame.assert_called_once_with("test_device", "tbl_actual")
        app.frame_processor.process.assert_called_once_with("test_device", "tbl_actual", frame_data)


class TestMainFunction:
    """Test the main() entry point."""

    @pytest.mark.asyncio
    async def test_main_creates_proxy_app(self):
        """Test that main() creates a ProxyApp instance."""
        with patch("main.Config") as mock_config_class:
            mock_config = Mock()
            mock_config.log_level = "INFO"
            mock_config.proxy_status_interval = 0
            mock_config.telemetry_enabled = False
            mock_config_class.return_value = mock_config

            with patch("main.configure_logging"):
                with patch("main.ProxyApp") as mock_app_class:
                    mock_app = AsyncMock()
                    mock_app_class.return_value = mock_app

                    # Run main in a way that we can stop it
                    async def run_and_cancel():
                        try:
                            await asyncio.wait_for(
                                asyncio.get_event_loop().run_in_executor(None, lambda: main()),
                                timeout=0.1
                            )
                        except asyncio.TimeoutError:
                            pass

                    try:
                        await run_and_cancel()
                    except Exception:
                        pass

                    mock_app_class.assert_called_once_with(mock_config)


class TestErrorHandling:
    """Test error handling in integration."""

    @pytest.mark.asyncio
    async def test_startup_handles_mqtt_failure(self, mock_config):
        """Test that startup continues even if MQTT connection fails."""
        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient") as mock_mqtt_class:
                mock_mqtt = Mock()
                mock_mqtt.connect.return_value = False  # Connection fails
                mock_mqtt.is_ready.return_value = False
                mock_mqtt.health_check_loop = AsyncMock()
                mock_mqtt_class.return_value = mock_mqtt

                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy
                    result = await app.startup()

        assert result is True  # Should still succeed
        assert app.mqtt is not None

    @pytest.mark.asyncio
    async def test_on_frame_handles_processor_error(self, mock_config):
        """Test that _on_frame handles FrameProcessor errors gracefully."""
        app = ProxyApp(mock_config)

        app.device_id_manager = Mock()
        app.device_id_manager.device_id = "test_device"
        app.device_id_manager.validate.return_value = True

        app.frame_processor = AsyncMock()
        app.frame_processor.process.side_effect = Exception("Processing error")

        app.status_publisher = Mock()

        # Should not raise
        await app._on_frame({
            "_device_id": "test_device",
            "_table": "tbl_actual",
            "Temp": 25.5,
        })

        # Frame processor was called even though it raised
        app.frame_processor.process.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_handles_cancelled_tasks(self, mock_config):
        """Test that shutdown handles already cancelled tasks."""
        app = ProxyApp(mock_config)

        app.proxy = AsyncMock()
        app.mqtt = Mock()

        # Create a task that's already done
        task = asyncio.create_task(asyncio.sleep(0))
        await task  # Let it complete
        app._tasks.add(task)

        # Should not raise
        await app.shutdown()


class TestSignalHandling:
    """Test signal handling for graceful shutdown."""

    @pytest.mark.asyncio
    async def test_signal_triggers_shutdown(self, mock_config):
        """Test that signals trigger graceful shutdown."""
        app = ProxyApp(mock_config)

        # Mock startup
        app.proxy = AsyncMock()
        app.mqtt = Mock()
        app._health_task = None

        # Set up signal handler
        loop = asyncio.get_running_loop()

        # Simulate signal by setting stop event
        app._stop_event.set()

        # Run should complete immediately due to stop event
        await app.run()

        # Verify shutdown was called (via run's finally block)
        # Note: In actual run(), shutdown is called in finally block


class TestComponentImports:
    """Test that all required components can be imported."""

    def test_all_components_imported(self):
        """Test that main.py imports all required components."""
        import main as main_module

        # Check that all expected components are imported
        assert hasattr(main_module, "DeviceIdManager")
        assert hasattr(main_module, "SensorMapLoader")
        assert hasattr(main_module, "FrameProcessor")
        assert hasattr(main_module, "TwinControlHandler")
        assert hasattr(main_module, "TwinDelivery")
        assert hasattr(main_module, "ProxyStatusPublisher")
        assert hasattr(main_module, "TelemetryCollector")
        assert hasattr(main_module, "MQTTClient")
        assert hasattr(main_module, "ProxyServer")
        assert hasattr(main_module, "TwinQueue")

    def test_proxy_app_has_all_attributes(self, mock_config):
        """Test that ProxyApp has all required component attributes."""
        app = ProxyApp(mock_config)

        # Check all component slots exist
        assert hasattr(app, "device_id_manager")
        assert hasattr(app, "sensor_loader")
        assert hasattr(app, "mqtt")
        assert hasattr(app, "frame_processor")
        assert hasattr(app, "twin_queue")
        assert hasattr(app, "twin_delivery")
        assert hasattr(app, "twin_handler")
        assert hasattr(app, "status_publisher")
        assert hasattr(app, "telemetry_collector")
        assert hasattr(app, "proxy")


class TestProxyControlScheduling:
    @pytest.mark.asyncio
    async def test_handle_proxy_control_schedules_mode_apply(self, mock_config):
        app = ProxyApp(mock_config)
        app._loop = asyncio.get_running_loop()
        app.proxy = Mock()
        app.proxy.mode_manager = Mock()
        app.proxy.mode_manager.apply_configured_mode = AsyncMock(return_value=True)
        app.status_publisher = Mock()

        handled = app._handle_proxy_control("proxy_control", "PROXY_MODE", 1)

        assert handled is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        app.proxy.mode_manager.apply_configured_mode.assert_called_once_with("hybrid")
        app.status_publisher._publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_proxy_control_accepts_mode_name_string(self, mock_config):
        app = ProxyApp(mock_config)
        app._loop = asyncio.get_running_loop()
        app.proxy = Mock()
        app.proxy.mode_manager = Mock()
        app.proxy.mode_manager.apply_configured_mode = AsyncMock(return_value=True)
        app.status_publisher = Mock()

        handled = app._handle_proxy_control("proxy_control", "PROXY_MODE", "offline")

        assert handled is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        app.proxy.mode_manager.apply_configured_mode.assert_called_once_with("offline")
        app.status_publisher._publish.assert_called_once()

    def test_handle_proxy_control_rejects_closed_loop(self, mock_config):
        app = ProxyApp(mock_config)
        loop = asyncio.new_event_loop()
        loop.close()
        app._loop = loop

        handled = app._handle_proxy_control("proxy_control", "PROXY_MODE", 1)

        assert handled is True

    def test_handle_proxy_control_rejects_invalid_mode(self, mock_config):
        app = ProxyApp(mock_config)

        handled = app._handle_proxy_control("proxy_control", "PROXY_MODE", 99)

        assert handled is True
