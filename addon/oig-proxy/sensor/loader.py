"""Sensor map loader for OIG Proxy v2."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SensorMapLoader:
    """Loads and provides lookup for sensor_map.json."""

    def __init__(self, path: str) -> None:
        """Initialize loader with path to sensor_map.json.

        Args:
            path: Path to the sensor_map.json file.
        """
        self._path = path
        self._data: dict[str, Any] = {"sensors": {}}

    def load(self) -> None:
        """Load the configured sensor map without injecting additional identities."""
        self._data = {"sensors": {}}
        file_path = Path(self._path)
        if not file_path.exists():
            logger.warning("Sensor map file not found: %s", self._path)
        else:
            try:
                with file_path.open("r", encoding="utf-8") as source:
                    loaded = json.load(source)
                if isinstance(loaded, dict):
                    self._data = loaded
                else:
                    logger.warning("Sensor map root must be an object: %s", self._path)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load sensor map %s: %s", self._path, exc)
        sensors = self._data.get("sensors")
        if not isinstance(sensors, dict):
            self._data["sensors"] = {}

    def lookup(self, table: str, key: str) -> dict | None:
        """Look up sensor config by table:key.

        Args:
            table: Table name (e.g., "tbl_actual").
            key: Sensor key (e.g., "Temp").

        Returns:
            Sensor config dict or None if not found.
        """
        lookup_key = f"{table}:{key}"
        sensors = self._data.get("sensors", {})
        metadata = sensors.get(lookup_key)
        return metadata if isinstance(metadata, dict) else None

    def sensor_count(self) -> int:
        """Return number of sensors in the map.

        Returns:
            Number of sensors loaded.
        """
        return len(self._data.get("sensors", {}))

    def iter_sensors(self) -> list[tuple[str, str, dict[str, Any]]]:
        sensors = self._data.get("sensors", {})
        if not isinstance(sensors, dict):
            return []

        out: list[tuple[str, str, dict[str, Any]]] = []
        for lookup_key, metadata in sensors.items():
            if not isinstance(lookup_key, str) or not isinstance(metadata, dict):
                continue
            if ":" not in lookup_key:
                continue
            table, key = lookup_key.split(":", 1)
            if not table or not key:
                continue
            out.append((table, key, metadata))
        return out
