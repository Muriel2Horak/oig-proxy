"""ControlSettings – odesílání nastavení do BOXu a zpracování ACK.

Zapouzdřuje Control API endpointy (health, send_setting),
sestavování control framů, odesílání přes TCP a detekci
Setting eventů z tbl_events.
"""

# pylint: disable=too-many-instance-attributes,protected-access
# pylint: disable=missing-function-docstring,too-many-return-statements
# pylint: disable=too-many-arguments,too-many-positional-arguments,broad-exception-caught

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from oig_frame import build_frame
from twin_state import QueueSettingDTO
from twin_transaction import generate_tx_id

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)


class ControlSettings:
    """Správa odesílání nastavení do BOXu a zpracování ACK odpovědí."""

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy
        self.set_commands_buffer: list[dict[str, str]] = []

    # -----------------------------------------------------------------
    # Static helpers
    # -----------------------------------------------------------------

    @staticmethod
    def parse_setting_event(
            content: str) -> tuple[str, str, str | None, str | None] | None:
        # Example:
        #   "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]"
        m = re.search(
            r"tbl_([a-z0-9_]+)\s*/\s*([A-Z0-9_]+):\s*\[([^\]]*)\]\s*->\s*\[([^\]]*)\]",
            content,
        )
        if not m:
            return None
        tbl_name = f"tbl_{m.group(1)}"
        return tbl_name, m.group(2), m.group(3), m.group(4)

    # -----------------------------------------------------------------
    # Setting event handling
    # -----------------------------------------------------------------

    async def handle_setting_event(
        self,
        parsed: dict[str, Any],
        table_name: str | None,
        device_id: str | None,
    ) -> None:
        if table_name != "tbl_events":
            return
        if not parsed or parsed.get("Type") != "Setting":
            return
        content = parsed.get("Content")
        if not content:
            return
        ev = self.parse_setting_event(str(content))
        if not ev:
            return
        tbl_name, tbl_item, _old_value, new_value = ev
        if new_value is None:
            return
        # Record for telemetry (cloud or local applied setting)
        self.set_commands_buffer.append({
            "key": f"{tbl_name}:{tbl_item}",
            "value": str(new_value),
            "result": "applied",
            "source": "tbl_events",
        })

        # Publish the setting event state to MQTT
        await self._proxy._ctrl.publish_setting_event_state(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=str(new_value),
            device_id=device_id,
            source="tbl_events",
        )

    # -----------------------------------------------------------------
    # Control API endpoints
    # -----------------------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        """Vrátí stavové info pro Control API health endpoint."""
        proxy = self._proxy
        now = time.time()
        last_age_s: float | None = None
        if proxy._last_data_epoch is not None:
            last_age_s = max(0.0, now - proxy._last_data_epoch)
        return {
            "ok": True,
            "device_id": None if proxy.device_id == "AUTO" else proxy.device_id,
            "box_connected": bool(proxy.box_connected),
            "box_peer": proxy._active_box_peer,
            "box_data_age_s": last_age_s,
        }

    def send_setting(
            self,
            *,
            tbl_name: str,
            tbl_item: str,
            new_value: str,
            confirm: str = "New",
    ) -> dict[str, Any]:
        """Odešle Setting do BOXu přes event loop a vrátí výsledek."""
        if not self.validate_loop_ready():
            return {"ok": False, "error": "event_loop_not_ready"}

        return self.send_via_event_loop(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=new_value,
            confirm=confirm,
        )

    def validate_loop_ready(self) -> bool:
        """Ověří že event loop je připraven."""
        return self._proxy._loop is not None

    def send_via_event_loop(
            self,
            *,
            tbl_name: str,
            tbl_item: str,
            new_value: str,
            confirm: str,
    ) -> dict[str, Any]:
        """Pošle setting přes event loop s timeoutem."""
        validation = self.validate_parameters(
            tbl_name, tbl_item, new_value
        )
        if not validation["ok"]:
            return validation

        return self.run_coroutine_threadsafe(tbl_name, tbl_item, new_value, confirm)

    def validate_parameters(
            self,
            tbl_name: str,  # noqa: ARG002 - parameter reserved for future use
            tbl_item: str,  # noqa: ARG002 - parameter reserved for future use
            new_value: str,  # noqa: ARG002 - parameter reserved for future use
    ) -> dict[str, Any]:
        """Validace parametrů pro control."""
        proxy = self._proxy
        # In OFFLINE mode, BOX doesn't send data continuously but can still receive commands
        if proxy.device_id == "AUTO":
            return {"ok": False, "error": "device_id_unknown"}
        return {"ok": True}

    # -----------------------------------------------------------------
    # Frame building
    # -----------------------------------------------------------------

    def build_frame(
            self,
            tbl_name: str,
            tbl_item: str,
            new_value: str,
            confirm: str,
    ) -> bytes:
        """Sestaví rámec pro nastavení."""
        proxy = self._proxy
        msg_id = secrets.randbelow(90_000_000) + 10_000_000
        id_set = int(time.time())
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)

        inner = (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{proxy.device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{now_local.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
            f"<NewValue>{new_value}</NewValue>"
            f"<Confirm>{confirm}</Confirm>"
            f"<TblName>{tbl_name}</TblName>"
            f"<TblItem>{tbl_item}</TblItem>"
            "<ID_Server>5</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
            "<ver>55734</ver>"
        )
        return build_frame(
            inner,
            add_crlf=True).encode(
            "utf-8",
            errors="strict")

    # -----------------------------------------------------------------
    # Coroutine dispatch
    # -----------------------------------------------------------------

    def run_coroutine_threadsafe(
            self, tbl_name: str, tbl_item: str, new_value: str, confirm: str
    ) -> dict[str, Any]:
        """Spustí coroutines threadsafe s timeoutem."""
        proxy = self._proxy
        loop = proxy._loop
        if loop is None:
            return {"ok": False, "error": "event_loop_not_ready"}
        fut = asyncio.run_coroutine_threadsafe(
            self.queue_setting(
                tbl_name=tbl_name,
                tbl_item=tbl_item,
                new_value=new_value,
                confirm=confirm,
            ),
            loop,
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as e:
            return {"ok": False, "error": f"send_failed:{type(e).__name__}"}

    async def queue_setting(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: str,
        confirm: str,
        tx_id: str | None = None,
    ) -> dict[str, Any]:
        if self._proxy.device_id == "AUTO":
            return {"ok": False, "error": "device_id_unknown"}
        twin = self._proxy._twin
        if twin is None:
            return {"ok": False, "error": "twin_unavailable"}

        resolved_tx_id = tx_id or generate_tx_id()
        dto = QueueSettingDTO(
            tx_id=resolved_tx_id,
            conn_id=0,
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=new_value,
            confirm=confirm,
        )
        result = await twin.queue_setting(dto)
        logger.info(
            "CONTROL: Queued via Twin %s/%s=%s tx_id=%s",
            tbl_name,
            tbl_item,
            new_value,
            result.tx_id,
        )
        return {
            "ok": True,
            "queued": True,
            "tx_id": result.tx_id,
            "status": result.status,
            "device_id": self._proxy.device_id,
        }
