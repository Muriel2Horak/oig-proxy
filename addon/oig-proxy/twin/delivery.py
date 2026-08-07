from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
import hashlib
import logging
import secrets
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

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
    CommandAttempt,
    CommandTransition,
    CommandState,
    DeliveryDecision,
    DeliveryDisposition,
    DeliveryTrigger,
    EvidenceContext,
    EventMatchResult,
    EventTimeoutCandidate,
    LocalResponseDecision,
    LocalResponseDisposition,
    LocalSettingWriter,
    RetryReason,
    RegisteredEventToken,
    RenderedAttempt,
    StoreStatus,
    SweepReport,
    TransitionAuditSnapshot,
    TwinCommand,
)
from .ack_parser import SettingEvent, SettingResponse
from .store import (
    StaleAttemptError,
    TwinCommandStore,
    event_is_eligible_for_timeout_candidate,
)

if TYPE_CHECKING:
    from ..mqtt.client import MQTTClient
    from telemetry.collector import TelemetryCollector
    from .state import TwinQueue, TwinSetting


logger = logging.getLogger(__name__)

_COORDINATOR_MUTATION_EXECUTOR = ThreadPoolExecutor(
    max_workers=32,
    thread_name_prefix="twin-coordinator-mutation",
)


class _RegisteredEventConsumed(ValueError):
    """Signal that another handler already claimed an exact receipt token."""


class DeadlineSweepError(RuntimeError):
    """Aggregate settled device failures plus the committed partial report."""

    def __init__(
        self,
        failures: tuple[tuple[str, BaseException], ...],
        partial_report: SweepReport,
    ) -> None:
        devices = ", ".join(device_id for device_id, _error in failures)
        super().__init__(f"deadline sweep failed for devices: {devices}")
        self.failures = failures
        self.partial_report = partial_report


def _validate_device_deadline_snapshot(
    snapshot: TransitionAuditSnapshot,
) -> None:
    """Require the exact nested contract emitted by a device deadline sweep."""
    command = snapshot.command
    transition = snapshot.transition
    if snapshot.evidence is not None:
        raise RuntimeError("device sweep snapshot must not include event evidence")
    if transition.to_state is CommandState.EXPIRED:
        if (
            transition.from_state is not CommandState.PENDING
            or transition.reason != "pending_ttl_expired"
        ):
            raise RuntimeError("device sweep expiry origin is inconsistent")
        if (
            snapshot.attempt is not None
            or transition.attempt_number is not None
            or transition.session_id is not None
        ):
            raise RuntimeError("device sweep pending expiry gained an attempt")
        if (
            type(command.attempt_count) is not int
            or command.attempt_count != 0
            or command.active_session_id is not None
            or command.ack_deadline_ms is not None
        ):
            raise RuntimeError("device sweep pending command origin is inconsistent")
        return
    if transition.to_state not in {
        CommandState.RETRY_PENDING,
        CommandState.FAILED,
    }:
        raise RuntimeError("device sweep snapshot target is invalid")
    if (
        transition.from_state is not CommandState.AWAITING_ACK
        or transition.reason != RetryReason.ACK_TIMEOUT.value
    ):
        raise RuntimeError("device sweep retry origin is inconsistent")
    attempt = snapshot.attempt
    if type(attempt) is not CommandAttempt:
        raise RuntimeError("device sweep ACK timeout requires an exact attempt")
    attempt = cast(CommandAttempt, attempt)
    if (
        type(command.attempt_count) is not int
        or command.attempt_count < 1
        or type(attempt.attempt_number) is not int
        or attempt.attempt_number < 1
        or type(transition.attempt_number) is not int
        or transition.attempt_number < 1
    ):
        raise RuntimeError("device sweep ACK-timeout attempt number is invalid")
    if (
        type(attempt.session_id) is not str
        or not attempt.session_id
        or type(transition.session_id) is not str
        or not transition.session_id
    ):
        raise RuntimeError("device sweep ACK-timeout session is invalid")
    if (
        attempt.command_id != command.command_id
        or attempt.attempt_number != command.attempt_count
        or transition.attempt_number != attempt.attempt_number
        or transition.session_id != attempt.session_id
    ):
        raise RuntimeError("device sweep ACK-timeout identity is inconsistent")
    if (
        command.active_session_id is not None
        or command.ack_deadline_ms is not None
    ):
        raise RuntimeError("device sweep ACK-timeout ownership was not released")


