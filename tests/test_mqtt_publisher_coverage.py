"""Additional coverage tests for mqtt_publisher."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import mqtt_publisher
import db_utils
from models import SensorConfig


def _make_publisher(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs):
            self.added = []

        async def add(self, topic, payload, retain=False):
            self.added.append((topic, payload, retain))
            return True

        def size(self):
            return len(self.added)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    return mqtt_publisher.MQTTPublisher(device_id="DEV1")


def test_mqtt_queue_retain_column_add_success(monkeypatch):
    class DummyConn:
        def __init__(self):
            self.executed = []
            self.commits = 0

        def execute(self, sql, params=None):
            self.executed.append(sql)
            if sql.startswith("PRAGMA table_info(queue)"):
                return [(0, "id"), (1, "topic")]
            return []

        def commit(self):
            self.commits += 1

    dummy_conn = DummyConn()

    def fake_init_sqlite_db(*_args, **_kwargs):
        return dummy_conn

    monkeypatch.setattr(mqtt_publisher, "MQTT_QUEUE_DB_PATH", ":memory:")
    monkeypatch.setattr(mqtt_publisher, "MQTT_QUEUE_MAX_SIZE", 10)
    monkeypatch.setattr(db_utils, "init_sqlite_db", fake_init_sqlite_db)

    queue = mqtt_publisher.MQTTQueue()
    assert any("ALTER TABLE queue ADD COLUMN retain" in sql for sql in dummy_conn.executed)
    assert dummy_conn.commits >= 1
    queue.conn = dummy_conn


def test_mqtt_queue_retain_column_check_error(monkeypatch):
    class DummyConn:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append(sql)
            if sql.startswith("PRAGMA table_info(queue)"):
                raise sqlite3.Error("boom")
            return []

        def commit(self):
            return None

    dummy_conn = DummyConn()

    def fake_init_sqlite_db(*_args, **_kwargs):
        return dummy_conn

    monkeypatch.setattr(db_utils, "init_sqlite_db", fake_init_sqlite_db)
    queue = mqtt_publisher.MQTTQueue(db_path=":memory:")
    assert "PRAGMA table_info(queue)" in dummy_conn.executed[0]
    queue.conn = dummy_conn


def test_mqtt_queue_clear_logs(monkeypatch, tmp_path):
    queue = mqtt_publisher.MQTTQueue(
        db_path=str(tmp_path / "queue.db"),
        max_size=10,
    )
    with patch("mqtt_publisher.logger") as mock_logger:
        queue.clear()
        assert any("MQTTQueue: Cleared" in call.args[0] for call in mock_logger.info.call_args_list)
    queue.conn.close()


def test_connect_timeout_logs(monkeypatch):
    class DummyClient:
        def __init__(self):
            self.on_connect = None
            self.on_disconnect = None
            self.on_publish = None
            self.on_message = None

        def username_pw_set(self, *_args, **_kwargs):
            return None

        def will_set(self, *_args, **_kwargs):
            return None

        def connect(self, *_args, **_kwargs):
            return None

        def loop_start(self):
            return None

    class DummyMQTT:
        MQTTv311 = 4

        class CallbackAPIVersion:
            VERSION1 = 1

        @staticmethod
        def Client(*_args, **_kwargs):
            return DummyClient()

    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(mqtt_publisher, "mqtt", DummyMQTT())

    publisher = _make_publisher(monkeypatch)

    with patch("mqtt_publisher.time.sleep", return_value=None):
        with patch("mqtt_publisher.time.time", side_effect=[0.0, 0.0, 6.0]):
            with patch("mqtt_publisher.logger") as mock_logger:
                assert publisher.connect(timeout=5) is False
                assert any("Connection timeout" in call.args[0] for call in mock_logger.error.call_args_list)


def test_cleanup_client_exception(monkeypatch):
    publisher = _make_publisher(monkeypatch)
    mock_client = MagicMock()
    mock_client.loop_stop.side_effect = RuntimeError("boom")
    mock_client.disconnect.side_effect = RuntimeError("boom")
    publisher.client = mock_client

    with patch("mqtt_publisher.logger") as mock_logger:
        publisher._cleanup_client()
        assert any("Client cleanup failed" in call.args[0] for call in mock_logger.debug.call_args_list)


def test_cleanup_client_disconnect_called(monkeypatch):
    publisher = _make_publisher(monkeypatch)
    mock_client = MagicMock()
    publisher.client = mock_client
    publisher._cleanup_client()
    mock_client.loop_stop.assert_called_once()
    mock_client.disconnect.assert_called_once()


def test_on_connect_subscribe_success_and_failure(monkeypatch):
    publisher = _make_publisher(monkeypatch)
    handler = lambda *_args: None
    publisher._message_handlers["topic/1"] = (1, handler)
    publisher._wildcard_handlers.append(("topic/+", 1, handler))
    publisher._wildcard_handlers.append(("topic/fail", 1, handler))

    class DummyClient:
        def __init__(self):
            self.subscribed = []

        def publish(self, *_args, **_kwargs):
            return SimpleNamespace(rc=0, mid=1)

        def subscribe(self, topic, qos=0):
            if topic == "topic/fail":
                raise RuntimeError("fail")
            self.subscribed.append((topic, qos))

    client = DummyClient()
    with patch("mqtt_publisher.logger") as mock_logger:
        publisher._on_connect(client, None, {}, 0)
        assert ("topic/1", 1) in client.subscribed
        assert ("topic/+", 1) in client.subscribed
        assert any("Subscribe failed" in call.args[0] for call in mock_logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_publish_raw_no_client(monkeypatch):
    publisher = _make_publisher(monkeypatch)
    publisher.connected = True
    publisher.client = None
    publisher.is_ready = MagicMock(return_value=True)
    assert await publisher.publish_raw(topic="a", payload="b") is False


@pytest.mark.asyncio
async def test_replay_queue_client_none(monkeypatch):
    class DummyQueue:
        def size(self):
            return 1

        async def get_next(self):
            return (1, "t1", "p1", False)

        async def remove(self, _msg_id):
            return True

    publisher = _make_publisher(monkeypatch)
    publisher.queue = DummyQueue()
    publisher.connected = True
    publisher.client = None
    publisher.is_ready = MagicMock(return_value=True)

    with patch("mqtt_publisher.logger") as mock_logger:
        await publisher.replay_queue()
        assert any("MQTT client is not initialized" in call.args[0] for call in mock_logger.error.call_args_list)


@pytest.mark.asyncio
async def test_replay_queue_progress_logs(monkeypatch):
    class DummyQueue:
        def __init__(self):
            self.items = [(idx, f"t{idx}", f"p{idx}", False) for idx in range(10)]

        def size(self):
            return len(self.items)

        async def get_next(self):
            return self.items.pop(0) if self.items else None

        async def remove(self, _msg_id):
            return True

    class DummyClient:
        def publish(self, *_args, **_kwargs):
            return SimpleNamespace(rc=0)

    publisher = _make_publisher(monkeypatch)
    publisher.queue = DummyQueue()
    publisher.client = DummyClient()
    publisher.connected = True

    async def fake_sleep(_interval):
        return None

    with patch("mqtt_publisher.asyncio.sleep", fake_sleep):
        with patch("mqtt_publisher.logger") as mock_logger:
            await publisher.replay_queue()
            assert any("Replay progress" in call.args[0] for call in mock_logger.debug.call_args_list)


@pytest.mark.asyncio
async def test_health_check_reconnect_success(monkeypatch):
    publisher = _make_publisher(monkeypatch)
    publisher.connected = False
    publisher.connect = MagicMock(return_value=True)

    call_count = {"count": 0}

    async def fake_sleep(_interval):
        call_count["count"] += 1
        if call_count["count"] > 1:
            raise RuntimeError("stop")
        return None

    with patch("mqtt_publisher.asyncio.sleep", fake_sleep):
        with patch("mqtt_publisher.logger") as mock_logger:
            with pytest.raises(RuntimeError):
                await publisher.health_check_loop()
            assert any("Reconnect succeeded" in call.args[0] for call in mock_logger.info.call_args_list)


@pytest.mark.asyncio
async def test_health_check_reconnect_failure(monkeypatch):
    publisher = _make_publisher(monkeypatch)
    publisher.connected = False
    publisher.connect = MagicMock(return_value=False)

    call_count = {"count": 0}

    async def fake_sleep(_interval):
        call_count["count"] += 1
        if call_count["count"] > 1:
            raise RuntimeError("stop")
        return None

    with patch("mqtt_publisher.asyncio.sleep", fake_sleep):
        with patch("mqtt_publisher.logger") as mock_logger:
            with pytest.raises(RuntimeError):
                await publisher.health_check_loop()
            assert any("Reconnect failed" in call.args[0] for call in mock_logger.warning.call_args_list)


def test_schedule_replay_failure(monkeypatch):
    publisher = _make_publisher(monkeypatch)
    publisher._main_loop = asyncio.new_event_loop()

    def boom(coro, *_args, **_kwargs):
        coro.close()
        raise RuntimeError("boom")

    with patch("mqtt_publisher.asyncio.run_coroutine_threadsafe", boom):
        with patch("mqtt_publisher.logger") as mock_logger:
            publisher._schedule_replay()
            assert any("Replay schedule failed" in call.args[0] for call in mock_logger.debug.call_args_list)


@pytest.mark.asyncio
async def test_publish_data_client_none(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs):
            self.added = []

        async def add(self, topic, payload, retain=False):
            self.added.append((topic, payload, retain))
            return True

        def size(self):
            return len(self.added)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    monkeypatch.setattr(
        mqtt_publisher,
        "get_sensor_config",
        lambda sensor_id, table=None: (None, sensor_id),
    )

    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.connected = True
    publisher.client = None
    publisher.is_ready = MagicMock(return_value=True)

    result = await publisher.publish_data({"_table": "tbl_actual", "POWER": 5})
    assert result is False
    assert publisher.publish_failed == 1
    assert len(publisher.queue.added) == 1


def test_map_data_for_publish_coerce_path(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs):
            pass

        def size(self):
            return 0

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)

    cfg = SensorConfig(name="Stamp", unit="", device_class="timestamp")
    monkeypatch.setattr(
        mqtt_publisher,
        "get_sensor_config",
        lambda sensor_id, table=None: (cfg, f"{table}:{sensor_id}"),
    )

    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher._coerce_state_value = MagicMock(return_value="coerced")

    data = {"_table": "tbl_box_prms", "TS": "2024-01-01 00:00:00"}
    payload, mapped = publisher.map_data_for_publish(
        data, table="tbl_box_prms", target_device_id="DEV1"
    )

    assert mapped == 1
    assert payload["TS"] == "coerced"
    publisher._coerce_state_value.assert_called_once()
