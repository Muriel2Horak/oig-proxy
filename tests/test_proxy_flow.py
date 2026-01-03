# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg,duplicate-code
import asyncio

import proxy as proxy_module
from models import ProxyMode


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


class DummyCloudQueue:
    def __init__(self):
        self.added = []

    async def add(self, frame_bytes, table_name, device_id):
        self.added.append((frame_bytes, table_name, device_id))

    def size(self):
        return len(self.added)


class DummyMQTT:
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

    def _state_topic(self, device_id, table):
        return f"{proxy_module.MQTT_NAMESPACE}/{device_id}/{table}/state"

    def _map_data_for_publish(self, data, *, table, target_device_id):
        payload = {k: v for k, v in data.items() if not k.startswith("_")}
        return payload, len(payload)

    def state_topic(self, device_id, table):
        return self._state_topic(device_id, table)

    def map_data_for_publish(self, data, *, table, target_device_id):
        return self._map_data_for_publish(data, table=table, target_device_id=target_device_id)

    def get_cached_payload(self, topic):
        return self._last_payload_by_topic.get(topic)

    def set_cached_payload(self, topic, payload):
        self._last_payload_by_topic[topic] = payload


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
    proxy.mode = ProxyMode.ONLINE
    proxy.mode_lock = asyncio.Lock()
    proxy.stats = {
        "frames_received": 0,
        "frames_forwarded": 0,
        "frames_queued": 0,
        "acks_local": 0,
        "acks_cloud": 0,
        "mode_changes": 0,
    }
    proxy.cloud_health = type("H", (), {"is_online": True, "fail_threshold": 1, "consecutive_successes": 0, "consecutive_failures": 0, "last_check_time": 0.0})()
    proxy.cloud_queue = DummyCloudQueue()
    proxy.mqtt_publisher = DummyMQTT()
    proxy.parser = DummyParser({})
    proxy._active_box_peer = None
    proxy._control_inflight = None
    proxy._control_queue = []
    proxy._control_last_result = None
    proxy._control_lock = asyncio.Lock()
    proxy._control_quiet_task = None
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_status_prefix = "oig/control/status"
    proxy._control_qos = 1
    proxy._control_status_retain = False
    proxy._control_retain = False
    proxy._control_result_topic = "oig/control/result"
    proxy._control_set_topic = "oig/control/set"
    proxy._control_pending_keys = set()
    proxy._control_pending_path = str(tmp_path / "pending.json")
    proxy._control_post_drain_refresh_pending = False
    proxy._prms_tables = {}
    proxy._prms_device_id = None
    proxy._table_cache = {}
    proxy._last_values = {}
    proxy._mqtt_cache_device_id = None
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
    proxy.cloud_connects = 0
    proxy.cloud_disconnects = 0
    proxy.cloud_timeouts = 0
    proxy.cloud_errors = 0
    proxy.cloud_session_connected = False
    proxy._box_conn_lock = asyncio.Lock()
    proxy._active_box_writer = None
    proxy._conn_seq = 0
    proxy._loop = None
    proxy._hb_interval_s = 0.0
    proxy._last_hb_ts = 0.0
    return proxy