def _validate_device_sweep_report(
    device_id: str, report: SweepReport
) -> None:
    """Reject malformed worker accounting before aggregate reconciliation."""
    if type(report) is not SweepReport:  # pylint: disable=unidiomatic-typecheck
        raise RuntimeError("device sweep report must have the exact type")
    if type(report.snapshots) is not tuple:  # pylint: disable=unidiomatic-typecheck
        raise RuntimeError("device sweep snapshots must be an exact tuple")
    counters = (
        report.expired_pending,
        report.retry_pending,
        report.failed_attempt_limit,
        report.incomplete_event_timeout,
    )
    if any(type(counter) is not int or counter < 0 for counter in counters):
        raise RuntimeError("device sweep counters must be nonnegative integers")
    if sum(counters) != len(report.snapshots):
        raise RuntimeError("device sweep counters do not match snapshot count")
    if report.incomplete_event_timeout != 0:
        raise RuntimeError("device sweep returned event-timeout work")
    previous_transition_id = 0
    transition_ids: set[int] = set()
    semantic_counters = {
        CommandState.EXPIRED: 0,
        CommandState.RETRY_PENDING: 0,
        CommandState.FAILED: 0,
    }
    for snapshot in report.snapshots:
        if type(snapshot) is not TransitionAuditSnapshot:
            raise RuntimeError("device sweep returned an invalid snapshot")
        if type(snapshot.command) is not TwinCommand:
            raise RuntimeError("device sweep returned an invalid command")
        if type(snapshot.transition) is not CommandTransition:
            raise RuntimeError("device sweep returned an invalid transition")
        command = snapshot.command
        transition = snapshot.transition
        if command.device_id != device_id:
            raise RuntimeError("device sweep returned another device's snapshot")
        if (
            command.command_id != transition.command_id
            or command.audit_id != transition.audit_id
        ):
            raise RuntimeError("device sweep snapshot identity is inconsistent")
        if command.state is not transition.to_state:
            raise RuntimeError("device sweep snapshot state is inconsistent")
        if transition.to_state not in semantic_counters:
            raise RuntimeError("device sweep snapshot target is invalid")
        _validate_device_deadline_snapshot(snapshot)
        semantic_counters[transition.to_state] += 1
        transition_id = transition.transition_id
        if type(transition_id) is not int or transition_id < 1:
            raise RuntimeError("device sweep transition ID must be positive")
        if transition_id in transition_ids:
            raise RuntimeError("device sweep transition IDs must be unique")
        if transition_id <= previous_transition_id:
            raise RuntimeError(
                "device sweep transition IDs must be strictly ascending"
            )
        transition_ids.add(transition_id)
        previous_transition_id = transition_id
    if (
        semantic_counters[CommandState.EXPIRED],
        semantic_counters[CommandState.RETRY_PENDING],
        semantic_counters[CommandState.FAILED],
    ) != counters[:3]:
        raise RuntimeError("device sweep counters do not match snapshot states")


@dataclass(slots=True)
class _RegisteredEventEntry:
    """Loop-owned lifecycle for one synchronously captured event receipt."""

    token: RegisteredEventToken
    receipt_sequence: int
    owner: asyncio.Task[Any] | None = None
    worker: asyncio.Task[EventMatchResult] | None = None
    result: EventMatchResult | None = None
    batch_owner: asyncio.Task[Any] | None = None
    batch_adopts_result: bool = False


@dataclass(frozen=True, slots=True)
class _EventTimeoutReservation:
    """Loop-owned ordering claim granted immediately before SQLite COMMIT."""

    command_id: str
    device_id: str
    table_name: str
    item_name: str
    value_text: str
    acked_at_ms: int
    ack_device_rdt: str | None
    event_deadline_ms: int


