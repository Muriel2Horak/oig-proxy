# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,missing-kwoa,unexpected-keyword-arg,duplicate-code
import asyncio
import json
import time
from collections import deque

import pytest

import proxy as proxy_module
from models import SensorConfig


class DummyQueue:
    def size(self) -> int:
        return 0


class DummyMQTT:
    def __init__(self) -> None:
        self.device_id = "DEV1"
        self._last_payload_by_topic = {}
        self.published_raw = []
        self.queue = DummyQueue()

    async def publish_raw(self, *, topic: str, payload: str, qos: int, retain: bool):
        self.published_raw.append((topic, payload, qos, retain))
        return True

    def _state_topic(self, device_id: str, table: str | None) -> str:
        if table:
            return f"{proxy_module.MQTT_NAMESPACE}/{device_id}/{table}/state"
        return f"{proxy_module.MQTT_NAMESPACE}/{device_id}/state"

    def _map_data_for_publish(self, data, *, table, target_device_id):
        payload = {k: v for k, v in data.items() if not k.startswith("_")}
        return payload, len(payload)

    def state_topic(self, device_id: str, table: str | None) -> str:
        return self._state_topic(device_id, table)

    def map_data_for_publish(self, data, *, table, target_device_id):
        return self._map_data_for_publish(data, table=table, target_device_id=target_device_id)

    def get_cached_payload(self, topic: str):
        return self._last_payload_by_topic.get(topic)

    def set_cached_payload(self, topic: str, payload: str) -> None:
        self._last_payload_by_topic[topic] = payload


class DummyWriter:
    def __init__(self):
        self.data = []
        self._closing = False

    def write(self, data):
        self.data.append(data)

    async def drain(self):
        return None

    def close(self):
        self._closing = True

    async def wait_closed(self):
        return None

    def is_closing(self):
        return self._closing


def make_proxy(tmp_path):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.box_connected = True
    proxy._box_connected_since_epoch = time.time() - 10
    proxy._last_data_epoch = time.time() - 1
    proxy._box_conn_lock = asyncio.Lock()
    proxy._active_box_writer = None
    proxy._control_lock = asyncio.Lock()
    proxy._control_queue = deque()
    proxy._control_inflight = None
    proxy._control_last_result = None
    proxy._control_key_state = {}
    proxy._control_session_id = "sess"
    proxy._control_qos = 1
    proxy._control_retain = False
    proxy._control_status_retain = False
    proxy._control_result_topic = "oig/control/result"
    proxy._control_status_prefix = "oig/control/status"
    proxy._control_log_enabled = False
    proxy._control_log_path = str(tmp_path / "control.log")
    proxy._control_pending_path = str(tmp_path / "pending.json")
    proxy._control_pending_keys = set()
    proxy._control_post_drain_refresh_pending = False
    proxy._control_max_attempts = 2
    proxy._control_retry_delay_s = 0.01
    proxy._control_ack_timeout_s = 0.0
    proxy._control_applied_timeout_s = 0.0
    proxy._control_mode_quiet_s = 0.0
    proxy._control_retry_task = None
    proxy._control_ack_task = None
    proxy._control_applied_task = None
    proxy._control_quiet_task = None
    proxy._control_whitelist = {"tbl_box_prms": {"MODE", "SA", "BAT_AC"}}
    proxy._control_box_ready_s = 0.0
    proxy._control_mqtt_enabled = False
    proxy._prms_tables = {}
    proxy._prms_device_id = None
    proxy._table_cache = {}
    proxy._last_values = {}
    proxy.mqtt_publisher = DummyMQTT()
    proxy._local_setting_pending = None
    return proxy


