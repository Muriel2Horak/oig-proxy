from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..mqtt.client import MQTTClient
    from .state import TwinQueue, TwinSetting


logger = logging.getLogger(__name__)


class TwinDelivery:
    """Manages delivery of pending settings to BOX via proxy.
    
    Session-level tracking ensures only one setting is in-flight per TCP session.
    Cloud-initiated settings take priority over local queue.
    """
    
    def __init__(self, twin_queue: TwinQueue, mqtt: MQTTClient, inflight_timeout_s: float = 60.0) -> None:
        self._twin_queue = twin_queue
        self._mqtt = mqtt
        self._inflight_timeout_s = inflight_timeout_s
        
        # Cloud-initiated setting tracking
        self._cloud_inflight: bool = False
        
        # Session-level inflight tracking: session_id -> (table, key, since)
        self._session_inflight: dict[str, tuple[str, str, float]] = {}
        
        # Global inflight for backward compatibility
        self._inflight_key: tuple[str, str] | None = None
        self._inflight_device_id: str | None = None
        self._inflight_since: float | None = None
        self._last_seen_id_set: int | None = None
        self._last_msg_id: int | None = None

    def observe_id_set(self, id_set: int | None) -> None:
        if id_set is None:
            return
        if self._last_seen_id_set is None or id_set > self._last_seen_id_set:
            self._last_seen_id_set = id_set

    def observe_msg_id(self, msg_id: int | None) -> None:
        if msg_id is None:
            return
        if self._last_msg_id is None or msg_id > self._last_msg_id:
            self._last_msg_id = msg_id

    def next_id_set(self) -> int:
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        if self._last_seen_id_set is None or self._last_seen_id_set < now_epoch:
            self._last_seen_id_set = now_epoch
        self._last_seen_id_set += 1
        return self._last_seen_id_set

    def next_msg_id(self) -> int:
        if self._last_msg_id is None:
            self._last_msg_id = secrets.randbelow(1_000_000) + 13_000_000
            return self._last_msg_id
        self._last_msg_id += 1
        if self._last_msg_id > 13_999_999:
            self._last_msg_id = 13_000_000
        return self._last_msg_id

    async def deliver_pending(
        self, 
        device_id: str,
        session_id: str | None = None,
    ) -> list[TwinSetting]:
        """Deliver pending settings for device.
        
        Args:
            device_id: Device ID
            session_id: Unique session identifier for tracking (defaults to conn_id)
            
        Returns:
            List of settings to deliver (max 1 per session)
        """
        # Check global inflight timeout
        if self._inflight_key is not None and self._inflight_since is not None:
            elapsed = time.monotonic() - self._inflight_since
            if elapsed >= self._inflight_timeout_s:
                logger.warning(
                    "TwinDelivery: inflight timeout for %s:%s after %.1fs, dropping",
                    self._inflight_key[0],
                    self._inflight_key[1],
                    elapsed,
                )
                self._twin_queue.acknowledge(self._inflight_key[0], self._inflight_key[1])
                self._clear_global_inflight()

        # Session-level check
        if session_id is not None:
            session_inflight = self._session_inflight.get(session_id)
            if session_inflight is not None:
                table, key, since = session_inflight
                elapsed = time.monotonic() - since
                if elapsed >= self._inflight_timeout_s:
                    logger.warning(
                        "TwinDelivery: session %s inflight timeout for %s:%s after %.1fs",
                        session_id,
                        table,
                        key,
                        elapsed,
                    )
                    del self._session_inflight[session_id]
                else:
                    logger.debug(
                        "TwinDelivery: session %s has inflight %s:%s, skipping",
                        session_id,
                        table,
                        key,
                    )
                    return []

        # Global inflight check (backward compatibility)
        if self._inflight_key is not None:
            logger.debug(
                "TwinDelivery: global inflight %s:%s, skipping",
                self._inflight_key[0],
                self._inflight_key[1],
            )
            return []

        # Get pending settings
        pending = self._twin_queue.get_pending()
        if not pending:
            return []

        # Take first setting
        setting = pending[0]
        
        # Mark as inflight (both session and global)
        now = time.monotonic()
        self._inflight_key = (setting.table, setting.key)
        self._inflight_device_id = device_id
        self._inflight_since = now
        
        if session_id is not None:
            self._session_inflight[session_id] = (setting.table, setting.key, now)
        
        logger.info(
            "TwinDelivery: delivering %s:%s=%s (device=%s, session=%s)",
            setting.table,
            setting.key,
            setting.value,
            device_id,
            session_id or "global",
        )
        
        return [setting]

    def acknowledge(self, table: str, key: str, session_id: str | None = None) -> bool:
        """Acknowledge setting delivery.
        
        Args:
            table: Table name
            key: Setting key
            session_id: Session ID (optional)
            
        Returns:
            True if setting was inflight and acknowledged
        """
        # Check session-level inflight
        if session_id is not None:
            session_inflight = self._session_inflight.get(session_id)
            if session_inflight is not None:
                s_table, s_key, _ = session_inflight
                if (s_table, s_key) == (table, key):
                    del self._session_inflight[session_id]
                    logger.info(
                        "TwinDelivery: session %s acknowledged %s:%s",
                        session_id,
                        table,
                        key,
                    )
        
        # Check global inflight
        if self._inflight_key == (table, key):
            self._clear_global_inflight()
            removed = self._twin_queue.acknowledge(table, key)
            if removed:
                logger.info("TwinDelivery: acknowledged %s:%s", table, key)
            return True
        
        # Try queue acknowledge anyway
        removed = self._twin_queue.acknowledge(table, key)
        return removed

    def _clear_global_inflight(self) -> None:
        """Clear global inflight state."""
        self._inflight_key = None
        self._inflight_device_id = None
        self._inflight_since = None

    def clear_session(self, session_id: str) -> None:
        """Clear session tracking (call when TCP session ends)."""
        if session_id in self._session_inflight:
            table, key, _ = self._session_inflight[session_id]
            logger.debug(
                "TwinDelivery: clearing session %s inflight %s:%s",
                session_id,
                table,
                key,
            )
            del self._session_inflight[session_id]

    def inflight(self) -> tuple[str, str] | None:
        """Get current global inflight setting."""
        return self._inflight_key

    def inflight_setting(self) -> tuple[TwinSetting, str] | None:
        """Return current inflight setting together with target device_id."""
        if self._inflight_key is None or self._inflight_device_id is None:
            return None
        setting = self._twin_queue.get(self._inflight_key[0], self._inflight_key[1])
        if setting is None:
            return None
        return setting, self._inflight_device_id

    def session_inflight(self, session_id: str) -> tuple[str, str] | None:
        """Get current session inflight setting."""
        data = self._session_inflight.get(session_id)
        if data:
            return (data[0], data[1])
        return None

    def has_pending_or_inflight(self, session_id: str | None = None) -> bool:
        """Check if there are pending or inflight settings."""
        if session_id is not None:
            if session_id in self._session_inflight:
                return True
        return self._inflight_key is not None or self._twin_queue.size() > 0

    def set_cloud_inflight(self) -> None:
        """Mark cloud-initiated setting as in-flight."""
        self._cloud_inflight = True
        logger.debug("TwinDelivery: cloud setting marked as inflight")

    def clear_cloud_inflight(self) -> None:
        """Clear cloud-initiated setting inflight flag."""
        self._cloud_inflight = False
        logger.debug("TwinDelivery: cloud setting inflight cleared")

    def is_cloud_inflight(self) -> bool:
        """Check if cloud-initiated setting is in-flight."""
        return self._cloud_inflight

    def has_pending(self) -> bool:
        """Check if there are pending local settings."""
        return self._twin_queue.size() > 0

    def get_first_pending(self) -> TwinSetting | None:
        """Get first pending local setting without marking as inflight."""
        pending = self._twin_queue.get_pending()
        return pending[0] if pending else None

    @staticmethod
    def build_setting_xml(
        table: str,
        key: str,
        value: object,
        device_id: str,
        id_set: int,
        msg_id: int = 0,
        confirm: str = "New",
    ) -> str:
        """Build XML payload for setting delivery."""
        if msg_id == 0:
            msg_id = secrets.randbelow(1_000_000) + 13_000_000

        now_utc = datetime.now(timezone.utc)
        now_local_cz = now_utc + timedelta(hours=1)
        ver = secrets.randbelow(65_535)

        return (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{now_local_cz.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
            f"<NewValue>{value}</NewValue>"
            f"<Confirm>{confirm}</Confirm>"
            f"<TblName>{table}</TblName>"
            f"<TblItem>{key}</TblItem>"
            "<ID_Server>9</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
            f"<ver>{ver:05d}</ver>"
        )
