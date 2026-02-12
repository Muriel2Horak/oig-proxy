# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import asyncio

import mqtt_publisher
from models import SensorConfig


class DummyQueue:
    def size(self) -> int:
        return 0


class DummyResult:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.mid = 1


class Dummyclient:
    def __init__(self, *args, **kwargs) -> None:
        self.published = []
        self.subscribed = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_publish = None
        self.on_message = None
        self._will = None
        self._auth = None

    def username_pw_set(self, username, password):
        self._auth = (username, password)

    def will_set(self, topic, payload, retain=True):
        self._will = (topic, payload, retain)

    def connect(self, host, port, _keepalive):
        if self.on_connect:
            self.on_connect(self, None, {}, 0)

    def loop_start(self):
        return None

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return DummyResult(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))


class DummyMQTTModule:
    class CallbackAPIVersion:
        VERSION1 = object()

    mqtt_v311 = 4
    MQTTv311 = 4
    client = Dummyclient
    Client = Dummyclient


def test_connect_skips_when_mqtt_unavailable(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", False)
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    assert publisher.connect(timeout=0.01) is False


def test_connect_success_and_disconnect(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(mqtt_publisher, "mqtt", DummyMQTTModule, raising=False)
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    assert publisher.connect(timeout=0.01) is True
    assert publisher.connected is True
    assert publisher.client is not None

    publisher.connected = True
    publisher._on_disconnect(publisher.client, None, 1)
    assert publisher.connected is False
    assert "Unexpected disconnect" in publisher.last_error_msg


def test_connect_with_username(monkeypatch):
    class DummyClientWithAuth(Dummyclient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.auth = None

        def username_pw_set(self, username, password):
            self.auth = (username, password)

    class DummyMQTTWithAuth:
        class CallbackAPIVersion:
            VERSION1 = object()

        mqtt_v311 = 4
        MQTTv311 = 4
        client = DummyClientWithAuth
        Client = DummyClientWithAuth

    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(mqtt_publisher, "mqtt", DummyMQTTWithAuth, raising=False)
    monkeypatch.setattr(mqtt_publisher, "MQTT_USERNAME", "test_user")
    monkeypatch.setattr(mqtt_publisher, "MQTT_PASSWORD", "test_pass")
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    assert publisher.connect(timeout=0.01) is True
    assert publisher.client.auth == ("test_user", "test_pass")


def test_connect_timeout_and_exception_paths(monkeypatch):
    class DummyclientNoConnect(Dummyclient):
        def connect(self, host, port, _keepalive):
            return None

    class DummyMQTTTimeout:
        class CallbackAPIVersion:
            VERSION1 = object()

        mqtt_v311 = 4
        client = DummyclientNoConnect

    class DummyclientFail(Dummyclient):
        def connect(self, host, port, _keepalive):
            raise RuntimeError("fail")

    class DummyMQTTFail:
        class CallbackAPIVersion:
            VERSION1 = object()

        mqtt_v311 = 4
        client = DummyclientFail

    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)

    monkeypatch.setattr(
        mqtt_publisher,
        "mqtt",
        DummyMQTTTimeout,
        raising=False)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    assert publisher.connect(timeout=0.0) is False

    monkeypatch.setattr(mqtt_publisher, "mqtt", DummyMQTTFail, raising=False)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    assert publisher.connect(timeout=0.01) is False


def test_cleanup_client_handles_errors(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class Brokenclient:
        def loop_stop(self):
            raise RuntimeError("stop")

        def disconnect(self):
            raise RuntimeError("disconnect")

    publisher.client = Brokenclient()
    publisher.connected = True
    publisher._cleanup_client()
    assert publisher.connected is False
    assert publisher.client is None


def test_on_connect_subscribe_failure(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.proxy_device_id = "PROXY"

    def handler(_topic, _payload, _qos, _retain):
        return None

    publisher._message_handlers["topic"] = (1, handler)
    publisher._wildcard_handlers.append(("t/+", 1, handler))

    class SubscribeFailclient(Dummyclient):
        def subscribe(self, topic, qos=0):
            raise RuntimeError("nope")

    client = SubscribeFailclient()
    publisher._on_connect(client, None, {}, 0)
    assert publisher.connected is True


def test_on_disconnect_clean(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.connected = True
    publisher._on_disconnect(Dummyclient(), None, 0)
    assert publisher.connected is False


def test_publish_raw_when_ready(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = Dummyclient()
    publisher.connected = True

    async def run():
        ok = await publisher.publish_raw(topic="t", payload="p", retain=False)
        return ok

    assert asyncio.run(run()) is True


def test_replay_queue_publish_failure(monkeypatch):
    class DummyQueueWithItem:
        def __init__(self) -> None:
            self.calls = []

        def size(self) -> int:
            return 1

        async def get_next(self):
            self.calls.append("get_next")
            return (1, "t", "p", False)

        async def remove(self, _msg_id):
            self.calls.append("remove")
            return True

    class PublishFailclient(Dummyclient):
        def publish(self, topic, payload, qos=0, retain=False):
            return DummyResult(rc=1)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = DummyQueueWithItem()
    publisher.client = PublishFailclient()
    publisher.connected = True
    asyncio.run(publisher.replay_queue())
    assert publisher.queue.calls == ["get_next"]


def test_send_discovery_skips_when_disconnected(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    cfg = SensorConfig(name="Mode", unit="")
    publisher.send_discovery(
        "MODE",
        cfg,
        table="tbl_box_prms",
        device_id="DEV1")
    assert publisher.discovery_sent == set()


def test_send_discovery_connected(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = Dummyclient()
    publisher.connected = True
    cfg = SensorConfig(name="Mode", unit="")
    publisher.send_discovery(
        "MODE",
        cfg,
        table="tbl_box_prms",
        device_id="DEV1")
    assert "MODE" in publisher.discovery_sent
    assert publisher.client.published


def test_on_publish_updates_stats(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    setattr(publisher, "PUBLISH_LOG_EVERY", 1)
    publisher._on_publish(Dummyclient(), None, 1)
    assert publisher.publish_success == 1


def test_add_message_handler_subscribe_failure(monkeypatch):
    class Failclient(Dummyclient):
        def subscribe(self, topic, qos=0):
            raise RuntimeError("nope")

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = Failclient()
    publisher.connected = True
    publisher.add_message_handler(topic="t", handler=lambda *_: None, qos=1)


def test_on_message_handler_exception(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    def bad_handler(*_args, **_kwargs):
        raise RuntimeError("boom")

    publisher._message_handlers["t"] = (1, bad_handler)

    msg = type(
        "Msg", (), {
            "topic": "t", "payload": b"x", "qos": 1, "retain": False})()
    publisher._on_message(None, None, msg)


def test_on_connect_error_sets_status(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = Dummyclient()
    publisher._on_connect(publisher.client, None, {}, 5)
    assert publisher.connected is False
    assert publisher.last_error_msg


def test_on_message_dispatches_handlers(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    received = []

    def handler(topic, payload, qos, retain):
        received.append((topic, payload, qos, retain))

    publisher._message_handlers["a/b"] = (1, handler)
    publisher._wildcard_handlers.append(("a/+", 1, handler))

    msg = type(
        "Msg",
        (),
        {"topic": "a/b", "payload": b"data", "qos": 1, "retain": True},
    )()
    publisher._on_message(None, None, msg)
    assert len(received) == 2


def test_replay_queue_interrupts_when_disconnected(monkeypatch):
    class DummyQueueWithItem:
        def __init__(self) -> None:
            self.calls = []

        def size(self) -> int:
            return 1

        async def get_next(self):
            self.calls.append("get_next")
            return (1, "t", "p", False)

        async def remove(self, _msg_id):
            self.calls.append("remove")
            return True

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = DummyQueueWithItem()
    publisher.connected = False
    asyncio.run(publisher.replay_queue())
    assert publisher.queue.calls == []


def test_health_check_loop_reconnects(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.connected = False
    calls = {"connect": 0, "sleep": 0}

    def fake_connect(timeout=None):
        calls["connect"] += 1
        return True

    async def fake_sleep(_interval):
        calls["sleep"] += 1
        if calls["sleep"] == 1:
            return None
        raise RuntimeError("stop")

    publisher.connect = fake_connect
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(publisher.health_check_loop())
    except RuntimeError:
        pass

    assert calls["connect"] == 1
    assert publisher.reconnect_attempts == 1


def test_publish_availability(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = Dummyclient()
    publisher.connected = True
    publisher.publish_availability()
    assert publisher.client.published


def test_schedule_replay_uses_loop(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    loop = asyncio.new_event_loop()
    publisher.attach_loop(loop)
    called = []

    def fake_threadsafe(coro, loop):
        coro.close()
        called.append(loop)

        class DummyFuture:
            def done(self):
                return True

        return DummyFuture()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fake_threadsafe)
    publisher._schedule_replay()
    loop.close()
    assert called


def test_mqtt_queue_sqlite_errors(monkeypatch):
    import sqlite3

    class BrokenConn:
        def execute(self, _sql, _params=None):
            raise sqlite3.Error("broken")

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", False)

    queue = mqtt_publisher.MQTTQueue.__new__(mqtt_publisher.MQTTQueue)
    queue.db_path = ":memory:"
    queue.max_size = 10
    queue.conn = BrokenConn()
    queue.lock = asyncio.Lock()

    async def run_add():
        return await queue.add("topic", "payload")

    async def run_get():
        return await queue.get_next()

    async def run_remove():
        return await queue.remove(1)

    assert asyncio.run(run_add()) is False
    assert asyncio.run(run_get()) is None
    assert asyncio.run(run_remove()) is False
    assert queue.size() == 0

    queue.clear()


def test_publish_raw_exception(monkeypatch):
    class DummyQueue:
        async def add(self, _topic, _payload, _retain):
            return True

    class Dummyclient:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def publish(self, _topic, _payload, _qos, _retain):
            raise RuntimeError("boom")

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = Dummyclient()
    publisher.connected = True

    async def run():
        return await publisher.publish_raw(topic="t", payload="p", retain=False)

    assert asyncio.run(run()) is False


def test_replay_queue_client_none(monkeypatch):
    class DummyQueueWithItem:
        def __init__(self):
            self.calls = []

        def size(self) -> int:
            return 1

        async def get_next(self):
            self.calls.append("get_next")
            return (1, "t", "p", False)

        async def remove(self, _msg_id):
            self.calls.append("remove")
            return True

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = DummyQueueWithItem()
    publisher.client = None
    publisher.connected = True
    asyncio.run(publisher.replay_queue())
    assert publisher.queue.calls == []


def test_replay_queue_exception(monkeypatch):
    class DummyQueueWithItem:
        def __init__(self):
            self.calls = []

        def size(self) -> int:
            return 1

        async def get_next(self):
            self.calls.append("get_next")
            return (1, "t", "p", False)

        async def remove(self, _msg_id):
            self.calls.append("remove")
            return True

    class DummyExceptionClient:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def publish(self, _topic, _payload, qos=0, retain=False):
            raise RuntimeError("boom")

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = DummyQueueWithItem()
    publisher.client = DummyExceptionClient()
    publisher.connected = True
    asyncio.run(publisher.replay_queue())
    assert len(publisher.queue.calls) == 1


def test_publish_availability_not_ready(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.publish_availability()
    assert publisher.client is None


def test_start_health_check(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    async def fake_health_check():
        await asyncio.sleep(0.01)

    publisher.health_check_loop = fake_health_check
    asyncio.run(publisher.start_health_check())
    assert publisher._health_check_task is not None


def test_schedule_replay_no_loop(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher._schedule_replay()


def test_publish_proxy_status(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    async def fake_publish(_data):
        return True

    publisher.publish_data = fake_publish

    async def run():
        return await publisher.publish_proxy_status({"status": "ok"})

    assert asyncio.run(run()) is True


def test_build_discovery_payload_binary(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    cfg = SensorConfig(name="Status", unit="", is_binary=True)
    topic, payload = publisher._build_discovery_payload(
        sensor_id="status",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert "binary_sensor" in topic
    assert payload["payload_on"] == "1"
    assert payload["payload_off"] == "0"


def test_map_data_for_publish_with_options(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = None
        json_attributes_topic = None
        state_class = None
        options = ["a", "b", "c"]
        unit = ""
        device_class = None
        icon = None
        entity_category = None

    class DummyGetSensorConfig:
        def __call__(self, _key, _table):
            return DummyConfig(), "test"

    monkeypatch.setattr(mqtt_publisher, "get_sensor_config", DummyGetSensorConfig())

    data = {"test": 1}
    result, mapped = publisher._map_data_for_publish(
        data, table=None, target_device_id="DEV1"
    )
    assert result["test"] == "b"
    assert mapped == 1


def test_coerce_state_value_not_timestamp(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        device_class = "other"

    result = publisher._coerce_state_value(DummyConfig(), "2025-01-01T00:00:00")
    assert result == "2025-01-01T00:00:00"


def test_publish_raw_no_client(monkeypatch):
    class DummyQueue:
        async def add(self, _topic, _payload, _retain):
            return True

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = None

    async def run():
        return await publisher.publish_raw(topic="t", payload="p", retain=False)

    assert asyncio.run(run()) is False


def test_publish_raw_queue_if_offline(monkeypatch):
    queue_calls = []

    class DummyQueue:
        async def add(self, topic, payload, retain):
            queue_calls.append((topic, payload, retain))
            return True

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = None

    async def run():
        return await publisher.publish_raw(topic="t", payload="p", retain=False, queue_if_offline=True)

    result = asyncio.run(run())
    assert result is False
    assert len(queue_calls) == 1


def test_connect_timeout_logs(monkeypatch):
    import time

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.CONNECT_TIMEOUT = 0.1

    class DummyNoConnectclient:
        def __init__(self, *args, **kwargs):
            pass

        def loop_start(self):
            pass

        def connect(self, _host, _port, _keepalive):
            pass

    class DummyMQTTTimeout:
        class CallbackAPIVersion:
            VERSION1 = object()

        mqtt_v311 = 4
        client = DummyNoConnectclient

    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(mqtt_publisher, "mqtt", DummyMQTTTimeout, raising=False)

    publisher._main_loop = asyncio.new_event_loop()
    assert publisher.connect(timeout=0.1) is False


def test_cleanup_exception(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class Brokenclient:
        def loop_stop(self):
            raise RuntimeError("stop")

        def disconnect(self):
            raise RuntimeError("disconnect")

    publisher.client = Brokenclient()
    publisher.connected = True
    publisher._cleanup_client()
    assert publisher.client is None


def test_replay_queue_empty(monkeypatch):
    class EmptyQueue:
        def size(self) -> int:
            return 0

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", EmptyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = EmptyQueue()

    async def run():
        await publisher.replay_queue()

    asyncio.run(run())


def test_replay_queue_progress_log(monkeypatch):
    class DummyQueueWithProgress:
        def __init__(self):
            self.calls = []

        def size(self) -> int:
            return 20

        async def get_next(self):
            self.calls.append("get_next")
            if len(self.calls) > 15:
                return None
            return (len(self.calls), "t", "p", False)

        async def remove(self, _msg_id):
            self.calls.append("remove")
            return True

    class DummyProgressClient:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def publish(self, _topic, _payload, qos=0, retain=False):
            return type("Result", (), {"rc": 0})()

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = DummyQueueWithProgress()
    publisher.client = DummyProgressClient()
    publisher.connected = True
    asyncio.run(publisher.replay_queue())


def test_map_data_wrapper(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    result, mapped = publisher.map_data_for_publish({}, table=None, target_device_id="DEV1")
    assert result == {}
    assert mapped == 0


def test_publish_data_no_client(monkeypatch):
    queue_calls = []

    class DummyQueue:
        def size(self):
            return 0

        async def add(self, topic, payload, retain):
            queue_calls.append((topic, payload, retain))
            return True

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = None

    async def run():
        return await publisher.publish_data({"data": "test"})

    result = asyncio.run(run())
    assert result is False
    assert len(queue_calls) == 1


def test_publish_data_exception(monkeypatch):
    queue_calls = []

    class DummyQueue:
        def size(self):
            return 0

        async def add(self, topic, payload, retain):
            queue_calls.append((topic, payload, retain))
            return True

    class DummyExceptionClient:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def publish(self, _topic, _payload, qos=0, retain=False):
            raise RuntimeError("boom")

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = DummyQueue()
    publisher.client = DummyExceptionClient()
    publisher.connected = True

    async def run():
        return await publisher.publish_data({"data": "test"})

    result = asyncio.run(run())
    assert result is False
    assert len(queue_calls) == 1


def test_state_topic(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    namespace = mqtt_publisher.MQTT_NAMESPACE if hasattr(mqtt_publisher, "MQTT_NAMESPACE") else "oig_local"
    assert publisher.state_topic("DEV1", "tbl_box") == f"{namespace}/DEV1/tbl_box/state"
    assert publisher.state_topic("DEV1", None) == f"{namespace}/DEV1/state"


def test_cached_payload(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    assert publisher.get_cached_payload("topic") is None
    publisher.set_cached_payload("topic", "payload")
    assert publisher.get_cached_payload("topic") == "payload"


def test_attach_loop(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    loop = asyncio.new_event_loop()
    publisher.attach_loop(loop)
    assert publisher._main_loop == loop
    loop.close()


def test_schedule_replay_already_scheduled(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    loop = asyncio.new_event_loop()
    publisher.attach_loop(loop)

    class DummyFuture:
        def done(self):
            return False

    publisher._replay_future = DummyFuture()
    publisher._schedule_replay()
    loop.close()


def test_send_discovery_already_sent_new(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = Dummyclient()
    publisher.connected = True
    publisher.discovery_sent.add("MODE")
    cfg = SensorConfig(name="Mode", unit="")
    publisher.send_discovery("MODE", cfg, table="tbl_box_prms", device_id="DEV1")
    assert len(publisher.client.published) == 0


def test_build_discovery_json_attributes(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = None
        json_attributes_topic = "state"
        state_class = None
        options = None
        unit = ""
        device_class = None
        icon = None
        entity_category = None

    cfg = DummyConfig()
    topic, payload = publisher._build_discovery_payload(
        sensor_id="test",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert "json_attributes_topic" in payload
    assert payload["json_attributes_topic"] == "oig_local/DEV1/state"


def test_build_discovery_via_device(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = "battery"
        json_attributes_topic = None
        state_class = None
        options = None
        unit = ""
        device_class = None
        icon = None
        entity_category = None

    cfg = DummyConfig()
    topic, payload = publisher._build_discovery_payload(
        sensor_id="test",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert "via_device" in payload["device"]
    assert "inverter" in payload["device"]["via_device"]


def test_build_discovery_state_class(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = None
        json_attributes_topic = None
        state_class = "measurement"
        options = None
        unit = ""
        device_class = None
        icon = None
        entity_category = None

    cfg = DummyConfig()
    topic, payload = publisher._build_discovery_payload(
        sensor_id="test",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert payload["state_class"] == "measurement"


def test_build_discovery_with_unit(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = None
        json_attributes_topic = None
        state_class = None
        options = None
        unit = "W"
        device_class = None
        icon = None
        entity_category = None

    cfg = DummyConfig()
    topic, payload = publisher._build_discovery_payload(
        sensor_id="test",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert payload["unit_of_measurement"] == "W"


def test_build_discovery_with_device_class(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = None
        json_attributes_topic = None
        state_class = None
        options = None
        unit = ""
        device_class = "voltage"
        icon = None
        entity_category = None

    cfg = DummyConfig()
    topic, payload = publisher._build_discovery_payload(
        sensor_id="test",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert payload["device_class"] == "voltage"


def test_build_discovery_with_icon(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = None
        json_attributes_topic = None
        state_class = None
        options = None
        unit = ""
        device_class = None
        icon = "mdi:battery"
        entity_category = None

    cfg = DummyConfig()
    topic, payload = publisher._build_discovery_payload(
        sensor_id="test",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert payload["icon"] == "mdi:battery"


def test_build_discovery_with_entity_category(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        name = "Test"
        is_binary = False
        device_mapping = None
        json_attributes_topic = None
        state_class = None
        options = None
        unit = ""
        device_class = None
        icon = None
        entity_category = "diagnostic"

    cfg = DummyConfig()
    topic, payload = publisher._build_discovery_payload(
        sensor_id="test",
        config=cfg,
        table=None,
        device_id="DEV1"
    )
    assert payload["entity_category"] == "diagnostic"


def test_coerce_state_value_timestamp_with_tz(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        device_class = "timestamp"

    result = publisher._coerce_state_value(DummyConfig(), "2025-01-01T00:00:00Z")
    assert result == "2025-01-01T00:00:00Z"


def test_coerce_state_value_timestamp_empty(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class DummyConfig:
        device_class = "timestamp"

    result = publisher._coerce_state_value(DummyConfig(), "")
    assert result == ""


def test_connect_timeout(monkeypatch):
    import time

    class DummyTimeoutclient:
        def __init__(self, *args, **kwargs):
            pass

        def loop_start(self):
            pass

        def connect(self, _host, _port, _keepalive):
            pass

    class DummyMQTTTimeout:
        class CallbackAPIVersion:
            VERSION1 = object()