@dataclass(slots=True)
class _DeadlineDeviceLease:
    """Transfer one acquired device lock to the aggregate sweep owner."""

    device_id: str
    lock: asyncio.Lock
    acquired: bool = False


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
        self._deadline_sweep_lock = asyncio.Lock()
        self._registered_events: dict[str, _RegisteredEventEntry] = {}
        self._next_event_receipt_sequence = 1
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._event_timeout_reservations: dict[
            str, _EventTimeoutReservation
        ] = {}
        self._event_timeout_candidates: dict[
            str, tuple[EventTimeoutCandidate, float]
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

    def _bind_event_loop(self) -> asyncio.AbstractEventLoop:
        """Bind synchronous event registration to one owning event loop."""
        loop = asyncio.get_running_loop()
        if self._event_loop is None:
            self._event_loop = loop
        elif self._event_loop is not loop:
            raise RuntimeError("TwinCoordinator event state belongs to another loop")
        return loop

    async def _publish(
        self, snapshots: tuple[TransitionAuditSnapshot, ...]
    ) -> None:
        if self._audit is None:
            return
        cancellation_latched = False
        publication_error: BaseException | None = None
        for snapshot in snapshots:
            try:
                await self._audit.publish_committed_async(snapshot)
            except asyncio.CancelledError:
                cancellation_latched = True
            except BaseException as caught:  # pylint: disable=broad-exception-caught
                publication_error = caught
                break
        if cancellation_latched:
            raise asyncio.CancelledError() from publication_error
        if publication_error is not None:
            raise publication_error

    async def _refresh_status(self) -> None:
        worker = _COORDINATOR_MUTATION_EXECUTOR.submit(
            self._store.status_snapshot
        )
        status, error, cancellation_latched = await self._drain_future(worker)
        if error is None:
            self._cached_status = cast(StoreStatus, status)
        elif isinstance(error, Exception):
            logger.warning("TwinCoordinator: passive status refresh failed", exc_info=True)
        else:
            raise error
        if cancellation_latched:
            raise asyncio.CancelledError() from error

    @staticmethod
    async def _drain_future(
        future: Future[Any],
    ) -> tuple[Any | None, BaseException | None, bool]:
        """Drain an executor future whose completion survives task cancellation."""
        cancellation_latched = False
        wrapped = asyncio.wrap_future(future)
        while True:
            try:
                return await asyncio.shield(wrapped), None, cancellation_latched
            except asyncio.CancelledError as error:
                if wrapped.cancelled():
                    return None, error, cancellation_latched
                cancellation_latched = True
            except BaseException as error:  # pylint: disable=broad-exception-caught
                return None, error, cancellation_latched

    @staticmethod
    async def _drain_task(
        task: asyncio.Task[Any],
    ) -> tuple[Any | None, BaseException | None, bool]:
        """Wait for a task definitively while latching outer cancellation."""
        cancellation_latched = False
        while True:
            try:
                return await asyncio.shield(task), None, cancellation_latched
            except asyncio.CancelledError as error:
                if task.cancelled():
                    return None, error, cancellation_latched
                cancellation_latched = True
            except BaseException as error:  # pylint: disable=broad-exception-caught
                return None, error, cancellation_latched

    async def _run_mutation_locked(
        self,
        operation: Callable[[], Any],
        *,
        snapshots: Callable[[Any], tuple[TransitionAuditSnapshot, ...]],
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[BaseException], None] | None = None,
    ) -> Any:
        """Drain one device-owned store mutation before cancellation escapes."""
        worker = _COORDINATOR_MUTATION_EXECUTOR.submit(operation)
        result, error, cancellation_latched = await self._drain_future(worker)
        reconciliation_error: BaseException | None = None
        try:
            if error is None:
                if on_success is not None:
                    on_success(result)
                try:
                    await self._publish(snapshots(result))
                except asyncio.CancelledError as caught:
                    cancellation_latched = True
                    if (
                        reconciliation_error is None
                        and caught.__cause__ is not None
                    ):
                        reconciliation_error = caught.__cause__
            elif on_failure is not None:
                on_failure(error)
        except BaseException as caught:  # pylint: disable=broad-exception-caught
            reconciliation_error = caught
        while True:
            try:
                await self._refresh_status()
                break
            except asyncio.CancelledError as caught:
                cancellation_latched = True
                if (
                    reconciliation_error is None
                    and caught.__cause__ is not None
                ):
                    reconciliation_error = caught.__cause__
            except BaseException as caught:  # pylint: disable=broad-exception-caught
                if reconciliation_error is None:
                    reconciliation_error = caught
                break
        if reconciliation_error is not None:
            if cancellation_latched:
                raise asyncio.CancelledError() from reconciliation_error
            raise reconciliation_error
        if cancellation_latched:
            raise asyncio.CancelledError() from error
        if error is not None:
            raise error
        return result

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
            claim = await self._run_mutation_locked(
                partial(
                    self._store.prepare_next_attempt,
                    device_id=device_id,
                    session_id=session_id,
                    prepared_at_ms=received_at_ms,
                    render=self._renderer,
                ),
                snapshots=lambda settled: settled.snapshots,
            )
            if claim.disposition is not ClaimDisposition.PREPARED:
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
                snapshot = await self._run_mutation_locked(
                    partial(
                        self._store.mark_write_started,
                        command_id=active.command_id,
                        attempt_number=active.attempt_number,
                        session_id=active.session_id,
                        started_at_ms=max(self._clock_ms(), prepared_at_ms),
                    ),
                    snapshots=lambda settled: (settled,),
                )
                committed.append(snapshot)

            result = await writer.write_attempt(active, before_write=before_write)
            if result.outcome is AttemptWriteOutcome.FAILED:
                if not result.error_text:
                    raise ValueError("failed writer result requires an error")
                failed = await self._run_mutation_locked(
                    partial(
                        self._store.mark_write_failed,
                        command_id=active.command_id,
                        attempt_number=active.attempt_number,
                        session_id=active.session_id,
                        occurred_at_ms=result.started_at_ms,
                        error=result.error_text,
                    ),
                    snapshots=lambda settled: (settled,),
                )
                committed.append(failed)
                return DeliveryDecision(
                    DeliveryDisposition.WRITE_FAILED,
                    None,
                    True,
                    tuple(committed),
                )
            if result.outcome is AttemptWriteOutcome.UNKNOWN:
                if not result.error_text:
                    raise ValueError("unknown writer result requires an error")
                unknown = await self._run_mutation_locked(
                    partial(
                        self._store.mark_write_unknown,
                        command_id=active.command_id,
                        attempt_number=active.attempt_number,
                        session_id=active.session_id,
                        occurred_at_ms=result.started_at_ms,
                        error=result.error_text,
                    ),
                    snapshots=lambda settled: (settled,),
                )
                committed.append(unknown)
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
            drained = await self._run_mutation_locked(
                partial(
                    self._store.mark_attempt_drained,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    drained_at_ms=result.drain_completed_at_ms,
                ),
                snapshots=lambda settled: (settled,),
            )
            committed.append(drained)
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
            snapshot = await self._run_mutation_locked(
                partial(
                    self._store.release_for_retry,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    occurred_at_ms=occurred_at_ms,
                    reason=reason,
                ),
                snapshots=lambda settled: (settled,),
            )
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
            snapshot = await self._run_mutation_locked(
                partial(
                    self._store.release_for_retry,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    occurred_at_ms=occurred_at_ms,
                    reason=reason,
                ),
                snapshots=lambda settled: (settled,),
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
            snapshot = await self._run_mutation_locked(
                partial(
                    self._store.mark_write_started,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    started_at_ms=max(self._clock_ms(), prepared_at_ms),
                ),
                snapshots=lambda settled: (settled,),
            )
            committed.append(snapshot)

        result = await writer.write_attempt(active, before_write=before_write)
        if result.outcome is AttemptWriteOutcome.DRAINED:
            if result.drain_completed_at_ms is None:
                raise ValueError("drained writer result requires completion time")
            snapshot = await self._run_mutation_locked(
                partial(
                    self._store.mark_attempt_drained,
                    command_id=active.command_id,
                    attempt_number=active.attempt_number,
                    session_id=active.session_id,
                    drained_at_ms=result.drain_completed_at_ms,
                ),
                snapshots=lambda settled: (settled,),
            )
            committed.append(snapshot)
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
        snapshot = await self._run_mutation_locked(
            partial(
                method,
                command_id=active.command_id,
                attempt_number=active.attempt_number,
                session_id=active.session_id,
                occurred_at_ms=result.started_at_ms,
                **{keyword: result.error_text},
            ),
            snapshots=lambda settled: (settled,),
        )
        committed.append(snapshot)
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
            if response.result == "ACK" and response.reason != "Setting":
                return await self._reject_response_locked(
                    active=active,
                    occurred_at_ms=context.received_at_ms,
                    reason=RetryReason.UNEXPECTED_RESPONSE,
                    disposition=LocalResponseDisposition.REJECTED,
                )
            try:
                if response.result == "NACK":
                    nack = await self._run_mutation_locked(
                        partial(
                            self._store.mark_nack,
                            command_id=active.command_id,
                            attempt_number=active.attempt_number,
                            session_id=active.session_id,
                            response=response,
                            received_at_ms=context.received_at_ms,
                            evidence_frame=context.raw_frame,
                        ),
                        snapshots=lambda settled: settled.snapshots,
                    )
                    if nack.duplicate:
                        return LocalResponseDecision(
                            LocalResponseDisposition.DUPLICATE,
                            None,
                            None,
                            False,
                            False,
                        )
                    return LocalResponseDecision(
                        LocalResponseDisposition.NACK_ACCEPTED,
                        nack.accepted_command,
                        None,
                        True,
                        False,
                        snapshots=nack.snapshots,
                    )
                ack = await self._run_mutation_locked(
                    partial(
                        self._store.acknowledge_and_prepare_next,
                        command_id=active.command_id,
                        attempt_number=active.attempt_number,
                        session_id=active.session_id,
                        response=response,
                        received_at_ms=context.received_at_ms,
                        evidence_frame=context.raw_frame,
                        render=self._renderer,
                    ),
                    snapshots=lambda settled: settled.snapshots,
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
        self._bind_event_loop()
        token = RegisteredEventToken(str(uuid.uuid4()), event, context)
        sequence = self._next_event_receipt_sequence
        self._next_event_receipt_sequence += 1
        self._registered_events[token.token_id] = _RegisteredEventEntry(
            token,
            sequence,
        )
        return token

    async def _process_registered_event(
        self, entry: _RegisteredEventEntry
    ) -> EventMatchResult:
        token = entry.token

        def retain_settled_result(settled: EventMatchResult) -> None:
            current = self._registered_events.get(token.token_id)
            if current is entry:
                entry.result = settled
            if settled.snapshot is not None:
                self._event_timeout_candidates.pop(
                    settled.snapshot.command.command_id,
                    None,
                )

        try:
            async with self._device_lock(token.context.device_id):
                result = await self._run_mutation_locked(
                    partial(
                        self._store.record_event,
                        evidence=token.event,
                        received_at_ms=token.context.received_at_ms,
                        evidence_frame=token.context.raw_frame,
                        active_session_id=token.context.session_id,
                    ),
                    snapshots=lambda settled: (
                        (settled.snapshot,)
                        if settled.snapshot is not None
                        else ()
                    ),
                    on_success=retain_settled_result,
                )
        except BaseException:  # pylint: disable=broad-exception-caught
            current = self._registered_events.get(token.token_id)
            if current is entry:
                entry.owner = None
                entry.worker = None
            raise
        current = self._registered_events.get(token.token_id)
        if current is entry:
            entry.result = result
            entry.worker = None
        return result

    async def _await_registered_owner(
        self,
        entry: _RegisteredEventEntry,
        owner: asyncio.Task[Any],
    ) -> EventMatchResult:
        worker = entry.worker
        if worker is None:
            raise RuntimeError("registered event claim omitted its worker")
        cancellation_latched = False
        while True:
            try:
                result = await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                cancellation_latched = True
                current = self._registered_events.get(entry.token.token_id)
                if current is entry and entry.owner is owner:
                    entry.owner = None
                if worker.cancelled():
                    if current is entry:
                        entry.worker = None
                    raise
            except BaseException as error:  # pylint: disable=broad-exception-caught
                if cancellation_latched:
                    raise asyncio.CancelledError() from error
                raise
        current = self._registered_events.get(entry.token.token_id)
        if cancellation_latched:
            if current is entry and entry.owner is owner:
                entry.owner = None
        elif current is entry and entry.owner is owner:
            entry.owner = None
            if entry.batch_owner is None or not entry.batch_adopts_result:
                self._registered_events.pop(entry.token.token_id)
        if cancellation_latched:
            raise asyncio.CancelledError()
        return result

    async def handle_registered_event(
        self, token: RegisteredEventToken
    ) -> EventMatchResult:
        """Commit one reserved event under its exact-device lock."""
        self._bind_event_loop()
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("registered event handling requires an asyncio task")
        entry = self._registered_events.get(token.token_id)
        if entry is None or entry.token != token:
            raise _RegisteredEventConsumed(
                "setting event token is not registered"
            )
        if (
            entry.owner is not None
            or entry.worker is not None
            or entry.result is not None
            or entry.batch_owner is not None
        ):
            raise _RegisteredEventConsumed(
                "setting event token is already claimed"
            )
        entry.owner = owner
        entry.worker = asyncio.create_task(
            self._process_registered_event(entry)
        )
        return await self._await_registered_owner(entry, owner)

    async def _flush_registered_entry(
        self,
        entry: _RegisteredEventEntry,
        batch_owner: asyncio.Task[Any],
    ) -> EventMatchResult | None:
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("registered event flush requires an asyncio task")
        current = self._registered_events.get(entry.token.token_id)
        if current is not entry or entry.batch_owner is not batch_owner:
            return None
        if entry.owner is not None and entry.owner is not owner:
            prior_owner = entry.owner
            _result, owner_error, cancellation_latched = await self._drain_task(
                prior_owner
            )
            if cancellation_latched:
                raise asyncio.CancelledError() from owner_error
            current = self._registered_events.get(entry.token.token_id)
            if owner_error is None:
                if current is entry:
                    raise RuntimeError(
                        "registered event owner completed without consumption"
                    )
                return None
            if not isinstance(owner_error, asyncio.CancelledError):
                raise owner_error
            if current is not entry:
                return None
            entry.batch_adopts_result = True
        if entry.result is not None:
            if entry.owner is not None:
                return None
            return entry.result
        if entry.worker is None:
            entry.owner = owner
            entry.worker = asyncio.create_task(
                self._process_registered_event(entry)
            )
            return await self._await_registered_owner(entry, owner)
        worker = entry.worker

        drained_result, error, cancellation_latched = await self._drain_task(
            worker
        )
        if error is not None:
            if cancellation_latched:
                raise asyncio.CancelledError() from error
            raise error
        current = self._registered_events.get(entry.token.token_id)
        if (
            current is entry
            and entry.batch_owner is batch_owner
            and entry.owner is None
        ):
            adopted = (
                entry.result if entry.result is not None else drained_result
            )
            entry.result = adopted
        else:
            adopted = None
        if cancellation_latched:
            raise asyncio.CancelledError()
        return adopted

    async def flush_registered_events(
        self, *, session_id: str
    ) -> tuple[EventMatchResult, ...]:
        """Commit every reserved session event in synchronous receipt order."""
        self._bind_event_loop()
        batch_owner = asyncio.current_task()
        if batch_owner is None:
            raise RuntimeError("registered event flush requires an asyncio task")
        while True:
            session_entries = tuple(
                sorted(
                    (
                        entry
                        for entry in self._registered_events.values()
                        if entry.token.context.session_id == session_id
                    ),
                    key=lambda entry: entry.receipt_sequence,
                )
            )
            competing_batch = next(
                (
                    entry.batch_owner
                    for entry in session_entries
                    if entry.batch_owner is not None
                    and entry.batch_owner is not batch_owner
                ),
                None,
            )
            if competing_batch is None:
                entries = session_entries
                break
            _result, _error, cancellation_latched = await self._drain_task(
                competing_batch
            )
            if cancellation_latched:
                raise asyncio.CancelledError()
        for entry in entries:
            entry.batch_owner = batch_owner
            entry.batch_adopts_result = entry.owner is None
        results: list[EventMatchResult] = []
        try:
            for entry in entries:
                result = await self._flush_registered_entry(entry, batch_owner)
                if result is not None:
                    results.append(result)
        except BaseException:  # pylint: disable=broad-exception-caught
            for entry in entries:
                current = self._registered_events.get(entry.token.token_id)
                if current is entry and entry.batch_owner is batch_owner:
                    entry.batch_owner = None
                    entry.batch_adopts_result = False
            raise
        for entry in entries:
            current = self._registered_events.get(entry.token.token_id)
            if current is not None and (
                current is not entry or entry.batch_owner is not batch_owner
            ):
                raise RuntimeError("flush batch changed before atomic delivery")
        for entry in entries:
            current = self._registered_events.get(entry.token.token_id)
            if current is entry:
                self._registered_events.pop(entry.token.token_id)
        return tuple(results)

    def _has_eligible_registered_event(
        self, reservation: _EventTimeoutReservation
    ) -> bool:
        candidate = EventTimeoutCandidate(
            command_id=reservation.command_id,
            device_id=reservation.device_id,
            table_name=reservation.table_name,
            item_name=reservation.item_name,
            value_text=reservation.value_text,
            acked_at_ms=reservation.acked_at_ms,
            ack_device_rdt=reservation.ack_device_rdt,
            event_deadline_ms=reservation.event_deadline_ms,
        )
        return any(
            event_is_eligible_for_timeout_candidate(
                event=entry.token.event,
                received_at_ms=entry.token.context.received_at_ms,
                candidate=candidate,
            )
            for entry in self._registered_events.values()
        )

    def _resolve_event_timeout_authorization(
        self,
        decision: Future[bool],
        reservation: _EventTimeoutReservation,
    ) -> None:
        """Resolve one worker request at the loop-owned ordering point."""
        if decision.done():
            return
        authorized = (
            reservation.command_id
            not in self._event_timeout_reservations
            and not self._has_eligible_registered_event(reservation)
        )
        if authorized:
            self._event_timeout_reservations[reservation.command_id] = reservation
        decision.set_result(authorized)

    def _authorize_event_timeout_from_worker(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        reservation: _EventTimeoutReservation,
    ) -> bool:
        """Ask the owning loop for final ordering without blocking that loop."""
        decision: Future[bool] = Future()
        loop.call_soon_threadsafe(
            self._resolve_event_timeout_authorization,
            decision,
            reservation,
        )
        return decision.result()

    def _clear_event_timeout_reservation(
        self, reservation: _EventTimeoutReservation
    ) -> None:
        current = self._event_timeout_reservations.get(reservation.command_id)
        if current == reservation:
            self._event_timeout_reservations.pop(reservation.command_id)

    def _finish_event_timeout_mutation(
        self,
        reservation: _EventTimeoutReservation,
        command_id: str,
        _result: Any,
    ) -> None:
        self._clear_event_timeout_reservation(reservation)
        if _result is not None:
            self._event_timeout_candidates.pop(command_id, None)

    def _fail_event_timeout_mutation(
        self,
        reservation: _EventTimeoutReservation,
        _error: BaseException,
    ) -> None:
        self._clear_event_timeout_reservation(reservation)

    async def sweep_deadlines(self, *, now_ms: int | None = None) -> SweepReport:
        """Run pending/ACK sweep plus two-pass exact event-timeout grace."""
        event_loop = self._bind_event_loop()
        effective_now = self._clock_ms() if now_ms is None else now_ms

        def validated_device_sweep(device_id: str) -> SweepReport:
            report = self._store.sweep_device_deadlines(
                device_id=device_id,
                now_ms=effective_now,
            )
            try:
                _validate_device_sweep_report(device_id, report)
                for snapshot in report.snapshots:
                    authoritative = self._store.read_transition_audit_snapshot(
                        snapshot.transition.transition_id
                    )
                    if snapshot != authoritative:
                        raise RuntimeError(
                            "device sweep snapshot differs from durable state"
                        )
            except Exception as error:  # pylint: disable=broad-exception-caught
                raise RuntimeError(
                    f"device sweep returned invalid report: {device_id}: {error}"
                ) from error
            return report

        async def sweep_device(
            lease: _DeadlineDeviceLease,
        ) -> tuple[SweepReport, bool]:
            await lease.lock.acquire()
            lease.acquired = True
            retained: list[SweepReport] = []
            try:
                report = await self._run_mutation_locked(
                    partial(validated_device_sweep, lease.device_id),
                    snapshots=lambda _report: (),
                    on_success=retained.append,
                )
            except asyncio.CancelledError:
                if retained:
                    return retained[0], True
                raise
            return report, False

        async with self._deadline_sweep_lock:
            deadline_devices = await asyncio.to_thread(
                self._store.read_deadline_devices,
                now_ms=effective_now,
            )
            if (
                type(deadline_devices) is not tuple  # pylint: disable=unidiomatic-typecheck
                or any(
                    type(device_id) is not str or not device_id  # pylint: disable=unidiomatic-typecheck
                    for device_id in deadline_devices
                )
            ):
                raise RuntimeError(
                    "deadline devices must be an exact tuple of non-empty strings"
                )
            if deadline_devices != tuple(sorted(set(deadline_devices))):
                raise RuntimeError(
                    "deadline devices must be unique and strictly sorted"
                )
            device_leases = tuple(
                _DeadlineDeviceLease(
                    device_id=device_id,
                    lock=self._device_lock(device_id),
                )
                for device_id in deadline_devices
            )
            device_tasks: list[tuple[str, asyncio.Task[Any]]] = []
            settled_tasks: set[asyncio.Task[Any]] = set()
            cancellation_latched = False
            creation_failure: tuple[str, BaseException] | None = None
            try:
                for lease in device_leases:
                    worker = sweep_device(lease)
                    try:
                        task = asyncio.create_task(worker)
                    except BaseException as caught:  # pylint: disable=broad-exception-caught
                        worker.close()  # pylint: disable=no-member
                        creation_failure = (lease.device_id, caught)
                        if isinstance(caught, asyncio.CancelledError):
                            cancellation_latched = True
                        break
                    device_tasks.append((lease.device_id, task))
                outcomes: list[tuple[str, Any]] = []
                for device_id, task in device_tasks:
                    outcome, error, wait_cancelled = await self._drain_task(task)
                    settled_tasks.add(task)
                    cancellation_latched = (
                        cancellation_latched or wait_cancelled
                    )
                    if error is not None:
                        if isinstance(error, asyncio.CancelledError):
                            cancellation_latched = True
                        outcomes.append((device_id, error))
                        continue
                    if (
                        type(outcome) is not tuple  # pylint: disable=unidiomatic-typecheck
                        or len(outcome) != 2
                        or type(outcome[0]) is not SweepReport  # pylint: disable=unidiomatic-typecheck
                        or type(outcome[1]) is not bool  # pylint: disable=unidiomatic-typecheck
                    ):
                        outcomes.append(
                            (
                                device_id,
                                RuntimeError(
                                    "device sweep returned invalid outcome: "
                                    f"{device_id}"
                                ),
                            )
                        )
                        continue
                    report, worker_cancelled = outcome
                    cancellation_latched = (
                        cancellation_latched or worker_cancelled
                    )
                    try:
                        _validate_device_sweep_report(device_id, report)
                    except Exception as error:  # pylint: disable=broad-exception-caught
                        outcomes.append(
                            (
                                device_id,
                                RuntimeError(
                                    "device sweep returned invalid report: "
                                    f"{device_id}: {error}"
                                ),
                            )
                        )
                        continue
                    outcomes.append((device_id, report))
                device_reports: list[SweepReport] = []
                failures: list[tuple[str, BaseException]] = []
                for device_id, outcome in outcomes:
                    if isinstance(outcome, BaseException):
                        if isinstance(outcome, asyncio.CancelledError):
                            cancellation_latched = True
                        else:
                            failures.append((device_id, outcome))
                    else:
                        device_reports.append(outcome)
                base_snapshots = tuple(
                    sorted(
                        (
                            snapshot
                            for report in device_reports
                            for snapshot in report.snapshots
                        ),
                        key=lambda snapshot: snapshot.transition.transition_id,
                    )
                )
                reconciliation_error: BaseException | None = None
                try:
                    await self._publish(base_snapshots)
                except asyncio.CancelledError as caught:
                    cancellation_latched = True
                    if (
                        reconciliation_error is None
                        and caught.__cause__ is not None
                    ):
                        reconciliation_error = caught.__cause__
                except BaseException as caught:  # pylint: disable=broad-exception-caught
                    reconciliation_error = caught
                while True:
                    try:
                        await self._refresh_status()
                        break
                    except asyncio.CancelledError as caught:
                        cancellation_latched = True
                        if (
                            reconciliation_error is None
                            and caught.__cause__ is not None
                        ):
                            reconciliation_error = caught.__cause__
                    except BaseException as caught:  # pylint: disable=broad-exception-caught
                        if reconciliation_error is None:
                            reconciliation_error = caught
                        break
                base_report = SweepReport(
                    sum(
                        report.expired_pending for report in device_reports
                    ),
                    sum(report.retry_pending for report in device_reports),
                    sum(
                        report.failed_attempt_limit
                        for report in device_reports
                    ),
                    0,
                    base_snapshots,
                )
                if reconciliation_error is not None:
                    if cancellation_latched:
                        raise asyncio.CancelledError() from reconciliation_error
                    raise reconciliation_error
                if cancellation_latched:
                    cause = failures[0][1] if failures else None
                    raise asyncio.CancelledError() from cause
                if creation_failure is not None:
                    _device_id, creation_error = creation_failure
                    if failures:
                        raise creation_error from DeadlineSweepError(
                            tuple(failures), base_report
                        )
                    raise creation_error
                if failures:
                    raise DeadlineSweepError(tuple(failures), base_report)
            finally:
                cleanup_cancellation_latched = False
                for _device_id, task in device_tasks:
                    if not task.done():
                        task.cancel()
                for _device_id, task in device_tasks:
                    if task not in settled_tasks:
                        _outcome, _error, wait_cancelled = (
                            await self._drain_task(task)
                        )
                        cleanup_cancellation_latched = (
                            cleanup_cancellation_latched or wait_cancelled
                        )
                for lease in reversed(device_leases):
                    if lease.acquired:
                        lease.lock.release()
                        lease.acquired = False
                if cleanup_cancellation_latched:
                    cause = (
                        creation_failure[1]
                        if creation_failure is not None
                        else None
                    )
                    raise asyncio.CancelledError() from cause
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
            if prior is None or prior[0] != candidate:
                self._event_timeout_candidates[candidate.command_id] = (
                    candidate,
                    now_monotonic,
                )
                continue
            if now_monotonic - prior[1] < 1.0:
                continue
            async with self._device_lock(candidate.device_id):
                reservation = _EventTimeoutReservation(
                    command_id=candidate.command_id,
                    device_id=candidate.device_id,
                    table_name=candidate.table_name,
                    item_name=candidate.item_name,
                    value_text=candidate.value_text,
                    acked_at_ms=candidate.acked_at_ms,
                    ack_device_rdt=candidate.ack_device_rdt,
                    event_deadline_ms=candidate.event_deadline_ms,
                )
                if self._has_eligible_registered_event(reservation):
                    continue

                snapshot = await self._run_mutation_locked(
                    partial(
                        self._store.mark_event_incomplete,
                        command_id=candidate.command_id,
                        expected_event_deadline_ms=(
                            candidate.event_deadline_ms
                        ),
                        now_ms=effective_now,
                        final_authorizer=partial(
                            self._authorize_event_timeout_from_worker,
                            loop=event_loop,
                            reservation=reservation,
                        ),
                    ),
                    snapshots=lambda committed: (
                        (committed,) if committed is not None else ()
                    ),
                    on_success=partial(
                        self._finish_event_timeout_mutation,
                        reservation,
                        candidate.command_id,
                    ),
                    on_failure=partial(
                        self._fail_event_timeout_mutation,
                        reservation,
                    ),
                )
                if snapshot is not None:
                    incomplete.append(snapshot)
        await self._refresh_status()
        snapshots = tuple(
            sorted(
                (*base_snapshots, *incomplete),
                key=lambda snapshot: snapshot.transition.transition_id,
            )
        )
        return SweepReport(
            sum(report.expired_pending for report in device_reports),
            sum(report.retry_pending for report in device_reports),
            sum(report.failed_attempt_limit for report in device_reports),
            len(incomplete),
            snapshots,
        )

    async def status_snapshot(self, device_id: str | None = None) -> StoreStatus:
        """Read authoritative status off the event loop."""
        status = await asyncio.to_thread(self._store.status_snapshot, device_id)
        if device_id is None:
            self._cached_status = status
        return status

    async def read_command(self, command_id: str) -> TwinCommand:
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
        self._cloud_pending: dict[
            tuple[str, str, str], deque[_CloudPendingSetting]
        ] = defaultdict(deque)
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
