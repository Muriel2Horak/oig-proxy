"""Behavioral tests for per-device durable local-setting coordination."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring,too-many-lines
# pylint: disable=too-few-public-methods,too-many-instance-attributes
# pylint: disable=too-many-arguments,use-implicit-booleaness-not-comparison
# pylint: disable=too-many-positional-arguments
# pylint: disable=too-many-locals

from __future__ import annotations

import asyncio
from concurrent.futures import Future
import gc
import hashlib
import sqlite3
import threading
import weakref
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast, Literal

import pytest

from protocol.frame import FrameDirection
import telemetry.settings_audit as settings_audit_module
from telemetry.settings_audit import (
    SettingStep,
    SettingsAuditPublisher,
    SettingsAuditRecord,
)
from twin.ack_parser import SettingEvent, SettingResponse, derive_event_evidence_id
from twin.delivery import DeadlineSweepError, TwinCoordinator
from twin.state import (
    ActiveLocalAttempt,
    AttemptWriteOutcome,
    AttemptWriteResult,
    CommandState,
    CommandTransition,
    ControlIngress,
    DeliveryDisposition,
    DeliveryTrigger,
    EvidenceContext,
    EventDisposition,
    EventMatchResult,
    LocalResponseDisposition,
    RetryReason,
    StoreStatus,
    SweepReport,
    TransitionAuditSnapshot,
    TwinCommand,
)
from twin.store import StoreRecordNotFound, TwinCommandStore


def _race_owner_cancellation_after_done(
    real_shield: Callable[[Any], Any],
    outer_errors: list[asyncio.CancelledError],
    *,
    selected_call: int = 1,
) -> Callable[[Any], Any]:
    """Inject owner cancellation only after the wrapped worker is definitive."""
    selected = False
    shield_calls = 0

    def controlled_shield(awaitable: Any) -> Any:
        nonlocal selected, shield_calls
        shield_calls += 1
        if selected or shield_calls != selected_call:
            return real_shield(awaitable)
        selected = True

        async def inject_outer_after_worker() -> Any:
            while not awaitable.done():
                await asyncio.sleep(0)
            owner = asyncio.current_task()
            assert owner is not None
            asyncio.get_running_loop().call_soon(
                owner.cancel,
                "outer cancelled",
            )
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError as outer_error:
                outer_errors.append(outer_error)
                raise
            raise AssertionError("owner cancellation was not injected")

        return inject_outer_after_worker()

    return controlled_shield


def _capture_pending_owner_cancellation(
    real_shield: Callable[[Any], Any],
    outer_errors: list[asyncio.CancelledError],
) -> Callable[[Any], Any]:
    """Capture exact task cancellation while a shielded worker is pending."""

    def observed(awaitable: Any) -> Any:
        async def wait() -> Any:
            try:
                return await real_shield(awaitable)
            except asyncio.CancelledError as caught:
                if not awaitable.done():
                    outer_errors.append(caught)
                raise

        return wait()

    return observed


def _enqueue(
    store: TwinCommandStore,
    *,
    value_text: str = "2",
    received_at_ms: int = 100,
    device_id: str = "123",
    item_name: str = "MODE",
) -> TwinCommand:
    try:
        store.read_device(device_id)
    except StoreRecordNotFound:
        store.observe_device(
            device_id=device_id,
            observed_at_ms=max(1, received_at_ms - 10),
            observed_wire_id=14_000_000,
            observed_wire_id_set=1_786_000_000,
        )
    ingress = ControlIngress(
        f"ing-{device_id}-{item_name}-{received_at_ms}-{value_text}",
        received_at_ms,
        f"oig/{device_id}/control/set",
        device_id,
        False,
        f'{{"value":"{value_text}"}}',
    )
    return store.enqueue_command(
        ingress,
        device_id=device_id,
        table_name="tbl_box_prms",
        item_name=item_name,
        value_text=value_text,
    ).command


class _Clock:
    def __init__(self, value: int = 201) -> None:
        self.value = value

    def __call__(self) -> int:
        value = self.value
        self.value += 1
        return value


class _ForgedTransitionAuditSnapshot(TransitionAuditSnapshot):
    """Structurally valid subclass rejected by an exact snapshot contract."""


async def _cancel_owner_and_internal_tasks(
    owner: asyncio.Task[Any],
    baseline: set[asyncio.Task[Any]],
    *,
    rounds: int = 3,
) -> None:
    """Model loop-wide shutdown cancelling an owner and its spawned tasks."""
    owner.cancel()
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    for _ in range(rounds):
        scheduling_turn = asyncio.Event()
        loop.call_soon(scheduling_turn.set)
        await scheduling_turn.wait()
        internal = (
            asyncio.all_tasks()
            - baseline
            - {owner}
            - ({current} if current is not None else set())
        )
        for task in internal:
            task.cancel()


class ScriptedLocalSettingWriter:
    """Writer double that observes durable state at the invocation boundary."""

    def __init__(
        self,
        store: TwinCommandStore,
        *,
        outcome: AttemptWriteOutcome = AttemptWriteOutcome.DRAINED,
        error_text: str | None = None,
        entered: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.store = store
        self.outcome = outcome
        self.error_text = error_text
        self.entered = entered
        self.release = release
        self.frames: list[bytes] = []
        self.attempts: list[ActiveLocalAttempt] = []
        self.states_at_invocation: list[CommandState] = []

    async def write_attempt(
        self,
        attempt: ActiveLocalAttempt,
        *,
        before_write: Callable[[], Awaitable[None]],
    ) -> AttemptWriteResult:
        prepared_at_ms = (
            attempt.ack_deadline_ms - self.store.policy.ack_timeout_ms
        )
        if self.outcome is AttemptWriteOutcome.FAILED:
            return AttemptWriteResult(
                outcome=self.outcome,
                started_at_ms=prepared_at_ms + 1,
                drain_completed_at_ms=None,
                error_text=self.error_text or "write rejected before invocation",
            )
        await before_write()
        self.states_at_invocation.append(
            self.store.read_command(attempt.command_id).state
        )
        self.frames.append(attempt.wire_frame)
        self.attempts.append(attempt)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.outcome is AttemptWriteOutcome.DRAINED:
            return AttemptWriteResult(
                outcome=self.outcome,
                started_at_ms=prepared_at_ms + 1,
                drain_completed_at_ms=prepared_at_ms + 2,
                error_text=None,
            )
        return AttemptWriteResult(
            outcome=AttemptWriteOutcome.UNKNOWN,
            started_at_ms=prepared_at_ms + 1,
            drain_completed_at_ms=None,
            error_text=self.error_text or "drain completion unknown",
        )


class _PausedCommitConnection:  # pylint: disable=too-few-public-methods
    """Pause one physical COMMIT while preserving the real connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        entered: threading.Event,
        release: threading.Event,
        failure: BaseException | None = None,
    ) -> None:
        self._connection = connection
        self._entered = entered
        self._release = release
        self._failure = failure

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, statement: str, parameters: Any = ()) -> Any:
        if statement.strip().upper() == "COMMIT":
            self._entered.set()
            assert self._release.wait(timeout=1)
            if self._failure is not None:
                failure = self._failure
                self._failure = None
                raise failure
        return self._connection.execute(statement, parameters)


class _InjectedCommitFailure(BaseException):
    """Recoverable test-only failure raised before a physical COMMIT."""


@pytest.fixture
def coordinator(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> TwinCoordinator:
    _enqueue(store)
    return TwinCoordinator(
        store,
        control_enabled=True,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )


@pytest.fixture
def disabled_coordinator(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> TwinCoordinator:
    _enqueue(store)
    return TwinCoordinator(
        store,
        control_enabled=False,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )


def _response(
    raw: bytes,
    *,
    result: Literal["ACK", "NACK"] = "ACK",
    rdt_text: str | None = "06.08.2026 10:12:00",
) -> SettingResponse:
    return SettingResponse(
        result=result,
        reason="Setting" if result == "ACK" else "Rejected",
        rdt_text=rdt_text,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _context(
    active: ActiveLocalAttempt,
    raw: bytes,
    *,
    session_id: str | None = None,
    device_id: str | None = None,
    received_at_ms: int | None = None,
    direction: FrameDirection = FrameDirection.BOX_TO_PROXY,
) -> EvidenceContext:
    return EvidenceContext(
        direction,
        session_id or active.session_id,
        device_id or active.device_id,
        active.ack_deadline_ms if received_at_ms is None else received_at_ms,
        raw,
    )


async def _deliver(
    coordinator: TwinCoordinator,
    writer: ScriptedLocalSettingWriter,
    *,
    session: str = "session-a",
    now_ms: int = 200,
) -> ActiveLocalAttempt:
    decision = await coordinator.claim_and_write_next(
        device_id="123",
        session_id=session,
        received_at_ms=now_ms,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )
    assert decision.disposition is DeliveryDisposition.SENT
    assert decision.active_attempt is not None
    return decision.active_attempt


def _event(
    *,
    device_id: str = "123",
    event_id_set: int = 55,
    device_dt: str = "06.08.2026 10:12:01",
    item_name: str = "MODE",
    new_value: str = "2",
) -> SettingEvent:
    content = f"Remotely : tbl_box_prms / {item_name}: [1]->[{new_value}]"
    return SettingEvent(
        evidence_id=derive_event_evidence_id(
            device_id, event_id_set, device_dt, content
        ),
        device_id=device_id,
        event_id_set=event_id_set,
        device_dt=device_dt,
        content_text=content,
        table_name="tbl_box_prms",
        item_name=item_name,
        old_value_text="1",
        new_value_text=new_value,
    )


def _register_batch_event(
    coordinator: TwinCoordinator,
    *,
    session_id: str,
    device_id: str,
    event_id_set: int,
    received_at_ms: int,
) -> Any:
    event = _event(
        device_id=device_id,
        event_id_set=event_id_set,
        new_value=str(event_id_set),
    )
    return coordinator.register_setting_event(
        event=event,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            session_id,
            device_id,
            received_at_ms,
            f"batch-{event_id_set}".encode(),
        ),
    )


async def _register_during_paused_commit(
    coordinator: TwinCoordinator,
    *,
    session_id: str,
    deadline_ms: int,
    commit_release: threading.Event,
) -> tuple[Any, bool]:
    """Prove registration plus a later heartbeat run before COMMIT resumes."""
    loop = asyncio.get_running_loop()
    registered: asyncio.Future[Any] = loop.create_future()
    registration_started = threading.Event()
    heartbeat_progressed = threading.Event()
    heartbeat_observed: list[bool] = []

    def register() -> None:
        registration_started.set()
        try:
            registered.set_result(
                coordinator.register_setting_event(
                    event=_event(),
                    context=EvidenceContext(
                        FrameDirection.BOX_TO_PROXY,
                        session_id,
                        "123",
                        deadline_ms,
                        b"event-during-physical-commit",
                    ),
                )
            )
        except BaseException as error:  # pylint: disable=broad-exception-caught
            registered.set_exception(error)

    def release_after_heartbeat() -> None:
        assert registration_started.wait(timeout=1)
        heartbeat_observed.append(heartbeat_progressed.wait(timeout=0.25))
        commit_release.set()

    releaser = threading.Thread(target=release_after_heartbeat)
    releaser.start()
    loop.call_soon(register)
    loop.call_soon(heartbeat_progressed.set)
    token = await registered
    await asyncio.to_thread(releaser.join, 1)
    assert not releaser.is_alive()
    return token, heartbeat_observed == [True]


@pytest.mark.asyncio
async def test_claim_requires_correlated_cloud_terminal_end(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)

    rejected = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=None,  # type: ignore[arg-type]
        writer=writer,
    )

    assert rejected.disposition is DeliveryDisposition.UNAUTHORIZED
    assert writer.frames == []
    assert (await coordinator.status_snapshot("123")).awaiting_ack == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", list(DeliveryTrigger))
async def test_only_declared_delivery_triggers_can_claim(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: Callable,
    trigger: DeliveryTrigger,
) -> None:
    store = store_factory(8)
    _enqueue(store)
    coordinator = TwinCoordinator(
        store, renderer=deterministic_renderer, clock_ms=_Clock()
    )
    writer = ScriptedLocalSettingWriter(store)

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=trigger,
        writer=writer,
    )

    assert result.disposition is DeliveryDisposition.SENT
    assert len(writer.frames) == 1


@pytest.mark.asyncio
async def test_deliver_next_writes_only_durably_prepared_frame(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)

    decision = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=DeliveryTrigger.CORRELATED_CLOUD_END,
        writer=writer,
    )

    assert decision.disposition is DeliveryDisposition.SENT
    assert decision.active_attempt is not None
    assert writer.frames == [decision.active_attempt.wire_frame]
    assert writer.states_at_invocation == [CommandState.AWAITING_ACK]
    assert decision.active_attempt.write_outcome is AttemptWriteOutcome.DRAINED
    persisted = store.read_attempt(
        decision.active_attempt.command_id,
        decision.active_attempt.attempt_number,
    )
    assert persisted.wire_frame == decision.active_attempt.wire_frame
    assert persisted.write_outcome is AttemptWriteOutcome.DRAINED


@pytest.mark.asyncio
async def test_disabled_coordinator_never_claims_or_writes(
    disabled_coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    before = await disabled_coordinator.status_snapshot("123")

    result = await disabled_coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )

    assert result.disposition is DeliveryDisposition.CONTROL_DISABLED
    assert await disabled_coordinator.status_snapshot("123") == before
    assert writer.frames == []


@pytest.mark.asyncio
async def test_write_before_invocation_failure_is_known_and_retryable(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(
        store, outcome=AttemptWriteOutcome.FAILED
    )

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )

    assert result.disposition is DeliveryDisposition.WRITE_FAILED
    assert result.active_attempt is None
    assert writer.frames == []
    assert store.single_nonterminal("123").state is CommandState.RETRY_PENDING
    assert store.read_attempt(
        store.single_nonterminal("123").command_id, 1
    ).write_outcome is AttemptWriteOutcome.FAILED


@pytest.mark.asyncio
async def test_executor_renderer_cancellation_does_not_spin_or_retain_lock(
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    sentinel = asyncio.CancelledError("renderer cancelled")

    def cancelled_renderer(_context: Any) -> Any:
        raise sentinel

    coordinator = TwinCoordinator(
        store,
        renderer=cancelled_renderer,
        clock_ms=_Clock(),
    )
    writer = ScriptedLocalSettingWriter(store)
    real_shield = asyncio.shield
    shield_calls: defaultdict[int, int] = defaultdict(int)

    def reject_reawait(awaitable: Any) -> asyncio.Future[Any]:
        identity = id(awaitable)
        shield_calls[identity] += 1
        if shield_calls[identity] > 1:
            raise AssertionError("executor future was awaited more than once")
        return real_shield(awaitable)

    monkeypatch.setattr(asyncio, "shield", reject_reawait)

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.claim_and_write_next(
            device_id="123",
            session_id="session-a",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=writer,
        )

    assert caught.value is sentinel
    assert store.single_nonterminal("123").state is CommandState.PENDING
    assert writer.frames == []
    assert not coordinator._device_lock(  # pylint: disable=protected-access
        "123"
    ).locked()
    assert shield_calls
    assert max(shield_calls.values()) == 1


@pytest.mark.asyncio
async def test_outer_cancellation_racing_executor_cancellation_is_latched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker: Future[Any] = Future()
    worker_error = asyncio.CancelledError("worker cancelled")
    worker.set_exception(worker_error)
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _race_owner_cancellation_after_done(asyncio.shield, outer_errors),
    )
    draining = asyncio.create_task(TwinCoordinator._drain_future(worker))  # pylint: disable=protected-access

    result, error, owner_cancellation = await draining

    assert result is None
    assert error is worker_error
    assert len(outer_errors) == 1
    assert owner_cancellation is outer_errors[0]


@pytest.mark.parametrize(
    "selected_call",
    [
        pytest.param(1, id="primary-mutation"),
        pytest.param(2, id="status-reconciliation"),
    ],
)
@pytest.mark.asyncio
async def test_mutation_cleanup_preserves_exact_owner_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    selected_call: int,
) -> None:
    outer_errors: list[asyncio.CancelledError] = []
    status_calls = 0
    original_status = store.status_snapshot

    def observed_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", observed_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    monkeypatch.setattr(
        asyncio,
        "shield",
        _race_owner_cancellation_after_done(
            asyncio.shield,
            outer_errors,
            selected_call=selected_call,
        ),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator._run_mutation_locked(  # pylint: disable=protected-access
            lambda: "committed",
            snapshots=lambda _result: (),
        )

    assert len(outer_errors) == 1
    assert caught.value is outer_errors[0]
    assert caught.value.args == ("outer cancelled",)
    assert status_calls == 1


@pytest.mark.asyncio
async def test_post_ack_status_preserves_exact_owner_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _race_owner_cancellation_after_done(
            asyncio.shield,
            outer_errors,
            selected_call=3,
        ),
    )
    raw = b"post-ack-owner-cancel"

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.handle_local_response(
            active=active,
            response=_response(raw),
            context=_context(active, raw, received_at_ms=220),
            writer=writer,
        )

    assert len(outer_errors) == 1
    assert caught.value is outer_errors[0]
    assert caught.value.args == ("outer cancelled",)
    assert store.read_command(active.command_id).state is CommandState.AWAITING_EVENT
    assert coordinator.cached_status_snapshot.awaiting_event == 1
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "selected_call",
    [
        pytest.param(1, id="aggregate-status"),
        pytest.param(2, id="final-status"),
    ],
)
@pytest.mark.asyncio
async def test_deadline_status_preserves_exact_owner_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    selected_call: int,
) -> None:
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _race_owner_cancellation_after_done(
            asyncio.shield,
            outer_errors,
            selected_call=selected_call,
        ),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(now_ms=1)

    assert len(outer_errors) == 1
    assert caught.value is outer_errors[0]
    assert caught.value.args == ("outer cancelled",)
    assert coordinator._deadline_sweep_lock.locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_status_worker_cancellation_is_terminal_without_retry(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = asyncio.CancelledError("status cancelled")
    status_calls = 0
    original_status = store.status_snapshot

    def cancel_first_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            raise sentinel
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", cancel_first_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator._run_mutation_locked(  # pylint: disable=protected-access
            lambda: "committed",
            snapshots=lambda _result: (),
        )

    assert caught.value is sentinel
    assert status_calls == 1


@pytest.mark.parametrize("cancel_owner", [False, True], ids=["worker", "owner"])
@pytest.mark.asyncio
async def test_mutation_retains_primary_and_status_worker_chain(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    cancel_owner: bool,
) -> None:
    mutation_error = asyncio.CancelledError("mutation worker cancelled")
    status_error = asyncio.CancelledError("status worker cancelled")
    mutation_entered = threading.Event()
    mutation_release = threading.Event()

    def cancelled_mutation() -> None:
        if cancel_owner:
            mutation_entered.set()
            assert mutation_release.wait(timeout=1)
        raise mutation_error

    def cancelled_status() -> StoreStatus:
        raise status_error

    monkeypatch.setattr(store, "status_snapshot", cancelled_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    if cancel_owner:
        owner = asyncio.create_task(
            coordinator._run_mutation_locked(  # pylint: disable=protected-access
                cancelled_mutation,
                snapshots=lambda _result: (),
            )
        )
        assert await asyncio.to_thread(mutation_entered.wait, 0.1)
        owner.cancel("mutation owner cancelled")
        while not outer_errors:
            await asyncio.sleep(0)
        mutation_release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await owner
        assert caught.value is outer_errors[0]
        assert caught.value.__cause__ is mutation_error
    else:
        with pytest.raises(asyncio.CancelledError) as caught:
            await coordinator._run_mutation_locked(  # pylint: disable=protected-access
                cancelled_mutation,
                snapshots=lambda _result: (),
            )
        assert caught.value is mutation_error

    assert mutation_error.__cause__ is status_error


@pytest.mark.asyncio
async def test_deadline_device_status_worker_cancellation_is_exact_after_reconciliation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    sentinel = asyncio.CancelledError("deadline status cancelled")
    status_calls = 0
    original_status = store.status_snapshot

    def cancel_first_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            raise sentinel
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", cancel_first_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )

    assert caught.value is sentinel
    assert status_calls == 2
    assert store.read_command(command.command_id).state is CommandState.EXPIRED
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 1


@pytest.mark.asyncio
async def test_deadline_device_status_control_flow_is_exact_after_reconciliation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WorkerAbort(BaseException):
        """Represent non-cancellation worker control flow."""

    command = _enqueue(store)
    sentinel = WorkerAbort("status aborted")
    status_calls = 0
    original_status = store.status_snapshot

    def abort_first_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            raise sentinel
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", abort_first_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )

    with pytest.raises(WorkerAbort) as caught:
        await coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )

    assert caught.value is sentinel
    assert status_calls == 2
    assert store.read_command(command.command_id).state is CommandState.EXPIRED
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 1


