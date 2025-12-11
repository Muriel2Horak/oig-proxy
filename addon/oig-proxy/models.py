#!/usr/bin/env python3
"""
Datové modely pro OIG Proxy.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


# ============================================================================
# Proxy Mode Enum
# ============================================================================

class ProxyMode(Enum):
    """Režimy provozu proxy."""
    ONLINE = "online"      # Cloud OK, fronta prázdná → direct forward
    OFFLINE = "offline"    # Cloud DOWN → local ACK + queue
    REPLAY = "replay"      # Cloud OK, fronta NEPRÁZDNÁ → vyprazdňování


# ============================================================================
# Sensor Configuration
# ============================================================================

@dataclass
class SensorConfig:
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


# ============================================================================
# Queue Models
# ============================================================================

@dataclass
class QueuedFrame:
    """Frame uložený ve frontě."""
    id: int
    timestamp: float
    table_name: str
    frame_data: str
    device_id: str | None = None
    queued_at: str | None = None


@dataclass
class QueuedMQTTMessage:
    """MQTT message uložená ve frontě."""
    id: int
    timestamp: float
    data: dict[str, Any]
    queued_at: str | None = None


# ============================================================================
# Warning Map Entry
# ============================================================================

@dataclass
class WarningEntry:
    """Položka v warning map pro bitové chyby."""
    bit: int
    remark: str | None = None
    remark_cs: str | None = None
    code: str | None = None
