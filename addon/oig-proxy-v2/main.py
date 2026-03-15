#!/usr/bin/env python3
"""
OIG Proxy v2 – entry point with full component integration.

Startup sequence:
1. Configure logging
2. Load device_id
3. Load sensor_map
4. Connect MQTT
5. Start TwinControlHandler
6. Start ProxyStatusPublisher
7. Start TelemetryCollector
8. Start ProxyServer

Graceful shutdown (reverse order):
1. Stop ProxyServer
2. Stop TelemetryCollector
3. Stop ProxyStatusPublisher
4. Stop TwinControlHandler
5. Disconnect MQTT
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from config import Config
from device_id import DeviceIdManager
from logging_config import configure_logging
from mqtt.client import MQTTClient
from mqtt.status import ProxyStatusPublisher
from proxy.server import ProxyServer
from sensor.loader import SensorMapLoader
from sensor.processor import FrameProcessor
from telemetry.collector import TelemetryCollector
from twin import TwinControlHandler, TwinQueue
from twin.delivery import TwinDelivery

logger = logging.getLogger("oig_proxy_v2")


class ProxyApp:
    """Main application class integrating all OIG Proxy v2 components."""

    def __init__(self, config: Config) -> None:
        """Initialize the proxy application with all components."""
        self.config = config
        self._stop_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Components (initialized in startup sequence)
        self.device_id_manager: DeviceIdManager | None = None
        self.sensor_loader: SensorMapLoader | None = None
        self.mqtt: MQTTClient | None = None
        self.frame_processor: FrameProcessor | None = None
        self.twin_queue: TwinQueue | None = None
        self.twin_delivery: TwinDelivery | None = None
        self.twin_handler: TwinControlHandler | None = None
        self.status_publisher: ProxyStatusPublisher | None = None
        self.telemetry_collector: TelemetryCollector | None = None
        self.proxy: ProxyServer | None = None

        # Background tasks
        self._tasks: set[asyncio.Task[Any]] = set()
        self._health_task: asyncio.Task[Any] | None = None

    async def startup(self) -> bool:
        """Execute startup sequence. Returns True if successful."""
        logger.info("OIG Proxy v2 starting up...")
        start_time = time.time()
        self._loop = asyncio.get_running_loop()

        # 1. Configure logging (already done in main())
        logger.info("Logging configured (level=%s)", self.config.log_level)

        # 2. Load device_id
        self.device_id_manager = DeviceIdManager()
        device_id = self.device_id_manager.load()
        logger.info("Device ID loaded: %s", device_id or "not set (will learn from frames)")

        # 3. Load sensor_map
        self.sensor_loader = SensorMapLoader(self.config.sensor_map_path)
        self.sensor_loader.load()
        sensor_count = self.sensor_loader.sensor_count()
        logger.info("Sensor map loaded: %d sensors from %s", sensor_count, self.config.sensor_map_path)

        # 4. Connect MQTT
        self.mqtt = MQTTClient(
            host=self.config.mqtt_host,
            port=self.config.mqtt_port,
            username=self.config.mqtt_username,
            password=self.config.mqtt_password,
            namespace=self.config.mqtt_namespace,
            qos=self.config.mqtt_qos,
            state_retain=self.config.mqtt_state_retain,
        )

        mqtt_device_id = device_id or "unknown"
        mqtt_ok = False
        mqtt_client = self.mqtt
        if mqtt_client is not None:
            mqtt_ok = await asyncio.get_event_loop().run_in_executor(
                None, lambda: mqtt_client.connect(mqtt_device_id)
            )
        if mqtt_ok:
            logger.info("MQTT connected to %s:%s", self.config.mqtt_host, self.config.mqtt_port)
        else:
            logger.warning("MQTT connection failed – proxy will run without MQTT")

        # Create frame processor (needs MQTT and sensor_loader)
        self.frame_processor = FrameProcessor(
            self.mqtt,
            self.sensor_loader,
            proxy_device_id=self.config.proxy_device_id,
        )
        if self.mqtt.is_ready() and self.frame_processor is not None:
            logger.info("FrameProcessor ready for lazy discovery from live frames")
            if device_id:
                self.frame_processor.publish_all_discovery(device_id)
                logger.info("Published full discovery for known device_id=%s", device_id)

        # Create twin components
        self.twin_queue = TwinQueue()
        self.twin_delivery = TwinDelivery(self.twin_queue, self.mqtt)

        # 5. Start TwinControlHandler (if MQTT ready)
        if self.mqtt.is_ready():
            self.twin_handler = TwinControlHandler(
                mqtt=self.mqtt,
                twin_queue=self.twin_queue,
                device_id=mqtt_device_id,
                proxy_control_handler=self._handle_proxy_control,
            )
            await self.twin_handler.start()
            logger.info("TwinControlHandler started")
        else:
            logger.warning("TwinControlHandler not started (MQTT not ready)")

        # 6. Start ProxyStatusPublisher
        if self.config.proxy_status_interval > 0:
            self.status_publisher = ProxyStatusPublisher(
                mqtt=self.mqtt,
                interval=self.config.proxy_status_interval,
                proxy_device_id=self.config.proxy_device_id,
                sensor_loader=self.sensor_loader,
                get_configured_mode=(
                    lambda: self.proxy.mode_manager.configured_mode if self.proxy else "online"
                ),
            )
            status_task = asyncio.create_task(
                self.status_publisher.run(),
                name="status_publisher",
            )
            self._tasks.add(status_task)
            status_task.add_done_callback(self._tasks.discard)
            logger.info("ProxyStatusPublisher started (interval=%ds)", self.config.proxy_status_interval)
        else:
            logger.info("ProxyStatusPublisher disabled (interval <= 0)")

        # 7. Start TelemetryCollector
        if self.config.telemetry_enabled:
            self.telemetry_collector = TelemetryCollector(
                interval_s=self.config.telemetry_interval_s,
                version="2.0.0",
                telemetry_enabled=self.config.telemetry_enabled,
                telemetry_mqtt_broker=self.config.telemetry_mqtt_broker,
                telemetry_interval_s=self.config.telemetry_interval_s,
                device_id=mqtt_device_id,
                mqtt_namespace=self.config.mqtt_namespace,
                mqtt_publisher=self.mqtt,
            )
            self.telemetry_collector.init()
            telemetry_task = asyncio.create_task(
                self.telemetry_collector.loop(),
                name="telemetry_collector",
            )
            self._tasks.add(telemetry_task)
            telemetry_task.add_done_callback(self._tasks.discard)
            logger.info("TelemetryCollector started (interval=%ds)", self.config.telemetry_interval_s)
        else:
            logger.info("TelemetryCollector disabled")

        # 8. Start ProxyServer
        self.proxy = ProxyServer(
            config=self.config,
            on_frame=self._on_frame,
            twin_delivery=self.twin_delivery,
        )
        await self.proxy.start()
        logger.info("ProxyServer started on %s:%s", self.config.proxy_host, self.config.proxy_port)

        # Start MQTT health check
        self._health_task = asyncio.create_task(
            self.mqtt.health_check_loop(mqtt_device_id),
            name="mqtt_health",
        )

        elapsed = time.time() - start_time
        logger.info("OIG Proxy v2 startup complete in %.2fs", elapsed)
        return True

    def _handle_proxy_control(self, table: str, key: str, value: Any) -> bool:
        if table != "proxy_control":
            return False
        if key == "PROXY_MODE":
            mode_map = {0: "online", 1: "hybrid", 2: "offline"}
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"online", "hybrid", "offline"}:
                    mode_name = normalized
                else:
                    try:
                        mode_name = mode_map[int(normalized)]
                    except (TypeError, ValueError, KeyError):
                        logger.warning("Proxy control rejected: invalid PROXY_MODE value=%s", value)
                        return True
            else:
                try:
                    mode_name = mode_map[int(value)]
                except (TypeError, ValueError, KeyError):
                    logger.warning("Proxy control rejected: invalid PROXY_MODE value=%s", value)
                    return True

            if self._loop is None:
                logger.warning("Proxy control rejected: event loop is not available")
                return True

            if self._loop.is_closed():
                logger.warning("Proxy control rejected: event loop is closed")
                return True

            loop = self._loop

            def _schedule() -> None:
                task = loop.create_task(self._apply_proxy_mode(mode_name), name="apply_proxy_mode")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

            try:
                loop.call_soon_threadsafe(_schedule)
            except RuntimeError as exc:
                logger.warning("Proxy control rejected: failed to schedule mode apply (%s)", exc)
            return True
        return False

    async def _apply_proxy_mode(self, mode_name: str) -> None:
        if self.proxy is None:
            return
        applied = await self.proxy.mode_manager.apply_configured_mode(mode_name)
        if applied:
            logger.info("Proxy mode set from HA control: %s", mode_name)
            if self.status_publisher is not None:
                self.status_publisher._publish()

    async def _on_frame(self, data: dict[str, Any]) -> None:
        """Handle parsed frame from proxy server.

        This callback is called for each frame received from the Box.
        It validates device_id, records frame in status publisher,
        and processes the frame through FrameProcessor.
        """
        if not data:
            return

        frame_device_id = data.get("_device_id")
        table = data.get("_table")
        if not table or not frame_device_id:
            return

        if table in ("IsNewSet", "IsNewWeather", "IsNewFW"):
            table = "tbl_actual"
            data["_table"] = "tbl_actual"

        # Device ID validation and learning
        if self.device_id_manager:
            if self.device_id_manager.device_id is None:
                # First frame with device_id - save it
                self.device_id_manager.save(frame_device_id)
                logger.info("Device ID set from first frame: %s", frame_device_id)
            elif not self.device_id_manager.validate(frame_device_id):
                # Mismatch - log warning and ignore frame
                logger.warning(
                    "Device ID mismatch: expected %s, got %s",
                    self.device_id_manager.device_id,
                    frame_device_id,
                )
                return

        # Record frame in status publisher
        if self.status_publisher:
            self.status_publisher.record_frame(frame_device_id, table)

        # Process frame through FrameProcessor (publishes to MQTT)
        if self.frame_processor:
            try:
                await self.frame_processor.process(frame_device_id, table, data)
            except Exception as exc:
                logger.error("Frame processing error: %s", exc)

    async def shutdown(self) -> None:
        """Execute graceful shutdown sequence (reverse of startup)."""
        logger.info("OIG Proxy v2 shutting down...")

        # Cancel MQTT health check
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            logger.info("MQTT health check stopped")

        # 1. Stop ProxyServer
        if self.proxy:
            await self.proxy.stop()
            logger.info("ProxyServer stopped")

        # 2. Stop TelemetryCollector
        if self.telemetry_collector and self.telemetry_collector.task:
            self.telemetry_collector.task.cancel()
            try:
                await self.telemetry_collector.task
            except asyncio.CancelledError:
                pass
            logger.info("TelemetryCollector stopped")

        # 3. Stop ProxyStatusPublisher
        if self.status_publisher:
            self.status_publisher.stop()
            logger.info("ProxyStatusPublisher stopped")

        # Cancel all background tasks
        if self._tasks:
            for task in list(self._tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        # 4. Stop TwinControlHandler
        if self.twin_handler:
            await self.twin_handler.stop()
            logger.info("TwinControlHandler stopped")

        # 5. Disconnect MQTT
        if self.mqtt:
            self.mqtt.disconnect()
            logger.info("MQTT disconnected")

        logger.info("OIG Proxy v2 shutdown complete")

    async def run(self) -> None:
        """Run the proxy application until stop signal."""
        # Setup signal handlers
        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received")
            self._stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        # Wait for stop signal
        try:
            await self._stop_event.wait()
        finally:
            await self.shutdown()


def main() -> None:
    """Entry point for OIG Proxy v2."""
    config = Config()
    configure_logging(config.log_level)

    app = ProxyApp(config)

    async def run_app() -> None:
        startup_ok = await app.startup()
        if not startup_ok:
            logger.error("Startup failed, exiting")
            sys.exit(1)
        await app.run()

    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
