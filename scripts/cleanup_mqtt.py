#!/usr/bin/env python3
"""
Skript pro vyčištění MQTT retained messages pro OIG Proxy.
Smaže všechny discovery a state topics.

Použití:
    pip install paho-mqtt
    python cleanup_mqtt.py

Nebo v HA terminálu:
    pip install paho-mqtt && python /config/cleanup_mqtt.py
"""

import time
import paho.mqtt.client as mqtt

# Konfigurace
MQTT_HOST = "10.0.0.143"  # nebo "core-mosquitto" v HA
MQTT_PORT = 1883
MQTT_USER = "oig"
MQTT_PASS = "oig"
DEVICE_ID = "2206237016"

# Topics k vyčištění
TOPICS_TO_CLEAR = []


def on_connect(client, _userdata, _flags, rc):
    """MQTT on_connect callback."""
    if rc == 0:
        print(f"✅ Připojeno k MQTT {MQTT_HOST}")
        # Subscribe na discovery topics
        client.subscribe("homeassistant/sensor/oig_local_#")
        client.subscribe("homeassistant/binary_sensor/oig_local_#")
        client.subscribe(f"oig_local/{DEVICE_ID}/#")
    else:
        print(f"❌ Připojení selhalo: {rc}")


def on_message(_client, _userdata, msg):
    """MQTT on_message callback pro retained zprávy."""
    if msg.retain:
        TOPICS_TO_CLEAR.append(msg.topic)
        print(f"  Found: {msg.topic}")


def main():
    """Spustí cleanup retained topics pro OIG proxy."""
    print("=== OIG MQTT Cleanup ===")
    print(f"Host: {MQTT_HOST}, Device: {DEVICE_ID}")
    print()

    # Krok 1: Zjistíme retained messages
    print("1. Hledám retained messages...")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    # Počkáme na messages
    time.sleep(3)
    client.loop_stop()

    # Přidáme známé topics
    known_topics = [
        f"oig_local/{DEVICE_ID}/availability",
        f"oig_local/{DEVICE_ID}/state",
        f"oig_local/{DEVICE_ID}/tbl_actual/state",
        f"oig_local/{DEVICE_ID}/tbl_box_prms/state",
        f"oig_local/{DEVICE_ID}/tbl_invertor_prms/state",
        f"oig_local/{DEVICE_ID}/tbl_batt_prms/state",
        f"oig_local/{DEVICE_ID}/tbl_events/state",
    ]
    for t in known_topics:
        if t not in TOPICS_TO_CLEAR:
            TOPICS_TO_CLEAR.append(t)

    print(f"\nNalezeno {len(TOPICS_TO_CLEAR)} topics k vyčištění")

    if not TOPICS_TO_CLEAR:
        print("Nic k vyčištění.")
        return

    # Krok 2: Smažeme (pošleme prázdnou retained message)
    print("\n2. Mažu retained messages...")

    client2 = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client2.username_pw_set(MQTT_USER, MQTT_PASS)
    client2.connect(MQTT_HOST, MQTT_PORT, 60)

    for topic in TOPICS_TO_CLEAR:
        client2.publish(topic, "", retain=True)
        print(f"  Cleared: {topic}")

    client2.disconnect()

    print(f"\n✅ Vyčištěno {len(TOPICS_TO_CLEAR)} topics")
    print("\nTeď restartuj MQTT integraci v HA:")
    print("  Settings → Devices & Services → MQTT → ⋮ → Reload")

if __name__ == "__main__":
    main()
