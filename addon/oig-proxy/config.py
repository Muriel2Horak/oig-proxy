#!/usr/bin/env python3
"""
Konfigurace OIG Proxy - všechny konstanty a environment variables.
"""

import os

# ============================================================================
# MQTT Availability Check
# ============================================================================
try:
    import paho.mqtt.client  # noqa: F401
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

# ============================================================================
# Helpers
# ============================================================================


def _get_int_env(name: str, default: int) -> int:
    """Vrátí int z env proměnné s bezpečným fallbackem."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw == "" or raw.lower() == "null":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ============================================================================
# MQTT Configuration
# ============================================================================
MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_NAMESPACE = os.getenv("MQTT_NAMESPACE", "oig_local")
MQTT_PUBLISH_QOS = 1  # QoS level (0=fire&forget, 1=at least once)
MQTT_STATE_RETAIN = os.getenv("MQTT_STATE_RETAIN", "true").lower() == "true"

# ============================================================================
# Cloud Configuration
# ============================================================================
TARGET_SERVER = os.getenv("TARGET_SERVER", "oigservis.cz")
TARGET_PORT = int(os.getenv("TARGET_PORT", "5710"))
CLOUD_ACK_TIMEOUT = float(os.getenv("CLOUD_ACK_TIMEOUT", "3.0"))

# ============================================================================
# Proxy Configuration
# ============================================================================
DEVICE_ID = os.getenv("DEVICE_ID", "")  # Povinné!
PROXY_LISTEN_HOST = os.getenv("PROXY_LISTEN_HOST", "0.0.0.0")
PROXY_LISTEN_PORT = int(os.getenv("PROXY_PORT", "5710"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PROXY_STATUS_INTERVAL = _get_int_env("PROXY_STATUS_INTERVAL", 60)

# ============================================================================
# Sensor Map Configuration
# ============================================================================
SENSOR_MAP_PATH = os.getenv(
    "SENSOR_MAP_PATH",
    os.path.join(os.path.dirname(__file__), "sensor_map.json")
)
# 0 disables reload
MAP_RELOAD_SECONDS = int(os.getenv("MAP_RELOAD_SECONDS", "0"))

# ============================================================================
# Persistence Paths
# ============================================================================
DATA_DIR = os.getenv("DATA_DIR", "/data")
MODE_STATE_PATH = os.path.join(DATA_DIR, "mode_state.json")
PRMS_STATE_PATH = os.path.join(DATA_DIR, "prms_state.json")
CLOUD_QUEUE_DB_PATH = os.path.join(DATA_DIR, "cloud_queue.db")
MQTT_QUEUE_DB_PATH = os.path.join(DATA_DIR, "mqtt_queue.db")
CAPTURE_DB_PATH = os.path.join(DATA_DIR, "payloads.db")

# ============================================================================
# Capture Configuration
# ============================================================================
CAPTURE_PAYLOADS = os.getenv("CAPTURE_PAYLOADS", "false").lower() == "true"
CAPTURE_RAW_BYTES = os.getenv("CAPTURE_RAW_BYTES", "false").lower() == "true"

# ============================================================================
# Queue Configuration
# ============================================================================
CLOUD_QUEUE_MAX_SIZE = int(os.getenv("CLOUD_QUEUE_MAX_SIZE", "10000"))
MQTT_QUEUE_MAX_SIZE = int(os.getenv("MQTT_QUEUE_MAX_SIZE", "5000"))

# ============================================================================
# Health Check Configuration
# ============================================================================
CLOUD_HEALTH_CHECK_INTERVAL = int(
    os.getenv("CLOUD_HEALTH_CHECK_INTERVAL", "30")
)
CLOUD_HEALTH_CHECK_TIMEOUT = float(
    os.getenv("CLOUD_HEALTH_CHECK_TIMEOUT", "5.0")
)
CLOUD_HEALTH_FAIL_THRESHOLD = int(
    os.getenv("CLOUD_HEALTH_FAIL_THRESHOLD", "3")
)
CLOUD_HEALTH_SUCCESS_THRESHOLD = int(
    os.getenv("CLOUD_HEALTH_SUCCESS_THRESHOLD", "2")
)

# ============================================================================
# Replay Configuration
# ============================================================================
CLOUD_REPLAY_RATE = float(os.getenv("CLOUD_REPLAY_RATE", "1.0"))  # frames/s
MQTT_REPLAY_RATE = float(os.getenv("MQTT_REPLAY_RATE", "10.0"))   # messages/s
REPLAY_ACK_TIMEOUT = float(os.getenv("REPLAY_ACK_TIMEOUT", "5.0"))  # seconds

# ============================================================================
# MQTT Publisher Configuration
# ============================================================================
MQTT_CONNECT_TIMEOUT = int(os.getenv("MQTT_CONNECT_TIMEOUT", "10"))
MQTT_HEALTH_CHECK_INTERVAL = int(os.getenv("MQTT_HEALTH_CHECK_INTERVAL", "30"))
MQTT_PUBLISH_LOG_EVERY = int(os.getenv("MQTT_PUBLISH_LOG_EVERY", "100"))

# ============================================================================
# Device Names (for MQTT discovery)
# ============================================================================
DEVICE_NAMES = {
    "inverter": "Střídač",
    "battery": "Baterie",
    "boiler": "Bojler",
    "box": "OIG Box",
    "pv": "FVE",
    "grid": "Síť",
    "load": "Spotřeba",
    "proxy": "Proxy",
}

# Pevný device_id pro proxy/status/event senzory
PROXY_DEVICE_ID = os.getenv("PROXY_DEVICE_ID", "oig_proxy")
