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

# pylint: disable=protected-access,unspecified-encoding,too-few-public-methods,missing-class-docstring
# pylint: disable=missing-function-docstring,unnecessary-lambda,broad-exception-caught,too-many-lines
# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Add addon path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "addon" / "oig-proxy"))

from main import Config, ProxyApp, main  # noqa: E402  # pylint: disable=wrong-import-position
from parameter_refresh import ParameterReadbackRefresh  # noqa: E402  # pylint: disable=wrong-import-position
from protocol.ver_profile import native_frame_fields  # noqa: E402  # pylint: disable=wrong-import-position
from settings_constraints import PowerControlLimits  # noqa: E402  # pylint: disable=wrong-import-position
from twin import delivery as delivery_module  # noqa: E402  # pylint: disable=wrong-import-position
from tests.v2.test_integration import (  # noqa: E402  # pylint: disable=wrong-import-position
    MODEL, TEST_BOX, _qualified_transport, _send_poll,
)
from tests.v2.test_wire_profile_qualification import native  # noqa: E402  # pylint: disable=wrong-import-position


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
    config.control_mqtt_enabled = True
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

    def test_control_evidence_storage_defaults_and_env_overrides(self, monkeypatch):
        monkeypatch.delenv("CONTROL_VER_PROFILE_STATE_PATH", raising=False)
        monkeypatch.delenv("CONTROL_COUNTER_STATE_PATH", raising=False)
        defaults = Config()
        assert defaults.control_ver_profile_state_path == "/data/control_ver_profile.json"
        assert defaults.control_counter_state_path == "/data/control_counters.json"

        monkeypatch.setenv("CONTROL_VER_PROFILE_STATE_PATH", "/custom/profile.json")
        monkeypatch.setenv("CONTROL_COUNTER_STATE_PATH", "")
        overridden = Config()
        assert overridden.control_ver_profile_state_path == "/custom/profile.json"
        assert overridden.control_counter_state_path == ""

    @pytest.mark.parametrize(
        ("configured", "enabled"),
        [(None, False), ("false", False), ("unexpected", False), ("true", True), ("1", True)],
    )
    def test_box_mqtt_control_requires_explicit_enablement(self, monkeypatch, configured, enabled):
        if configured is None:
            monkeypatch.delenv("CONTROL_MQTT_ENABLED", raising=False)
        else:
            monkeypatch.setenv("CONTROL_MQTT_ENABLED", configured)
        assert Config().control_mqtt_enabled is enabled


