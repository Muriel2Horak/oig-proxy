"""
Testy pro mqtt/status.py — ProxyStatusPublisher.
"""
from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from mqtt.status import ProxyStatusPublisher


def make_publisher(mqtt_client=None, interval=60, proxy_device_id="oig_proxy"):
    """Helper pro vytvoření ProxyStatusPublisher s default hodnotami."""
    if mqtt_client is None:
        mqtt_client = MagicMock()
    return ProxyStatusPublisher(mqtt_client, interval, proxy_device_id)


def make_mqtt_client(connected=True, publish_result=True):
    """Helper pro vytvoření mock MQTTClient."""
    client = MagicMock()
    client.connected = connected
    client.is_ready.return_value = connected
    client.publish_state.return_value = publish_result
    return client


def test_init_default():
    """Inicializace nastaví správné default hodnoty."""
    mqtt = MagicMock()
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    assert pub._mqtt is mqtt
    assert pub._interval == 60
    assert pub._proxy_device_id == "oig_proxy"
    assert pub._frame_count == 0
    assert pub._last_frame_table == ""
    assert pub._last_frame_device_id == ""
    assert pub._last_frame_timestamp == 0.0
    assert pub._running is False


def test_record_frame_increments_count():
    """record_frame inkrementuje počítadlo frame_count."""
    mqtt = MagicMock()
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    assert pub._frame_count == 0
    pub.record_frame("DEV01", "tbl_invertor")
    assert pub._frame_count == 1
    pub.record_frame("DEV01", "tbl_invertor")
    assert pub._frame_count == 2


def test_record_frame_updates_last_frame_info():
    """record_frame aktualizuje poslední frame info."""
    mqtt = MagicMock()
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    pub.record_frame("DEV01", "tbl_invertor")

    assert pub._last_frame_device_id == "DEV01"
    assert pub._last_frame_table == "tbl_invertor"
    assert pub._last_frame_timestamp > 0


def test_record_frame_overwrites():
    """record_frame přepíše předchozí hodnoty."""
    mqtt = MagicMock()
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    pub.record_frame("DEV01", "tbl_invertor")
    first_timestamp = pub._last_frame_timestamp

    pub.record_frame("DEV02", "tbl_batt")

    assert pub._last_frame_device_id == "DEV02"
    assert pub._last_frame_table == "tbl_batt"
    assert pub._last_frame_timestamp > first_timestamp
    assert pub._frame_count == 2


def test_publish_when_mqtt_not_ready():
    """_publish nepublikuje, když MQTT není ready."""
    mqtt = MagicMock()
    mqtt.is_ready.return_value = False
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    pub.record_frame("DEV01", "tbl_invertor")
    pub._publish()

    mqtt.publish_state.assert_not_called()


def test_publish_when_mqtt_ready():
    """_publish publikuje status, když je MQTT ready."""
    mqtt = make_mqtt_client(connected=True)
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    pub.record_frame("DEV01", "tbl_invertor")
    pub._publish()

    mqtt.publish_state.assert_called_once()
    call_args = mqtt.publish_state.call_args
    device_id, table, payload = call_args[0]

    assert device_id == "oig_proxy"
    assert table == "proxy_status"
    assert payload["connection_status"] == "connected"
    assert payload["last_frame_table"] == "tbl_invertor"
    assert payload["frame_count"] == 1
    assert payload["box_device_id"] == "DEV01"
    assert payload["last_data"]
    assert payload["last_data"].endswith("Z")
    assert payload["last_data_update"] == payload["last_data"]
    assert isinstance(payload["last_data_age_s"], int)


def test_publish_disconnected_status():
    """_publish nepublikuje, když MQTT není ready (disconnected)."""
    mqtt = make_mqtt_client(connected=False)
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    pub.record_frame("DEV01", "tbl_invertor")
    pub._publish()

    mqtt.publish_state.assert_not_called()


def test_publish_with_no_frames():
    """_publish správně publikuje s nulovým počtem frameů."""
    mqtt = make_mqtt_client(connected=True)
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    pub._publish()

    call_args = mqtt.publish_state.call_args
    payload = call_args[0][2]

    assert payload["frame_count"] == 0
    assert payload["last_frame_table"] == ""
    assert payload["box_device_id"] == ""
    assert payload["last_data"] == ""
    assert payload["last_data_update"] == ""
    assert payload["last_data_age_s"] == 0


def test_run_with_disabled_interval():
    """run se okamžitě ukončí při intervalu <= 0."""
    mqtt = make_mqtt_client()
    pub = ProxyStatusPublisher(mqtt, 0, "oig_proxy")

    async def run_test():
        await pub.run()

    asyncio.run(run_test())

    assert pub._running is False


def test_run_with_negative_interval():
    """run se okamžitě ukončí při negativním intervalu."""
    mqtt = make_mqtt_client()
    pub = ProxyStatusPublisher(mqtt, -1, "oig_proxy")

    async def run_test():
        await pub.run()

    asyncio.run(run_test())

    assert pub._running is False


def test_run_publishes_periodically():
    """run periodicky publikuje status."""
    mqtt = make_mqtt_client()
    pub = ProxyStatusPublisher(mqtt, 0.1, "oig_proxy")  # 100ms interval

    pub.record_frame("DEV01", "tbl_invertor")

    async def run_test():
        task = asyncio.create_task(pub.run())
        await asyncio.sleep(0.35)  # Čekáme na 3-4 publikace
        pub.stop()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            pass

    asyncio.run(run_test())

    assert mqtt.publish_state.call_count >= 3


def test_stop_sets_running_false():
    """stop nastaví _running na False."""
    mqtt = MagicMock()
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    pub.stop()
    assert pub._running is False


def test_custom_proxy_device_id():
    """Použije se custom proxy_device_id."""
    mqtt = make_mqtt_client()
    pub = ProxyStatusPublisher(mqtt, 60, "my_proxy")

    pub._publish()

    call_args = mqtt.publish_state.call_args
    device_id = call_args[0][0]

    assert device_id == "my_proxy"


def test_multiple_frames_accumulate():
    """Více frameů se akumuluje do frame_count."""
    mqtt = make_mqtt_client()
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy")

    for i in range(5):
        pub.record_frame(f"DEV{i:02d}", "tbl_test")

    pub._publish()

    call_args = mqtt.publish_state.call_args
    payload = call_args[0][2]

    assert payload["frame_count"] == 5
    assert payload["box_device_id"] == "DEV04"
    assert payload["last_frame_table"] == "tbl_test"


def test_publish_sends_discovery_for_proxy_status_when_loader_present():
    mqtt = make_mqtt_client(connected=True)
    loader = MagicMock()
    loader.lookup.return_value = {
        "name_cs": "Proxy Stav",
        "device_mapping": "proxy",
        "entity_category": "diagnostic",
    }
    pub = ProxyStatusPublisher(mqtt, 60, "oig_proxy", sensor_loader=loader)

    pub._publish()

    assert mqtt.send_discovery.call_count >= 1
