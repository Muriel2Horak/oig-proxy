"""Tests for TwinControlHandler.

Tests verify:
- Handler initialization
- MQTT subscription on start
- Message parsing and enqueueing
- Log output verification
- Unsubscribe on stop

Run: PYTHONPATH=addon/oig-proxy-v2 pytest tests/v2/test_twin_handler.py -v
"""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

import asyncio
import json
from unittest.mock import MagicMock, call

import pytest

from twin.handler import TwinControlHandler
from twin.state import TwinQueue


class TestTwinControlHandler:
    """Tests for TwinControlHandler class."""

    @pytest.fixture
    def mock_mqtt(self):
        """Create a mock MQTT client."""
        mqtt = MagicMock()
        mqtt.is_ready.return_value = True
        mqtt.subscribe.return_value = True
        mqtt.unsubscribe.return_value = True
        return mqtt

    @pytest.fixture
    def twin_queue(self):
        """Create a fresh TwinQueue instance."""
        return TwinQueue()

    @pytest.fixture
    def handler(self, mock_mqtt, twin_queue):
        """Create a TwinControlHandler instance."""
        return TwinControlHandler(
            mqtt=mock_mqtt,
            twin_queue=twin_queue,
            device_id="test_device_123",
        )

    def test_handler_initialization(self, handler, mock_mqtt, twin_queue):
        """Test handler is initialized with correct attributes."""
        assert handler._mqtt == mock_mqtt
        assert handler._twin_queue == twin_queue
        assert handler._device_id == "test_device_123"
        assert handler._namespace == "oig_local"
        assert handler._topic == "oig/+/control/set"
        assert handler._topic_compat == "oig_local/+/set/#"
        assert handler._subscribed is False

    @pytest.mark.asyncio
    async def test_start_subscribes_to_topic(self, handler, mock_mqtt):
        """Test start() subscribes to the control topic."""
        await handler.start()

        assert mock_mqtt.subscribe.call_count == 2
        mock_mqtt.subscribe.assert_has_calls(
            [
                call("oig/+/control/set", handler._on_message),
                call("oig_local/+/set/#", handler._on_message),
            ]
        )
        assert handler._subscribed is True

    @pytest.mark.asyncio
    async def test_start_when_mqtt_not_ready(self, handler, mock_mqtt):
        """Test start() does nothing when MQTT is not ready."""
        mock_mqtt.is_ready.return_value = False

        await handler.start()

        mock_mqtt.subscribe.assert_not_called()
        assert handler._subscribed is False

    @pytest.mark.asyncio
    async def test_stop_unsubscribes_from_topic(self, handler, mock_mqtt):
        """Test stop() unsubscribes from the control topic."""
        await handler.start()
        assert handler._subscribed is True

        await handler.stop()

        assert mock_mqtt.unsubscribe.call_count == 2
        mock_mqtt.unsubscribe.assert_has_calls(
            [
                call("oig/+/control/set"),
                call("oig_local/+/set/#"),
            ]
        )
        assert handler._subscribed is False

    @pytest.mark.asyncio
    async def test_stop_when_not_subscribed(self, handler, mock_mqtt):
        """Test stop() does nothing when not subscribed."""
        await handler.stop()

        mock_mqtt.unsubscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_when_mqtt_not_ready(self, handler, mock_mqtt):
        """Test stop() handles MQTT not being ready."""
        await handler.start()
        mock_mqtt.is_ready.return_value = False

        await handler.stop()

        mock_mqtt.unsubscribe.assert_not_called()

    def test_on_message_parses_and_enqueues(self, handler, twin_queue):
        """Test _on_message parses JSON and enqueues setting."""
        payload = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": 2})

        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 1
        setting = twin_queue.get("tbl_box_prms", "MODE")
        assert setting is not None
        assert setting.table == "tbl_box_prms"
        assert setting.key == "MODE"
        assert setting.value == 2

    def test_on_message_with_string_value(self, handler, twin_queue):
        """Test _on_message handles string values."""
        payload = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": "2"})

        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        setting = twin_queue.get("tbl_box_prms", "MODE")
        assert setting.value == 2

    def test_on_message_with_float_value(self, handler, twin_queue):
        """Test _on_message handles float values."""
        payload = json.dumps({"table": "tbl_invertor_prms", "key": "P_ADJ_STRT", "value": 56.5})

        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        setting = twin_queue.get("tbl_invertor_prms", "P_ADJ_STRT")
        assert setting.value == 56.5

    def test_on_message_with_boolean_value(self, handler, twin_queue):
        """Test _on_message handles boolean values."""
        payload = json.dumps({"table": "tbl_box_prms", "key": "SA", "value": True})

        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        setting = twin_queue.get("tbl_box_prms", "SA")
        assert setting.value == 1

    def test_on_message_missing_table(self, handler, twin_queue, caplog):
        """Test _on_message handles missing table field."""
        payload = json.dumps({"key": "T_Room", "value": 22})

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0
        assert "Invalid message format" in caplog.text

    def test_on_message_missing_key(self, handler, twin_queue, caplog):
        """Test _on_message handles missing key field."""
        payload = json.dumps({"table": "tbl_set", "value": 22})

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0
        assert "Invalid message format" in caplog.text

    def test_on_message_missing_value(self, handler, twin_queue, caplog):
        """Test _on_message handles missing value field."""
        payload = json.dumps({"table": "tbl_set", "key": "T_Room"})

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0
        assert "Invalid message format" in caplog.text

    def test_on_message_invalid_json(self, handler, twin_queue, caplog):
        """Test _on_message handles invalid JSON."""
        payload = b"not valid json"

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload)

        assert twin_queue.size() == 0
        assert "Failed to parse JSON" in caplog.text

    def test_on_message_logs_enqueued(self, handler, twin_queue, caplog):
        """Test _on_message logs the enqueued setting."""
        payload = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": 2})

        with caplog.at_level("INFO"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert "Twin setting enqueued: tbl_box_prms:MODE=2" in caplog.text

    def test_on_message_overwrites_same_key(self, handler, twin_queue):
        """Test _on_message overwrites existing setting with same key."""
        payload1 = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": 2})
        payload2 = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": 1})

        handler._on_message("oig/test_device_123/control/set", payload1.encode("utf-8"))
        handler._on_message("oig/test_device_123/control/set", payload2.encode("utf-8"))

        assert twin_queue.size() == 1
        setting = twin_queue.get("tbl_box_prms", "MODE")
        assert setting.value == 1

    def test_on_message_different_keys(self, handler, twin_queue):
        """Test _on_message handles different keys."""
        payload1 = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": 2})
        payload2 = json.dumps({"table": "tbl_box_prms", "key": "SA", "value": 1})

        handler._on_message("oig/test_device_123/control/set", payload1.encode("utf-8"))
        handler._on_message("oig/test_device_123/control/set", payload2.encode("utf-8"))

        assert twin_queue.size() == 2

    def test_on_message_different_tables(self, handler, twin_queue):
        """Test _on_message handles different tables."""
        payload1 = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": 2})
        payload2 = json.dumps({"table": "tbl_batt_prms", "key": "BAT_MIN", "value": 25})

        handler._on_message("oig/test_device_123/control/set", payload1.encode("utf-8"))
        handler._on_message("oig/test_device_123/control/set", payload2.encode("utf-8"))

        assert twin_queue.size() == 2
        assert twin_queue.get("tbl_box_prms", "MODE").value == 2
        assert twin_queue.get("tbl_batt_prms", "BAT_MIN").value == 25


class TestTwinControlHandlerEdgeCases:
    """Edge case tests for TwinControlHandler."""

    @pytest.fixture
    def mock_mqtt(self):
        """Create a mock MQTT client."""
        mqtt = MagicMock()
        mqtt.is_ready.return_value = True
        mqtt.subscribe.return_value = True
        mqtt.unsubscribe.return_value = True
        return mqtt

    @pytest.fixture
    def twin_queue(self):
        """Create a fresh TwinQueue instance."""
        return TwinQueue()

    @pytest.fixture
    def handler(self, mock_mqtt, twin_queue):
        """Create a TwinControlHandler instance."""
        return TwinControlHandler(
            mqtt=mock_mqtt,
            twin_queue=twin_queue,
            device_id="test_device_123",
        )

    def test_on_message_with_null_value(self, handler, twin_queue, caplog):
        """Test _on_message rejects null value as invalid."""
        payload = json.dumps({"table": "tbl_set", "key": "T_Null", "value": None})

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0
        assert "Invalid message format" in caplog.text

    def test_on_message_with_list_value(self, handler, twin_queue):
        payload = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": [1, 2, 3]})

        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0

    def test_on_message_with_dict_value(self, handler, twin_queue):
        payload = json.dumps({"table": "tbl_box_prms", "key": "MODE", "value": {"a": 1}})

        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0

    def test_on_message_empty_json(self, handler, twin_queue, caplog):
        """Test _on_message handles empty JSON object."""
        payload = json.dumps({})

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0
        assert "Invalid message format" in caplog.text

    def test_on_message_extra_fields(self, handler, twin_queue):
        """Test _on_message ignores extra fields."""
        payload = json.dumps({
            "table": "tbl_box_prms",
            "key": "MODE",
            "value": 2,
            "extra": "ignored",
        })

        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        setting = twin_queue.get("tbl_box_prms", "MODE")
        assert setting.value == 2

    def test_on_message_rejects_not_allowed_setting(self, handler, twin_queue, caplog):
        payload = json.dumps({"table": "tbl_box_prms", "key": "NOT_ALLOWED", "value": 1})

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0
        assert "not allowed" in caplog.text

    def test_on_message_rejects_value_below_min(self, handler, twin_queue, caplog):
        payload = json.dumps({"table": "tbl_batt_prms", "key": "BAT_MIN", "value": 10})

        with caplog.at_level("WARNING"):
            handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert twin_queue.size() == 0
        assert "below min" in caplog.text

    def test_on_message_accepts_compat_topic_value_with_validation(self, handler, twin_queue):
        handler._on_message(
            "oig_local/test_device_123/set/tbl_batt_prms/BAT_MIN",
            b"22",
        )

        setting = twin_queue.get("tbl_batt_prms", "BAT_MIN")
        assert setting is not None
        assert setting.value == 22

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (b"true", 1),
            (b"ON", 1),
            (b"false", 0),
            (b"off", 0),
        ],
    )
    def test_on_message_accepts_compat_switch_payloads(self, handler, twin_queue, payload, expected):
        handler._on_message(
            "oig_local/test_device_123/set/tbl_box_prms/SA",
            payload,
        )

        setting = twin_queue.get("tbl_box_prms", "SA")
        assert setting is not None
        assert setting.value == expected

    def test_on_message_routes_proxy_control_without_queue(self, mock_mqtt, twin_queue):
        called = []

        def _cb(table, key, value):
            called.append((table, key, value))
            return True

        handler = TwinControlHandler(
            mqtt=mock_mqtt,
            twin_queue=twin_queue,
            device_id="test_device_123",
            proxy_control_handler=_cb,
        )

        payload = json.dumps({"table": "proxy_control", "key": "PROXY_MODE", "value": 2})
        handler._on_message("oig/test_device_123/control/set", payload.encode("utf-8"))

        assert called == [("proxy_control", "PROXY_MODE", 2)]
        assert twin_queue.size() == 0

    @pytest.mark.asyncio
    async def test_start_uses_custom_namespace_for_compat_topic(self, mock_mqtt, twin_queue):
        handler = TwinControlHandler(
            mqtt=mock_mqtt,
            twin_queue=twin_queue,
            device_id="test_device_123",
            namespace="custom_ns",
        )

        await handler.start()

        mock_mqtt.subscribe.assert_has_calls(
            [
                call("oig/+/control/set", handler._on_message),
                call("custom_ns/+/set/#", handler._on_message),
            ]
        )

    def test_on_message_accepts_custom_namespace_compat_topic(self, mock_mqtt, twin_queue):
        handler = TwinControlHandler(
            mqtt=mock_mqtt,
            twin_queue=twin_queue,
            device_id="test_device_123",
            namespace="custom_ns",
        )

        handler._on_message(
            "custom_ns/test_device_123/set/tbl_batt_prms/BAT_MIN",
            b"22",
        )

        setting = twin_queue.get("tbl_batt_prms", "BAT_MIN")
        assert setting is not None
        assert setting.value == 22

    def test_handler_with_different_device_id(self, mock_mqtt, twin_queue):
        """Test handler uses correct topic for different device ID."""
        handler = TwinControlHandler(
            mqtt=mock_mqtt,
            twin_queue=twin_queue,
            device_id="my_device_456",
        )

        assert handler._topic == "oig/+/control/set"

    @pytest.mark.asyncio
    async def test_start_logs_subscription(self, handler, mock_mqtt, caplog):
        """Test start() logs subscription message."""
        with caplog.at_level("INFO"):
            await handler.start()

        assert "Subscribed to oig/+/control/set" in caplog.text

    @pytest.mark.asyncio
    async def test_stop_logs_unsubscription(self, handler, mock_mqtt, caplog):
        """Test stop() logs unsubscription message."""
        await handler.start()

        with caplog.at_level("INFO"):
            await handler.stop()

        assert "Unsubscribed from oig/+/control/set" in caplog.text
