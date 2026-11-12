"""Deliver settings with device-scoped profile and wire correlation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
import threading
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

from protocol.frames import build_setting_frame
from protocol.ver_profile import (
    ProfileStatus, VerProfiles, matches_setting_poll, native_frame_fields, parse_setting_reply,
)
from settings_constraints import is_setting_allowed, validate_setting_value
from telemetry.settings_audit import (
    TERMINAL_STEPS,
    SettingResult,
    SettingStep,
    is_stronger_ack,
    make_incoming_record,
    make_step_record,
)
from .state import TwinSetting

if TYPE_CHECKING:
    from mqtt.client import MQTTClient
    from telemetry.collector import TelemetryCollector
    from .state import TwinQueue


logger = logging.getLogger(__name__)


_MAX_MSG_ID = 2**31 - 1
_MAX_ID_SET = 9_999_999_999


@dataclass(frozen=True)
class _CounterState:
    msg_id: int | None = None
    id_set: int | None = None


@dataclass
class _CloudPendingSetting:
    setting: TwinSetting
    device_id: str
    tracked_at: float
    wire_dt: datetime | None = None
    reason_setting_seen: bool = False
    reason_setting_at: float | None = None
    session_id: str | None = None
    reply_ambiguous: bool = False
    event_ambiguous: bool = False


@dataclass(frozen=True)
class _SelectedRequest:
    """Freeze admitted scalar data while retaining the exact source request identity."""

    source: TwinSetting
    table: str
    key: str
    value: Any
    enqueued_at: float
    raw_text: str
    audit_id: str
    msg_id: int
    id_set: int
    confirm: str
    profile_epoch: int | None
    device_id: str

    @classmethod
    def capture(cls, source: TwinSetting, value: Any) -> _SelectedRequest:
        """Copy admitted scalar fields independently from the replaceable queue DTO."""
        return cls(source, source.table, source.key, value, source.enqueued_at,
                   source.raw_text, source.audit_id, source.msg_id, source.id_set,
                   source.confirm, source.profile_epoch, source.device_id)

    def as_setting(self) -> TwinSetting:
        """Expose an isolated compatibility DTO without leaking the private snapshot."""
        return TwinSetting(self.table, self.key, self.value, self.enqueued_at,
                           self.raw_text, self.audit_id, self.msg_id, self.id_set,
                           self.confirm, self.profile_epoch, self.device_id)


@dataclass(frozen=True)
class _BoxObservation:
    """Separate inbound BOX counter namespace from outbound transport IDs."""

    highwater: int
    epoch: int
    session_id: str | None
    source_dt: datetime


@dataclass(frozen=True)
class _LocalInflight:
    request: _SelectedRequest
    device_id: str
    selected_at: float
    session_id: str | None
    profile_status: ProfileStatus
    wire_dt: datetime | None = None
    wire_counters: _CounterState | None = None
    wire_box: _BoxObservation | None = None
    cloud_generation: int = 0
    injected_at: float | None = None
    reply_ambiguous: bool = False
    event_ambiguous: bool = False
    retain_receipt: bool = False


@dataclass(frozen=True)
class _EventOwner:
    """One resolved event consumer shared by the local and cloud compatibility APIs."""

    source: str
    request: Any


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
        on_result: Callable[[dict[str, Any]], None] | None = None,
        on_profile_change: Callable[[], None] | None = None,
        *,
        profile_state_path: Path | None = None,
        counter_state_path: Path | None = None,
        box_writes_enabled: bool = True,
    ) -> None:
        self._twin_queue = twin_queue
        self._mqtt = mqtt
        self._inflight_timeout_s = inflight_timeout_s
        self._telemetry_collector = telemetry_collector
        self._box_writes_enabled = box_writes_enabled

        # Cloud-initiated setting tracking
        self._cloud_pending: dict[tuple[str, str, str], deque[_CloudPendingSetting]] = defaultdict(deque)
        self._cloud_legacy_sessions: set[str | None] = set()

        # Session-level inflight tracking: session_id -> (table, key, since)
        self._session_inflight: dict[str, tuple[str, str, float]] = {}

        # Global inflight for backward compatibility
        self._inflight_key: tuple[str, str] | None = None
        self._inflight_device_id: str | None = None
        self._inflight_since: float | None = None
        self._local_inflight: _LocalInflight | None = None
        self._counter_state_path = counter_state_path
        self._counters = self._load_counters()

        self._recorded_terminal: dict[str, SettingStep] = {}
        self._on_result = on_result
        self._profiles = VerProfiles(state_path=profile_state_path)
        self._last_profile_status: dict[str, ProfileStatus] = {}
        self._on_profile_change = on_profile_change
        self._result_lock = threading.Lock()
        self._terminal_results: set[str] = set()
        self._box_observations: dict[str, _BoxObservation] = {}
        self._consumed_box_counters: dict[str, int] = {}
        self._consumed_event_bases: set[tuple[str, ...]] = set()
        self._consumed_unqualified_events: set[tuple[str, ...]] = set()
        self._consumed_event_counters: set[tuple[str, ...]] = set()
        self._generic_reply_quarantine: set[str | None] = set()
        self._used_local_sessions: set[str] = set()
        self._event_receipts: dict[str, _LocalInflight] = {}
        self._cloud_generations: dict[tuple[str, str, str], int] = defaultdict(int)
        register_drop = getattr(self._twin_queue, "set_drop_callback", None)
        if callable(register_drop):
            register_drop(self._queue_dropped)

    def profile_status(self, device_id: str) -> ProfileStatus:
        """Return the current proof used by the actual wire gate."""
        return self._profiles.status(device_id)

    def _refresh_profile_status(self, device_id: str) -> ProfileStatus:
        status = self.profile_status(device_id)
        if status == self._last_profile_status.get(device_id):
            return status
        self._last_profile_status[device_id] = status
        self._twin_queue.set_profile_epoch(
            status.epoch if status.profile is not None else None, device_id=device_id,
        )
        if self._on_profile_change is not None:
            try:
                self._on_profile_change()
            except Exception:
                logger.exception("Could not publish local control profile state")
        return status

    def observe_box_profile(self, raw: bytes, session_id: str | None, device_id: str) -> int | None:
        """Update the gate before processing BOX event evidence or selecting a Setting."""
        counter = self._profiles.observe_box(raw, session_id)
        self._refresh_profile_status(device_id)
        return counter

    def observe_cloud_profile(self, raw: bytes, session_id: str | None, device_id: str) -> None:
        """Track actual transparent Cloud→BOX traffic."""
        self._profiles.observe_cloud(raw, session_id)
        self._refresh_profile_status(device_id)

    def _emit_result(self, setting: TwinSetting, result: str, *, source: str,
                     evidence: str, reason: str = "") -> None:
        """Emit one immutable outcome per admitted request without transport side effects."""
        identity = setting.audit_id
        if not identity:
            return
        with self._result_lock:
            if identity in self._terminal_results:
                return
            self._terminal_results.add(identity)
        event = {"result": result, "table": setting.table, "key": setting.key,
                 "value": deepcopy(setting.value), "source": source, "audit_id": identity,
                 "at": datetime.now(timezone.utc).isoformat(), "event_id": uuid4().hex,
                 "evidence": evidence, "reason": reason}
        if self._on_result is not None:
            try:
                self._on_result(event)
            except Exception:  # Result observers cannot break setting transport completion.
                logger.exception("Setting result callback failed for %s", identity)

    def _queue_dropped(self, setting: TwinSetting, reason: str) -> None:
        self._emit_result(setting, "dropped", source="local", evidence="queue", reason=reason)

    def _quarantine_overlaps(self, cloud: _CloudPendingSetting) -> None:
        """Keep ambiguous evidence quarantined even after competing requests retire."""
        local = self._local_inflight
        if local is not None and local.wire_dt is not None:
            same_reply = local.session_id == cloud.session_id and not cloud.reason_setting_seen
            same_event = (local.device_id == cloud.device_id
                          and (local.request.table, local.request.key) == (cloud.setting.table, cloud.setting.key)
                          and self._same_typed_value(
                                                    local.request.value, cloud.setting.value))
            if same_reply:
                self._generic_reply_quarantine.add(local.session_id)
            if same_reply or same_event:
                self._local_inflight = replace(local,
                    reply_ambiguous=local.reply_ambiguous or same_reply,
                    event_ambiguous=local.event_ambiguous or same_event)
                cloud.reply_ambiguous = cloud.reply_ambiguous or same_reply
                cloud.event_ambiguous = cloud.event_ambiguous or same_event
        for other in self._iter_cloud_pending():
            if other is cloud:
                continue
            if (other.session_id == cloud.session_id and not other.reason_setting_seen
                    and not cloud.reason_setting_seen):
                other.reply_ambiguous = cloud.reply_ambiguous = True
                self._generic_reply_quarantine.add(cloud.session_id)
            if (other.device_id == cloud.device_id
                    and (other.setting.table, other.setting.key) == (cloud.setting.table, cloud.setting.key)
                    and self._same_typed_value(
                                              other.setting.value, cloud.setting.value)):
                other.event_ambiguous = cloud.event_ambiguous = True

    def _event_tokens(self, device: str, table: str, key: str, value: Any,
                      stamp: datetime, event_id_set: int | None) -> tuple[tuple[str, ...], tuple[str, ...] | None]:
        try:
            number = Decimal(str(value))
            canonical = "number:" + ("0" if number == 0 else str(number.normalize()))
        except (InvalidOperation, ValueError, TypeError):
            canonical = "text:" + str(value)
        normalized = stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else stamp
        base = (device, table, key, canonical, normalized.isoformat())
        counter = ((device, table, key, canonical, str(event_id_set))
                   if type(event_id_set) is int and event_id_set > 0 else None)
        return base, counter

    def _event_owner(self, device: str, table: str, key: str, value: Any,
                     stamp: datetime, event_id_set: int | None,
                     session_id: str | None) -> _EventOwner | None:
        """Resolve one event across both owners without relying on wrapper call order."""
        base, counter = self._event_tokens(device, table, key, value, stamp, event_id_set)
        if counter is None:
            if base in self._consumed_event_bases:
                return None
        elif counter in self._consumed_event_counters or base in self._consumed_unqualified_events:
            return None
        candidates: list[_EventOwner] = []
        local = self._local_inflight
        if (local is not None and local.wire_dt is not None and not local.event_ambiguous
                and local.device_id == device and local.session_id == session_id
                and (local.request.table, local.request.key) == (table, key)
                and counter is not None
                and self.profile_status(device) == local.profile_status
                and (stamp.tzinfo is None) == (local.wire_dt.tzinfo is None)
                and self._same_typed_value(local.request.value, value)
                and (stamp >= local.wire_dt
                     or self._qualified_early_event(local, stamp, event_id_set, session_id))):
            candidates.append(_EventOwner("local", local))
        for cloud in self._iter_cloud_pending():
            if (not cloud.event_ambiguous and cloud.wire_dt is not None and cloud.device_id == device
                    and cloud.session_id == (session_id or None)
                    and (cloud.setting.table, cloud.setting.key) == (table, key)
                    and (stamp.tzinfo is None) == (cloud.wire_dt.tzinfo is None)
                    and stamp >= cloud.wire_dt
                    and self._same_typed_value(cloud.setting.value, value)):
                candidates.append(_EventOwner("cloud", cloud))
        return candidates[0] if len(candidates) == 1 else None

    def _consume_event(self, device: str, table: str, key: str, value: Any,
                       stamp: datetime, event_id_set: int | None) -> None:
        base, counter = self._event_tokens(device, table, key, value, stamp, event_id_set)
        self._consumed_event_bases.add(base)
        if counter is None:
            self._consumed_unqualified_events.add(base)
        else:
            self._consumed_event_counters.add(counter)
        if type(event_id_set) is int:
            self._consumed_box_counters[device] = max(
                self._consumed_box_counters.get(device, 0), event_id_set)

    def poll_timeouts(self) -> None:
        """Expire uncertain deliveries from the owner loop, including a silent BOX."""
        for device_id in tuple(self._last_profile_status):
            self._refresh_profile_status(device_id)
        self._expire_local_inflight()
        self._expire_cloud_pending()
        now = time.monotonic()
        for session, context in tuple(self._event_receipts.items()):
            if now - context.selected_at >= self._inflight_timeout_s:
                self._event_receipts.pop(session, None)
        for setting in self._twin_queue.get_pending():
            if time.time() - setting.enqueued_at > 120:
                self._twin_queue.discard(setting, "expired")

    def observe_box_frame(self, device_id: str, id_set: int | None,
                          source_dt: str | None, *, session_id: str | None) -> None:
        """Observe validated inbound BOX counters after matching current events."""
        stamp = self._device_time(source_dt)
        if not device_id or type(id_set) is not int or id_set <= 0 or stamp is None:
            return
        previous = self._box_observations.get(device_id)
        epoch = previous.epoch if previous is not None else 0
        highwater = id_set
        if previous is not None:
            if (id_set < previous.highwater or session_id != previous.session_id
                    or (stamp.tzinfo is None) != (previous.source_dt.tzinfo is None)):
                epoch += 1  # Invalidate current proof; never infer a safe clock/counter reset.
            highwater = max(id_set, previous.highwater)
        self._box_observations[device_id] = _BoxObservation(highwater, epoch, session_id, stamp)

    def _qualified_early_event(self, context: _LocalInflight, stamp: datetime,
                                 event_id_set: int | None, session_id: str | None) -> bool:
        """Match a bounded device clock discrepancy with same-session counter proof."""
        baseline = context.wire_box
        current = self._box_observations.get(context.device_id)
        if (baseline is None or current is None
                or context.wire_dt is None or context.injected_at is None
                or context.session_id is None or session_id != context.session_id):
            return False
        if (baseline.session_id != context.session_id or current.session_id != context.session_id
                or current.epoch != baseline.epoch
                or type(event_id_set) is not int
                or event_id_set <= current.highwater
                or event_id_set <= self._consumed_box_counters.get(context.device_id, 0)):
            return False
        # Exact replay showed a two-second BOX event clock skew on the sending socket.
        deficit = (context.wire_dt - stamp).total_seconds()
        now = time.monotonic()
        if not (0 < deficit <= 2 and context.injected_at <= now
                and now - context.selected_at < self._inflight_timeout_s):
            return False
        identity = self._cloud_pending_key(context.device_id, context.request.table, context.request.key)
        if self._cloud_generations[identity] != context.cloud_generation:
            return False
        return self.profile_status(context.device_id) == context.profile_status

    def _load_counters(self) -> dict[str, _CounterState]:
        path = self._counter_state_path
        if path is None:
            return {}
        try:
            with path.open("rb") as stream:
                raw = stream.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("Counter state is too large")
            snapshot = json.loads(raw)
            if (not isinstance(snapshot, dict) or snapshot.get("version") != 1
                    or not isinstance(snapshot.get("devices"), dict)):
                raise ValueError("Unsupported counter state")
            counters = {}
            for device, state in snapshot["devices"].items():
                if not isinstance(state, dict):
                    raise ValueError("Invalid counter entry")
                msg_id, id_set = state.get("msg_id"), state.get("id_set")
                if ((msg_id is not None and not self._valid_counter(msg_id, _MAX_MSG_ID))
                        or (id_set is not None and not self._valid_counter(id_set, _MAX_ID_SET))):
                    raise ValueError("Counter outside its wire range")
                counters[device] = _CounterState(msg_id, id_set)
            return counters
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError):
            logger.warning("Could not restore outbound Setting counters from %s", path, exc_info=True)
            return {}

    @staticmethod
    def _valid_counter(value: int | None, maximum: int) -> bool:
        return type(value) is int and 0 < value <= maximum

    def _store_counters(self, device_id: str, state: _CounterState) -> None:
        if self._counters.get(device_id) == state:
            return
        path = self._counter_state_path
        if path is not None:
            counters = {**self._counters, device_id: state}
            snapshot = {"version": 1, "devices": {
                device: {"msg_id": value.msg_id, "id_set": value.id_set}
                for device, value in counters.items()
            }}
            temporary_path = None
            try:
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                        prefix=f".{path.name}.", delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    json.dump(snapshot, temporary, separators=(",", ":"))
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_path, path)
                parent_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            except OSError as exc:
                raise ValueError("Could not persist outbound Setting counters") from exc
            finally:
                if temporary_path is not None:
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("Could not remove temporary outbound Setting counter state")
        self._counters[device_id] = state

    def _observe_counters(self, device_id: str, msg_id: int | None, id_set: int | None) -> None:
        current = self._counters.get(device_id, _CounterState())
        if msg_id is not None and msg_id <= (current.msg_id or 0):
            msg_id = None
        if id_set is not None and id_set <= (current.id_set or 0):
            id_set = None
        if msg_id is not None or id_set is not None:
            self._store_counters(device_id, _CounterState(
                msg_id if msg_id is not None else current.msg_id,
                id_set if id_set is not None else current.id_set,
            ))

    def observe_id_set(self, id_set: int | None, device_id: str = "") -> None:
        if self._valid_counter(id_set, _MAX_ID_SET):
            self._observe_counters(device_id, None, id_set)

    def observe_msg_id(self, msg_id: int | None, device_id: str = "") -> None:
        if self._valid_counter(msg_id, _MAX_MSG_ID):
            self._observe_counters(device_id, msg_id, None)

    @staticmethod
    def _allocate_id_set(state: _CounterState) -> int:
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        next_id = max(now_epoch - 2, (state.id_set or 0) + 1)
        if next_id > now_epoch or next_id > _MAX_ID_SET:
            raise ValueError("Cannot allocate a non-future ID_Set")
        return next_id

    @staticmethod
    def _allocate_msg_id(state: _CounterState) -> int:
        if state.msg_id is None:
            raise ValueError("Local control requires an observed message counter")
        if state.msg_id >= _MAX_MSG_ID:
            raise ValueError("Outbound Setting message counter exhausted")
        return state.msg_id + 1

    def next_id_set(self, device_id: str = "") -> int:
        current = self._counters.get(device_id, _CounterState())
        next_id = self._allocate_id_set(current)
        self._store_counters(device_id, replace(current, id_set=next_id))
        return next_id

    def next_msg_id(self, device_id: str = "") -> int:
        current = self._counters.get(device_id, _CounterState())
        next_id = self._allocate_msg_id(current)
        self._store_counters(device_id, replace(current, msg_id=next_id))
        return next_id

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
            if existing is not None and not is_stronger_ack(step, existing):
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
                    self._record_audit_step(
                        pending.setting, pending.device_id, SettingStep.TIMEOUT,
                        result=SettingResult.INCOMPLETE, session_id=pending.session_id or "",
                    )
                    self._remove_cloud_pending(pending)
            elif now - pending.tracked_at >= self._inflight_timeout_s:
                self._record_audit_step(
                    pending.setting,
                    pending.device_id,
                    SettingStep.TIMEOUT,
                )
                self._generic_reply_quarantine.add(pending.session_id)
                self._emit_result(pending.setting, "timeout", source="cloud",
                                  evidence="no_ack", reason="inflight_timeout")
                self._remove_cloud_pending(pending)

    def _finish_local(self) -> bool:
        context = self._local_inflight
        if context is None:
            return False
        self._twin_queue.remove_if_current(context.request.source)
        self._twin_queue.release_sent(context.request.source)
        self._clear_global_inflight()
        return True

    def _expire_local_inflight(self) -> None:
        context = self._local_inflight
        if context is None:
            return
        if context.wire_dt is None and not self._twin_queue.is_current(context.request.source):
            self._finish_local()
            return
        if time.monotonic() - context.selected_at < self._inflight_timeout_s:
            return
        if context.wire_dt is None:
            self._record_audit_step(context.request.as_setting(), context.device_id,
                                    SettingStep.SESSION_CLEARED, session_id=context.session_id or "")
            self._twin_queue.discard(context.request.source, "unsent_timeout")
            self._finish_local()
            return
        self._generic_reply_quarantine.add(context.session_id)
        logger.warning("TwinDelivery: local inflight timeout for %s:%s; no replay",
                       context.request.table, context.request.key)
        self._record_audit_step(context.request.as_setting(), context.device_id,
                                SettingStep.TIMEOUT, session_id=context.session_id or "")
        self._emit_result(context.request.as_setting(), "timeout", source="local",
                          evidence="no_ack", reason="inflight_timeout")
        self._finish_local()

    def _reject_local(self, source: TwinSetting, device_id: str,
                      session_id: str | None, reason: str, *, disposition: str = "validation") -> None:
        logger.warning("Local control rejected before send: %s", reason)
        self._record_audit_step(source, device_id, SettingStep.REJECTED_VALIDATION,
                                session_id=session_id or "")
        self._twin_queue.discard(source, disposition)

    async def deliver_pending(
        self,
        device_id: str,
        session_id: str | None = None,
    ) -> list[TwinSetting]:
        """Select at most one admitted request; never reselect uncertain inflight writes."""
        self._expire_local_inflight()
        if (not self._box_writes_enabled or self._local_inflight is not None
                or self.is_cloud_inflight(device_id)
                or session_id is not None and (session_id in self._used_local_sessions
                                               or session_id in self._generic_reply_quarantine)):
            return []
        status = self._refresh_profile_status(device_id)
        pending = self._twin_queue.get_pending(device_id=device_id)
        if not pending:
            return []
        source = pending[0]
        if time.time() - source.enqueued_at > 120:
            self._reject_local(source, device_id, session_id,
                               "manual request older than the 120-second project intent limit",
                               disposition="expired")
            return []
        if source.table == "proxy_control" or not is_setting_allowed(source.table, source.key):
            self._reject_local(source, device_id, session_id, "Setting is outside local write policy")
            return []
        ok, normalized, reason = validate_setting_value(source.table, source.key, source.value)
        if not ok:
            self._reject_local(source, device_id, session_id, reason)
            return []
        if status.profile is None:
            self._reject_local(source, device_id, session_id, status.reason, disposition="profile_unready")
            return []
        if source.profile_epoch != status.epoch:
            self._reject_local(source, device_id, session_id, "Request predates current ver proof",
                               disposition="profile_epoch_changed")
            return []
        counters = self._counters.get(device_id, _CounterState())
        try:
            self._allocate_msg_id(counters)
            self._allocate_id_set(counters)
        except ValueError as exc:
            logger.debug("TwinDelivery: waiting for valid outbound counters: %s", exc)
            return []
        if not self._twin_queue.is_current(source):
            return []
        request = _SelectedRequest.capture(source, normalized)
        now = time.monotonic()
        self._local_inflight = _LocalInflight(request, device_id, now, session_id, status)
        self._inflight_key = (request.table, request.key)
        self._inflight_device_id = device_id
        self._inflight_since = now
        if session_id is not None:
            self._session_inflight[session_id] = (request.table, request.key, now)
        selected = request.as_setting()
        self._record_audit_step(selected, device_id, SettingStep.DELIVER_SELECTED,
                                session_id=session_id or "")
        logger.info("TwinDelivery: selected %s:%s=%s (device=%s, session=%s)",
                    request.table, request.key, request.value, device_id, session_id)
        return [selected]

    @staticmethod
    def _device_time(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.strptime(value.strip(), "%d.%m.%Y %H:%M:%S")
            except ValueError:
                return None

    @staticmethod
    def _same_typed_value(expected: Any, observed: Any) -> bool:
        try:
            left, right = Decimal(str(expected)), Decimal(str(observed))
            return left.is_finite() and right.is_finite() and left == right
        except (InvalidOperation, ValueError, TypeError):
            return isinstance(expected, str) and expected == observed

    def build_local_frame(self, setting: TwinSetting, session_id: str | None) -> bytes:
        """Serialize the selected request with its immutable outbound profile."""
        context = self._wire_context(setting, session_id)
        profile = context.profile_status.profile
        if profile is None:
            raise ValueError("Missing outbound ver proof")
        if context.wire_counters is not None:
            raise ValueError("The selected request already has reserved wire counters")
        current = self._counters.get(context.device_id, _CounterState())
        id_set = self._allocate_id_set(current)
        msg_id = self._allocate_msg_id(current)
        reserved = _CounterState(msg_id, id_set)
        self._store_counters(context.device_id, reserved)
        self._local_inflight = replace(context, wire_counters=reserved)
        return build_setting_frame(
            device_id=context.device_id, table=setting.table, key=setting.key,
            value=setting.value, id_set=id_set, msg_id=msg_id, profile=profile,
            include_tto=context.profile_status.include_tto,
        )

    def _wire_context(self, setting: TwinSetting, session_id: str | None) -> _LocalInflight:
        context = self._local_inflight
        if (not self._box_writes_enabled or context is None or setting != context.request.as_setting()
                or context.session_id != session_id or context.wire_dt is not None):
            raise ValueError("Wire binding requires the exact unbound local request and sender session")
        if self._refresh_profile_status(context.device_id) != context.profile_status:
            self._reject_local(context.request.source, context.device_id, session_id,
                               "Profile changed before serialization", disposition="profile_epoch_changed")
            self._finish_local()
            raise ValueError("Profile changed before serialization")
        if not self._twin_queue.is_current(context.request.source):
            self._finish_local()
            raise ValueError("Selected manual request was replaced or cleared before send")
        return context

    def bind_local_wire(self, setting: TwinSetting, frame_bytes: bytes,
                        session_id: str | None = None, *, retain_receipt: bool = False) -> None:
        """Bind the actual serialized device DT before writing to the selected socket."""
        context = self._wire_context(setting, session_id)
        profile = context.profile_status.profile
        if profile is None:
            raise ValueError("Missing outbound ver proof")
        fields = native_frame_fields(frame_bytes, allow_repeated_identity=False)
        ver_at = frame_bytes.find(b"<ver>")
        if (fields is None or ver_at <= 7
                or fields.get("ver") != f"{profile.compute(frame_bytes[7:ver_at]):05d}"):
            raise ValueError("Serialized Setting does not match the qualified wire profile")
        reserved = context.wire_counters
        if reserved is None:
            raise ValueError("Serialized Setting has no reserved wire counters")
        required = {"ID_Device": context.device_id, "TblName": setting.table,
                    "TblItem": setting.key, "Reason": "Setting",
                    "ID": str(reserved.msg_id), "ID_Set": str(reserved.id_set)}
        if any(fields.get(key) != expected for key, expected in required.items()):
            raise ValueError("Serialized setting identity does not match the selected local request")
        tto_tags = [key for key in fields if key in {"TTO_P", "TTO_E"}]
        expected_tto_tags = ["TTO_P", "TTO_E"] if context.profile_status.include_tto else []
        if tto_tags != expected_tto_tags:
            raise ValueError("Serialized setting layout does not match the qualified profile")
        if not self._same_typed_value(context.request.value, fields.get("NewValue")):
            raise ValueError("Serialized setting value is invalid")
        stamp = self._device_time(fields.get("DT"))
        if stamp is None:
            raise ValueError("Serialized setting requires a parseable device DT")
        if not self._twin_queue.mark_sent(context.request.source):
            self._finish_local()
            raise ValueError("Selected manual request was replaced or cleared before send")
        if session_id is not None:
            self._used_local_sessions.add(session_id)
        identity = self._cloud_pending_key(context.device_id, setting.table, setting.key)
        self._local_inflight = replace(context, wire_dt=stamp, retain_receipt=retain_receipt,
                                       wire_box=self._box_observations.get(context.device_id),
                                       cloud_generation=self._cloud_generations[identity])
        for cloud in self._iter_cloud_pending():
            self._quarantine_overlaps(cloud)
        self._profiles.note_local_send(session_id)

    def valid_local_poll(self, raw: bytes, device_id: str) -> bool:
        """Qualify the poll with the current proof before opening a write slot."""
        profile = self._refresh_profile_status(device_id).profile
        return self._box_writes_enabled and profile is not None and matches_setting_poll(raw, device_id, profile)

    def local_wire_deadline(self, session_id: str | None) -> float | None:
        """Expose the existing deadline for an offline socket waiting for its reply."""
        context = self._local_inflight
        if session_id is not None and (context is None or context.session_id != session_id):
            context = self._event_receipts.get(session_id)
        if context is None or context.wire_dt is None or context.session_id != session_id:
            return None
        return context.selected_at + self._inflight_timeout_s

    def local_protocol_reply(self, raw: bytes, session_id: str | None) -> dict[str, str] | None:
        """Qualify and decode an offline protocol reply against the bound Setting."""
        retained = self._event_receipts.get(session_id) if session_id is not None else None
        context = retained or self._local_inflight
        if (context is None or context.wire_dt is None or context.session_id != session_id
                or time.monotonic() - context.selected_at >= self._inflight_timeout_s
                or self.profile_status(context.device_id) != context.profile_status):
            return None
        profile = context.profile_status.profile
        fields = parse_setting_reply(raw, context.device_id, profile) if profile is not None else None
        if fields is None or context.wire_counters is None:
            return None
        table, key = context.request.table, context.request.key
        if not self._reply_fields_match(fields, table, key, context.wire_counters):
            return None
        if retained is not None and fields["Result"] != "ACK":
            return None
        named = fields.get("TblName") == table and (
            fields.get("ToDo") == key or fields.get("TblItem") == key
        )
        if fields.get("Reason") != "Setting" and not named:
            return None
        if "Rdt" in fields:
            stamp = self._device_time(fields["Rdt"])
            if (stamp is None or stamp.tzinfo != context.wire_dt.tzinfo
                    or stamp < context.wire_dt - timedelta(seconds=2)):
                return None
        reply = {"result": fields["Result"], "reason": fields.get("Reason", "")}
        if named:
            reply.update(table=table, todo=key)
        if retained is not None and session_id is not None:
            self._event_receipts.pop(session_id, None)
            reply["event_confirmed"] = "true"
        return reply

    def acknowledge(self, table: str, key: str, session_id: str | None = None,
                    *, result: str = "ack", evidence: str = "protocol_ack", reason: str = "") -> bool:
        """Acknowledge only a bound local request on its actual sending session."""
        if result not in {"ack", "nack"}:
            raise ValueError("Acknowledgment result must be ack or nack")
        self._expire_local_inflight()
        context = self._local_inflight
        if (context is None or context.wire_dt is None or context.session_id != session_id
                or (context.request.table, context.request.key) != (table, key)
                or context.reply_ambiguous or context.session_id in self._generic_reply_quarantine
                or self.profile_status(context.device_id) != context.profile_status):
            return False
        self._emit_result(context.request.as_setting(), result, source="local",
                          evidence=evidence, reason=reason)
        if context.session_id is not None:
            self._generic_reply_quarantine.add(context.session_id)
        return self._finish_local()

    def acknowledge_local_event(self, device_id: str, table: str, key: str,
                                value: Any, source_dt: str | None = None,
                                *, event_id_set: int | None = None,
                                session_id: str | None = None) -> bool:
        """Accept only uniquely attributable, unconsumed value-bearing BOX evidence."""
        self.poll_timeouts()
        context = self._local_inflight
        stamp = self._device_time(source_dt)
        if context is None or stamp is None:
            return False
        owner = self._event_owner(device_id, table, key, value, stamp, event_id_set, session_id)
        if owner is None or owner.source != "local" or owner.request is not context:
            return False
        self._consume_event(device_id, table, key, value, stamp, event_id_set)
        self._record_audit_step(context.request.as_setting(), device_id,
                                SettingStep.ACK_TBL_EVENTS, confirmed_value=value,
                                session_id=context.session_id or "")
        self._emit_result(context.request.as_setting(), "ack", source="local", evidence="setting_event")
        if context.session_id is not None:
            self._generic_reply_quarantine.add(context.session_id)
            if context.retain_receipt:
                self._event_receipts[context.session_id] = context
        return self._finish_local()

    def _clear_global_inflight(self) -> None:
        self._inflight_key = None
        self._inflight_device_id = None
        self._inflight_since = None
        self._local_inflight = None
        self._session_inflight.clear()

    def clear_session(self, session_id: str) -> None:
        self._profiles.close_session(session_id)
        self._cloud_legacy_sessions.discard(session_id)
        self._used_local_sessions.discard(session_id)
        self._generic_reply_quarantine.discard(session_id)
        self._event_receipts.pop(session_id, None)
        context = self._local_inflight
        if context is not None and context.session_id == session_id:
            self._record_audit_step(context.request.as_setting(), context.device_id,
                                    SettingStep.SESSION_CLEARED, session_id=session_id)
            self._session_inflight.pop(session_id, None)
            if context.wire_dt is None:
                self._twin_queue.discard(context.request.source, "session_closed")
                self._finish_local()
            # Keep sent requests until their timeout; never replay on reconnect.

    def record_injected_box(
        self,
        setting: TwinSetting,
        device_id: str,
        session_id: str = "",
    ) -> None:
        context = self._local_inflight
        if (context is not None and context.wire_dt is not None and context.injected_at is None
                and context.device_id == device_id and context.session_id == (session_id or None)
                and setting == context.request.as_setting()):
            self._local_inflight = replace(context, injected_at=time.monotonic())
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
        context = self._local_inflight
        if context is not None:
            self._record_audit_step(context.request.as_setting(), context.device_id,
                                    SettingStep.SESSION_CLEARED,
                                    session_id=context.session_id or "")
            if context.wire_dt is None:
                self._twin_queue.discard(context.request.source, "shutdown")
            else:
                self._emit_result(context.request.as_setting(), "timeout", source="local",
                                  evidence="no_ack", reason="shutdown")
            self._finish_local()
        for pending in list(self._iter_cloud_pending()):
            self._record_audit_step(pending.setting, pending.device_id, SettingStep.SESSION_CLEARED)
            self._emit_result(pending.setting, "timeout", source="cloud", evidence="no_ack", reason="shutdown")
            self._remove_cloud_pending(pending)
        self._twin_queue.clear(reason="shutdown")
        self._event_receipts.clear()

    def control_inflight_snapshot(self) -> str:
        """Read status without expiring requests, emitting callbacks or auditing."""
        cloud = self._oldest_cloud_pending()
        if cloud is not None:
            setting = cloud.setting
            return f"{setting.table}:{setting.key}={setting.value}"
        local = self._local_inflight
        if local is None:
            return ""
        request = local.request
        return f"{request.table}:{request.key}={request.value}"

    def inflight(self) -> tuple[str, str] | None:
        """Preserve the cloud-preferred public compatibility view."""
        pending = self.inflight_setting()
        return (pending[0].table, pending[0].key) if pending is not None else None

    def local_inflight_setting(self) -> tuple[TwinSetting, str] | None:
        """Return an isolated local snapshot, independent of cloud pending/settings replacement."""
        self._expire_local_inflight()
        context = self._local_inflight
        if context is None:
            return None
        return context.request.as_setting(), context.device_id

    def inflight_setting(self) -> tuple[TwinSetting, str] | None:
        """Return the legacy cloud-preferred view without aliasing selected local data."""
        self._expire_cloud_pending()
        pending = self._oldest_cloud_pending()
        if pending is not None:
            return pending.setting, pending.device_id
        return self.local_inflight_setting()

    def has_pending_or_inflight(self, session_id: str | None = None) -> bool:
        """Check if there are pending or inflight settings."""
        self._expire_cloud_pending()
        if session_id is not None:
            if session_id in self._session_inflight:
                return True
        return (bool(self._cloud_legacy_sessions) or bool(self._iter_cloud_pending())
                or self._local_inflight is not None or self._twin_queue.size() > 0)

    @staticmethod
    def _wire_counter_matches(text: str, value: int | None) -> bool:
        return text.isascii() and text.isdecimal() and len(text) <= 10 and int(text) == value

    @classmethod
    def _reply_fields_match(cls, fields: dict[str, str], table: str, key: str,
                            counters: _CounterState) -> bool:
        for name, expected in {"TblName": table, "ToDo": key, "TblItem": key}.items():
            if name in fields and fields[name] != expected:
                return False
        return all(name not in fields or cls._wire_counter_matches(fields[name], expected)
                   for name, expected in {"ID": counters.msg_id, "ID_Set": counters.id_set}.items())

    def _observe_cloud_counters(self, fields: dict[str, str], device_id: str,
                                msg_id: int, id_set: int) -> None:
        if (fields.get("ID_Device") == device_id and fields.get("Reason") == "Setting"
                and self._valid_counter(msg_id, _MAX_MSG_ID) and self._valid_counter(id_set, _MAX_ID_SET)
                and self._wire_counter_matches(fields.get("ID", ""), msg_id)
                and self._wire_counter_matches(fields.get("ID_Set", ""), id_set)):
            try:
                self._observe_counters(device_id, msg_id, id_set)
            except ValueError:
                logger.warning("Could not preserve Cloud Setting counters for %s", device_id, exc_info=True)

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
        session_id: str | None = None,
    ) -> None:
        """Create audit-backed inflight state for a cloud-originated setting."""
        self._expire_cloud_pending()
        self._cloud_generations[self._cloud_pending_key(device_id, table, key)] += 1

        fields = native_frame_fields(raw_text.encode("utf-8"), allow_repeated_identity=False)
        wire_dt = None
        if (fields is not None and fields.get("ID_Device") == device_id
                and fields.get("Reason") == "Setting" and fields.get("TblName") == table
                and fields.get("TblItem") == key and self._same_typed_value(value, fields.get("NewValue"))):
            wire_dt = self._device_time(fields.get("DT"))
            self._observe_cloud_counters(fields, device_id, msg_id, id_set)

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
            device_id=device_id,
        )
        queue_key = self._cloud_pending_key(device_id, table, key)
        self._cloud_pending[queue_key].append(
            _CloudPendingSetting(
                setting=setting,
                device_id=device_id,
                tracked_at=time.monotonic(),
                wire_dt=wire_dt,
                session_id=session_id or None,
            )
        )
        self._quarantine_overlaps(self._cloud_pending[queue_key][-1])
        logger.debug("TwinDelivery: cloud setting tracked as inflight %s:%s", table, key)

    def _cloud_reply_candidates(self, device_id: str, session_id: str,
                                reply_fields: dict[str, str] | None) -> list[_CloudPendingSetting]:
        """Resolve valueless replies only through their exact bound sending session."""
        session = session_id or None
        if session in self._generic_reply_quarantine or not device_id and session is None:
            return []
        candidates = [pending for pending in self._iter_cloud_pending()
                      if pending.session_id == session and pending.wire_dt is not None
                      and not pending.reason_setting_seen
                      and not pending.reply_ambiguous
                      and (not device_id or pending.device_id == device_id)
                      and (reply_fields is None or self._reply_fields_match(
                          reply_fields, pending.setting.table, pending.setting.key,
                          _CounterState(pending.setting.msg_id, pending.setting.id_set),
                      ))]
        if len(candidates) != 1:
            return []
        return candidates

    def mark_cloud_reason_setting(
        self,
        device_id: str,
        session_id: str = "",
        *,
        reply_fields: dict[str, str] | None = None,
    ) -> tuple[TwinSetting, str] | None:
        self._expire_cloud_pending()
        candidates = self._cloud_reply_candidates(device_id, session_id, reply_fields)
        pending = min(candidates, key=lambda item: item.tracked_at) if candidates else None
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
            self._emit_result(pending.setting, "ack", source="cloud", evidence="protocol_ack")
        return pending.setting, pending.device_id

    def acknowledge_cloud(self, device_id: str, *, session_id: str = "",
                          result: str = "nack", evidence: str = "protocol_nack", reason: str = "",
                          reply_fields: dict[str, str] | None = None) -> bool:
        """Finish only the matching cloud session's oldest request on protocol rejection."""
        if result not in {"ack", "nack"}:
            raise ValueError("Acknowledgment result must be ack or nack")
        self._expire_cloud_pending()
        candidates = self._cloud_reply_candidates(device_id, session_id, reply_fields)
        if not candidates:
            return False
        pending = min(candidates, key=lambda item: item.tracked_at)
        self._emit_result(pending.setting, result, source="cloud", evidence=evidence, reason=reason)
        if result == "nack":
            self.record_nack(pending.setting, pending.device_id, session_id=session_id)
        self._remove_cloud_pending(pending)
        return True

    def match_cloud_tbl_events(
        self,
        device_id: str,
        table: str,
        key: str,
        confirmed_value: Any,
        session_id: str = "",
        source_dt: str | None = None,
        event_id_set: int | None = None,
    ) -> tuple[TwinSetting, str] | None:
        """Retire one uniquely attributable cloud request without double-consuming events."""
        self.poll_timeouts()
        stamp = self._device_time(source_dt)
        if stamp is None:
            return None
        owner = self._event_owner(device_id, table, key, confirmed_value, stamp, event_id_set, session_id)
        if owner is None or owner.source != "cloud":
            return None
        match = owner.request
        self._consume_event(device_id, table, key, confirmed_value, stamp, event_id_set)
        self.record_ack_tbl_events(match.setting, match.device_id,
                                  confirmed_value=confirmed_value, session_id=session_id)
        self._emit_result(match.setting, "ack", source="cloud", evidence="setting_event")
        self._remove_cloud_pending(match)
        return match.setting, match.device_id

    def set_cloud_inflight(self, session_id: str | None = None) -> None:
        """Mark the legacy cloud handling flag for a single transport session."""
        self._cloud_legacy_sessions.add(session_id)

    def clear_cloud_inflight(self, session_id: str | None = None) -> None:
        """Clear the matching legacy flag and keep unresolved tracked requests."""
        self._cloud_legacy_sessions.discard(session_id)
        self._expire_cloud_pending()

    def is_cloud_inflight(self, device_id: str | None = None) -> bool:
        """Check whether an applicable Cloud Setting is unresolved."""
        self._expire_cloud_pending()
        return bool(self._cloud_legacy_sessions) or self._oldest_cloud_pending(device_id) is not None

    def has_pending(self, device_id: str | None = None) -> bool:
        """Check whether an applicable local request is waiting."""
        return self._twin_queue.size(device_id=device_id) > 0
