"""Behavioral tests for per-device durable local-setting coordination."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring,too-many-lines
# pylint: disable=too-few-public-methods,too-many-instance-attributes
# pylint: disable=too-many-arguments,use-implicit-booleaness-not-comparison
# pylint: disable=too-many-locals

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Literal

import pytest

from protocol.frame import FrameDirection
from telemetry.settings_audit import SettingsAuditPublisher, SettingsAuditRecord
from twin.ack_parser import SettingEvent, SettingResponse, derive_event_evidence_id
from twin.delivery import TwinCoordinator
from twin.state import (
    ActiveLocalAttempt,
    AttemptWriteOutcome,
    AttemptWriteResult,
    CommandState,
    ControlIngress,
    DeliveryDisposition,
    DeliveryTrigger,
    EvidenceContext,
    EventDisposition,
    LocalResponseDisposition,
    RetryReason,
    TwinCommand,
)
from twin.store import StoreRecordNotFound, TwinCommandStore


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
        transition.reason for transition in store.read_transitions(successor.command_id)
    )
    assert reasons == ("accepted_ingress", "selected", "attempt_prepared", "write_started", "attempt_drained")


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


def test_legacy_delivery_remains_importable() -> None:
    from twin.delivery import TwinDelivery  # pylint: disable=import-outside-toplevel

    assert TwinDelivery.__name__ == "TwinDelivery"


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
        audit_publisher=SettingsAuditPublisher(records.append),
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
