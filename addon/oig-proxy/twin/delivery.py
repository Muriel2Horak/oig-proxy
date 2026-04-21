from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any

from telemetry.settings_audit import (
    ACK_TERMINAL_PRECEDENCE,
    TERMINAL_STEPS,
    SettingResult,
    SettingStep,
    _normalize_value_for_text,
    make_incoming_record,
    make_step_record,
)

if TYPE_CHECKING:
    from ..mqtt.client import MQTTClient
    from telemetry.collector import TelemetryCollector
    from .state import TwinQueue, TwinSetting


logger = logging.getLogger(__name__)


@dataclass
class _CloudPendingSetting:
    setting: TwinSetting
    device_id: str
    tracked_at: float
    reason_setting_seen: bool = False
    reason_setting_at: float | None = None


class TwinDelivery:
    """Manages delivery of pending settings to BOX via proxy.
    
    Session-level tracking ensures only one setting is in-flight per TCP session.
    Cloud-initiated settings take priority over local queue.
    """
    
    def __init__(
        self,
        twin_queue: TwinQueue,
        mqtt: MQTTClient,
        inflight_timeout_s: float = 60.0,
        telemetry_collector: TelemetryCollector | None = None,
    ) -> None:
        self._twin_queue = twin_queue
        self._mqtt = mqtt
        self._inflight_timeout_s = inflight_timeout_s
        self._telemetry_collector = telemetry_collector

        # Cloud-initiated setting tracking
        self._cloud_pending: dict[tuple[str, str, str], deque[_CloudPendingSetting]] = defaultdict(deque)
        self._cloud_legacy_inflight: bool = False

        # Session-level inflight tracking: session_id -> (table, key, since)
        self._session_inflight: dict[str, tuple[str, str, float]] = {}

        # Global inflight for backward compatibility
        self._inflight_key: tuple[str, str] | None = None
        self._inflight_device_id: str | None = None
        self._inflight_since: float | None = None
        self._last_seen_id_set: int | None = None
        self._last_msg_id: int | None = None

        self._recorded_terminal: dict[str, SettingStep] = {}

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

    def _make_parent_record(self, setting: TwinSetting, device_id: str) -> Any:
        record = make_incoming_record(
            device_id=device_id,
            table=setting.table,
            key=setting.key,
            raw_text=setting.raw_text,
            value=setting.value,
            msg_id=setting.msg_id,
            id_set=setting.id_set,
        )
        record.audit_id = setting.audit_id
        return record

    def _record_audit_step(
        self,
        setting: TwinSetting,
        device_id: str,
        step: SettingStep,
        *,
        result: SettingResult | None = None,
        confirmed_value: Any = None,
        raw_text: str | None = None,
        session_id: str = "",
    ) -> None:
        if self._telemetry_collector is None or not setting.audit_id:
            return
        is_terminal = step in TERMINAL_STEPS and result != SettingResult.PENDING
        if is_terminal:
            existing = self._recorded_terminal.get(setting.audit_id)
            if existing is not None:
                existing_prec = ACK_TERMINAL_PRECEDENCE.get(existing, 0)
                new_prec = ACK_TERMINAL_PRECEDENCE.get(step, 0)
                if existing_prec >= new_prec:
                    return
            self._recorded_terminal[setting.audit_id] = step
        parent = self._make_parent_record(setting, device_id)
        record = make_step_record(
            parent,
            step,
            result=result,
            confirmed_value=confirmed_value,
            raw_text=raw_text,
            session_id=session_id,
        )
        self._telemetry_collector.record_setting_audit_step(record)

    @staticmethod
    def _cloud_pending_key(device_id: str, table: str, key: str) -> tuple[str, str, str]:
        return device_id, table, key

    def _iter_cloud_pending(self) -> list[_CloudPendingSetting]:
        entries: list[_CloudPendingSetting] = []
        for queue in self._cloud_pending.values():
            entries.extend(queue)
        return entries

    def _remove_cloud_pending(self, pending: _CloudPendingSetting) -> None:
        queue_key = self._cloud_pending_key(
            pending.device_id,
            pending.setting.table,
            pending.setting.key,
        )
        queue = self._cloud_pending.get(queue_key)
        if queue is None:
            return
        try:
            queue.remove(pending)
        except ValueError:
            return
        if not queue:
            self._cloud_pending.pop(queue_key, None)

    def _oldest_cloud_pending(self, device_id: str | None = None) -> _CloudPendingSetting | None:
        candidates = [
            pending
            for pending in self._iter_cloud_pending()
            if device_id is None or pending.device_id == device_id
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda pending: pending.tracked_at)

    def _expire_cloud_pending(self, now: float | None = None) -> None:
        if now is None:
            now = time.monotonic()
        for pending in list(self._iter_cloud_pending()):
            if pending.reason_setting_seen and pending.reason_setting_at is not None:
                if now - pending.reason_setting_at >= self._inflight_timeout_s:
                    self.record_ack_reason_setting(
                        pending.setting,
                        pending.device_id,
                        session_id="",
                        terminal=True,
                    )
                    self._remove_cloud_pending(pending)
            elif now - pending.tracked_at >= self._inflight_timeout_s:
                self._record_audit_step(
                    pending.setting,
                    pending.device_id,
                    SettingStep.TIMEOUT,
                )
                self._remove_cloud_pending(pending)

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
                setting = self._twin_queue.get(self._inflight_key[0], self._inflight_key[1])
                if setting is not None and self._inflight_device_id is not None:
                    self._record_audit_step(
                        setting,
                        self._inflight_device_id,
                        SettingStep.TIMEOUT,
                        session_id=session_id or "",
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
                    setting = self._twin_queue.get(table, key)
                    if setting is not None and self._inflight_device_id is not None:
                        self._record_audit_step(
                            setting,
                            self._inflight_device_id,
                            SettingStep.TIMEOUT,
                            session_id=session_id,
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

        self._record_audit_step(
            setting,
            device_id,
            SettingStep.DELIVER_SELECTED,
            session_id=session_id or "",
        )

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
        if removed:
            return True

        for pending in list(self._iter_cloud_pending()):
            if (pending.setting.table, pending.setting.key) == (table, key):
                self._remove_cloud_pending(pending)
                return True

        return False

    def _clear_global_inflight(self) -> None:
        self._inflight_key = None
        self._inflight_device_id = None
        self._inflight_since = None

    def clear_session(self, session_id: str) -> None:
        if session_id in self._session_inflight:
            table, key, _ = self._session_inflight[session_id]
            setting = self._twin_queue.get(table, key)
            if setting is not None and self._inflight_device_id is not None:
                self._record_audit_step(
                    setting,
                    self._inflight_device_id,
                    SettingStep.SESSION_CLEARED,
                    session_id=session_id,
                )
            del self._session_inflight[session_id]

    def record_injected_box(
        self,
        setting: TwinSetting,
        device_id: str,
        session_id: str = "",
    ) -> None:
        self._record_audit_step(
            setting,
            device_id,
            SettingStep.INJECTED_BOX,
            session_id=session_id,
        )

    def record_ack_box_observed(
        self,
        setting: TwinSetting,
        device_id: str,
        session_id: str = "",
    ) -> None:
        self._record_audit_step(
            setting,
            device_id,
            SettingStep.ACK_BOX_OBSERVED,
            session_id=session_id,
        )

    def record_ack_tbl_events(
        self,
        setting: TwinSetting,
        device_id: str,
        confirmed_value: Any,
        session_id: str = "",
    ) -> None:
        self._record_audit_step(
            setting,
            device_id,
            SettingStep.ACK_TBL_EVENTS,
            confirmed_value=confirmed_value,
            session_id=session_id,
        )

    def record_ack_reason_setting(
        self,
        setting: TwinSetting,
        device_id: str,
        session_id: str = "",
        *,
        terminal: bool = True,
    ) -> None:
        self._record_audit_step(
            setting,
            device_id,
            SettingStep.ACK_REASON_SETTING,
            result=SettingResult.CONFIRMED if terminal else SettingResult.PENDING,
            session_id=session_id,
        )

    def record_nack(
        self,
        setting: TwinSetting,
        device_id: str,
        session_id: str = "",
    ) -> None:
        self._record_audit_step(
            setting,
            device_id,
            SettingStep.NACK,
            session_id=session_id,
        )

    def shutdown(self) -> None:
        if self._inflight_key is not None and self._inflight_device_id is not None:
            setting = self._twin_queue.get(self._inflight_key[0], self._inflight_key[1])
            if setting is not None:
                self._record_audit_step(
                    setting,
                    self._inflight_device_id,
                    SettingStep.SESSION_CLEARED,
                )
            self._clear_global_inflight()

        for pending in list(self._iter_cloud_pending()):
            self._record_audit_step(
                pending.setting,
                pending.device_id,
                SettingStep.SESSION_CLEARED,
            )
            self._remove_cloud_pending(pending)

    def inflight(self) -> tuple[str, str] | None:
        """Get current global inflight setting."""
        self._expire_cloud_pending()
        pending = self._oldest_cloud_pending()
        if pending is not None:
            return pending.setting.table, pending.setting.key
        return self._inflight_key

    def inflight_setting(self) -> tuple[TwinSetting, str] | None:
        """Return current inflight setting together with target device_id."""
        self._expire_cloud_pending()
        pending = self._oldest_cloud_pending()
        if pending is not None:
            return pending.setting, pending.device_id
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
        self._expire_cloud_pending()
        if session_id is not None:
            if session_id in self._session_inflight:
                return True
        return self._cloud_legacy_inflight or bool(self._iter_cloud_pending()) or self._inflight_key is not None or self._twin_queue.size() > 0

    def begin_cloud_setting(
        self,
        device_id: str,
        table: str,
        key: str,
        value: Any,
        raw_text: str,
        *,
        msg_id: int = 0,
        id_set: int = 0,
        confirm: str = "New",
    ) -> None:
        """Create audit-backed inflight state for a cloud-originated setting."""
        from .state import TwinSetting

        self._expire_cloud_pending()

        incoming_record = make_incoming_record(
            device_id=device_id,
            table=table,
            key=key,
            raw_text=raw_text,
            value=value,
            msg_id=msg_id,
            id_set=id_set,
        )
        if self._telemetry_collector is not None:
            self._telemetry_collector.record_setting_audit_step(incoming_record)

        setting = TwinSetting(
            table=table,
            key=key,
            value=value,
            enqueued_at=time.time(),
            raw_text=incoming_record.raw_text,
            audit_id=incoming_record.audit_id,
            msg_id=msg_id,
            id_set=id_set,
            confirm=confirm,
        )
        queue_key = self._cloud_pending_key(device_id, table, key)
        self._cloud_pending[queue_key].append(
            _CloudPendingSetting(
                setting=setting,
                device_id=device_id,
                tracked_at=time.monotonic(),
            )
        )
        logger.debug("TwinDelivery: cloud setting tracked as inflight %s:%s", table, key)

    def mark_cloud_reason_setting(
        self,
        device_id: str,
        session_id: str = "",
    ) -> tuple[TwinSetting, str] | None:
        self._expire_cloud_pending()
        pending = self._oldest_cloud_pending(device_id)
        if pending is None:
            return None
        if not pending.reason_setting_seen:
            self.record_ack_reason_setting(
                pending.setting,
                pending.device_id,
                session_id=session_id,
                terminal=False,
            )
            pending.reason_setting_seen = True
            pending.reason_setting_at = time.monotonic()
        return pending.setting, pending.device_id

    def match_cloud_tbl_events(
        self,
        device_id: str,
        table: str,
        key: str,
        confirmed_value: Any,
        session_id: str = "",
    ) -> tuple[TwinSetting, str] | None:
        self._expire_cloud_pending()
        queue_key = self._cloud_pending_key(device_id, table, key)
        queue = self._cloud_pending.get(queue_key)
        if not queue:
            return None

        normalized_confirmed = _normalize_value_for_text(confirmed_value)
        match = None
        for pending in queue:
            if _normalize_value_for_text(pending.setting.value) == normalized_confirmed:
                match = pending
                break
        if match is None:
            match = queue[0]

        self.record_ack_tbl_events(
            match.setting,
            match.device_id,
            confirmed_value=confirmed_value,
            session_id=session_id,
        )
        self._remove_cloud_pending(match)
        return match.setting, match.device_id

    def set_cloud_inflight(self) -> None:
        """Mark cloud-initiated setting as in-flight."""
        self._cloud_legacy_inflight = True
        logger.debug("TwinDelivery: cloud setting marked as inflight")

    def clear_cloud_inflight(self) -> None:
        """Clear cloud-initiated setting inflight flag."""
        self._cloud_legacy_inflight = False
        self._expire_cloud_pending()
        logger.debug("TwinDelivery: cloud setting inflight cleared")

    def is_cloud_inflight(self) -> bool:
        """Check if cloud-initiated setting is in-flight."""
        self._expire_cloud_pending()
        return self._cloud_legacy_inflight or bool(self._iter_cloud_pending())

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
