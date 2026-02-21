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

from oig_frame import build_frame, build_end_time_frame

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)


class ControlSettings:
    """Správa odesílání nastavení do BOXu a zpracování ACK odpovědí."""

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy
        self.pending: dict[str, Any] | None = None
        self.pending_frame: bytes | None = None
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
        await self._proxy._ctrl.publish_setting_event_state(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            new_value=new_value,
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
        fut = asyncio.run_coroutine_threadsafe(
            self.send_to_box(
                tbl_name=tbl_name,
                tbl_item=tbl_item,
                new_value=new_value,
                confirm=confirm,
            ),
            proxy._loop if proxy._loop else None,  # type: ignore[arg-type]
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as e:
            return {"ok": False, "error": f"send_failed:{type(e).__name__}"}

    # -----------------------------------------------------------------
    # Send setting to BOX
    # -----------------------------------------------------------------

    async def send_to_box(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: str,
        confirm: str,
        tx_id: str | None = None,
    ) -> dict[str, Any]:
        proxy = self._proxy

        if proxy.device_id == "AUTO":
            return {"ok": False, "error": "device_id_unknown"}

        # Check TCP connection (box_connected flag doesn't work in OFFLINE mode)
        async with proxy._box_conn_lock:
            writer = proxy._active_box_writer
        if writer is None:
            return {"ok": False, "error": "no_active_box_writer"}

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
        frame = build_frame(
            inner,
            add_crlf=True).encode(
            "utf-8",
            errors="strict")

        # BOX accepts Settings as responses to any poll type (IsNewSet, IsNewFW, IsNewWeather) (protocol
        # requirement).  Queue the frame for delivery on the next poll
        # instead of writing directly to the socket — applies to ALL modes
        # (OFFLINE, ONLINE, HYBRID).
        self.pending = {
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": new_value,
            "id": msg_id,
            "id_set": id_set,
            "tx_id": tx_id,
        }
        self.pending_frame = frame
        logger.info(
            "CONTROL: Queued Setting %s/%s=%s for next poll "
            "(id=%s id_set=%s)",
            tbl_name, tbl_item, new_value, msg_id, id_set,
        )
        return {
            "ok": True,
            "sent": True,
            "device_id": proxy.device_id,
            "id": msg_id,
            "id_set": id_set,
        }

    # -----------------------------------------------------------------
    # ACK handling
    # -----------------------------------------------------------------

    def maybe_handle_ack(  # pylint: disable=too-many-locals
        self, frame: str, box_writer: asyncio.StreamWriter, *, conn_id: int
    ) -> bool:
        pending = self.pending
        if not pending:
            return False

        # Validate conn_id ownership - only the connection that delivered the Setting
        # should process the ACK/NACK response
        delivered_conn_id = pending.get("delivered_conn_id")
        if delivered_conn_id is not None and conn_id != delivered_conn_id:
            logger.debug(
                "CONTROL: ACK/NACK ignored — conn_id mismatch "
                "(delivered_conn=%s, current_conn=%s, %s/%s)",
                delivered_conn_id,
                conn_id,
                pending.get("tbl_name"),
                pending.get("tbl_item"),
            )
            if hasattr(self._proxy, "_tc"):
                self._proxy._tc.record_conn_mismatch()
            return False

        sent_at = pending.get("sent_at")
        if sent_at is None:
            # Setting queued but not yet delivered to BOX via IsNewSet
            return False
        elapsed = time.monotonic() - float(sent_at)
        if elapsed > self._proxy._ctrl.ack_timeout_s:
            logger.info(
                "CONTROL: ACK check skipped — timeout exceeded "
                "(%.1fs > %.1fs, conn=%s, %s/%s)",
                elapsed,
                self._proxy._ctrl.ack_timeout_s,
                conn_id,
                pending.get("tbl_name"),
                pending.get("tbl_item"),
            )
            return False

        has_reason = "<Reason>Setting</Reason>" in frame
        has_ack = "<Result>ACK</Result>" in frame
        has_nack = "<Result>NACK</Result>" in frame

        if has_reason and (has_ack or has_nack):
            logger.info(
                "CONTROL: BOX Setting %s detected (conn=%s, elapsed=%.1fs)",
                "ACK" if has_ack else "NACK",
                conn_id,
                elapsed,
            )
        elif has_reason or has_ack or has_nack:
            # Partial match — log for debugging
            logger.debug(
                "CONTROL: ACK partial match — Reason=%s ACK=%s NACK=%s "
                "(conn=%s, frame=%.200s)",
                has_reason, has_ack, has_nack,
                conn_id, frame,
            )
            return False
        else:
            return False

        ack_ok = has_ack
        tx_id = pending.get("tx_id")

        end_frame = build_end_time_frame()

        box_writer.write(end_frame)
        try:
            task = asyncio.create_task(box_writer.drain())
            self._proxy._background_tasks.add(task)
            task.add_done_callback(self._proxy._background_tasks.discard)
        except Exception as exc:
            logger.debug("CONTROL: Failed to schedule END drain: %s", exc)

        try:
            task = asyncio.create_task(
                self._proxy._ctrl.on_box_setting_ack(
                    tx_id=str(tx_id) if tx_id else None,
                    ack=ack_ok,
                )
            )
            self._proxy._background_tasks.add(task)
            task.add_done_callback(self._proxy._background_tasks.discard)
        except Exception as exc:
            logger.debug("CONTROL: Failed to schedule ACK handling: %s", exc)

        logger.info(
            "CONTROL: BOX responded to local Setting (sent END), "
            "last=%s/%s=%s (conn=%s)",
            pending.get("tbl_name"),
            pending.get("tbl_item"),
            pending.get("new_value"),
            conn_id,
        )
        # Record for telemetry (local control command)
        self.set_commands_buffer.append({
            "key": f"{pending.get('tbl_name')}:{pending.get('tbl_item')}",
            "value": str(pending.get("new_value", "")),
            "result": "ack" if ack_ok else "nack",
            "source": "local",
        })
        self.pending = None
        return True

    # -----------------------------------------------------------------
    # Disconnect cleanup
    # -----------------------------------------------------------------

    def clear_pending_on_disconnect(self) -> None:
        """Clear pending state when BOX disconnects.

        Only clears Settings that were already DELIVERED to the BOX in this
        session (sent_at is set). This prevents cross-session ACK confusion
        where a new connection would process an ACK meant for the old one.

        Undelivered Settings (pending_frame still set, sent_at not set) are
        intentionally kept — the BOX never received them so there is zero
        cross-session risk and they should be delivered on the next connection.
        """
        if self.pending is None and self.pending_frame is None:
            return

        sent_at = self.pending.get("sent_at") if self.pending else None

        if sent_at is not None:
            # Delivered but ACK not yet received — clear to avoid cross-session ACK.
            logger.info(
                "CONTROL: Clearing delivered-but-unacked Setting on BOX disconnect "
                "(tbl=%s/%s, delivered_conn=%s)",
                self.pending.get("tbl_name") if self.pending else "?",
                self.pending.get("tbl_item") if self.pending else "?",
                self.pending.get("delivered_conn_id") if self.pending else "?",
            )
            self.pending = None
            self.pending_frame = None
        else:
            # Not yet delivered — keep for the next connection.
            logger.info(
                "CONTROL: Keeping undelivered Setting across BOX disconnect "
                "(tbl=%s/%s) — will deliver on next poll",
                self.pending.get("tbl_name") if self.pending else "?",
                self.pending.get("tbl_item") if self.pending else "?",
            )
