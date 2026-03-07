from __future__ import annotations

import re
import uuid
from typing import Any


class ControlPipeline:
    def __init__(self, _proxy: Any) -> None:
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
        return f"{tbl_name}/{tbl_item}/{canon_value}"

    @staticmethod
    def result_key_state(result: str | None, sub_result: str | None) -> str | None:
        if result == "accepted":
            return "queued"
        if result == "completed" and sub_result == "noop_already_set":
            return None
        return None

    def normalize_value(
        self, tbl_name: str, tbl_item: str, new_value: Any
    ) -> tuple[str | None, str]:
        # Simple normalization - for MODE in tbl_box_prms, must be "3"
        if tbl_name == "tbl_box_prms" and tbl_item == "MODE":
            if new_value == "3":
                return ("3", "3")
            return (None, "bad_value")
        # For AAC_MAX_CHRG, add .0 suffix
        if tbl_name == "tbl_invertor_prm1" and tbl_item == "AAC_MAX_CHRG":
            try:
                val = float(new_value)
                return (f"{val:.1f}", f"{val:.1f}")
            except (ValueError, TypeError):
                return (None, "bad_value")
        return (str(new_value), str(new_value))

    @staticmethod
    def coerce_value(value: Any) -> Any:
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
        return

    async def note_box_disconnect(self) -> None:
        return

    async def observe_box_frame(
        self,
        _parsed: dict[str, Any],
        _table_name: str | None,
        _frame: str,
    ) -> None:
        return

    async def maybe_start_next(self) -> None:
        return

    async def publish_setting_event_state(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: Any,
        device_id: str | None,
        source: str,
    ) -> None:
        if not device_id:
            return
        proxy = self._proxy
        if not hasattr(proxy, "mqtt_publisher") or not proxy.mqtt_publisher:
            return
        mqtt = proxy.mqtt_publisher
        topic = f"oig_local/{device_id}/{tbl_name}/state"
        payload = {tbl_item: new_value}
        import json
        try:
            await mqtt.publish_raw(
                topic=topic,
                payload=json.dumps(payload),
                qos=self.qos,
                retain=True,
            )
        except Exception:
            pass

    async def on_box_setting_ack(self, *, tx_id: str | None, ack: bool) -> None:
        _ = (tx_id, ack)
        return

    def append_to_log(self, entry: str) -> None:
        if not self.log_path:
            return
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry)
