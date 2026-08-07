from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from protocol.frames import build_setting_frame, czech_local_datetime_from_epoch
from protocol.frame import FrameDirection
from telemetry.settings_audit import (
    TERMINAL_STEPS,
    SettingResult,
    SettingStep,
    _normalize_value_for_text,
    is_stronger_ack,
    make_incoming_record,
    make_step_record,
)
from .state import (
    ActiveLocalAttempt,
    AttemptRenderContext,
    AttemptRenderer,
    AttemptWriteOutcome,
    ClaimDisposition,
    CommandState,
    DeliveryDecision,
    DeliveryDisposition,
    DeliveryTrigger,
    EvidenceContext,
    EventMatchResult,
    LocalResponseDecision,
    LocalResponseDisposition,
    LocalSettingWriter,
    RetryReason,
    RegisteredEventToken,
    RenderedAttempt,
    StoreStatus,
    SweepReport,
    TransitionAuditSnapshot,
)
from .ack_parser import SettingEvent, SettingResponse
from .store import StaleAttemptError, TwinCommandStore

if TYPE_CHECKING:
    from ..mqtt.client import MQTTClient
    from telemetry.collector import TelemetryCollector
    from .state import TwinQueue, TwinSetting


logger = logging.getLogger(__name__)


class TwinCoordinator:
    """Serialize durable local-setting delivery per exact device."""

    def __init__(
        self,
        store: TwinCommandStore,
        *,
        control_enabled: bool = True,
        renderer: AttemptRenderer | None = None,
        audit_publisher: Any | None = None,
        clock_ms: Callable[[], int] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._control_enabled = control_enabled
        self._renderer = renderer or self._render_attempt
        self._audit = audit_publisher
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._monotonic = monotonic or time.monotonic
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._registered_events: dict[str, RegisteredEventToken] = {}
        self._event_timeout_candidates: dict[
            str, tuple[str, int, float]
        ] = {}
        self._cached_status = StoreStatus(
            tuple((state, 0) for state in CommandState),
            0,
            bool(control_enabled),
            None,
        )

    @staticmethod
    def _render_attempt(context: AttemptRenderContext) -> RenderedAttempt:
        """Render one attempt with bounded random version selection."""
        used = frozenset(context.used_ver_texts)
        selected: int | None = None
        for _ in range(16):
            candidate = secrets.randbelow(65_536)
            if f"{candidate:05d}" not in used:
                selected = candidate
                break
        if selected is None:
            selected = next(
                (
                    candidate
                    for candidate in range(65_536)
                    if f"{candidate:05d}" not in used
                ),
                None,
            )
        if selected is None:
            raise ValueError("all attempt version values are exhausted")
        tsec_text = datetime.fromtimestamp(
            context.prepared_at_ms / 1000, timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")
        rendered = build_setting_frame(
            device_id=context.command.device_id,
            table_name=context.command.table_name,
            item_name=context.command.item_name,
            value_text=context.command.value_text,
            wire_id=context.wire_id,
            wire_id_set=context.wire_id_set,
            wire_dt=context.wire_dt,
            tsec_text=tsec_text,
            ver_text=f"{selected:05d}",
        )
        return RenderedAttempt(
            tsec_text=tsec_text,
            ver_text=f"{selected:05d}",
            crc_text=rendered.crc_text,
            wire_frame=rendered.wire_frame,
        )

    @property
    def store(self) -> TwinCommandStore:
        """Expose the durable repository for composition and diagnostics."""
        return self._store

    @property
    def cached_status_snapshot(self) -> StoreStatus:
        """Return passive, potentially stale status without blocking."""
        return self._cached_status

    def _device_lock(self, device_id: str) -> asyncio.Lock:
        lock = self._device_locks.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            self._device_locks[device_id] = lock
        return lock

    def _publish(self, snapshots: tuple[TransitionAuditSnapshot, ...]) -> None:
        if self._audit is None:
            return
        for snapshot in snapshots:
            self._audit.publish_committed(snapshot)

    async def _refresh_status(self) -> None:
        try:
            self._cached_status = await asyncio.to_thread(self._store.status_snapshot)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("TwinCoordinator: passive status refresh failed", exc_info=True)

    @staticmethod
    def _active_from_claim(claim: Any) -> ActiveLocalAttempt:
        if claim.command is None or claim.attempt is None:
            raise RuntimeError("prepared claim omitted durable attempt")
        return ActiveLocalAttempt(
            command_id=claim.command.command_id,
            audit_id=claim.command.audit_id,
            device_id=claim.command.device_id,
            attempt_number=claim.attempt.attempt_number,
            session_id=claim.attempt.session_id,
            ack_deadline_ms=claim.attempt.ack_deadline_ms,
            wire_frame=claim.attempt.wire_frame,
            write_outcome=claim.attempt.write_outcome,
        )

    async def claim_and_write_next(
        self,
        *,
        device_id: str,
        session_id: str,
        received_at_ms: int,
        trigger: DeliveryTrigger | None,
        writer: LocalSettingWriter,
    ) -> DeliveryDecision:
        """Prepare, start, write, and drain one authorized attempt in order."""
        if not self._control_enabled:
            return DeliveryDecision(
                DeliveryDisposition.CONTROL_DISABLED, None, False
            )
        if not isinstance(trigger, DeliveryTrigger):
            return DeliveryDecision(DeliveryDisposition.UNAUTHORIZED, None, False)
        async with self._device_lock(device_id):
            claim = await asyncio.to_thread(
                self._store.prepare_next_attempt,
                device_id=device_id,
                session_id=session_id,
                prepared_at_ms=received_at_ms,
                render=self._renderer,
            )
            self._publish(claim.snapshots)
            if claim.disposition is not ClaimDisposition.PREPARED:
                await self._refresh_status()
                mapping = {
                    ClaimDisposition.NO_ELIGIBLE: DeliveryDisposition.NO_ELIGIBLE,
                    ClaimDisposition.ACTIVE_DELIVERY_ELSEWHERE: (
                        DeliveryDisposition.ACTIVE_DELIVERY_ELSEWHERE
                    ),
                    ClaimDisposition.CONTROL_DISABLED: (
                        DeliveryDisposition.CONTROL_DISABLED
                    ),
                    ClaimDisposition.RENDER_FAILED: DeliveryDisposition.RENDER_FAILED,
                }
                return DeliveryDecision(
                    mapping[claim.disposition], None, False, claim.snapshots
                )
            active = self._active_from_claim(claim)
            committed = list(claim.snapshots)

            async def before_write() -> None:
                prepared_at_ms = (
                    active.ack_deadline_ms - self._store.policy.ack_timeout_ms
                )
                snapshot = await asyncio.to_thread(
                    self._store.mark_write_started,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    started_at_ms=max(self._clock_ms(), prepared_at_ms),
                )
                self._publish((snapshot,))
                committed.append(snapshot)

            result = await writer.write_attempt(active, before_write=before_write)
            if result.outcome is AttemptWriteOutcome.FAILED:
                if not result.error_text:
                    raise ValueError("failed writer result requires an error")
                failed = await asyncio.to_thread(
                    self._store.mark_write_failed,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    occurred_at_ms=result.started_at_ms,
                    error=result.error_text,
                )
                self._publish((failed,))
                committed.append(failed)
                await self._refresh_status()
                return DeliveryDecision(
                    DeliveryDisposition.WRITE_FAILED,
                    None,
                    True,
                    tuple(committed),
                )
            if result.outcome is AttemptWriteOutcome.UNKNOWN:
                if not result.error_text:
                    raise ValueError("unknown writer result requires an error")
                unknown = await asyncio.to_thread(
                    self._store.mark_write_unknown,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    occurred_at_ms=result.started_at_ms,
                    error=result.error_text,
                )
                self._publish((unknown,))
                committed.append(unknown)
                await self._refresh_status()
                return DeliveryDecision(
                    DeliveryDisposition.WRITE_UNKNOWN,
                    None,
                    True,
                    tuple(committed),
                )
            if result.outcome is not AttemptWriteOutcome.DRAINED:
                raise ValueError("writer result must be failed, unknown, or drained")
            if result.drain_completed_at_ms is None:
                raise ValueError("drained writer result requires completion time")
            drained = await asyncio.to_thread(
                self._store.mark_attempt_drained,
                command_id=active.command_id,
                attempt_number=active.attempt_number,
                session_id=active.session_id,
                drained_at_ms=result.drain_completed_at_ms,
            )
            self._publish((drained,))
            committed.append(drained)
            await self._refresh_status()
            written = ActiveLocalAttempt(
                active.command_id,
                active.audit_id,
                active.device_id,
                active.attempt_number,
                active.session_id,
                active.ack_deadline_ms,
                active.wire_frame,
                AttemptWriteOutcome.DRAINED,
            )
            return DeliveryDecision(
                DeliveryDisposition.SENT,
                written,
                False,
                tuple(committed),
            )

    async def abort_dialogue(
        self,
        *,
        active: ActiveLocalAttempt,
        occurred_at_ms: int,
        reason: RetryReason,
    ) -> TransitionAuditSnapshot:
        """Durably release one exact active dialogue for retry or failure."""
        async with self._device_lock(active.device_id):
            snapshot = await asyncio.to_thread(
                self._store.release_for_retry,
                command_id=active.command_id,
                attempt_number=active.attempt_number,
                session_id=active.session_id,
                occurred_at_ms=occurred_at_ms,
                reason=reason,
            )
            self._publish((snapshot,))
            await self._refresh_status()
            return snapshot

    async def _reject_response_locked(
        self,
        *,
        active: ActiveLocalAttempt,
        occurred_at_ms: int,
        reason: RetryReason,
        disposition: LocalResponseDisposition,
    ) -> LocalResponseDecision:
        snapshots: tuple[TransitionAuditSnapshot, ...] = ()
        command = None
        try:
            snapshot = await asyncio.to_thread(
                self._store.release_for_retry,
                command_id=active.command_id,
                attempt_number=active.attempt_number,
                session_id=active.session_id,
                occurred_at_ms=occurred_at_ms,
                reason=reason,
            )
        except StaleAttemptError:
            try:
                command = await asyncio.to_thread(
                    self._store.read_command, active.command_id
                )
            except Exception:  # pylint: disable=broad-exception-caught
                command = None
        else:
            command = snapshot.command
            snapshots = (snapshot,)
            self._publish(snapshots)
        await self._refresh_status()
        return LocalResponseDecision(
            disposition,
            command,
            None,
            False,
            True,
            snapshots=snapshots,
        )

    async def _write_prepared_successor_locked(
        self,
        *,
        active: ActiveLocalAttempt,
        writer: LocalSettingWriter,
        committed: list[TransitionAuditSnapshot],
    ) -> ActiveLocalAttempt | None:
        async def before_write() -> None:
            prepared_at_ms = (
                active.ack_deadline_ms - self._store.policy.ack_timeout_ms
            )
            snapshot = await asyncio.to_thread(
                self._store.mark_write_started,
                command_id=active.command_id,
                attempt_number=active.attempt_number,
                session_id=active.session_id,
                started_at_ms=max(self._clock_ms(), prepared_at_ms),
            )
            committed.append(snapshot)
            self._publish((snapshot,))

        result = await writer.write_attempt(active, before_write=before_write)
        if result.outcome is AttemptWriteOutcome.DRAINED:
            if result.drain_completed_at_ms is None:
                raise ValueError("drained writer result requires completion time")
            snapshot = await asyncio.to_thread(
                self._store.mark_attempt_drained,
                command_id=active.command_id,
                attempt_number=active.attempt_number,
                session_id=active.session_id,
                drained_at_ms=result.drain_completed_at_ms,
            )
            committed.append(snapshot)
            self._publish((snapshot,))
            return ActiveLocalAttempt(
                active.command_id,
                active.audit_id,
                active.device_id,
                active.attempt_number,
                active.session_id,
                active.ack_deadline_ms,
                active.wire_frame,
                AttemptWriteOutcome.DRAINED,
            )
        if not result.error_text:
            raise ValueError("non-drained writer result requires an error")
        method = (
            self._store.mark_write_failed
            if result.outcome is AttemptWriteOutcome.FAILED
            else self._store.mark_write_unknown
        )
        keyword = "error"
        snapshot = await asyncio.to_thread(
            method,
            command_id=active.command_id,
            attempt_number=active.attempt_number,
            session_id=active.session_id,
            occurred_at_ms=result.started_at_ms,
            **{keyword: result.error_text},
        )
        committed.append(snapshot)
        self._publish((snapshot,))
        return None

    async def handle_local_response(
        self,
        *,
        active: ActiveLocalAttempt,
        response: SettingResponse,
        context: EvidenceContext,
        writer: LocalSettingWriter,
    ) -> LocalResponseDecision:
        """Correlate one exact local response and continue only atomic ACK work."""
        async with self._device_lock(active.device_id):
            correlated = (
                context.direction is FrameDirection.BOX_TO_PROXY
                and context.session_id == active.session_id
                and context.device_id == active.device_id
                and response.fingerprint
                == hashlib.sha256(context.raw_frame).hexdigest()
            )
            if not correlated:
                return await self._reject_response_locked(
                    active=active,
                    occurred_at_ms=context.received_at_ms,
                    reason=RetryReason.UNEXPECTED_RESPONSE,
                    disposition=LocalResponseDisposition.REJECTED,
                )
            if context.received_at_ms > active.ack_deadline_ms:
                return await self._reject_response_locked(
                    active=active,
                    occurred_at_ms=context.received_at_ms,
                    reason=RetryReason.ACK_TIMEOUT,
                    disposition=LocalResponseDisposition.TIMED_OUT,
                )
            try:
                if response.result == "NACK":
                    nack = await asyncio.to_thread(
                        self._store.mark_nack,
                        command_id=active.command_id,
                        attempt_number=active.attempt_number,
                        session_id=active.session_id,
                        response=response,
                        received_at_ms=context.received_at_ms,
                        evidence_frame=context.raw_frame,
                    )
                    if nack.duplicate:
                        return LocalResponseDecision(
                            LocalResponseDisposition.DUPLICATE,
                            None,
                            None,
                            False,
                            False,
                        )
                    self._publish(nack.snapshots)
                    await self._refresh_status()
                    return LocalResponseDecision(
                        LocalResponseDisposition.NACK_ACCEPTED,
                        nack.accepted_command,
                        None,
                        True,
                        False,
                        snapshots=nack.snapshots,
                    )
                ack = await asyncio.to_thread(
                    self._store.acknowledge_and_prepare_next,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    response=response,
                    received_at_ms=context.received_at_ms,
                    evidence_frame=context.raw_frame,
                    render=self._renderer,
                )
            except (StaleAttemptError, ValueError):
                return await self._reject_response_locked(
                    active=active,
                    occurred_at_ms=context.received_at_ms,
                    reason=RetryReason.UNEXPECTED_RESPONSE,
                    disposition=LocalResponseDisposition.REJECTED,
                )
            if ack.duplicate:
                return LocalResponseDecision(
                    LocalResponseDisposition.DUPLICATE,
                    None,
                    None,
                    False,
                    False,
                )
            committed = list(ack.snapshots)
            self._publish(ack.snapshots)
            next_attempt = None
            close_connection = False
            if ack.next_claim.disposition is ClaimDisposition.PREPARED:
                prepared = self._active_from_claim(ack.next_claim)
                next_attempt = await self._write_prepared_successor_locked(
                    active=prepared,
                    writer=writer,
                    committed=committed,
                )
                close_connection = next_attempt is None
            await self._refresh_status()
            return LocalResponseDecision(
                (
                    LocalResponseDisposition.NEXT_SENT
                    if next_attempt is not None
                    else LocalResponseDisposition.ACK_ACCEPTED
                ),
                ack.accepted_command,
                next_attempt,
                next_attempt is None and not close_connection,
                close_connection,
                snapshots=tuple(committed),
            )

    def register_setting_event(
        self,
        *,
        event: SettingEvent,
        context: EvidenceContext,
    ) -> RegisteredEventToken:
        """Synchronously reserve exact event evidence before a later await."""
        if context.direction is not FrameDirection.BOX_TO_PROXY:
            raise ValueError("setting events must be BOX-to-proxy evidence")
        if context.device_id != event.device_id:
            raise ValueError("setting event device does not match context device")
        token = RegisteredEventToken(str(uuid.uuid4()), event, context)
        self._registered_events[token.token_id] = token
        return token

    async def handle_registered_event(
        self, token: RegisteredEventToken
    ) -> EventMatchResult:
        """Commit one reserved event under its exact-device lock."""
        registered = self._registered_events.get(token.token_id)
        if registered != token:
            raise ValueError("setting event token is not registered")
        async with self._device_lock(token.context.device_id):
            result = await asyncio.to_thread(
                self._store.record_event,
                evidence=token.event,
                received_at_ms=token.context.received_at_ms,
                evidence_frame=token.context.raw_frame,
                active_session_id=token.context.session_id,
            )
            self._registered_events.pop(token.token_id, None)
            if result.snapshot is not None:
                self._publish((result.snapshot,))
                self._event_timeout_candidates.pop(
                    result.snapshot.command.command_id, None
                )
            await self._refresh_status()
            return result

    async def flush_registered_events(
        self, *, session_id: str
    ) -> tuple[EventMatchResult, ...]:
        """Commit every reserved session event in synchronous receipt order."""
        tokens = tuple(
            token
            for token in self._registered_events.values()
            if token.context.session_id == session_id
        )
        results: list[EventMatchResult] = []
        for token in tokens:
            if token.token_id in self._registered_events:
                results.append(await self.handle_registered_event(token))
        return tuple(results)

    def _has_in_deadline_registered_event(
        self, *, device_id: str, event_deadline_ms: int
    ) -> bool:
        return any(
            token.context.device_id == device_id
            and token.context.received_at_ms <= event_deadline_ms
            for token in self._registered_events.values()
        )

    async def sweep_deadlines(self, *, now_ms: int | None = None) -> SweepReport:
        """Run pending/ACK sweep plus two-pass exact event-timeout grace."""
        effective_now = self._clock_ms() if now_ms is None else now_ms
        base = await asyncio.to_thread(
            self._store.sweep_deadlines,
            now_ms=effective_now,
            include_event_timeouts=False,
        )
        self._publish(base.snapshots)
        candidates = await asyncio.to_thread(
            self._store.read_event_timeout_candidates, now_ms=effective_now
        )
        current_ids = {candidate.command_id for candidate in candidates}
        for command_id in tuple(self._event_timeout_candidates):
            if command_id not in current_ids:
                self._event_timeout_candidates.pop(command_id, None)

        now_monotonic = self._monotonic()
        incomplete: list[TransitionAuditSnapshot] = []
        for candidate in candidates:
            prior = self._event_timeout_candidates.get(candidate.command_id)
            identity = (candidate.device_id, candidate.event_deadline_ms)
            if prior is None or prior[:2] != identity:
                self._event_timeout_candidates[candidate.command_id] = (
                    candidate.device_id,
                    candidate.event_deadline_ms,
                    now_monotonic,
                )
                continue
            if now_monotonic - prior[2] < 1.0:
                continue
            async with self._device_lock(candidate.device_id):
                if self._has_in_deadline_registered_event(
                    device_id=candidate.device_id,
                    event_deadline_ms=candidate.event_deadline_ms,
                ):
                    continue
                snapshot = await asyncio.to_thread(
                    self._store.mark_event_incomplete,
                    command_id=candidate.command_id,
                    expected_event_deadline_ms=candidate.event_deadline_ms,
                    now_ms=effective_now,
                )
                self._event_timeout_candidates.pop(candidate.command_id, None)
                if snapshot is not None:
                    incomplete.append(snapshot)
                    self._publish((snapshot,))
        await self._refresh_status()
        snapshots = tuple(
            sorted(
                (*base.snapshots, *incomplete),
                key=lambda snapshot: snapshot.transition.transition_id,
            )
        )
        return SweepReport(
            base.expired_pending,
            base.retry_pending,
            base.failed_attempt_limit,
            len(incomplete),
            snapshots,
        )

    async def status_snapshot(self, device_id: str | None = None) -> StoreStatus:
        """Read authoritative status off the event loop."""
        status = await asyncio.to_thread(self._store.status_snapshot, device_id)
        self._cached_status = status
        return status

    async def read_command(self, command_id: str) -> Any:
        """Read one immutable command off the event loop."""
        return await asyncio.to_thread(self._store.read_command, command_id)


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
            self._last_msg_id = secrets.randbelow(1_000_000) + 14_000_000
            return self._last_msg_id
        self._last_msg_id += 1
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

    def has_pending_or_inflight(self, session_id: str | None = None) -> bool:
        """Check if there are pending or inflight settings."""
        self._expire_cloud_pending()
        if session_id is not None:
            if session_id in self._session_inflight:
                return True
        return (
            self._cloud_legacy_inflight
            or bool(self._iter_cloud_pending())
            or self._inflight_key is not None
            or self._twin_queue.size() > 0
        )

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
            msg_id = secrets.randbelow(1_000_000) + 14_000_000

        now_utc = datetime.now(timezone.utc)
        now_epoch = int(now_utc.timestamp())
        tsec_utc = (
            now_utc
            if now_epoch >= id_set
            else datetime.fromtimestamp(id_set, tz=timezone.utc)
        )
        setting_dt_cz = czech_local_datetime_from_epoch(id_set)
        ver = secrets.randbelow(65_535)

        return (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{setting_dt_cz.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
            f"<NewValue>{value}</NewValue>"
            f"<Confirm>{confirm}</Confirm>"
            f"<TblName>{table}</TblName>"
            f"<TblItem>{key}</TblItem>"
            "<ID_Server>9</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{tsec_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
            f"<ver>{ver:05d}</ver>"
        )
