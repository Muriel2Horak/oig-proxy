"""Control Pipeline – control setting flow orchestration.

Manages the control setting pipeline for OIG Box settings, including
queue management, transaction tracking, and MQTT event publishing.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# pylint: disable=too-many-instance-attributes
class ControlPipeline:
    """Manages control setting flow orchestration for OIG Box."""

    def __init__(self, _proxy: Any) -> None:
        """Initialize the control pipeline.

        Args:
            _proxy: The OIG proxy instance.
        """
        self._proxy = _proxy
        self.session_id: str = uuid.uuid4().hex
        self.mqtt_enabled: bool = False
        self.qos: int = 1
        self.queue: list[dict[str, Any]] = []
        self.inflight: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self.log_path: str | None = None

    @staticmethod
    def format_tx(tx: dict[str, Any] | None) -> str:
        """Format a transaction for logging.

        Args:
            tx: Transaction dictionary with tbl_name, tbl_item, new_value, etc.

        Returns:
            Formatted string representation of the transaction.
        """
        if not tx:
            return ""
        tbl = str(tx.get("tbl_name") or "")
        item = str(tx.get("tbl_item") or "")
        val = str(tx.get("new_value") or "")
        stage = str(tx.get("stage") or "")
        attempts = tx.get("_attempts")
        tx_id = str(tx.get("tx_id") or "")
        if attempts is None:
            return f"{tbl}/{item}={val} ({stage}) tx={tx_id}".strip()
        return f"{tbl}/{item}={val} ({stage} {attempts}) tx={tx_id}".strip()

    @staticmethod
    def format_result(result: dict[str, Any] | None) -> str:
        """Format a result for logging.

        Args:
            result: Result dictionary with status, tbl_name, tbl_item, etc.

        Returns:
            Formatted string representation of the result.
        """
        if not result:
            return ""
        status = str(result.get("status") or "")
        tbl = str(result.get("tbl_name") or "")
        item = str(result.get("tbl_item") or "")
        val = str(result.get("new_value") or "")
        err = result.get("error")
        tx_id = str(result.get("tx_id") or "")
        if err:
            return f"{status} {tbl}/{item}={val} err={err} tx={tx_id}".strip()
        return f"{status} {tbl}/{item}={val} tx={tx_id}".strip()

    @staticmethod
    def build_request_key(tbl_name: str, tbl_item: str, canon_value: str) -> str:
        """Build a request key for deduplication.

        Args:
            tbl_name: Table name.
            tbl_item: Item/parameter name.
            canon_value: Canonical value.

        Returns:
            Composite key in format "tbl_name/tbl_item/canon_value".
        """
        return f"{tbl_name}/{tbl_item}/{canon_value}"

    @staticmethod
    def result_key_state(result: str | None, sub_result: str | None) -> str | None:
        """Determine the state key based on result and sub-result.

        Args:
            result: Primary result status.
            sub_result: Secondary result status.

        Returns:
            State key string or None if no state change.
        """
        if result == "accepted":
            return "queued"
        if result == "completed" and sub_result == "noop_already_set":
            return None
        return None

    def normalize_value(
        self, tbl_name: str, tbl_item: str, new_value: Any
    ) -> tuple[str | None, str]:
        """Normalize setting values for specific parameters.

        Handles special value formatting requirements for certain OIG Box
        parameters like MODE and AAC_MAX_CHRG.

        Args:
            tbl_name: Table name.
            tbl_item: Parameter name.
            new_value: New value to normalize.

        Returns:
            Tuple of (normalized_value, state).
        """
        # Simple normalization - for MODE in tbl_box_prms, accept values 0-5
        if tbl_name == "tbl_box_prms" and tbl_item == "MODE":
            try:
                mode_int = int(new_value)
                if 0 <= mode_int <= 5:
                    canon = str(mode_int)
                    return (canon, canon)
            except (ValueError, TypeError):
                pass
            return (None, "bad_value")
        # For AAC_MAX_CHRG, add .0 suffix
        if tbl_name == "tbl_invertor_prm1" and tbl_item == "AAC_MAX_CHRG":
            try:
                val = float(new_value)
                return (f"{val:.1f}", f"{val:.1f}")
            except (ValueError, TypeError):
                return (None, "bad_value")
        return (str(new_value), str(new_value))

    # pylint: disable=too-many-return-statements
    @staticmethod
    def coerce_value(value: Any) -> Any:
        """Coerce a value to appropriate Python type.

        Attempts to convert string values to int, float, or bool
        based on their format.

        Args:
            value: Value to coerce.

        Returns:
            Coerced value in appropriate Python type.
        """
        if value is None or isinstance(value, (int, float, bool)):
            return value
        text = str(value).strip()
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        if re.fullmatch(r"-?\d+", text):
            try:
                return int(text)
            except ValueError:
                return value
        if re.fullmatch(r"-?\d+\.\d+", text):
            try:
                return float(text)
            except ValueError:
                return value
        return value

    async def publish_restart_errors(self) -> None:
        """Publish restart errors to MQTT.

        Placeholder for future implementation.
        """
        return

    async def note_box_disconnect(self) -> None:
        """Handle box disconnection event.

        Placeholder for future implementation.
        """
        return

    async def observe_box_frame(
        self,
        _parsed: dict[str, Any],
        _table_name: str | None,
        _frame: str,
    ) -> None:
        """Observe and process box frames.

        Placeholder for future implementation.

        Args:
            _parsed: Parsed frame data.
            _table_name: Table name.
            _frame: Raw frame string.
        """
        return

    async def maybe_start_next(self) -> None:
        """Start next transaction in queue.

        Placeholder for future implementation.
        """
        return

    # pylint: disable=too-many-arguments
    async def publish_setting_event_state(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: Any,
        device_id: str | None,
        source: str,
    ) -> None:
        """Publish setting event state to MQTT.

        Args:
            tbl_name: Table name.
            tbl_item: Parameter name.
            new_value: New value.
            device_id: Device identifier.
            source: Event source.
        """
        if not device_id:
            return
        proxy = self._proxy
        if not hasattr(proxy, "mqtt_publisher") or not proxy.mqtt_publisher:
            return
        mqtt = proxy.mqtt_publisher
        topic = f"oig_local/{device_id}/{tbl_name}/state"
        payload = {tbl_item: new_value}
        try:
            await mqtt.publish_raw(
                topic=topic,
                payload=json.dumps(payload),
                qos=self.qos,
                retain=True,
            )
        except Exception: # pylint: disable=broad-exception-caught
            logger.debug("Failed to publish control state to MQTT: %s", Exception)

    async def on_box_setting_ack(self, *, tx_id: str | None, ack: bool) -> None:
        """Handle box setting acknowledgment.

        Placeholder for future implementation.

        Args:
            tx_id: Transaction ID.
            ack: Whether the setting was acknowledged.
        """
        _ = (tx_id, ack)
        return

    def append_to_log(self, entry: str) -> None:
        """Append entry to log file.

        Args:
            entry: Log entry to append.
        """
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except OSError:
            pass
