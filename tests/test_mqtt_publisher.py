# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import asyncio
import datetime
import json

from models import SensorConfig
import mqtt_publisher


def test_mqtt_queue_add_get_remove(tmp_path):
    queue = mqtt_publisher.MQTTQueue(
        db_path=str(tmp_path / "queue.db"),
        max_size=2,
    )

    async def run():
        await queue.add("t1", "p1")
        await queue.add("t2", "p2")
        await queue.add("t3", "p3")

        assert queue.size() == 2
        item = await queue.get_next()
        assert item is not None
        msg_id, topic, payload, retain = item
        assert topic == "t2"
        assert payload == "p2"
        assert retain is False

        removed = await queue.remove(msg_id)
        assert removed is True
        assert queue.size() == 1

    try:
        asyncio.run(run())
    finally:
        queue.conn.close()


def test_mqtt_queue_retain_dedupes(tmp_path):
    queue = mqtt_publisher.MQTTQueue(
        db_path=str(tmp_path / "queue.db"),
        max_size=10,
    )

    async def run():
        await queue.add("topic", "first", retain=True)
        await queue.add("topic", "second", retain=True)
        assert queue.size() == 1

        item = await queue.get_next()
        assert item is not None
        _msg_id, topic, payload, retain = item
        assert topic == "topic"
        assert payload == "second"
        assert retain is True

    try:
        asyncio.run(run())
    finally:
        queue.conn.close()


