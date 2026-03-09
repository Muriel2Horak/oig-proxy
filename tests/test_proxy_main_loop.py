"""Tests for main box connection loop and offline routing."""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import control_pipeline as ctrl_module
import control_settings as cs_module
import proxy as proxy_module
from models import ProxyMode


class DummyWriter:
    def __init__(self):
        self.buffer = []
        self._closing = False

    def write(self, data):
        self.buffer.append(data)

    async def drain(self):
        return None

    def close(self):
        self._closing = True

    async def wait_closed(self):
        return None

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name):
        if name == "peername":
            return ("127.0.0.1", 10000)
        return None


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)
    proxy._hm.should_try_cloud = MagicMock(return_value=True)
    proxy._hm.should_route_settings_via_twin = MagicMock(return_value=False)
    proxy.device_id = "DEV1"
    proxy._active_box_peer = "peer"
    proxy._tc = MagicMock()
    proxy._close_writer = AsyncMock()
    proxy._read_box_bytes = AsyncMock()
    proxy._process_box_frame_common = AsyncMock(return_value=("DEV1", "tbl"))
    proxy._maybe_handle_local_control_poll = AsyncMock(return_value=False)
    proxy._handle_frame_local_offline = AsyncMock(return_value=(None, None))
    proxy._cf = MagicMock()
    proxy._cf.forward_frame = AsyncMock(return_value=(None, None))
    proxy._cf.session_connected = False
    proxy._cf.rx_buf = bytearray()
    proxy._pending_twin_activation = False
    proxy._twin_mode_active = False
    proxy._twin = None
    proxy.stats = {"acks_local": 0, "frames_forwarded": 0, "frames_received": 0}
    return proxy


@pytest.mark.asyncio
async def test_respond_local_offline_sends_ack(monkeypatch):
    proxy = _make_proxy()
    captured = []
    monkeypatch.setattr(proxy_module, "build_offline_ack_frame", lambda _tbl: b"ACK")
    monkeypatch.setattr(
        proxy_module,
        "capture_payload",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    writer = DummyWriter()

    await proxy._respond_local_offline(
        b"x",
        "tbl",
        "DEV1",
        writer,
        send_ack=True,
        conn_id=1,
    )

    assert writer.buffer == [b"ACK"]
    assert proxy.stats["acks_local"] == 1
    assert captured[-1][1]["direction"] == "proxy_to_box"
    assert captured[-1][0][1] == "tbl"


@pytest.mark.asyncio
async def test_handle_frame_local_offline_closes_cloud(monkeypatch):
    proxy = _make_proxy()
    proxy._cf.session_connected = True
    cloud_writer = DummyWriter()
    box_writer = DummyWriter()
    monkeypatch.setattr(proxy_module, "build_offline_ack_frame", lambda _tbl: b"ACK")

    reader, writer = await proxy_module.OIGProxy._handle_frame_local_offline(
        proxy,
        frame_bytes=b"x",
        table_name="tbl",
        device_id="DEV1",
        conn_id=1,
        box_writer=box_writer,
        cloud_writer=cloud_writer,
    )

    assert (reader, writer) == (None, None)
    proxy._close_writer.assert_awaited_once()
    assert box_writer.buffer == [b"ACK"]


@pytest.mark.asyncio
async def test_handle_box_connection_offline_path_uses_local_handler():
    proxy = _make_proxy()
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.OFFLINE)

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._handle_frame_local_offline.assert_called_once()
    proxy._cf.forward_frame.assert_not_called()


@pytest.mark.asyncio
async def test_handle_box_connection_hybrid_no_cloud_uses_local_handler():
    proxy = _make_proxy()
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.HYBRID)
    proxy._hm.should_try_cloud = MagicMock(return_value=False)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._handle_frame_local_offline.assert_called_once()
    proxy._cf.forward_frame.assert_not_called()


@pytest.mark.asyncio
async def test_handle_box_connection_online_path_uses_cloud_forwarder():
    proxy = _make_proxy()
    proxy._hm.get_current_mode = AsyncMock(return_value=ProxyMode.ONLINE)
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._cf.forward_frame.assert_called_once()


