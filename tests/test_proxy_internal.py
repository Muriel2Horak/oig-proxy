# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import asyncio
import json
import logging
import time
from collections import deque, defaultdict

import proxy as proxy_module
import models.ProxyMode, SensorConfig


class DummyCloudHealth:
    def __init__(
            self,
            is_online: bool = True,
            fail_threshold: int = 2) -> None:
        self.is_online = is_online
        self.fail_threshold = fail_threshold
        self.consecutive_successes = 0
        self.consecutive_failures = 0
        self.last_check_time = 0.0


class DummyCloudQueue:
    def __init__(self, size: int = 0, next_item=None) -> None:
        self._size = size
        self._next_item = next_item
        self.deferred = []
        self.removed = []

    def size(self) -> int:
        return self._size

    def get_next(self):
        return self._next_item

    def next_ready_in(self):
        return None

    def defer(self, frame_id: int, delay_s: float = 60.0) -> bool:
        self.deferred.append((frame_id, delay_s))
        return True

    def remove(self, frame_id: int) -> bool:
        self.removed.append(frame_id)
        return True


class DummyMQTTQueue:
    def __init__(self, size: int = 0) -> None:
        self._size = size

    def size(self) -> int:
        return self._size


class DummyMQTT:
    def __init__(self) -> None:
        self.queue = DummyMQTTQueue()
        self.device_id = "DEV1"
        self._last_payload_by_topic = {}
        self.published_data = []
        self.published_raw = []
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    def publish_data(self, payload: dict):
        self.published_data.append(payload)

    async def publish_raw(
            self,
            *,
            topic: str,
            payload: str,
            qos: int,
            retain: bool):
        self.published_raw.append((topic, payload, qos, retain))

    def _state_topic(self, device_id: str, table: str) -> str:
        return f"{proxy_module.MQTT_NAMESPACE}/{device_id}/{table}/state"

    def _map_data_for_publish(self, data, *, table, target_device_id):
        payload = {k: v for k, v in data.items() if not k.startswith("_")}
        return payload, len(payload)

    def state_topic(self, device_id: str, table: str) -> str:
        return self._state_topic(device_id, table)

    def map_data_for_publish(self, data, *, table, target_device_id):
        return self._map_data_for_publish(
            data, table=table, target_device_id=target_device_id)

    def get_cached_payload(self, topic: str):
        return self._last_payload_by_topic.get(topic)

    def set_cached_payload(self, topic: str, payload: str) -> None:
        self._last_payload_by_topic[topic] = payload


class DummyParser:
    def __init__(self, mode_value: int | None = None) -> None:
        self._mode_value = mode_value

    def parse_mode_from_event(self, content: str):
        return self._mode_value


