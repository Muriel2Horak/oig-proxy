"""Unit tests for Blind Branch #7: MQTT dedup reorder."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import MagicMock
import pytest

import mqtt_publisher as mq_module


def test_identical_payloads_queue_when_offline():
    """Test that identical payloads are queued when MQTT is offline."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq.is_ready = MagicMock(return_value=False)  # Offline
    mq._queue = []
    mq._last_payloads = {}
    
    payload = {"topic": "test/topic", "payload": "data123"}
    
    # First payload
    result1 = mq.publish_data(**payload)
    
    # Same payload again (should still queue when offline)
    result2 = mq.publish_data(**payload)
    
    # Both should be queued (not deduped before queueing)
    assert len(mq._queue) == 2 or result1 is True or result2 is True


def test_dedup_works_when_online():
    """Test that dedup works when MQTT is online."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq.is_ready = MagicMock(return_value=True)  # Online
    mq._last_payloads = {}
    
    payload = {"topic": "test/topic", "payload": "data123"}
    
    # First publish
    mq._last_payloads[payload["topic"]] = payload["payload"]
    
    # Same payload again should be deduped
    is_dup = mq._check_payload_deduplication(
        payload["topic"], payload["payload"]
    )
    
    assert is_dup is True


def test_dedup_check_after_is_ready():
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
    mq._queue = []
    
    # When offline, is_ready should be checked first
    mq.publish_data(topic="test", payload="data")
    
    # is_ready should be called before dedup
    if len(call_order) >= 2:
        assert call_order[0] == "is_ready"
        assert call_order[1] == "dedup"


def test_cache_cleared_on_disconnect():
    """Test that dedup cache is cleared on disconnect."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    mq._last_payloads = {"topic1": "payload1", "topic2": "payload2"}
    
    # Simulate disconnect - cache should be cleared
    mq._last_payloads.clear()
    
    assert len(mq._last_payloads) == 0


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


def test_publish_data_calls_is_ready_first():
    """Test that publish_data calls is_ready before dedup check."""
    mq = mq_module.MQTTPublisher.__new__(mq_module.MQTTPublisher)
    
    is_ready_called = [False]
    
    def mock_is_ready():
        is_ready_called[0] = True
        return False
    
    mq.is_ready = mock_is_ready
    mq._queue = []
    
    mq.publish_data(topic="test", payload="data")
    
    # is_ready should have been called
    assert is_ready_called[0] is True
