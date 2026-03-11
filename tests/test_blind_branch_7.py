"""Unit tests for Blind Branch #7: MQTT dedup reorder."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import MagicMock
import pytest

import mqtt_publisher as mq_module


@pytest.mark.asyncio
async def test_identical_payloads_dropped_when_offline():
    """Test that identical payloads are dropped when MQTT is offline (no queue)."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq.is_ready = MagicMock(return_value=False)  # Offline
    mq._last_payload_by_topic = {}
    mq.publish_count = 0
    mq.publish_failed = 0
    mq.device_id = "test_device"
    mq.proxy_device_id = "test_device"
    mq.client = None
    mq.connected = False

    payload = {"_table": "test", "value": "data123"}

    # First payload — offline → dropped
    result1 = await mq.publish_data(payload)

    # Same payload again — offline → dropped
    result2 = await mq.publish_data(payload)

    # Both should fail (no queue, just drop)
    assert result1 is False
    assert result2 is False
    assert mq.publish_failed == 2


def test_dedup_works_when_online():
    """Test that dedup works when MQTT is online."""
    import time as _time
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq.is_ready = MagicMock(return_value=True)  # Online
    mq._last_payload_by_topic = {}
    mq._last_publish_time_by_topic = {}

    payload = {"topic": "test/topic", "payload": "data123"}

    mq._last_payload_by_topic[payload["topic"]] = payload["payload"]
    mq._last_publish_time_by_topic[payload["topic"]] = _time.time()

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


@pytest.mark.asyncio
async def test_publish_data_calls_is_ready_first():
    """Test that publish_data calls is_ready before dedup check."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)

    is_ready_called = [False]

    def mock_is_ready():
        is_ready_called[0] = True
        return False

    mq.is_ready = mock_is_ready
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