def test_build_discovery_payload_binary(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def size(self) -> int:
            return 0

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    cfg = SensorConfig(
        name="Battery Flag",
        unit="%",
        device_class="problem",
        device_mapping="battery",
        entity_category="diagnostic",
        icon="mdi:battery",
        is_binary=True,
        json_attributes_topic="state",
    )
    topic, payload = publisher._build_discovery_payload(
        sensor_id="tbl_batt:FLAG",
        config=cfg,
        table="tbl_batt",
        device_id="DEV1",
    )

    assert topic.startswith("homeassistant/binary_sensor/")
    assert payload["payload_on"] == "1"
    assert payload["payload_off"] == "0"
    assert "unit_of_measurement" not in payload
    assert payload["state_topic"].endswith("/tbl_batt/state")
    assert payload["json_attributes_topic"] == payload["state_topic"]
    assert payload["device"]["via_device"] == (
        f"{mqtt_publisher.MQTT_NAMESPACE}_DEV1_inverter"
    )


def test_publish_raw_queues_when_offline(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            self.added = []

        def add(
                self,
                topic: str,
                payload: str,
                retain: bool = False) -> bool:
            self.added.append((topic, payload, retain))
            return True

        def size(self) -> int:
            return len(self.added)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    async def run():
        ok = await publisher.publish_raw(topic="topic", payload="payload", retain=True)
        assert ok is False
        assert publisher.queue.added == [("topic", "payload", True)]

        ok = await publisher.publish_raw(
            topic="topic2",
            payload="payload2",
            retain=False,
            queue_if_offline=False,
        )
        assert ok is False
        assert len(publisher.queue.added) == 1

    asyncio.run(run())


def test_publish_data_offline_queues_and_dedupes(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            self.added = []

        def add(
                self,
                topic: str,
                payload: str,
                retain: bool = False) -> bool:
            self.added.append((topic, payload, retain))
            return True

        def size(self) -> int:
            return len(self.added)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    monkeypatch.setattr(
        mqtt_publisher,
        "get_sensor_config",
        lambda sensor_id, table=None: (None, sensor_id),
    )
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    async def run():
        data = {"_table": "tbl_actual", "POWER": 5}
        ok1 = await publisher.publish_data(data)
        ok2 = await publisher.publish_data(data)

        assert ok1 is False
        assert ok2 is True
        assert len(publisher.queue.added) == 1
        topic, payload, retain = publisher.queue.added[0]
        assert topic == (
            f"{mqtt_publisher.MQTT_NAMESPACE}/DEV1/tbl_actual/state"
        )
        assert json.loads(payload) == {"POWER": 5}
        assert retain is mqtt_publisher.MQTT_STATE_RETAIN
        assert publisher.publish_failed == 1

    asyncio.run(run())


def test_publish_data_online_success_maps_and_calls_discovery(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            self.added = []

        def add(
                self,
                topic: str,
                payload: str,
                retain: bool = False) -> bool:
            self.added.append((topic, payload, retain))
            return True

        def size(self) -> int:
            return len(self.added)

    class DummyResult:
        def __init__(self, rc: int = 0, mid: int = 1) -> None:
            self.rc = rc
            self.mid = mid

    class DummyClient:
        def __init__(self) -> None:
            self.published = []

        def publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))
            return DummyResult(rc=0, mid=len(self.published))

    cfg = SensorConfig(name="Mode", unit="", options=["A", "B", "C"])

    def fake_get_sensor_config(sensor_id, table=None):
        if sensor_id == "MODE":
            return cfg, f"{table}:{sensor_id}"
        return None, sensor_id

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    monkeypatch.setattr(
        mqtt_publisher,
        "get_sensor_config",
        fake_get_sensor_config)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = DummyClient()
    publisher.connected = True

    discovery_calls = []

    def fake_send_discovery(sensor_id, config, table, device_id=None):
        discovery_calls.append((sensor_id, config, table, device_id))

    publisher.send_discovery = fake_send_discovery

    async def run():
        data = {"_table": "tbl_box_prms", "MODE": 1, "OTHER": 5}
        ok = await publisher.publish_data(data)

        assert ok is True
        assert publisher.publish_count == 1
        assert publisher.queue.added == []
        assert discovery_calls == [
            ("tbl_box_prms:MODE", cfg, "tbl_box_prms", "DEV1")]

        assert len(publisher.client.published) == 1
        topic, payload, qos, retain = publisher.client.published[0]
        assert topic == (
            f"{mqtt_publisher.MQTT_NAMESPACE}/DEV1/tbl_box_prms/state"
        )
        assert json.loads(payload) == {"MODE": "B", "OTHER": 5}
        assert qos == mqtt_publisher.MQTT_PUBLISH_QOS
        assert retain is mqtt_publisher.MQTT_STATE_RETAIN

    asyncio.run(run())


def test_publish_data_online_failure_queues(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            self.added = []

        def add(
                self,
                topic: str,
                payload: str,
                retain: bool = False) -> bool:
            self.added.append((topic, payload, retain))
            return True

        def size(self) -> int:
            return len(self.added)

    class DummyResult:
        def __init__(self, rc: int = 1) -> None:
            self.rc = rc
            self.mid = 1

    class DummyClient:
        def publish(self, topic, payload, qos=0, retain=False):
            return DummyResult(rc=1)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    monkeypatch.setattr(
        mqtt_publisher,
        "get_sensor_config",
        lambda sensor_id, table=None: (None, sensor_id),
    )
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = DummyClient()
    publisher.connected = True

    async def run():
        ok = await publisher.publish_data({"_table": "tbl_actual", "POWER": 5})
        assert ok is False
        assert publisher.publish_count == 1
        assert publisher.publish_failed == 1
        assert len(publisher.queue.added) == 1

    asyncio.run(run())


def test_coerce_state_value_timestamp(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def size(self) -> int:
            return 0

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher._local_tzinfo = datetime.timezone.utc

    cfg = SensorConfig(name="Stamp", unit="", device_class="timestamp")
    value = "2024-01-02 03:04:05"
    coerced = publisher._coerce_state_value(cfg, value)
    assert coerced == "2024-01-02T03:04:05+00:00"


def test_coerce_state_value_timestamp_passthrough(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def size(self) -> int:
            return 0

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    cfg = SensorConfig(name="Stamp", unit="", device_class="timestamp")
    value = "2024-01-02T03:04:05+00:00"
    assert publisher._coerce_state_value(cfg, value) == value


def test_topic_matches_patterns():
    match = mqtt_publisher.MQTTPublisher._topic_matches
    assert match("a/b/c", "a/b/c") is True
    assert match("a/+/c", "a/b/c") is True
    assert match("a/+/c", "a/b/d") is False
    assert match("a/#", "a/b/c") is True
    assert match("a/b/#", "a/b") is True
    assert match("a/b/#", "a") is False


def test_add_message_handler_subscribes_when_connected(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def size(self) -> int:
            return 0

    class DummyClient:
        def __init__(self) -> None:
            self.subscribed = []

        def subscribe(self, topic, qos=0):
            self.subscribed.append((topic, qos))

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = DummyClient()
    publisher.connected = True

    publisher.add_message_handler(
        topic="oig/DEV1/command",
        handler=lambda *_: None,
        qos=1,
    )
    publisher.add_message_handler(
        topic="oig/+/command",
        handler=lambda *_: None,
        qos=2,
    )

    assert ("oig/DEV1/command", 1) in publisher.client.subscribed
    assert ("oig/+/command", 2) in publisher.client.subscribed


def test_replay_queue_sends_and_clears(tmp_path, monkeypatch):
    queue = mqtt_publisher.MQTTQueue(
        db_path=str(
            tmp_path /
            "queue.db"),
        max_size=10)

    def fake_queue(*args, **kwargs):
        return queue

    class DummyResult:
        def __init__(self, rc: int = 0) -> None:
            self.rc = rc

    class DummyClient:
        def __init__(self) -> None:
            self.published = []

        def publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))
            return DummyResult(rc=0)

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", fake_queue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")
    publisher.client = DummyClient()
    publisher.connected = True

    async def run():
        await queue.add("t1", "p1")
        await queue.add("t2", "p2", retain=True)
        assert queue.size() == 2
        await publisher.replay_queue()
        assert queue.size() == 0
        assert publisher.client.published[0][0] == "t1"
        assert publisher.client.published[1][0] == "t2"

    try:
        asyncio.run(run())
    finally:
        queue.conn.close()


def test_build_discovery_payload_with_options(monkeypatch):
    class DummyQueue:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def size(self) -> int:
            return 0

    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyQueue)
    publisher = mqtt_publisher.MQTTPublisher(device_id="DEV1")

    cfg = SensorConfig(
        name="Mode",
        unit="",
        options=["A", "B", "C"],
        state_class="measurement",
        device_mapping="box",
        json_attributes_topic="oig/attrs",
    )
    topic, payload = publisher._build_discovery_payload(
        sensor_id="tbl_box_prms:MODE",
        config=cfg,
        table="tbl_box_prms",
        device_id="DEV1",
    )

    assert topic.startswith("homeassistant/sensor/")
    assert payload["options"] == ["A", "B", "C"]
    assert payload["state_class"] == "measurement"
    assert payload["json_attributes_topic"] == "oig/attrs"
    assert payload["device"]["via_device"] == (
        f"{mqtt_publisher.MQTT_NAMESPACE}_DEV1_inverter"
    )