@pytest.mark.asyncio
async def test_deadline_store_worker_cancellation_remains_exact_after_cleanup(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    sentinel = asyncio.CancelledError("deadline store worker cancelled")
    sweep_calls = 0
    status_calls = 0
    original_status = store.status_snapshot

    def cancel_device_sweep(*, device_id: str, now_ms: int) -> SweepReport:
        del device_id, now_ms
        nonlocal sweep_calls
        sweep_calls += 1
        raise sentinel

    def observe_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "sweep_device_deadlines", cancel_device_sweep)
    monkeypatch.setattr(store, "status_snapshot", observe_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )

    assert caught.value is sentinel
    assert sweep_calls == 1
    assert status_calls == 2
    assert store.read_command(command.command_id).state is CommandState.PENDING
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_deadline_aggregate_status_worker_cancellation_is_terminal_without_retry(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    sentinel = asyncio.CancelledError("aggregate status cancelled")
    status_calls = 0
    original_status = store.status_snapshot

    def cancel_aggregate_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 2:
            raise sentinel
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", cancel_aggregate_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )

    assert caught.value is sentinel
    assert status_calls == 2
    assert store.read_command(command.command_id).state is CommandState.EXPIRED


@pytest.mark.asyncio
async def test_drain_uncertainty_is_persisted_and_connection_closes(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(
        store, outcome=AttemptWriteOutcome.UNKNOWN
    )

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )

    assert result.disposition is DeliveryDisposition.WRITE_UNKNOWN
    assert result.active_attempt is None
    assert result.close_connection is True
    command = store.single_nonterminal("123")
    assert command.state is CommandState.RETRY_PENDING
    assert store.read_attempt(command.command_id, 1).write_outcome is (
        AttemptWriteOutcome.UNKNOWN
    )


@pytest.mark.asyncio
async def test_active_owner_elsewhere_never_invokes_writer(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    first_writer = ScriptedLocalSettingWriter(store)
    await _deliver(coordinator, first_writer, session="session-a")
    second_writer = ScriptedLocalSettingWriter(store)

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-b",
        received_at_ms=210,
        trigger=DeliveryTrigger.CORRELATED_CLOUD_END,
        writer=second_writer,
    )

    assert result.disposition is DeliveryDisposition.ACTIVE_DELIVERY_ELSEWHERE
    assert second_writer.frames == []


@pytest.mark.asyncio
async def test_same_device_lock_spans_writer_drain(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    writer = ScriptedLocalSettingWriter(store, entered=entered, release=release)
    first = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="session-a",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=writer,
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="session-b",
            received_at_ms=201,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=writer,
        )
    )
    await asyncio.sleep(0)
    assert second.done() is False

    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result.disposition is DeliveryDisposition.SENT
    assert second_result.disposition is DeliveryDisposition.ACTIVE_DELIVERY_ELSEWHERE
    assert len(writer.frames) == 1


@pytest.mark.asyncio
async def test_distinct_device_writes_can_progress_in_parallel(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    _enqueue(store, device_id="123")
    _enqueue(store, device_id="456")
    coordinator = TwinCoordinator(
        store, renderer=deterministic_renderer, clock_ms=_Clock()
    )
    entered_a = asyncio.Event()
    entered_b = asyncio.Event()
    release = asyncio.Event()
    writer_a = ScriptedLocalSettingWriter(
        store, entered=entered_a, release=release
    )
    writer_b = ScriptedLocalSettingWriter(
        store, entered=entered_b, release=release
    )
    task_a = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="a",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=writer_a,
        )
    )
    task_b = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="456",
            session_id="b",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=writer_b,
        )
    )

    await asyncio.wait_for(
        asyncio.gather(entered_a.wait(), entered_b.wait()), timeout=1
    )
    release.set()
    first, second = await asyncio.gather(task_a, task_b)

    assert first.disposition is DeliveryDisposition.SENT
    assert second.disposition is DeliveryDisposition.SENT


@pytest.mark.asyncio
async def test_rapid_same_key_updates_preserve_attempted_predecessor(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    first = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="a",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )
    assert first.active_attempt is not None

    second = _enqueue(store, value_text="3", received_at_ms=210)

    assert (await coordinator.read_command(first.active_attempt.command_id)).value_text == "2"
    assert second.predecessor_command_id == first.active_attempt.command_id


@pytest.mark.asyncio
async def test_disconnect_requeues_same_wire_identity_for_next_dialogue(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    first = await _deliver(coordinator, writer, session="a", now_ms=200)
    await coordinator.abort_dialogue(
        active=first,
        occurred_at_ms=210,
        reason=RetryReason.DISCONNECT,
    )
    second = await _deliver(coordinator, writer, session="b", now_ms=300)

    first_command = await coordinator.read_command(first.command_id)
    second_command = await coordinator.read_command(second.command_id)
    assert (second_command.wire_id, second_command.wire_id_set, second_command.wire_dt) == (
        first_command.wire_id,
        first_command.wire_id_set,
        first_command.wire_dt,
    )
    assert second.wire_frame != first.wire_frame


@pytest.mark.asyncio
async def test_timeout_stops_at_limit_and_nack_never_retries(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: Callable,
) -> None:
    store = store_factory(1)
    _enqueue(store)
    coordinator = TwinCoordinator(
        store, renderer=deterministic_renderer, clock_ms=_Clock()
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)

    timed_out = await coordinator.abort_dialogue(
        active=active,
        occurred_at_ms=active.ack_deadline_ms + 1,
        reason=RetryReason.ACK_TIMEOUT,
    )

    assert timed_out.command.state is CommandState.FAILED
    _enqueue(store, value_text="3", received_at_ms=40_500)
    nack_active = await _deliver(
        coordinator, writer, session="next", now_ms=40_600
    )
    raw = b"nack"
    nack = await coordinator.handle_local_response(
        active=nack_active,
        response=_response(raw, result="NACK"),
        context=_context(nack_active, raw, received_at_ms=40_601),
        writer=writer,
    )

    assert nack.disposition is LocalResponseDisposition.NACK_ACCEPTED
    assert nack.next_attempt is None
    assert (await coordinator.read_command(nack_active.command_id)).state is (
        CommandState.FAILED
    )


@pytest.mark.asyncio
async def test_ack_moves_to_awaiting_event_without_confirmation(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    raw = b"ack"

    decision = await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw),
        writer=writer,
    )

    assert decision.disposition is LocalResponseDisposition.ACK_ACCEPTED
    assert (await coordinator.read_command(active.command_id)).state is (
        CommandState.AWAITING_EVENT
    )
    assert decision.confirmation is None
    assert decision.send_final_end is True


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "", "NotSetting"])
async def test_ack_requires_exact_setting_reason_before_successor_claim(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    reason: str | None,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    successor = _enqueue(
        store,
        value_text="50",
        received_at_ms=210,
        item_name="BAT_AC",
    )
    raw = b"ack-with-wrong-reason"

    decision = await coordinator.handle_local_response(
        active=active,
        response=replace(_response(raw), reason=reason),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )

    assert decision.disposition is LocalResponseDisposition.REJECTED
    assert decision.close_connection is True
    assert decision.next_attempt is None
    assert store.read_command(active.command_id).state is CommandState.RETRY_PENDING
    assert store.read_command(successor.command_id).attempt_count == 0
    assert len(writer.frames) == 1


@pytest.mark.asyncio
async def test_ack_atomically_writes_already_prepared_successor(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    successor = _enqueue(
        store,
        value_text="50",
        received_at_ms=210,
        item_name="BAT_AC",
    )
    raw = b"ack"

    decision = await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )

    assert decision.disposition is LocalResponseDisposition.NEXT_SENT
    assert decision.next_attempt is not None
    assert decision.next_attempt.command_id == successor.command_id
    assert writer.frames[-1] == decision.next_attempt.wire_frame
    reasons = tuple(
        transition.reason
        for transition in store.read_transitions(successor.command_id)
    )
    assert reasons == (
        "accepted_ingress",
        "selected",
        "attempt_prepared",
        "write_started",
        "attempt_drained",
    )


@pytest.mark.asyncio
async def test_ack_requires_active_session_and_dialog_owner(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    raw = b"foreign"

    result = await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, session_id="foreign-session", received_at_ms=201),
        writer=writer,
    )

    assert result.disposition is LocalResponseDisposition.REJECTED
    assert result.close_connection is True
    assert (await coordinator.read_command(active.command_id)).state in {
        CommandState.RETRY_PENDING,
        CommandState.FAILED,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "device_id"),
    [
        (FrameDirection.CLOUD_TO_PROXY, "123"),
        (FrameDirection.BOX_TO_PROXY, "456"),
    ],
)
async def test_wrong_direction_or_device_response_fails_closed(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    direction: FrameDirection,
    device_id: str,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    raw = b"wrong-context"

    result = await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(
            active,
            raw,
            direction=direction,
            device_id=device_id,
            received_at_ms=202,
        ),
        writer=writer,
    )

    assert result.disposition is LocalResponseDisposition.REJECTED
    assert result.close_connection is True
    assert (await coordinator.read_command(active.command_id)).state is (
        CommandState.RETRY_PENDING
    )


@pytest.mark.asyncio
async def test_late_ack_fails_closed_without_advancing_successor(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    successor = _enqueue(
        store,
        value_text="50",
        received_at_ms=210,
        item_name="BAT_AC",
    )
    raw = b"late"

    result = await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(
            active, raw, received_at_ms=active.ack_deadline_ms + 1
        ),
        writer=writer,
    )

    assert result.disposition is LocalResponseDisposition.TIMED_OUT
    assert result.close_connection is True
    assert store.read_command(successor.command_id).attempt_count == 0


@pytest.mark.asyncio
async def test_exact_duplicate_ack_is_idempotent(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    raw = b"same-ack"
    response = _response(raw)
    context = _context(active, raw)
    first = await coordinator.handle_local_response(
        active=active, response=response, context=context, writer=writer
    )

    duplicate = await coordinator.handle_local_response(
        active=active, response=response, context=context, writer=writer
    )

    assert first.disposition is LocalResponseDisposition.ACK_ACCEPTED
    assert duplicate.disposition is LocalResponseDisposition.DUPLICATE
    assert duplicate.close_connection is False


@pytest.mark.asyncio
async def test_decreasing_rdt_rejects_second_batch_response(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    first = await _deliver(coordinator, writer)
    successor = _enqueue(
        store,
        value_text="50",
        received_at_ms=210,
        item_name="BAT_AC",
    )
    first_raw = b"first"
    first_decision = await coordinator.handle_local_response(
        active=first,
        response=_response(first_raw, rdt_text="06.08.2026 10:12:02"),
        context=_context(first, first_raw, received_at_ms=220),
        writer=writer,
    )
    assert first_decision.next_attempt is not None
    assert first_decision.next_attempt.command_id == successor.command_id
    second = first_decision.next_attempt
    second_raw = b"second"

    result = await coordinator.handle_local_response(
        active=second,
        response=_response(second_raw, rdt_text="06.08.2026 10:12:01"),
        context=_context(second, second_raw, received_at_ms=230),
        writer=writer,
    )

    assert result.disposition is LocalResponseDisposition.REJECTED
    assert result.close_connection is True
    assert (await coordinator.read_command(second.command_id)).state is (
        CommandState.RETRY_PENDING
    )


@pytest.mark.asyncio
async def test_event_match_prefers_awaiting_event_before_direct_active_attempt(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    first = await _deliver(coordinator, writer)
    raw = b"ack"
    await coordinator.handle_local_response(
        active=first,
        response=_response(raw),
        context=_context(first, raw, received_at_ms=220),
        writer=writer,
    )
    _enqueue(store, value_text="3", received_at_ms=230)
    second = await _deliver(coordinator, writer, session="session-b", now_ms=240)
    event = _event()
    token = coordinator.register_setting_event(
        event=event,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "session-b", "123", 250, b"event"
        ),
    )

    decision = await coordinator.handle_registered_event(token)

    assert decision.disposition is EventDisposition.CONFIRMED
    assert decision.confirmation is not None
    assert decision.confirmation.command_id == first.command_id
    assert decision.active_session_id is None
    assert store.read_command(second.command_id).state is CommandState.AWAITING_ACK


@pytest.mark.asyncio
async def test_direct_event_confirms_and_reports_owning_dialogue(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            220,
            b"direct-event",
        ),
    )

    decision = await coordinator.handle_registered_event(token)

    assert decision.disposition is EventDisposition.CONFIRMED
    assert decision.prior_state is CommandState.AWAITING_ACK
    assert decision.active_session_id == active.session_id
    assert decision.confirmation is not None
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED


def test_event_registration_rejects_wrong_direction_or_device_without_store_mutation(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    before = store.status_snapshot()
    event = _event()

    with pytest.raises(ValueError, match="BOX-to-proxy"):
        coordinator.register_setting_event(
            event=event,
            context=EvidenceContext(
                FrameDirection.CLOUD_TO_PROXY, "s", "123", 200, b"event"
            ),
        )
    with pytest.raises(ValueError, match="device"):
        coordinator.register_setting_event(
            event=event,
            context=EvidenceContext(
                FrameDirection.BOX_TO_PROXY, "s", "456", 200, b"event"
            ),
        )

    assert store.status_snapshot() == before


@pytest.mark.asyncio
async def test_unmatched_and_duplicate_event_never_confirm_twice(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    unmatched = _event(new_value="3")
    first_token = coordinator.register_setting_event(
        event=unmatched,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "session-a", "123", 220, b"unmatched"
        ),
    )
    first = await coordinator.handle_registered_event(first_token)
    duplicate_token = coordinator.register_setting_event(
        event=unmatched,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "session-a", "123", 221, b"unmatched"
        ),
    )
    duplicate = await coordinator.handle_registered_event(duplicate_token)

    assert first.disposition is EventDisposition.UNMATCHED
    assert first.confirmation is None
    assert duplicate.disposition is EventDisposition.DUPLICATE
    assert duplicate.confirmation is None
    assert store.read_event_receipt(unmatched.evidence_id).duplicate_count == 1


@pytest.mark.asyncio
async def test_session_flush_is_lossless_and_receipt_ordered(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    first_event = _event(event_id_set=55, new_value="3")
    second_event = _event(event_id_set=56, new_value="4")
    for event, raw, received_at in (
        (first_event, b"first", 220),
        (second_event, b"second", 221),
    ):
        coordinator.register_setting_event(
            event=event,
            context=EvidenceContext(
                FrameDirection.BOX_TO_PROXY,
                "flush-session",
                "123",
                received_at,
                raw,
            ),
        )

    decisions = await coordinator.flush_registered_events(
        session_id="flush-session"
    )

    assert [decision.evidence.evidence_id for decision in decisions] == [
        first_event.evidence_id,
        second_event.evidence_id,
    ]
    assert store.read_event_receipt(first_event.evidence_id).evidence_frame == b"first"
    assert store.read_event_receipt(second_event.evidence_id).evidence_frame == b"second"
    assert await coordinator.flush_registered_events(session_id="flush-session") == ()


@pytest.mark.asyncio
async def test_registered_event_wins_final_timeout_check_to_cas_gap(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw = b"ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    await coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 1)
    monotonic["value"] = 1.1

    cas_entered = threading.Event()
    cas_release = threading.Event()
    original = store.mark_event_incomplete

    def blocked_cas(**kwargs):
        cas_entered.set()
        assert cas_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "mark_event_incomplete", blocked_cas)
    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 2)
    )
    assert await asyncio.to_thread(cas_entered.wait, 0.1)
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            command.event_deadline_ms,
            b"event-in-final-gap",
        ),
    )
    cas_release.set()

    sweep = await sweep_task
    decision = await coordinator.handle_registered_event(token)
    reasons = tuple(
        transition.reason
        for transition in store.read_transitions(active.command_id)
    )
    assert sweep.incomplete_event_timeout == 0
    assert decision.disposition is EventDisposition.CONFIRMED
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED
    assert "event_timeout" not in reasons


@pytest.mark.asyncio
async def test_registered_event_wins_after_transition_before_commit(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw = b"ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    await coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 1)
    monotonic["value"] = 1.1

    transition_written = threading.Event()
    transition_release = threading.Event()
    original = store._transition_command_state_locked  # pylint: disable=protected-access

    def blocked_transition(connection, **kwargs):
        snapshot = original(connection, **kwargs)
        if kwargs.get("reason") == "event_timeout":
            transition_written.set()
            if not transition_release.wait(timeout=0.25):
                raise RuntimeError("registration blocked behind SQLite mutation")
        return snapshot

    monkeypatch.setattr(
        store, "_transition_command_state_locked", blocked_transition
    )
    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 2)
    )
    assert await asyncio.to_thread(transition_written.wait, 0.1)
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            command.event_deadline_ms,
            b"event-before-commit",
        ),
    )
    transition_release.set()

    sweep = await sweep_task
    decision = await coordinator.handle_registered_event(token)
    reasons = tuple(
        transition.reason
        for transition in store.read_transitions(active.command_id)
    )
    assert sweep.incomplete_event_timeout == 0
    assert decision.disposition is EventDisposition.CONFIRMED
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED
    assert "event_timeout" not in reasons


@pytest.mark.asyncio
async def test_registration_and_heartbeat_progress_during_authorized_commit(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw = b"ack-before-paused-commit"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    await coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 1)
    monotonic["value"] = 1.1
    commit_entered = threading.Event()
    commit_release = threading.Event()
    connection = store._connection  # pylint: disable=protected-access
    assert connection is not None
    store._connection = cast(  # pylint: disable=protected-access
        Any,
        _PausedCommitConnection(
            connection,
            entered=commit_entered,
            release=commit_release,
        ),
    )

    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 2)
    )
    assert await asyncio.to_thread(commit_entered.wait, 0.1)
    token, heartbeat_progressed = await _register_during_paused_commit(
        coordinator,
        session_id=active.session_id,
        deadline_ms=command.event_deadline_ms,
        commit_release=commit_release,
    )

    sweep = await sweep_task
    decision = await coordinator.handle_registered_event(token)

    assert heartbeat_progressed is True
    assert sweep.incomplete_event_timeout == 1
    assert decision.disposition is EventDisposition.UNMATCHED
    assert store.read_command(active.command_id).state is CommandState.INCOMPLETE


@pytest.mark.asyncio
async def test_commit_failure_clears_timeout_reservation_for_registered_event(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw = b"ack-before-failed-commit"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    await coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 1)
    monotonic["value"] = 1.1
    commit_entered = threading.Event()
    commit_release = threading.Event()
    connection = store._connection  # pylint: disable=protected-access
    assert connection is not None
    store._connection = cast(  # pylint: disable=protected-access
        Any,
        _PausedCommitConnection(
            connection,
            entered=commit_entered,
            release=commit_release,
            failure=_InjectedCommitFailure("injected pre-commit failure"),
        ),
    )

    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 2)
    )
    assert await asyncio.to_thread(commit_entered.wait, 0.1)
    token, heartbeat_progressed = await _register_during_paused_commit(
        coordinator,
        session_id=active.session_id,
        deadline_ms=command.event_deadline_ms,
        commit_release=commit_release,
    )

    with pytest.raises(_InjectedCommitFailure, match="pre-commit failure"):
        await sweep_task
    decision = await coordinator.handle_registered_event(token)
    reasons = tuple(
        transition.reason
        for transition in store.read_transitions(active.command_id)
    )

    assert heartbeat_progressed is True
    assert decision.disposition is EventDisposition.CONFIRMED
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED
    assert "event_timeout" not in reasons


@pytest.mark.asyncio
async def test_cancelled_event_timeout_drains_and_protected_event_wins(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw = b"ack-before-cancelled-timeout"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    await coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 1)
    monotonic["value"] = 1.1
    transition_entered = threading.Event()
    transition_release = threading.Event()
    original = store._transition_command_state_locked  # pylint: disable=protected-access

    def blocked_transition(connection, **kwargs):
        snapshot = original(connection, **kwargs)
        if kwargs.get("reason") == "event_timeout":
            transition_entered.set()
            assert transition_release.wait(timeout=1)
        return snapshot

    monkeypatch.setattr(
        store, "_transition_command_state_locked", blocked_transition
    )
    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=command.event_deadline_ms + 2)
    )
    assert await asyncio.to_thread(transition_entered.wait, 0.1)

    sweep_task.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    cancellation_waited_for_store = not sweep_task.done()
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            command.event_deadline_ms,
            b"event-protected-from-cancelled-timeout",
        ),
    )
    transition_release.set()

    with pytest.raises(asyncio.CancelledError):
        await sweep_task
    decision = await coordinator.handle_registered_event(token)
    reasons = tuple(
        transition.reason
        for transition in store.read_transitions(active.command_id)
    )

    assert cancellation_waited_for_store is True
    assert decision.disposition is EventDisposition.CONFIRMED
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED
    assert "event_timeout" not in reasons


