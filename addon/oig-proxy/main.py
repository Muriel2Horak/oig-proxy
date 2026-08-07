#!/usr/bin/env python3
"""OIG Proxy v2 durable runtime composition."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from typing import Any

from capture.frame_capture import FrameCapture
from capture.pcap_capture import PcapCapture
from config import Config
from device_id import DeviceIdManager
from logging_config import configure_logging
from mqtt.client import MQTTClient
from mqtt.status import ProxyStatusPublisher
from proxy.server import ProxyServer
from sensor.loader import SensorMapLoader
from sensor.processor import FrameProcessor
from telemetry.collector import TelemetryCollector
from telemetry.settings_audit import SettingsAuditPublisher
from twin import (
    ControlPolicy,
    RecoveryReport,
    StoreRecordNotFound,
    TwinCommandStore,
    TwinControlHandler,
)
from twin.delivery import TwinCoordinator
from twin.state import ConfirmedSetting

logger = logging.getLogger("oig_proxy_v2")


class _TelemetryLogHandler(logging.Handler):
    """Forward local log records into the bounded telemetry collector."""

    def __init__(self, collector: TelemetryCollector) -> None:
        super().__init__(level=logging.NOTSET)
        self._collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        self._collector.record_log_entry(record)


class ProxyApp:  # pylint: disable=too-many-instance-attributes
    """Own every runtime component and its deterministic lifecycle."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._stop_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._health_task: asyncio.Task[Any] | None = None
        self._deadline_task: asyncio.Task[Any] | None = None
        self._identity_lock = asyncio.Lock()
        self._handler_lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self._telemetry_log_handler: logging.Handler | None = None
        self._shutting_down = False
        self._store_ready = False
        self._last_handler_store_failure_count = 0

        self.device_id_manager: DeviceIdManager | None = None
        self.sensor_loader: SensorMapLoader | None = None
        self.mqtt: MQTTClient | None = None
        self.frame_processor: FrameProcessor | None = None
        self.twin_store: TwinCommandStore | None = None
        self.control_recovery_report: RecoveryReport | None = None
        self.control_degradation_reason: str | None = None
        self.audit_publisher: SettingsAuditPublisher | None = None
        self.twin_coordinator: TwinCoordinator | None = None
        self.twin_handler: TwinControlHandler | None = None
        self.status_publisher: ProxyStatusPublisher | None = None
        self.telemetry_collector: TelemetryCollector | None = None
        self.frame_capture: FrameCapture | None = None
        self.pcap_capture: PcapCapture | None = None
        self.proxy: ProxyServer | None = None

    def _control_policy(self) -> ControlPolicy:
        """Project validated second-based configuration into store milliseconds."""
        return ControlPolicy(
            ack_timeout_ms=int(self.config.control_ack_timeout_s * 1000),
            event_timeout_ms=int(self.config.control_event_timeout_s * 1000),
            pending_ttl_ms=int(self.config.control_command_ttl_s * 1000),
            max_attempts=self.config.control_max_attempts,
        )

    def _mqtt_identity(self) -> str:
        """Return a safe MQTT client identity without the executable unknown sentinel."""
        persisted = (
            self.device_id_manager.device_id
            if self.device_id_manager is not None
            else None
        )
        if persisted and DeviceIdManager.is_safe(persisted):
            return persisted
        fallback = self.config.proxy_device_id
        if not DeviceIdManager.is_safe(fallback):
            raise ValueError("PROXY_DEVICE_ID must be a safe MQTT identity")
        return fallback

    async def _open_control_store(self) -> bool:
        """Open and recover the existing database at offsets 0, 1, and 2 seconds."""
        store = TwinCommandStore(
            self.config.twin_db_path,
            policy=self._control_policy(),
        )
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(1.0)
            try:
                now_ms = time.time_ns() // 1_000_000
                await asyncio.to_thread(store.open, now_ms=now_ms)
                report = await asyncio.to_thread(store.recover, now_ms=now_ms)
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Durable control store recovery attempt %d/3 failed: %s",
                    attempt + 1,
                    error,
                )
                try:
                    await asyncio.to_thread(store.close)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("Durable control store cleanup failed")
                continue
            self.twin_store = store
            self.control_recovery_report = report
            self.control_degradation_reason = None
            self._store_ready = True
            logger.info("Durable control store recovered: %s", report)
            return True
        self.twin_store = None
        self.control_degradation_reason = "durable_control_store_unavailable"
        self._store_ready = False
        return False

    def _track_task(self, coroutine: Any, *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def startup(self) -> bool:  # pylint: disable=too-many-statements
        """Start forwarding with local control attached only after recovery."""
        logger.info("OIG Proxy v2 starting up")
        self._loop = asyncio.get_running_loop()
        identity_path = getattr(self.config, "device_id_path", "/data/device_id.json")
        self.device_id_manager = DeviceIdManager(identity_path)
        persisted_device_id = self.device_id_manager.load()

        store_ready = await self._open_control_store()
        if not store_ready:
            logger.error(
                "Durable control store unavailable; local control disabled while "
                "transparent proxy startup continues"
            )

        try:
            self.sensor_loader = SensorMapLoader(self.config.sensor_map_path)
            self.sensor_loader.load()
            self.mqtt = MQTTClient(
                host=self.config.mqtt_host,
                port=self.config.mqtt_port,
                username=self.config.mqtt_username,
                password=self.config.mqtt_password,
                namespace=self.config.mqtt_namespace,
                qos=self.config.mqtt_qos,
                state_retain=self.config.mqtt_state_retain,
                control_enabled=self.config.control_mqtt_enabled,
            )
            mqtt_identity = self._mqtt_identity()
            mqtt_ok = await asyncio.to_thread(self.mqtt.connect, mqtt_identity)
            if not mqtt_ok:
                logger.warning("MQTT unavailable; proxy continues without control ingress")

            self.frame_processor = FrameProcessor(
                self.mqtt,
                self.sensor_loader,
                proxy_device_id=self.config.proxy_device_id,
            )
            self._start_telemetry(mqtt_identity)
            if store_ready:
                self._compose_durable_control()
                if self.audit_publisher is not None:
                    await self.audit_publisher.replay_pending_async()
            self._start_status_publisher(persisted_device_id)
            await self._start_capture()

            self.proxy = ProxyServer(
                config=self.config,
                on_frame=self._on_frame,
                frame_capture=self.frame_capture,
                telemetry_collector=self.telemetry_collector,
                twin_coordinator=self.twin_coordinator,
                on_valid_device=self._on_valid_device_identity,
                on_committed_confirmation=self._on_committed_confirmation,
            )
            if self.telemetry_collector is not None:
                self.proxy.mode_manager.on_hybrid_transition = (
                    self._on_hybrid_transition
                )
            await self.proxy.start()

            self._deadline_task = self._track_task(
                self._deadline_sweep_loop(),
                name="twin_deadline_sweeper",
            )
            if self.mqtt.is_ready():
                await self._on_mqtt_ready()
            self._health_task = self._track_task(
                self.mqtt.health_check_loop(
                    mqtt_identity,
                    on_ready=self._on_mqtt_ready,
                ),
                name="mqtt_health",
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Runtime startup failed")
            await self.shutdown()
            return False
        logger.info("OIG Proxy v2 startup complete")
        return True

    def _start_telemetry(self, mqtt_identity: str) -> None:
        if not self.config.telemetry_enabled or self.mqtt is None:
            return
        self.telemetry_collector = TelemetryCollector(
            interval_s=self.config.telemetry_interval_s,
            version=self.config.version,
            telemetry_enabled=True,
            telemetry_mqtt_broker=self.config.telemetry_mqtt_broker,
            telemetry_interval_s=self.config.telemetry_interval_s,
            device_id=mqtt_identity,
            mqtt_namespace=self.config.mqtt_namespace,
            mqtt_publisher=self.mqtt,
            get_mode=lambda: (
                self.proxy.mode_manager.runtime_mode.value
                if self.proxy is not None
                else "offline"
            ),
            get_configured_mode=lambda: (
                str(self.proxy.mode_manager.configured_mode)
                if self.proxy is not None
                else "online"
            ),
            get_box_connected=lambda: bool(
                self.proxy and self.proxy.is_box_connected()
            ),
            get_box_peer=lambda: self.proxy.box_peer if self.proxy else None,
            get_uptime_s=lambda: self.proxy.uptime_s() if self.proxy else 0.0,
            get_frames_received=lambda: self.proxy.frames_received if self.proxy else 0,
            get_frames_forwarded=lambda: self.proxy.frames_forwarded if self.proxy else 0,
            get_cloud_connects=lambda: self.proxy.cloud_connects if self.proxy else 0,
            get_cloud_disconnects=lambda: self.proxy.cloud_disconnects if self.proxy else 0,
            get_cloud_timeouts=lambda: self.proxy.cloud_timeouts if self.proxy else 0,
            get_cloud_errors=lambda: self.proxy.cloud_errors if self.proxy else 0,
            get_cloud_session_connected=lambda: bool(
                self.proxy and self.proxy.is_cloud_connected()
            ),
            consume_set_commands=lambda: [],
            get_background_tasks=lambda: self._tasks,
        )
        self.telemetry_collector.init()
        self._telemetry_log_handler = _TelemetryLogHandler(
            self.telemetry_collector
        )
        logging.getLogger().addHandler(self._telemetry_log_handler)
        telemetry_task = self._track_task(
            self.telemetry_collector.loop(),
            name="telemetry_collector",
        )
        self.telemetry_collector.task = telemetry_task

    def _compose_durable_control(self) -> None:
        if self.twin_store is None:
            raise RuntimeError("durable store is unavailable")
        sink = (
            self.telemetry_collector.record_setting_audit_step
            if self.telemetry_collector is not None
            else None
        )
        self.audit_publisher = SettingsAuditPublisher(
            sink,
            acceptance_ledger=self.twin_store,
        )
        self.twin_coordinator = TwinCoordinator(
            self.twin_store,
            control_enabled=self.config.control_mqtt_enabled,
            audit_publisher=self.audit_publisher,
        )

    def _start_status_publisher(self, device_id: str | None) -> None:
        if self.config.proxy_status_interval <= 0:
            return
        if self.mqtt is None or self.sensor_loader is None:
            raise RuntimeError("status publisher dependencies are unavailable")
        self.status_publisher = ProxyStatusPublisher(
            mqtt=self.mqtt,
            interval=self.config.proxy_status_interval,
            proxy_device_id=self.config.proxy_device_id,
            sensor_loader=self.sensor_loader,
            get_configured_mode=lambda: (
                self.proxy.mode_manager.configured_mode
                if self.proxy is not None
                else "online"
            ),
            get_control_status=self._control_status,
            initial_device_id=device_id,
        )
        self._track_task(self.status_publisher.run(), name="status_publisher")

    def _control_status(self) -> tuple[bool, str | None]:
        """Return bounded local-control health for passive status publication."""
        available = bool(self._store_ready and self.twin_coordinator is not None)
        if available:
            return True, None
        return False, self.control_degradation_reason or "control_not_ready"

    async def _degrade_control_runtime(self, reason: str) -> None:
        """Detach failed local control immediately while forwarding stays active."""
        self.control_degradation_reason = reason
        self._store_ready = False
        if self.proxy is not None:
            self.proxy.twin_coordinator = None
        self.twin_coordinator = None
        handler = self.twin_handler
        self.twin_handler = None
        if handler is not None:
            try:
                await handler.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Failed control handler stop during degradation")

    async def _start_capture(self) -> None:
        if self.config.capture_payloads:
            self.frame_capture = FrameCapture(
                db_path=self.config.capture_db_path,
                capture_raw_bytes=self.config.capture_raw_bytes,
                retention_days=self.config.capture_retention_days,
            )
            self.frame_capture.start()
        if self.config.capture_pcap:
            self.pcap_capture = PcapCapture(
                port=self.config.proxy_port,
                pcap_path=self.config.capture_pcap_path,
                interface=self.config.capture_pcap_interface,
                max_size_mb=self.config.capture_pcap_max_size_mb,
            )
            await self.pcap_capture.start_async()

    async def _on_mqtt_ready(self) -> None:
        """Restore discovery cleanup and exact-device subscriptions after connect."""
        manager = self.device_id_manager
        device_id = manager.device_id if manager is not None else None
        if device_id and self.frame_processor is not None:
            self.frame_processor.publish_all_discovery(device_id)
        await self._reconcile_control_handler()

    async def _reconcile_control_handler(self) -> None:
        """Converge subscriptions to one healthy persisted device row."""
        async with self._handler_lock:
            manager = self.device_id_manager
            device_id = manager.device_id if manager is not None else None
            store = self.twin_store
            desired = bool(
                self.config.control_mqtt_enabled
                and self._store_ready
                and device_id
                and self.mqtt is not None
                and self.mqtt.is_ready()
                and store is not None
                and self.twin_coordinator is not None
                and self._loop is not None
            )
            if desired and store is not None and device_id is not None:
                try:
                    row = await asyncio.to_thread(
                        store.read_device,
                        device_id,
                    )
                    desired = row.device_id == device_id
                except (StoreRecordNotFound, ValueError):
                    desired = False
                except Exception as error:  # pylint: disable=broad-exception-caught
                    logger.error("Control device row check failed: %s", error)
                    await self._degrade_control_runtime(
                        "durable_control_runtime_failure"
                    )
                    return

            current = self.twin_handler
            current_device = current.device_id if current is not None else None
            if current is not None and (not desired or current_device != device_id):
                await current.stop()
                self.twin_handler = None
            if not desired or self.twin_handler is not None:
                return
            handler = TwinControlHandler(
                mqtt=self.mqtt,  # type: ignore[arg-type]
                store=self.twin_store,  # type: ignore[arg-type]
                device_id=device_id,  # type: ignore[arg-type]
                control_enabled=True,
                loop=self._loop,  # type: ignore[arg-type]
                namespace=self.config.mqtt_namespace,
                proxy_control_handler=self._handle_proxy_control,
                audit_publisher=self.audit_publisher,
            )
            if await handler.start():
                self.twin_handler = handler

    async def _on_valid_device_identity(
        self,
        device_id: str,
        observed_wire_id: int | None,
        observed_wire_id_set: int | None,
    ) -> bool:
        """Persist and observe only a semantically validated exact identity."""
        if (
            not DeviceIdManager.is_safe(device_id)
            or type(observed_wire_id) is not int
            or type(observed_wire_id_set) is not int
            or observed_wire_id < 0
            or observed_wire_id_set < 0
            or self.device_id_manager is None
            or self.twin_store is None
            or not self._store_ready
        ):
            return False
        async with self._identity_lock:
            bound = self.device_id_manager.device_id
            if bound is not None and bound != device_id:
                logger.warning(
                    "Device identity mismatch: expected %s, got %s",
                    bound,
                    device_id,
                )
                return False
            if bound is None and not self.device_id_manager.save(device_id):
                return False
            try:
                await asyncio.to_thread(
                    self.twin_store.observe_device,
                    device_id=device_id,
                    observed_at_ms=time.time_ns() // 1_000_000,
                    observed_wire_id=observed_wire_id,
                    observed_wire_id_set=observed_wire_id_set,
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error("Durable device observation failed: %s", error)
                await self._degrade_control_runtime(
                    "durable_control_runtime_failure"
                )
                return False
            if self.telemetry_collector is not None:
                self.telemetry_collector.update_device_id(device_id)
            if self.status_publisher is not None:
                self.status_publisher.record_frame(device_id, "identity")
                self.status_publisher._publish()  # pylint: disable=protected-access
            if self.frame_processor is not None and self.mqtt and self.mqtt.is_ready():
                self.frame_processor.publish_all_discovery(device_id)
            await self._reconcile_control_handler()
            return True

    async def _on_frame(self, data: dict[str, Any]) -> None:
        """Publish passive frames without ever establishing device binding."""
        if not data:
            return
        device_id = data.get("_device_id")
        table = data.get("_table")
        if type(device_id) is not str or type(table) is not str:
            return
        manager = self.device_id_manager
        if manager is not None and manager.device_id is not None:
            if not manager.validate(device_id):
                logger.warning("Ignoring frame from mismatched device %s", device_id)
                return
        normalized_table = (
            "tbl_actual"
            if table in {"IsNewSet", "IsNewWeather", "IsNewFW"}
            else table
        )
        frame_data = dict(data)
        frame_data["_table"] = normalized_table
        if self.status_publisher is not None:
            self.status_publisher.record_frame(device_id, normalized_table)
        if self.frame_processor is not None:
            try:
                await self.frame_processor.process(
                    device_id,
                    normalized_table,
                    frame_data,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Frame processing failed")
        if normalized_table == "tbl_events" and self.telemetry_collector is not None:
            self.telemetry_collector.record_tbl_event(
                parsed=frame_data,
                device_id=device_id,
            )

    async def _on_committed_confirmation(
        self,
        confirmation: ConfirmedSetting,
    ) -> None:
        if self.frame_processor is None:
            return
        if not self.frame_processor.publish_confirmed_setting(confirmation):
            logger.warning(
                "Confirmed setting MQTT publication deferred: %s",
                confirmation.command_id,
            )

    async def _deadline_sweep_loop(self) -> None:
        """Sweep deadlines even with ingress disabled and recover bounded failures."""
        while True:
            try:
                if not self._store_ready or self.twin_coordinator is None:
                    if not await self._recover_control_runtime():
                        logger.error(
                            "Durable control runtime remains unavailable; local "
                            "control stays disabled while proxy forwarding continues"
                        )
                else:
                    await self.twin_coordinator.sweep_deadlines()
                    handler = self.twin_handler
                    failures = (
                        handler.store_failure_count if handler is not None else 0
                    )
                    if failures > self._last_handler_store_failure_count:
                        self._last_handler_store_failure_count = failures
                        await self._degrade_control_runtime(
                            "durable_control_runtime_failure"
                        )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error("Deadline sweep failed: %s", error)
                await self._degrade_control_runtime(
                    "durable_control_runtime_failure"
                )
            await asyncio.sleep(1.0)

    async def _recover_control_runtime(self) -> bool:
        """Perform one serialized bounded recovery burst after runtime failure."""
        async with self._recovery_lock:
            if self._store_ready and self.twin_coordinator is not None:
                return True
            await self._degrade_control_runtime(
                "durable_control_runtime_recovering"
            )
            self.audit_publisher = None
            old_store = self.twin_store
            if old_store is not None:
                try:
                    await asyncio.to_thread(old_store.close)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("Runtime store close failed")
            if not await self._open_control_store():
                return False
            self._compose_durable_control()
            if self.audit_publisher is not None:
                await self.audit_publisher.replay_pending_async()
            if self.proxy is not None:
                self.proxy.twin_coordinator = self.twin_coordinator
            await self._reconcile_control_handler()
            return True

    def _handle_proxy_control(self, table: str, key: str, value: str) -> bool:
        if table != "proxy_control" or key != "PROXY_MODE":
            return False
        mode_name = {"0": "online", "1": "hybrid", "2": "offline"}.get(
            value.strip().lower(),
            value.strip().lower(),
        )
        if mode_name not in {"online", "hybrid", "offline"}:
            return True
        loop = self._loop
        if loop is None or loop.is_closed():
            return True

        def schedule() -> None:
            self._track_task(
                self._apply_proxy_mode(mode_name),
                name="apply_proxy_mode",
            )

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            logger.exception("Proxy mode scheduling failed")
        return True

    async def _apply_proxy_mode(self, mode_name: str) -> None:
        if self.proxy is None:
            return
        if await self.proxy.mode_manager.apply_configured_mode(mode_name):
            if self.status_publisher is not None:
                self.status_publisher._publish()  # pylint: disable=protected-access

    def _on_hybrid_transition(
        self,
        state: str,
        started_at: float,
        reason: str | None,
    ) -> None:
        if self.telemetry_collector is not None:
            self.telemetry_collector.record_hybrid_state_end(
                state=state,
                state_since_epoch=started_at,
                ended_at=time.time(),
                mode="hybrid",
                reason=reason,
            )

    async def shutdown(self) -> None:
        """Stop ingress, drain tasks, close durable state, then disconnect MQTT."""
        if self._shutting_down:
            return
        self._shutting_down = True
        if self.proxy is not None:
            await self.proxy.stop()
        if self.twin_handler is not None:
            await self.twin_handler.stop()
            self.twin_handler = None
        for task in tuple(self._tasks):
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
        pending = tuple(
            task for task in self._tasks if task is not asyncio.current_task()
        )
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        if self.status_publisher is not None:
            self.status_publisher.stop()
        if self._telemetry_log_handler is not None:
            logging.getLogger().removeHandler(self._telemetry_log_handler)
            self._telemetry_log_handler = None
        if self.twin_store is not None:
            await asyncio.to_thread(self.twin_store.close)
            self._store_ready = False
        if self.pcap_capture is not None:
            self.pcap_capture.stop()
        if self.frame_capture is not None:
            self.frame_capture.stop()
        if self.mqtt is not None:
            self.mqtt.disconnect()

    async def run(self) -> None:
        """Wait for a process signal and always execute graceful shutdown."""
        loop = asyncio.get_running_loop()

        def signal_handler() -> None:
            self._stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)
        try:
            await self._stop_event.wait()
        finally:
            await self.shutdown()


def main() -> None:
    """Run the configured proxy process."""
    config = Config()
    configure_logging(config.log_level)
    for warning in config.startup_warnings:
        logger.warning("Configuration: %s", warning)
    app = ProxyApp(config)

    async def run_app() -> None:
        if not await app.startup():
            logger.error("Startup failed, exiting")
            sys.exit(1)
        await app.run()

    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
