#!/usr/bin/env python3
"""
Datové modely pro OIG Proxy.
"""

from dataclasses import dataclass
from enum import Enum


# ============================================================================
# Proxy Mode Enum
# ============================================================================

class ProxyMode(Enum):
    """Režimy provozu proxy."""
    ONLINE = "online"      # Transparent forward - no local ACK, no HC
    HYBRID = "hybrid"      # Smart fallback - timeout-based offline detection
    OFFLINE = "offline"    # Always local ACK, never connect to cloud


# ============================================================================
# Sensor Configuration
# ============================================================================

@dataclass
class SensorConfig:  # pylint: disable=too-many-instance-attributes
    """Konfigurace senzoru pro MQTT discovery."""
    name: str
    unit: str
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    device_mapping: str | None = None
    entity_category: str | None = None
    options: list[str] | None = None  # Pro enum device_class
    is_binary: bool = False  # True pro binary_sensor
    json_attributes_topic: str | None = None
