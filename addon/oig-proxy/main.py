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
from collections import deque
from copy import deepcopy
import logging
import math
import signal
import sys
import time
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from config import Config
from capture.frame_capture import FrameCapture
from capture.pcap_capture import PcapCapture
from device_id import DeviceIdManager
from logging_config import configure_logging
from mqtt.client import MQTTClient
from mqtt.status import ProxyStatusPublisher
from protocol.ver_profile import ProfileStatus
from proxy.server import ProxyServer
from sensor.loader import SensorMapLoader
from sensor.processor import FrameProcessor
from sensor.confirmed_store import ConfirmedStateStore
from parameter_refresh import ParameterRefreshScheduler
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
        self._setting_results_pending: deque[dict[str, Any]] = deque()
        self._setting_results_closed = False

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
        self.frame_capture: FrameCapture | None = None
        self.pcap_capture: PcapCapture | None = None
        self.proxy: ProxyServer | None = None

        # Background tasks
        self._tasks: set[asyncio.Task[Any]] = set()
        self._health_task: asyncio.Task[Any] | None = None
        self._controls_discovery_marker: tuple[str, int] | None = None
        self.confirmed_store: ConfirmedStateStore | None = None
        self._recovery_task: asyncio.Task[Any] | None = None
        self._recovery_requested = False
        cache_path = getattr(config, "confirmed_state_path", "")
        self._confirmed_state_path = str(cache_path) if isinstance(cache_path, (str, Path)) else ""
        self.parameter_refresh: ParameterRefreshScheduler | None = None

    def _configured_state_path(self, name: str) -> Path | None:
        value = getattr(self.config, name, "")
        return Path(value) if isinstance(value, (str, Path)) and value else None

    def _box_control_enabled(self) -> bool:
        return getattr(self.config, "control_mqtt_enabled", False) is True

    async def startup(self) -> bool:
        """Execute startup sequence. Returns True if successful."""
        logger.info("OIG Proxy v2 starting up...")
        start_time = time.time()
        self._loop = asyncio.get_running_loop()

        # 1. Configure logging (already done in main())
        logger.info("Logging configured (level=%s)", self.config.log_level)

        # 2. Load device_id
        device_id_manager = DeviceIdManager()
        self.device_id_manager = device_id_manager
        device_id = device_id_manager.load()
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

        # Restore observations before a reconnect callback or first BOX frame can publish.
        if self.mqtt is not None:
            self._create_frame_processor(device_id)
            self.mqtt.set_recovery_callback(
                self._request_state_recovery,
                birth_topic=getattr(self.config, "ha_mqtt_birth_topic", "homeassistant/status"),
                birth_payload=getattr(self.config, "ha_mqtt_birth_payload", "online"),
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

        if mqtt_client is not None and mqtt_client.is_ready() and self.frame_processor is not None:
            logger.info("FrameProcessor ready for lazy discovery from live frames")
            if device_id:
                if self.frame_processor.publish_all_discovery(device_id):
                    self._controls_discovery_marker = (device_id, getattr(mqtt_client, "discovery_generation", 0))
                logger.info("Published full discovery for known device_id=%s", device_id)

        # Create twin components
        self.twin_queue = TwinQueue()

        if self.config.telemetry_enabled:
            self.telemetry_collector = TelemetryCollector(
                interval_s=self.config.telemetry_interval_s,
                version=self.config.version,
                telemetry_enabled=self.config.telemetry_enabled,
                telemetry_mqtt_broker=self.config.telemetry_mqtt_broker,
                telemetry_interval_s=self.config.telemetry_interval_s,
                device_id=mqtt_device_id,
                mqtt_namespace=self.config.mqtt_namespace,
                mqtt_publisher=self.mqtt,
                get_mode=lambda: (
                    self.proxy.mode_manager.configured_mode
                    if self.proxy and self.proxy.mode_manager.configured_mode == "hybrid"
                    else (self.proxy.mode_manager.runtime_mode.value if self.proxy else "offline")
                ),
                get_configured_mode=lambda: str(self.proxy.mode_manager.configured_mode) if self.proxy else "online",
                get_box_connected=lambda: self.proxy.is_box_connected() if self.proxy else False,
                get_box_peer=lambda: self.proxy.box_peer if self.proxy else None,
                get_uptime_s=lambda: self.proxy.uptime_s() if self.proxy else 0.0,
                get_frames_received=lambda: self.proxy.frames_received if self.proxy else 0,
                get_frames_forwarded=lambda: self.proxy.frames_forwarded if self.proxy else 0,
                get_cloud_connects=lambda: self.proxy.cloud_connects if self.proxy else 0,
                get_cloud_disconnects=lambda: self.proxy.cloud_disconnects if self.proxy else 0,
                get_cloud_timeouts=lambda: self.proxy.cloud_timeouts if self.proxy else 0,
                get_cloud_errors=lambda: self.proxy.cloud_errors if self.proxy else 0,
                get_cloud_session_connected=lambda: self.proxy.is_cloud_connected() if self.proxy else False,
                consume_set_commands=lambda: self._consume_twin_commands(),
                get_background_tasks=lambda: self._tasks,
            )
            self.telemetry_collector.init()

        twin_delivery = TwinDelivery(
            self.twin_queue,
            self.mqtt,
            telemetry_collector=self.telemetry_collector,
            on_result=self._on_setting_result,
            on_profile_change=self._on_ver_profile_change,
            profile_state_path=self._configured_state_path("control_ver_profile_state_path"),
            counter_state_path=self._configured_state_path("control_counter_state_path"),
            box_writes_enabled=self._box_control_enabled(),
        )
        self.twin_delivery = twin_delivery

        # 5. Start TwinControlHandler (also restored lazily after a broker outage).
        await self._ensure_control_handler(mqtt_device_id)

        # 6. Start ProxyStatusPublisher
        if self.mqtt is not None:
            self.status_publisher = ProxyStatusPublisher(
                mqtt=self.mqtt,
                interval=self.config.proxy_status_interval,
                proxy_device_id=self.config.proxy_device_id,
                sensor_loader=self.sensor_loader,
                get_configured_mode=(
                    lambda: self.proxy.mode_manager.configured_mode if self.proxy else "online"
                ),
                get_cloud_connects=lambda: self.proxy.cloud_connects if self.proxy else 0,
                get_cloud_disconnects=(
                    lambda: self.proxy.cloud_disconnects if self.proxy else 0
                ),
                get_cloud_timeouts=lambda: self.proxy.cloud_timeouts if self.proxy else 0,
                get_cloud_errors=lambda: self.proxy.cloud_errors if self.proxy else 0,
                initial_device_id=device_id,
                get_control_inflight=self._control_inflight_snapshot,
                get_control_queue_len=lambda: self.twin_queue.size() if self.twin_queue else 0,
                get_ver_profile_status=lambda: (
                    twin_delivery.profile_status(device_id_manager.device_id or "")
                    if self._box_control_enabled() else ProfileStatus(None, "control_disabled", 0)
                ),
            )
            self._drain_setting_results()
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
        if self.telemetry_collector is not None:
            class _TelemetryLogHandler(logging.Handler):
                def __init__(self, collector: TelemetryCollector) -> None:
                    super().__init__(level=logging.NOTSET)
                    self._collector = collector

                def emit(self, record: logging.LogRecord) -> None:
                    self._collector.record_log_entry(record)

            logging.getLogger().addHandler(_TelemetryLogHandler(self.telemetry_collector))

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
        if self.config.capture_payloads:
            self.frame_capture = FrameCapture(
                db_path=self.config.capture_db_path,
                capture_raw_bytes=self.config.capture_raw_bytes,
                retention_days=self.config.capture_retention_days,
            )
            self.frame_capture.start()
            logger.info(
                "Frame payload capture enabled (db=%s raw_bytes=%s)",
                self.config.capture_db_path,
                self.config.capture_raw_bytes,
            )
        else:
            logger.warning("Frame payload capture disabled (CAPTURE_PAYLOADS=false)")

        if self.config.capture_pcap:
            self.pcap_capture = PcapCapture(
                port=self.config.proxy_port,
                pcap_path=self.config.capture_pcap_path,
                interface=self.config.capture_pcap_interface,
                max_size_mb=self.config.capture_pcap_max_size_mb,
            )
            await self.pcap_capture.start_async()
            logger.info(
                "PCAP capture enabled (path=%s interface=%s max_size_mb=%s)",
                self.config.capture_pcap_path,
                self.config.capture_pcap_interface,
                self.config.capture_pcap_max_size_mb,
            )
        else:
            logger.warning("PCAP capture disabled (CAPTURE_PCAP=false)")

        if self.config.log_level == "TRACE" and not self.config.capture_pcap:
            logger.warning(
                "TRACE level active without PCAP capture; enable CAPTURE_PCAP for full TCP trace"
            )

        self.proxy = ProxyServer(
            config=self.config,
            on_frame=self._on_frame,
            on_confirmed_setting=self._on_confirmed_setting,
            twin_delivery=self.twin_delivery,
            frame_capture=self.frame_capture,
            telemetry_collector=self.telemetry_collector,
            device_id_provider=lambda: device_id_manager.device_id,
        )
        if self.telemetry_collector is not None:
            self.proxy.mode_manager.on_hybrid_transition = self._on_hybrid_transition
        await self.proxy.start()
        logger.info("ProxyServer started on %s:%s", self.config.proxy_host, self.config.proxy_port)

        # Start MQTT health check
        if mqtt_client is not None:
            self._health_task = asyncio.create_task(
                mqtt_client.health_check_loop(mqtt_device_id),
                name="mqtt_health",
            )

        elapsed = time.time() - start_time
        logger.info("OIG Proxy v2 startup complete in %.2fs", elapsed)
        self._start_parameter_refresh()
        result_task = asyncio.create_task(
            self._setting_result_maintenance(), name="setting_result_maintenance")
        self._tasks.add(result_task)
        result_task.add_done_callback(self._tasks.discard)
        if mqtt_ok:
            self._request_state_recovery()
        return True

    async def _setting_result_maintenance(self) -> None:
        """Resolve result deadlines and profile expiry even without BOX traffic."""
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            if self._stop_event.is_set():
                return
            try:
                poll = getattr(self.twin_delivery, "poll_timeouts", None)
                if callable(poll):
                    poll()  # pylint: disable=not-callable
                self._drain_setting_results()
                if self.status_publisher is not None:
                    self.status_publisher.flush_setting_results()
                    self.status_publisher.publish_ver_profile_change()
            except Exception:
                logger.exception("Local control housekeeping deferred")

    def _on_ver_profile_change(self) -> None:
        """Schedule publication outside the learner callback."""
        loop = self._loop
        publisher = self.status_publisher
        if loop is None or loop.is_closed() or publisher is None or self._stop_event.is_set():
            return
        try:
            loop.call_soon_threadsafe(publisher.publish_ver_profile_change)
        except RuntimeError:
            logger.debug("Ignoring ver profile change after event-loop shutdown")

    def _on_setting_result(self, event: dict[str, Any]) -> None:
        """Freeze terminal evidence and marshal Paho callbacks to the app owner."""
        loop = self._loop
        if self._setting_results_closed or loop is None or loop.is_closed():
            return
        snapshot = deepcopy(event)
        try:
            owner = asyncio.get_running_loop() is loop
        except RuntimeError:
            owner = False
        if owner:
            self._deliver_setting_result(snapshot)
        else:
            try:
                loop.call_soon_threadsafe(self._deliver_setting_result, snapshot)
            except RuntimeError:
                logger.debug("Ignoring result after event-loop shutdown")

    def _deliver_setting_result(self, event: dict[str, Any]) -> None:
        if self._setting_results_closed:
            return
        self._setting_results_pending.append(event)
        self._drain_setting_results()

    def _drain_setting_results(self) -> None:
        if self.status_publisher is None:
            return
        while self._setting_results_pending:
            event = self._setting_results_pending[0]
            try:
                self.status_publisher.record_setting_result(event)
            except Exception:
                logger.exception("Setting result publication deferred")
                return
            self._setting_results_pending.popleft()

    def _control_inflight_snapshot(self) -> str:
        if self.twin_delivery is None:
            return ""
        getter = getattr(self.twin_delivery, "control_inflight_snapshot", None)
        value = getter() if callable(getter) else ""  # pylint: disable=not-callable
        return value if isinstance(value, str) else ""

    def _create_frame_processor(self, device_id: str | None) -> None:
        """Load an identity-bound observation cache without learning identity from it."""
        if self.mqtt is None or self.sensor_loader is None:
            return
        path = self._confirmed_state_path
        if device_id and device_id != "unknown" and path:
            self.confirmed_store = ConfirmedStateStore(
                Path(path), device_id=device_id, namespace=self.config.mqtt_namespace)
        if self.confirmed_store is not None:
            self.frame_processor = FrameProcessor(
                self.mqtt, self.sensor_loader,
                proxy_device_id=self.config.proxy_device_id,
                state_store=self.confirmed_store,
            )
        else:
            self.frame_processor = FrameProcessor(
                self.mqtt, self.sensor_loader, proxy_device_id=self.config.proxy_device_id)

    def _request_state_recovery(self) -> None:
        """Marshal Paho callbacks onto the application event-loop owner."""
        loop = self._loop
        if loop is None or loop.is_closed() or self._stop_event.is_set():
            return
        try:
            loop.call_soon_threadsafe(self._schedule_state_recovery)
        except RuntimeError:
            logger.debug("Ignoring state recovery after event-loop shutdown")

    def _schedule_state_recovery(self) -> None:
        if self._stop_event.is_set():
            return
        self._recovery_requested = True
        if self._recovery_task is None or self._recovery_task.done():
            self._recovery_task = asyncio.create_task(
                self._run_state_recovery(), name="confirmed_state_recovery")
            self._tasks.add(self._recovery_task)
            self._recovery_task.add_done_callback(self._tasks.discard)

    async def _run_state_recovery(self) -> None:
        delay = 0.25
        while self._recovery_requested and not self._stop_event.is_set():
            self._recovery_requested = False
            try:
                recovered = await self._recover_confirmed_state()
            except Exception:  # Recovery must not terminate BOX/cloud transport.
                logger.exception("Confirmed state recovery failed")
                recovered = False
            if not recovered and not self._stop_event.is_set():
                self._recovery_requested = True
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            else:
                delay = 0.25

    async def _recover_confirmed_state(self) -> bool:
        """Replay confirmed data without a frame callback, device online or a command."""
        if self.mqtt is None or not self.mqtt.is_ready():
            return False
        manager = self.device_id_manager
        device_id = manager.device_id if manager is not None else None
        await self._ensure_control_handler(device_id or "unknown")
        self._start_parameter_refresh()
        status_ok = True
        if self.status_publisher is not None:
            self._drain_setting_results()
            self.status_publisher.flush_setting_results()
            status_ok = self.status_publisher._publish()
        if not device_id:
            return status_ok  # BOX identity must come from the manager or a live frame.
        if self.frame_processor is None:
            return False
        discovery_ok = self.frame_processor.publish_all_discovery(device_id)
        state_ok = self.frame_processor.replay_confirmed_states()
        if discovery_ok:
            self._controls_discovery_marker = (device_id, self.mqtt.discovery_generation)
        if discovery_ok and state_ok:
            logger.info("Restored confirmed MQTT states for device_id=%s", device_id)
        return bool(discovery_ok and state_ok and status_ok)

    def _parameter_refresh_idle(self) -> bool:
        """Daily reads yield to local/cloud writes and require a connected BOX."""
        device_id = self.device_id_manager.device_id if self.device_id_manager else None
        return bool(
            self._box_control_enabled()
            and device_id
            and self.proxy is not None
            and self.proxy.is_box_connected()
            and self.twin_handler is not None
            and self.twin_queue is not None
            and self.twin_delivery is not None
            and not self.twin_delivery.has_pending_or_inflight()
            and self.twin_delivery.profile_status(device_id).profile is not None
        )

    def _queue_parameter_refresh(self, reason: str) -> bool:
        """Admit only the diagnostic SA request through the ordinary validator/audit."""
        if not self._parameter_refresh_idle():
            return False
        manager = self.device_id_manager
        if manager is None or not manager.device_id or self.twin_handler is None or self.twin_queue is None:
            return False
        topic = f"{self.config.mqtt_namespace}/{manager.device_id}/set/tbl_box_prms/SA"
        self.twin_handler._on_message(topic, b"1")
        queued = self.twin_queue.get("tbl_box_prms", "SA", device_id=manager.device_id)
        if queued is not None:
            logger.info("Parameter refresh queued (%s); waiting for BOX confirmation", reason)
            return True
        return False

    def _start_parameter_refresh(self) -> None:
        """Start the separate daily timer; construction/recovery never requests SA."""
        manager = self.device_id_manager
        interval = getattr(self.config, "parameter_refresh_interval_s", 0)
        path = getattr(self.config, "parameter_refresh_state_path", "")
        if (not self._box_control_enabled()
                or self.parameter_refresh is not None or self.twin_handler is None
                or manager is None or not manager.device_id
                or type(interval) not in (int, float) or interval <= 0
                or not isinstance(path, (str, Path)) or not str(path)):
            return
        self.parameter_refresh = ParameterRefreshScheduler(
            Path(path), manager.device_id,
            request=self._queue_parameter_refresh, idle=self._parameter_refresh_idle,
            interval_s=interval,
            after_operations=getattr(self.config, "parameter_refresh_after_operations", frozenset()),
        )
        task = asyncio.create_task(self._parameter_refresh_loop(), name="parameter_refresh")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _parameter_refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(30)
            if self.parameter_refresh is not None and not self._stop_event.is_set():
                try:
                    self.parameter_refresh.poll()
                except Exception:
                    logger.exception("Parameter refresh deferred after scheduler error")

    def _notify_parameter_confirmation(self, table: str, key: str, value: Any,
                                       source_dt: str | None, previous: dict[str, Any] | None) -> None:
        """Notify timer only from an accepted observation, never from cache replay."""
        scheduler = self.parameter_refresh
        if scheduler is None or source_dt is None or self.confirmed_store is None:
            return
        current = self.confirmed_store.snapshot()["tables"].get(table, {}).get("fields", {}).get(key)
        if current is None or current["sourceDT"] != source_dt or current["value"] != value:
            return
        if (table, key) == ("tbl_box_prms", "SA") and value == 1:
            stamp = FrameProcessor._parse_device_time(source_dt)
            if stamp is not None:
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=ZoneInfo("Europe/Prague"))
                scheduler.note_sa_confirmed(stamp.timestamp())
        elif previous is not None and previous.get("value") != value:
            scheduler.note_operation(table, key)

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
        applied = await self.proxy.apply_configured_mode(mode_name)
        if applied:
            logger.info("Proxy mode set from HA control: %s", mode_name)
            if self.status_publisher is not None:
                self.status_publisher._publish()

    def _consume_twin_commands(self) -> list[dict[str, str]]:
        """Return pending twin set commands in v1-compatible format for telemetry.

        Does NOT clear the queue — twin delivery acknowledges items individually.
        """
        if self.twin_queue is None:
            return []
        return [
            {"key": f"{s.table}:{s.key}", "value": str(s.value)}
            for s in self.twin_queue.get_pending()
        ]

    def _on_hybrid_transition(self, state: str, started_at: float, reason: str | None) -> None:
        if self.telemetry_collector is None:
            return
        self.telemetry_collector.record_hybrid_state_end(
            state=state,
            state_since_epoch=started_at,
            ended_at=time.time(),
            mode="hybrid",
            reason=reason,
        )

    async def _ensure_control_handler(self, device_id: str) -> None:
        if self.mqtt is None or not self.mqtt.is_ready() or self.twin_queue is None:
            return
        if self.twin_handler is None:
            self.twin_handler = TwinControlHandler(
                mqtt=self.mqtt, twin_queue=self.twin_queue, device_id=device_id,
                namespace=self.config.mqtt_namespace, proxy_control_handler=self._handle_proxy_control,
                telemetry_collector=self.telemetry_collector, proxy_device_id=self.config.proxy_device_id,
                require_profile=True,
                box_writes_enabled=self._box_control_enabled(),
            )
            await self.twin_handler.start()
        else:
            self.twin_handler.set_device_id(device_id)

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
        status_recorded = False
        if self.device_id_manager:
            if self.device_id_manager.device_id is None:
                self.device_id_manager.save(frame_device_id)
                logger.info("Device ID set from first frame: %s", frame_device_id)
                if self.status_publisher:
                    self.status_publisher.record_frame(frame_device_id, table)
                    self.status_publisher._publish()
                    status_recorded = True
                if self.telemetry_collector:
                    self.telemetry_collector.update_device_id(frame_device_id)
            elif not self.device_id_manager.validate(frame_device_id):
                logger.warning(
                    "Device ID mismatch: expected %s, got %s",
                    self.device_id_manager.device_id,
                    frame_device_id,
                )
                return

        if self.status_publisher and not status_recorded:
            self.status_publisher.record_frame(frame_device_id, table)

        await self._ensure_control_handler(frame_device_id)
        self._start_parameter_refresh()
        if (self.confirmed_store is None
                and self._confirmed_state_path
                and self.device_id_manager is not None
                and self.device_id_manager.device_id):
            self._create_frame_processor(self.device_id_manager.device_id)
            self._request_state_recovery()
        if self.frame_processor and self.mqtt is not None and self.mqtt.is_ready():
            marker = (frame_device_id, getattr(self.mqtt, "discovery_generation", 0))
            if self._controls_discovery_marker != marker:
                if self.frame_processor.publish_all_discovery(frame_device_id):
                    self._controls_discovery_marker = marker

        # Process frame through FrameProcessor (publishes to MQTT)
        if self.frame_processor:
            try:
                await self.frame_processor.process(frame_device_id, table, data)
            except Exception as exc:
                logger.error("Frame processing error: %s", exc)

        if table == "tbl_events" and self.telemetry_collector is not None:
            self.telemetry_collector.record_tbl_event(parsed=data, device_id=frame_device_id)

    async def _on_confirmed_setting(
        self,
        device_id: str,
        table: str,
        key: str,
        value: Any,
        source_dt: str | None = None,
    ) -> None:
        manager = self.device_id_manager
        if (manager is None or not manager.device_id or not device_id
                or not manager.validate(device_id)):
            logger.warning("Confirmed setting ignored: missing or mismatched loaded BOX identity")
            return
        if self.frame_processor is None:
            return
        actual = self._coerce_confirmed_value(value)
        data = {key: actual}
        previous = None
        if self.confirmed_store is not None:
            previous = self.confirmed_store.snapshot()["tables"].get(table, {}).get("fields", {}).get(key)
        if source_dt is not None:
            data["_dt"] = source_dt
        await self.frame_processor.process(device_id, table, data, confirmed_event=True)
        self._notify_parameter_confirmation(table, key, actual, source_dt, previous)

    @staticmethod
    def _coerce_confirmed_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            return value
        try:
            return int(normalized)
        except ValueError:
            pass
        try:
            numeric = float(normalized)
            return numeric if math.isfinite(numeric) else value
        except ValueError:
            return value

    async def shutdown(self) -> None:
        """Execute graceful shutdown sequence (reverse of startup)."""
        logger.info("OIG Proxy v2 shutting down...")
        self._stop_event.set()

        # Cancel MQTT health check
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            await asyncio.gather(self._health_task, return_exceptions=True)
            logger.info("MQTT health check stopped")

        # 1. Stop ProxyServer
        if self.proxy:
            await self.proxy.stop()
            logger.info("ProxyServer stopped")

        # 2. Stop TelemetryCollector
        if self.telemetry_collector and self.telemetry_collector.task:
            self.telemetry_collector.task.cancel()
            await asyncio.gather(self.telemetry_collector.task, return_exceptions=True)
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

        shutdown_delivery = getattr(self.twin_delivery, "shutdown", None)
        if callable(shutdown_delivery):
            shutdown_delivery()  # pylint: disable=not-callable
        self._drain_setting_results()
        if self.status_publisher is not None:
            self.status_publisher.flush_setting_results()

        # 5. Stop capture
        if self.pcap_capture:
            self.pcap_capture.stop()
            logger.info("PcapCapture stopped")

        if self.frame_capture:
            self.frame_capture.stop()
            logger.info("FrameCapture stopped")

        if self.confirmed_store is not None and not self.confirmed_store.flush():
            logger.warning("Confirmed observation cache could not be flushed at shutdown")

        self._setting_results_closed = True

        # 6. Disconnect MQTT
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
