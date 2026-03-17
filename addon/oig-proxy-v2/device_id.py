#!/usr/bin/env python3
"""Device ID persistence manager for OIG Proxy v2."""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def iso_now() -> str:
    """Return current time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class DeviceIdManager:
    """Manages device ID persistence and validation."""

    def __init__(self, path: str = "/data/device_id.json") -> None:
        self._path = path
        self._device_id: str | None = None

    def load(self) -> str | None:
        """Load saved device_id from file.

        Returns:
            The saved device_id string, or None if not found/valid.
        """
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._device_id = data.get("device_id")
                    if self._device_id:
                        logger.info("Device ID loaded: %s", self._device_id)
                        return self._device_id
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load device_id from %s: %s", self._path, exc)
        return None

    def save(self, device_id: str) -> None:
        """Save device_id to file with timestamp.

        Args:
            device_id: The device_id to save.
        """
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({
                    "device_id": device_id,
                    "first_seen": iso_now()
                }, f, ensure_ascii=False)
            self._device_id = device_id
            logger.info("Device ID saved: %s", device_id)
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Failed to save device_id to %s: %s", self._path, exc)

    def validate(self, device_id: str) -> bool:
        """Validate that device_id matches the saved value.

        Args:
            device_id: The device_id to validate.

        Returns:
            True if device_id matches saved value, False otherwise.
        """
        if self._device_id is None:
            return False
        return self._device_id == device_id

    @property
    def device_id(self) -> str | None:
        """Return the current device_id (loaded or set)."""
        return self._device_id