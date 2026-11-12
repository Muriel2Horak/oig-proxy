"""Setting-result publication through real status, discovery and MQTT classes."""
# Exercise the periodic publisher and broker callbacks at their transport boundary.
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mqtt.client import MQTTClient
from mqtt.status import ProxyStatusPublisher
from sensor.loader import SensorMapLoader

STATE_TOPIC = "oig_local/oig_proxy/proxy_status/state"
DISCOVERY_TOPIC = "homeassistant/sensor/oig_local_oig_proxy_proxy_status_control_last_result/config"


class Broker:
    """Transport-only double; fail selected state sends without networking."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, int, bool]] = []
        self.failures: list[Any] = []
        self.retained: dict[str, str] = {"oig_local/BOX/availability": "offline"}

    def publish(self, topic: str, payload: str, *, qos: int, retain: bool) -> Any:
        if topic == STATE_TOPIC and self.failures:
            result = self.failures.pop(0)
            if isinstance(result, Exception):
                raise result
            if result:
                return SimpleNamespace(rc=result)
        self.messages.append((topic, payload, qos, retain))
        if retain:
            self.retained[topic] = payload
        return SimpleNamespace(rc=0)

    def subscribe(self, _topic: str, *, qos: int) -> tuple[int, int]:
        return 0, 1


def make_publisher(*, loader: bool = False, **kwargs):
    mqtt = MQTTClient(host="unused.invalid", port=1883, namespace="oig_local")
    broker = Broker()
    mqtt._client = broker
    mqtt.connected = True
    sensor_loader = SensorMapLoader(str(Path(__file__).resolve().parents[2] / "addon/oig-proxy/sensor_map.json")) if loader else None
    if sensor_loader:
        sensor_loader.load()
    publisher = ProxyStatusPublisher(mqtt, 60, "oig_proxy", sensor_loader=sensor_loader, **kwargs)
    return publisher, mqtt, broker


def event(event_id="event-1", **updates):
    return {
        "result": "ack", "table": "tbl_box_prms", "key": "MODE", "value": 3,
        "source": "local", "audit_id": "audit-1", "at": "2026-11-06T09:02:55Z",
        "event_id": event_id, "evidence": "protocol_ack", "reason": "accepted",
        **updates,
    }


def statuses(broker):
    return [json.loads(payload) for topic, payload, _qos, _retain in broker.messages if topic == STATE_TOPIC]


def test_repeated_identical_results_publish_immediately_with_distinct_metadata():
    publisher, _mqtt, broker = make_publisher()
    first, second = event(), event("event-2", audit_id="audit-2", at="2026-11-06T09:03:00Z")

    publisher.record_setting_result(first)
    assert len(statuses(broker)) == 1
    publisher.record_setting_result(second)

    payloads = statuses(broker)
    assert [p["control_last_result"] for p in payloads] == ["ack tbl_box_prms:MODE=3"] * 2
    assert [p["control_result"] for p in payloads] == [first, second]
    assert all(p["frame_count"] == 0 and p["box_connected"] == 0 for p in payloads)
    assert all(topic == STATE_TOPIC for topic, *_ in broker.messages)
    assert broker.retained["oig_local/BOX/availability"] == "offline"


@pytest.mark.parametrize("failure", [4, OSError("broker unavailable")])
def test_failed_result_then_new_result_retries_fifo_without_loss(failure):
    publisher, _mqtt, broker = make_publisher()
    broker.failures = [failure, failure]
    publisher.record_setting_result(event())
    publisher.record_setting_result(event("event-2", result="timeout", value=0))
    assert statuses(broker) == []

    publisher.flush_setting_results()

    assert [p["control_result"]["event_id"] for p in statuses(broker)] == ["event-1", "event-2"]
    assert [p["control_last_result"] for p in statuses(broker)] == [
        "ack tbl_box_prms:MODE=3", "timeout tbl_box_prms:MODE=0",
    ]
    publisher.flush_setting_results()
    assert len(statuses(broker)) == 2


def test_not_ready_backlog_drains_in_order_on_periodic_status_then_preserves_latest():
    publisher, mqtt, broker = make_publisher()
    mqtt.connected = False
    publisher.record_setting_result(event())
    publisher.record_setting_result(event("event-2", result="dropped", reason="expired"))
    publisher._publish()
    assert statuses(broker) == []

    mqtt.connected = True
    publisher._publish()
    publisher._publish()

    assert [p["control_result"]["event_id"] for p in statuses(broker)] == ["event-1", "event-2", "event-2"]
    assert statuses(broker)[-1]["control_last_result"] == "dropped tbl_box_prms:MODE=3"


def test_missing_metadata_gets_unique_id_and_utc_timestamp_without_rewriting_supplied_metadata():
    publisher, _mqtt, broker = make_publisher()
    minimal = {"result": "nack", "table": "tbl_box_prms", "key": "MODE", "value": "0", "source": "cloud"}
    publisher.record_setting_result(minimal)
    publisher.record_setting_result(minimal)
    one, two = statuses(broker)
    assert one["control_result"]["event_id"] != two["control_result"]["event_id"]
    assert datetime.fromisoformat(one["control_result"]["at"].replace("Z", "+00:00")).utcoffset() == timezone.utc.utcoffset(None)
    assert one["control_last_result"] == "nack tbl_box_prms:MODE=0"
    assert "event_id" not in minimal and "at" not in minimal
    supplied = event(at="2026-11-06T10:00:00+01:00", evidence={"kind": "setting_event"})
    publisher.record_setting_result(supplied)
    assert statuses(broker)[-1]["control_result"] == supplied


def test_pending_event_metadata_is_an_owned_snapshot():
    publisher, mqtt, broker = make_publisher()
    mqtt.connected = False
    original = event(evidence={"kind": "setting_event", "proof": ["matching"]})
    publisher.record_setting_result(original)
    original["value"] = 0
    original["evidence"]["proof"].append("mutated")
    mqtt.connected = True

    publisher.flush_setting_results()

    payload = statuses(broker)[0]
    assert payload["control_last_result"] == "ack tbl_box_prms:MODE=3"
    assert payload["control_result"]["evidence"] == {"kind": "setting_event", "proof": ["matching"]}


def test_startup_unknown_is_safe_and_ordinary_discovery_exposes_result_attributes():
    publisher, _mqtt, broker = make_publisher(loader=True)
    publisher._publish()

    payload = statuses(broker)[0]
    assert payload["control_last_result"] == "unknown"
    assert payload["control_result"] == {}
    assert payload["box_connected"] == 0
    config = json.loads(next(value for topic, value, *_ in broker.messages if topic == DISCOVERY_TOPIC))
    assert config["unique_id"] == "oig_local_oig_proxy_proxy_status_control_last_result"
    assert config["default_entity_id"] == "sensor.oig_local_oig_proxy_proxy_status_control_last_result"
    assert config["state_topic"] == STATE_TOPIC
    assert config["json_attributes_topic"] == STATE_TOPIC
    assert config["force_update"] is True
    assert config["value_template"] == "{{ value_json.get('control_last_result') }}"
    assert config["json_attributes_template"] == "{{ value_json.get('control_result', {}) | tojson }}"
    assert not any("/set/" in topic or topic.endswith("/control/set") for topic, *_ in broker.messages)
    assert broker.retained["oig_local/BOX/availability"] == "offline"


def test_reconnect_discovery_and_periodic_snapshot_preserve_result_state_and_attributes():
    publisher, mqtt, broker = make_publisher(loader=True)
    expected = event(evidence="setting_event")
    publisher.record_setting_result(expected)
    first_config = json.loads(next(value for topic, value, *_ in broker.messages if topic == DISCOVERY_TOPIC))
    broker.messages.clear()
    mqtt._on_disconnect(broker, None, 1)
    mqtt._on_connect(broker, None, {}, 0)
    publisher.flush_setting_results()
    publisher._publish()

    second_config = json.loads(next(value for topic, value, *_ in broker.messages if topic == DISCOVERY_TOPIC))
    assert first_config == second_config
    payload = statuses(broker)[-1]
    assert payload["control_result"] == expected
    assert payload["control_last_result"] == "ack tbl_box_prms:MODE=3"
    assert second_config["json_attributes_template"] == "{{ value_json.get('control_result', {}) | tojson }}"
    assert broker.retained["oig_local/BOX/availability"] == "offline"


def test_diagnostics_callbacks_are_sampled_for_each_full_result_payload():
    diagnostics = {"inflight": "tbl_box_prms:SA=1", "queue_len": 2}
    publisher, _mqtt, broker = make_publisher(
        get_control_inflight=lambda: diagnostics["inflight"],
        get_control_queue_len=lambda: diagnostics["queue_len"],
    )
    publisher.record_setting_result(event())
    diagnostics.update(inflight="", queue_len=0)
    publisher.record_setting_result(event("event-2"))

    assert [(p["control_inflight"], p["control_queue_len"]) for p in statuses(broker)] == [
        ("tbl_box_prms:SA=1", 2), ("", 0),
    ]


def test_result_attribute_special_case_does_not_change_unrelated_discovery():
    _publisher, mqtt, broker = make_publisher()
    assert mqtt.send_discovery(device_id="oig_proxy", table="proxy_status", sensor_key="frame_count", sensor_name="Frames")
    config = json.loads(broker.messages[-1][1])
    assert "json_attributes_topic" not in config
    assert "json_attributes_template" not in config