@pytest.mark.asyncio
async def test_concurrent_event_handlers_claim_one_token_once(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = coordinator.register_setting_event(
        event=_event(new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "s", "123", 220, b"one-token"
        ),
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    call_count = 0
    original = store.record_event

    def blocked_record(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_entered.set()
            assert first_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", blocked_record)
    first_task = asyncio.create_task(coordinator.handle_registered_event(token))
    assert await asyncio.to_thread(first_entered.wait, 0.1)
    second_task = asyncio.create_task(coordinator.handle_registered_event(token))
    first_release.set()

    outcomes = await asyncio.gather(
        first_task, second_task, return_exceptions=True
    )
    decisions = [
        outcome for outcome in outcomes if not isinstance(outcome, BaseException)
    ]
    failures = [
        outcome for outcome in outcomes if isinstance(outcome, BaseException)
    ]
    assert call_count == 1
    assert len(decisions) == 1
    assert decisions[0].disposition is EventDisposition.UNMATCHED
    assert len(failures) == 1
    assert isinstance(failures[0], ValueError)
    assert store.read_event_receipt(token.event.evidence_id).duplicate_count == 0


@pytest.mark.asyncio
async def test_direct_event_handler_and_session_flush_share_one_claim(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = coordinator.register_setting_event(
        event=_event(new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "cleanup-session",
            "123",
            220,
            b"cleanup-token",
        ),
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    call_count = 0
    original = store.record_event

    def blocked_record(**kwargs):
        nonlocal call_count
        call_count += 1
        first_entered.set()
        assert first_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", blocked_record)
    direct_task = asyncio.create_task(coordinator.handle_registered_event(token))
    assert await asyncio.to_thread(first_entered.wait, 0.1)
    flush_task = asyncio.create_task(
        coordinator.flush_registered_events(session_id="cleanup-session")
    )
    await asyncio.sleep(0)
    first_release.set()

    direct, flushed = await asyncio.gather(direct_task, flush_task)
    assert direct.disposition is EventDisposition.UNMATCHED
    assert flushed == ()
    assert call_count == 1
    assert store.read_event_receipt(token.event.evidence_id).duplicate_count == 0


@pytest.mark.asyncio
async def test_event_token_is_restored_in_receipt_order_after_store_failure(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = coordinator.register_setting_event(
        event=_event(event_id_set=55, new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "s", "123", 220, b"first"
        ),
    )
    second = coordinator.register_setting_event(
        event=_event(event_id_set=56, new_value="4"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "s", "123", 221, b"second"
        ),
    )
    call_count = 0
    original = store.record_event

    def fail_once(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("temporary store failure")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", fail_once)

    with pytest.raises(RuntimeError, match="temporary store failure"):
        await coordinator.handle_registered_event(first)
    flushed = await coordinator.flush_registered_events(session_id="s")

    assert call_count == 3
    assert [result.evidence.evidence_id for result in flushed] == [
        first.event.evidence_id,
        second.event.evidence_id,
    ]
    assert store.read_event_receipt(first.event.evidence_id).duplicate_count == 0
    assert store.read_event_receipt(second.event.evidence_id).duplicate_count == 0


@pytest.mark.asyncio
async def test_flush_later_failure_preserves_complete_ordered_batch(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store, device_id="456", value_text="9")
    tokens = (
        _register_batch_event(
            coordinator,
            session_id="atomic-failure",
            device_id="123",
            event_id_set=101,
            received_at_ms=301,
        ),
        _register_batch_event(
            coordinator,
            session_id="atomic-failure",
            device_id="456",
            event_id_set=102,
            received_at_ms=302,
        ),
        _register_batch_event(
            coordinator,
            session_id="atomic-failure",
            device_id="123",
            event_id_set=103,
            received_at_ms=303,
        ),
    )
    calls: dict[str, int] = defaultdict(int)
    original = store.record_event

    def fail_second_once(**kwargs):
        evidence_id = kwargs["evidence"].evidence_id
        calls[evidence_id] += 1
        if evidence_id == tokens[1].event.evidence_id and calls[evidence_id] == 1:
            raise RuntimeError("later batch mutation failed")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", fail_second_once)

    with pytest.raises(RuntimeError, match="later batch mutation failed"):
        await coordinator.flush_registered_events(session_id="atomic-failure")
    delivered = await coordinator.flush_registered_events(
        session_id="atomic-failure"
    )

    assert [result.evidence.evidence_id for result in delivered] == [
        token.event.evidence_id for token in tokens
    ]
    assert calls == {
        tokens[0].event.evidence_id: 1,
        tokens[1].event.evidence_id: 2,
        tokens[2].event.evidence_id: 1,
    }
    assert await coordinator.flush_registered_events(
        session_id="atomic-failure"
    ) == ()


@pytest.mark.asyncio
async def test_cancelled_partial_flush_preserves_all_results_for_retry(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = tuple(
        _register_batch_event(
            coordinator,
            session_id="atomic-cancel",
            device_id="123",
            event_id_set=event_id_set,
            received_at_ms=300 + event_id_set,
        )
        for event_id_set in (111, 112, 113)
    )
    second_entered = threading.Event()
    second_release = threading.Event()
    calls: dict[str, int] = defaultdict(int)
    original = store.record_event

    def block_second(**kwargs):
        evidence_id = kwargs["evidence"].evidence_id
        calls[evidence_id] += 1
        if evidence_id == tokens[1].event.evidence_id:
            second_entered.set()
            assert second_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", block_second)
    flushing = asyncio.create_task(
        coordinator.flush_registered_events(session_id="atomic-cancel")
    )
    assert await asyncio.to_thread(second_entered.wait, 0.1)

    flushing.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    second_release.set()
    with pytest.raises(asyncio.CancelledError):
        await flushing
    delivered = await coordinator.flush_registered_events(
        session_id="atomic-cancel"
    )

    assert [result.evidence.evidence_id for result in delivered] == [
        token.event.evidence_id for token in tokens
    ]
    assert calls == {
        token.event.evidence_id: 1 for token in tokens
    }
    assert await coordinator.flush_registered_events(
        session_id="atomic-cancel"
    ) == ()


@pytest.mark.asyncio
async def test_flush_batch_excludes_competing_handler_consumption(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = tuple(
        _register_batch_event(
            coordinator,
            session_id="atomic-competition",
            device_id="123",
            event_id_set=event_id_set,
            received_at_ms=400 + event_id_set,
        )
        for event_id_set in (121, 122, 123)
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    calls: dict[str, int] = defaultdict(int)
    original = store.record_event

    def block_first(**kwargs):
        evidence_id = kwargs["evidence"].evidence_id
        calls[evidence_id] += 1
        if evidence_id == tokens[0].event.evidence_id:
            first_entered.set()
            assert first_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", block_first)
    flushing = asyncio.create_task(
        coordinator.flush_registered_events(session_id="atomic-competition")
    )
    assert await asyncio.to_thread(first_entered.wait, 0.1)
    competing = asyncio.create_task(
        coordinator.handle_registered_event(tokens[1])
    )
    scheduling_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(scheduling_turn.set)
    await scheduling_turn.wait()
    first_release.set()

    with pytest.raises(ValueError, match="already claimed"):
        await competing
    delivered = await flushing

    assert [result.evidence.evidence_id for result in delivered] == [
        token.event.evidence_id for token in tokens
    ]
    assert calls == {
        token.event.evidence_id: 1 for token in tokens
    }


@pytest.mark.asyncio
async def test_flush_waits_for_earlier_handler_before_later_receipt(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store, device_id="456", value_text="9")
    first = _register_batch_event(
        coordinator,
        session_id="handler-dependency",
        device_id="123",
        event_id_set=131,
        received_at_ms=531,
    )
    second = _register_batch_event(
        coordinator,
        session_id="handler-dependency",
        device_id="456",
        event_id_set=132,
        received_at_ms=532,
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    second_entered = threading.Event()
    calls: dict[str, int] = defaultdict(int)
    original = store.record_event

    def ordered_record(**kwargs):
        evidence_id = kwargs["evidence"].evidence_id
        calls[evidence_id] += 1
        if evidence_id == first.event.evidence_id:
            first_entered.set()
            assert first_release.wait(timeout=1)
        elif evidence_id == second.event.evidence_id:
            second_entered.set()
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", ordered_record)
    handler = asyncio.create_task(coordinator.handle_registered_event(first))
    assert await asyncio.to_thread(first_entered.wait, 0.1)
    flushing = asyncio.create_task(
        coordinator.flush_registered_events(session_id="handler-dependency")
    )
    scheduling_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(scheduling_turn.set)
    await scheduling_turn.wait()
    later_waited = not second_entered.is_set()
    first_release.set()

    handled = await handler
    delivered = await flushing

    assert later_waited is True
    assert handled.evidence.evidence_id == first.event.evidence_id
    assert [result.evidence.evidence_id for result in delivered] == [
        second.event.evidence_id
    ]
    assert calls == {
        first.event.evidence_id: 1,
        second.event.evidence_id: 1,
    }


@pytest.mark.asyncio
async def test_competing_flush_waits_for_active_batch_completion(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = tuple(
        _register_batch_event(
            coordinator,
            session_id="flush-competition",
            device_id="123",
            event_id_set=event_id_set,
            received_at_ms=600 + event_id_set,
        )
        for event_id_set in (141, 142)
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    calls: dict[str, int] = defaultdict(int)
    original = store.record_event

    def blocked_first(**kwargs):
        evidence_id = kwargs["evidence"].evidence_id
        calls[evidence_id] += 1
        if evidence_id == tokens[0].event.evidence_id:
            first_entered.set()
            assert first_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", blocked_first)
    first_flush = asyncio.create_task(
        coordinator.flush_registered_events(session_id="flush-competition")
    )
    assert await asyncio.to_thread(first_entered.wait, 0.1)
    second_flush = asyncio.create_task(
        coordinator.flush_registered_events(session_id="flush-competition")
    )
    scheduling_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(scheduling_turn.set)
    await scheduling_turn.wait()
    contender_waited = not second_flush.done()
    first_release.set()

    first_result, second_result = await asyncio.gather(
        first_flush, second_flush
    )

    assert contender_waited is True
    assert [result.evidence.evidence_id for result in first_result] == [
        token.event.evidence_id for token in tokens
    ]
    assert second_result == ()
    assert calls == {
        token.event.evidence_id: 1 for token in tokens
    }


@pytest.mark.asyncio
async def test_cancelled_event_owner_drains_success_and_cleanup_adopts_once(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw_ack = b"cancel-owner-ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw_ack),
        context=_context(active, raw_ack, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            command.event_deadline_ms,
            b"cancel-owner-event",
        ),
    )
    worker_entered = threading.Event()
    worker_release = threading.Event()
    worker_finished = threading.Event()
    call_count = 0
    original = store.record_event

    def blocked_record(**kwargs):
        nonlocal call_count
        call_count += 1
        worker_entered.set()
        assert worker_release.wait(timeout=1)
        try:
            return original(**kwargs)
        finally:
            worker_finished.set()

    monkeypatch.setattr(store, "record_event", blocked_record)
    owner = asyncio.create_task(coordinator.handle_registered_event(token))
    assert await asyncio.to_thread(worker_entered.wait, 0.1)

    owner.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    owner_waited_for_worker = not owner.done()
    worker_release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert await asyncio.to_thread(worker_finished.wait, 0.1)

    adopted = await coordinator.flush_registered_events(
        session_id=active.session_id
    )

    assert owner_waited_for_worker is True
    assert call_count == 1
    assert len(adopted) == 1
    assert adopted[0].disposition is EventDisposition.CONFIRMED
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED
    assert [record.step for record in records].count(
        SettingStep.EVENT_CONFIRMED
    ) == 1
    assert await coordinator.flush_registered_events(
        session_id=active.session_id
    ) == ()


@pytest.mark.asyncio
async def test_cancelled_event_owner_failure_is_retryable_without_flush_spin(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = coordinator.register_setting_event(
        event=_event(new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "cancelled-failure-session",
            "123",
            220,
            b"cancelled-failure-event",
        ),
    )
    worker_entered = threading.Event()
    worker_release = threading.Event()
    call_count = 0
    original = store.record_event

    def fail_once(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            worker_entered.set()
            assert worker_release.wait(timeout=1)
            raise RuntimeError("definitive event-store failure")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", fail_once)
    owner = asyncio.create_task(coordinator.handle_registered_event(token))
    assert await asyncio.to_thread(worker_entered.wait, 0.1)

    owner.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    observing_flush = asyncio.create_task(
        coordinator.flush_registered_events(
            session_id="cancelled-failure-session"
        )
    )
    observer_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(observer_turn.set)
    await observer_turn.wait()
    worker_release.set()

    with pytest.raises(asyncio.CancelledError):
        await owner
    with pytest.raises(RuntimeError, match="definitive event-store failure"):
        await observing_flush
    assert call_count == 1

    retried = await coordinator.flush_registered_events(
        session_id="cancelled-failure-session"
    )

    assert call_count == 2
    assert len(retried) == 1
    assert retried[0].evidence.evidence_id == token.event.evidence_id
    assert await coordinator.flush_registered_events(
        session_id="cancelled-failure-session"
    ) == ()


@pytest.mark.asyncio
async def test_cancelled_event_after_store_completion_drains_reconciliation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw_ack = b"cancel-after-store-ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw_ack),
        context=_context(active, raw_ack, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            command.event_deadline_ms,
            b"cancel-after-store-event",
        ),
    )
    refresh_entered = asyncio.Event()
    refresh_release = asyncio.Event()
    original_refresh = coordinator._refresh_status  # pylint: disable=protected-access

    async def blocked_refresh() -> tuple[
        BaseException | None,
        asyncio.CancelledError | None,
    ]:
        refresh_entered.set()
        await refresh_release.wait()
        return await original_refresh()

    monkeypatch.setattr(coordinator, "_refresh_status", blocked_refresh)
    owner = asyncio.create_task(coordinator.handle_registered_event(token))
    await refresh_entered.wait()

    owner.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    waited_for_reconciliation = not owner.done()
    refresh_release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner

    adopted = await coordinator.flush_registered_events(
        session_id=active.session_id
    )

    assert waited_for_reconciliation is True
    assert len(adopted) == 1
    assert adopted[0].disposition is EventDisposition.CONFIRMED
    assert [record.step for record in records].count(
        SettingStep.EVENT_CONFIRMED
    ) == 1


@pytest.mark.asyncio
async def test_cancelled_event_after_reconciliation_is_adopted_once(
    coordinator: TwinCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = coordinator.register_setting_event(
        event=_event(new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "post-reconciliation-session",
            "123",
            220,
            b"post-reconciliation-event",
        ),
    )
    reconciled = asyncio.Event()
    result_release = asyncio.Event()
    original_mutation = coordinator._run_mutation_locked  # pylint: disable=protected-access

    async def block_after_reconciliation(*args, **kwargs):
        result = await original_mutation(*args, **kwargs)
        reconciled.set()
        await result_release.wait()
        return result

    monkeypatch.setattr(
        coordinator, "_run_mutation_locked", block_after_reconciliation
    )
    owner = asyncio.create_task(coordinator.handle_registered_event(token))
    await reconciled.wait()

    owner.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    waited_for_result_delivery = not owner.done()
    result_release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner

    adopted = await coordinator.flush_registered_events(
        session_id="post-reconciliation-session"
    )

    assert waited_for_result_delivery is True
    assert len(adopted) == 1
    assert adopted[0].evidence.evidence_id == token.event.evidence_id
    assert await coordinator.flush_registered_events(
        session_id="post-reconciliation-session"
    ) == ()


@pytest.mark.asyncio
async def test_cancelled_cleanup_adopter_retains_reconciled_result_once(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = coordinator.register_setting_event(
        event=_event(new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "cancelled-adopter-session",
            "123",
            220,
            b"cancelled-adopter-event",
        ),
    )
    worker_entered = threading.Event()
    worker_release = threading.Event()
    original_record = store.record_event
    adopter: asyncio.Task[tuple[EventMatchResult, ...]] | None = None
    original_mutation = coordinator._run_mutation_locked  # pylint: disable=protected-access

    def blocked_record(**kwargs):
        worker_entered.set()
        assert worker_release.wait(timeout=1)
        return original_record(**kwargs)

    async def cancel_adopter_after_reconciliation(*args, **kwargs):
        result = await original_mutation(*args, **kwargs)
        assert adopter is not None
        asyncio.get_running_loop().call_soon(adopter.cancel)
        return result

    monkeypatch.setattr(store, "record_event", blocked_record)
    monkeypatch.setattr(
        coordinator,
        "_run_mutation_locked",
        cancel_adopter_after_reconciliation,
    )
    owner = asyncio.create_task(coordinator.handle_registered_event(token))
    assert await asyncio.to_thread(worker_entered.wait, 0.1)
    owner.cancel()
    owner_relinquished = asyncio.Event()
    asyncio.get_running_loop().call_soon(owner_relinquished.set)
    await owner_relinquished.wait()
    adopter = asyncio.create_task(
        coordinator.flush_registered_events(
            session_id="cancelled-adopter-session"
        )
    )
    worker_release.set()

    with pytest.raises(asyncio.CancelledError):
        await owner
    with pytest.raises(asyncio.CancelledError):
        await adopter

    adopted = await coordinator.flush_registered_events(
        session_id="cancelled-adopter-session"
    )

    assert len(adopted) == 1
    assert adopted[0].evidence.evidence_id == token.event.evidence_id
    assert await coordinator.flush_registered_events(
        session_id="cancelled-adopter-session"
    ) == ()


@pytest.mark.asyncio
async def test_restored_event_keeps_sequence_across_consumed_and_later_tokens(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumed = coordinator.register_setting_event(
        event=_event(device_id="999", event_id_set=1, new_value="1"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "three-token-session",
            "999",
            210,
            b"consumed-first",
        ),
    )
    restored = coordinator.register_setting_event(
        event=_event(event_id_set=2, new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "three-token-session",
            "123",
            220,
            b"restored-second",
        ),
    )
    assert (
        await coordinator.handle_registered_event(consumed)
    ).disposition is EventDisposition.UNMATCHED
    call_count = 0
    original = store.record_event

    def fail_once(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("restore this exact token")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", fail_once)
    with pytest.raises(RuntimeError, match="restore this exact token"):
        await coordinator.handle_registered_event(restored)
    later = coordinator.register_setting_event(
        event=_event(event_id_set=3, new_value="4"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "three-token-session",
            "123",
            230,
            b"registered-later",
        ),
    )

    flushed = await coordinator.flush_registered_events(
        session_id="three-token-session"
    )

    assert call_count == 3
    assert [result.evidence.evidence_id for result in flushed] == [
        restored.event.evidence_id,
        later.event.evidence_id,
    ]


@pytest.mark.asyncio
async def test_cancelled_prepare_keeps_device_lock_until_commit_reconciles(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    second_entered = threading.Event()
    call_count = 0
    original = store.prepare_next_attempt

    def blocked_prepare(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_entered.set()
            assert first_release.wait(timeout=1)
        else:
            second_entered.set()
        return original(**kwargs)

    monkeypatch.setattr(store, "prepare_next_attempt", blocked_prepare)
    first = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="first-session",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )
    )
    assert await asyncio.to_thread(first_entered.wait, 0.1)
    first.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()

    second = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="second-session",
            received_at_ms=201,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )
    )
    scheduling_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(scheduling_turn.set)
    await scheduling_turn.wait()
    second_waited_for_device = not second_entered.is_set()
    first_waited_for_store = not first.done()
    first_release.set()

    with pytest.raises(asyncio.CancelledError):
        await first
    second_result = await second

    assert first_waited_for_store is True
    assert second_waited_for_device is True
    assert second_result.disposition is DeliveryDisposition.ACTIVE_DELIVERY_ELSEWHERE
    assert [record.step for record in records].count(SettingStep.SELECTED) == 1
    assert [record.step for record in records].count(
        SettingStep.ATTEMPT_PREPARED
    ) == 1
    assert coordinator.cached_status_snapshot.awaiting_ack == 1


@pytest.mark.asyncio
async def test_direct_internal_prepare_cancellation_keeps_mutation_owned(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    second_entered = threading.Event()
    calls = 0
    original = store.prepare_next_attempt

    def blocked_prepare(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            assert first_release.wait(timeout=1)
        else:
            second_entered.set()
        return original(**kwargs)

    monkeypatch.setattr(store, "prepare_next_attempt", blocked_prepare)
    baseline = asyncio.all_tasks()
    first = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="first-session",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )
    )
    assert await asyncio.to_thread(first_entered.wait, 0.1)

    await _cancel_owner_and_internal_tasks(first, baseline)
    second = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="second-session",
            received_at_ms=201,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )
    )
    scheduling_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(scheduling_turn.set)
    await scheduling_turn.wait()
    public_incomplete = not first.done()
    device_lock_held = coordinator._device_lock("123").locked()  # pylint: disable=protected-access
    second_blocked = not second_entered.is_set()
    first_release.set()

    with pytest.raises(asyncio.CancelledError):
        await first
    second_result = await second

    assert public_incomplete is True
    assert device_lock_held is True
    assert second_blocked is True
    assert second_result.disposition is DeliveryDisposition.ACTIVE_DELIVERY_ELSEWHERE
    assert [record.step for record in records].count(SettingStep.SELECTED) == 1
    assert [record.step for record in records].count(
        SettingStep.ATTEMPT_PREPARED
    ) == 1
    assert coordinator.cached_status_snapshot.awaiting_ack == 1


@pytest.mark.asyncio
async def test_direct_publication_cancellation_finishes_before_device_unlock(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    proposal_entered = threading.Event()
    proposal_release = threading.Event()
    records: list[SettingsAuditRecord] = []
    original = store.propose_audit_delivery

    def blocked_proposal(**kwargs):
        proposal_entered.set()
        assert proposal_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "propose_audit_delivery", blocked_proposal)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    baseline = asyncio.all_tasks()
    delivery = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="publication-session",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )
    )
    assert await asyncio.to_thread(proposal_entered.wait, 0.1)

    await _cancel_owner_and_internal_tasks(delivery, baseline)
    public_incomplete = not delivery.done()
    device_lock_held = coordinator._device_lock("123").locked()  # pylint: disable=protected-access
    proposal_release.set()

    with pytest.raises(asyncio.CancelledError):
        await delivery

    assert public_incomplete is True
    assert device_lock_held is True
    assert [record.step for record in records].count(SettingStep.SELECTED) == 1
    assert [record.step for record in records].count(
        SettingStep.ATTEMPT_PREPARED
    ) == 1
    assert coordinator.cached_status_snapshot.awaiting_ack == 1


def test_control_flow_chain_keeps_first_repeated_identity() -> None:
    first = asyncio.CancelledError("first worker cancellation")
    second = asyncio.CancelledError("second worker cancellation")

    chain = TwinCoordinator._control_flow_chain(  # pylint: disable=protected-access
        first,
        second,
        first,
    )

    assert chain is first
    assert first.__cause__ is second
    assert second.__cause__ is None


def test_owner_control_flow_chain_excludes_repeated_owner_identity() -> None:
    owner = asyncio.CancelledError("owner cancellation")
    worker = asyncio.CancelledError("worker cancellation")
    tail = SystemExit("cleanup control flow")

    with pytest.raises(asyncio.CancelledError) as caught:
        TwinCoordinator._raise_owner_over_control_flow(  # pylint: disable=protected-access
            owner,
            worker,
            owner,
            tail,
        )

    assert caught.value is owner
    assert owner.__cause__ is worker
    assert worker.__cause__ is tail
    assert tail.__cause__ is None


def test_latched_owner_cancellation_breaks_existing_self_cycle() -> None:
    owner = asyncio.CancelledError("self-caused owner cancellation")
    owner.__cause__ = owner

    with pytest.raises(asyncio.CancelledError) as caught:
        TwinCoordinator._raise_latched_cancellation(  # pylint: disable=protected-access
            owner,
            None,
        )

    assert caught.value is owner
    assert owner.__cause__ is None


def test_owner_chain_keeps_shared_tail_before_distinct_workers() -> None:
    owner = asyncio.CancelledError("owner cancellation")
    prior_error = RuntimeError("shared prior cause")
    first_worker = asyncio.CancelledError("first worker cancellation")
    second_worker = asyncio.CancelledError("second worker cancellation")
    owner.__cause__ = prior_error
    first_worker.__cause__ = prior_error

    with pytest.raises(asyncio.CancelledError) as caught:
        TwinCoordinator._raise_owner_over_control_flow(  # pylint: disable=protected-access
            owner,
            first_worker,
            second_worker,
        )

    assert caught.value is owner
    assert owner.__cause__ is prior_error
    assert prior_error.__cause__ is first_worker
    assert first_worker.__cause__ is second_worker
    assert second_worker.__cause__ is None


@pytest.mark.asyncio
async def test_publisher_worker_cancellation_remains_exact_after_reconciliation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    command = _enqueue(store)
    sentinel = asyncio.CancelledError("publisher worker cancelled")

    class CancelledPublisher:
        """Return one exact cancellation object for every committed snapshot."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            self.calls += 1
            raise sentinel

    publisher = CancelledPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.claim_and_write_next(
            device_id="123",
            session_id="publisher-cancellation-session",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )

    assert caught.value is sentinel
    assert publisher.calls == 2
    committed = store.read_command(command.command_id)
    assert committed.state is CommandState.AWAITING_ACK
    assert committed.active_session_id == "publisher-cancellation-session"
    assert coordinator.cached_status_snapshot.awaiting_ack == 1
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_owner_cancellation_dominates_prior_publisher_worker_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    worker_error = asyncio.CancelledError("publisher worker cancelled")
    second_entered = asyncio.Event()
    second_release = asyncio.Event()

    class WorkerThenBlockingPublisher:
        """Cancel one publication, then expose owner cancellation on the next."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            self.calls += 1
            if self.calls == 1:
                raise worker_error
            second_entered.set()
            await second_release.wait()

    publisher = WorkerThenBlockingPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )
    snapshot = cast(TransitionAuditSnapshot, object())
    publishing = asyncio.create_task(
        coordinator._publish((snapshot, snapshot))  # pylint: disable=protected-access
    )
    await second_entered.wait()

    publishing.cancel("owner cancelled")
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    waited_for_publisher = not publishing.done()
    second_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await publishing

    assert waited_for_publisher is True
    assert caught.value is not worker_error
    assert caught.value.args == ("owner cancelled",)
    assert caught.value.__cause__ is worker_error
    assert publisher.calls == 2


@pytest.mark.asyncio
async def test_publication_retains_multiple_worker_cancellations_in_order(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    worker_errors = (
        asyncio.CancelledError("first publisher worker cancelled"),
        asyncio.CancelledError("second publisher worker cancelled"),
    )

    class SequencedCancelledPublisher:
        """Return distinct exact cancellations for consecutive snapshots."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            error = worker_errors[self.calls]
            self.calls += 1
            raise error

    publisher = SequencedCancelledPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )
    snapshot = cast(TransitionAuditSnapshot, object())

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator._publish(  # pylint: disable=protected-access
            (snapshot, snapshot)
        )

    assert caught.value is worker_errors[0]
    assert worker_errors[0].__cause__ is worker_errors[1]
    assert publisher.calls == 2


@pytest.mark.asyncio
async def test_publication_retains_existing_worker_cause_before_next_worker(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    prior_error = RuntimeError("first publisher cancellation cause")
    first_worker = asyncio.CancelledError("first publisher worker cancelled")
    second_worker = asyncio.CancelledError("second publisher worker cancelled")

    class ChainedThenCancelledPublisher:
        """Return a nested first cancellation and a distinct second one."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            self.calls += 1
            if self.calls == 1:
                raise first_worker from prior_error
            raise second_worker

    publisher = ChainedThenCancelledPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )
    snapshot = cast(TransitionAuditSnapshot, object())

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator._publish(  # pylint: disable=protected-access
            (snapshot, snapshot)
        )

    assert caught.value is first_worker
    assert first_worker.__cause__ is prior_error
    assert prior_error.__cause__ is second_worker
    assert second_worker.__cause__ is None
    assert publisher.calls == 2


@pytest.mark.asyncio
async def test_direct_publication_owner_retains_error_and_worker_chain(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_error = asyncio.CancelledError("publisher worker cancelled")
    publication_error = RuntimeError("later publication failed")
    second_entered = asyncio.Event()
    second_release = asyncio.Event()

    class WorkerThenFailPublisher:
        """Return worker control flow before a later ordinary failure."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            self.calls += 1
            if self.calls == 1:
                raise worker_error
            second_entered.set()
            await second_release.wait()
            raise publication_error

    publisher = WorkerThenFailPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    snapshot = cast(TransitionAuditSnapshot, object())
    owner = asyncio.create_task(
        coordinator._publish(  # pylint: disable=protected-access
            (snapshot, snapshot)
        )
    )
    await second_entered.wait()
    owner.cancel("publication owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    second_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.__cause__ is publication_error
    assert publication_error.__cause__ is worker_error
    assert publisher.calls == 2


@pytest.mark.asyncio
async def test_mutation_owner_retains_publication_error_before_worker(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_error = asyncio.CancelledError("publisher worker cancelled")
    publication_error = RuntimeError("later publication failed")
    second_entered = asyncio.Event()
    second_release = asyncio.Event()

    class WorkerThenFailPublisher:
        """Expose an owner cancellation after one exact worker cancellation."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            self.calls += 1
            if self.calls == 1:
                raise worker_error
            second_entered.set()
            await second_release.wait()
            raise publication_error

    publisher = WorkerThenFailPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    snapshot = cast(TransitionAuditSnapshot, object())
    owner = asyncio.create_task(
        coordinator._run_mutation_locked(  # pylint: disable=protected-access
            lambda: "committed",
            snapshots=lambda _result: (snapshot, snapshot),
        )
    )
    await second_entered.wait()
    owner.cancel("mutation owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    second_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.__cause__ is publication_error
    assert publication_error.__cause__ is worker_error
    assert worker_error.__cause__ is None
    assert publisher.calls == 2


@pytest.mark.asyncio
async def test_mutation_owner_during_status_dominates_publisher_worker_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_error = asyncio.CancelledError("publisher worker cancelled")
    status_entered = threading.Event()
    status_release = threading.Event()
    original_status = store.status_snapshot

    class CancelledPublisher:
        """Return one exact worker cancellation before status cleanup."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise worker_error

    def blocked_status(*args: Any, **kwargs: Any) -> StoreStatus:
        status_entered.set()
        assert status_release.wait(timeout=1)
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", blocked_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    snapshot = cast(TransitionAuditSnapshot, object())

    async def mutate_under_device_lock() -> None:
        async with coordinator._device_lock("123"):  # pylint: disable=protected-access
            await coordinator._run_mutation_locked(  # pylint: disable=protected-access
                lambda: "committed",
                snapshots=lambda _result: (snapshot,),
            )

    owner = asyncio.create_task(mutate_under_device_lock())
    assert await asyncio.to_thread(status_entered.wait, 0.1)
    owner.cancel("mutation owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    assert owner.done() is False
    status_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.args == ("mutation owner cancelled",)
    assert caught.value.__cause__ is worker_error
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_mutation_owner_retains_publication_and_status_worker_chain(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_error = asyncio.CancelledError("publisher worker cancelled")
    status_error = asyncio.CancelledError("status worker cancelled")
    status_entered = threading.Event()
    status_release = threading.Event()

    class CancelledPublisher:
        """Return the first exact worker cancellation before status cleanup."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise publication_error

    def cancelled_status() -> StoreStatus:
        status_entered.set()
        assert status_release.wait(timeout=1)
        raise status_error

    monkeypatch.setattr(store, "status_snapshot", cancelled_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    snapshot = cast(TransitionAuditSnapshot, object())
    owner = asyncio.create_task(
        coordinator._run_mutation_locked(  # pylint: disable=protected-access
            lambda: "committed",
            snapshots=lambda _result: (snapshot,),
        )
    )
    assert await asyncio.to_thread(status_entered.wait, 0.1)
    owner.cancel("mutation owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    status_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.__cause__ is publication_error
    assert publication_error.__cause__ is status_error


@pytest.mark.asyncio
async def test_prior_mutation_owner_retains_publisher_worker_as_cause(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation_entered = threading.Event()
    mutation_release = threading.Event()
    worker_error = asyncio.CancelledError("publisher worker cancelled")

    class CancelledPublisher:
        """Return one exact worker cancellation after the owner is cancelled."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise worker_error

    def blocked_mutation() -> str:
        mutation_entered.set()
        assert mutation_release.wait(timeout=1)
        return "committed"

    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    snapshot = cast(TransitionAuditSnapshot, object())
    owner = asyncio.create_task(
        coordinator._run_mutation_locked(  # pylint: disable=protected-access
            blocked_mutation,
            snapshots=lambda _result: (snapshot,),
        )
    )
    assert await asyncio.to_thread(mutation_entered.wait, 0.1)
    owner.cancel("mutation owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    mutation_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.args == ("mutation owner cancelled",)
    assert caught.value.__cause__ is worker_error


@pytest.mark.asyncio
async def test_prior_mutation_owner_retains_snapshot_worker_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation_entered = threading.Event()
    mutation_release = threading.Event()
    snapshot_error = asyncio.CancelledError("snapshot projection cancelled")

    def blocked_mutation() -> str:
        mutation_entered.set()
        assert mutation_release.wait(timeout=1)
        return "committed"

    def cancelled_snapshots(
        _result: Any,
    ) -> tuple[TransitionAuditSnapshot, ...]:
        raise snapshot_error

    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    owner = asyncio.create_task(
        coordinator._run_mutation_locked(  # pylint: disable=protected-access
            blocked_mutation,
            snapshots=cancelled_snapshots,
        )
    )
    assert await asyncio.to_thread(mutation_entered.wait, 0.1)
    owner.cancel("mutation owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    mutation_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.__cause__ is snapshot_error
    assert snapshot_error.__cause__ is None


@pytest.mark.asyncio
async def test_later_mutation_owner_dominates_snapshot_worker_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_error = asyncio.CancelledError("snapshot projection cancelled")
    status_entered = threading.Event()
    status_release = threading.Event()
    original_status = store.status_snapshot

    def cancelled_snapshots(
        _result: Any,
    ) -> tuple[TransitionAuditSnapshot, ...]:
        raise snapshot_error

    def blocked_status(*args: Any, **kwargs: Any) -> StoreStatus:
        status_entered.set()
        assert status_release.wait(timeout=1)
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", blocked_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    owner = asyncio.create_task(
        coordinator._run_mutation_locked(  # pylint: disable=protected-access
            lambda: "committed",
            snapshots=cancelled_snapshots,
        )
    )
    assert await asyncio.to_thread(status_entered.wait, 0.1)
    owner.cancel("mutation owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    status_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.__cause__ is snapshot_error
    assert snapshot_error.__cause__ is None


@pytest.mark.asyncio
async def test_mutation_owner_dominates_publication_task_creation_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation_entered = threading.Event()
    mutation_release = threading.Event()
    status_entered = threading.Event()
    status_release = threading.Event()
    worker_error = asyncio.CancelledError("publication task creation cancelled")
    original_status = store.status_snapshot

    class UnusedPublisher:
        """The injected task-creation failure prevents publication."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise AssertionError("publisher must not run")

    def blocked_mutation() -> str:
        mutation_entered.set()
        assert mutation_release.wait(timeout=1)
        return "committed"

    def blocked_status(*args: Any, **kwargs: Any) -> StoreStatus:
        status_entered.set()
        assert status_release.wait(timeout=1)
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", blocked_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=UnusedPublisher(),
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    loop = asyncio.get_running_loop()
    original_create_task = loop.create_task
    snapshot = cast(TransitionAuditSnapshot, object())
    owner = original_create_task(
        coordinator._run_mutation_locked(  # pylint: disable=protected-access
            blocked_mutation,
            snapshots=lambda _result: (snapshot,),
        )
    )
    assert await asyncio.to_thread(mutation_entered.wait, 0.1)

    def fail_publication_task_creation(coroutine: Any, **_kwargs: Any) -> Any:
        coroutine.close()
        raise worker_error

    monkeypatch.setattr(loop, "create_task", fail_publication_task_creation)
    mutation_release.set()
    assert await asyncio.to_thread(status_entered.wait, 0.1)
    monkeypatch.setattr(loop, "create_task", original_create_task)
    owner.cancel("mutation owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    status_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.args == ("mutation owner cancelled",)
    assert caught.value.__cause__ is worker_error


@pytest.mark.asyncio
async def test_aggregate_owner_during_status_dominates_publisher_worker_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    worker_error = asyncio.CancelledError("aggregate publisher cancelled")
    status_entered = threading.Event()
    status_release = threading.Event()
    status_calls = 0
    original_status = store.status_snapshot

    class CancelledPublisher:
        """Cancel aggregate publication after the device commit."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise worker_error

    def block_aggregate_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 2:
            status_entered.set()
            assert status_release.wait(timeout=1)
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", block_aggregate_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    owner = asyncio.create_task(
        coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )
    )
    assert await asyncio.to_thread(status_entered.wait, 0.1)
    owner.cancel("aggregate owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    assert owner.done() is False
    status_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.args == ("aggregate owner cancelled",)
    assert caught.value.__cause__ is worker_error
    assert status_calls == 2
    assert store.read_command(command.command_id).state is CommandState.EXPIRED
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 1
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_aggregate_owner_retains_publication_and_status_worker_chain(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    publication_error = asyncio.CancelledError("aggregate publisher cancelled")
    status_error = asyncio.CancelledError("aggregate status cancelled")
    status_entered = threading.Event()
    status_release = threading.Event()
    status_calls = 0
    original_status = store.status_snapshot

    class CancelledPublisher:
        """Return the first exact aggregate worker cancellation."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise publication_error

    def cancel_aggregate_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 2:
            status_entered.set()
            assert status_release.wait(timeout=1)
            raise status_error
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", cancel_aggregate_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    owner = asyncio.create_task(
        coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )
    )
    assert await asyncio.to_thread(status_entered.wait, 0.1)
    owner.cancel("aggregate owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    status_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.__cause__ is publication_error
    assert publication_error.__cause__ is status_error
    assert store.read_command(command.command_id).state is CommandState.EXPIRED
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_aggregate_owner_retains_publication_error_before_worker(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    worker_error = asyncio.CancelledError("aggregate publisher cancelled")
    publication_error = RuntimeError("later aggregate publication failed")
    second_entered = asyncio.Event()
    second_release = asyncio.Event()

    class WorkerThenFailPublisher:
        """Expose aggregate owner cancellation after a worker cancellation."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            self.calls += 1
            if self.calls == 1:
                raise worker_error
            second_entered.set()
            await second_release.wait()
            raise publication_error

    publisher = WorkerThenFailPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    owner = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    await second_entered.wait()
    owner.cancel("aggregate owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    second_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.__cause__ is publication_error
    assert publication_error.__cause__ is worker_error
    assert worker_error.__cause__ is None
    assert publisher.calls == 2
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.EXPIRED
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.asyncio
async def test_aggregate_publisher_worker_dominates_later_status_worker(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    publication_error = asyncio.CancelledError("aggregate publisher cancelled")
    status_error = asyncio.CancelledError("aggregate status cancelled")
    status_calls = 0
    original_status = store.status_snapshot

    class CancelledPublisher:
        """Return one exact publication-worker cancellation."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise publication_error

    def cancel_aggregate_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 2:
            raise status_error
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", cancel_aggregate_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )

    assert caught.value is publication_error
    assert caught.value.__cause__ is status_error
    assert status_calls == 2
    assert store.read_command(command.command_id).state is CommandState.EXPIRED
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_busy_audit_stripe_cancellation_finishes_before_device_unlock(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    delivered: list[SettingsAuditRecord] = []
    accepted_while_locked: list[bool] = []
    acquisition_entered = asyncio.Event()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            delivered.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    lifecycle_lock = settings_audit_module._audit_lifecycle_lock(  # pylint: disable=protected-access
        command.audit_id
    )
    lifecycle_lock.acquire()
    original_acquire = (
        # pylint: disable-next=protected-access
        settings_audit_module._acquire_audit_lifecycle_lock
    )
    original_accept = store.accept_audit_delivery

    async def observed_acquire(
        lock: Any,
    ) -> asyncio.CancelledError | None:
        acquisition_entered.set()
        return await original_acquire(lock)

    def observed_accept(**kwargs):
        accepted_while_locked.append(
            coordinator._device_lock("123").locked()  # pylint: disable=protected-access
        )
        return original_accept(**kwargs)

    monkeypatch.setattr(
        settings_audit_module,
        "_acquire_audit_lifecycle_lock",
        observed_acquire,
    )
    monkeypatch.setattr(store, "accept_audit_delivery", observed_accept)
    delivery = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="busy-stripe-session",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )
    )
    await acquisition_entered.wait()

    delivery.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    cancellation_waited = not delivery.done()
    device_lock_held = coordinator._device_lock("123").locked()  # pylint: disable=protected-access
    lifecycle_lock.release()

    with pytest.raises(asyncio.CancelledError):
        await delivery

    assert cancellation_waited is True
    assert device_lock_held is True
    assert [record.step for record in delivered] == [
        SettingStep.SELECTED,
        SettingStep.ATTEMPT_PREPARED,
    ]
    assert accepted_while_locked == [True, True]
    assert store.audit_delivery_decision_count() == 2


@pytest.mark.asyncio
async def test_direct_internal_registered_event_cancellation_reconciles_once(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    active = await _deliver(
        coordinator, ScriptedLocalSettingWriter(store), now_ms=100
    )
    raw_ack = b"direct-internal-cancel-ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw_ack),
        context=_context(active, raw_ack, received_at_ms=220),
        writer=ScriptedLocalSettingWriter(store),
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            command.event_deadline_ms,
            b"direct-internal-cancel-event",
        ),
    )
    worker_entered = threading.Event()
    worker_release = threading.Event()
    original = store.record_event

    def blocked_record(**kwargs):
        worker_entered.set()
        assert worker_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", blocked_record)
    baseline = asyncio.all_tasks()
    owner = asyncio.create_task(coordinator.handle_registered_event(token))
    assert await asyncio.to_thread(worker_entered.wait, 0.1)

    await _cancel_owner_and_internal_tasks(owner, baseline)
    public_incomplete = not owner.done()
    device_lock_held = coordinator._device_lock("123").locked()  # pylint: disable=protected-access
    worker_release.set()

    with pytest.raises(asyncio.CancelledError):
        await owner
    adopted = await coordinator.flush_registered_events(
        session_id=active.session_id
    )

    assert public_incomplete is True
    assert device_lock_held is True
    assert len(adopted) == 1
    assert adopted[0].disposition is EventDisposition.CONFIRMED
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED
    assert [record.step for record in records].count(
        SettingStep.EVENT_CONFIRMED
    ) == 1
    assert coordinator.cached_status_snapshot.count(CommandState.CONFIRMED) == 1


@pytest.mark.asyncio
async def test_registered_event_worker_cancellation_is_exact_and_retryable(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = asyncio.CancelledError("event store worker cancelled")
    record_calls = 0
    original_record = store.record_event

    def cancel_first_record(**kwargs: Any) -> EventMatchResult:
        nonlocal record_calls
        record_calls += 1
        if record_calls == 1:
            raise sentinel
        return original_record(**kwargs)

    monkeypatch.setattr(store, "record_event", cancel_first_record)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "event-worker-session",
            "123",
            220,
            b"event-worker-cancelled",
        ),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.handle_registered_event(token)

    assert caught.value is sentinel
    assert record_calls == 1
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access

    retried = await coordinator.handle_registered_event(token)

    assert retried.disposition is EventDisposition.UNMATCHED
    assert record_calls == 2
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_cancelled_event_owner_and_flush_share_exact_worker_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = asyncio.CancelledError("shared event worker cancelled")
    worker_entered = threading.Event()
    worker_release = threading.Event()
    record_calls = 0
    original_record = store.record_event

    def cancel_first_record(**kwargs: Any) -> EventMatchResult:
        nonlocal record_calls
        record_calls += 1
        if record_calls == 1:
            worker_entered.set()
            assert worker_release.wait(timeout=1)
            raise sentinel
        return original_record(**kwargs)

    monkeypatch.setattr(store, "record_event", cancel_first_record)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            "shared-event-session",
            "123",
            220,
            b"shared-event-worker-cancelled",
        ),
    )
    owner = asyncio.create_task(coordinator.handle_registered_event(token))
    assert await asyncio.to_thread(worker_entered.wait, 0.1)

    owner.cancel("event owner cancelled")
    owner_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(owner_turn.set)
    await owner_turn.wait()
    flush = asyncio.create_task(
        coordinator.flush_registered_events(session_id="shared-event-session")
    )
    flush_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(flush_turn.set)
    await flush_turn.wait()
    worker_release.set()

    with pytest.raises(asyncio.CancelledError) as owner_caught:
        await owner
    with pytest.raises(asyncio.CancelledError) as flush_caught:
        await flush

    assert owner_caught.value.args == ("event owner cancelled",)
    assert owner_caught.value.__cause__ is sentinel
    assert flush_caught.value is sentinel
    assert record_calls == 1
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access

    retried = await coordinator.handle_registered_event(token)

    assert retried.disposition is EventDisposition.UNMATCHED
    assert record_calls == 2


@pytest.mark.parametrize(
    "failure_type",
    [asyncio.CancelledError, RuntimeError],
)
@pytest.mark.asyncio
async def test_drained_failed_task_is_collectable(
    coordinator: TwinCoordinator,
    failure_type: type[BaseException],
) -> None:
    failures = [failure_type("drained task failed")]

    async def fail() -> None:
        raise failures[0]

    task = asyncio.create_task(fail())
    result, error, owner_cancellation = await coordinator._drain_task(task)  # pylint: disable=protected-access

    assert result is None
    assert error is failures[0]
    assert owner_cancellation is None

    task_reference = weakref.ref(task)
    failures.clear()
    del error
    del task
    for _ in range(3):
        gc.collect()
        await asyncio.sleep(0)

    assert task_reference() is None


@pytest.mark.asyncio
async def test_pre_start_registered_worker_cancellation_restores_receipt_order(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _register_batch_event(
        coordinator,
        session_id="pre-start-cancel",
        device_id="123",
        event_id_set=151,
        received_at_ms=751,
    )
    calls: list[str] = []
    original = store.record_event

    def observed_record(**kwargs):
        calls.append(kwargs["evidence"].evidence_id)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_event", observed_record)
    owner = asyncio.create_task(coordinator.handle_registered_event(first))
    child_cancelled = asyncio.Event()

    def cancel_child_before_first_turn() -> None:
        entry = coordinator._registered_events[first.token_id]  # pylint: disable=protected-access
        assert entry.worker is not None
        entry.worker.cancel()
        child_cancelled.set()

    asyncio.get_running_loop().call_soon(cancel_child_before_first_turn)
    await child_cancelled.wait()
    with pytest.raises(asyncio.CancelledError):
        await owner
    second = _register_batch_event(
        coordinator,
        session_id="pre-start-cancel",
        device_id="123",
        event_id_set=152,
        received_at_ms=752,
    )

    delivered = await coordinator.flush_registered_events(
        session_id="pre-start-cancel"
    )

    assert [result.evidence.evidence_id for result in delivered] == [
        first.event.evidence_id,
        second.event.evidence_id,
    ]
    assert calls == [
        first.event.evidence_id,
        second.event.evidence_id,
    ]


@pytest.mark.parametrize(
    ("response_result", "store_method", "expected_state", "expected_step"),
    [
        (
            "ACK",
            "acknowledge_and_prepare_next",
            CommandState.AWAITING_EVENT,
            SettingStep.ACK_OBSERVED,
        ),
        (
            "NACK",
            "mark_nack",
            CommandState.FAILED,
            SettingStep.NACK,
        ),
    ],
)
@pytest.mark.asyncio
async def test_cancelled_response_mutation_reconciles_before_escape(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    response_result: Literal["ACK", "NACK"],
    store_method: str,
    expected_state: CommandState,
    expected_step: SettingStep,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    entered = threading.Event()
    release = threading.Event()
    original = getattr(store, store_method)

    def blocked_response(**kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, store_method, blocked_response)
    raw = f"cancel-{response_result.lower()}".encode()
    response_task = asyncio.create_task(
        coordinator.handle_local_response(
            active=active,
            response=_response(raw, result=response_result),
            context=_context(active, raw),
            writer=writer,
        )
    )
    assert await asyncio.to_thread(entered.wait, 0.1)
    response_task.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    waited_for_store = not response_task.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await response_task

    assert waited_for_store is True
    assert store.read_command(active.command_id).state is expected_state
    assert [record.step for record in records].count(expected_step) == 1
    assert dict(coordinator.cached_status_snapshot.state_counts)[
        expected_state
    ] == 1


@pytest.mark.asyncio
async def test_cancelled_retry_mutation_reconciles_before_escape(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    active = await _deliver(coordinator, ScriptedLocalSettingWriter(store))
    entered = threading.Event()
    release = threading.Event()
    original = store.release_for_retry

    def blocked_retry(**kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "release_for_retry", blocked_retry)
    retry_task = asyncio.create_task(
        coordinator.abort_dialogue(
            active=active,
            occurred_at_ms=active.ack_deadline_ms,
            reason=RetryReason.DISCONNECT,
        )
    )
    assert await asyncio.to_thread(entered.wait, 0.1)
    retry_task.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    waited_for_store = not retry_task.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await retry_task

    assert waited_for_store is True
    assert store.read_command(active.command_id).state is CommandState.RETRY_PENDING
    assert [record.step for record in records].count(SettingStep.RETRY) == 1
    assert dict(coordinator.cached_status_snapshot.state_counts)[
        CommandState.RETRY_PENDING
    ] == 1


@pytest.mark.asyncio
async def test_cancelled_write_state_mutation_reconciles_before_escape(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    entered = threading.Event()
    release = threading.Event()
    original = store.mark_write_started

    def blocked_write_started(**kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "mark_write_started", blocked_write_started)
    delivery = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="write-state-session",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(store),
        )
    )
    assert await asyncio.to_thread(entered.wait, 0.1)
    delivery.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    waited_for_store = not delivery.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await delivery

    attempt = store.read_attempt(command.command_id, 1)
    assert waited_for_store is True
    assert attempt.write_outcome is AttemptWriteOutcome.STARTED
    assert [record.step for record in records].count(
        SettingStep.WRITE_STARTED
    ) == 1
    assert coordinator.cached_status_snapshot.awaiting_ack == 1


@pytest.mark.asyncio
async def test_active_writer_serializes_ack_sweep_and_in_deadline_response(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer_entered = asyncio.Event()
    writer_release = asyncio.Event()
    writer = ScriptedLocalSettingWriter(
        store, entered=writer_entered, release=writer_release
    )
    delivery_task = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="session-a",
            received_at_ms=200,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=writer,
        )
    )
    await writer_entered.wait()
    active = writer.attempts[0]
    raw = b"ack-received-before-deadline"
    response_task = asyncio.create_task(
        coordinator.handle_local_response(
            active=active,
            response=_response(raw),
            context=_context(
                active,
                raw,
                received_at_ms=active.ack_deadline_ms,
            ),
            writer=writer,
        )
    )
    await asyncio.sleep(0)
    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=active.ack_deadline_ms + 1)
    )

    sweep_completed_while_writer_owned = True
    try:
        await asyncio.wait_for(asyncio.shield(sweep_task), timeout=0.05)
    except TimeoutError:
        sweep_completed_while_writer_owned = False
    finally:
        writer_release.set()

    delivery, response, sweep = await asyncio.gather(
        delivery_task, response_task, sweep_task
    )
    assert sweep_completed_while_writer_owned is False
    assert delivery.disposition is DeliveryDisposition.SENT
    assert response.disposition is LocalResponseDisposition.ACK_ACCEPTED
    assert sweep.retry_pending == 0
    assert sweep.failed_attempt_limit == 0
    assert store.read_command(active.command_id).state is CommandState.AWAITING_EVENT


@pytest.mark.asyncio
async def test_sweeper_holds_device_lock_before_expiring_pending_delivery(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep_entered = threading.Event()
    sweep_release = threading.Event()
    original = getattr(store, "sweep_device_deadlines", None)

    def blocked_sweep(*, device_id: str, now_ms: int):
        sweep_entered.set()
        assert sweep_release.wait(timeout=1)
        assert original is not None
        return original(device_id=device_id, now_ms=now_ms)

    monkeypatch.setattr(
        store, "sweep_device_deadlines", blocked_sweep, raising=False
    )
    sweep_task = asyncio.create_task(coordinator.sweep_deadlines(now_ms=900_101))
    assert await asyncio.to_thread(sweep_entered.wait, 0.1)

    writer_entered = asyncio.Event()
    writer = ScriptedLocalSettingWriter(store, entered=writer_entered)
    delivery_task = asyncio.create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="session-a",
            received_at_ms=900_102,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=writer,
        )
    )
    delivery_started_while_sweep_owned = True
    try:
        await asyncio.wait_for(writer_entered.wait(), timeout=0.05)
    except TimeoutError:
        delivery_started_while_sweep_owned = False
    finally:
        sweep_release.set()

    sweep, delivery = await asyncio.gather(sweep_task, delivery_task)
    assert delivery_started_while_sweep_owned is False
    assert sweep.expired_pending == 1
    assert delivery.disposition is DeliveryDisposition.NO_ELIGIBLE
    assert writer.frames == []


@pytest.mark.asyncio
async def test_deadline_sweep_progresses_distinct_devices_in_parallel(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    _enqueue(store, device_id="456")
    coordinator = TwinCoordinator(
        store, renderer=deterministic_renderer, clock_ms=_Clock()
    )
    first_entered = threading.Event()
    second_entered = threading.Event()
    first_release = threading.Event()
    original = getattr(store, "sweep_device_deadlines", None)

    def blocked_first(*, device_id: str, now_ms: int):
        if device_id == "123":
            first_entered.set()
            assert first_release.wait(timeout=1)
        else:
            second_entered.set()
        assert original is not None
        return original(device_id=device_id, now_ms=now_ms)

    monkeypatch.setattr(
        store, "sweep_device_deadlines", blocked_first, raising=False
    )
    sweep_task = asyncio.create_task(coordinator.sweep_deadlines(now_ms=900_101))
    assert await asyncio.to_thread(first_entered.wait, 0.1)
    second_progressed = await asyncio.to_thread(second_entered.wait, 0.1)
    first_release.set()

    sweep = await sweep_task
    assert second_progressed is True
    assert sweep.expired_pending == 2


@pytest.mark.asyncio
# pylint: disable-next=too-many-statements
async def test_deadline_sweep_retains_device_locks_through_reconciliation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = (_enqueue(store), _enqueue(store, device_id="456"))
    awaiting_commands: list[TwinCommand] = []
    for index, command in enumerate(commands):
        prepared_at_ms = 200 + index * 10
        session_id = f"initial-session-{command.device_id}"
        claim = store.prepare_next_attempt(
            device_id=command.device_id,
            session_id=session_id,
            prepared_at_ms=prepared_at_ms,
            render=deterministic_renderer,
        )
        assert claim.command is not None
        assert claim.attempt is not None
        store.mark_write_started(
            command_id=claim.command.command_id,
            attempt_number=claim.attempt.attempt_number,
            session_id=session_id,
            started_at_ms=prepared_at_ms + 1,
        )
        store.mark_attempt_drained(
            command_id=claim.command.command_id,
            attempt_number=claim.attempt.attempt_number,
            session_id=session_id,
            drained_at_ms=prepared_at_ms + 2,
        )
        awaiting = store.read_command(command.command_id)
        assert awaiting.state is CommandState.AWAITING_ACK
        assert awaiting.ack_deadline_ms is not None
        awaiting_commands.append(awaiting)

    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )

    class AcquisitionTracingLock(asyncio.Lock):
        """Record exact lock acquisition attempts and completions."""

        def __init__(self) -> None:
            super().__init__()
            self.acquisition_attempts = 0
            self.acquisitions = 0
            self.second_attempted = asyncio.Event()

        async def acquire(self) -> Literal[True]:
            self.acquisition_attempts += 1
            if self.acquisition_attempts == 2:
                self.second_attempted.set()
            acquired = await super().acquire()
            self.acquisitions += 1
            return acquired

    traced_lock = AcquisitionTracingLock()
    coordinator._device_locks["123"] = traced_lock  # pylint: disable=protected-access

    second_entered = threading.Event()
    second_release = threading.Event()
    original_sweep = store.sweep_device_deadlines

    def block_second_device(*, device_id: str, now_ms: int) -> SweepReport:
        if device_id == "456":
            second_entered.set()
            assert second_release.wait(timeout=1)
        return original_sweep(device_id=device_id, now_ms=now_ms)

    monkeypatch.setattr(store, "sweep_device_deadlines", block_second_device)

    base_refresh_completed = asyncio.Event()
    original_refresh = coordinator._refresh_status  # pylint: disable=protected-access
    sweep_owner: asyncio.Task[SweepReport] | None = None

    async def observe_base_refresh() -> tuple[
        BaseException | None,
        asyncio.CancelledError | None,
    ]:
        outcome = await original_refresh()
        if asyncio.current_task() is sweep_owner:
            base_refresh_completed.set()
        return outcome

    monkeypatch.setattr(coordinator, "_refresh_status", observe_base_refresh)

    original_create_task = asyncio.create_task
    device_workers: list[asyncio.Task[Any]] = []

    def capture_device_worker(coroutine: Any, **kwargs: Any) -> asyncio.Task[Any]:
        task = original_create_task(coroutine, **kwargs)
        device_workers.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", capture_device_worker)
    effective_now = max(
        cast(int, command.ack_deadline_ms) for command in awaiting_commands
    ) + 1
    sweep_owner = original_create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    assert await asyncio.to_thread(second_entered.wait, 0.1)
    assert len(device_workers) == 2
    monkeypatch.setattr(asyncio, "create_task", original_create_task)

    first_worker_completed = asyncio.Event()
    device_workers[0].add_done_callback(
        lambda _task: first_worker_completed.set()
    )
    await first_worker_completed.wait()
    assert traced_lock.acquisitions == 1

    retry_writer_entered = asyncio.Event()
    retry_writer_release = asyncio.Event()
    retry_task = original_create_task(
        coordinator.claim_and_write_next(
            device_id="123",
            session_id="retry-session-123",
            received_at_ms=effective_now + 1,
            trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
            writer=ScriptedLocalSettingWriter(
                store,
                entered=retry_writer_entered,
                release=retry_writer_release,
            ),
        )
    )
    await traced_lock.second_attempted.wait()
    retry_acquired_before_release = traced_lock.acquisitions == 2
    if retry_acquired_before_release:
        await retry_writer_entered.wait()
    records_before_release = tuple(records)
    base_refresh_preceded_retry = (
        base_refresh_completed.is_set()
        if retry_acquired_before_release
        else None
    )

    second_release.set()
    await retry_writer_entered.wait()
    if base_refresh_preceded_retry is None:
        base_refresh_preceded_retry = base_refresh_completed.is_set()
    retry_writer_release.set()
    sweep, retry = await asyncio.gather(sweep_owner, retry_task)

    transition_ids = [record.transition_id for record in records]
    assert retry_acquired_before_release is False
    assert records_before_release == ()
    assert base_refresh_preceded_retry is True
    assert sweep.retry_pending == 2
    assert retry.disposition is DeliveryDisposition.SENT
    assert [record.step for record in records] == [
        SettingStep.RETRY,
        SettingStep.RETRY,
        SettingStep.SELECTED,
        SettingStep.ATTEMPT_PREPARED,
        SettingStep.WRITE_STARTED,
        SettingStep.ATTEMPT_DRAINED,
    ]
    assert all(transition_id is not None for transition_id in transition_ids)
    assert transition_ids == sorted(
        cast(int, transition_id) for transition_id in transition_ids
    )
    assert coordinator.cached_status_snapshot.awaiting_ack == 1
    assert coordinator.cached_status_snapshot.retry_pending == 1


@pytest.mark.asyncio
async def test_deadline_sweep_reconciles_commit_after_task_creation_failure(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    loop = asyncio.get_running_loop()
    original_create_task = asyncio.create_task
    created_tasks: list[asyncio.Task[Any]] = []
    create_calls = 0

    def fail_second_task_creation(
        coroutine: Any, **kwargs: Any
    ) -> asyncio.Task[Any]:
        nonlocal create_calls
        create_calls += 1
        if create_calls == 1:
            coroutine.close()
            report = store.sweep_device_deadlines(
                device_id=first.device_id,
                now_ms=effective_now,
            )
            completed = loop.create_future()
            completed.set_result((report, None))
            task = cast(asyncio.Task[Any], completed)
            created_tasks.append(task)
            return task
        if create_calls == 2:
            coroutine.close()
            raise RuntimeError("injected second task creation failure")
        task = original_create_task(coroutine, **kwargs)
        created_tasks.append(task)
        return task

    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    monkeypatch.setattr(asyncio, "create_task", fail_second_task_creation)
    try:
        with pytest.raises(
            RuntimeError,
            match="injected second task creation failure",
        ):
            await coordinator.sweep_deadlines(now_ms=effective_now)
    finally:
        monkeypatch.setattr(asyncio, "create_task", original_create_task)

    assert len(created_tasks) == 1
    assert all(task.done() for task in created_tasks)
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.PENDING
    assert [(record.device_id, record.step) for record in records] == [
        (first.device_id, SettingStep.EXPIRED)
    ]
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 1
    assert coordinator.cached_status_snapshot.count(CommandState.PENDING) == 1
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.asyncio
async def test_deadline_creation_error_retains_prior_and_device_failure(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    device_error = RuntimeError("first device sweep failure")
    prior_error = RuntimeError("prior creation failure cause")
    creation_error = RuntimeError("second task creation failure")
    loop = asyncio.get_running_loop()
    create_calls = 0

    def fail_workers_and_creation(
        coroutine: Any, **_kwargs: Any
    ) -> asyncio.Task[Any]:
        nonlocal create_calls
        create_calls += 1
        coroutine.close()
        if create_calls == 1:
            completed = loop.create_future()
            completed.set_exception(device_error)
            return cast(asyncio.Task[Any], completed)
        raise creation_error from prior_error

    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    monkeypatch.setattr(asyncio, "create_task", fail_workers_and_creation)
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1

    with pytest.raises(RuntimeError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert caught.value is creation_error
    assert creation_error.__cause__ is prior_error
    aggregate_cause = prior_error.__cause__
    assert isinstance(aggregate_cause, DeadlineSweepError)
    aggregate_failures = getattr(aggregate_cause, "failures")
    aggregate_report = getattr(aggregate_cause, "partial_report")
    assert aggregate_failures == ((first.device_id, device_error),)
    assert aggregate_report.snapshots == ()
    assert create_calls == 2
    assert store.read_command(first.command_id).state is CommandState.PENDING
    assert store.read_command(second.command_id).state is CommandState.PENDING


@pytest.mark.asyncio
async def test_deadline_sweep_cleanup_cancellation_dominates_creation_failure(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    loop = asyncio.get_running_loop()
    original_create_task = asyncio.create_task
    created_tasks: list[asyncio.Task[Any]] = []
    create_calls = 0

    def cancel_during_failed_creation(
        coroutine: Any, **kwargs: Any
    ) -> asyncio.Task[Any]:
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            coroutine.close()
            owner = asyncio.current_task()
            assert owner is not None
            loop.call_soon(owner.cancel)
            raise RuntimeError("injected second task creation failure")
        task = original_create_task(coroutine, **kwargs)
        created_tasks.append(task)
        return task

    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    monkeypatch.setattr(asyncio, "create_task", cancel_during_failed_creation)
    try:
        with pytest.raises(asyncio.CancelledError):
            await coordinator.sweep_deadlines(now_ms=effective_now)
    finally:
        monkeypatch.setattr(asyncio, "create_task", original_create_task)

    assert len(created_tasks) == 1
    assert all(task.done() for task in created_tasks)
    expired = tuple(
        command
        for command in (
            store.read_command(first.command_id),
            store.read_command(second.command_id),
        )
        if command.state is CommandState.EXPIRED
    )
    assert [(record.device_id, record.step) for record in records] == [
        (command.device_id, SettingStep.EXPIRED) for command in expired
    ]
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == len(
        expired
    )
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.asyncio
async def test_deadline_owner_dominates_device_task_creation_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    worker_error = asyncio.CancelledError("device task creation cancelled")
    sweep_entered = threading.Event()
    sweep_release = threading.Event()
    original_sweep = store.sweep_device_deadlines

    def blocked_first_sweep(*, device_id: str, now_ms: int) -> SweepReport:
        if device_id == first.device_id:
            sweep_entered.set()
            assert sweep_release.wait(timeout=1)
        return original_sweep(device_id=device_id, now_ms=now_ms)

    monkeypatch.setattr(store, "sweep_device_deadlines", blocked_first_sweep)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    original_create_task = asyncio.create_task
    created_tasks: list[asyncio.Task[Any]] = []
    create_calls = 0

    def cancel_second_task_creation(
        coroutine: Any, **kwargs: Any
    ) -> asyncio.Task[Any]:
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            coroutine.close()
            raise worker_error
        task = original_create_task(coroutine, **kwargs)
        created_tasks.append(task)
        return task

    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    monkeypatch.setattr(asyncio, "create_task", cancel_second_task_creation)
    owner = original_create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    assert await asyncio.to_thread(sweep_entered.wait, 0.1)
    assert create_calls == 2
    monkeypatch.setattr(asyncio, "create_task", original_create_task)
    owner.cancel("deadline owner cancelled")
    while not outer_errors:
        await asyncio.sleep(0)
    sweep_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await owner

    assert caught.value is outer_errors[0]
    assert caught.value.args == ("deadline owner cancelled",)
    assert caught.value.__cause__ is worker_error
    assert len(created_tasks) == 1
    assert created_tasks[0].done()
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.PENDING
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.asyncio
async def test_device_task_creation_worker_dominates_later_status_worker(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    creation_error = asyncio.CancelledError("device task creation cancelled")
    status_error = asyncio.CancelledError("aggregate status cancelled")

    def cancelled_status() -> StoreStatus:
        raise status_error

    monkeypatch.setattr(store, "status_snapshot", cancelled_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )

    def cancel_task_creation(
        coroutine: Any, **_kwargs: Any
    ) -> asyncio.Task[Any]:
        coroutine.close()
        raise creation_error

    monkeypatch.setattr(asyncio, "create_task", cancel_task_creation)

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1
        )

    assert caught.value is creation_error
    assert creation_error.__cause__ is status_error
    assert store.read_command(command.command_id).state is CommandState.PENDING
    assert coordinator._device_lock("123").locked() is False  # pylint: disable=protected-access


@pytest.mark.parametrize("cancel_owner", [False, True], ids=["worker", "owner"])
@pytest.mark.asyncio
# pylint: disable-next=too-many-statements
async def test_deadline_retains_creation_publication_status_control_flow_chain(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    cancel_owner: bool,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    creation_error = asyncio.CancelledError("device task creation cancelled")
    publication_error = asyncio.CancelledError("aggregate publisher cancelled")
    status_error = asyncio.CancelledError("aggregate status cancelled")
    status_entered = threading.Event()
    status_release = threading.Event()
    status_calls = 0
    original_status = store.status_snapshot

    class CancelledPublisher:
        """Return the middle control-flow object in the aggregate chain."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise publication_error

    def cancel_aggregate_status(*args: Any, **kwargs: Any) -> StoreStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 2:
            status_entered.set()
            if cancel_owner:
                assert status_release.wait(timeout=1)
            raise status_error
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "status_snapshot", cancel_aggregate_status)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )
    outer_errors: list[asyncio.CancelledError] = []
    monkeypatch.setattr(
        asyncio,
        "shield",
        _capture_pending_owner_cancellation(asyncio.shield, outer_errors),
    )
    original_create_task = asyncio.create_task
    created_tasks: list[asyncio.Task[Any]] = []
    create_calls = 0

    def cancel_second_task_creation(
        coroutine: Any, **kwargs: Any
    ) -> asyncio.Task[Any]:
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            coroutine.close()
            raise creation_error
        task = original_create_task(coroutine, **kwargs)
        created_tasks.append(task)
        return task

    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    monkeypatch.setattr(asyncio, "create_task", cancel_second_task_creation)
    if cancel_owner:
        owner = original_create_task(
            coordinator.sweep_deadlines(now_ms=effective_now)
        )
        assert await asyncio.to_thread(status_entered.wait, 0.1)
        owner.cancel("deadline owner cancelled")
        while not outer_errors:
            await asyncio.sleep(0)
        status_release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await owner
        assert caught.value is outer_errors[0]
        assert caught.value.__cause__ is creation_error
    else:
        with pytest.raises(asyncio.CancelledError) as caught:
            await coordinator.sweep_deadlines(now_ms=effective_now)
        assert caught.value is creation_error

    assert creation_error.__cause__ is publication_error
    assert publication_error.__cause__ is status_error
    assert len(created_tasks) == 1
    assert created_tasks[0].done()
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.PENDING
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.asyncio
async def test_deadline_retains_multiple_device_worker_cancellations(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    worker_errors = (
        asyncio.CancelledError("first device worker cancelled"),
        asyncio.CancelledError("second device worker cancelled"),
    )
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )
    loop = asyncio.get_running_loop()
    created_tasks: list[asyncio.Task[Any]] = []
    create_calls = 0

    def completed_device_worker(
        coroutine: Any, **_kwargs: Any
    ) -> asyncio.Task[Any]:
        nonlocal create_calls
        coroutine.close()
        device_id = (first.device_id, second.device_id)[create_calls]
        report = store.sweep_device_deadlines(
            device_id=device_id,
            now_ms=effective_now,
        )
        completed = loop.create_future()
        completed.set_result((report, worker_errors[create_calls]))
        task = cast(asyncio.Task[Any], completed)
        created_tasks.append(task)
        create_calls += 1
        return task

    monkeypatch.setattr(asyncio, "create_task", completed_device_worker)

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert caught.value is worker_errors[0]
    assert worker_errors[0].__cause__ is worker_errors[1]
    assert create_calls == 2
    assert all(task.done() for task in created_tasks)
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.EXPIRED
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2


@pytest.mark.asyncio
async def test_deadline_sweep_does_not_release_foreign_lock_on_worker_cancel(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = _enqueue(store)
    sibling = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    foreign_lock = coordinator._device_lock(  # pylint: disable=protected-access
        blocked.device_id
    )
    await foreign_lock.acquire()
    sibling_finished = threading.Event()
    original_sweep = store.sweep_device_deadlines

    def observe_sibling(*, device_id: str, now_ms: int) -> SweepReport:
        report = original_sweep(device_id=device_id, now_ms=now_ms)
        if device_id == sibling.device_id:
            sibling_finished.set()
        return report

    monkeypatch.setattr(store, "sweep_device_deadlines", observe_sibling)
    original_create_task = asyncio.create_task
    device_workers: list[asyncio.Task[Any]] = []

    def capture_worker(coroutine: Any, **kwargs: Any) -> asyncio.Task[Any]:
        task = original_create_task(coroutine, **kwargs)
        device_workers.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", capture_worker)
    effective_now = max(
        blocked.pending_expires_at_ms,
        sibling.pending_expires_at_ms,
    ) + 1
    sweep_task = original_create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    try:
        assert await asyncio.to_thread(sibling_finished.wait, 0.2)
        assert len(device_workers) == 2
        monkeypatch.setattr(asyncio, "create_task", original_create_task)
        device_workers[0].cancel()

        with pytest.raises(asyncio.CancelledError):
            await sweep_task

        assert foreign_lock.locked() is True
        assert all(task.done() for task in device_workers)
        assert store.read_command(blocked.command_id).state is CommandState.PENDING
        assert store.read_command(sibling.command_id).state is CommandState.EXPIRED
        assert [(record.device_id, record.step) for record in records] == [
            (sibling.device_id, SettingStep.EXPIRED)
        ]
    finally:
        if foreign_lock.locked():
            foreign_lock.release()
        if not sweep_task.done():
            sweep_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_deadline_sweeps_serialize_base_reconciliation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    original_read = store.read_deadline_devices
    read_calls = 0
    second_read = threading.Event()

    def observe_enumeration(*, now_ms: int) -> tuple[str, ...]:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 2:
            second_read.set()
        return original_read(now_ms=now_ms)

    monkeypatch.setattr(store, "read_deadline_devices", observe_enumeration)
    original_publish = coordinator._publish_completion  # pylint: disable=protected-access
    publish_entered = asyncio.Event()
    publish_release = asyncio.Event()

    async def block_first_aggregate(
        snapshots: tuple[TransitionAuditSnapshot, ...],
    ) -> Any:
        if snapshots and not publish_entered.is_set():
            publish_entered.set()
            await publish_release.wait()
        return await original_publish(snapshots)

    monkeypatch.setattr(
        coordinator,
        "_publish_completion",
        block_first_aggregate,
    )
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    first_sweep = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    await asyncio.wait_for(publish_entered.wait(), timeout=0.2)
    second_sweep = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    try:
        second_enumerated_while_first_owned = await asyncio.to_thread(
            second_read.wait, 0.05
        )
        assert second_enumerated_while_first_owned is False
    finally:
        publish_release.set()

    first_report, second_report = await asyncio.gather(
        first_sweep,
        second_sweep,
    )
    assert first_report.expired_pending == 2
    assert second_report.expired_pending == 0
    assert read_calls == 2
    assert [record.step for record in records] == [
        SettingStep.EXPIRED,
        SettingStep.EXPIRED,
    ]
    assert [record.transition_id for record in records] == sorted(
        cast(int, record.transition_id) for record in records
    )
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.asyncio
async def test_publication_error_does_not_override_latched_cancellation(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    sentinel = RuntimeError("injected second publication failure")

    class CancelThenFailPublisher:
        """Latch owner cancellation before one later publication error."""

        def __init__(self) -> None:
            self.calls = 0

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            self.calls += 1
            if self.calls == 1:
                owner = asyncio.current_task()
                assert owner is not None
                owner.cancel()
                await asyncio.sleep(0)
            raise sentinel

    publisher = CancelThenFailPublisher()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=publisher,
        clock_ms=_Clock(),
    )
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await sweep_task

    assert caught.value.__cause__ is sentinel
    assert publisher.calls == 2
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.EXPIRED
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.parametrize(
    "deadline_devices",
    [
        pytest.param(["123"], id="list"),
        pytest.param(("",), id="empty-device"),
        pytest.param(("456", "123"), id="unsorted"),
        pytest.param(("123", "123"), id="duplicate"),
        pytest.param((123,), id="non-string"),
    ],
)
@pytest.mark.asyncio
async def test_deadline_sweep_rejects_malformed_device_enumeration(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    deadline_devices: Any,
) -> None:
    mutation_called = threading.Event()
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
    )

    def malformed_enumeration(*, now_ms: int) -> Any:
        del now_ms
        return deadline_devices

    def unexpected_mutation(*, device_id: str, now_ms: int) -> SweepReport:
        del device_id, now_ms
        mutation_called.set()
        raise AssertionError("malformed enumeration reached mutation")

    monkeypatch.setattr(store, "read_deadline_devices", malformed_enumeration)
    monkeypatch.setattr(store, "sweep_device_deadlines", unexpected_mutation)

    with pytest.raises(RuntimeError, match="deadline devices must be"):
        await coordinator.sweep_deadlines(now_ms=900_101)

    assert mutation_called.is_set() is False
    assert coordinator._device_locks == {}  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_deadline_sweep_reconciles_success_before_sibling_failure(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    first_entered = threading.Event()
    first_release = threading.Event()
    first_finished = threading.Event()
    second_failed = threading.Event()
    original = store.sweep_device_deadlines

    def controlled_sweep(*, device_id: str, now_ms: int):
        if device_id == "456":
            assert first_entered.wait(timeout=1)
            second_failed.set()
            raise RuntimeError("device B sweep failure")
        first_entered.set()
        assert first_release.wait(timeout=1)
        try:
            return original(device_id=device_id, now_ms=now_ms)
        finally:
            first_finished.set()

    monkeypatch.setattr(store, "sweep_device_deadlines", controlled_sweep)
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    assert await asyncio.to_thread(second_failed.wait, 0.1)
    failure_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(failure_turn.set)
    await failure_turn.wait()
    waited_for_successful_sibling = not sweep_task.done()
    first_release.set()

    with pytest.raises(RuntimeError) as caught:
        await sweep_task
    assert await asyncio.to_thread(first_finished.wait, 0.1)
    partial_report = getattr(caught.value, "partial_report", None)

    assert waited_for_successful_sibling is True
    assert partial_report is not None
    assert partial_report.expired_pending == 1
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.PENDING
    assert [record.step for record in records] == [SettingStep.EXPIRED]
    assert dict(coordinator.cached_status_snapshot.state_counts)[
        CommandState.EXPIRED
    ] == 1
    assert dict(coordinator.cached_status_snapshot.state_counts)[
        CommandState.PENDING
    ] == 1


@pytest.mark.asyncio
async def test_deadline_publication_worker_retains_all_device_failures(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    successful = _enqueue(store)
    failed_first = _enqueue(store, device_id="456")
    failed_second = _enqueue(store, device_id="789")
    device_errors = (
        RuntimeError("first device sweep failure"),
        RuntimeError("second device sweep failure"),
    )
    publication_error = asyncio.CancelledError(
        "aggregate publisher worker cancelled"
    )
    original_sweep = store.sweep_device_deadlines

    def controlled_sweep(*, device_id: str, now_ms: int) -> SweepReport:
        if device_id == failed_first.device_id:
            raise device_errors[0]
        if device_id == failed_second.device_id:
            raise device_errors[1]
        return original_sweep(device_id=device_id, now_ms=now_ms)

    class CancelledPublisher:
        """Return exact publication control flow after sibling settlement."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise publication_error

    monkeypatch.setattr(store, "sweep_device_deadlines", controlled_sweep)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=CancelledPublisher(),
        clock_ms=_Clock(),
    )
    effective_now = max(
        successful.pending_expires_at_ms,
        failed_first.pending_expires_at_ms,
        failed_second.pending_expires_at_ms,
    ) + 1

    with pytest.raises(asyncio.CancelledError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert caught.value is publication_error
    aggregate_error = caught.value.__cause__
    assert isinstance(aggregate_error, DeadlineSweepError)
    assert aggregate_error.failures == (
        (failed_first.device_id, device_errors[0]),
        (failed_second.device_id, device_errors[1]),
    )
    assert aggregate_error.partial_report.expired_pending == 1
    assert len(aggregate_error.partial_report.snapshots) == 1
    assert aggregate_error.partial_report.snapshots[0].command.command_id == (
        successful.command_id
    )
    assert store.read_command(successful.command_id).state is CommandState.EXPIRED
    assert store.read_command(failed_first.command_id).state is CommandState.PENDING
    assert store.read_command(failed_second.command_id).state is CommandState.PENDING


@pytest.mark.asyncio
async def test_deadline_publication_error_retains_device_failure_report(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    successful = _enqueue(store)
    failed = _enqueue(store, device_id="456")
    device_error = RuntimeError("device sweep failure")
    publication_error = RuntimeError("aggregate publication failure")
    original_sweep = store.sweep_device_deadlines

    def controlled_sweep(*, device_id: str, now_ms: int) -> SweepReport:
        if device_id == failed.device_id:
            raise device_error
        return original_sweep(device_id=device_id, now_ms=now_ms)

    class FailedPublisher:
        """Return one ordinary aggregate publication failure."""

        async def publish_committed_async(
            self, _snapshot: TransitionAuditSnapshot
        ) -> None:
            raise publication_error

    monkeypatch.setattr(store, "sweep_device_deadlines", controlled_sweep)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=FailedPublisher(),
        clock_ms=_Clock(),
    )
    effective_now = max(
        successful.pending_expires_at_ms,
        failed.pending_expires_at_ms,
    ) + 1

    with pytest.raises(RuntimeError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert caught.value is publication_error
    aggregate_error = caught.value.__cause__
    assert isinstance(aggregate_error, DeadlineSweepError)
    assert aggregate_error.failures == ((failed.device_id, device_error),)
    assert aggregate_error.partial_report.expired_pending == 1
    assert len(aggregate_error.partial_report.snapshots) == 1
    assert aggregate_error.partial_report.snapshots[0].command.command_id == (
        successful.command_id
    )
    assert store.read_command(successful.command_id).state is CommandState.EXPIRED
    assert store.read_command(failed.command_id).state is CommandState.PENDING


@pytest.mark.asyncio
async def test_malformed_device_sweep_report_reconciles_successful_sibling(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = _enqueue(store)
    successful = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    original = store.sweep_device_deadlines

    def malformed_first(*, device_id: str, now_ms: int):
        if device_id == malformed.device_id:
            snapshots = cast(
                tuple[TransitionAuditSnapshot, ...],
                (object(),),
            )
            return SweepReport(1, 0, 0, 0, snapshots)
        return original(device_id=device_id, now_ms=now_ms)

    monkeypatch.setattr(store, "sweep_device_deadlines", malformed_first)
    effective_now = max(
        malformed.pending_expires_at_ms,
        successful.pending_expires_at_ms,
    ) + 1

    with pytest.raises(DeadlineSweepError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert [device_id for device_id, _error in caught.value.failures] == ["123"]
    assert isinstance(caught.value.failures[0][1], RuntimeError)
    partial = caught.value.partial_report
    assert partial.expired_pending == 1
    assert partial.retry_pending == 0
    assert partial.failed_attempt_limit == 0
    assert partial.incomplete_event_timeout == 0
    assert len(partial.snapshots) == 1
    assert partial.snapshots[0].command.device_id == successful.device_id
    assert store.read_command(malformed.command_id).state is CommandState.PENDING
    assert store.read_command(successful.command_id).state is CommandState.EXPIRED
    assert [record.step for record in records] == [SettingStep.EXPIRED]
    assert coordinator.cached_status_snapshot.count(CommandState.PENDING) == 1
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 1


@pytest.mark.parametrize(
    "container_kind",
    ["changing-tuple", "report-subclass"],
)
@pytest.mark.asyncio
async def test_mutable_report_container_reconciles_only_valid_sibling(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    container_kind: str,
) -> None:
    malformed = _enqueue(store)
    successful = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    original_sweep = store.sweep_device_deadlines
    iterations = 0

    class ChangingSnapshotTuple(tuple):
        """Change iteration contents after the validation passes."""

        def __iter__(self):
            nonlocal iterations
            iterations += 1
            if iterations <= 3:
                return super().__iter__()
            return iter((object(),))

    class SweepReportSubclass(SweepReport):
        """Exercise rejection of a structurally valid report subclass."""

    def mutable_report(*, device_id: str, now_ms: int) -> SweepReport:
        report = original_sweep(device_id=device_id, now_ms=now_ms)
        if device_id != malformed.device_id:
            return report
        if container_kind == "changing-tuple":
            snapshots = ChangingSnapshotTuple(report.snapshots)
            return SweepReport(
                report.expired_pending,
                report.retry_pending,
                report.failed_attempt_limit,
                report.incomplete_event_timeout,
                snapshots,
            )
        return SweepReportSubclass(
            report.expired_pending,
            report.retry_pending,
            report.failed_attempt_limit,
            report.incomplete_event_timeout,
            report.snapshots,
        )

    monkeypatch.setattr(store, "sweep_device_deadlines", mutable_report)
    effective_now = max(
        malformed.pending_expires_at_ms,
        successful.pending_expires_at_ms,
    ) + 1

    with pytest.raises(DeadlineSweepError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert [device_id for device_id, _error in caught.value.failures] == [
        malformed.device_id
    ]
    assert caught.value.partial_report.expired_pending == 1
    assert len(caught.value.partial_report.snapshots) == 1
    assert caught.value.partial_report.snapshots[0].command.device_id == (
        successful.device_id
    )
    assert store.read_command(malformed.command_id).state is CommandState.EXPIRED
    assert store.read_command(successful.command_id).state is CommandState.EXPIRED
    assert [(record.device_id, record.step) for record in records] == [
        (successful.device_id, SettingStep.EXPIRED)
    ]
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2


@pytest.mark.asyncio
async def test_deadline_sweep_rejects_mutable_worker_outcome_tuple(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )

    class MutableOutcomeTuple(tuple):
        """Expose valid indexed values but substitute during unpacking."""

        def __iter__(self):
            return iter((self[0], "forged-cancellation"))

    original_create_task = asyncio.create_task

    def wrap_worker(coroutine: Any, **kwargs: Any) -> asyncio.Task[Any]:
        async def mutable_outcome() -> MutableOutcomeTuple:
            outcome = await coroutine
            return MutableOutcomeTuple(outcome)

        return original_create_task(mutable_outcome(), **kwargs)

    monkeypatch.setattr(asyncio, "create_task", wrap_worker)
    sweep_task = original_create_task(
        coordinator.sweep_deadlines(
            now_ms=command.pending_expires_at_ms + 1,
        )
    )

    with pytest.raises(DeadlineSweepError) as caught:
        await sweep_task

    assert [device_id for device_id, _error in caught.value.failures] == [
        command.device_id
    ]
    assert caught.value.partial_report.snapshots == ()
    assert store.read_command(command.command_id).state is CommandState.EXPIRED
    assert records == []
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 1
    assert all(
        not lock.locked()
        for lock in coordinator._device_locks.values()  # pylint: disable=protected-access
    )


@pytest.mark.parametrize(
    "malformation",
    [
        "counter-category",
        "command-type",
        "transition-type",
        "command-link",
        "audit-link",
        "state-target-link",
        "snapshot-type",
        "attempt-type",
        "evidence-type",
        "unsupported-target",
    ],
)
@pytest.mark.asyncio
async def test_semantically_forged_sweep_report_reconciles_only_valid_sibling(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    forged_command = _enqueue(store)
    successful = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    original = store.sweep_device_deadlines
    forged_report_returned = threading.Event()

    def semantically_forged(*, device_id: str, now_ms: int):
        report = original(device_id=device_id, now_ms=now_ms)
        if device_id != forged_command.device_id:
            return report
        snapshot = report.snapshots[0]
        counters = (1, 0, 0)
        forged_snapshot: Any = snapshot
        if malformation == "counter-category":
            counters = (0, 1, 0)
        elif malformation == "command-type":
            forged_snapshot = replace(
                snapshot,
                command=cast(
                    TwinCommand,
                    SimpleNamespace(
                        command_id=snapshot.command.command_id,
                        audit_id=snapshot.command.audit_id,
                        device_id=snapshot.command.device_id,
                        state=snapshot.command.state,
                    ),
                ),
            )
        elif malformation == "transition-type":
            forged_snapshot = replace(
                snapshot,
                transition=cast(
                    CommandTransition,
                    SimpleNamespace(
                        transition_id=snapshot.transition.transition_id,
                        command_id=snapshot.transition.command_id,
                        audit_id=snapshot.transition.audit_id,
                        to_state=snapshot.transition.to_state,
                    ),
                ),
            )
        elif malformation == "command-link":
            forged_snapshot = replace(
                snapshot,
                transition=replace(
                    snapshot.transition,
                    command_id="forged-command-link",
                ),
            )
        elif malformation == "audit-link":
            forged_snapshot = replace(
                snapshot,
                transition=replace(
                    snapshot.transition,
                    audit_id="forged-audit-link",
                ),
            )
        elif malformation == "state-target-link":
            forged_snapshot = replace(
                snapshot,
                command=replace(
                    snapshot.command,
                    state=CommandState.RETRY_PENDING,
                ),
            )
        elif malformation == "snapshot-type":
            forged_snapshot = _ForgedTransitionAuditSnapshot(
                snapshot.command,
                snapshot.transition,
                snapshot.attempt,
                snapshot.evidence,
            )
        elif malformation == "attempt-type":
            forged_snapshot = replace(
                snapshot,
                attempt=cast(Any, object()),
            )
        elif malformation == "evidence-type":
            forged_snapshot = replace(
                snapshot,
                evidence=cast(Any, object()),
            )
        else:
            forged_snapshot = replace(
                snapshot,
                command=replace(
                    snapshot.command,
                    state=CommandState.CONFIRMED,
                ),
                transition=replace(
                    snapshot.transition,
                    to_state=CommandState.CONFIRMED,
                ),
            )
        forged_report_returned.set()
        return SweepReport(*counters, 0, (forged_snapshot,))

    monkeypatch.setattr(store, "sweep_device_deadlines", semantically_forged)
    effective_now = max(
        forged_command.pending_expires_at_ms,
        successful.pending_expires_at_ms,
    ) + 1

    with pytest.raises(DeadlineSweepError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert forged_report_returned.is_set()
    assert [device_id for device_id, _error in caught.value.failures] == ["123"]
    partial = caught.value.partial_report
    assert (
        partial.expired_pending,
        partial.retry_pending,
        partial.failed_attempt_limit,
        partial.incomplete_event_timeout,
    ) == (1, 0, 0, 0)
    assert len(partial.snapshots) == 1
    assert partial.snapshots[0].command.device_id == successful.device_id
    assert store.read_command(forged_command.command_id).state is CommandState.EXPIRED
    assert store.read_command(successful.command_id).state is CommandState.EXPIRED
    assert [(record.device_id, record.step) for record in records] == [
        (successful.device_id, SettingStep.EXPIRED)
    ]
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2
    assert coordinator.cached_status_snapshot.count(CommandState.RETRY_PENDING) == 0


@pytest.mark.parametrize(
    "malformation",
    [
        "attempt-command-link",
        "attempt-number-link",
        "attempt-session-link",
        "transition-attempt-link",
        "transition-session-link",
        "empty-session-link",
        "non-string-session-link",
        "boolean-attempt-link",
        "attempt-write-error",
    ],
)
@pytest.mark.asyncio
# pylint: disable-next=too-many-statements
async def test_forged_ack_timeout_attempt_linkage_reconciles_only_valid_sibling(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    forged_command = _enqueue(store)
    successful = _enqueue(store, device_id="456")
    awaiting_commands: dict[str, TwinCommand] = {}
    for index, command in enumerate((forged_command, successful)):
        prepared_at_ms = 200 + index * 10
        session_id = f"sweep-session-{command.device_id}"
        claim = store.prepare_next_attempt(
            device_id=command.device_id,
            session_id=session_id,
            prepared_at_ms=prepared_at_ms,
            render=deterministic_renderer,
        )
        assert claim.command is not None
        assert claim.attempt is not None
        store.mark_write_started(
            command_id=claim.command.command_id,
            attempt_number=claim.attempt.attempt_number,
            session_id=session_id,
            started_at_ms=prepared_at_ms + 1,
        )
        store.mark_attempt_drained(
            command_id=claim.command.command_id,
            attempt_number=claim.attempt.attempt_number,
            session_id=session_id,
            drained_at_ms=prepared_at_ms + 2,
        )
        awaiting = store.read_command(command.command_id)
        assert awaiting.state is CommandState.AWAITING_ACK
        assert awaiting.ack_deadline_ms is not None
        awaiting_commands[command.device_id] = awaiting

    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    original = store.sweep_device_deadlines
    forged_report_returned = threading.Event()

    def forged_attempt_linkage(*, device_id: str, now_ms: int):
        report = original(device_id=device_id, now_ms=now_ms)
        if device_id != forged_command.device_id:
            return report
        snapshot = report.snapshots[0]
        assert snapshot.attempt is not None
        forged_snapshot = snapshot
        if malformation == "attempt-command-link":
            forged_snapshot = replace(
                snapshot,
                attempt=replace(
                    snapshot.attempt,
                    command_id="forged-attempt-command",
                ),
            )
        elif malformation == "attempt-number-link":
            forged_snapshot = replace(
                snapshot,
                attempt=replace(
                    snapshot.attempt,
                    attempt_number=snapshot.attempt.attempt_number + 1,
                ),
            )
        elif malformation == "attempt-session-link":
            forged_snapshot = replace(
                snapshot,
                attempt=replace(
                    snapshot.attempt,
                    session_id="forged-attempt-session",
                ),
            )
        elif malformation == "transition-attempt-link":
            forged_snapshot = replace(
                snapshot,
                transition=replace(
                    snapshot.transition,
                    attempt_number=snapshot.attempt.attempt_number + 1,
                ),
            )
        elif malformation == "transition-session-link":
            forged_snapshot = replace(
                snapshot,
                transition=replace(
                    snapshot.transition,
                    session_id="forged-transition-session",
                ),
            )
        elif malformation == "empty-session-link":
            forged_snapshot = replace(
                snapshot,
                attempt=replace(snapshot.attempt, session_id=""),
                transition=replace(snapshot.transition, session_id=""),
            )
        elif malformation == "non-string-session-link":
            forged_session = cast(Any, object())
            forged_snapshot = replace(
                snapshot,
                attempt=replace(
                    snapshot.attempt,
                    session_id=forged_session,
                ),
                transition=replace(
                    snapshot.transition,
                    session_id=forged_session,
                ),
            )
        elif malformation == "boolean-attempt-link":
            forged_snapshot = replace(
                snapshot,
                attempt=replace(snapshot.attempt, attempt_number=True),
                transition=replace(snapshot.transition, attempt_number=True),
            )
        else:
            forged_snapshot = replace(
                snapshot,
                attempt=replace(
                    snapshot.attempt,
                    write_error="forged-unprojected-error",
                ),
            )
        forged_report_returned.set()
        return SweepReport(0, 1, 0, 0, (forged_snapshot,))

    monkeypatch.setattr(store, "sweep_device_deadlines", forged_attempt_linkage)
    effective_now = max(
        cast(int, command.ack_deadline_ms)
        for command in awaiting_commands.values()
    ) + 1

    with pytest.raises(DeadlineSweepError) as caught:
        await coordinator.sweep_deadlines(now_ms=effective_now)

    assert forged_report_returned.is_set()
    assert [device_id for device_id, _error in caught.value.failures] == ["123"]
    partial = caught.value.partial_report
    assert (
        partial.expired_pending,
        partial.retry_pending,
        partial.failed_attempt_limit,
        partial.incomplete_event_timeout,
    ) == (0, 1, 0, 0)
    assert len(partial.snapshots) == 1
    assert partial.snapshots[0].command.device_id == successful.device_id
    assert store.read_command(forged_command.command_id).state is (
        CommandState.RETRY_PENDING
    )
    assert store.read_command(successful.command_id).state is (
        CommandState.RETRY_PENDING
    )
    assert [(record.device_id, record.step) for record in records] == [
        (successful.device_id, SettingStep.RETRY)
    ]
    assert coordinator.cached_status_snapshot.count(CommandState.RETRY_PENDING) == 2
    assert coordinator.cached_status_snapshot.count(CommandState.AWAITING_ACK) == 0


@pytest.mark.asyncio
async def test_malformed_cancelled_worker_preserves_cancellation_dominance(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled_command = _enqueue(store)
    successful = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    entered = {device_id: threading.Event() for device_id in ("123", "456")}
    release = {device_id: threading.Event() for device_id in ("123", "456")}
    finished = {device_id: threading.Event() for device_id in ("123", "456")}
    original = store.sweep_device_deadlines

    def controlled_sweep(*, device_id: str, now_ms: int):
        entered[device_id].set()
        assert release[device_id].wait(timeout=1)
        try:
            report = original(device_id=device_id, now_ms=now_ms)
            if device_id == cancelled_command.device_id:
                snapshots = cast(
                    tuple[TransitionAuditSnapshot, ...],
                    (object(),),
                )
                return SweepReport(1, 0, 0, 0, snapshots)
            return report
        finally:
            finished[device_id].set()

    monkeypatch.setattr(store, "sweep_device_deadlines", controlled_sweep)
    original_create_task = asyncio.create_task
    device_workers: list[asyncio.Task[Any]] = []

    def capture_device_worker(coro, **kwargs):
        task = original_create_task(coro, **kwargs)
        device_workers.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", capture_device_worker)
    effective_now = max(
        cancelled_command.pending_expires_at_ms,
        successful.pending_expires_at_ms,
    ) + 1
    sweep_task = original_create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    assert await asyncio.to_thread(entered["123"].wait, 0.2)
    assert await asyncio.to_thread(entered["456"].wait, 0.2)
    assert len(device_workers) == 2
    monkeypatch.setattr(asyncio, "create_task", original_create_task)

    device_workers[0].cancel()
    release["123"].set()
    assert await asyncio.to_thread(finished["123"].wait, 0.2)
    sibling_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(sibling_turn.set)
    await sibling_turn.wait()
    waited_for_sibling = not sweep_task.done()
    assert records == []
    release["456"].set()

    with pytest.raises(asyncio.CancelledError):
        await sweep_task
    await asyncio.gather(*device_workers, return_exceptions=True)

    assert waited_for_sibling is True
    assert await asyncio.to_thread(finished["456"].wait, 0.2)
    assert store.read_command(cancelled_command.command_id).state is (
        CommandState.EXPIRED
    )
    assert store.read_command(successful.command_id).state is CommandState.EXPIRED
    assert [(record.device_id, record.step) for record in records] == [
        (successful.device_id, SettingStep.EXPIRED)
    ]
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2


@pytest.mark.asyncio
async def test_cancelled_deadline_sweep_reconciles_all_device_commits(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    entered = {device_id: threading.Event() for device_id in ("123", "456")}
    release = {device_id: threading.Event() for device_id in ("123", "456")}
    finished = {device_id: threading.Event() for device_id in ("123", "456")}
    original = store.sweep_device_deadlines

    def controlled_sweep(*, device_id: str, now_ms: int):
        entered[device_id].set()
        assert release[device_id].wait(timeout=1)
        try:
            return original(device_id=device_id, now_ms=now_ms)
        finally:
            finished[device_id].set()

    monkeypatch.setattr(store, "sweep_device_deadlines", controlled_sweep)
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    sweep_task = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    assert await asyncio.to_thread(entered["123"].wait, 0.1)
    assert await asyncio.to_thread(entered["456"].wait, 0.1)

    sweep_task.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    cancellation_waited_for_workers = not sweep_task.done()
    release["456"].set()
    assert await asyncio.to_thread(finished["456"].wait, 0.1)
    release["123"].set()

    with pytest.raises(asyncio.CancelledError):
        await sweep_task
    assert await asyncio.to_thread(finished["123"].wait, 0.1)

    assert cancellation_waited_for_workers is True
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.EXPIRED
    transition_ids = [
        record.transition_id
        for record in records
        if record.transition_id is not None
    ]
    assert len(transition_ids) == len(records)
    assert transition_ids == sorted(transition_ids)
    assert [record.step for record in records] == [
        SettingStep.EXPIRED,
        SettingStep.EXPIRED,
    ]
    assert dict(coordinator.cached_status_snapshot.state_counts)[
        CommandState.EXPIRED
    ] == 2
    assert dict(coordinator.cached_status_snapshot.state_counts)[
        CommandState.PENDING
    ] == 0


@pytest.mark.asyncio
# pylint: disable-next=too-many-statements
async def test_pre_settlement_cancellation_drains_workers_before_propagating(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    entered = {device_id: threading.Event() for device_id in ("123", "456")}
    release = {device_id: threading.Event() for device_id in ("123", "456")}
    finished = {device_id: threading.Event() for device_id in ("123", "456")}
    original_sweep = store.sweep_device_deadlines

    def controlled_sweep(*, device_id: str, now_ms: int):
        entered[device_id].set()
        assert release[device_id].wait(timeout=1)
        try:
            return original_sweep(device_id=device_id, now_ms=now_ms)
        finally:
            finished[device_id].set()

    monkeypatch.setattr(store, "sweep_device_deadlines", controlled_sweep)

    async def immediate_refresh() -> tuple[
        BaseException | None,
        asyncio.CancelledError | None,
    ]:
        coordinator._cached_status = (  # pylint: disable=protected-access
            store.status_snapshot()
        )
        return None, None

    monkeypatch.setattr(coordinator, "_refresh_status", immediate_refresh)
    original_create_task = asyncio.create_task
    created_tasks: list[asyncio.Task[Any]] = []
    sweep_reference: dict[str, asyncio.Task[Any]] = {}

    def inject_pre_settlement_cancellation(coro, **kwargs):
        task = original_create_task(coro, **kwargs)
        created_tasks.append(task)
        if len(created_tasks) == 2:
            owner = asyncio.current_task()
            assert owner is sweep_reference["task"]
            owner.cancel()
        elif len(created_tasks) == 3 and not any(
            barrier.is_set() for barrier in entered.values()
        ):
            task.cancel()
        return task

    monkeypatch.setattr(
        asyncio,
        "create_task",
        inject_pre_settlement_cancellation,
    )
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    sweep_task = original_create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    sweep_reference["task"] = sweep_task
    assert await asyncio.to_thread(entered["123"].wait, 0.2)
    assert await asyncio.to_thread(entered["456"].wait, 0.2)
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    cancellation_waited_for_workers = not sweep_task.done()
    assert records == []
    release["123"].set()
    assert await asyncio.to_thread(finished["123"].wait, 0.2)
    sibling_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(sibling_turn.set)
    await sibling_turn.wait()
    cancellation_waited_for_sibling = not sweep_task.done()
    assert records == []
    release["456"].set()

    with pytest.raises(asyncio.CancelledError):
        await sweep_task
    await asyncio.gather(*created_tasks, return_exceptions=True)

    assert cancellation_waited_for_workers is True
    assert cancellation_waited_for_sibling is True
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.EXPIRED
    assert [record.step for record in records] == [
        SettingStep.EXPIRED,
        SettingStep.EXPIRED,
    ]
    assert [record.transition_id for record in records] == sorted(
        cast(int, record.transition_id) for record in records
    )
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2


@pytest.mark.asyncio
async def test_direct_sweep_worker_cancellation_preserves_aggregate_reports(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    entered = {device_id: threading.Event() for device_id in ("123", "456")}
    release = {device_id: threading.Event() for device_id in ("123", "456")}
    original = store.sweep_device_deadlines

    def controlled_sweep(*, device_id: str, now_ms: int):
        entered[device_id].set()
        assert release[device_id].wait(timeout=1)
        return original(device_id=device_id, now_ms=now_ms)

    monkeypatch.setattr(store, "sweep_device_deadlines", controlled_sweep)
    original_create_task = asyncio.create_task
    device_workers: list[asyncio.Task[Any]] = []

    def capture_device_worker(coro, **kwargs):
        task = original_create_task(coro, **kwargs)
        device_workers.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", capture_device_worker)
    effective_now = max(
        first.pending_expires_at_ms,
        second.pending_expires_at_ms,
    ) + 1
    sweep_task = original_create_task(
        coordinator.sweep_deadlines(now_ms=effective_now)
    )
    assert await asyncio.to_thread(entered["123"].wait, 0.1)
    assert await asyncio.to_thread(entered["456"].wait, 0.1)
    assert len(device_workers) == 2
    monkeypatch.setattr(asyncio, "create_task", original_create_task)

    device_workers[0].cancel()
    release["123"].set()
    release["456"].set()

    with pytest.raises(asyncio.CancelledError):
        await sweep_task

    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.EXPIRED
    assert [record.step for record in records] == [
        SettingStep.EXPIRED,
        SettingStep.EXPIRED,
    ]
    assert [record.transition_id for record in records] == sorted(
        cast(int, record.transition_id) for record in records
    )
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2


@pytest.mark.asyncio
async def test_cancelled_aggregate_publication_still_refreshes_final_status(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    delivered: list[SettingsAuditRecord] = []
    proposal_entered = threading.Event()
    proposal_release = threading.Event()
    proposal_calls = 0
    original_proposal = store.propose_audit_delivery

    def block_first_proposal(**kwargs):
        nonlocal proposal_calls
        proposal_calls += 1
        if proposal_calls == 1:
            proposal_entered.set()
            assert proposal_release.wait(timeout=1)
        return original_proposal(**kwargs)

    monkeypatch.setattr(store, "propose_audit_delivery", block_first_proposal)
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            delivered.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    refresh_calls = 0
    original_refresh = coordinator._refresh_status  # pylint: disable=protected-access

    async def observed_refresh() -> tuple[
        BaseException | None,
        asyncio.CancelledError | None,
    ]:
        nonlocal refresh_calls
        refresh_calls += 1
        return await original_refresh()

    monkeypatch.setattr(coordinator, "_refresh_status", observed_refresh)
    effective_now = max(first.pending_expires_at_ms, second.pending_expires_at_ms) + 1
    sweep = asyncio.create_task(coordinator.sweep_deadlines(now_ms=effective_now))
    assert await asyncio.to_thread(proposal_entered.wait, 0.1)

    sweep.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    cancellation_waited = not sweep.done()
    proposal_release.set()

    with pytest.raises(asyncio.CancelledError):
        await sweep

    assert cancellation_waited is True
    assert store.read_command(first.command_id).state is CommandState.EXPIRED
    assert store.read_command(second.command_id).state is CommandState.EXPIRED
    assert [record.step for record in delivered] == [
        SettingStep.EXPIRED,
        SettingStep.EXPIRED,
    ]
    assert [record.transition_id for record in delivered] == sorted(
        cast(int, record.transition_id) for record in delivered
    )
    assert refresh_calls == 3
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2


@pytest.mark.asyncio
async def test_cancelled_aggregate_status_refresh_is_drained(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _enqueue(store)
    second = _enqueue(store, device_id="456")
    delivered: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            delivered.append,
            acceptance_ledger=store,
        ),
        clock_ms=_Clock(),
    )
    status_calls = 0
    status_guard = threading.Lock()
    aggregate_entered = threading.Event()
    aggregate_release = threading.Event()
    aggregate_finished = threading.Event()
    original_status = store.status_snapshot

    def blocked_aggregate_status(*args, **kwargs):
        nonlocal status_calls
        with status_guard:
            status_calls += 1
            call_number = status_calls
        if call_number == 3:
            aggregate_entered.set()
            assert aggregate_release.wait(timeout=1)
        try:
            return original_status(*args, **kwargs)
        finally:
            if call_number == 3:
                aggregate_finished.set()

    monkeypatch.setattr(store, "status_snapshot", blocked_aggregate_status)
    effective_now = max(first.pending_expires_at_ms, second.pending_expires_at_ms) + 1
    sweep = asyncio.create_task(coordinator.sweep_deadlines(now_ms=effective_now))
    assert await asyncio.to_thread(aggregate_entered.wait, 0.2)

    sweep.cancel()
    cancellation_turn = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    cancellation_waited = not sweep.done()
    aggregate_release.set()

    with pytest.raises(asyncio.CancelledError):
        await sweep

    assert cancellation_waited is True
    assert await asyncio.to_thread(aggregate_finished.wait, 0.1)
    assert status_calls == 3
    assert [record.step for record in delivered] == [
        SettingStep.EXPIRED,
        SettingStep.EXPIRED,
    ]
    assert coordinator.cached_status_snapshot.count(CommandState.EXPIRED) == 2


@pytest.mark.asyncio
async def test_in_deadline_registered_event_wins_over_second_timeout_pass(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw = b"ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    assert command.event_deadline_ms is not None

    first_sweep = await coordinator.sweep_deadlines(
        now_ms=command.event_deadline_ms + 1
    )
    assert first_sweep.incomplete_event_timeout == 0
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            command.event_deadline_ms,
            b"boundary-event",
        ),
    )
    monotonic["value"] = 1.1
    second_sweep = await coordinator.sweep_deadlines(
        now_ms=command.event_deadline_ms + 2
    )
    decision = await coordinator.handle_registered_event(token)

    assert second_sweep.incomplete_event_timeout == 0
    assert decision.disposition is EventDisposition.CONFIRMED
    assert store.read_command(active.command_id).state is CommandState.CONFIRMED


@pytest.mark.asyncio
async def test_two_pass_sweeper_marks_exact_unchanged_candidate_incomplete(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw = b"ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=writer,
    )
    deadline = store.read_command(active.command_id).event_deadline_ms
    assert deadline is not None

    first = await coordinator.sweep_deadlines(now_ms=deadline + 1)
    monotonic["value"] = 0.9
    grace = await coordinator.sweep_deadlines(now_ms=deadline + 2)
    monotonic["value"] = 1.0
    second = await coordinator.sweep_deadlines(now_ms=deadline + 3)

    assert first.incomplete_event_timeout == 0
    assert grace.incomplete_event_timeout == 0
    assert second.incomplete_event_timeout == 1
    assert store.read_command(active.command_id).state is CommandState.INCOMPLETE


@pytest.mark.parametrize(
    ("receipt_offset", "expected_state"),
    [
        pytest.param(-1, CommandState.INCOMPLETE, id="before-ack"),
        pytest.param(0, CommandState.CONFIRMED, id="equal-ack"),
        pytest.param(1, CommandState.CONFIRMED, id="after-ack"),
    ],
)
@pytest.mark.asyncio
async def test_timeout_authorization_uses_inclusive_ack_receipt_lower_bound(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    receipt_offset: int,
    expected_state: CommandState,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw_ack = b"ack-receipt-lower-bound"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw_ack),
        context=_context(active, raw_ack, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    acked_at_ms = command.acked_at_ms
    deadline = command.event_deadline_ms
    assert acked_at_ms is not None
    assert deadline is not None
    await coordinator.sweep_deadlines(now_ms=deadline + 1)
    mutation_entered = threading.Event()
    mutation_release = threading.Event()
    original_timeout = store.mark_event_incomplete

    def blocked_timeout(**kwargs):
        mutation_entered.set()
        assert mutation_release.wait(timeout=1)
        return original_timeout(**kwargs)

    monkeypatch.setattr(store, "mark_event_incomplete", blocked_timeout)
    monotonic["value"] = 1.1
    sweep = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=deadline + 2)
    )
    assert await asyncio.to_thread(mutation_entered.wait, 0.1)
    token = coordinator.register_setting_event(
        event=_event(),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            acked_at_ms + receipt_offset,
            b"ack-receipt-boundary-event",
        ),
    )
    mutation_release.set()
    report = await sweep
    decision = await coordinator.handle_registered_event(token)

    if expected_state is CommandState.CONFIRMED:
        assert report.incomplete_event_timeout == 0
        assert decision.disposition is EventDisposition.CONFIRMED
    else:
        assert report.incomplete_event_timeout == 1
        assert decision.disposition is EventDisposition.UNMATCHED
    assert store.read_command(active.command_id).state is expected_state


@pytest.mark.parametrize(
    (
        "event_item",
        "event_value",
        "receipt_offset",
        "event_device_dt",
        "content_value",
        "expected_state",
    ),
    [
        ("HEAT", "2", 0, "06.08.2026 10:12:01", None, CommandState.INCOMPLETE),
        ("MODE", "3", 0, "06.08.2026 10:12:01", None, CommandState.INCOMPLETE),
        ("MODE", "2", 1, "06.08.2026 10:12:01", None, CommandState.INCOMPLETE),
        ("MODE", "2", 0, "06.08.2026 10:12:01", None, CommandState.CONFIRMED),
        ("MODE", "02", 0, "06.08.2026 10:12:01", None, CommandState.CONFIRMED),
        ("MODE", "invalid", 0, "06.08.2026 10:12:01", None, CommandState.INCOMPLETE),
        ("MODE", "2", 0, "06.08.2026 10:12:01", "3", CommandState.INCOMPLETE),
        ("MODE", "2", 0, "06.08.2026 10:11:59", None, CommandState.INCOMPLETE),
        ("MODE", "2", 0, "06.08.2026 10:12:00", None, CommandState.CONFIRMED),
    ],
)
@pytest.mark.asyncio
async def test_timeout_authorization_uses_exact_eligible_event_identity(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
    event_item: str,
    event_value: str,
    receipt_offset: int,
    event_device_dt: str,
    content_value: str | None,
    expected_state: CommandState,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw_ack = b"exact-authorization-ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw_ack),
        context=_context(active, raw_ack, received_at_ms=220),
        writer=writer,
    )
    command = store.read_command(active.command_id)
    deadline = command.event_deadline_ms
    assert deadline is not None
    await coordinator.sweep_deadlines(now_ms=deadline + 1)
    mutation_entered = threading.Event()
    mutation_release = threading.Event()
    original = store.mark_event_incomplete

    def blocked_timeout(**kwargs):
        mutation_entered.set()
        assert mutation_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "mark_event_incomplete", blocked_timeout)
    monotonic["value"] = 1.1
    sweep = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=deadline + 2)
    )
    assert await asyncio.to_thread(mutation_entered.wait, 0.1)
    event = _event(
        item_name=event_item,
        new_value=event_value,
        device_dt=event_device_dt,
    )
    if content_value is not None:
        content = (
            f"Remotely : tbl_box_prms / {event_item}: [1]->[{content_value}]"
        )
        event = replace(
            event,
            evidence_id=derive_event_evidence_id(
                event.device_id,
                event.event_id_set,
                event.device_dt,
                content,
            ),
            content_text=content,
        )
    token = coordinator.register_setting_event(
        event=event,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY,
            active.session_id,
            active.device_id,
            deadline + receipt_offset,
            b"authorization-event",
        ),
    )
    mutation_release.set()
    report = await sweep

    if expected_state is CommandState.CONFIRMED:
        decision = await coordinator.handle_registered_event(token)
        assert report.incomplete_event_timeout == 0
        assert decision.disposition is EventDisposition.CONFIRMED
    else:
        assert report.incomplete_event_timeout == 1
        decision = await coordinator.handle_registered_event(token)
        assert decision.disposition is EventDisposition.UNMATCHED
    assert store.read_command(active.command_id).state is expected_state


@pytest.mark.asyncio
async def test_repeated_late_events_cannot_restart_timeout_grace(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(store)
    monotonic = {"value": 0.0}
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        clock_ms=_Clock(),
        monotonic=lambda: monotonic["value"],
    )
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer, now_ms=100)
    raw_ack = b"late-traffic-ack"
    await coordinator.handle_local_response(
        active=active,
        response=_response(raw_ack),
        context=_context(active, raw_ack, received_at_ms=220),
        writer=writer,
    )
    deadline = store.read_command(active.command_id).event_deadline_ms
    assert deadline is not None
    await coordinator.sweep_deadlines(now_ms=deadline + 1)
    mutation_entered = threading.Event()
    mutation_release = threading.Event()
    original = store.mark_event_incomplete

    def blocked_timeout(**kwargs):
        mutation_entered.set()
        assert mutation_release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "mark_event_incomplete", blocked_timeout)
    monotonic["value"] = 1.1
    sweep = asyncio.create_task(
        coordinator.sweep_deadlines(now_ms=deadline + 2)
    )
    assert await asyncio.to_thread(mutation_entered.wait, 0.1)
    late_tokens = tuple(
        coordinator.register_setting_event(
            event=_event(event_id_set=event_id_set),
            context=EvidenceContext(
                FrameDirection.BOX_TO_PROXY,
                active.session_id,
                active.device_id,
                deadline + event_id_set,
                f"late-{event_id_set}".encode(),
            ),
        )
        for event_id_set in (1, 2, 3)
    )
    mutation_release.set()
    report = await sweep

    assert report.incomplete_event_timeout == 1
    assert store.read_command(active.command_id).state is CommandState.INCOMPLETE
    delivered = await coordinator.flush_registered_events(
        session_id=active.session_id
    )
    assert [result.evidence.evidence_id for result in delivered] == [
        token.event.evidence_id for token in late_tokens
    ]
    assert all(
        result.disposition is EventDisposition.UNMATCHED for result in delivered
    )


@pytest.mark.asyncio
async def test_cached_status_is_observability_only(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    coordinator._cached_status = replace(  # pylint: disable=protected-access
        coordinator.cached_status_snapshot,
        control_available=False,
        degradation_reason="stale telemetry",
    )

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )

    assert result.disposition is DeliveryDisposition.SENT
    assert writer.frames


@pytest.mark.asyncio
async def test_device_scoped_status_read_does_not_replace_global_cache(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    _enqueue(store)
    _enqueue(store, device_id="456")
    coordinator = TwinCoordinator(
        store, renderer=deterministic_renderer, clock_ms=_Clock()
    )
    global_status = await coordinator.status_snapshot()

    scoped_status = await coordinator.status_snapshot("123")

    assert scoped_status.nonterminal_commands == 1
    assert global_status.nonterminal_commands == 2
    assert coordinator.cached_status_snapshot == global_status


@pytest.mark.asyncio
async def test_status_refresh_failure_does_not_change_delivery_decision(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    before = coordinator.cached_status_snapshot

    def fail_status(_device_id: str | None = None) -> object:
        raise RuntimeError("status telemetry unavailable")

    monkeypatch.setattr(store, "status_snapshot", fail_status)

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="session-a",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )

    assert result.disposition is DeliveryDisposition.SENT
    assert writer.frames
    assert coordinator.cached_status_snapshot == before


@pytest.mark.asyncio
async def test_production_renderer_bounds_random_collisions_and_uses_serializer(
    store_factory: Callable[[int], TwinCommandStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_factory(2)
    _enqueue(store)
    samples = {"count": 0}

    def collide(_upper: int) -> int:
        samples["count"] += 1
        return 1

    monkeypatch.setattr("twin.delivery.secrets.randbelow", collide)
    coordinator = TwinCoordinator(store, clock_ms=_Clock())
    writer = ScriptedLocalSettingWriter(store)
    first = await _deliver(coordinator, writer, session="first", now_ms=200)
    await coordinator.abort_dialogue(
        active=first, occurred_at_ms=210, reason=RetryReason.DISCONNECT
    )

    second = await _deliver(coordinator, writer, session="second", now_ms=300)

    first_wire = first.wire_frame.decode("utf-8")
    second_wire = second.wire_frame.decode("utf-8")
    assert "<ver>00001</ver>" in first_wire
    assert "<ver>00000</ver>" in second_wire
    assert "<TSec>1970-01-01 00:00:00</TSec>" in second_wire
    assert second_wire.endswith("</Frame>\r\n")
    assert samples["count"] == 17


@pytest.mark.asyncio
async def test_coordinator_publishes_only_committed_delivery_snapshots(
    store: TwinCommandStore,
    deterministic_renderer: Callable,
) -> None:
    _enqueue(store)
    records: list[SettingsAuditRecord] = []
    coordinator = TwinCoordinator(
        store,
        renderer=deterministic_renderer,
        audit_publisher=SettingsAuditPublisher(
            records.append, acceptance_ledger=store
        ),
        clock_ms=_Clock(),
    )
    writer = ScriptedLocalSettingWriter(store)

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="s",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )

    assert coordinator.store is store
    assert result.disposition is DeliveryDisposition.SENT
    assert [record.step.value for record in records] == [
        "selected",
        "attempt_prepared",
        "write_started",
        "attempt_drained",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_outcome"),
    [
        (AttemptWriteOutcome.FAILED, AttemptWriteOutcome.FAILED),
        (AttemptWriteOutcome.UNKNOWN, AttemptWriteOutcome.UNKNOWN),
    ],
)
async def test_accepted_ack_closes_when_prepared_successor_write_is_not_drained(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
    outcome: AttemptWriteOutcome,
    expected_outcome: AttemptWriteOutcome,
) -> None:
    initial_writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, initial_writer)
    successor = _enqueue(
        store,
        value_text="50",
        received_at_ms=210,
        item_name="BAT_AC",
    )
    raw = b"ack-with-successor"
    failing_writer = ScriptedLocalSettingWriter(store, outcome=outcome)

    result = await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, received_at_ms=220),
        writer=failing_writer,
    )

    assert result.disposition is LocalResponseDisposition.ACK_ACCEPTED
    assert result.next_attempt is None
    assert result.close_connection is True
    assert store.read_command(successor.command_id).state is CommandState.RETRY_PENDING
    assert store.read_attempt(successor.command_id, 1).write_outcome is expected_outcome


@pytest.mark.asyncio
async def test_exact_duplicate_nack_is_idempotent(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    raw = b"same-nack"
    response = _response(raw, result="NACK")
    context = _context(active, raw, received_at_ms=220)
    first = await coordinator.handle_local_response(
        active=active, response=response, context=context, writer=writer
    )

    duplicate = await coordinator.handle_local_response(
        active=active, response=response, context=context, writer=writer
    )

    assert first.disposition is LocalResponseDisposition.NACK_ACCEPTED
    assert duplicate.disposition is LocalResponseDisposition.DUPLICATE
    assert duplicate.close_connection is False


@pytest.mark.asyncio
async def test_stale_unexpected_response_closes_without_second_retry(
    coordinator: TwinCoordinator,
    store: TwinCommandStore,
) -> None:
    writer = ScriptedLocalSettingWriter(store)
    active = await _deliver(coordinator, writer)
    await coordinator.abort_dialogue(
        active=active, occurred_at_ms=210, reason=RetryReason.DISCONNECT
    )
    raw = b"late-after-abort"

    result = await coordinator.handle_local_response(
        active=active,
        response=_response(raw),
        context=_context(active, raw, session_id="wrong", received_at_ms=220),
        writer=writer,
    )

    assert result.disposition is LocalResponseDisposition.REJECTED
    assert result.close_connection is True
    assert result.command is not None
    assert result.command.state is CommandState.RETRY_PENDING
    assert store.read_command(active.command_id).attempt_count == 1


@pytest.mark.asyncio
async def test_registered_event_token_is_single_use(
    coordinator: TwinCoordinator,
) -> None:
    token = coordinator.register_setting_event(
        event=_event(new_value="3"),
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "s", "123", 220, b"event"
        ),
    )
    await coordinator.handle_registered_event(token)

    with pytest.raises(ValueError, match="not registered"):
        await coordinator.handle_registered_event(token)


@pytest.mark.asyncio
async def test_render_failure_is_terminal_without_writer_invocation(
    store: TwinCommandStore,
) -> None:
    _enqueue(store)

    def fail_render(_context: object) -> object:
        raise ValueError("cannot render")

    coordinator = TwinCoordinator(
        store, renderer=fail_render, clock_ms=_Clock()  # type: ignore[arg-type]
    )
    writer = ScriptedLocalSettingWriter(store)

    result = await coordinator.claim_and_write_next(
        device_id="123",
        session_id="s",
        received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET,
        writer=writer,
    )

    assert result.disposition is DeliveryDisposition.RENDER_FAILED
    assert writer.frames == []
    command_id = result.snapshots[0].command.command_id
    assert store.read_command(command_id).state is CommandState.FAILED
