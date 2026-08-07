"""Durable runtime composition tests for the v2 application entry point."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

import main as main_module
from main import ProxyApp
from twin.state import ConfirmedSetting, ControlPolicy, RecoveryReport
from twin.store import StoreRecordNotFound


@pytest.fixture
def runtime_config(make_config, tmp_path):
    """Build a complete hermetic runtime configuration."""
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


def test_proxy_app_exposes_only_durable_twin_components(runtime_config):
    app = ProxyApp(runtime_config)

    assert app.twin_store is None
    assert app.twin_coordinator is None
    assert app.audit_publisher is None
    assert app.twin_handler is None
    assert not hasattr(app, "twin_queue")
    assert not hasattr(app, "twin_delivery")


def test_control_policy_projects_seconds_to_exact_milliseconds(runtime_config):
    runtime_config.control_ack_timeout_s = 1.25
    runtime_config.control_event_timeout_s = 2.5
    runtime_config.control_command_ttl_s = 3.75
    runtime_config.control_max_attempts = 4

    assert ProxyApp(runtime_config)._control_policy() == ControlPolicy(
        ack_timeout_ms=1250,
        event_timeout_ms=2500,
        pending_ttl_ms=3750,
        max_attempts=4,
    )


@pytest.mark.asyncio
async def test_startup_disabled_recovers_store_without_handler_or_local_write(
    runtime_config,
):
    runtime_config.control_mqtt_enabled = False
    app = ProxyApp(runtime_config)
    app._loop = asyncio.get_running_loop()
    app._store_ready = True
    app.device_id_manager = MagicMock(device_id="DEV01")
    app.mqtt = MagicMock()
    app.mqtt.is_ready.return_value = True
    app.twin_store = MagicMock()
    app.twin_coordinator = MagicMock()

    with patch.object(main_module, "TwinControlHandler") as factory:
        await app._reconcile_control_handler()

    factory.assert_not_called()
    app.twin_store.read_device.assert_not_called()
    assert app.twin_handler is None


@pytest.mark.asyncio
async def test_unknown_device_poll_and_control_cannot_claim(runtime_config):
    runtime_config.control_mqtt_enabled = True
    app = ProxyApp(runtime_config)
    app._loop = asyncio.get_running_loop()
    app._store_ready = True
    app.device_id_manager = MagicMock(device_id=None)
    app.mqtt = MagicMock()
    app.mqtt.is_ready.return_value = True
    app.twin_store = MagicMock()
    app.twin_coordinator = MagicMock()

    assert await app._on_valid_device_identity("DEV01", None, 1) is False
    with patch.object(main_module, "TwinControlHandler") as factory:
        await app._reconcile_control_handler()

    factory.assert_not_called()
    app.twin_store.observe_device.assert_not_called()
    assert app.twin_handler is None


@pytest.mark.asyncio
async def test_store_startup_retries_at_offsets_zero_one_two(runtime_config):
    app = ProxyApp(runtime_config)
    store = MagicMock()
    store.open.side_effect = [RuntimeError("one"), RuntimeError("two"), None]
    store.recover.return_value = RecoveryReport(0, 0, 0, 0, 0)

    with patch.object(main_module, "TwinCommandStore", return_value=store), patch.object(
        main_module.asyncio,
        "sleep",
        new=AsyncMock(),
    ) as sleep:
        assert await app._open_control_store() is True

    assert store.open.call_count == 3
    assert sleep.await_args_list == [call(1.0), call(1.0)]
    assert app.twin_store is store
    assert app.control_recovery_report == RecoveryReport(0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_store_failure_aborts_before_network_components(runtime_config):
    app = ProxyApp(runtime_config)
    store = MagicMock()
    store.open.side_effect = RuntimeError("corrupt")
    manager = MagicMock()
    manager.load.return_value = None

    with patch.object(main_module, "DeviceIdManager", return_value=manager), patch.object(
        main_module,
        "TwinCommandStore",
        return_value=store,
    ), patch.object(main_module.asyncio, "sleep", new=AsyncMock()), patch.object(
        main_module,
        "MQTTClient",
    ) as mqtt_class, patch.object(main_module, "ProxyServer") as proxy_class:
        assert await app.startup() is False

    mqtt_class.assert_not_called()
    proxy_class.assert_not_called()
    assert store.open.call_count == 3


@pytest.mark.asyncio
async def test_valid_identity_persists_before_store_observation(runtime_config):
    app = ProxyApp(runtime_config)
    app.device_id_manager = main_module.DeviceIdManager(runtime_config.device_id_path)
    app.twin_store = MagicMock()
    app._store_ready = True
    app._reconcile_control_handler = AsyncMock()  # type: ignore[method-assign]

    assert await app._on_valid_device_identity("DEV01", 10, 20) is True

    persisted = json.loads(
        Path(runtime_config.device_id_path).read_text(encoding="utf-8")
    )
    assert persisted["device_id"] == "DEV01"
    app.twin_store.observe_device.assert_called_once_with(
        device_id="DEV01",
        observed_at_ms=ANY,
        observed_wire_id=10,
        observed_wire_id_set=20,
    )
    app._reconcile_control_handler.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_identity_mismatch_never_retargets_store(runtime_config):
    app = ProxyApp(runtime_config)
    manager = main_module.DeviceIdManager(runtime_config.device_id_path)
    assert manager.save("DEV01")
    app.device_id_manager = manager
    app.twin_store = MagicMock()

    assert await app._on_valid_device_identity("DEV02", 10, 20) is False

    assert manager.device_id == "DEV01"
    app.twin_store.observe_device.assert_not_called()


@pytest.mark.asyncio
async def test_identity_without_both_counters_is_not_bound(runtime_config):
    app = ProxyApp(runtime_config)
    manager = main_module.DeviceIdManager(runtime_config.device_id_path)
    app.device_id_manager = manager
    app.twin_store = MagicMock()

    assert await app._on_valid_device_identity("DEV01", None, 20) is False

    assert manager.device_id is None
    app.twin_store.observe_device.assert_not_called()


@pytest.mark.asyncio
async def test_generic_frame_callback_never_establishes_identity(runtime_config):
    app = ProxyApp(runtime_config)
    manager = main_module.DeviceIdManager(runtime_config.device_id_path)
    app.device_id_manager = manager
    app.frame_processor = AsyncMock()

    await app._on_frame(
        {"_device_id": "DEV01", "_table": "tbl_actual", "P": 10}
    )

    assert manager.device_id is None
    app.frame_processor.process.assert_awaited_once()


@pytest.mark.asyncio
async def test_committed_confirmation_uses_dedicated_publish_boundary(runtime_config):
    app = ProxyApp(runtime_config)
    app.frame_processor = MagicMock()
    confirmation = ConfirmedSetting(
        "command",
        "audit",
        "evidence",
        "DEV01",
        "tbl_box_prms",
        "SA",
        "1",
        123,
    )

    await app._on_committed_confirmation(confirmation)

    app.frame_processor.publish_confirmed_setting.assert_called_once_with(confirmation)


@pytest.mark.asyncio
async def test_handler_reconciliation_requires_ready_mqtt_and_persisted_device_row(
    runtime_config,
):
    runtime_config.control_mqtt_enabled = True
    app = ProxyApp(runtime_config)
    app._loop = asyncio.get_running_loop()
    app.device_id_manager = MagicMock(device_id="DEV01")
    app.mqtt = MagicMock()
    app.mqtt.is_ready.return_value = True
    app.twin_store = MagicMock()
    app.twin_store.read_device.return_value = SimpleNamespace(device_id="DEV01")
    app.twin_coordinator = MagicMock()
    app._store_ready = True
    app.audit_publisher = MagicMock()
    handler = MagicMock(device_id="DEV01")
    handler.start = AsyncMock(return_value=True)

    with patch.object(main_module, "TwinControlHandler", return_value=handler) as factory:
        await app._reconcile_control_handler()

    factory.assert_called_once()
    handler.start.assert_awaited_once_with()
    assert app.twin_handler is handler


@pytest.mark.asyncio
async def test_handler_reconciliation_skips_missing_device_row(runtime_config):
    runtime_config.control_mqtt_enabled = True
    app = ProxyApp(runtime_config)
    app._loop = asyncio.get_running_loop()
    app.device_id_manager = MagicMock(device_id="DEV01")
    app.mqtt = MagicMock()
    app.mqtt.is_ready.return_value = True
    app.twin_store = MagicMock()
    app.twin_store.read_device.side_effect = StoreRecordNotFound("missing")

    with patch.object(main_module, "TwinControlHandler") as factory:
        await app._reconcile_control_handler()

    factory.assert_not_called()
    assert app.twin_handler is None


@pytest.mark.asyncio
async def test_shutdown_closes_store_before_mqtt_disconnect(runtime_config):
    order: list[str] = []
    app = ProxyApp(runtime_config)
    app.proxy = MagicMock()
    app.proxy.stop = AsyncMock(side_effect=lambda: order.append("proxy"))
    app.twin_handler = MagicMock()
    app.twin_handler.stop = AsyncMock(side_effect=lambda: order.append("handler"))
    app.twin_store = MagicMock()
    app.twin_store.close.side_effect = lambda: order.append("store")
    app.mqtt = MagicMock()
    app.mqtt.disconnect.side_effect = lambda: order.append("mqtt")

    await app.shutdown()

    assert order.index("proxy") < order.index("handler")
    assert order.index("handler") < order.index("store")
    assert order.index("store") < order.index("mqtt")


def test_mqtt_identity_never_uses_unknown(runtime_config):
    app = ProxyApp(runtime_config)
    app.device_id_manager = MagicMock(device_id=None)

    assert app._mqtt_identity() == runtime_config.proxy_device_id

    app.device_id_manager.device_id = "DEV01"
    assert app._mqtt_identity() == "DEV01"
