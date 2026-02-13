# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import asyncio
from collections import deque
from unittest.mock import MagicMock
import proxy as proxy_module
import cloud_forwarder as cf_module
from cloud_forwarder import CloudForwarder
from control_pipeline import ControlPipeline
from control_settings import ControlSettings
from models import ProxyMode
from mqtt_state_cache import MqttStateCache
from tests.mqtt_dummy_helpers import DummyMQTTMixin


def _make_real_ctrl(proxy, tmp_path):
    """Create a real ControlPipeline (bypassing __init__) for tests that call its async methods."""
    ctrl = ControlPipeline.__new__(ControlPipeline)
    ctrl._proxy = proxy
    ctrl.mqtt_enabled = False
    ctrl.set_topic = "oig/control/set"
    ctrl.result_topic = "oig/control/result"
    ctrl.status_prefix = "oig/control/status"
    ctrl.qos = 1
    ctrl.retain = False
    ctrl.status_retain = False
    ctrl.log_enabled = False
    ctrl.log_path = str(tmp_path / "control.log")
    ctrl.box_ready_s = 0.0
    ctrl.ack_timeout_s = 0.01
    ctrl.applied_timeout_s = 0.01
    ctrl.mode_quiet_s = 0.01
    ctrl.whitelist = {"tbl_box_prms": {"MODE", "SA"}}
    ctrl.max_attempts = 2
    ctrl.retry_delay_s = 0.01
    ctrl.session_id = "test-session"
    ctrl.pending_path = str(tmp_path / "pending.json")
    ctrl.pending_keys = set()
    ctrl.queue = deque()
    ctrl.inflight = None
    ctrl.lock = asyncio.Lock()
    ctrl.ack_task = None
    ctrl.applied_task = None
    ctrl.quiet_task = None
    ctrl.retry_task = None
    ctrl.last_result = None
    ctrl.key_state = {}
    ctrl.post_drain_refresh_pending = False
    return ctrl


class DummyWriter:
    def __init__(self):
        self.data = []
        self._closing = False

    def is_closing(self):
        return self._closing

    def write(self, data):
        self.data.append(data)

    async def drain(self):
        return None

    def close(self):
        self._closing = True

    async def wait_closed(self):
        return None


class DummyReader:
    def __init__(self, payload: bytes):
        self._payload = payload

    async def read(self, _size):
        return self._payload


class DummyMQTT(DummyMQTTMixin):
    def __init__(self):
        self.device_id = "AUTO"
        self.discovery_sent = {"x"}
        self.published = []
        self.handlers = []
        self.connected = False
        self._last_payload_by_topic = {}
        self.queue = type("Q", (), {"size": lambda self: 0})()

    def publish_availability(self):
        self.published.append("availability")

    async def publish_data(self, payload):
        self.published.append(payload)

    async def publish_raw(self, *, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))

    def add_message_handler(self, *, topic, handler, qos):
        self.handlers.append((topic, qos))

    def is_ready(self):
        return True


class DummyParser:
    def __init__(self, parsed):
        self._parsed = parsed

    def parse_xml_frame(self, _frame):
        return self._parsed

    def parse_mode_from_event(self, _content):
        return None


def _make_proxy(tmp_path):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "AUTO"
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._hm.mode_lock = asyncio.Lock()
    proxy._hm.force_offline_enabled.return_value = False
    proxy._hm.should_try_cloud.return_value = True
    proxy._hm.is_hybrid_mode.return_value = False
    proxy.stats = {
        "frames_received": 0,
        "frames_forwarded": 0,
        "frames_queued": 0,
        "acks_local": 0,
        "acks_cloud": 0,
        "mode_changes": 0,
    }
    proxy.mqtt_publisher = DummyMQTT()
    proxy._cf = MagicMock()
    proxy.parser = DummyParser({})
    proxy._active_box_peer = None
    proxy._ctrl = MagicMock()
    proxy._ctrl.inflight = None
    proxy._ctrl.queue = []
    proxy._ctrl.last_result = None
    proxy._ctrl.lock = asyncio.Lock()
    proxy._ctrl.quiet_task = None
    proxy._ctrl.ack_task = None
    proxy._ctrl.applied_task = None
    proxy._ctrl.status_prefix = "oig/control/status"
    proxy._ctrl.qos = 1
    proxy._ctrl.status_retain = False
    proxy._ctrl.retain = False
    proxy._ctrl.result_topic = "oig/control/result"
    proxy._ctrl.set_topic = "oig/control/set"
    proxy._ctrl.pending_keys = set()
    proxy._ctrl.pending_path = str(tmp_path / "pending.json")
    proxy._ctrl.post_drain_refresh_pending = False
    proxy._prms_tables = {}
    proxy._prms_device_id = None
    proxy._msc = MagicMock()
    proxy._msc.table_cache = {}
    proxy._msc.last_values = {}
    proxy._msc.cache_device_id = None
    proxy._mode_value = None
    proxy._mode_device_id = None
    proxy._force_offline_config = False
    proxy._proxy_status_attrs_topic = "oig/status/attrs"
    proxy._last_data_iso = None
    proxy._last_data_epoch = None
    proxy._isnew_last_response = None
    proxy._isnew_last_poll_epoch = None
    proxy._isnew_last_rtt_ms = None
    proxy._isnew_polls = 0
    proxy._box_conn_lock = asyncio.Lock()
    proxy._active_box_writer = None
    proxy._conn_seq = 0
    proxy._loop = None
    proxy._hb_interval_s = 0.0
    proxy._last_hb_ts = 0.0
    proxy._hm.configured_mode = "online"
    proxy._hm.fail_count = 0
    proxy._hm.fail_threshold = 3
    proxy._hm.retry_interval = 300.0
    proxy._hm.connect_timeout = 5.0
    proxy._hm.last_offline_time = 0.0
    proxy._hm.in_offline = False
    proxy._tc = MagicMock()
    cs = ControlSettings.__new__(ControlSettings)
    cs._proxy = proxy
    cs.pending = None
    cs.set_commands_buffer = []
    proxy._cs = cs
    return proxy