class TestConfirmedSettingHandling:
    @pytest.mark.asyncio
    async def test_phase_write_publishes_only_separate_config_state(self, mock_config, tmp_path):
        mock_config.power_limits = PowerControlLimits(phase_calibration_enabled=True)
        mock_config.confirmed_state_path = str(tmp_path / "confirmed.json")
        app = ProxyApp(mock_config)
        app.device_id_manager = Mock(device_id="DEV01")
        app.sensor_loader = Mock()
        app.sensor_loader.iter_sensors.return_value = []
        app.mqtt = Mock()
        app._create_frame_processor("DEV01")
        app.frame_processor = AsyncMock()

        await app._on_phase_confirmation("FOREIGN", "P_CAL_R", "20", "2026-11-05 18:00:00", "setting_event")
        app.mqtt.publish_confirmed_state.assert_not_called()
        await app._on_phase_confirmation("DEV01", "P_CAL_R", "90", "2026-11-05 18:00:00", "setting_event")

        app.frame_processor.process.assert_not_awaited()
        args = app.mqtt.publish_confirmed_state.call_args.args
        assert args[:2] == ("DEV01", "phase_calibration_write")
        assert args[2]["P_CAL_R"]["value"] == 90
        assert args[2]["P_CAL_R"]["evidence"] == "setting_event"

    @pytest.mark.asyncio
    async def test_on_confirmed_setting_routes_through_frame_processor(self, mock_config) -> None:
        app = ProxyApp(mock_config)
        app.device_id_manager = Mock(device_id="DEV01")
        app.device_id_manager.validate.side_effect = lambda device: device == "DEV01"
        app.frame_processor = AsyncMock()

        await app._on_confirmed_setting("DEV01", "tbl_box_prms", "MODE", "3")

        app.frame_processor.process.assert_awaited_once_with(
            "DEV01",
            "tbl_box_prms",
            {"MODE": 3},
            confirmed_event=True,
        )

    @pytest.mark.asyncio
    async def test_confirmation_retains_empty_value_for_loaded_box(self, mock_config) -> None:
        app = ProxyApp(mock_config)
        app.device_id_manager = Mock(device_id="DEV01")
        app.device_id_manager.validate.side_effect = lambda device: device == "DEV01"
        app.frame_processor = AsyncMock()

        await app._on_confirmed_setting("OTHER", "tbl_box_prms", "IP", "wrong")
        await app._on_confirmed_setting("DEV01", "tbl_box_prms", "IP", "", "2026-11-12 13:00:00")

        app.frame_processor.process.assert_awaited_once_with(
            "DEV01", "tbl_box_prms", {"IP": "", "_dt": "2026-11-12 13:00:00"}, confirmed_event=True,
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


class TestControlLifecycle:
    @pytest.mark.asyncio
    async def test_broker_recovery_enables_proxy_controls_without_a_box_frame(self, mock_config):
        """An initial broker outage cannot leave controls unsubscribed until BOX traffic."""
        app = ProxyApp(mock_config)
        app.device_id_manager = Mock(device_id=None)
        app.mqtt = Mock()
        app.mqtt.is_ready.return_value = True
        app.mqtt.subscribe.return_value = True
        app.twin_queue = importlib.import_module("twin.state").TwinQueue()

        assert await app._recover_confirmed_state() is True

        assert app.twin_handler is not None
        assert {args[0] for args, _kwargs in app.mqtt.subscribe.call_args_list} == {
            "oig/+/control/set", "oig_local/+/set/#",
        }

    @pytest.mark.asyncio
    async def test_disabled_box_writes_keep_only_proxy_mode_topics(self, mock_config):
        mock_config.control_mqtt_enabled = False
        app = ProxyApp(mock_config)
        app.mqtt = Mock()
        app.mqtt.is_ready.return_value = True
        app.mqtt.subscribe.return_value = True
        app.twin_queue = importlib.import_module("twin.state").TwinQueue()
        app._handle_proxy_control = Mock(return_value=True)

        await app._ensure_control_handler("BOX01")

        callbacks = dict(args for args, _kwargs in app.mqtt.subscribe.call_args_list)
        proxy_topic = "oig_local/oig_proxy/set/proxy_control/PROXY_MODE"
        assert set(callbacks) == {proxy_topic, "oig/oig_proxy/control/set"}
        callbacks[proxy_topic](proxy_topic, b"1")
        app._handle_proxy_control.assert_called_once_with("proxy_control", "PROXY_MODE", 1)
        assert app.twin_queue.size() == 0

    def test_parameter_refresh_waits_for_device_profile(self, mock_config):
        app = ProxyApp(mock_config)
        app.device_id_manager = Mock(device_id="BOX01")
        app.proxy = Mock()
        app.proxy.is_box_connected.return_value = True
        app.twin_handler = Mock()
        app.twin_queue = Mock()
        app.twin_delivery = Mock()
        app.twin_delivery.has_pending_or_inflight.return_value = False
        app.twin_delivery.profile_status.return_value = SimpleNamespace(profile=None)

        assert app._queue_parameter_refresh("daily") is False
        app.twin_handler.admit_message.assert_not_called()

        app.twin_delivery.profile_status.return_value = SimpleNamespace(profile=object())
        app.twin_handler.admit_message.return_value = object()
        mock_config.control_mqtt_enabled = False
        assert app._queue_parameter_refresh("daily") is False
        app.twin_handler.admit_message.assert_not_called()
        mock_config.control_mqtt_enabled = True
        assert app._queue_parameter_refresh("daily") is True
        app.twin_handler.admit_message.assert_called_once_with(
            "oig_local/BOX01/set/tbl_box_prms/SA", b"1", only_if_empty=True,
        )
        app.twin_queue.get.assert_not_called()


async def _prepare_mode_receipt(config, clock, source, writer):
    """Send MODE through the normal local or Cloud path and return the result sink."""
    app = ProxyApp(config)
    app.device_id_manager = Mock(device_id=TEST_BOX)
    app.frame_processor = AsyncMock()
    proxy, delivery, queue, wire_clock = _qualified_transport(config, callback=app._on_confirmed_setting)
    app.proxy = proxy
    app.twin_delivery = delivery
    app.twin_queue = queue
    setting_results = []
    delivery._on_result = setting_results.append
    app.mqtt = Mock()
    app.mqtt.is_ready.return_value = True
    await app._ensure_control_handler(TEST_BOX)

    proxy._readback = ParameterReadbackRefresh(clock=clock)
    proxy._request_readback = lambda: app._admit_parameter_refresh("missing native readback")
    proxy._box_peers["box-flow"] = "127.0.0.1:5710"

    if source == "local":
        assert app.twin_handler.admit_message(f"oig_local/{TEST_BOX}/set/tbl_box_prms/MODE", b"1") is not None
        await _send_poll(proxy, writer)
    else:
        await _send_poll(proxy, writer)
        wire_clock.now = time.time() - 10
        setting = native(
            f"<ID_Device>{TEST_BOX}</ID_Device><ID>13800001</ID><ID_Set>{int(wire_clock.now)}</ID_Set>"
            f"<DT>{wire_clock.stamp()}</DT><Reason>Setting</Reason>"
            "<TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue>", MODEL,
        )
        reader = asyncio.StreamReader()
        reader.feed_data(setting)
        reader.feed_eof()
        await proxy._pipe_cloud_to_box(reader, writer, session_id="box-flow")
    return app, setting_results


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["local", "cloud"])
@pytest.mark.parametrize("receipt", ["ACK", "NACK"])
async def test_setting_receipt_arms_readback_only_for_positive_ack(
    make_config, monkeypatch, dummy_writer_factory, source, receipt,
):
    """A matched ACK without an event deserves readback from either write path."""
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(monotonic=clock, time=time.time))
    writer = dummy_writer_factory()
    app, setting_results = await _prepare_mode_receipt(make_config(control_mqtt_enabled=True), clock, source, writer)
    assert app.twin_delivery.inflight() == ("tbl_box_prms", "MODE")
    assert len(writer.written) == 1

    wrong_receipt = native("<Result>ACK</Result><Reason>Setting</Reason><TblItem>BAT_MIN</TblItem>", MODEL)
    await _send_poll(app.proxy, writer, poll=wrong_receipt)
    assert not setting_results

    reply = native(f"<Result>{receipt}</Result><Reason>Setting</Reason>", MODEL)
    forwarded = await _send_poll(app.proxy, writer, poll=reply)
    forwarded.write.assert_called_once_with(reply)
    assert [(entry["result"], entry["source"], entry["key"]) for entry in setting_results] == [
        (receipt.lower(), source, "MODE"),
    ]
    app.frame_processor.process.assert_not_awaited()

    writer = dummy_writer_factory()
    clock.return_value = 59.0
    await _send_poll(app.proxy, writer, session_id="readback-flow")
    assert not writer.written
    clock.return_value = 65.0
    forwarded = await _send_poll(app.proxy, writer, session_id="readback-flow", poll=native(
        f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device><Lat>17</Lat>", MODEL,
    ))
    if receipt == "NACK":
        assert not writer.written
        return

    assert len(writer.written) == 1, "Expected one SA after the matched ACK"
    sent = writer.written[0]
    fields = native_frame_fields(sent)
    assert fields is not None
    assert (fields["TblName"], fields["TblItem"], fields["NewValue"]) == ("tbl_box_prms", "SA", "1")
    forwarded.write.assert_not_called()

    sa_ack = native("<Result>ACK</Result><Reason>Setting</Reason><TblItem>SA</TblItem>", MODEL)
    await _send_poll(app.proxy, writer, session_id="readback-flow", poll=sa_ack)
    assert app.twin_delivery.inflight() is None
    clock.return_value = 130.0
    await _send_poll(app.proxy, writer, session_id="later-flow")
    assert writer.written == [sent]


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
    async def test_startup_wires_proxy_cloud_counters_into_status_publisher(
        self, mock_config
    ):
        """Status callbacks read live counters after ProxyServer starts."""
        mock_config.proxy_status_interval = 60
        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager, patch(
            "main.MQTTClient"
        ) as mock_mqtt_class, patch(
            "main.ProxyStatusPublisher"
        ) as mock_status_class, patch("main.ProxyServer") as mock_proxy_class:
            device_manager = Mock()
            device_manager.load.return_value = "BOX01"
            device_manager.device_id = "BOX01"
            mock_device_manager.return_value = device_manager

            mqtt = Mock()
            mqtt.is_ready.return_value = False
            mqtt.health_check_loop = AsyncMock()
            mock_mqtt_class.return_value = mqtt

            status = Mock()
            status.run = AsyncMock()
            mock_status_class.return_value = status

            proxy = AsyncMock()
            proxy.cloud_connects = 7
            proxy.cloud_disconnects = 3
            proxy.cloud_timeouts = 2
            proxy.cloud_errors = 1
            proxy.mode_manager.configured_mode = "hybrid"
            mock_proxy_class.return_value = proxy

            await app.startup()

        callbacks = mock_status_class.call_args.kwargs
        assert callbacks["get_cloud_connects"]() == 7
        assert callbacks["get_cloud_disconnects"]() == 3
        assert callbacks["get_cloud_timeouts"]() == 2
        assert callbacks["get_cloud_errors"]() == 1

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
    @pytest.mark.parametrize("enable_writes", [False, True])
    async def test_startup_creates_mqtt_client(self, mock_config, enable_writes):
        """Test that startup creates MQTT client with correct config."""
        limits = PowerControlLimits(ac_charge_max_a=125, grid_export_max_w=5400)
        mock_config.control_mqtt_enabled = enable_writes
        mock_config.power_limits = limits
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
            power_limits=limits if enable_writes else PowerControlLimits(),
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
    async def test_startup_passes_telemetry_and_evidence_paths_to_twin_delivery(self, mock_config, temp_dir):
        """TwinDelivery receives the live telemetry collector and durable evidence paths."""
        mock_config.telemetry_enabled = True
        profile_path = temp_dir / "profile.json"
        counter_path = temp_dir / "counters.json"
        limits = PowerControlLimits(ac_charge_max_a=125, grid_export_max_w=5400)
        mock_config.power_limits = limits
        mock_config.control_ver_profile_state_path = str(profile_path)
        mock_config.control_counter_state_path = str(counter_path)
        mock_config.capture_db_path = str(temp_dir / "payloads.db")
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
            on_result=app._on_setting_result,
            on_profile_change=app._on_ver_profile_change,
            profile_state_path=profile_path,
            counter_state_path=counter_path,
            archive_path=temp_dir / "payloads.db",
            box_writes_enabled=True,
            power_limits=limits,
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
    @pytest.mark.parametrize("source_table", ["tbl_actual", "IsNewSet"])
    async def test_on_frame_records_in_status_publisher(self, mock_config, source_table):
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
            "_table": source_table,
            "Temp": 25.5,
        })

        app.status_publisher.record_frame.assert_called_once_with("test_device", "tbl_actual")
        assert app.frame_processor.process.await_args.args[:2] == ("test_device", source_table)

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
    async def test_startup_handles_none_mqtt_client(self, mock_config):
        app = ProxyApp(mock_config)

        with patch("main.DeviceIdManager") as mock_device_manager:
            mock_instance = Mock()
            mock_instance.load.return_value = None
            mock_instance.device_id = None
            mock_device_manager.return_value = mock_instance

            with patch("main.MQTTClient", return_value=None):
                with patch("main.ProxyServer") as mock_proxy_class:
                    mock_proxy = AsyncMock()
                    mock_proxy_class.return_value = mock_proxy
                    result = await app.startup()

        assert result is True
        assert app.frame_processor is None

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

    @pytest.mark.asyncio
    async def test_shutdown_handles_cancelled_health_task(self, mock_config):
        app = ProxyApp(mock_config)
        app.proxy = AsyncMock()
        app.mqtt = Mock()

        async def never_finishes() -> None:
            await asyncio.Event().wait()

        app._health_task = asyncio.create_task(never_finishes())

        await app.shutdown()

        assert app._health_task.cancelled() is True

    @pytest.mark.asyncio
    async def test_shutdown_handles_cancelled_telemetry_task(self, mock_config):
        app = ProxyApp(mock_config)
        app.proxy = AsyncMock()
        app.mqtt = Mock()

        async def never_finishes() -> None:
            await asyncio.Event().wait()

        telemetry_task = asyncio.create_task(never_finishes())
        app.telemetry_collector = Mock(task=telemetry_task)

        await app.shutdown()

        assert telemetry_task.cancelled() is True


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
        app.proxy.apply_configured_mode = AsyncMock(return_value=True)
        app.status_publisher = Mock()

        handled = app._handle_proxy_control("proxy_control", "PROXY_MODE", 1)

        assert handled is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        app.proxy.apply_configured_mode.assert_awaited_once_with("hybrid")
        app.status_publisher._publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_proxy_control_accepts_mode_name_string(self, mock_config):
        app = ProxyApp(mock_config)
        app._loop = asyncio.get_running_loop()
        app.proxy = Mock()
        app.proxy.apply_configured_mode = AsyncMock(return_value=True)
        app.status_publisher = Mock()

        handled = app._handle_proxy_control("proxy_control", "PROXY_MODE", "offline")

        assert handled is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        app.proxy.apply_configured_mode.assert_awaited_once_with("offline")
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
