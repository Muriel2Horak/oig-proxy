"""Focused branch contracts for durable runtime composition."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main as main_module
from main import ProxyApp, _TelemetryLogHandler
from twin.state import ConfirmedSetting


@pytest.fixture
def runtime_config(make_config, tmp_path):
    """Build a complete runtime configuration for branch-focused tests."""
    return make_config(
        version="test",
        device_id_path=str(tmp_path / "device_id.json"),
        twin_db_path=str(tmp_path / "twin.db"),
        capture_payloads=False,
        capture_raw_bytes=False,
        capture_retention_days=7,
        capture_db_path=str(tmp_path / "payloads.db"),
        capture_pcap=False,
        capture_pcap_path=str(tmp_path / "capture.pcap"),
        capture_pcap_interface="lo0",
        capture_pcap_max_size_mb=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mqtt_ready", (False, True))
async def test_startup_composes_every_runtime_boundary(
    runtime_config,
    mqtt_ready: bool,
) -> None:
    app = ProxyApp(runtime_config)
    manager = MagicMock(device_id="DEV01")
    manager.load.return_value = "DEV01"
    loader = MagicMock()
    mqtt = MagicMock()
    mqtt.connect.return_value = mqtt_ready
    mqtt.is_ready.return_value = mqtt_ready
    mqtt.health_check_loop.return_value = "health-loop"
    publisher = MagicMock()
    publisher.replay_pending_async = AsyncMock()
    proxy = MagicMock()
    proxy.start = AsyncMock()
    app._open_control_store = AsyncMock(return_value=True)  # type: ignore[method-assign]
    app._start_telemetry = MagicMock()  # type: ignore[method-assign]
    app._compose_durable_control = MagicMock()  # type: ignore[method-assign]
    app._start_status_publisher = MagicMock()  # type: ignore[method-assign]
    app._start_capture = AsyncMock()  # type: ignore[method-assign]
    app._track_task = MagicMock()  # type: ignore[method-assign]
    app._deadline_sweep_loop = MagicMock(return_value="deadline-loop")  # type: ignore[method-assign]
    app._on_mqtt_ready = AsyncMock()  # type: ignore[method-assign]

    with (
        patch.object(main_module, "DeviceIdManager", return_value=manager),
        patch.object(main_module, "SensorMapLoader", return_value=loader),
        patch.object(main_module, "MQTTClient", return_value=mqtt),
        patch.object(main_module, "FrameProcessor", return_value=MagicMock()),
        patch.object(main_module, "ProxyServer", return_value=proxy),
    ):
        app.audit_publisher = publisher
        assert await app.startup() is True

    loader.load.assert_called_once_with()
    proxy.start.assert_awaited_once_with()
    publisher.replay_pending_async.assert_awaited_once_with()
    if mqtt_ready:
        app._on_mqtt_ready.assert_awaited_once_with()
    else:
        app._on_mqtt_ready.assert_not_awaited()
    assert app._track_task.call_count == 2


@pytest.mark.asyncio
async def test_startup_without_store_keeps_proxy_transparent(
    runtime_config,
) -> None:
    app = ProxyApp(runtime_config)
    app._open_control_store = AsyncMock(return_value=False)  # type: ignore[method-assign]
    app._start_telemetry = MagicMock()  # type: ignore[method-assign]
    app._compose_durable_control = MagicMock()  # type: ignore[method-assign]
    app._start_status_publisher = MagicMock()  # type: ignore[method-assign]
    app._start_capture = AsyncMock()  # type: ignore[method-assign]
    app._track_task = MagicMock()  # type: ignore[method-assign]
    app._deadline_sweep_loop = MagicMock(return_value="deadline-loop")  # type: ignore[method-assign]
    manager = MagicMock(load=MagicMock(return_value=None))
    loader = MagicMock()
    mqtt = MagicMock()
    mqtt.connect.return_value = False
    mqtt.is_ready.return_value = False
    mqtt.health_check_loop.return_value = "health-loop"
    proxy = MagicMock(start=AsyncMock())
    with (
        patch.object(main_module, "DeviceIdManager", return_value=manager),
        patch.object(main_module, "SensorMapLoader", return_value=loader),
        patch.object(main_module, "MQTTClient", return_value=mqtt),
        patch.object(main_module, "FrameProcessor", return_value=MagicMock()),
        patch.object(main_module, "ProxyServer", return_value=proxy),
    ):
        assert await app.startup() is True
    app._compose_durable_control.assert_not_called()
    proxy.start.assert_awaited_once_with()
    assert app.twin_coordinator is None


@pytest.mark.asyncio
async def test_startup_failure_during_network_composition_shuts_down(
    runtime_config,
) -> None:

    failing = ProxyApp(runtime_config)
    failing._open_control_store = AsyncMock(return_value=True)  # type: ignore[method-assign]
    failing.shutdown = AsyncMock()  # type: ignore[method-assign]
    loader = MagicMock()
    loader.load.side_effect = RuntimeError("broken map")
    with (
        patch.object(main_module, "DeviceIdManager", return_value=MagicMock(load=MagicMock(return_value=None))),
        patch.object(main_module, "SensorMapLoader", return_value=loader),
    ):
        assert await failing.startup() is False
    failing.shutdown.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_task_tracking_discards_completed_task(runtime_config) -> None:
    app = ProxyApp(runtime_config)

    async def work() -> int:
        return 7

    task = app._track_task(work(), name="tracked")
    assert task in app._tasks
    assert await task == 7
    await asyncio.sleep(0)
    assert task not in app._tasks


def test_telemetry_handler_and_live_getters_cover_absent_and_present_proxy(
    runtime_config,
) -> None:
    runtime_config.telemetry_enabled = True
    app = ProxyApp(runtime_config)
    app.mqtt = MagicMock()
    collector = MagicMock()
    collector.loop.return_value = "telemetry-loop"
    app._track_task = MagicMock(return_value="task")  # type: ignore[method-assign]

    with patch.object(main_module, "TelemetryCollector", return_value=collector) as factory:
        app._start_telemetry("DEV01")

    handler = _TelemetryLogHandler(collector)
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    handler.emit(record)
    collector.record_log_entry.assert_called_once_with(record)

    getters = factory.call_args.kwargs
    assert getters["get_mode"]() == "offline"
    assert getters["get_configured_mode"]() == "online"
    assert getters["get_box_connected"]() is False
    assert getters["get_box_peer"]() is None
    assert getters["get_uptime_s"]() == 0.0
    for name in (
        "get_frames_received",
        "get_frames_forwarded",
        "get_cloud_connects",
        "get_cloud_disconnects",
        "get_cloud_timeouts",
        "get_cloud_errors",
    ):
        assert getters[name]() == 0
    assert getters["get_cloud_session_connected"]() is False
    assert getters["consume_set_commands"]() == []
    assert getters["get_background_tasks"]() is app._tasks

    mode_manager = SimpleNamespace(runtime_mode=SimpleNamespace(value="hybrid"), configured_mode="offline")
    app.proxy = MagicMock(
        mode_manager=mode_manager,
        box_peer="box:1",
        frames_received=1,
        frames_forwarded=2,
        cloud_connects=3,
        cloud_disconnects=4,
        cloud_timeouts=5,
        cloud_errors=6,
    )
    app.proxy.is_box_connected.return_value = True
    app.proxy.is_cloud_connected.return_value = True
    app.proxy.uptime_s.return_value = 7.0
    assert getters["get_mode"]() == "hybrid"
    assert getters["get_configured_mode"]() == "offline"
    assert getters["get_box_connected"]() is True
    assert getters["get_box_peer"]() == "box:1"
    assert getters["get_uptime_s"]() == 7.0
    assert getters["get_frames_received"]() == 1
    assert getters["get_frames_forwarded"]() == 2
    assert getters["get_cloud_connects"]() == 3
    assert getters["get_cloud_disconnects"]() == 4
    assert getters["get_cloud_timeouts"]() == 5
    assert getters["get_cloud_errors"]() == 6
    assert getters["get_cloud_session_connected"]() is True

    logging.getLogger().removeHandler(app._telemetry_log_handler)


def test_telemetry_and_status_skip_or_fail_without_dependencies(runtime_config) -> None:
    app = ProxyApp(runtime_config)
    runtime_config.telemetry_enabled = False
    app._start_telemetry("DEV01")
    assert app.telemetry_collector is None

    runtime_config.telemetry_enabled = True
    app._start_telemetry("DEV01")
    assert app.telemetry_collector is None

    runtime_config.proxy_status_interval = 0
    app._start_status_publisher(None)
    runtime_config.proxy_status_interval = 5
    with pytest.raises(RuntimeError, match="dependencies"):
        app._start_status_publisher(None)


def test_status_publisher_getter_tracks_proxy(runtime_config) -> None:
    app = ProxyApp(runtime_config)
    app.mqtt = MagicMock()
    app.sensor_loader = MagicMock()
    app._track_task = MagicMock()  # type: ignore[method-assign]
    publisher = MagicMock()
    publisher.run.return_value = "status-loop"
    with patch.object(main_module, "ProxyStatusPublisher", return_value=publisher) as factory:
        app._start_status_publisher("DEV01")
    getter = factory.call_args.kwargs["get_configured_mode"]
    assert getter() == "online"
    app.proxy = MagicMock(mode_manager=SimpleNamespace(configured_mode="hybrid"))
    assert getter() == "hybrid"
    control_getter = factory.call_args.kwargs["get_control_status"]
    assert control_getter() == (False, "control_not_ready")
    app._store_ready = True
    app.twin_coordinator = MagicMock()
    assert control_getter() == (True, None)


@pytest.mark.asyncio
async def test_capture_starts_both_optional_boundaries(runtime_config) -> None:
    runtime_config.capture_payloads = True
    runtime_config.capture_pcap = True
    app = ProxyApp(runtime_config)
    frame_capture = MagicMock()
    pcap_capture = MagicMock()
    pcap_capture.start_async = AsyncMock()
    with (
        patch.object(main_module, "FrameCapture", return_value=frame_capture),
        patch.object(main_module, "PcapCapture", return_value=pcap_capture),
    ):
        await app._start_capture()
    frame_capture.start.assert_called_once_with()
    pcap_capture.start_async.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_mqtt_ready_and_reconciliation_cover_handler_lifecycle(runtime_config) -> None:
    runtime_config.control_mqtt_enabled = True
    app = ProxyApp(runtime_config)
    app._loop = asyncio.get_running_loop()
    app._store_ready = True
    app.mqtt = MagicMock()
    app.mqtt.is_ready.return_value = True
    app.frame_processor = MagicMock()
    app.device_id_manager = MagicMock(device_id="DEV01")
    app.twin_store = MagicMock()
    app.twin_store.read_device.return_value = SimpleNamespace(device_id="DEV01")
    app.twin_coordinator = MagicMock()
    handler = MagicMock(device_id="DEV01")
    handler.start = AsyncMock(return_value=False)

    with patch.object(main_module, "TwinControlHandler", return_value=handler):
        await app._on_mqtt_ready()
    app.frame_processor.publish_all_discovery.assert_called_once_with("DEV01")
    assert app.twin_handler is None

    current = MagicMock(device_id="OTHER")
    current.stop = AsyncMock()
    app.twin_handler = current
    with patch.object(main_module, "TwinControlHandler", return_value=handler):
        await app._reconcile_control_handler()
    current.stop.assert_awaited_once_with()
    assert app.twin_handler is None

    app.twin_store.read_device.side_effect = RuntimeError("health")
    await app._reconcile_control_handler()
    assert app.twin_handler is None

    app.device_id_manager = None
    app.frame_processor.reset_mock()
    await app._on_mqtt_ready()
    app.frame_processor.publish_all_discovery.assert_not_called()


@pytest.mark.asyncio
async def test_identity_observation_covers_failures_and_side_effects(runtime_config) -> None:
    app = ProxyApp(runtime_config)
    app._loop = asyncio.get_running_loop()
    app._store_ready = True
    app.device_id_manager = MagicMock(device_id="OTHER")
    app.twin_store = MagicMock()
    assert await app._on_valid_device_identity("DEV01", 1, 2) is False

    app.device_id_manager.device_id = None
    app.device_id_manager.save.return_value = False
    assert await app._on_valid_device_identity("DEV01", 1, 2) is False

    app.device_id_manager.save.return_value = True
    app.twin_store.observe_device.side_effect = RuntimeError("disk")
    assert await app._on_valid_device_identity("DEV01", 1, 2) is False
    assert app._store_ready is False

    app._store_ready = True
    app.twin_store.observe_device.side_effect = None
    app.telemetry_collector = MagicMock()
    app.status_publisher = MagicMock()
    app.frame_processor = MagicMock()
    app.mqtt = MagicMock()
    app.mqtt.is_ready.return_value = True
    app._reconcile_control_handler = AsyncMock()  # type: ignore[method-assign]
    assert await app._on_valid_device_identity("DEV01", 1, 2) is True
    app.telemetry_collector.update_device_id.assert_called_once_with("DEV01")
    app.status_publisher.record_frame.assert_called_once_with("DEV01", "identity")
    app.status_publisher._publish.assert_called_once_with()
    app.frame_processor.publish_all_discovery.assert_called_once_with("DEV01")


@pytest.mark.asyncio
async def test_frame_and_confirmation_callbacks_cover_all_optional_sinks(
    runtime_config,
) -> None:
    app = ProxyApp(runtime_config)
    await app._on_frame({})
    await app._on_frame({"_device_id": 1, "_table": "tbl_actual"})
    app.device_id_manager = MagicMock(device_id="DEV01")
    app.device_id_manager.validate.return_value = False
    await app._on_frame({"_device_id": "OTHER", "_table": "tbl_actual"})

    app.device_id_manager.validate.return_value = True
    app.status_publisher = MagicMock()
    app.frame_processor = MagicMock()
    app.frame_processor.process = AsyncMock(side_effect=RuntimeError("sink"))
    app.telemetry_collector = MagicMock()
    await app._on_frame({"_device_id": "DEV01", "_table": "tbl_events"})
    app.telemetry_collector.record_tbl_event.assert_called_once()
    await app._on_frame({"_device_id": "DEV01", "_table": "IsNewFW"})
    app.status_publisher.record_frame.assert_called_with("DEV01", "tbl_actual")

    confirmation = ConfirmedSetting(
        "command", "audit", "evidence", "DEV01", "tbl", "key", "1", 1
    )
    empty = ProxyApp(runtime_config)
    await empty._on_committed_confirmation(confirmation)
    app.frame_processor.publish_confirmed_setting.return_value = False
    await app._on_committed_confirmation(confirmation)
    app.frame_processor.publish_confirmed_setting.return_value = True
    await app._on_committed_confirmation(confirmation)


@pytest.mark.asyncio
async def test_deadline_loop_recovers_marks_failures_and_propagates_cancel(
    runtime_config,
) -> None:
    missing = ProxyApp(runtime_config)
    missing._recover_control_runtime = AsyncMock(return_value=False)  # type: ignore[method-assign]
    with patch.object(
        main_module.asyncio,
        "sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    ):
        with pytest.raises(asyncio.CancelledError):
            await missing._deadline_sweep_loop()
    assert not missing._stop_event.is_set()

    healthy = ProxyApp(runtime_config)
    healthy._store_ready = True
    healthy.twin_coordinator = MagicMock()
    healthy.twin_coordinator.sweep_deadlines = AsyncMock()
    healthy.twin_handler = MagicMock(store_failure_count=2)
    with patch.object(main_module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await healthy._deadline_sweep_loop()
    assert healthy._store_ready is False
    assert healthy.control_degradation_reason == "durable_control_runtime_failure"

    broken = ProxyApp(runtime_config)
    broken._store_ready = True
    broken.twin_coordinator = MagicMock()
    broken.twin_coordinator.sweep_deadlines = AsyncMock(side_effect=RuntimeError("db"))
    with patch.object(main_module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await broken._deadline_sweep_loop()
    assert broken._store_ready is False
    assert broken.control_degradation_reason == "durable_control_runtime_failure"


@pytest.mark.asyncio
async def test_runtime_recovery_covers_ready_failure_and_success(runtime_config) -> None:
    ready = ProxyApp(runtime_config)
    ready._store_ready = True
    ready.twin_coordinator = MagicMock()
    assert await ready._recover_control_runtime() is True

    failed = ProxyApp(runtime_config)
    failed.twin_handler = MagicMock()
    failed.twin_handler.stop = AsyncMock()
    failed.twin_store = MagicMock()
    failed.twin_store.close.side_effect = RuntimeError("close")
    failed._open_control_store = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert await failed._recover_control_runtime() is False
    assert failed.twin_handler is None

    recovered = ProxyApp(runtime_config)
    recovered.twin_store = MagicMock()
    recovered._open_control_store = AsyncMock(return_value=True)  # type: ignore[method-assign]
    recovered._compose_durable_control = MagicMock()  # type: ignore[method-assign]
    recovered._reconcile_control_handler = AsyncMock()  # type: ignore[method-assign]
    recovered.audit_publisher = MagicMock()
    recovered.audit_publisher.replay_pending_async = AsyncMock()
    recovered.proxy = MagicMock()
    recovered.twin_coordinator = MagicMock()
    assert await recovered._recover_control_runtime() is True
    assert recovered.proxy.twin_coordinator is recovered.twin_coordinator


@pytest.mark.asyncio
async def test_proxy_mode_control_and_hybrid_telemetry(runtime_config) -> None:
    app = ProxyApp(runtime_config)
    assert app._handle_proxy_control("wrong", "PROXY_MODE", "1") is False
    assert app._handle_proxy_control("proxy_control", "wrong", "1") is False
    assert app._handle_proxy_control("proxy_control", "PROXY_MODE", "unknown") is True
    assert app._handle_proxy_control("proxy_control", "PROXY_MODE", "1") is True

    app._loop = MagicMock()
    app._loop.is_closed.return_value = False
    app._loop.call_soon_threadsafe.side_effect = RuntimeError("closed")
    assert app._handle_proxy_control("proxy_control", "PROXY_MODE", "2") is True

    absent = ProxyApp(runtime_config)
    await absent._apply_proxy_mode("online")
    app.proxy = MagicMock()
    app.proxy.mode_manager.apply_configured_mode = AsyncMock(return_value=False)
    await app._apply_proxy_mode("online")
    app.status_publisher = MagicMock()
    app.proxy.mode_manager.apply_configured_mode.return_value = True
    await app._apply_proxy_mode("hybrid")
    app.status_publisher._publish.assert_called_once_with()

    app._on_hybrid_transition("failed", 1.0, "reason")
    app.telemetry_collector = MagicMock()
    app._on_hybrid_transition("failed", 1.0, None)
    app.telemetry_collector.record_hybrid_state_end.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_full_lifecycle_is_idempotent(runtime_config) -> None:
    app = ProxyApp(runtime_config)
    app.proxy = MagicMock(stop=AsyncMock())
    app.twin_handler = MagicMock(stop=AsyncMock())
    app.status_publisher = MagicMock()
    app.twin_store = MagicMock()
    app.pcap_capture = MagicMock()
    app.frame_capture = MagicMock()
    app.mqtt = MagicMock()
    app._telemetry_log_handler = logging.NullHandler()

    blocker = asyncio.Event()

    async def pending() -> None:
        await blocker.wait()

    task = asyncio.create_task(pending())
    app._tasks.add(task)
    await app.shutdown()
    assert task.cancelled()
    app.status_publisher.stop.assert_called_once_with()
    app.twin_store.close.assert_called_once_with()
    app.pcap_capture.stop.assert_called_once_with()
    app.frame_capture.stop.assert_called_once_with()
    app.mqtt.disconnect.assert_called_once_with()
    await app.shutdown()