def _make_proxy(tmp_path):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy.mode_lock = asyncio.Lock()
    proxy._cloud_queue_enabled = True
    proxy._cloud_queue_disabled_warned = False
    proxy.stats = {
        "mode_changes": 0,
        "frames_received": 0,
        "frames_forwarded": 0,
        "frames_queued": 0,
        "acks_local": 0,
        "acks_cloud": 0,
    }
    proxy.cloud_health = DummyCloudHealth()
    proxy.cloud_queue = DummyCloudQueue()
    proxy.cloud_connects = 0
    proxy.cloud_disconnects = 0
    proxy.cloud_timeouts = 0
    proxy.cloud_errors = 0
    proxy.cloud_session_connected = False
    proxy._cloud_connected_since_epoch = None
    proxy._cloud_peer = None
    proxy._configured_mode = "online"
    proxy._telemetry_hybrid_sessions = deque()
    proxy._hybrid_state = None
    proxy._hybrid_state_since_epoch = None
    proxy._hybrid_last_offline_reason = None
    proxy.mqtt_publisher = DummyMQTT()
    proxy.parser = DummyParser()
    proxy.box_connected = False
    proxy.box_connections = 0
    proxy._box_connected_since_epoch = None
    proxy._last_box_disconnect_reason = None
    proxy._last_data_epoch = None
    proxy._last_data_iso = None
    proxy._isnew_polls = 0
    proxy._isnew_last_poll_iso = None
    proxy._isnew_last_response = None
    proxy._isnew_last_rtt_ms = None
    proxy._control_session_id = "sess"
    proxy._control_inflight = None
    proxy._control_queue = deque()
    proxy._control_last_result = None
    proxy._control_qos = 1
    proxy._control_retain = False
    proxy._control_status_retain = False
    proxy._control_result_topic = "oig/control/result"
    proxy._control_status_prefix = "oig/control/status"
    proxy._control_log_enabled = False
    proxy._control_log_path = str(tmp_path / "control.log")
    proxy._control_whitelist = {"tbl_box_prms": {"MODE"}}
    proxy._control_key_state = {}
    proxy._control_lock = asyncio.Lock()
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
    proxy._control_mqtt_enabled = False
    proxy._last_values = {}
    proxy._prms_tables = {}
    proxy._prms_device_id = None
    proxy._mode_value = None
    proxy._mode_device_id = None
    proxy._mode_pending_publish = False
    proxy._prms_pending_publish = False
    proxy._table_cache = {}
    proxy._mqtt_cache_device_id = None
    proxy._mqtt_was_ready = False
    proxy._status_task = None
    proxy._box_conn_lock = asyncio.Lock()
    proxy._active_box_writer = None
    proxy._active_box_peer = None
    proxy._conn_seq = 0
    proxy._control_set_topic = "oig/control/set"
    proxy._control_result_topic = "oig/control/result"
    proxy._control_status_prefix = "oig/control/status"
    proxy._control_status_retain = False
    proxy._control_retain = False
    proxy._control_log_enabled = False
    proxy._control_log_path = str(tmp_path / "control.log")
    proxy._control_box_ready_s = 0.0
    proxy._box_connected_since_epoch = None
    proxy._last_box_disconnect_reason = None
    proxy._hb_interval_s = 0.0
    proxy._last_hb_ts = 0.0
    proxy._force_offline_config = False
    proxy._proxy_status_attrs_topic = "oig/status/attrs"
    proxy._configured_mode = "online"
    proxy._hybrid_fail_count = 0
    proxy._hybrid_fail_threshold = 3
    proxy._hybrid_retry_interval = 300.0
    proxy._hybrid_connect_timeout = 5.0
    proxy._hybrid_last_offline_time = 0.0
    proxy._hybrid_in_offline = False
    proxy._telemetry_interval_s = 300
    proxy._start_time = time.time()
    proxy._set_commands_buffer = []
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


def test_status_payload_and_mode_publish(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    proxy.box_connected = True
    proxy.box_connections = 2
    proxy._last_data_epoch = time.time()
    proxy._last_data_iso = "2025-01-01T00:00:00Z"
    proxy._isnew_polls = 1
    proxy._isnew_last_poll_iso = "2025-01-01T00:00:01Z"
    proxy._control_inflight = {
        "request_key": "k1",
        "tbl_name": "tbl",
        "tbl_item": "MODE"}
    proxy._control_queue = deque([{"request_key": "k2"}])
    proxy._control_last_result = {"status": "ok"}

    payload = proxy._build_status_payload()
    assert payload["status"] == ProxyMode.ONLINE.value
    assert payload["box_connected"] == 1
    assert payload["mqtt_queue"] == 0
    assert payload["control_queue_len"] == 1
    assert payload["control_inflight_key"] == "k1"

    attrs = proxy._build_status_attrs_payload()
    assert attrs["control_inflight_key"] == "k1"
    assert attrs["control_queue_keys"] == ["k2"]

    proxy._mode_value = 2
    proxy._mode_device_id = "DEV2"


def test_collect_telemetry_metrics_flushes_window_metrics(tmp_path):
    proxy = _make_proxy(tmp_path)
    proxy._start_time = time.time() - 123
    proxy._telemetry_box_sessions.append({"timestamp": "t1"})
    proxy._telemetry_cloud_sessions.append({"timestamp": "t2"})
    proxy._telemetry_offline_events.append({"timestamp": "t3"})
    proxy._telemetry_tbl_events.append({"timestamp": "t4", "event_time": "t4"})
    proxy._telemetry_error_context.append({"timestamp": "t5"})
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Test warning",
        args=(),
        exc_info=None,
    )
    proxy._record_log_entry(record)

    metrics = proxy._collect_telemetry_metrics()
    window_metrics = metrics["window_metrics"]

    assert metrics["interval_s"] == 300
    assert "timestamp" in metrics
    assert window_metrics["box_sessions"] == [{"timestamp": "t1"}]
    assert window_metrics["cloud_sessions"] == [{"timestamp": "t2"}]
    assert window_metrics["offline_events"] == [{"timestamp": "t3"}]
    assert window_metrics["tbl_events"] == [
        {"timestamp": "t4", "event_time": "t4"}]
    assert window_metrics["error_context"] == [{"timestamp": "t5"}]
    assert window_metrics["stats"] == []
    assert len(window_metrics["logs"]) == 1

    assert not proxy._telemetry_box_sessions
    assert not proxy._telemetry_cloud_sessions
    assert not proxy._telemetry_offline_events
    assert not proxy._telemetry_tbl_events
    assert not proxy._telemetry_error_context
    assert not proxy._telemetry_logs

    proxy._mode_value = 2

    async def run():
        await proxy._publish_mode_if_ready()

    asyncio.run(run())
    assert proxy.mqtt_publisher.published_data
    assert proxy.mqtt_publisher.published_data[0]["MODE"] == 2