def test_extract_and_autodetect_device_id(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    proxy.parser = DummyParser({"_device_id": "DEV2", "_table": "tbl_box_prms", "MODE": 1})
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
    proxy._control_observe_box_frame = fake_observe
    proxy._maybe_process_mode = fake_mode
    proxy._control_maybe_start_next = fake_start
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)

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
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)

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
    proxy.cloud_queue = DummyCloudQueue()

    async def run():
        await proxy._process_frame_offline(
            frame_bytes=b"<Frame><TblName>tbl_actual</TblName></Frame>",
            table_name="tbl_actual",
            device_id="DEV1",
            box_writer=writer,
            send_ack=True,
        )

        await proxy._process_frame_offline(
            frame_bytes=b"<Frame><Result>END</Result><Reason>All data sent</Reason></Frame>",
            table_name="END",
            device_id="DEV1",
            box_writer=writer,
            send_ack=True,
        )

    asyncio.run(run())
    assert proxy.stats["acks_local"] >= 1
    assert proxy.stats["frames_queued"] == 1

    called = []

    async def fake_process(*_args, **_kwargs):
        called.append("process")

    async def fake_note(*_args, **_kwargs):
        called.append("note")

    proxy._process_frame_offline = fake_process
    proxy._note_cloud_failure = fake_note

    async def run_fallback():
        return await proxy._fallback_offline_from_cloud_issue(
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

    async def fake_open(_host, _port):
        return DummyReader(b"ACK"), DummyWriter()

    monkeypatch.setattr(proxy_module, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    async def run_success():
        return await proxy._ensure_cloud_connected(
            None,
            None,
            conn_id=1,
            table_name="tbl_actual",
            connect_timeout_s=0.1,
        )

    reader, writer = asyncio.run(run_success())
    assert reader is not None
    assert writer is not None

    async def fake_fail(_host, _port):
        raise RuntimeError("fail")

    monkeypatch.setattr(asyncio, "open_connection", fake_fail)

    async def run_fail():
        return await proxy._ensure_cloud_connected(
            None,
            None,
            conn_id=2,
            table_name="tbl_actual",
            connect_timeout_s=0.1,
        )

    reader, writer = asyncio.run(run_fail())
    assert reader is None
    assert writer is None


def test_forward_frame_online_success_and_eof(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    writer = DummyWriter()
    cloud_writer = DummyWriter()

    async def fake_ensure(*_args, **_kwargs):
        return DummyReader(b"<ACK/>"), cloud_writer

    proxy._ensure_cloud_connected = fake_ensure
    monkeypatch.setattr(proxy_module, "capture_payload", lambda *_args, **_kwargs: None)

    async def run_success():
        return await proxy._forward_frame_online(
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

    async def fake_ensure_eof(*_args, **_kwargs):
        return DummyReader(b""), cloud_writer

    proxy._ensure_cloud_connected = fake_ensure_eof
    called = []

    async def fake_fallback(*_args, **_kwargs):
        called.append("fallback")
        return None, None

    proxy._fallback_offline_from_cloud_issue = fake_fallback

    async def run_eof():
        return await proxy._forward_frame_online(
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
    results = []

    async def fake_publish_result(*, tx, status, error=None, detail=None, extra=None):
        results.append((status, detail))

    async def fake_finish():
        results.append(("finish", None))

    proxy._control_publish_result = fake_publish_result
    proxy._control_finish_inflight = fake_finish

    proxy._control_inflight = {
        "tx_id": "t1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
        "stage": "box_ack",
    }

    async def run_marker():
        await proxy._control_observe_box_frame(
            {"Result": "END"},
            "END",
            "<Frame></Frame>",
        )

    asyncio.run(run_marker())
    assert ("completed", "box_marker:END") in results

    proxy._control_inflight = {
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
        await proxy._control_observe_box_frame(parsed, "tbl_events", "frame")

    asyncio.run(run_setting())
    assert ("applied", None) in results


def test_setup_mqtt_handlers(tmp_path):
    proxy = _make_proxy(tmp_path)

    async def run():
        proxy._loop = asyncio.get_running_loop()
        proxy.device_id = "DEV1"
        proxy.mqtt_publisher.device_id = "DEV1"
        proxy._setup_mqtt_state_cache()
        proxy._setup_control_mqtt()

    asyncio.run(run())
    assert proxy.mqtt_publisher.handlers


def test_main_stats_and_get_stats(tmp_path):
    proxy = _make_proxy(tmp_path)
    stats = proxy.get_stats()
    assert stats["mode"] == ProxyMode.ONLINE.value
    assert "mqtt_queue_size" in stats
