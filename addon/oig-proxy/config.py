#!/usr/bin/env python3
"""
Konfigurace OIG Proxy - všechny konstanty a environment variables.
"""

import os
from importlib.util import find_spec

# ============================================================================
# MQTT Availability Check
# ============================================================================
try:
    MQTT_AVAILABLE = find_spec("paho.mqtt.client") is not None
except ModuleNotFoundError:
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


def _get_float_env(name: str, default: float) -> float:
    """Vrátí float z env proměnné s bezpečným fallbackem."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw == "" or raw.lower() == "null":
        return default
    try:
        return float(raw)
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
PROXY_STATUS_ATTRS_TOPIC = os.getenv(
    "PROXY_STATUS_ATTRS_TOPIC", "oig_local/oig_proxy/proxy_status/attrs"
)
LOCAL_GETACTUAL_ENABLED = os.getenv("LOCAL_GETACTUAL_ENABLED", "false").lower() == "true"
LOCAL_GETACTUAL_INTERVAL_S = _get_float_env("LOCAL_GETACTUAL_INTERVAL_S", 10.0)
FULL_REFRESH_INTERVAL_H = max(1, _get_int_env("FULL_REFRESH_INTERVAL_H", 24))

# ============================================================================
# Proxy Mode Configuration
# ============================================================================
# ONLINE = transparent forward (default), HYBRID = smart fallback, OFFLINE = always local
PROXY_MODE = os.getenv("PROXY_MODE", "online").lower()
if PROXY_MODE == "online":
    PROXY_MODE = "hybrid"  # Force upgrade: treat legacy 'online' as 'hybrid'

# For HYBRID mode: seconds to wait before retry online
HYBRID_RETRY_INTERVAL = _get_int_env("HYBRID_RETRY_INTERVAL", 300)
# For HYBRID mode: consecutive failures before switching to offline (1 = immediate)
HYBRID_FAIL_THRESHOLD = _get_int_env("HYBRID_FAIL_THRESHOLD", 1)
# For HYBRID mode: connect timeout when probing cloud
HYBRID_CONNECT_TIMEOUT = _get_float_env("HYBRID_CONNECT_TIMEOUT", 5.0)

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
PROXY_LISTEN_HOST = os.getenv(
    "PROXY_LISTEN_HOST",
    "0.0.0.0",
)  # nosec B104 - needs LAN binding for appliance mode
PROXY_LISTEN_PORT = int(os.getenv("PROXY_PORT", "5710"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PROXY_STATUS_INTERVAL = _get_int_env("PROXY_STATUS_INTERVAL", 60)

# ============================================================================
# Control API (prototype)
# ============================================================================
# No auth in prototype; set port to 0 to disable.
CONTROL_API_HOST = os.getenv("CONTROL_API_HOST", "127.0.0.1")
CONTROL_API_PORT = _get_int_env("CONTROL_API_PORT", 0)

# ============================================================================
# Control over MQTT (production)
# ============================================================================
CONTROL_MQTT_ENABLED = os.getenv("CONTROL_MQTT_ENABLED", "false").lower() == "true"
CONTROL_MQTT_SET_TOPIC = os.getenv(
    "CONTROL_MQTT_SET_TOPIC", "oig_local/oig_proxy/control/set"
)
CONTROL_MQTT_RESULT_TOPIC = os.getenv(
    "CONTROL_MQTT_RESULT_TOPIC", "oig_local/oig_proxy/control/result"
)
CONTROL_MQTT_STATUS_PREFIX = os.getenv(
    "CONTROL_MQTT_STATUS_PREFIX", "oig_local/oig_proxy/control/status"
)
CONTROL_MQTT_QOS = _get_int_env("CONTROL_MQTT_QOS", 1)
CONTROL_MQTT_RETAIN = os.getenv("CONTROL_MQTT_RETAIN", "false").lower() == "true"
CONTROL_MQTT_STATUS_RETAIN = os.getenv("CONTROL_MQTT_STATUS_RETAIN", "true").lower() == "true"
CONTROL_MQTT_BOX_READY_SECONDS = _get_int_env("CONTROL_MQTT_BOX_READY_SECONDS", 10)
CONTROL_MQTT_ACK_TIMEOUT_S = float(os.getenv("CONTROL_MQTT_ACK_TIMEOUT_S", "30"))
CONTROL_MQTT_APPLIED_TIMEOUT_S = float(os.getenv("CONTROL_MQTT_APPLIED_TIMEOUT_S", "120"))
CONTROL_MQTT_MODE_QUIET_SECONDS = float(os.getenv("CONTROL_MQTT_MODE_QUIET_SECONDS", "20"))

# Default whitelist (deny-by-default for everything else)
CONTROL_WRITE_WHITELIST: dict[str, set[str]] = {
    "tbl_batt_prms": {"FMT_ON", "BAT_MIN"},
    "tbl_boiler_prms": {"ISON", "MANUAL", "SSR0", "SSR1", "SSR2", "OFFSET"},
    "tbl_box_prms": {"MODE", "BAT_AC", "BAT_FORMAT", "SA", "RQRESET"},
    "tbl_invertor_prms": {"GRID_PV_ON", "GRID_PV_OFF", "TO_GRID"},
    "tbl_invertor_prm1": {"AAC_MAX_CHRG", "A_MAX_CHRG"},
}

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
MQTT_QUEUE_DB_PATH = os.path.join(DATA_DIR, "mqtt_queue.db")
CAPTURE_DB_PATH = os.path.join(DATA_DIR, "payloads.db")

# Control MQTT logging
CONTROL_MQTT_LOG_ENABLED = os.getenv("CONTROL_MQTT_LOG_ENABLED", "false").lower() == "true"
CONTROL_MQTT_LOG_PATH = os.getenv(
    "CONTROL_MQTT_LOG_PATH", os.path.join(DATA_DIR, "control_results.jsonl")
)
CONTROL_MQTT_PENDING_PATH = os.getenv(
    "CONTROL_MQTT_PENDING_PATH", os.path.join(DATA_DIR, "control_pending.json")
)

# ============================================================================
# Capture Configuration
# ============================================================================
CAPTURE_PAYLOADS = os.getenv("CAPTURE_PAYLOADS", "false").lower() == "true"
CAPTURE_RAW_BYTES = os.getenv("CAPTURE_RAW_BYTES", "false").lower() == "true"

# ============================================================================
# MQTT Queue Configuration
# ============================================================================
MQTT_QUEUE_MAX_SIZE = int(os.getenv("MQTT_QUEUE_MAX_SIZE", "5000"))

# ============================================================================
# Telemetry Configuration (MQTT to muriel-cz.cz)
# ============================================================================
TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() == "true"
TELEMETRY_MQTT_BROKER = os.getenv("TELEMETRY_MQTT_BROKER", "telemetry.muriel-cz.cz:1883")
TELEMETRY_INTERVAL_S = _get_int_env("TELEMETRY_INTERVAL_S", 300)  # 5 minutes

# ============================================================================
# MQTT Publisher Configuration
# ============================================================================
MQTT_REPLAY_RATE = float(os.getenv("MQTT_REPLAY_RATE", "10.0"))   # messages/s
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
    "recuper": "Rekuperace",
    "heat_pump": "Tepelné čerpadlo",
    "aircon": "Klimatizace",
    "wl_charge": "Wallbox",
    "box": "OIG Box",
    "pv": "FVE",
    "grid": "Síť",
    "load": "Spotřeba",
    "proxy": "Proxy",
}

# Pevný device_id pro proxy/status/event senzory
PROXY_DEVICE_ID = os.getenv("PROXY_DEVICE_ID", "oig_proxy")