@pytest.mark.asyncio
async def test_handle_box_connection_processing_error_is_guarded():
    proxy = _make_proxy()
    proxy._read_box_bytes = AsyncMock(side_effect=[b"frame", None])
    # _process_box_frame_with_guard catches ValueError and continues safely.
    proxy._process_box_frame_common = AsyncMock(side_effect=ValueError("boom"))

    await proxy._handle_box_connection(
        box_reader=MagicMock(),
        box_writer=DummyWriter(),
        conn_id=1,
    )

    proxy._cf.forward_frame.assert_not_called()


@pytest.mark.asyncio
async def test_session_twin_mode_deactivates_when_idle():
    proxy = _make_proxy()
    proxy._twin_mode_active = True
    proxy._pending_twin_activation = False
    proxy._hm.should_route_settings_via_twin = MagicMock(return_value=False)
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=None),
        get_queue_length=AsyncMock(return_value=0),
    )

    await proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=7)

    assert proxy._twin_mode_active is False


@pytest.mark.asyncio
async def test_session_twin_mode_kept_when_inflight_exists():
    proxy = _make_proxy()
    proxy._twin_mode_active = True
    proxy._pending_twin_activation = False
    proxy._hm.should_route_settings_via_twin = MagicMock(return_value=False)
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=SimpleNamespace(tx_id="tx-1")),
        get_queue_length=AsyncMock(return_value=0),
    )

    await proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=8)

    assert proxy._twin_mode_active is True


@pytest.mark.asyncio
async def test_session_twin_mode_kept_when_queue_not_empty():
    proxy = _make_proxy()
    proxy._twin_mode_active = True
    proxy._pending_twin_activation = False
    proxy._hm.should_route_settings_via_twin = MagicMock(return_value=False)
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=None),
        get_queue_length=AsyncMock(return_value=2),
    )

    await proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=9)

    assert proxy._twin_mode_active is True


@pytest.mark.asyncio
async def test_control_pipeline_note_box_disconnect_is_noop():
    ctrl = ctrl_module.ControlPipeline.__new__(ctrl_module.ControlPipeline)
    ctrl.inflight = {"tx_id": "tx-1"}

    await ctrl.note_box_disconnect()

    assert ctrl.inflight == {"tx_id": "tx-1"}


@pytest.mark.asyncio
async def test_control_settings_queue_setting_requires_twin():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy._twin = None
    cs = cs_module.ControlSettings(proxy)

    result = await cs.queue_setting(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="1",
        confirm="New",
    )

    assert result == {"ok": False, "error": "twin_unavailable"}


def test_control_settings_send_setting_routes_twin_when_flag_on(monkeypatch):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._twin = object()
    proxy._twin_kill_switch = False
    cs = cs_module.ControlSettings(proxy)
    cs.send_via_event_loop = MagicMock(return_value={"ok": True, "tx_id": "tx-1"})
    cs.send_via_event_loop_legacy = MagicMock(return_value={"ok": True, "route": "legacy"})
    monkeypatch.setattr(cs_module, "CONTROL_TWIN_FIRST_ENABLED", True)

    result = cs.send_setting(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="3",
        confirm="New",
    )

    assert result["ok"] is True
    cs.send_via_event_loop.assert_called_once()
    cs.send_via_event_loop_legacy.assert_not_called()
    assert cs.set_commands_buffer[-1]["source"] == "twin"


def test_control_settings_send_setting_routes_legacy_when_flag_off(monkeypatch):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._twin = object()
    proxy._twin_kill_switch = False
    cs = cs_module.ControlSettings(proxy)
    cs.send_via_event_loop = MagicMock(return_value={"ok": True, "tx_id": "tx-1"})
    cs.send_via_event_loop_legacy = MagicMock(return_value={"ok": True, "route": "legacy"})
    monkeypatch.setattr(cs_module, "CONTROL_TWIN_FIRST_ENABLED", False)

    result = cs.send_setting(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="3",
        confirm="New",
    )

    assert result["ok"] is True
    cs.send_via_event_loop.assert_not_called()
    cs.send_via_event_loop_legacy.assert_called_once()
    assert cs.set_commands_buffer[-1]["source"] == "legacy_fallback"


