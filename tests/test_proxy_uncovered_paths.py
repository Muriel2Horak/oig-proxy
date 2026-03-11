"""Coverage-focused tests for remaining proxy.py branches."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pyright: reportMissingImports=false

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxy as proxy_module
from models import ProxyMode


class DummyWriter:
    def __init__(self, *, peer=("127.0.0.1", 5710), sock=None):
        self._peer = peer
        self._sock = sock
        self.buffer = []
        self.closed = False

    def write(self, data):
        self.buffer.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None

    def get_extra_info(self, name, default=None):
        if name == "peername":
            return self._peer
        if name == "socket":
            return self._sock
        return default


def _mk_proxy() -> proxy_module.OIGProxy:
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "AUTO"
    proxy.box_connected = True
    proxy.box_connections = 0
    proxy.stats = {"acks_local": 0, "frames_received": 0, "frames_forwarded": 0}
    proxy._active_box_peer = "peer:1"
    proxy._last_box_disconnect_reason = None
    proxy._background_tasks = set()
    proxy._conn_seq = 0
    proxy._active_box_writer = None
    proxy._box_conn_lock = asyncio.Lock()
    proxy._local_getactual_task = None
    proxy._pending_twin_activation = False
    proxy._twin_mode_active = False
    proxy._local_control_routing = "auto"
    proxy._twin_kill_switch = False
    proxy._twin_enabled = True
    proxy._twin = None
    proxy._loop = None

    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._hm.configured_mode = "online"
    proxy._hm.should_try_cloud.return_value = True
    proxy._hm.should_route_settings_via_twin.return_value = False
    proxy._hm.force_offline_enabled.return_value = False
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)

    proxy._tc = MagicMock()
    proxy._tc.box_seen_in_window = False
    proxy._tc.force_logs_this_window = False
    proxy._tc.record_response = MagicMock()
    proxy._tc.record_end_frame = MagicMock()
    proxy._tc.record_tbl_event = MagicMock()

    proxy._cs = MagicMock()
    proxy._cs.queue_setting = AsyncMock(return_value={"ok": True})
    proxy._cs.parse_setting_event = MagicMock(return_value=None)

    proxy._cf = MagicMock()
    proxy._cf.forward_frame = AsyncMock(return_value=(None, object()))
    proxy._cf.session_connected = False
    proxy._cf.rx_buf = bytearray()

    proxy._msc = MagicMock()
    proxy._mp = MagicMock()
    proxy._mp.mode_device_id = None
    proxy._mp.prms_device_id = None

    proxy.mqtt_publisher = MagicMock()
    proxy.mqtt_publisher.device_id = "AUTO"
    proxy.mqtt_publisher.discovery_sent = {"x"}

    proxy.publish_proxy_status = AsyncMock()
    proxy._local_getactual_loop = AsyncMock()
    proxy._unregister_box_connection = AsyncMock()
    return proxy


def test_box_session_id_fallback_to_str():
    assert proxy_module._box_session_id(123) == "123"


@pytest.mark.asyncio
async def test_initialize_mqtt_sets_twin_handler_when_loop_present():
    proxy = _mk_proxy()
    proxy._loop = object()
    proxy.mqtt_publisher.connect = MagicMock(return_value=True)
    proxy.mqtt_publisher.start_health_check = AsyncMock()
    proxy._twin_mqtt_handler = MagicMock()

    await proxy._initialize_mqtt()

    proxy._twin_mqtt_handler.setup_mqtt.assert_called_once_with(proxy._loop)


@pytest.mark.asyncio
async def test_install_twin_activation_hook_sets_pending_only_online_with_queue():
    proxy = _mk_proxy()
    called = []

    async def original(*, topic, payload):
        called.append((topic, payload))

    proxy._twin_mqtt_handler = SimpleNamespace(on_mqtt_message=original)
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)
    proxy._queue_has_items = AsyncMock(return_value=True)

    proxy._install_twin_mqtt_activation_hook()
    await proxy._twin_mqtt_handler.on_mqtt_message(topic="t", payload=b"p")
    assert called == [("t", b"p")]
    assert proxy._pending_twin_activation is True


@pytest.mark.asyncio
async def test_install_twin_activation_hook_does_not_arm_in_non_online_or_empty_queue():
    proxy = _mk_proxy()

    async def original(*, topic, payload):
        return None

    proxy._twin_mqtt_handler = SimpleNamespace(on_mqtt_message=original)
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.HYBRID)
    proxy._queue_has_items = AsyncMock(return_value=True)
    proxy._install_twin_mqtt_activation_hook()
    await proxy._twin_mqtt_handler.on_mqtt_message(topic="t", payload=b"p")
    assert proxy._pending_twin_activation is False

    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)
    proxy._queue_has_items = AsyncMock(return_value=False)
    await proxy._twin_mqtt_handler.on_mqtt_message(topic="t", payload=b"p")
    assert proxy._pending_twin_activation is False


@pytest.mark.asyncio
async def test_queue_has_items_false_without_twin_and_true_with_queue():
    proxy = _mk_proxy()
    proxy._twin = None
    assert await proxy._queue_has_items() is False

    proxy._twin = SimpleNamespace(get_queue_length=AsyncMock(return_value=1))
    assert await proxy._queue_has_items() is True


def test_restore_device_id_returns_when_not_auto():
    proxy = _mk_proxy()
    proxy.device_id = "DEV1"
    proxy._restore_device_id()
    assert proxy.device_id == "DEV1"


def test_restore_device_id_updates_twin_config_when_available():
    proxy = _mk_proxy()
    proxy.device_id = "AUTO"
    proxy._mp.mode_device_id = "2206237016"
    proxy._twin = SimpleNamespace(config=SimpleNamespace(device_id="AUTO"))

    proxy._restore_device_id()

    assert proxy.device_id == "2206237016"
    assert proxy._twin.config.device_id == "2206237016"


@pytest.mark.asyncio
async def test_full_refresh_loop_continue_branches(monkeypatch):
    proxy = _mk_proxy()
    proxy._full_refresh_interval_h = 1

    calls = {"sleep": 0}

    async def stop_after_one(_):
        calls["sleep"] += 1
        if calls["sleep"] > 1:
            raise RuntimeError("stop")
        return None

    monkeypatch.setattr(asyncio, "sleep", stop_after_one)

    # force offline -> continue (line 343)
    calls["sleep"] = 0
    proxy._hm.force_offline_enabled.return_value = True
    with pytest.raises(RuntimeError):
        await proxy._full_refresh_loop()

    # not box connected -> continue (line 345)
    calls["sleep"] = 0
    proxy._hm.force_offline_enabled.return_value = False
    proxy.box_connected = False
    with pytest.raises(RuntimeError):
        await proxy._full_refresh_loop()

    # non-online mode -> continue (line 347)
    calls["sleep"] = 0
    proxy.box_connected = True
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.HYBRID)
    with pytest.raises(RuntimeError):
        await proxy._full_refresh_loop()

    # twin queue/inflight guards (lines 349-352)
    calls["sleep"] = 0
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)
    proxy._twin = SimpleNamespace(get_inflight=AsyncMock(return_value=None))
    proxy._queue_has_items = AsyncMock(return_value=True)
    with pytest.raises(RuntimeError):
        await proxy._full_refresh_loop()

    calls["sleep"] = 0
    proxy._queue_has_items = AsyncMock(return_value=False)
    proxy._twin.get_inflight = AsyncMock(return_value=SimpleNamespace())
    with pytest.raises(RuntimeError):
        await proxy._full_refresh_loop()


def test_tune_socket_returns_without_socket():
    writer = DummyWriter(sock=None)
    proxy_module.OIGProxy._tune_socket(writer)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_handle_connection_full_lifecycle_with_twin_and_pending(monkeypatch):
    proxy = _mk_proxy()
    reader = object()
    writer = DummyWriter(peer=("1.2.3.4", 1234))
    proxy._register_box_connection = AsyncMock(return_value=7)
    proxy._close_writer = AsyncMock()

    old_task = MagicMock()
    old_task.done.return_value = False
    proxy._local_getactual_task = old_task

    new_task = MagicMock()
    new_task.done.return_value = False

    def fake_create_task(coro):
        coro.close()
        return new_task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    proxy._tune_socket = MagicMock()
    proxy._pending_twin_activation = True
    proxy._queue_has_items = AsyncMock(return_value=True)
    proxy._handle_box_connection = AsyncMock()

    result = SimpleNamespace(tx_id="tx", status="ok")
    proxy._twin = SimpleNamespace(
        on_reconnect=AsyncMock(return_value=[result]),
        on_disconnect=AsyncMock(return_value=[result]),
    )

    await proxy.handle_connection(reader, writer)

    assert old_task.cancel.called
    assert new_task.cancel.called
    proxy._twin.on_reconnect.assert_awaited_once_with(conn_id=7)
    proxy._twin.on_disconnect.assert_awaited_once()
    proxy._unregister_box_connection.assert_awaited_once_with(writer)
    assert proxy.publish_proxy_status.await_count == 2


@pytest.mark.asyncio
async def test_handle_connection_exception_path(monkeypatch):
    proxy = _mk_proxy()
    writer = DummyWriter(peer=("9.9.9.9", 9999))
    proxy._register_box_connection = AsyncMock(return_value=1)
    proxy._close_writer = AsyncMock()
    proxy._handle_box_connection = AsyncMock(side_effect=RuntimeError("boom"))

    def fake_create_task(coro):
        coro.close()
        return MagicMock(done=lambda: True)

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await proxy.handle_connection(object(), writer)

    assert proxy._last_box_disconnect_reason is None


@pytest.mark.asyncio
async def test_close_writer_none_and_valid_writer():
    proxy = _mk_proxy()
    await proxy._close_writer(None)

    writer = DummyWriter()
    await proxy._close_writer(writer)
    assert writer.closed is True


@pytest.mark.asyncio
async def test_maybe_autodetect_device_id_returns_when_not_auto():
    proxy = _mk_proxy()
    proxy.device_id = "DEV1"
    await proxy._maybe_autodetect_device_id("DEV2")
    assert proxy.device_id == "DEV1"


@pytest.mark.asyncio
async def test_maybe_autodetect_device_id_updates_twin_device_id():
    proxy = _mk_proxy()
    proxy.device_id = "AUTO"
    proxy._twin = SimpleNamespace(set_device_id=AsyncMock())

    await proxy._maybe_autodetect_device_id("2206237016")

    proxy._twin.set_device_id.assert_awaited_once_with("2206237016")


@pytest.mark.asyncio
async def test_process_box_frame_common_records_end_and_tbl_event(monkeypatch):
    proxy = _mk_proxy()
    proxy._active_box_peer = "1.2.3.4:1234"
    proxy._touch_last_data = MagicMock()
    proxy.parser = SimpleNamespace(parse_xml_frame=lambda _f: {"_table": "END"})
    proxy._extract_device_and_table = MagicMock(return_value=(None, "END"))
    proxy._infer_table_name = MagicMock(return_value="END")
    proxy._infer_device_id = MagicMock(return_value=None)
    proxy._maybe_autodetect_device_id = AsyncMock()
    proxy._mp.maybe_persist_table_state = MagicMock()
    proxy._cs.handle_setting_event = AsyncMock()
    proxy._maybe_handle_twin_event = AsyncMock()
    proxy._mp.maybe_process_mode = AsyncMock()
    proxy.mqtt_publisher.publish_data = AsyncMock()
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)

    await proxy._process_box_frame_common(frame_bytes=b"x", frame="<Result>END</Result>", conn_id=1)
    proxy._tc.record_end_frame.assert_called_once_with(sent=False)

    proxy.parser = SimpleNamespace(parse_xml_frame=lambda _f: {"_table": "tbl_events", "Type": "Setting"})
    proxy._extract_device_and_table = MagicMock(return_value=("DEV1", "tbl_events"))
    await proxy._process_box_frame_common(frame_bytes=b"x", frame="<TblName>tbl_events</TblName>", conn_id=2)
    proxy._tc.record_tbl_event.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_handle_local_control_poll_branches():
    proxy = _mk_proxy()
    writer = DummyWriter()
    assert await proxy._maybe_handle_local_control_poll(table_name="tbl_actual", conn_id=1, box_writer=writer) is False

    proxy._resolve_local_control_routing = MagicMock(return_value="cloud")
    assert await proxy._maybe_handle_local_control_poll(table_name="IsNewSet", conn_id=1, box_writer=writer) is False

    proxy._resolve_local_control_routing = MagicMock(return_value="twin")
    proxy._dispatch_local_control_via_twin = AsyncMock(return_value=True)
    assert await proxy._maybe_handle_local_control_poll(table_name="IsNewSet", conn_id=1, box_writer=writer) is True


@pytest.mark.asyncio
async def test_route_box_frame_by_mode_non_break_path_returns_false():
    proxy = _mk_proxy()
    proxy._cf.forward_frame = AsyncMock(return_value=("r", object()))
    _, _, should_break = await proxy._route_box_frame_by_mode(
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        conn_id=1,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
        current_mode=ProxyMode.ONLINE,
    )
    assert should_break is False


@pytest.mark.asyncio
async def test_route_box_frame_by_mode_default_transport_path_when_legacy_fallback_off():
    proxy = _mk_proxy()
    proxy._legacy_fallback_enabled = False
    proxy._handle_frame_local_offline = AsyncMock(return_value=(None, None))
    proxy._cf.forward_frame = AsyncMock(return_value=("r", object()))

    _, _, should_break = await proxy._route_box_frame_by_mode(
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        conn_id=1,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
        current_mode=ProxyMode.OFFLINE,
    )

    assert should_break is False
    proxy._cf.forward_frame.assert_awaited_once()
    proxy._handle_frame_local_offline.assert_not_called()


@pytest.mark.asyncio
async def test_transport_only_forward_ignores_twin_unavailable_and_continues():
    proxy = _mk_proxy()
    proxy._twin = None
    proxy._cf.forward_frame = AsyncMock(return_value=("r", object()))

    _, _, should_break = await proxy._transport_only_forward(
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        conn_id=1,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
    )

    assert should_break is False
    proxy._cf.forward_frame.assert_awaited_once()


def test_process_box_frame_common_telemetry_failure_is_fail_open(monkeypatch, caplog):
    proxy = _mk_proxy()
    proxy.stats["acks_cloud"] = 0
    proxy.parser = SimpleNamespace(parse_xml_frame=lambda _f: {"_table": "tbl_actual"})
    proxy._extract_device_and_table = MagicMock(return_value=("DEV1", "tbl_actual"))
    proxy._maybe_autodetect_device_id = AsyncMock()
    proxy._cs.handle_setting_event = AsyncMock()
    proxy._maybe_handle_twin_event = AsyncMock()
    proxy._mp.maybe_process_mode = AsyncMock()
    proxy.mqtt_publisher.publish_data = AsyncMock(side_effect=RuntimeError("mqtt down"))
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)
    caplog.set_level("WARNING")

    async def _run():
        return await proxy._process_box_frame_common(
            frame_bytes=b"<Frame><TblName>tbl_actual</TblName></Frame>",
            frame="<Frame><TblName>tbl_actual</TblName></Frame>",
            conn_id=41,
        )

    device_id, table_name = asyncio.run(_run())

    assert (device_id, table_name) == ("DEV1", "tbl_actual")
    assert "TELEMETRY: publish_data failed (transport fail-open" in caplog.text
    proxy._mp.maybe_process_mode.assert_awaited_once()


def test_process_box_frame_common_twin_failure_is_fail_open(monkeypatch, caplog):
    proxy = _mk_proxy()
    proxy.stats["acks_cloud"] = 0
    proxy.parser = SimpleNamespace(parse_xml_frame=lambda _f: {"_table": "tbl_actual"})
    proxy._extract_device_and_table = MagicMock(return_value=("DEV1", "tbl_actual"))
    proxy._maybe_autodetect_device_id = AsyncMock()
    proxy._cs.handle_setting_event = AsyncMock()
    proxy._maybe_handle_twin_event = AsyncMock(side_effect=RuntimeError("twin down"))
    proxy._mp.maybe_process_mode = AsyncMock()
    proxy.mqtt_publisher.publish_data = AsyncMock()
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)
    caplog.set_level("WARNING")

    async def _run():
        return await proxy._process_box_frame_common(
            frame_bytes=b"<Frame><TblName>tbl_actual</TblName></Frame>",
            frame="<Frame><TblName>tbl_actual</TblName></Frame>",
            conn_id=42,
        )

    device_id, table_name = asyncio.run(_run())

    assert (device_id, table_name) == ("DEV1", "tbl_actual")
    assert "TWIN: Sidecar dependency failure ignored (transport fail-open" in caplog.text
    proxy.mqtt_publisher.publish_data.assert_awaited_once()


def test_telemetry_down_still_returns_cloud_ack_when_legacy_fallback_off(monkeypatch):
    proxy = _mk_proxy()
    proxy._legacy_fallback_enabled = False
    proxy.stats["acks_cloud"] = 0
    proxy.parser = SimpleNamespace(parse_xml_frame=lambda _f: {"_table": "tbl_actual"})
    proxy._extract_device_and_table = MagicMock(return_value=("DEV1", "tbl_actual"))
    proxy._maybe_autodetect_device_id = AsyncMock()
    proxy._cs.handle_setting_event = AsyncMock()
    proxy._maybe_handle_twin_event = AsyncMock()
    proxy._mp.maybe_process_mode = AsyncMock()
    proxy.mqtt_publisher.publish_data = AsyncMock(side_effect=RuntimeError("mqtt down"))
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)

    async def _forward(**_kwargs):
        proxy.stats["acks_cloud"] += 1
        return None, object()

    proxy._cf.forward_frame = AsyncMock(side_effect=_forward)

    async def _run():
        return await proxy._handle_box_frame_iteration(
            data=b"<Frame><TblName>tbl_actual</TblName></Frame>",
            conn_id=43,
            box_writer=DummyWriter(),
            cloud_reader=None,
            cloud_writer=None,
            cloud_connect_timeout_s=1.0,
        )

    _, _, should_break = asyncio.run(_run())

    assert should_break is False
    assert proxy.stats["acks_cloud"] == 1


def test_twin_down_has_no_transport_impact_when_cloud_healthy(monkeypatch):
    proxy = _mk_proxy()
    proxy._legacy_fallback_enabled = False
    proxy.stats["acks_cloud"] = 0
    proxy.parser = SimpleNamespace(parse_xml_frame=lambda _f: {"_table": "tbl_actual"})
    proxy._extract_device_and_table = MagicMock(return_value=("DEV1", "tbl_actual"))
    proxy._maybe_autodetect_device_id = AsyncMock()
    proxy._cs.handle_setting_event = AsyncMock()
    proxy._maybe_handle_twin_event = AsyncMock(side_effect=RuntimeError("twin down"))
    proxy._mp.maybe_process_mode = AsyncMock()
    proxy.mqtt_publisher.publish_data = AsyncMock()
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)

    async def _forward(**_kwargs):
        proxy.stats["acks_cloud"] += 1
        return None, object()

    proxy._cf.forward_frame = AsyncMock(side_effect=_forward)

    async def _run():
        return await proxy._handle_box_frame_iteration(
            data=b"<Frame><TblName>tbl_actual</TblName></Frame>",
            conn_id=44,
            box_writer=DummyWriter(),
            cloud_reader=None,
            cloud_writer=None,
            cloud_connect_timeout_s=1.0,
        )

    _, _, should_break = asyncio.run(_run())

    assert should_break is False
    assert proxy.stats["acks_cloud"] == 1


@pytest.mark.asyncio
async def test_activate_session_twin_mode_pending_and_callable_guards():
    proxy = _mk_proxy()
    proxy._pending_twin_activation = True
    proxy._queue_has_items = AsyncMock(return_value=True)
    await proxy._activate_session_twin_mode_if_needed(conn_id=1)
    assert proxy._twin_mode_active is True

    proxy._pending_twin_activation = False
    proxy._twin_mode_active = False
    proxy._is_twin_routing_available = None  # type: ignore[assignment]
    await proxy._activate_session_twin_mode_if_needed(conn_id=2)
    assert proxy._twin_mode_active is False


@pytest.mark.asyncio
async def test_activate_session_twin_mode_offline_setting_path():
    proxy = _mk_proxy()
    proxy._pending_twin_activation = False
    proxy._is_twin_routing_available = MagicMock(return_value=True)
    proxy._hm.should_route_settings_via_twin.return_value = True
    await proxy._activate_session_twin_mode_if_needed(conn_id=3)
    assert proxy._twin_mode_active is True


@pytest.mark.asyncio
async def test_activate_session_twin_mode_returns_when_routing_unavailable_or_disabled():
    proxy = _mk_proxy()
    proxy._pending_twin_activation = False

    proxy._is_twin_routing_available = MagicMock(return_value=False)
    proxy._hm.should_route_settings_via_twin.return_value = True
    await proxy._activate_session_twin_mode_if_needed(conn_id=4)
    assert proxy._twin_mode_active is False

    proxy._is_twin_routing_available = MagicMock(return_value=True)
    proxy._hm.should_route_settings_via_twin.return_value = False
    await proxy._activate_session_twin_mode_if_needed(conn_id=5)
    assert proxy._twin_mode_active is False


@pytest.mark.asyncio
async def test_handle_box_frame_iteration_short_circuit_paths():
    proxy = _mk_proxy()
    proxy._process_box_frame_with_guard = AsyncMock(return_value=("DEV1", "IsNewSet", False))
    proxy._maybe_handle_twin_ack = AsyncMock(return_value=True)
    _, _, should_break = await proxy._handle_box_frame_iteration(
        data=b"x",
        conn_id=1,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
    )
    assert should_break is False

    proxy._maybe_handle_twin_ack = AsyncMock(return_value=False)
    proxy._maybe_handle_local_control_poll = AsyncMock(return_value=True)
    _, _, should_break = await proxy._handle_box_frame_iteration(
        data=b"x",
        conn_id=1,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
    )
    assert should_break is False


@pytest.mark.asyncio
async def test_handle_box_frame_iteration_cloud_healthy_skips_twin_state_queries():
    proxy = _mk_proxy()
    proxy._process_box_frame_with_guard = AsyncMock(
        return_value=("DEV1", "tbl_actual", False)
    )
    proxy._route_box_frame_by_mode = AsyncMock(return_value=(None, object(), False))
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=None),
        get_queue_length=AsyncMock(return_value=0),
    )

    _, _, should_break = await proxy._handle_box_frame_iteration(
        data=b"<Frame><TblName>tbl_actual</TblName></Frame>",
        conn_id=11,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
    )

    assert should_break is False
    proxy._route_box_frame_by_mode.assert_awaited_once()
    proxy._twin.get_inflight.assert_not_awaited()
    proxy._twin.get_queue_length.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_box_frame_iteration_setting_ack_queries_twin_inflight_once():
    proxy = _mk_proxy()
    proxy._process_box_frame_with_guard = AsyncMock(
        return_value=("DEV1", "tbl_actual", False)
    )
    proxy._route_box_frame_by_mode = AsyncMock(return_value=(None, object(), False))
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(
            return_value=SimpleNamespace(tx_id="tx-1", delivered_conn_id=11)
        ),
        on_ack=AsyncMock(return_value=SimpleNamespace(tx_id="tx-1")),
    )

    _, _, should_break = await proxy._handle_box_frame_iteration(
        data=b"<Frame><Reason>Setting</Reason><Result>ACK</Result></Frame>",
        conn_id=11,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
    )

    assert should_break is False
    proxy._route_box_frame_by_mode.assert_not_called()
    proxy._twin.get_inflight.assert_awaited_once()
    proxy._twin.on_ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_box_frame_iteration_legacy_fallback_off_bypasses_twin_logic():
    proxy = _mk_proxy()
    proxy._legacy_fallback_enabled = False
    proxy._process_box_frame_with_guard = AsyncMock(
        return_value=("DEV1", "tbl_actual", False)
    )
    proxy._route_box_frame_by_mode = AsyncMock(return_value=(None, object(), False))
    proxy._maybe_handle_twin_ack = AsyncMock(return_value=False)
    proxy._maybe_handle_local_control_poll = AsyncMock(return_value=False)
    proxy._maybe_deactivate_session_twin_mode_if_idle = AsyncMock()

    _, _, should_break = await proxy._handle_box_frame_iteration(
        data=b"<Frame><TblName>tbl_actual</TblName></Frame>",
        conn_id=12,
        box_writer=DummyWriter(),
        cloud_reader=None,
        cloud_writer=None,
        cloud_connect_timeout_s=1.0,
    )

    assert should_break is False
    proxy._route_box_frame_by_mode.assert_awaited_once()
    proxy._maybe_handle_twin_ack.assert_not_called()
    proxy._maybe_handle_local_control_poll.assert_not_called()
    proxy._maybe_deactivate_session_twin_mode_if_idle.assert_not_called()


@pytest.mark.asyncio
async def test_handle_box_connection_exception_handlers_and_cloud_session_end():
    proxy = _mk_proxy()
    proxy._close_writer = AsyncMock()
    proxy._activate_session_twin_mode_if_needed = AsyncMock()

    proxy._read_box_bytes = AsyncMock(side_effect=ConnectionResetError("rst"))
    proxy._cf.session_connected = False
    proxy._cf.rx_buf = bytearray(b"abc")
    await proxy._handle_box_connection(MagicMock(), DummyWriter(), conn_id=1)

    proxy._read_box_bytes = AsyncMock(side_effect=OSError("io"))
    proxy._cf.session_connected = True
    await proxy._handle_box_connection(MagicMock(), DummyWriter(), conn_id=2)

    proxy._tc.record_cloud_session_end.assert_called()


@pytest.mark.asyncio
async def test_respond_local_offline_send_ack_false():
    proxy = _mk_proxy()
    writer = DummyWriter()
    await proxy._respond_local_offline(b"x", "tbl", "DEV1", writer, send_ack=False)
    assert not writer.buffer


def test_resolve_local_control_routing_all_branches():
    proxy = _mk_proxy()

    proxy._local_control_routing = "force_twin"
    proxy._is_twin_routing_available = MagicMock(return_value=False)
    assert proxy._resolve_local_control_routing() == "local"
    proxy._is_twin_routing_available.return_value = True
    assert proxy._resolve_local_control_routing() == "twin"

    proxy._local_control_routing = "force_cloud"
    proxy._hm.should_try_cloud.return_value = True
    assert proxy._resolve_local_control_routing() == "cloud"
    proxy._hm.should_try_cloud.return_value = False
    assert proxy._resolve_local_control_routing() == "local"

    proxy._local_control_routing = "auto"
    proxy._hm.should_route_settings_via_twin.return_value = True
    proxy._is_twin_routing_available = MagicMock(return_value=True)
    assert proxy._resolve_local_control_routing() == "twin"

    proxy._hm.should_route_settings_via_twin.return_value = False
    proxy._hm.configured_mode = "offline"
    proxy._is_twin_routing_available.return_value = False
    assert proxy._resolve_local_control_routing() == "local"

    proxy._hm.configured_mode = "online"
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._twin_mode_active = True
    proxy._is_twin_routing_available.return_value = True
    assert proxy._resolve_local_control_routing() == "twin"
    proxy._twin_mode_active = False
    assert proxy._resolve_local_control_routing() == "cloud"

    proxy._hm.mode = ProxyMode.HYBRID
    proxy._hm.should_try_cloud.return_value = False
    proxy._is_twin_routing_available.return_value = False
    assert proxy._resolve_local_control_routing() == "local"
    proxy._is_twin_routing_available.return_value = True
    assert proxy._resolve_local_control_routing() == "twin"
    proxy._hm.should_try_cloud.return_value = True
    assert proxy._resolve_local_control_routing() == "cloud"

    proxy._hm.mode = "unexpected"
    assert proxy._resolve_local_control_routing() == "cloud"


def test_set_twin_kill_switch_toggles_both_branches():
    proxy = _mk_proxy()
    proxy.set_twin_kill_switch(True)
    assert proxy._twin_kill_switch is True
    proxy.set_twin_kill_switch(False)
    assert proxy._twin_kill_switch is False


@pytest.mark.asyncio
async def test_dispatch_local_control_via_twin_branches(monkeypatch, caplog):
    proxy = _mk_proxy()
    writer = DummyWriter()
    captured = []
    caplog.set_level("INFO")

    monkeypatch.setattr(
        proxy_module,
        "capture_payload",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    proxy._twin = None
    assert (
        await proxy._dispatch_local_control_via_twin(
            table_name="IsNewSet",
            conn_id=1,
            box_writer=writer,
        )
        is False
    )

    proxy._twin = SimpleNamespace(on_poll=AsyncMock())
    assert (
        await proxy._dispatch_local_control_via_twin(
            table_name="tbl_actual",
            conn_id=1,
            box_writer=writer,
        )
        is False
    )

    proxy._twin.on_poll = AsyncMock(
        return_value=SimpleNamespace(frame_data="<Frame><Result>ACK</Result></Frame>")
    )
    assert (
        await proxy._dispatch_local_control_via_twin(
            table_name="IsNewSet",
            conn_id=1,
            box_writer=writer,
        )
        is True
    )
    assert captured[-1][1]["direction"] == "proxy_to_box"
    assert captured[-1][0][1] == "IsNewSet"
    assert "TWIN: Delivered setting via twin (table=IsNewSet, conn=1)" in caplog.text

    proxy._twin.on_poll = AsyncMock(return_value=SimpleNamespace(frame_data=None))
    assert (
        await proxy._dispatch_local_control_via_twin(
            table_name="IsNewWeather",
            conn_id=1,
            box_writer=writer,
        )
        is True
    )
    assert captured[-1][1]["direction"] == "proxy_to_box"
    assert captured[-1][0][1] == "IsNewWeather"
    assert "source=twin table=IsNewWeather conn=1" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_local_control_via_twin_reaches_final_false_with_stateful_table_name():
    proxy = _mk_proxy()
    writer = DummyWriter()

    class StatefulName:  # pylint: disable=too-few-public-methods
        def __init__(self):
            self.calls = 0

        def __eq__(self, other):
            self.calls += 1
            return self.calls == 1 and other == "IsNewSet"

    table_name = StatefulName()
    proxy._twin = SimpleNamespace(on_poll=AsyncMock(return_value=SimpleNamespace(frame_data=None)))

    assert await proxy._dispatch_local_control_via_twin(
        table_name=table_name,
        conn_id=1,
        box_writer=writer,
    ) is False


@pytest.mark.asyncio
async def test_maybe_handle_twin_ack_branches(monkeypatch):
    proxy = _mk_proxy()
    writer = DummyWriter()

    proxy._twin = None
    assert await proxy._maybe_handle_twin_ack("<Frame/>", writer, conn_id=1) is False

    proxy._twin = SimpleNamespace(get_inflight=AsyncMock(return_value=None))
    assert await proxy._maybe_handle_twin_ack("<Frame/>", writer, conn_id=1) is False

    inflight = SimpleNamespace(tx_id="tx1", delivered_conn_id=1)
    proxy._twin.get_inflight = AsyncMock(return_value=inflight)
    assert await proxy._maybe_handle_twin_ack("<Result>ACK</Result>", writer, conn_id=1) is False

    proxy._twin.on_ack = AsyncMock(side_effect=RuntimeError("boom"))
    frame = "<Reason>Setting</Reason><Result>NACK</Result>"
    assert await proxy._maybe_handle_twin_ack(frame, writer, conn_id=1) is False

    proxy._twin.on_ack = AsyncMock(return_value=SimpleNamespace(tx_id="tx1"))

    def fake_create_task(coro):
        coro.close()
        raise RuntimeError("task")

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    frame = "<Reason>Setting</Reason><Result>ACK</Result>"
    assert await proxy._maybe_handle_twin_ack(frame, writer, conn_id=1) is True


@pytest.mark.asyncio
async def test_maybe_handle_twin_ack_end_skips_end_write():
    proxy = _mk_proxy()
    writer = DummyWriter()
    inflight = SimpleNamespace(tx_id="tx1", delivered_conn_id=1)
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=inflight),
        on_ack=AsyncMock(return_value=SimpleNamespace(tx_id="tx1")),
    )
    frame = "<Reason>Setting</Reason><Result>END</Result>"
    assert await proxy._maybe_handle_twin_ack(frame, writer, conn_id=1) is True


@pytest.mark.asyncio
async def test_maybe_handle_twin_ack_schedules_background_task(monkeypatch):
    proxy = _mk_proxy()
    writer = DummyWriter()
    inflight = SimpleNamespace(tx_id="tx2", delivered_conn_id=1)
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=inflight),
        on_ack=AsyncMock(return_value=SimpleNamespace(tx_id="tx2")),
    )

    class DummyTask:  # pylint: disable=too-few-public-methods
        def __init__(self):
            self.cb = None

        def add_done_callback(self, cb):
            self.cb = cb

    task = DummyTask()

    def fake_create_task(coro):
        coro.close()
        return task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    frame = "<Reason>Setting</Reason><Result>ACK</Result>"
    assert await proxy._maybe_handle_twin_ack(frame, writer, conn_id=1) is True
    assert task in proxy._background_tasks
    assert task.cb is not None


@pytest.mark.asyncio
async def test_maybe_handle_twin_ack_records_end_after_flush():
    proxy = _mk_proxy()
    writer = DummyWriter()
    inflight = SimpleNamespace(tx_id="tx3", delivered_conn_id=1)
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=inflight),
        on_ack=AsyncMock(return_value=SimpleNamespace(tx_id="tx3")),
    )
    proxy._record_proxy_to_box_frame = MagicMock()

    frame = "<Reason>Setting</Reason><Result>ACK</Result>"
    assert await proxy._maybe_handle_twin_ack(frame, writer, conn_id=1) is True
    await asyncio.sleep(0)

    proxy._record_proxy_to_box_frame.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_handle_twin_event_guard_and_success_paths():
    proxy = _mk_proxy()
    twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=None),
        on_tbl_event=AsyncMock(return_value=SimpleNamespace(tx_id="x")),
    )
    proxy._twin = twin

    await proxy._maybe_handle_twin_event({}, "tbl_actual", "DEV1")  # line 1099 return
    await proxy._maybe_handle_twin_event({"Type": "Other"}, "tbl_events", "DEV1")  # line 1101 return

    await proxy._maybe_handle_twin_event({"Type": "Setting", "Content": "x"}, "tbl_events", "DEV1")

    inflight = SimpleNamespace(tx_id="tx", conn_id=1, tbl_name="tbl_box_prms", tbl_item="MODE", new_value="1")
    twin.get_inflight = AsyncMock(return_value=inflight)

    await proxy._maybe_handle_twin_event({"Type": "Setting"}, "tbl_events", "DEV1")  # no content
    proxy._cs.parse_setting_event = MagicMock(return_value=None)
    await proxy._maybe_handle_twin_event({"Type": "Setting", "Content": "bad"}, "tbl_events", "DEV1")

    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_x", "MODE", "0", "1"))
    await proxy._maybe_handle_twin_event({"Type": "Setting", "Content": "x"}, "tbl_events", "DEV1")

    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_box_prms", "MODE", "0", "9"))
    await proxy._maybe_handle_twin_event({"Type": "Setting", "Content": "x"}, "tbl_events", "DEV1")

    proxy._cs.parse_setting_event = MagicMock(return_value=("tbl_box_prms", "MODE", "0", "1"))
    await proxy._maybe_handle_twin_event({"Type": "Setting", "Content": "ok"}, "tbl_events", "DEV1")
    twin.on_tbl_event.assert_awaited()

    twin.on_tbl_event = AsyncMock(side_effect=RuntimeError("boom"))
    await proxy._maybe_handle_twin_event({"Type": "Setting", "Content": "ok"}, "tbl_events", "DEV1")
