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


class DummyClient:
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

    MQTTv311 = 4
    Client = DummyClient


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


def test_connect_timeout_and_exception_paths(monkeypatch):
    class DummyClientNoConnect(DummyClient):
        def connect(self, host, port, _keepalive):
            return None

    class DummyMQTTTimeout:
        class CallbackAPIVersion:
            VERSION1 = object()

        MQTTv311 = 4
        Client = DummyClientNoConnect

    class DummyClientFail(DummyClient):
        def connect(self, host, port, _keepalive):
            raise RuntimeError("fail")

    class DummyMQTTFail:
        class CallbackAPIVersion:
            VERSION1 = object()

        MQTTv311 = 4
        Client = DummyClientFail

    monkeypatch.setattr(mqtt_publisher, "MQTT_AVAILABLE", True)
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)

    monkeypatch.setattr(mqtt_publisher, "mqtt", DummyMQTTTimeout, raising=False)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    assert publisher.connect(timeout=0.0) is False

    monkeypatch.setattr(mqtt_publisher, "mqtt", DummyMQTTFail, raising=False)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    assert publisher.connect(timeout=0.01) is False


def test_cleanup_client_handles_errors(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    class BrokenClient:
        def loop_stop(self):
            raise RuntimeError("stop")

        def disconnect(self):
            raise RuntimeError("disconnect")

    publisher.client = BrokenClient()
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

    class SubscribeFailClient(DummyClient):
        def subscribe(self, topic, qos=0):
            raise RuntimeError("nope")

    client = SubscribeFailClient()
    publisher._on_connect(client, None, {}, 0)
    assert publisher.connected is True


def test_on_disconnect_clean(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.connected = True
    publisher._on_disconnect(DummyClient(), None, 0)
    assert publisher.connected is False


def test_publish_raw_when_ready(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = DummyClient()
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

    class PublishFailClient(DummyClient):
        def publish(self, topic, payload, qos=0, retain=False):
            return DummyResult(rc=1)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.queue = DummyQueueWithItem()
    publisher.client = PublishFailClient()
    publisher.connected = True
    asyncio.run(publisher.replay_queue())
    assert publisher.queue.calls == ["get_next"]


def test_send_discovery_skips_when_disconnected(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    cfg = SensorConfig(name="Mode", unit="")
    publisher.send_discovery("MODE", cfg, table="tbl_box_prms", device_id="DEV1")
    assert publisher.discovery_sent == set()


def test_send_discovery_connected(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = DummyClient()
    publisher.connected = True
    cfg = SensorConfig(name="Mode", unit="")
    publisher.send_discovery("MODE", cfg, table="tbl_box_prms", device_id="DEV1")
    assert "MODE" in publisher.discovery_sent
    assert publisher.client.published


def test_on_publish_updates_stats(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.PUBLISH_LOG_EVERY = 1
    publisher._on_publish(DummyClient(), None, 1)
    assert publisher.publish_success == 1


def test_add_message_handler_subscribe_failure(monkeypatch):
    class FailClient(DummyClient):
        def subscribe(self, topic, qos=0):
            raise RuntimeError("nope")

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = FailClient()
    publisher.connected = True
    publisher.add_message_handler(topic="t", handler=lambda *_: None, qos=1)


def test_on_message_handler_exception(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    def bad_handler(*_args, **_kwargs):
        raise RuntimeError("boom")

    publisher._message_handlers["t"] = (1, bad_handler)

    msg = type("Msg", (), {"topic": "t", "payload": b"x", "qos": 1, "retain": False})()
    publisher._on_message(None, None, msg)

def test_on_connect_error_sets_status(monkeypatch):
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = DummyClient()
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
    publisher.client = DummyClient()
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