def test_control_settings_send_setting_falls_back_to_legacy_when_twin_fails(monkeypatch):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._twin = object()
    proxy._twin_kill_switch = False
    cs = cs_module.ControlSettings(proxy)
    cs.send_via_event_loop = MagicMock(return_value={"ok": False, "error": "twin_unavailable"})
    cs.send_via_event_loop_legacy = MagicMock(return_value={"ok": True, "route": "legacy"})
    monkeypatch.setattr(cs_module, "CONTROL_TWIN_FIRST_ENABLED", True)

    result = cs.send_setting(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="3",
        confirm="New",
    )

    assert result["ok"] is True
    cs.send_via_event_loop.assert_called_once()
    cs.send_via_event_loop_legacy.assert_called_once()
    assert cs.set_commands_buffer[-1]["source"] == "legacy_fallback"


@pytest.mark.asyncio
async def test_control_settings_queue_setting_arms_twin_session_activation_flags():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.box_connected = True
    proxy._pending_twin_activation = False
    proxy._twin_mode_active = False
    proxy._twin = SimpleNamespace(
        queue_setting=AsyncMock(return_value=SimpleNamespace(tx_id="tx-1", status="accepted"))
    )
    cs = cs_module.ControlSettings(proxy)

    result = await cs.queue_setting(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="3",
        confirm="New",
    )

    assert result["ok"] is True
    assert proxy._pending_twin_activation is True
    assert proxy._twin_mode_active is True


def test_stale_frame_detector_emits_warning_after_threshold(monkeypatch):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._active_box_peer = "10.0.0.166:1972"
    proxy._stale_table_name = None
    proxy._stale_payload_hash = None
    proxy._stale_first_seen_epoch = None
    proxy._stale_last_seen_epoch = None
    proxy._stale_repeat_count = 0
    proxy._stale_last_log_epoch = None

    calls = []

    def fake_warning(msg, *args):
        calls.append(msg % args)

    monkeypatch.setattr(proxy_module.logger, "warning", fake_warning)

    base = 1_000_000.0
    ts = [base + i * 2.0 for i in range(121)]
    it = iter(ts)
    monkeypatch.setattr(proxy_module.time, "time", lambda: next(it))

    frame = b"<Frame><TblName>tbl_dc_in</TblName><FV_P1>478</FV_P1></Frame>"
    for _ in range(121):
        proxy._update_stale_frame_detector(frame_bytes=frame, table_name="tbl_dc_in")

    assert len(calls) == 1
    assert "STALE_STREAM detected" in calls[0]
    expected_hash = hashlib.sha256(frame).hexdigest()[:12]
    assert expected_hash in calls[0]


def test_stale_frame_detector_resets_on_payload_change(monkeypatch):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._active_box_peer = "10.0.0.166:1972"
    proxy._stale_table_name = None
    proxy._stale_payload_hash = None
    proxy._stale_first_seen_epoch = None
    proxy._stale_last_seen_epoch = None
    proxy._stale_repeat_count = 0
    proxy._stale_last_log_epoch = None

    monkeypatch.setattr(proxy_module.logger, "warning", lambda *_args: None)
    now = 2_000_000.0
    monkeypatch.setattr(proxy_module.time, "time", lambda: now)

    proxy._update_stale_frame_detector(frame_bytes=b"A", table_name="tbl_dc_in")
    assert proxy._stale_repeat_count == 1
    first_hash = proxy._stale_payload_hash

    proxy._update_stale_frame_detector(frame_bytes=b"B", table_name="tbl_dc_in")
    assert proxy._stale_repeat_count == 1
    assert proxy._stale_payload_hash != first_hash
