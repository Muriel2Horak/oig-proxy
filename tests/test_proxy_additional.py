# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import asyncio
import time
from collections import deque, defaultdict

import proxy as proxy_module
from tests.fixtures.dummy import DummyQueue, DummyWriter, DummyReader
from tests.mqtt_dummy_helpers import DummyMQTTMixin
from models import ProxyMode


class DummyMQTT(DummyMQTTMixin):
    def __init__(self) -> None:
        self.queue = DummyQueue()
        self.device_id = "DEV1"
        self.connected = True
        self.discovery_sent = set()
        self._last_payload_by_topic = {}
        self.published_raw = []
        self.published_data = []
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    def publish_availability(self):
        self.published_raw.append(("availability", None))

    async def publish_raw(
            self,
            *,
            topic: str,
            payload: str,
            qos: int,
            retain: bool):
        self.published_raw.append((topic, payload, qos, retain))
        return True

    async def publish_data(self, payload):
        self.published_data.append(payload)
        return True

    def add_message_handler(self, *, topic, handler, qos):
        return None


class DummyParser:
    def __init__(self, parsed=None) -> None:
        self._parsed = parsed or {}

    def parse_xml_frame(self, _frame):
        return dict(self._parsed)

    def parse_mode_from_event(self, _content):
        return None


def make_proxy(tmp_path):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy.mode_lock = asyncio.Lock()
    proxy._configured_mode = "online"
    proxy._hybrid_fail_count = 0
    proxy._hybrid_fail_threshold = 3
    proxy._hybrid_retry_interval = 300.0
    proxy._hybrid_connect_timeout = 5.0
    proxy._hybrid_last_offline_time = 0.0
    proxy._hybrid_in_offline = False
    proxy.stats = {
        "frames_received": 0,
        "frames_forwarded": 0,
        "acks_local": 0,
        "acks_cloud": 0,
        "mode_changes": 0,
    }
    proxy.mqtt_publisher = DummyMQTT()
    proxy.parser = DummyParser()
    proxy._active_box_peer = "1.2.3.4:1234"
    proxy._box_conn_lock = asyncio.Lock()
    proxy._active_box_writer = None
    proxy._conn_seq = 0
    proxy._control_lock = asyncio.Lock()
    proxy._control_queue = deque()
    proxy._control_inflight = None
    proxy._control_last_result = None
    proxy._control_session_id = "sess"
    proxy._control_qos = 1
    proxy._control_retain = False
    proxy._control_status_retain = False
    proxy._control_result_topic = "oig/control/result"
    proxy._control_status_prefix = "oig/control/status"
    proxy._control_set_topic = "oig/control/set"
    proxy._control_log_enabled = False
    proxy._control_log_path = str(tmp_path / "control.log")
    proxy._control_pending_path = str(tmp_path / "pending.json")
    proxy._control_pending_keys = set()
    proxy._control_max_attempts = 2
    proxy._control_retry_delay_s = 0.01
    proxy._control_ack_timeout_s = 0.01
    proxy._control_applied_timeout_s = 0.01
    proxy._control_mode_quiet_s = 0.01
    proxy._control_retry_task = None
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_quiet_task = None
    proxy._control_post_drain_refresh_pending = False
    proxy._control_whitelist = {"tbl_box_prms": {"MODE", "SA"}}
    proxy._control_box_ready_s = 0.0
    proxy._control_mqtt_enabled = False
    proxy._proxy_status_attrs_topic = "oig/status/attrs"
    proxy._prms_tables = {}
    proxy._prms_device_id = None
    proxy._mode_value = None
    proxy._mode_device_id = None
    proxy._mode_pending_publish = False
    proxy._prms_pending_publish = False
    proxy._table_cache = {}
    proxy._last_values = {}
    proxy._mqtt_cache_device_id = None
    proxy._mqtt_was_ready = False
    proxy._status_task = None
    proxy._box_connected_since_epoch = None
    proxy._last_box_disconnect_reason = None
    proxy._last_data_epoch = None
    proxy._last_data_iso = None
    proxy._isnew_polls = 0
    proxy._isnew_last_poll_iso = None
    proxy._isnew_last_response = None
    proxy._isnew_last_rtt_ms = None
    proxy._isnew_last_poll_epoch = None
    proxy.cloud_connects = 0
    proxy.cloud_disconnects = 0
    proxy.cloud_timeouts = 0
    proxy.cloud_errors = 0
    proxy.cloud_session_connected = False
    proxy._cloud_connected_since_epoch = None
    proxy._cloud_peer = None
    proxy.box_connected = False
    proxy.box_connections = 0
    proxy._hb_interval_s = 0.0
    proxy._last_hb_ts = 0.0
    proxy._local_getactual_enabled = True
    proxy._local_getactual_interval_s = 0.0
    proxy._local_getactual_task = None
    proxy._full_refresh_interval_h = 1
    proxy._full_refresh_task = None
    proxy._local_setting_pending = None
    proxy._telemetry_client = None
    proxy._set_commands_buffer = []
    proxy._telemetry_interval_s = 300
    proxy._telemetry_box_sessions = deque()
    proxy._telemetry_cloud_sessions = deque()
    proxy._telemetry_offline_events = deque()
    proxy._telemetry_tbl_events = deque()
    proxy._telemetry_error_context = deque()
    proxy._telemetry_logs = deque()
    proxy._telemetry_log_window_s = 60
    proxy._telemetry_log_max = 1000
    proxy._telemetry_debug_windows_remaining = 0
    proxy._telemetry_box_seen_in_window = False
    proxy._telemetry_cloud_ok_in_window = False
    proxy._telemetry_cloud_failed_in_window = False
    proxy._telemetry_cloud_eof_short_in_window = False
    proxy._telemetry_req_pending = defaultdict(deque)
    proxy._telemetry_stats = {}
    return proxy


