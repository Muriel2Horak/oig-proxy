#!/usr/bin/env python3
"""
Konfigurace OIG Proxy - všechny konstanty a environment variables.
"""

import os
from importlib.util import find_spec
from typing import Any

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


def _get_bool_env(name: str, default: bool) -> bool:
    """Vrátí bool z env proměnné s bezpečným fallbackem."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw == "" or raw.lower() == "null":
        return default
    if raw.lower() in ("true", "1", "yes", "on"):
        return True
    if raw.lower() in ("false", "0", "no", "off"):
        return False
    return default


def _get_str_env(name: str, default: str, valid_values: list[str] | None = None) -> str:
    """Vrátí string z env proměnné s bezpečným fallbackem a validací."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw == "" or raw.lower() == "null":
        return default
    if valid_values and raw not in valid_values:
        return default
    return raw


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
LOCAL_GETACTUAL_ENABLED = os.getenv(
    "LOCAL_GETACTUAL_ENABLED",
    "true").lower() == "true"
LOCAL_GETACTUAL_INTERVAL_S = _get_float_env("LOCAL_GETACTUAL_INTERVAL_S", 30.0)
BOX_WATCHDOG_TIMEOUT_S = _get_float_env("BOX_WATCHDOG_TIMEOUT_S", 300.0)
FULL_REFRESH_INTERVAL_H = max(1, _get_int_env("FULL_REFRESH_INTERVAL_H", 24))

# ============================================================================
# Proxy Mode Configuration
# ============================================================================
# ONLINE = transparent forward (default; explicit online is respected),
# HYBRID = smart fallback, OFFLINE = always local
PROXY_MODE = os.getenv("PROXY_MODE", "online").lower()

# For HYBRID mode: seconds to wait before retry online
HYBRID_RETRY_INTERVAL = _get_int_env("HYBRID_RETRY_INTERVAL", 60)
# For HYBRID mode: consecutive failures before switching to offline (1 =
# immediate)
HYBRID_FAIL_THRESHOLD = _get_int_env("HYBRID_FAIL_THRESHOLD", 1)
# For HYBRID mode: connect timeout when probing cloud
HYBRID_CONNECT_TIMEOUT = _get_float_env("HYBRID_CONNECT_TIMEOUT", 5.0)

# ============================================================================
# Cloud Configuration
# ============================================================================
TARGET_SERVER = os.getenv("TARGET_SERVER", "oigservis.cz")
TARGET_PORT = int(os.getenv("TARGET_PORT", "5710"))
# ACK timeout — how long to wait for cloud ACK before treating cloud as down
CLOUD_ACK_TIMEOUT = _get_float_env("CLOUD_ACK_TIMEOUT", 60.0)

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
CONTROL_MQTT_ENABLED = os.getenv(
    "CONTROL_MQTT_ENABLED",
    "false").lower() == "true"
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
CONTROL_MQTT_RETAIN = os.getenv(
    "CONTROL_MQTT_RETAIN",
    "false").lower() == "true"
CONTROL_MQTT_STATUS_RETAIN = os.getenv(
    "CONTROL_MQTT_STATUS_RETAIN",
    "true").lower() == "true"
CONTROL_MQTT_BOX_READY_SECONDS = _get_int_env(
    "CONTROL_MQTT_BOX_READY_SECONDS", 10)
CONTROL_MQTT_ACK_TIMEOUT_S = float(
    os.getenv("CONTROL_MQTT_ACK_TIMEOUT_S", "30"))
CONTROL_MQTT_APPLIED_TIMEOUT_S = float(
    os.getenv("CONTROL_MQTT_APPLIED_TIMEOUT_S", "120"))
CONTROL_MQTT_MODE_QUIET_SECONDS = float(
    os.getenv("CONTROL_MQTT_MODE_QUIET_SECONDS", "20"))

CONTROL_WRITE_PARITY_CONTRACT: dict[str, dict[str, str]] = {
    "tbl_batt_prms": {
        "FMT_ON": "identity",
        "BAT_MIN": "identity",
    },
    "tbl_boiler_prms": {
        "ISON": "identity",
        "MANUAL": "identity",
        "SSR0": "identity",
        "SSR1": "identity",
        "SSR2": "identity",
        "OFFSET": "identity",
    },
    "tbl_box_prms": {
        "MODE": "mode_0_5",
        "BAT_AC": "identity",
        "BAT_FORMAT": "identity",
        "SA": "identity",
        "RQRESET": "identity",
    },
    "tbl_invertor_prms": {
        "GRID_PV_ON": "identity",
        "GRID_PV_OFF": "identity",
        "TO_GRID": "identity",
    },
    "tbl_invertor_prm1": {
        "AAC_MAX_CHRG": "float_1dp",
        "A_MAX_CHRG": "identity",
    },
}

# Default whitelist (deny-by-default for everything else)
CONTROL_WRITE_WHITELIST: dict[str, set[str]] = {
    tbl_name: set(tbl_items.keys())
    for tbl_name, tbl_items in CONTROL_WRITE_PARITY_CONTRACT.items()
}


