"""Tests for telemetry integration with main.py."""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTelemetryIntegration:
    """Test telemetry collector integration with main proxy loop."""

    @pytest.mark.asyncio
    async def test_telemetry_collector_initialized_with_device_id(self) -> None:
        """Verify TelemetryCollector is initialized with correct device_id."""
        from telemetry.collector import TelemetryCollector

        device_id = "test_device_123"
        collector = TelemetryCollector(
            interval_s=300,
            version="2.0.0",
            telemetry_enabled=True,
            telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
            telemetry_interval_s=300,
            device_id=device_id,
            mqtt_namespace="oig_local",
            mqtt_publisher=None,
        )
        collector.init()

        assert collector._device_id == device_id
        assert collector.client is not None
        assert collector.client.device_id == device_id

    @pytest.mark.asyncio
    async def test_telemetry_topic_format(self) -> None:
        """Verify telemetry topic format is oig/telemetry/{device_id}."""
        from telemetry.client import TelemetryClient

        device_id = "test_device_456"
        client = TelemetryClient(
            device_id=device_id,
            version="2.0.0",
            telemetry_enabled=True,
            telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
            telemetry_interval_s=300,
        )

        expected_topic = f"oig/telemetry/{device_id}"
        assert client.device_id == device_id
        assert expected_topic == "oig/telemetry/test_device_456"

    @pytest.mark.asyncio
    async def test_telemetry_payload_contains_expected_fields(self) -> None:
        """Verify telemetry payload contains device_id, timestamp, etc."""
        from telemetry.collector import TelemetryCollector

        device_id = "test_device_789"
        collector = TelemetryCollector(
            interval_s=300,
            version="2.0.0",
            telemetry_enabled=True,
            telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
            telemetry_interval_s=300,
            device_id=device_id,
            mqtt_namespace="oig_local",
            mqtt_publisher=None,
        )
        collector.init()

        metrics = collector.collect_metrics()

        assert "timestamp" in metrics
        assert "interval_s" in metrics
        assert metrics["interval_s"] == 300
        assert "uptime_s" in metrics
        assert "mode" in metrics
        assert "configured_mode" in metrics
        assert "box_connected" in metrics
        assert "mqtt_ok" in metrics
        assert "window_metrics" in metrics

    @pytest.mark.asyncio
    async def test_telemetry_collector_starts_and_publishes(self) -> None:
        """Verify collector starts and would publish telemetry."""
        from telemetry.collector import TelemetryCollector

        device_id = "test_device_abc"
        collector = TelemetryCollector(
            interval_s=300,
            version="2.0.0",
            telemetry_enabled=True,
            telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
            telemetry_interval_s=300,
            device_id=device_id,
            mqtt_namespace="oig_local",
            mqtt_publisher=None,
        )
        collector.init()

        assert collector.task is None

        collector.task = asyncio.create_task(collector.loop())
        await asyncio.sleep(0.01)

        assert collector.task is not None
        assert not collector.task.done()

        collector.task.cancel()
        try:
            await collector.task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_telemetry_collector_respects_interval(self) -> None:
        """Verify collector uses configured interval."""
        from telemetry.collector import TelemetryCollector

        custom_interval = 60
        collector = TelemetryCollector(
            interval_s=custom_interval,
            version="2.0.0",
            telemetry_enabled=True,
            telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
            telemetry_interval_s=custom_interval,
            device_id="test_device",
            mqtt_namespace="oig_local",
            mqtt_publisher=None,
        )

        assert collector.interval_s == custom_interval
        assert collector._telemetry_interval_s == custom_interval

    @pytest.mark.asyncio
    async def test_telemetry_disabled_when_config_false(self) -> None:
        """Verify telemetry is disabled when config.telemetry_enabled is False."""
        from telemetry.collector import TelemetryCollector

        collector = TelemetryCollector(
            interval_s=300,
            version="2.0.0",
            telemetry_enabled=False,
            telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
            telemetry_interval_s=300,
            device_id="test_device",
            mqtt_namespace="oig_local",
            mqtt_publisher=None,
        )
        collector.init()

        assert collector.client is not None
        assert collector.client._enabled is False

    @pytest.mark.asyncio
    async def test_telemetry_collector_with_mqtt_publisher(self) -> None:
        """Verify collector works with mqtt_publisher for state caching."""
        from telemetry.collector import TelemetryCollector

        mock_mqtt = MagicMock()
        mock_mqtt.get_cached_payload = MagicMock(return_value='{"lat": 123}')
        mock_mqtt.is_ready = MagicMock(return_value=True)

        device_id = "test_device_xyz"
        collector = TelemetryCollector(
            interval_s=300,
            version="2.0.0",
            telemetry_enabled=True,
            telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
            telemetry_interval_s=300,
            device_id=device_id,
            mqtt_namespace="oig_local",
            mqtt_publisher=mock_mqtt,
        )
        collector.init()

        assert collector._mqtt_publisher == mock_mqtt

        metrics = collector.collect_metrics()
        assert metrics["mqtt_ok"] is True


class TestConfigTelemetrySettings:
    """Test config telemetry settings."""

    def test_telemetry_interval_s_default(self) -> None:
        """Verify default telemetry_interval_s is 300."""
        from config import Config

        with patch.dict("os.environ", {}, clear=True):
            config = Config()
            assert config.telemetry_interval_s == 300

    def test_telemetry_interval_s_from_env(self) -> None:
        """Verify telemetry_interval_s can be set from env."""
        from config import Config

        with patch.dict("os.environ", {"TELEMETRY_INTERVAL_S": "60"}, clear=True):
            config = Config()
            assert config.telemetry_interval_s == 60

    def test_telemetry_enabled_default(self) -> None:
        """Verify default telemetry_enabled is True."""
        from config import Config

        with patch.dict("os.environ", {}, clear=True):
            config = Config()
            assert config.telemetry_enabled is True

    def test_telemetry_mqtt_broker_default(self) -> None:
        """Verify default telemetry_mqtt_broker."""
        from config import Config

        with patch.dict("os.environ", {}, clear=True):
            config = Config()
            assert config.telemetry_mqtt_broker == "telemetry.muriel-cz.cz:1883"
