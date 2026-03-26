#!/usr/bin/env python3
"""Konfigurace OIG Proxy v2.

Nacita se z env vars (override) nebo defaults.
Zadne feature flagy.
"""

from __future__ import annotations

import os


class Config:
    """Konfigurace proxy nactena z env vars."""

    # TCP proxy
    proxy_host: str = "0.0.0.0"
    proxy_port: int = 5710

    # Cloud target
    cloud_host: str = "oigservis.cz"
    cloud_port: int = 5710
    cloud_connect_timeout: float = 10.0
    cloud_ack_timeout: float = 30.0

    # MQTT
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_namespace: str = "oig_local"
    mqtt_qos: int = 1
    mqtt_state_retain: bool = True

    # Proxy mode (online / hybrid)
    proxy_mode: str = "online"
    hybrid_retry_interval: int = 60
    hybrid_fail_threshold: int = 3

    # Logging
    log_level: str = "INFO"

    # Proxy status publisher
    proxy_status_interval: int = 60
    proxy_device_id: str = "oig_proxy"

    # Sensor map
    sensor_map_path: str = "/data/sensor_map.json"

    telemetry_enabled: bool = True
    telemetry_mqtt_broker: str = "telemetry.muriel-cz.cz:1883"
    telemetry_interval_s: int = 300

    capture_payloads: bool = False
    capture_raw_bytes: bool = False
    capture_retention_days: int = 7
    capture_db_path: str = "/data/payloads.db"

    capture_pcap: bool = False
    capture_pcap_path: str = "/data/capture.pcap"
    capture_pcap_interface: str = "any"
    capture_pcap_max_size_mb: int = 100

    def __init__(self) -> None:
        self.proxy_host = os.environ.get("PROXY_HOST", "0.0.0.0")
        self.proxy_port = int(os.environ.get("PROXY_PORT", "5710"))

        self.cloud_host = os.environ.get("TARGET_SERVER", "oigservis.cz")
        self.cloud_port = int(os.environ.get("TARGET_PORT", "5710"))
        self.cloud_connect_timeout = float(
            os.environ.get("CLOUD_CONNECT_TIMEOUT", "10.0"))
        self.cloud_ack_timeout = float(
            os.environ.get("CLOUD_ACK_TIMEOUT", "30.0"))

        self.mqtt_host = os.environ.get("MQTT_HOST", "core-mosquitto")
        self.mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        self.mqtt_username = os.environ.get("MQTT_USERNAME", "")
        self.mqtt_password = os.environ.get("MQTT_PASSWORD", "")
        self.mqtt_namespace = os.environ.get("MQTT_NAMESPACE", "oig_local")
        self.mqtt_qos = int(os.environ.get("MQTT_QOS", "1"))
        self.mqtt_state_retain = os.environ.get(
            "MQTT_STATE_RETAIN", "true").lower() == "true"

        self.log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

        self.proxy_status_interval = int(
            os.environ.get("PROXY_STATUS_INTERVAL", "60"))
        self.proxy_device_id = os.environ.get("PROXY_DEVICE_ID", "oig_proxy")
        self.sensor_map_path = os.environ.get("SENSOR_MAP_PATH", "/data/sensor_map.json")

        self.telemetry_enabled = os.environ.get("TELEMETRY_ENABLED", "true").lower() == "true"
        self.telemetry_mqtt_broker = os.environ.get(
            "TELEMETRY_MQTT_BROKER", "telemetry.muriel-cz.cz:1883"
        )
        self.telemetry_interval_s = int(os.environ.get("TELEMETRY_INTERVAL_S", "300"))

        self.capture_payloads = os.environ.get("CAPTURE_PAYLOADS", "false").lower() == "true"
        self.capture_raw_bytes = os.environ.get("CAPTURE_RAW_BYTES", "false").lower() == "true"
        self.capture_retention_days = int(os.environ.get("CAPTURE_RETENTION_DAYS", "7"))
        self.capture_db_path = os.environ.get("CAPTURE_DB_PATH", "/data/payloads.db")

        self.capture_pcap = os.environ.get("CAPTURE_PCAP", "false").lower() == "true"
        self.capture_pcap_path = os.environ.get("CAPTURE_PCAP_PATH", "/data/capture.pcap")
        self.capture_pcap_interface = os.environ.get("CAPTURE_PCAP_INTERFACE", "any")
        self.capture_pcap_max_size_mb = int(os.environ.get("CAPTURE_PCAP_MAX_SIZE_MB", "100"))

        self.proxy_mode = os.environ.get("PROXY_MODE", "online").strip().lower()
        self.hybrid_retry_interval = int(os.environ.get("HYBRID_RETRY_INTERVAL", "60"))
        self.hybrid_fail_threshold = int(os.environ.get("HYBRID_FAIL_THRESHOLD", "3"))

    def __repr__(self) -> str:
        return (
            f"Config(proxy={self.proxy_host}:{self.proxy_port}, "
            f"cloud={self.cloud_host}:{self.cloud_port}, "
            f"mqtt={self.mqtt_host}:{self.mqtt_port}, "
            f"namespace={self.mqtt_namespace})"
        )
