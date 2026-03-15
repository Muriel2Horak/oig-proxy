from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..mqtt.client import MQTTClient
    from .state import TwinQueue, TwinSetting


logger = logging.getLogger(__name__)


class TwinDelivery:
    def __init__(self, twin_queue: TwinQueue, mqtt: MQTTClient, inflight_timeout_s: float = 30.0) -> None:
        self._twin_queue = twin_queue
        self._mqtt = mqtt
        self._inflight_key: tuple[str, str] | None = None
        self._inflight_device_id: str | None = None
        self._inflight_since: float | None = None
        self._inflight_timeout_s = inflight_timeout_s

    async def deliver_pending(self, device_id: str) -> list[TwinSetting]:
        if self._inflight_key is not None:
            if self._inflight_since is not None:
                elapsed = time.monotonic() - self._inflight_since
                if elapsed >= self._inflight_timeout_s:
                    logger.warning(
                        "TwinDelivery: inflight timeout for %s:%s after %.1fs, dropping",
                        self._inflight_key[0],
                        self._inflight_key[1],
                        elapsed,
                    )
                    self._twin_queue.acknowledge(self._inflight_key[0], self._inflight_key[1])
                    self._inflight_key = None
                    self._inflight_device_id = None
                    self._inflight_since = None

            if self._inflight_key is not None:
                logger.debug(
                    "TwinDelivery: skip deliver, inflight=%s:%s",
                    self._inflight_key[0],
                    self._inflight_key[1],
                )
                return []

        pending = self._twin_queue.get_pending()
        if not pending:
            return []

        setting = pending[0]
        self._inflight_key = (setting.table, setting.key)
        self._inflight_device_id = device_id
        self._inflight_since = time.monotonic()
        logger.info(
            "TwinDelivery: deliver %s:%s=%s (device=%s)",
            setting.table,
            setting.key,
            setting.value,
            device_id,
        )
        return [setting]

    def acknowledge(self, table: str, key: str) -> None:
        removed = self._twin_queue.acknowledge(table, key)
        if removed:
            logger.info("TwinDelivery: acknowledged %s:%s", table, key)
        if self._inflight_key == (table, key):
            self._inflight_key = None
            self._inflight_device_id = None
            self._inflight_since = None

    def inflight(self) -> tuple[str, str] | None:
        return self._inflight_key

    @staticmethod
    def build_setting_xml(table: str, key: str, value: object) -> str:
        return f"<TblName>{table}</TblName><{key}>{value}</{key}>"