def test_extract_and_autodetect_device_id(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    proxy.parser = DummyParser(
        {"_device_id": "DEV2", "_table": "tbl_box_prms", "MODE": 1})
    called = []

    async def fake_handle(*_args, **_kwargs):
        called.append("handle")

    async def fake_observe(*_args, **_kwargs):
        called.append("observe")

    async def fake_mode(*_args, **_kwargs):
        called.append("mode")

    async def fake_start(*_args, **_kwargs):
        called.append("start")

    proxy._handle_setting_event = fake_handle
    proxy._ctrl.observe_box_frame = fake_observe
    proxy._maybe_process_mode = fake_mode
    proxy._ctrl.maybe_start_next = fake_start
    monkeypatch.setattr(
        proxy_module,
        "capture_payload",
        lambda *_args,
        **_kwargs: None)

    async def run():
        device_id, table = await proxy._process_box_frame_common(
            frame_bytes=b"<Frame><TblName>tbl_box_prms</TblName></Frame>",
            frame="<Frame><TblName>tbl_box_prms</TblName></Frame>",
            conn_id=1,
        )
        return device_id, table

    device_id, table = asyncio.run(run())
    assert device_id == "DEV2"
    assert table == "tbl_box_prms"
    assert proxy.device_id == "DEV2"
    assert "availability" in proxy.mqtt_publisher.published


def test_process_box_frame_common_infers_from_frame(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    proxy.parser = DummyParser({})
    monkeypatch.setattr(
        proxy_module,
        "capture_payload",
        lambda *_args,
        **_kwargs: None)

    async def run():
        device_id, table = await proxy._process_box_frame_common(
            frame_bytes=b"<Frame><TblName>tbl_actual</TblName><ID_Device>123</ID_Device></Frame>",
            frame="<Frame><TblName>tbl_actual</TblName><ID_Device>123</ID_Device></Frame>",
            conn_id=1,
        )
        return device_id, table

    device_id, table = asyncio.run(run())
    assert device_id == "123"
    assert table == "tbl_actual"


def test_process_frame_offline_and_fallback(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    writer = DummyWriter()

    async def run():
        await proxy._process_frame_offline(
            b"<Frame><TblName>tbl_actual</TblName></Frame>",
            "tbl_actual",
            "DEV1",
            writer,
            send_ack=True,
        )

        await proxy._process_frame_offline(
            b"<Frame><Result>END</Result><Reason>All data sent</Reason></Frame>",
            "END",
            "DEV1",
            writer,
            send_ack=True,
        )

    asyncio.run(run())
    assert proxy.stats["acks_local"] >= 1

    called = []

    async def fake_process(*_args, **_kwargs):
        called.append("process")

    async def fake_note(*_args, **_kwargs):
        called.append("note")

    proxy._process_frame_offline = fake_process

    cf = CloudForwarder.__new__(CloudForwarder)
    cf._proxy = proxy
    cf.connects = 0
    cf.disconnects = 0
    cf.timeouts = 0
    cf.errors = 0
    cf.session_connected = False
    cf.connected_since_epoch = None
    cf.peer = None
    cf.rx_buf = bytearray()
    cf.note_failure = fake_note
    proxy._cf = cf

    async def run_fallback():
        return await proxy._cf.fallback_offline(
            reason="test",
            frame_bytes=b"<Frame></Frame>",
            table_name="tbl_actual",
            device_id="DEV1",
            box_writer=writer,
            cloud_writer=None,
        )

    asyncio.run(run_fallback())
    assert called == ["process", "note"]


def test_ensure_cloud_connected_success_and_failure(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    cf = CloudForwarder.__new__(CloudForwarder)
    cf._proxy = proxy
    cf.connects = 0
    cf.disconnects = 0
    cf.timeouts = 0
    cf.errors = 0
    cf.session_connected = False
    cf.connected_since_epoch = None
    cf.peer = None
    cf.rx_buf = bytearray()
    proxy._cf = cf

    async def fake_open(_host, _port):
        return DummyReader(b"ACK"), DummyWriter()

    monkeypatch.setattr(cf_module, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    async def run_success():
        return await proxy._cf.ensure_connected(
            None,
            None,
            conn_id=1,
            table_name="tbl_actual",
            connect_timeout_s=0.1,
        )

    reader, writer, _attempted = asyncio.run(run_success())
    assert reader is not None
    assert writer is not None

    async def fake_fail(_host, _port):
        raise RuntimeError("fail")

    monkeypatch.setattr(asyncio, "open_connection", fake_fail)

    async def run_fail():
        return await proxy._cf.ensure_connected(
            None,
            None,
            conn_id=2,
            table_name="tbl_actual",
            connect_timeout_s=0.1,
        )

    reader, writer, _attempted = asyncio.run(run_fail())
    assert reader is None
    assert writer is None


def test_forward_frame_online_success_and_eof(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    writer = DummyWriter()
    cloud_writer = DummyWriter()
    cf = CloudForwarder.__new__(CloudForwarder)
    cf._proxy = proxy
    cf.connects = 0
    cf.disconnects = 0
    cf.timeouts = 0
    cf.errors = 0
    cf.session_connected = False
    cf.connected_since_epoch = None
    cf.peer = None
    cf.rx_buf = bytearray()
    proxy._cf = cf

    async def fake_ensure(*_args, **_kwargs):
        return DummyReader(b"<ACK/>"), cloud_writer, True

    cf.ensure_connected = fake_ensure
    monkeypatch.setattr(
        cf_module,
        "capture_payload",
        lambda *_args,
        **_kwargs: None)

    async def run_success():
        return await proxy._cf.forward_frame(
            frame_bytes=b"<Frame>1</Frame>",
            table_name="IsNewSet",
            device_id="DEV1",
            conn_id=1,
            box_writer=writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )

    asyncio.run(run_success())
    assert proxy.stats["acks_cloud"] == 1

    # Test EOF scenario - fallback only happens in HYBRID mode after threshold
    proxy._hm.configured_mode = "hybrid"
    proxy._hm.is_hybrid_mode.return_value = True
    proxy._hm.in_offline = True  # Already reached threshold

    async def fake_ensure_eof(*_args, **_kwargs):
        return DummyReader(b""), cloud_writer, True

    cf.ensure_connected = fake_ensure_eof
    called = []

    async def fake_fallback(*_args, **_kwargs):
        called.append("fallback")
        return None, None

    cf.fallback_offline = fake_fallback

    async def run_eof():
        return await proxy._cf.forward_frame(
            frame_bytes=b"<Frame>2</Frame>",
            table_name="tbl_actual",
            device_id="DEV1",
            conn_id=2,
            box_writer=writer,
            cloud_reader=None,
            cloud_writer=None,
            connect_timeout_s=0.1,
        )

    asyncio.run(run_eof())
    assert called == ["fallback"]


def test_control_observe_box_frame_setting(tmp_path):
    proxy = _make_proxy(tmp_path)
    ctrl = _make_real_ctrl(proxy, tmp_path)
    proxy._ctrl = ctrl
    results = []

    async def fake_publish_result(
        *,
        tx,
        status,
        error=None,
        detail=None,
            extra=None):
        results.append((status, detail))

    async def fake_finish():
        results.append(("finish", None))

    ctrl.publish_result = fake_publish_result
    ctrl.finish_inflight = fake_finish

    ctrl.inflight = {
        "tx_id": "t1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
        "stage": "box_ack",
    }

    async def run_marker():
        await ctrl.observe_box_frame(
            {"Result": "END"},
            "END",
            "<Frame></Frame>",
        )

    asyncio.run(run_marker())
    assert ("completed", "box_marker:END") in results

    ctrl.inflight = {
        "tx_id": "t2",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
    }
    parsed = {
        "Type": "Setting",
        "Content": "Remotely : tbl_box_prms / SA: [0]->[1]",
    }

    async def run_setting():
        await ctrl.observe_box_frame(parsed, "tbl_events", "frame")

    asyncio.run(run_setting())
    assert ("applied", None) in results


def test_setup_mqtt_handlers(tmp_path):
    proxy = _make_proxy(tmp_path)
    ctrl = _make_real_ctrl(proxy, tmp_path)
    proxy._ctrl = ctrl
    msc = MqttStateCache.__new__(MqttStateCache)
    msc._proxy = proxy
    msc.last_values = {}
    msc.table_cache = {}
    msc.cache_device_id = None
    proxy._msc = msc

    async def run():
        proxy._loop = asyncio.get_running_loop()
        proxy.device_id = "DEV1"
        proxy.mqtt_publisher.device_id = "DEV1"
        proxy._msc.setup()
        ctrl.setup_mqtt()

    asyncio.run(run())
    assert proxy.mqtt_publisher.handlers


def test_main_stats_and_get_stats(tmp_path):
    proxy = _make_proxy(tmp_path)
    stats = proxy.get_stats()
    assert stats["mode"] == ProxyMode.ONLINE.value
    assert "mqtt_queue_size" in stats