def normalize_control_value(
    tbl_name: str,
    tbl_item: str,
    new_value: Any,
) -> tuple[str | None, str]:
    """Normalize control input using the parity contract for whitelist items."""
    profile = CONTROL_WRITE_PARITY_CONTRACT.get(tbl_name, {}).get(tbl_item)
    if profile is None:
        return (str(new_value), str(new_value))

    if profile == "mode_0_5":
        try:
            mode_int = int(new_value)
            if 0 <= mode_int <= 5:
                canon = str(mode_int)
                return (canon, canon)
        except (ValueError, TypeError):
            pass
        return (None, "bad_value")

    if profile == "float_1dp":
        try:
            val = float(new_value)
            canon = f"{val:.1f}"
            return (canon, canon)
        except (ValueError, TypeError):
            return (None, "bad_value")

    return (str(new_value), str(new_value))

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
CAPTURE_DB_PATH = os.path.join(DATA_DIR, "payloads.db")

# Control MQTT logging
CONTROL_MQTT_LOG_ENABLED = os.getenv(
    "CONTROL_MQTT_LOG_ENABLED",
    "false").lower() == "true"
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
# Retence capture DB (frames) v dnech. 0 = nevynucovat retenci.
CAPTURE_RETENTION_DAYS = max(0, _get_int_env("CAPTURE_RETENTION_DAYS", 7))

# ============================================================================
# Telemetry Configuration (MQTT to muriel-cz.cz)
# ============================================================================
TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() == "true"
TELEMETRY_MQTT_BROKER = os.getenv(
    "TELEMETRY_MQTT_BROKER",
    "telemetry.muriel-cz.cz:1883")
TELEMETRY_INTERVAL_S = _get_int_env("TELEMETRY_INTERVAL_S", 300)  # 5 minutes

# ============================================================================
# Twin Configuration
# ============================================================================
TWIN_ENABLED = _get_bool_env("TWIN_ENABLED", False)
TWIN_KILL_SWITCH = _get_bool_env("TWIN_KILL_SWITCH", False)
TWIN_ACK_DEADLINE_SECONDS = _get_float_env("TWIN_ACK_DEADLINE_SECONDS", 30.0)
TWIN_APPLIED_DEADLINE_SECONDS = _get_float_env("TWIN_APPLIED_DEADLINE_SECONDS", 60.0)
TWIN_VERBOSE_LOGGING = _get_bool_env("TWIN_VERBOSE_LOGGING", False)
LOCAL_CONTROL_ROUTING = _get_str_env(
    "LOCAL_CONTROL_ROUTING", 
    "auto", 
    ["auto", "force_twin", "force_cloud"]
)

# Twin Cloud Alignment: when enabled, Twin uses cloud endpoint directly
# instead of local MQTT-based sync. Default False for backward compatibility.
TWIN_CLOUD_ALIGNED = _get_bool_env("TWIN_CLOUD_ALIGNED", False)

CONTROL_TWIN_FIRST_ENABLED = _get_bool_env("CONTROL_TWIN_FIRST_ENABLED", False)

THIN_PASS_THROUGH = _get_bool_env("THIN_PASS_THROUGH", True)
LEGACY_FALLBACK = _get_bool_env("LEGACY_FALLBACK", False)


def validate_startup_guards() -> None:
    """Ověří konzistenci konfigurace a vyhodí ValueError při chybě.

    Guards (collected, not fail-fast):
      G1: force_twin requires active twin
      G2: cloud_aligned requires twin enabled
      G3: cloud_aligned incompatible with kill switch
      G4: ACK deadline must be positive
      G5: Applied deadline must be positive
      G6: Applied deadline must be >= ACK deadline
    """
    errors: list[str] = []

    # G4: ACK deadline must be positive
    if TWIN_ACK_DEADLINE_SECONDS <= 0:
        errors.append("TWIN_ACK_DEADLINE_SECONDS must be > 0")

    # G5: Applied deadline must be positive
    if TWIN_APPLIED_DEADLINE_SECONDS <= 0:
        errors.append("TWIN_APPLIED_DEADLINE_SECONDS must be > 0")

    # G6: Applied deadline must be >= ACK deadline
    if TWIN_APPLIED_DEADLINE_SECONDS < TWIN_ACK_DEADLINE_SECONDS:
        errors.append(
            "TWIN_APPLIED_DEADLINE_SECONDS must be >= TWIN_ACK_DEADLINE_SECONDS"
        )

    # G1: force_twin requires active twin
    if LOCAL_CONTROL_ROUTING == "force_twin" and (not TWIN_ENABLED or TWIN_KILL_SWITCH):
        errors.append(
            "LOCAL_CONTROL_ROUTING=force_twin requires TWIN_ENABLED=true and "
            "TWIN_KILL_SWITCH=false"
        )

    # G2: cloud_aligned requires twin enabled
    if TWIN_CLOUD_ALIGNED and not TWIN_ENABLED:
        errors.append(
            "TWIN_CLOUD_ALIGNED=true requires TWIN_ENABLED=true"
        )

    # G3: cloud_aligned incompatible with kill switch
    if TWIN_CLOUD_ALIGNED and TWIN_KILL_SWITCH:
        errors.append(
            "TWIN_CLOUD_ALIGNED=true is incompatible with TWIN_KILL_SWITCH=true"
        )

    if errors:
        raise ValueError("; ".join(errors))

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
