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
        """Load sensor map from JSON file.

        If file doesn't exist, logs a warning and uses empty sensor map.
        """
        file_path = Path(self._path)
        if not file_path.exists():
            logger.warning("Sensor map file not found: %s", self._path)
            self._data = {"sensors": {}}
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse sensor map JSON: %s", e)
            self._data = {"sensors": {}}
        except OSError as e:
            logger.error("Failed to read sensor map file: %s", e)
            self._data = {"sensors": {}}

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
        return sensors.get(lookup_key)

    def get_warnings(self, table: str, key: str) -> list[dict]:
        """Get warnings_3f list for given table:key field.

        Args:
            table: Table name.
            key: Sensor key.

        Returns:
            List of warning dicts, empty list if none.
        """
        sensor = self.lookup(table, key)
        if sensor is None:
            return []
        return sensor.get("warnings_3f", [])

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
