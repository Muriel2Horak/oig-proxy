"""Mode & PRMS persistence – ukládá a obnovuje MODE a tabulkový stav."""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from utils import load_mode_state, load_prms_state, save_mode_state

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)


class ModePersistence:
    """Manages MODE value tracking and PRMS table persistence/publish."""

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy
        loaded_mode, loaded_dev = load_mode_state()
        self.mode_value: int | None = loaded_mode
        self.mode_device_id: str | None = loaded_dev
        self.mode_pending_publish: bool = self.mode_value is not None
        self.prms_tables: dict[str, dict[str, Any]]
        self.prms_device_id: str | None
        self.prms_tables, self.prms_device_id = load_prms_state()
        self.prms_pending_publish: bool = bool(self.prms_tables)

    # ------------------------------------------------------------------
    # MODE publish
    # ------------------------------------------------------------------

    async def publish_mode_if_ready(
        self,
        device_id: str | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        """Publikuje známý MODE do MQTT."""
        if self.mode_value is None:
            return
        target_device_id = device_id
        if not target_device_id:
            if (self._proxy.device_id
                    and self._proxy.device_id != "AUTO"):
                target_device_id = self._proxy.device_id
            elif self.mode_device_id:
                target_device_id = self.mode_device_id
        if not target_device_id:
            logger.debug("MODE: No device_id, publish deferred")
            return

        payload: dict[str, Any] = {
            "_table": "tbl_box_prms",
            "MODE": int(self.mode_value),
        }
        payload["_device_id"] = target_device_id

        try:
            await self._proxy.mqtt_publisher.publish_data(payload)
            if reason:
                logger.info(
                    "MODE: Published state %s (%s)",
                    self.mode_value,
                    reason,
                )
        except Exception as exc:
            logger.debug("MODE publish failed: %s", exc)

    # ------------------------------------------------------------------
    # PRMS table state
    # ------------------------------------------------------------------

    def maybe_persist_table_state(
        self,
        _parsed: dict[str, Any] | None,
        _table_name: str | None,
        _device_id: str | None,
    ) -> None:
        """Uloží poslední známé hodnoty pro vybrané tabulky (stub)."""

    async def publish_prms_if_ready(
        self, *, reason: str | None = None,
    ) -> None:
        """Publikuje uložené *_prms hodnoty do MQTT."""
        if not self.prms_tables:
            return

        if not self._proxy.mqtt_publisher.is_ready():
            self.prms_pending_publish = True
            return

        if self._proxy.device_id == "AUTO":
            self.prms_pending_publish = True
            return

        # Publish jen když je potřeba (startup nebo po MQTT reconnectu)
        if not self.prms_pending_publish and reason not in (
                "startup", "device_autodetect"):
            return

        for table_name, values in self.prms_tables.items():
            if not isinstance(values, dict) or not values:
                continue
            payload: dict[str, Any] = {"_table": table_name, **values}
            try:
                await self._proxy.mqtt_publisher.publish_data(payload)
            except Exception as exc:
                logger.debug(
                    "STATE publish failed (%s): %s", table_name, exc)
                self.prms_pending_publish = True
                return

        self.prms_pending_publish = False
        if reason:
            logger.info("STATE: Published snapshot (%s)", reason)

    # ------------------------------------------------------------------
    # MODE update & detection
    # ------------------------------------------------------------------

    async def handle_mode_update(
        self,
        new_mode: Any,
        device_id: str | None,
        source: str,
    ) -> None:
        """Uloží a publikuje MODE pokud máme nové info."""
        if new_mode is None:
            return
        try:
            mode_int = int(new_mode)
        except Exception:
            return
        if mode_int < 0 or mode_int > 5:
            logger.debug(
                "MODE: Value %s out of range 0-5, source %s, ignoring",
                mode_int,
                source,
            )
            return

        if mode_int != self.mode_value:
            self.mode_value = mode_int
            await asyncio.to_thread(
                save_mode_state,
                mode_int,
                device_id or self._proxy.device_id or self.mode_device_id,
            )
            logger.info("MODE: %s → %s", source, mode_int)
        if device_id:
            self.mode_device_id = device_id

        await self.publish_mode_if_ready(device_id, reason=source)

    async def maybe_process_mode(
        self,
        parsed: dict[str, Any],
        table_name: str | None,
        device_id: str | None,
    ) -> None:
        """Detekuje MODE ze známých zdrojů a zajistí publish + persist."""
        if not parsed:
            return

        if table_name == "tbl_box_prms" and "MODE" in parsed:
            await self.handle_mode_update(
                parsed.get("MODE"), device_id, "tbl_box_prms")
            return

        if table_name == "tbl_events":
            content = parsed.get("Content")
            if content:
                new_mode = self._proxy.parser.parse_mode_from_event(
                    str(content))
                if new_mode is not None:
                    await self.handle_mode_update(
                        new_mode, device_id, "tbl_events")