def test_control_publish_result_and_key_status(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_log_enabled = True
    proxy._control_log_path = str(tmp_path / "control.jsonl")

    async def fake_status():
        return None

    proxy.publish_proxy_status = fake_status

    tx = {
        "tx_id": "t1",
        "request_key": "tbl_box_prms/MODE/1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
    }

    asyncio.run(proxy._control_publish_result(tx=tx, status="applied", detail="ok"))
    assert proxy._control_last_result["status"] == "applied"
    assert proxy._control_post_drain_refresh_pending is True
    assert "tbl_box_prms/MODE/1" in proxy._control_key_state
    assert "tbl_box_prms/MODE/1" in proxy._control_pending_keys
    assert proxy.mqtt_publisher.published_raw
    assert tmp_path.joinpath("control.jsonl").exists()


def test_control_pending_keys_load_store(tmp_path):
    proxy = make_proxy(tmp_path)
    missing_path = tmp_path / "missing.json"
    proxy._control_pending_path = str(missing_path)
    assert proxy._control_load_pending_keys() == set()

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{broken", encoding="utf-8")
    proxy._control_pending_path = str(bad_path)
    assert proxy._control_load_pending_keys() == set()

    good_path = tmp_path / "good.json"
    good_path.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    proxy._control_pending_path = str(good_path)
    assert proxy._control_load_pending_keys() == {"a", "b"}

    proxy._control_pending_keys = {"x"}
    proxy._control_store_pending_keys()
    assert json.loads(good_path.read_text(encoding="utf-8")) == ["x"]


def test_control_publish_restart_errors(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_pending_keys = {"tbl_box_prms/MODE/1"}
    results = []

    async def fake_publish(**kwargs):
        results.append(kwargs["status"])

    proxy._control_publish_result = fake_publish

    asyncio.run(proxy._control_publish_restart_errors())
    assert results == ["error"]
    assert proxy._control_pending_keys == set()


def test_control_normalize_and_coerce(tmp_path):
    proxy = make_proxy(tmp_path)

    assert proxy._control_normalize_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", new_value="9"
    ) == (None, "bad_value")
    assert proxy._control_normalize_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", new_value="2"
    ) == ("2", "2")
    assert proxy._control_normalize_value(
        tbl_name="tbl_invertor_prm1", tbl_item="A_MAX_CHRG", new_value="5"
    ) == ("5.0", "5.0")

    assert proxy._control_coerce_value("true") is True
    assert proxy._control_coerce_value("10") == 10
    assert proxy._control_coerce_value("10.5") == pytest.approx(10.5)
    assert proxy._control_coerce_value("text") == "text"


def test_control_map_optimistic_value_and_snapshot(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    cfg = SensorConfig(name="Mode", unit="", options=["A", "B", "C"])
    monkeypatch.setattr(proxy_module, "get_sensor_config", lambda *_: (cfg, "MODE"))

    assert proxy._control_map_optimistic_value(
        tbl_name="tbl_box_prms", tbl_item="MODE", value="1"
    ) == "B"

    calls = []
    monkeypatch.setattr(proxy_module, "save_prms_state", lambda *args: calls.append(args))
    proxy._control_update_persisted_snapshot(
        tbl_name="tbl_box_prms", tbl_item="MODE", raw_value=1
    )
    assert calls
    assert proxy._prms_tables["tbl_box_prms"]["MODE"] == 1


def test_control_is_box_ready_conditions(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy.box_connected = False
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_connected"

    proxy.box_connected = True
    proxy.device_id = "AUTO"
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "device_id_unknown"

    proxy.device_id = "DEV1"
    proxy._box_connected_since_epoch = None
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_ready"

    proxy._box_connected_since_epoch = time.time() - 1
    proxy._control_box_ready_s = 10
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_ready"

    proxy._control_box_ready_s = 0
    proxy._last_data_epoch = None
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_sending_data"

    proxy._last_data_epoch = time.time() - 40
    ok, reason = proxy._control_is_box_ready()
    assert ok is False
    assert reason == "box_not_sending_data"

    proxy._last_data_epoch = time.time() - 1
    ok, reason = proxy._control_is_box_ready()
    assert ok is True
    assert reason is None


def test_control_on_mqtt_message_accepts(tmp_path):
    proxy = make_proxy(tmp_path)
    results = []

    async def fake_publish_result(*, tx, status, **_kwargs):
        results.append(status)

    async def fake_start():
        results.append("start")

    proxy._control_publish_result = fake_publish_result
    proxy._control_maybe_start_next = fake_start

    payload = json.dumps(
        {
            "tx_id": "t1",
            "tbl_name": "tbl_box_prms",
            "tbl_item": "MODE",
            "new_value": 1,
            "request_key": "mismatch",
        }
    ).encode("utf-8")

    asyncio.run(proxy._control_on_mqtt_message(topic="t", payload=payload, retain=False))
    assert "accepted" in results
    assert proxy._control_queue
    assert "request_key_raw" in proxy._control_queue[0]


def test_control_defer_inflight_max_attempts(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_inflight = {"_attempts": 2}
    results = []

    async def fake_publish_result(*, tx, status, **_kwargs):
        results.append(status)

    async def fake_start_next():
        results.append("start")

    async def fake_post_drain(*_args, **_kwargs):
        results.append("post_drain")

    proxy._control_publish_result = fake_publish_result
    proxy._control_maybe_start_next = fake_start_next
    proxy._control_maybe_queue_post_drain_refresh = fake_post_drain

    asyncio.run(proxy._control_defer_inflight(reason="timeout"))
    assert results[0] == "error"


def test_control_ack_and_applied_timeouts(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_inflight = {"stage": "sent_to_box"}
    proxy.box_connected = False
    deferred = []

    async def fake_defer(*, reason):
        deferred.append(reason)

    proxy._control_defer_inflight = fake_defer
    asyncio.run(proxy._control_ack_timeout())
    assert deferred == ["box_not_connected"]

    proxy._control_inflight = {"stage": "box_ack"}
    results = []

    async def fake_publish(*, tx, status, **_kwargs):
        results.append(status)

    async def fake_finish():
        results.append("finish")

    proxy._control_publish_result = fake_publish
    proxy._control_finish_inflight = fake_finish
    asyncio.run(proxy._control_applied_timeout())
    assert "error" in results
    assert "finish" in results


def test_control_quiet_wait_completes(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_inflight = {"stage": "applied", "applied_at_mono": time.monotonic() - 1}
    results = []

    async def fake_publish(*, tx, status, **_kwargs):
        results.append(status)

    async def fake_finish():
        results.append("finish")

    proxy._control_publish_result = fake_publish
    proxy._control_finish_inflight = fake_finish
    asyncio.run(proxy._control_quiet_wait())
    assert "completed" in results
    assert "finish" in results


def test_control_maybe_queue_post_drain_refresh(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_post_drain_refresh_pending = True
    called = []

    async def fake_enqueue(*, reason):
        called.append(reason)

    proxy._control_enqueue_internal_sa = fake_enqueue

    asyncio.run(proxy._control_maybe_queue_post_drain_refresh(last_tx={"tbl_name": "tbl_box_prms", "tbl_item": "MODE"}))
    assert called == ["queue_drained"]


def test_control_observe_box_frame_setting(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._control_inflight = {
        "tx_id": "t1",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "BAT_AC",
        "new_value": "1",
        "stage": "box_ack",
    }
    results = []

    async def fake_publish(*, tx, status, **_kwargs):
        results.append(status)

    async def fake_finish():
        results.append("finish")

    proxy._control_publish_result = fake_publish
    proxy._control_finish_inflight = fake_finish

    parsed = {
        "Type": "Setting",
        "Content": "Remotely : tbl_box_prms / BAT_AC: [0]->[1]",
    }

    asyncio.run(proxy._control_observe_box_frame(parsed, "tbl_events", "<Frame></Frame>"))
    assert "applied" in results
    assert "completed" in results
    assert "finish" in results


def test_publish_setting_event_state(tmp_path):
    proxy = make_proxy(tmp_path)
    proxy._table_cache = {"tbl_box_prms": {"MODE": 1}}
    proxy.mqtt_publisher.device_id = "DEV1"

    async def run():
        await proxy._publish_setting_event_state(
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="2",
            device_id="DEV1",
            source="tbl_events",
        )

    asyncio.run(run())
    assert proxy.mqtt_publisher.published_raw


def test_send_setting_to_box_and_local_ack(tmp_path, monkeypatch):
    proxy = make_proxy(tmp_path)
    writer = DummyWriter()
    proxy._active_box_writer = writer
    proxy._control_ack_timeout_s = 10.0
    results = []

    async def fake_ack(**_kwargs):
        results.append("ack")

    proxy._control_on_box_setting_ack = fake_ack

    async def run():
        res = await proxy._send_setting_to_box(
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            confirm="New",
            tx_id="tx1",
        )
        ok = proxy._maybe_handle_local_setting_ack(
            "<Reason>Setting</Reason><Result>ACK</Result>",
            writer,
        )
        await asyncio.sleep(0)
        return res, ok

    res, ok = asyncio.run(run())
    assert res["ok"] is True
    assert ok is True
    assert results == ["ack"]