def test_cloud_online_success_wins_over_failure(tmp_path):
    proxy = _make_proxy(tmp_path)
    proxy._telemetry_cloud_ok_in_window = True
    proxy._telemetry_cloud_failed_in_window = True
    proxy.cloud_session_connected = False
    metrics = proxy._collect_telemetry_metrics()
    assert metrics["cloud_online"] is True


def test_cloud_online_eof_short_without_success_is_false(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    proxy._cloud_connected_since_epoch = 100.0
    proxy._telemetry_cloud_ok_in_window = False
    monkeypatch.setattr(time, "time", lambda: 100.5)
    proxy._record_cloud_session_end(reason="eof")
    metrics = proxy._collect_telemetry_metrics()
    assert metrics["cloud_online"] is False


def test_cloud_online_eof_short_after_success_is_true(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    proxy._cloud_connected_since_epoch = 200.0
    proxy._telemetry_cloud_ok_in_window = True
    monkeypatch.setattr(time, "time", lambda: 200.2)
    proxy._record_cloud_session_end(reason="eof")
    metrics = proxy._collect_telemetry_metrics()
    assert metrics["cloud_online"] is True


def test_telemetry_stats_pairing_and_flush(tmp_path):
    proxy = _make_proxy(tmp_path)
    proxy._start_time = time.time() - 1
    proxy.mode = ProxyMode.OFFLINE
    conn_id = 7
    proxy._telemetry_record_request("IsNewSet", conn_id)
    proxy._telemetry_record_response(
        "<Frame><Result>END</Result><Time>2026-02-04 08:42:50</Time>"
        "<UTCTime>2026-02-04 08:42:50</UTCTime><CRC>33821</CRC></Frame>",
        source="local",
        conn_id=conn_id,
    )

    metrics = proxy._collect_telemetry_metrics()
    stats = metrics["window_metrics"]["stats"]
    assert len(stats) == 1
    entry = stats[0]
    assert entry["table"] == "IsNewSet"
    assert entry["mode"] == ProxyMode.OFFLINE.value
    assert entry["response_source"] == "local"
    assert entry["req_count"] == 1
    assert entry["resp_end"] == 1


def test_telemetry_cached_state_value_pascalcase(tmp_path):
    proxy = _make_proxy(tmp_path)
    proxy.mqtt_publisher = DummyMQTT()
    topic = proxy.mqtt_publisher.state_topic("DEV1", "IsNewFW")
    proxy.mqtt_publisher.set_cached_payload(
        topic, json.dumps({"Fw": "v1.2.3"})
    )
    value = proxy._telemetry_cached_state_value("DEV1", "isnewfw", "fw")
    assert value == "v1.2.3"


def test_mode_update_and_processing(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    calls = []

    async def fake_publish(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(proxy, "_publish_mode_if_ready", fake_publish)
    monkeypatch.setattr(
        proxy_module,
        "save_mode_state",
        lambda mode,
        dev: calls.append(
            ("save",
             mode,
             dev)))

    async def run():
        await proxy._handle_mode_update("bad", None, "src")
        await proxy._handle_mode_update("6", None, "src")
        await proxy._handle_mode_update("3", "DEV1", "src")
        proxy.parser = DummyParser(mode_value=1)
        await proxy._maybe_process_mode({"Content": "event"}, "tbl_events", "DEV1")

    asyncio.run(run())
    assert proxy._mode_value == 1
    assert ("save", 3, "DEV1") in calls


def test_prms_publish_flow(tmp_path):
    proxy = _make_proxy(tmp_path)
    proxy._prms_tables = {"tbl_box_prms": {"MODE": 1}}

    async def run():
        proxy.mqtt_publisher._ready = False
        await proxy._publish_prms_if_ready(reason="startup")
        assert proxy._prms_pending_publish is True

        proxy.mqtt_publisher._ready = True
        proxy.device_id = "AUTO"
        await proxy._publish_prms_if_ready(reason="startup")
        assert proxy._prms_pending_publish is True

        proxy.device_id = "DEV1"

        async def fail_publish(_payload):
            raise RuntimeError("fail")
        proxy.mqtt_publisher.publish_data = fail_publish
        await proxy._publish_prms_if_ready(reason="startup")
        assert proxy._prms_pending_publish is True

        async def ok_publish(payload):
            proxy.mqtt_publisher.published_data.append(payload)
        proxy.mqtt_publisher.publish_data = ok_publish
        await proxy._publish_prms_if_ready(reason="startup")
        assert proxy._prms_pending_publish is False

    asyncio.run(run())


def test_register_and_unregister_box_connection(tmp_path):
    proxy = _make_proxy(tmp_path)
    closed = []

    async def fake_close(writer):
        closed.append(writer)

    proxy._close_writer = fake_close

    class DummyWriter:
        def __init__(self, closing: bool = False) -> None:
            self._closing = closing

        def is_closing(self):
            return self._closing

    old = DummyWriter()
    proxy._active_box_writer = old
    writer = DummyWriter()

    async def run():
        conn_id = await proxy._register_box_connection(writer, ("1.2.3.4", 1234))
        assert conn_id == 1
        assert proxy._active_box_peer == "1.2.3.4:1234"
        await proxy._unregister_box_connection(writer)

    asyncio.run(run())
    assert closed and closed[0] is old
    assert proxy._active_box_writer is None


def test_note_cloud_failure_records_hybrid_failure(tmp_path):
    """Test that _note_cloud_failure records failure for HYBRID mode tracking."""
    proxy = _make_proxy(tmp_path)
    proxy._configured_mode = "hybrid"
    proxy._hybrid_fail_count = 0
    proxy._hybrid_fail_threshold = 3

    async def run():
        await proxy._note_cloud_failure(reason="test", local_ack=False)

    asyncio.run(run())
    # In HYBRID mode, failure count should increase
    assert proxy._hybrid_fail_count == 1
    # Should not be in offline yet (threshold is 3)
    assert proxy._hybrid_in_offline is False

    # After reaching threshold, should switch to offline
    proxy._hybrid_fail_count = 2
    asyncio.run(run())
    assert proxy._hybrid_in_offline is True


def test_mqtt_state_message_updates_cache(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)
    proxy.mqtt_publisher.device_id = "DEV1"
    proxy.device_id = "DEV1"

    cfg = SensorConfig(name="Mode", unit="", options=["A", "B", "C"])
    monkeypatch.setattr(
        proxy_module,
        "get_sensor_config",
        lambda key,
        table=None: (
            cfg,
            key))

    updated = []

    def fake_update(*, tbl_name, tbl_item, raw_value, update_mode):
        updated.append((tbl_name, tbl_item, raw_value, update_mode))

    proxy._update_cached_value = fake_update
    monkeypatch.setattr(proxy_module, "save_prms_state", lambda *_: None)

    payload = json.dumps({"MODE": "B"})

    async def run():
        await proxy._handle_mqtt_state_message(
            topic=f"{proxy_module.MQTT_NAMESPACE}/DEV1/tbl_box_prms/state",
            payload_text=payload,
            retain=False,
        )

    asyncio.run(run())
    assert updated[0][2] == 1
    assert "tbl_box_prms" in proxy._prms_tables


def test_control_message_validation_and_accept(tmp_path):
    proxy = _make_proxy(tmp_path)
    results = []

    async def fake_publish_result(
        *,
        tx,
        status,
        error=None,
        detail=None,
            extra=None):
        results.append((status, error, detail))

    async def fake_maybe_start():
        results.append(("start", None, None))

    proxy._control_publish_result = fake_publish_result
    proxy._control_maybe_start_next = fake_maybe_start

    async def run():
        await proxy._control_on_mqtt_message(
            topic="t",
            payload=b"{invalid",
            retain=False,
        )
        await proxy._control_on_mqtt_message(
            topic="t",
            payload=json.dumps({"tx_id": "1"}).encode("utf-8"),
            retain=False,
        )
        await proxy._control_on_mqtt_message(
            topic="t",
            payload=json.dumps({
                "tx_id": "1",
                "tbl_name": "tbl_box_prms",
                "tbl_item": "UNKNOWN",
                "new_value": 1,
            }).encode("utf-8"),
            retain=False,
        )
        await proxy._control_on_mqtt_message(
            topic="t",
            payload=json.dumps({
                "tx_id": "1",
                "tbl_name": "tbl_box_prms",
                "tbl_item": "MODE",
                "new_value": 3,
            }).encode("utf-8"),
            retain=False,
        )

    asyncio.run(run())
    assert ("accepted", None, None) in results
    assert ("start", None, None) in results


def test_control_start_inflight_paths(tmp_path):
    proxy = _make_proxy(tmp_path)
    results = []
    finished = []
    deferred = []

    async def fake_publish_result(
        *,
        tx,
        status,
        error=None,
        detail=None,
            extra=None):
        results.append(status)

    async def fake_finish():
        finished.append(True)

    async def fake_defer(*, reason):
        deferred.append(reason)

    proxy._control_publish_result = fake_publish_result
    proxy._control_finish_inflight = fake_finish
    proxy._control_defer_inflight = fake_defer

    async def run():
        proxy._control_inflight = {"_attempts": 2}
        await proxy._control_start_inflight()

        proxy._control_inflight = {
            "_attempts": 0,
            "tbl_name": "tbl_box_prms",
            "tbl_item": "MODE",
            "new_value": "1",
            "tx_id": "t1",
        }

        async def fake_send(**_):
            return {"ok": False, "error": "box_not_connected"}

        proxy._send_setting_to_box = fake_send
        await proxy._control_start_inflight()

        async def ok_send(**_):
            return {"ok": True, "id": 1, "id_set": 2}

        proxy._send_setting_to_box = ok_send
        proxy._control_inflight = {
            "_attempts": 0,
            "tbl_name": "tbl_box_prms",
            "tbl_item": "MODE",
            "new_value": "1",
            "tx_id": "t2",
        }

        async def fake_ack_timeout():
            return None

        proxy._control_ack_timeout = fake_ack_timeout
        await proxy._control_start_inflight()

    asyncio.run(run())
    assert "error" in results
    assert deferred == ["box_not_connected"]


def test_control_on_box_setting_ack(tmp_path):
    proxy = _make_proxy(tmp_path)
    results = []

    async def fake_publish_result(
        *,
        tx,
        status,
        error=None,
        detail=None,
            extra=None):
        results.append((status, error))

    async def fake_finish():
        results.append(("finish", None))

    proxy._control_publish_result = fake_publish_result
    proxy._control_finish_inflight = fake_finish

    async def run():
        proxy._control_inflight = {"tx_id": "t1"}
        await proxy._control_on_box_setting_ack(tx_id="t1", ack=False)

        proxy._control_inflight = {"tx_id": "t2"}

        async def fake_applied():
            return None

        proxy._control_applied_timeout = fake_applied
        await proxy._control_on_box_setting_ack(tx_id="t2", ack=True)

    asyncio.run(run())
    assert ("error", "box_nack") in results
    assert ("box_ack", None) in results
