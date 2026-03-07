from __future__ import annotations

import re
import uuid
from typing import Any


class ControlPipeline:
    def __init__(self, _proxy: Any) -> None:
        self.session_id: str = uuid.uuid4().hex
        self.mqtt_enabled: bool = False
        self.qos: int = 1
        self.queue: list[dict[str, Any]] = []
        self.inflight: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None

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
        _ = (tbl_name, tbl_item, new_value, device_id, source)
        return

    async def on_box_setting_ack(self, *, tx_id: str | None, ack: bool) -> None:
        _ = (tx_id, ack)
        return
