#!/usr/bin/env python3
"""Device ID persistence manager for OIG Proxy v2."""

import json
import logging
import os
import re
import tempfile
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
        self._device_id = None
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    device_id = data.get("device_id")
                    if self.is_safe(device_id):
                        self._device_id = device_id
                        logger.info("Device ID loaded: %s", device_id)
                        return device_id
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load device_id from %s: %s", self._path, exc)
        return None

    def save(self, device_id: str) -> bool:
        """Save device_id to file with timestamp.

        Args:
            device_id: The device_id to save.
        """
        if not self.is_safe(device_id):
            logger.error("Refusing unsafe device ID")
            return False
        directory = os.path.dirname(self._path) or "."
        temporary_path: str | None = None
        try:
            os.makedirs(directory, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=".device_id-",
                suffix=".tmp",
                dir=directory,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as file_handle:
                json.dump(
                    {"device_id": device_id, "first_seen": iso_now()},
                    file_handle,
                    ensure_ascii=False,
                )
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
            self._device_id = device_id
            logger.info("Device ID saved: %s", device_id)
            return True
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Failed to save device_id to %s: %s", self._path, exc)
            return False
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    @staticmethod
    def is_safe(device_id: object) -> bool:
        """Return whether an identity is safe for files, topics, and exact binding."""
        return (
            type(device_id) is str
            and device_id.casefold() != "unknown"
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", device_id)
            is not None
        )

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