def test_publish_proxy_status_handles_errors(tmp_path):
    proxy = make_proxy(tmp_path)

    def fail(*_args, **_kwargs):
        raise RuntimeError("fail")

    proxy.mqtt_publisher.publish_proxy_status = fail
    proxy.mqtt_publisher.publish_raw = fail

    asyncio.run(proxy.publish_proxy_status())


def test_getactual_frames_and_send(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    monkeypatch.setattr(
        proxy_module,
        "capture_payload",
        lambda *_args,
        **_kwargs: None)
    writer = DummyWriter()

    asyncio.run(proxy._send_getactual_to_box(writer, conn_id=1))
    assert writer.data
    assert b"<Result>ACK</Result>" in proxy._build_getactual_frame()
    assert b"<Result>END</Result>" in proxy._build_offline_ack_frame("END")


def test_local_getactual_loop_exits_when_closed(tmp_path):
    proxy = make_proxy(tmp_path)
    writer = DummyWriter()
    writer._closing = True

    asyncio.run(proxy._local_getactual_loop(writer, conn_id=1))


def test_full_refresh_loop_triggers_send(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    proxy.box_connected = True
    proxy._control_inflight = None
    proxy._control_queue = []

    async def fake_mode():
        return ProxyMode.ONLINE

    proxy._get_current_mode = fake_mode
    calls = {"send": 0, "sleep": 0}

    async def fake_send(**_):
        calls["send"] += 1
        raise RuntimeError("boom")

    async def fake_sleep(_interval):
        calls["sleep"] += 1
        if calls["sleep"] == 1:
            return None
        raise RuntimeError("stop")

    proxy._send_setting_to_box = fake_send
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(proxy._full_refresh_loop())
    except RuntimeError:
        # Expected: fake_sleep raises RuntimeError("stop") to break the loop.
        pass
    assert calls["send"] == 1


def test_proxy_status_loop_disabled(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    monkeypatch.setattr(proxy_module, "PROXY_STATUS_INTERVAL", 0)
    asyncio.run(proxy._proxy_status_loop())


def test_proxy_status_loop_runs_once(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    proxy.mode = ProxyMode.OFFLINE
    proxy._hb_interval_s = 0.01
    proxy._last_data_epoch = time.time() - 5
    proxy._box_connected_since_epoch = time.time() - 10

    calls = {"sleep": 0, "status": 0}

    async def fake_sleep(_interval):
        calls["sleep"] += 1
        if calls["sleep"] == 1:
            return None
        raise RuntimeError("stop")

    async def fake_status():
        calls["status"] += 1

    proxy.publish_proxy_status = fake_status
    monkeypatch.setattr(proxy_module, "PROXY_STATUS_INTERVAL", 1)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(proxy._proxy_status_loop())
    except RuntimeError:
        # Expected: fake_sleep raises RuntimeError("stop") to break the loop.
        pass

    assert calls["status"] == 1


def test_read_box_bytes_timeout_and_reset(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)

    async def fake_publish():
        return None

    proxy.publish_proxy_status = fake_publish

    async def timeout_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout_wait_for)
    res = asyncio.run(
        proxy._read_box_bytes(
            DummyReader(
                [b"data"]),
            conn_id=1,
            idle_timeout_s=0.1))
    assert res is None

    async def reset_wait_for(coro, timeout):
        coro.close()
        raise ConnectionResetError

    monkeypatch.setattr(asyncio, "wait_for", reset_wait_for)
    res = asyncio.run(
        proxy._read_box_bytes(
            DummyReader(
                [b"data"]),
            conn_id=2,
            idle_timeout_s=0.1))
    assert res is None


def test_read_box_bytes_eof(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)

    async def fake_publish():
        return None

    proxy.publish_proxy_status = fake_publish

    async def passthrough_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", passthrough_wait_for)
    res = asyncio.run(
        proxy._read_box_bytes(
            DummyReader(
                [b""]),
            conn_id=3,
            idle_timeout_s=0.1))
    assert res is None


def test_ensure_cloud_connected_success_and_failure(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)

    async def fake_open(_host, _port):
        return DummyReader([]), DummyWriter()

    monkeypatch.setattr(proxy_module, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    reader, writer, _attempted = asyncio.run(
        proxy._ensure_cloud_connected(
            None,
            None,
            conn_id=1,
            table_name="tbl_actual",
            connect_timeout_s=0.1,
        )
    )
    assert reader is not None
    assert writer is not None
    assert proxy.cloud_session_connected is True
    assert proxy.cloud_connects == 1

    async def fake_fail(_host, _port):
        raise RuntimeError("fail")

    monkeypatch.setattr(asyncio, "open_connection", fake_fail)
    writer._closing = True
    reader, writer, _attempted = asyncio.run(
        proxy._ensure_cloud_connected(
            reader,
            writer,
            conn_id=2,
            table_name="tbl_actual",
            connect_timeout_s=0.1,
        )
    )
    assert reader is None
    assert writer is None
    assert proxy.cloud_errors == 1


def test_ensure_cloud_connected_force_offline(tmp_path):
    """Test that _ensure_cloud_connected returns None when configured as offline."""
    proxy = make_proxy(tmp_path)
    proxy.cloud_session_connected = True
    proxy._configured_mode = "offline"  # Set to OFFLINE mode
    closed = {"called": False}

    async def fake_close(_writer):
        closed["called"] = True

    proxy._close_writer = fake_close

    reader, writer, _attempted = asyncio.run(
        proxy._ensure_cloud_connected(
            None,
            DummyWriter(),
            conn_id=1,
            table_name="tbl_actual",
            connect_timeout_s=0.1,
        )
    )
    assert reader is None
    assert writer is None
    assert closed["called"] is True
    assert proxy.cloud_session_connected is False


def test_forward_frame_online_success(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    proxy._last_data_iso = "2025-01-01T00:00:00Z"
    proxy._isnew_last_poll_epoch = time.time() - 1
    box_writer = DummyWriter()
    cloud_reader = DummyReader([b"<Frame>ACK</Frame>"])
    cloud_writer = DummyWriter()

    async def fake_ensure(_reader, _writer, **_kwargs):
        return cloud_reader, cloud_writer, True

    proxy._ensure_cloud_connected = fake_ensure
    monkeypatch.setattr(
        proxy_module,
        "capture_payload",
        lambda *_args,
        **_kwargs: None)

    asyncio.run(
        proxy._forward_frame_online(
            frame_bytes=b"<Frame></Frame>",
            table_name="IsNewSet",
            device_id="DEV1",
            conn_id=1,
            box_writer=box_writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )
    )
    assert proxy.stats["frames_forwarded"] == 1
    assert proxy.stats["acks_cloud"] == 1


def _setup_cloud_timeout(proxy, monkeypatch):
    box_writer = DummyWriter()
    cloud_reader = DummyReader([b"<Frame>ACK</Frame>"])
    cloud_writer = DummyWriter()

    async def fake_ensure(_reader, _writer, **_kwargs):
        return cloud_reader, cloud_writer, True

    proxy._ensure_cloud_connected = fake_ensure

    async def timeout_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout_wait_for)
    return box_writer


def test_forward_frame_online_timeout_end_transparent(tmp_path, monkeypatch):
    """In ONLINE mode, END timeout does NOT send local ACK - fully transparent."""
    proxy = make_proxy(tmp_path)
    proxy._configured_mode = "online"  # ONLINE mode: transparent
    box_writer = _setup_cloud_timeout(proxy, monkeypatch)

    asyncio.run(
        proxy._forward_frame_online(
            frame_bytes=b"<Frame>END</Frame>",
            table_name="END",
            device_id="DEV1",
            conn_id=2,
            box_writer=box_writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )
    )
    # ONLINE: no local ACK, BOX times out
    assert proxy.stats["acks_local"] == 0
    assert proxy.cloud_timeouts == 1


def test_forward_frame_hybrid_timeout_end_local_ack(tmp_path, monkeypatch):
    """In HYBRID mode (after threshold), END timeout sends local END ACK."""
    proxy = make_proxy(tmp_path)
    proxy._configured_mode = "hybrid"  # HYBRID mode
    proxy._hybrid_in_offline = True  # Already reached threshold
    box_writer = _setup_cloud_timeout(proxy, monkeypatch)

    asyncio.run(
        proxy._forward_frame_online(
            frame_bytes=b"<Frame>END</Frame>",
            table_name="END",
            device_id="DEV1",
            conn_id=2,
            box_writer=box_writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )
    )
    # HYBRID in offline: sends local END ACK
    assert proxy.stats["acks_local"] == 1
    assert proxy.cloud_timeouts == 1


def test_forward_frame_online_timeout_non_end(tmp_path, monkeypatch):
    """In HYBRID mode (after threshold), timeout on non-END frame triggers fallback to local ACK."""
    proxy = make_proxy(tmp_path)
    proxy._configured_mode = "hybrid"  # HYBRID mode does fallback
    proxy._hybrid_in_offline = True  # Already reached threshold
    box_writer = DummyWriter()
    cloud_reader = DummyReader([b"<Frame>ACK</Frame>"])
    cloud_writer = DummyWriter()
    called = []

    async def fake_ensure(_reader, _writer, **_kwargs):
        return cloud_reader, cloud_writer, True

    async def fake_fallback(**kwargs):
        called.append(kwargs["reason"])
        return None, None

    proxy._ensure_cloud_connected = fake_ensure
    proxy._fallback_offline_from_cloud_issue = fake_fallback

    async def timeout_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout_wait_for)

    asyncio.run(
        proxy._forward_frame_online(
            frame_bytes=b"<Frame>DATA</Frame>",
            table_name="tbl_actual",
            device_id="DEV1",
            conn_id=4,
            box_writer=box_writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )
    )
    assert called == ["ack_timeout"]
    assert proxy.cloud_timeouts == 1


def test_forward_frame_online_timeout_non_end_online_mode(
        tmp_path, monkeypatch):
    """In ONLINE mode, timeout on non-END frame does NOT trigger fallback - BOX times out."""
    proxy = make_proxy(tmp_path)
    proxy._configured_mode = "online"  # ONLINE mode: no fallback
    box_writer = DummyWriter()
    cloud_reader = DummyReader([b"<Frame>ACK</Frame>"])
    cloud_writer = DummyWriter()
    closed = {"called": False}

    async def fake_ensure(_reader, _writer, **_kwargs):
        return cloud_reader, cloud_writer, True

    async def fake_close(_writer):
        closed["called"] = True

    proxy._ensure_cloud_connected = fake_ensure
    proxy._close_writer = fake_close

    async def timeout_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout_wait_for)

    reader, writer = asyncio.run(
        proxy._forward_frame_online(
            frame_bytes=b"<Frame>DATA</Frame>",
            table_name="tbl_actual",
            device_id="DEV1",
            conn_id=4,
            box_writer=box_writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )
    )
    # ONLINE mode: no fallback, just return None
    assert reader is None
    assert writer is None
    assert proxy.cloud_timeouts == 1
    assert closed["called"] is True  # writer should be closed


def test_forward_frame_online_exception(tmp_path, monkeypatch):
    """In HYBRID mode (after threshold), cloud exception triggers fallback to local ACK."""
    proxy = make_proxy(tmp_path)
    proxy._configured_mode = "hybrid"  # HYBRID mode does fallback
    proxy._hybrid_in_offline = True  # Already reached threshold
    box_writer = DummyWriter()
    cloud_reader = DummyReader([RuntimeError("boom")])
    cloud_writer = DummyWriter()
    called = []

    async def fake_ensure(_reader, _writer, **_kwargs):
        return cloud_reader, cloud_writer, True

    async def fake_fallback(**kwargs):
        called.append(kwargs["reason"])
        return None, None

    async def fake_wait_for(coro, timeout):
        return await coro

    proxy._ensure_cloud_connected = fake_ensure
    proxy._fallback_offline_from_cloud_issue = fake_fallback
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    asyncio.run(
        proxy._forward_frame_online(
            frame_bytes=b"<Frame>DATA</Frame>",
            table_name="tbl_actual",
            device_id="DEV1",
            conn_id=5,
            box_writer=box_writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )
    )
    assert called == ["cloud_error"]
    assert proxy.cloud_errors == 1


def test_forward_frame_online_ack_eof(tmp_path, monkeypatch):
    """In HYBRID mode (after threshold), cloud EOF triggers fallback to local ACK."""
    proxy = make_proxy(tmp_path)
    proxy._configured_mode = "hybrid"  # HYBRID mode does fallback
    proxy._hybrid_in_offline = True  # Already reached threshold
    box_writer = DummyWriter()
    cloud_reader = DummyReader([b""])
    cloud_writer = DummyWriter()
    called = []

    async def fake_ensure(_reader, _writer, **_kwargs):
        return cloud_reader, cloud_writer, True

    async def fake_fallback(**_kwargs):
        called.append("fallback")
        return None, None

    proxy._ensure_cloud_connected = fake_ensure
    proxy._fallback_offline_from_cloud_issue = fake_fallback

    asyncio.run(
        proxy._forward_frame_online(
            frame_bytes=b"<Frame></Frame>",
            table_name="tbl_actual",
            device_id="DEV1",
            conn_id=3,
            box_writer=box_writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )
    )
    assert called == ["fallback"]
    assert proxy.cloud_disconnects == 1


def test_handle_box_connection_online(tmp_path):
    proxy = make_proxy(tmp_path)
    reader = DummyReader([b"<Frame></Frame>", b""])
    writer = DummyWriter()
    called = []

    async def fake_read(_reader, **_kwargs):
        data = await _reader.read(8192)
        if data == b"":
            return None
        return data

    async def fake_process(**_kwargs):
        return "DEV1", "tbl_actual"

    def fake_ack(_frame, _writer, *, conn_id):
        del conn_id
        return False

    async def fake_mode():
        return ProxyMode.ONLINE

    async def fake_forward(**_kwargs):
        called.append("forward")
        return None, None

    proxy._read_box_bytes = fake_read
    proxy._process_box_frame_common = fake_process
    proxy._maybe_handle_local_setting_ack = fake_ack
    proxy._get_current_mode = fake_mode
    proxy._forward_frame_online = fake_forward

    asyncio.run(proxy._handle_box_connection(reader, writer, conn_id=1))
    assert called == ["forward"]


def test_handle_box_connection_offline(tmp_path):
    proxy = make_proxy(tmp_path)
    reader = DummyReader([b"<Frame></Frame>", b""])
    writer = DummyWriter()
    called = []

    async def fake_read(_reader, **_kwargs):
        data = await _reader.read(8192)
        if data == b"":
            return None
        return data

    async def fake_process(**_kwargs):
        return "DEV1", "tbl_actual"

    def fake_ack(_frame, _writer, *, conn_id):
        del conn_id
        return False

    async def fake_mode():
        return ProxyMode.OFFLINE

    async def fake_offline(**_kwargs):
        called.append("offline")
        return None, None

    proxy._read_box_bytes = fake_read
    proxy._process_box_frame_common = fake_process
    proxy._maybe_handle_local_setting_ack = fake_ack
    proxy._get_current_mode = fake_mode
    proxy._handle_frame_offline_mode = fake_offline

    asyncio.run(proxy._handle_box_connection(reader, writer, conn_id=2))
    assert called == ["offline"]


def test_handle_connection_lifecycle(tmp_path):
    proxy = make_proxy(tmp_path)
    reader = DummyReader([b""])
    writer = DummyWriter()
    proxy._local_getactual_task = None

    async def fake_status():
        return None

    async def fake_loop(*_args, **_kwargs):
        return None

    async def fake_handle(*_args, **_kwargs):
        return None

    async def fake_close(_writer):
        return None

    async def fake_unregister(_writer):
        return None

    proxy.publish_proxy_status = fake_status
    proxy._local_getactual_loop = fake_loop
    proxy._handle_box_connection = fake_handle
    proxy._close_writer = fake_close
    proxy._unregister_box_connection = fake_unregister

    asyncio.run(proxy.handle_connection(reader, writer))
    assert proxy.box_connected is False


def test_tune_socket_sets_options():
    class DummySocket:
        def __init__(self):
            self.calls = []

        def setsockopt(self, *args):
            self.calls.append(args)

    class SocketWriter:
        def __init__(self):
            self.sock = DummySocket()

        def get_extra_info(self, name):
            if name == "socket":
                return self.sock
            return None

    writer = SocketWriter()
    proxy_module.OIGProxy._tune_socket(writer)  # type: ignore[arg-type]
    assert writer.sock.calls


def test_control_note_box_disconnect_marks_tx(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_inflight = {"stage": "sent_to_box"}
    asyncio.run(proxy._control_note_box_disconnect())
    assert proxy._control_inflight["disconnected"] is True


def test_local_getactual_loop_sends_and_logs(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    writer = DummyWriter()
    proxy.box_connected = True

    async def fake_send(*_args, **_kwargs):
        raise RuntimeError("fail")

    async def fake_sleep(_interval):
        raise RuntimeError("stop")

    proxy._send_getactual_to_box = fake_send
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(proxy._local_getactual_loop(writer, conn_id=1))
    except RuntimeError:
        # Expected: fake_sleep/fake_send raise RuntimeError to stop the loop.
        pass


def test_force_offline_enabled_property(tmp_path):
    """Test _force_offline_enabled property logic for OFFLINE mode detection."""
    proxy = make_proxy(tmp_path)

    # configured_mode = "offline" means force_offline enabled
    proxy._configured_mode = "offline"
    assert proxy._force_offline_enabled() is True

    # configured_mode = "hybrid" - not force_offline
    proxy._configured_mode = "hybrid"
    assert proxy._force_offline_enabled() is False

    # configured_mode = "online" - not force_offline
    proxy._configured_mode = "online"
    assert proxy._force_offline_enabled() is False


def test_handle_setting_event_publishes(tmp_path):
    proxy = make_proxy(tmp_path)
    called = []

    async def fake_publish(**kwargs):
        called.append(kwargs)

    proxy._publish_setting_event_state = fake_publish
    parsed = {
        "Type": "Setting",
        "Content": "Remotely : tbl_box_prms / MODE: [0]->[1]"}

    asyncio.run(proxy._handle_setting_event(parsed, "tbl_events", "DEV1"))
    assert called


def test_process_frame_offline_skips_all_data_sent(tmp_path):
    """Test that offline mode just sends local ACK without any queueing."""
    proxy = make_proxy(tmp_path)
    writer = DummyWriter()

    frame = b"<Frame><Result>END</Result><Reason>All data sent</Reason></Frame>"
    asyncio.run(
        proxy._process_frame_offline(
            frame,
            "END",
            "DEV1",
            writer,
            send_ack=True))
    # Verify ACK was sent
    assert proxy.stats["acks_local"] >= 1


def test_extract_device_and_table_infers_result(tmp_path):
    proxy = make_proxy(tmp_path)
    parsed = {"Result": "IsNewSet"}
    device_id, table_name = proxy._extract_device_and_table(parsed)
    assert device_id is None
    assert table_name == "IsNewSet"
    assert parsed["_table"] == "IsNewSet"


def test_process_box_frame_common_isnew_updates(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    proxy.device_id = "AUTO"
    proxy.mqtt_publisher.device_id = "AUTO"
    proxy._setup_mqtt_state_cache = lambda: None
    proxy._maybe_persist_table_state = lambda *_args, **_kwargs: None

    async def async_noop(*_args, **_kwargs):
        return None

    proxy._handle_setting_event = async_noop
    proxy._control_observe_box_frame = async_noop
    proxy._maybe_process_mode = async_noop
    proxy._control_maybe_start_next = async_noop
    monkeypatch.setattr(
        proxy_module,
        "capture_payload",
        lambda *_args,
        **_kwargs: None)

    proxy.parser = DummyParser({"Result": "IsNewSet"})
    frame = "<Frame><ID_Device>123</ID_Device><Result>IsNewSet</Result></Frame>"
    device_id, table_name = asyncio.run(
        proxy._process_box_frame_common(
            frame_bytes=frame.encode("utf-8"),
            frame=frame,
            conn_id=1,
        )
    )
    assert table_name == "IsNewSet"
    assert device_id == "123"
    assert proxy._isnew_polls == 1
    assert proxy._isnew_last_poll_epoch is not None
    assert proxy._isnew_last_poll_iso is not None
    assert proxy.device_id == "123"
