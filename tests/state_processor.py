"""State message processing components for MQTT messages.

Extracts and transforms state values for cleaner code.
"""
# pylint: disable=unnecessary-pass,too-few-public-methods,broad-exception-caught

import json
import logging
from abc import ABC, abstractmethod
from typing import Any


logger = logging.getLogger(__name__)


class ValueTransformer(ABC):
    """Transforms MQTT state values to raw format."""

    @abstractmethod
    def transform(self, tbl_name: str, tbl_item: str, value: Any) -> str:
        """Transform value to raw format."""
        pass


class StatePersistence(ABC):
    """Persists PRMS table state."""

    @abstractmethod
    def save(self, table_name: str, device_id: str, values: dict) -> None:
        """Save PRMS values."""
        pass

    @abstractmethod
    def load(self, table_name: str, device_id: str) -> dict:
        """Load PRMS values."""
        pass


class StateMessageProcessor:
    """Processes state messages with value transformation and persistence."""

    def __init__(
        self,
        transformer: ValueTransformer,
        persistence: StatePersistence,
    ):
        """Initialize state message processor."""
        self.transformer = transformer
        self.persistence = persistence

    def process(
        self,
        topic: str,
        payload_text: str,
        device_id: str,
        table_name: str,
    ) -> dict[str, Any]:
        """Process MQTT state message with transformation and persistence."""
        logger.debug("Processing state message: %s", topic)

        try:
            payload = json.loads(payload_text)
        except Exception as e:
            logger.error("Failed to parse JSON payload: %s", e)
            return {"status": "error", "error": f"json_parse_error:{type(e).__name__}"}

        if not device_id or device_id == "AUTO":
            return {"status": "error", "error": "device_id_unknown"}

        if not table_name or not table_name.startswith("tbl_"):
            return {"status": "error", "error": "invalid_table_name"}

        # Extract and transform values
        raw_values = {}
        for key, value in payload.items():
            if key.startswith("_"):
                continue

            parsed = self._parse_mqtt_state_topic(key)
            if not parsed:
                continue

            tbl_name, tbl_item = parsed

            raw_value = self.transformer.transform(tbl_name, tbl_item, value)

            if raw_value is not None:
                raw_values[tbl_item] = raw_value

        # Save to persistence
        try:
            self.persistence.save(table_name, device_id, raw_values)
        except Exception as e:
            logger.error("Failed to persist state: %s", e)

        # Return success response
        return {"status": "ok", "saved": list(raw_values.keys())}

    def _parse_mqtt_state_topic(self, topic: str) -> tuple[str, str] | None:
        """Parse MQTT topic to extract table name and item."""
        if not topic:
            return None
        if "/" not in topic:
            return "tbl_box_prms", topic
        if not topic.startswith("tele/"):
            parts = topic.split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]
            return None

        parts = topic.split("/")
        if len(parts) < 3:
            return None

        return parts[1], parts[2]
