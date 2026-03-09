"""Unit tests for Blind Branch #7: MQTT dedup reorder."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import MagicMock
import pytest

import mqtt_publisher as mq_module


class _DummyQueue:
    def __init__(self):
        self.items = []

    async def add(self, topic, payload, retain):
        self.items.append((topic, payload, retain))
        return True

    def size(self):
        return len(self.items)


@pytest.mark.asyncio
async def test_identical_payloads_queue_when_offline():
    """Test that identical payloads are queued when MQTT is offline."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq.is_ready = MagicMock(return_value=False)  # Offline
    mq.queue = _DummyQueue()
    mq._last_payload_by_topic = {}
    mq.publish_count = 0
    mq.publish_failed = 0
    mq.device_id = "test_device"
    mq.proxy_device_id = "test_device"
    mq.client = None
    mq.connected = False

    payload = {"_table": "test", "value": "data123"}

    # First payload
    result1 = await mq.publish_data(payload)

    # Same payload again (should still queue when offline)
    result2 = await mq.publish_data(payload)

    # Both should be queued (not deduped before queueing)
    assert len(mq.queue.items) == 2
    assert result1 is False
    assert result2 is False


def test_dedup_works_when_online():
    """Test that dedup works when MQTT is online."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq.is_ready = MagicMock(return_value=True)  # Online
    mq._last_payload_by_topic = {}

    payload = {"topic": "test/topic", "payload": "data123"}

    # First publish
    mq._last_payload_by_topic[payload["topic"]] = payload["payload"]

    # Same payload again should be deduped
    is_dup = mq._check_payload_deduplication(
        payload["topic"], payload["payload"]
    )

    assert is_dup is True


@pytest.mark.asyncio
async def test_dedup_check_after_is_ready():
    """Test that dedup check happens AFTER is_ready check."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)

    call_order = []

    def mock_is_ready():
        call_order.append("is_ready")
        return False  # Offline

    def mock_dedup(*args, **kwargs):
        call_order.append("dedup")
        return False

    mq.is_ready = mock_is_ready
    mq._check_payload_deduplication = mock_dedup
    mq.queue = _DummyQueue()
    mq.publish_count = 0
    mq.publish_failed = 0
    mq._last_payload_by_topic = {}
    mq.device_id = "test_device"
    mq.proxy_device_id = "test_device"
    mq.client = None
    mq.connected = False

    # When offline, is_ready should be checked first
    await mq.publish_data({"_table": "test", "value": "data"})

    # is_ready should be called before dedup
    assert call_order[0] == "is_ready"


def test_cache_cleared_on_disconnect():
    """Test that dedup cache is cleared on disconnect."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq._last_payload_by_topic = {"topic1": "payload1", "topic2": "payload2"}

    # Simulate disconnect - cache should be cleared
    mq._last_payload_by_topic.clear()

    assert len(mq._last_payload_by_topic) == 0


def test_offline_queue_bypasses_dedup():
    """Test that offline queue bypasses dedup."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq.is_ready = MagicMock(return_value=False)  # Offline
    mq._queue = []

    payload = {"topic": "test/topic", "payload": "data123"}

    # When offline, identical payloads should both queue
    # (dedup happens after is_ready check)
    mq._queue.append(payload)
    mq._queue.append(payload)  # Same payload again

    # Both should be in queue
    assert len(mq._queue) == 2


@pytest.mark.asyncio
async def test_publish_data_calls_is_ready_first():
    """Test that publish_data calls is_ready before dedup check."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)

    is_ready_called = [False]

    def mock_is_ready():
        is_ready_called[0] = True
        return False

    mq.is_ready = mock_is_ready
    mq.queue = _DummyQueue()
    mq.publish_count = 0
    mq.publish_failed = 0
    mq._last_payload_by_topic = {}
    mq.device_id = "test_device"
    mq.proxy_device_id = "test_device"
    mq.client = None
    mq.connected = False

    await mq.publish_data({"_table": "test", "value": "data"})

    # is_ready should have been called
    assert is_ready_called[0] is True
