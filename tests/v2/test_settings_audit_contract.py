"""Committed local audit projection and passive cloud-observer contracts."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring,too-many-lines
# pylint: disable=too-many-arguments,too-many-locals
# pylint: disable=use-implicit-booleaness-not-comparison

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from protocol.crc import crc16_modbus
from protocol.frame import ValidatedFrame
from protocol.parser import FrameMetadata
import telemetry.settings_audit as settings_audit_module
from telemetry.settings_audit import (
    CloudSettingAuditObserver,
    CloudSettingAuditRecord,
    SettingResult,
    SettingsAuditRecord,
    SettingsAuditPublisher,
    SettingStep,
    record_to_dict,
)
from twin.ack_parser import SettingEvent, SettingResponse, derive_event_evidence_id
from twin.state import (
    AttemptRenderer,
    CommandState,
    ControlPolicy,
    ControlIngress,
    RetryReason,
    TransitionAuditSnapshot,
)
from twin.store import TwinCommandStore


def _observe(store: TwinCommandStore, device_id: str = "123") -> None:
    store.observe_device(
        device_id=device_id,
        observed_at_ms=90,
        observed_wire_id=14_000_000,
        observed_wire_id_set=1_786_000_000,
    )


def _enqueue(
    store: TwinCommandStore,
    *,
    value_text: str = "2",
    received_at_ms: int = 100,
    item_name: str = "MODE",
    raw_text: str | None = None,
):
    ingress = ControlIngress(
        f"ing-{item_name}-{received_at_ms}-{value_text}",
        received_at_ms,
        "oig/123/control/set",
        "123",
        False,
        raw_text if raw_text is not None else f'{{"value":"{value_text}"}}',
    )
    return store.enqueue_command(
        ingress,
        device_id="123",
        table_name="tbl_box_prms",
        item_name=item_name,
        value_text=value_text,
    )


def _prepared_and_drained(
    store: TwinCommandStore,
    renderer: AttemptRenderer,
    *,
    session_id: str = "session-a",
    now_ms: int = 200,
) -> tuple[TransitionAuditSnapshot, ...]:
    claim = store.prepare_next_attempt(
        device_id="123",
        session_id=session_id,
        prepared_at_ms=now_ms,
        render=renderer,
    )
    assert claim.command is not None and claim.attempt is not None
    started = store.mark_write_started(
        command_id=claim.command.command_id,
        attempt_number=claim.attempt.attempt_number,
        session_id=session_id,
        started_at_ms=now_ms + 1,
    )
    drained = store.mark_attempt_drained(
        command_id=claim.command.command_id,
        attempt_number=claim.attempt.attempt_number,
        session_id=session_id,
        drained_at_ms=now_ms + 2,
    )
    return (*claim.snapshots, started, drained)


def _response(raw: bytes, result: str = "ACK") -> SettingResponse:
    return SettingResponse(
        result=result,  # type: ignore[arg-type]
        reason="Setting" if result == "ACK" else "Rejected",
        rdt_text="06.08.2026 10:12:00",
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _event(
    new_value: str = "2", *, item_name: str = "MODE"
) -> SettingEvent:
    content = (
        f"Remotely : tbl_box_prms / {item_name}: [1]->[{new_value}]"
    )
    evidence_id = derive_event_evidence_id(
        "123", 55, "06.08.2026 10:12:01", content
    )
    return SettingEvent(
        evidence_id,
        "123",
        55,
        "06.08.2026 10:12:01",
        content,
        "tbl_box_prms",
        item_name,
        "1",
        new_value,
    )


@pytest.fixture
def committed_snapshots(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> tuple[TransitionAuditSnapshot, ...]:
    _observe(store)
    enqueued = _enqueue(store)
    write = _prepared_and_drained(store, deterministic_renderer)
    command = write[-1].command
    raw_ack = b"exact-ack-bytes"
    ack = store.acknowledge_and_prepare_next(
        command_id=command.command_id,
        attempt_number=command.attempt_count,
        session_id="session-a",
        response=_response(raw_ack),
        received_at_ms=220,
        evidence_frame=raw_ack,
        render=deterministic_renderer,
    )
    event = store.record_event(
        evidence=_event(),
        received_at_ms=230,
        evidence_frame=b"exact-event-bytes",
    )
    assert event.snapshot is not None
    return (*enqueued.snapshots, *write, *ack.snapshots, event.snapshot)


def test_write_outcomes_and_telemetry_reuse_persisted_identity(
    committed_snapshots: tuple[TransitionAuditSnapshot, ...],
    store: TwinCommandStore,
) -> None:
    records: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    )

    for snapshot in committed_snapshots:
        publisher.publish_committed(snapshot)

    assert {record.command_id for record in records} == {
        committed_snapshots[0].command.command_id
    }
    assert {record.audit_id for record in records} == {
        committed_snapshots[0].command.audit_id
    }
    assert {record.msg_id for record in records if record.msg_id is not None} == {
        committed_snapshots[-1].command.wire_id
    }
    assert {record.id_set for record in records if record.id_set is not None} == {
        committed_snapshots[-1].command.wire_id_set
    }
    assert {record.write_outcome for record in records if record.write_outcome} >= {
        "prepared",
        "started",
        "drained",
    }


def test_transition_projection_maps_full_committed_lifecycle(
    committed_snapshots: tuple[TransitionAuditSnapshot, ...],
    store: TwinCommandStore,
) -> None:
    records: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    )
    for snapshot in committed_snapshots:
        publisher.publish_committed(snapshot)

    assert [record.step for record in records] == [
        SettingStep.ENQUEUED,
        SettingStep.SELECTED,
        SettingStep.ATTEMPT_PREPARED,
        SettingStep.WRITE_STARTED,
        SettingStep.ATTEMPT_DRAINED,
        SettingStep.ACK_OBSERVED,
        SettingStep.EVENT_CONFIRMED,
    ]
    assert records[-1].result is SettingResult.CONFIRMED
    assert committed_snapshots[-1].evidence is not None
    assert records[-1].evidence_id == committed_snapshots[-1].evidence.evidence_id


def test_projection_preserves_exact_persisted_wire_and_evidence_bytes(
    committed_snapshots: tuple[TransitionAuditSnapshot, ...],
    store: TwinCommandStore,
) -> None:
    records: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    )
    for snapshot in committed_snapshots:
        publisher.publish_committed(snapshot)

    prepared = next(
        record for record in records if record.step is SettingStep.ATTEMPT_PREPARED
    )
    acknowledged = next(
        record for record in records if record.step is SettingStep.ACK_OBSERVED
    )
    confirmed = next(
        record for record in records if record.step is SettingStep.EVENT_CONFIRMED
    )
    prepared_snapshot = next(
        snapshot
        for snapshot in committed_snapshots
        if snapshot.transition.reason == "attempt_prepared"
    )
    assert prepared_snapshot.attempt is not None
    assert committed_snapshots[-1].attempt is not None
    acknowledged_snapshot = next(
        snapshot
        for snapshot in committed_snapshots
        if snapshot.transition.reason == "ack_received"
    )
    assert acknowledged_snapshot.attempt is not None
    assert prepared.wire_frame == prepared_snapshot.attempt.wire_frame
    assert acknowledged.evidence_frame == b"exact-ack-bytes"
    assert acknowledged.evidence_id == (
        acknowledged_snapshot.attempt.response_fingerprint
    )
    assert confirmed.evidence_frame == b"exact-event-bytes"
    assert committed_snapshots[-1].evidence is not None
    assert confirmed.evidence_id == committed_snapshots[-1].evidence.evidence_id
    assert confirmed.wire_frame == committed_snapshots[-1].attempt.wire_frame


def test_record_dictionary_is_deterministic_and_json_serializable(
    committed_snapshots: tuple[TransitionAuditSnapshot, ...],
    store: TwinCommandStore,
) -> None:
    records: list[SettingsAuditRecord] = []
    SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    ).publish_committed(committed_snapshots[-1])

    projected = record_to_dict(records[0])

    assert json.loads(json.dumps(projected, sort_keys=True)) == projected
    assert projected["wire_frame_b64"]
    assert projected["evidence_frame_b64"]
    assert projected["command_id"] == committed_snapshots[-1].command.command_id


def test_audit_sink_failure_does_not_change_committed_state(
    committed_snapshots: tuple[TransitionAuditSnapshot, ...],
    store: TwinCommandStore,
) -> None:
    snapshot = committed_snapshots[4]
    assert snapshot.command.state is CommandState.AWAITING_ACK

    def failing_sink(_record: object) -> None:
        raise RuntimeError("telemetry unavailable")

    SettingsAuditPublisher(
        failing_sink, acceptance_ledger=store
    ).publish_committed(snapshot)

    assert snapshot.command.state is CommandState.AWAITING_ACK
    assert snapshot.attempt is not None
    assert snapshot.attempt.write_outcome.value == "drained"


@pytest.mark.parametrize(
    ("operation", "expected_step", "expected_state"),
    [
        ("retry", SettingStep.RETRY, CommandState.RETRY_PENDING),
        ("nack", SettingStep.NACK, CommandState.FAILED),
        ("write_failed", SettingStep.WRITE_FAILED, CommandState.RETRY_PENDING),
        ("write_unknown", SettingStep.WRITE_UNKNOWN, CommandState.RETRY_PENDING),
    ],
)
def test_projection_maps_retry_nack_and_write_failures(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: AttemptRenderer,
    operation: str,
    expected_step: SettingStep,
    expected_state: CommandState,
) -> None:
    store = store_factory(8)
    _observe(store)
    _enqueue(store)
    claim = store.prepare_next_attempt(
        device_id="123",
        session_id="s",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    assert claim.command is not None and claim.attempt is not None
    if operation in {"retry", "nack", "write_unknown"}:
        store.mark_write_started(
            command_id=claim.command.command_id,
            attempt_number=1,
            session_id="s",
            started_at_ms=201,
        )
    if operation == "retry":
        snapshot = store.release_for_retry(
            command_id=claim.command.command_id,
            attempt_number=1,
            session_id="s",
            occurred_at_ms=202,
            reason=RetryReason.DISCONNECT,
        )
    elif operation == "nack":
        raw = b"nack"
        result = store.mark_nack(
            command_id=claim.command.command_id,
            attempt_number=1,
            session_id="s",
            response=_response(raw, "NACK"),
            received_at_ms=202,
            evidence_frame=raw,
        )
        snapshot = result.snapshots[0]
    elif operation == "write_failed":
        snapshot = store.mark_write_failed(
            command_id=claim.command.command_id,
            attempt_number=1,
            session_id="s",
            occurred_at_ms=201,
            error="write failed",
        )
    else:
        snapshot = store.mark_write_unknown(
            command_id=claim.command.command_id,
            attempt_number=1,
            session_id="s",
            occurred_at_ms=202,
            error="drain unknown",
        )
    records: list[SettingsAuditRecord] = []

    SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    ).publish_committed(snapshot)

    assert records[0].step is expected_step
    assert records[0].to_state is expected_state
    assert records[0].error
    if operation == "nack":
        assert records[0].evidence_id == hashlib.sha256(b"nack").hexdigest()


def test_projection_maps_superseded_expired_incomplete_and_failed(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: AttemptRenderer,
) -> None:
    records: list[SettingsAuditRecord] = []

    superseded_store = store_factory(8)
    _observe(superseded_store)
    _enqueue(superseded_store, received_at_ms=100)
    second = _enqueue(superseded_store, value_text="3", received_at_ms=101)
    SettingsAuditPublisher(
        records.append, acceptance_ledger=superseded_store
    ).publish_committed(second.snapshots[0])

    expired_store = store_factory(8)
    _observe(expired_store)
    _enqueue(expired_store, received_at_ms=100)
    expired = expired_store.sweep_deadlines(now_ms=900_101)
    SettingsAuditPublisher(
        records.append, acceptance_ledger=expired_store
    ).publish_committed(expired.snapshots[0])

    incomplete_store = store_factory(8)
    _observe(incomplete_store)
    _enqueue(incomplete_store, received_at_ms=100)
    write = _prepared_and_drained(incomplete_store, deterministic_renderer)
    raw = b"ack"
    ack = incomplete_store.acknowledge_and_prepare_next(
        command_id=write[-1].command.command_id,
        attempt_number=1,
        session_id="session-a",
        response=_response(raw),
        received_at_ms=220,
        evidence_frame=raw,
        render=deterministic_renderer,
    )
    assert ack.accepted_command is not None
    deadline = ack.accepted_command.event_deadline_ms
    assert deadline is not None
    incomplete = incomplete_store.mark_event_incomplete(
        command_id=ack.accepted_command.command_id,
        expected_event_deadline_ms=deadline,
        now_ms=deadline + 1,
    )
    assert incomplete is not None
    SettingsAuditPublisher(
        records.append, acceptance_ledger=incomplete_store
    ).publish_committed(incomplete)

    failed_store = store_factory(1)
    _observe(failed_store)
    _enqueue(failed_store, received_at_ms=100)
    failed_claim = failed_store.prepare_next_attempt(
        device_id="123",
        session_id="s",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    assert failed_claim.command is not None
    failed_store.mark_write_started(
        command_id=failed_claim.command.command_id,
        attempt_number=1,
        session_id="s",
        started_at_ms=201,
    )
    failed = failed_store.release_for_retry(
        command_id=failed_claim.command.command_id,
        attempt_number=1,
        session_id="s",
        occurred_at_ms=202,
        reason=RetryReason.ACK_TIMEOUT,
    )
    SettingsAuditPublisher(
        records.append, acceptance_ledger=failed_store
    ).publish_committed(failed)

    assert [record.step for record in records] == [
        SettingStep.SUPERSEDED,
        SettingStep.EXPIRED,
        SettingStep.INCOMPLETE,
        SettingStep.FAILED,
    ]


def test_sensitive_projection_redacts_values_and_exact_payloads(
    committed_snapshots: tuple[TransitionAuditSnapshot, ...],
    store: TwinCommandStore,
) -> None:
    snapshot = committed_snapshots[-1]
    sensitive = replace(
        snapshot,
        command=replace(
            snapshot.command,
            item_name="API_TOKEN",
            value_text="top-secret",
            raw_ingress_text='{"API_TOKEN":"top-secret"}',
        ),
    )
    records: list[SettingsAuditRecord] = []

    SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    ).publish_committed(sensitive)

    record = records[0]
    assert record.value_text == "[REDACTED]"
    assert record.raw_text == "[REDACTED]"
    assert record.wire_frame == b"[REDACTED]"
    assert record.evidence_frame == b"[REDACTED]"


def test_committed_projection_preserves_per_audit_raw_payload_cap(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    snapshots = _large_lifecycle_snapshots(store, deterministic_renderer)
    records: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    )

    for snapshot in snapshots:
        publisher.publish_committed(snapshot)

    assert sum(len(record.raw_text.encode("utf-8")) for record in records) <= (
        64 * 1024
    )
    assert records[-1].audit_payload_capped is True


def _large_lifecycle_snapshots(
    store: TwinCommandStore,
    renderer: AttemptRenderer,
) -> tuple[TransitionAuditSnapshot, ...]:
    _observe(store)
    enqueued = _enqueue(store, raw_text="x" * (16 * 1024))
    write = _prepared_and_drained(store, renderer)
    snapshots = (*enqueued.snapshots, *write)
    assert len(snapshots) == 5
    return snapshots


def _large_confirmed_lifecycle_snapshots(
    store: TwinCommandStore,
    renderer: AttemptRenderer,
) -> tuple[TransitionAuditSnapshot, ...]:
    snapshots = _large_lifecycle_snapshots(store, renderer)
    active = snapshots[-1].command
    raw_ack = b"large-lifecycle-ack"
    acknowledged = store.acknowledge_and_prepare_next(
        command_id=active.command_id,
        attempt_number=active.attempt_count,
        session_id="session-a",
        response=_response(raw_ack),
        received_at_ms=220,
        evidence_frame=raw_ack,
        render=renderer,
    )
    confirmed = store.record_event(
        evidence=_event(),
        received_at_ms=230,
        evidence_frame=b"large-lifecycle-event",
    )
    assert confirmed.snapshot is not None
    return (*snapshots, *acknowledged.snapshots, confirmed.snapshot)


def test_repeated_committed_snapshot_projects_identically_without_recharging(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    snapshot = _large_lifecycle_snapshots(store, deterministic_renderer)[0]
    records: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    )

    for _ in range(5):
        publisher.publish_committed(snapshot)

    assert records == [records[0]] * 5
    assert len(records[0].raw_text.encode("utf-8")) == 16 * 1024
    assert records[0].audit_payload_capped is False


def test_failed_sink_does_not_consume_later_accepted_audit_budget(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    snapshots = _large_lifecycle_snapshots(store, deterministic_renderer)
    accepted: list[SettingsAuditRecord] = []
    call_count = 0

    def fail_first(record: SettingsAuditRecord) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("sink unavailable")
        accepted.append(record)

    publisher = SettingsAuditPublisher(
        fail_first, acceptance_ledger=store
    )
    publisher.publish_committed(snapshots[0])
    publisher.publish_committed(snapshots[0])
    for snapshot in snapshots[1:]:
        publisher.publish_committed(snapshot)

    assert [len(record.raw_text.encode("utf-8")) for record in accepted[:4]] == [
        16 * 1024,
    ] * 4
    assert accepted[4].raw_text == ""
    assert accepted[4].audit_payload_capped is True


def test_active_audit_budget_does_not_expire_after_300_seconds(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = _large_lifecycle_snapshots(store, deterministic_renderer)
    now = {"value": 100.0}
    monkeypatch.setattr(
        settings_audit_module.time, "time", lambda: now["value"]
    )
    records: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(
        records.append, acceptance_ledger=store
    )
    for snapshot in snapshots[:4]:
        publisher.publish_committed(snapshot)

    now["value"] += 301.0
    publisher.publish_committed(snapshots[4])

    assert sum(len(record.raw_text.encode("utf-8")) for record in records) == (
        64 * 1024
    )
    assert records[-1].raw_text == ""
    assert records[-1].audit_payload_capped is True


def test_failed_first_then_four_accepts_and_retry_uses_durable_budget(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    snapshots = _large_lifecycle_snapshots(store, deterministic_renderer)
    accepted: list[SettingsAuditRecord] = []
    call_count = 0

    def fail_first(record: SettingsAuditRecord) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("sink unavailable for T1")
        accepted.append(record)

    publisher = SettingsAuditPublisher(
        fail_first,
        acceptance_ledger=store,
    )
    publisher.publish_committed(snapshots[0])
    for snapshot in snapshots[1:]:
        publisher.publish_committed(snapshot)
    publisher.publish_committed(snapshots[0])
    publisher.publish_committed(snapshots[1])

    assert [len(record.raw_text.encode("utf-8")) for record in accepted] == [
        16 * 1024,
        16 * 1024,
        16 * 1024,
        16 * 1024,
        0,
        16 * 1024,
    ]
    assert accepted[4].audit_payload_capped is True
    assert accepted[5] == accepted[0]
    assert store.audit_delivery_decision_count() == 5
    assert publisher.accounting_diagnostics.volatile_entries == 0
    assert publisher.accounting_diagnostics.volatile_payload_bytes == 0


@pytest.mark.asyncio
async def test_concurrent_novel_deliveries_serialize_at_aggregate_boundary(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    snapshots = _large_lifecycle_snapshots(store, deterministic_renderer)
    delivered: list[tuple[SettingsAuditRecord, int]] = []
    loop_thread = threading.get_ident()
    publisher = SettingsAuditPublisher(
        lambda record: delivered.append((record, threading.get_ident())),
        acceptance_ledger=store,
    )
    for snapshot in snapshots[:3]:
        await publisher.publish_committed_async(snapshot)

    await asyncio.gather(
        publisher.publish_committed_async(snapshots[3]),
        publisher.publish_committed_async(snapshots[4]),
    )

    boundary = [record for record, _thread_id in delivered[-2:]]
    assert sorted(len(record.raw_text.encode("utf-8")) for record in boundary) == [
        0,
        16 * 1024,
    ]
    assert sum(record.audit_payload_capped for record in boundary) == 1
    assert {thread_id for _record, thread_id in delivered} == {loop_thread}
    assert store.audit_delivery_decision_count() == 5


@pytest.mark.asyncio
async def test_concurrent_failed_sink_releases_budget_before_later_proposal(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = _large_lifecycle_snapshots(store, deterministic_renderer)
    fourth_id = snapshots[3].transition.transition_id
    fifth_id = snapshots[4].transition.transition_id
    delivered: list[SettingsAuditRecord] = []
    fourth_sink_entered = threading.Event()
    release_fourth_sink = threading.Event()
    fifth_proposal_entered = threading.Event()
    fifth_entered_before_release: list[bool] = []
    original_proposal = store.propose_audit_delivery

    def observed_proposal(**kwargs):
        if kwargs["transition_id"] == fifth_id:
            fifth_proposal_entered.set()
        return original_proposal(**kwargs)

    def blocked_failing_sink(record: SettingsAuditRecord) -> None:
        if record.transition_id == fourth_id:
            fourth_sink_entered.set()
            assert release_fourth_sink.wait(timeout=1)
            raise RuntimeError("fourth delivery rejected")
        delivered.append(record)

    monkeypatch.setattr(store, "propose_audit_delivery", observed_proposal)
    publisher = SettingsAuditPublisher(
        blocked_failing_sink,
        acceptance_ledger=store,
    )
    for snapshot in snapshots[:3]:
        await publisher.publish_committed_async(snapshot)

    fourth = asyncio.create_task(
        asyncio.to_thread(publisher.publish_committed, snapshots[3])
    )
    assert await asyncio.to_thread(fourth_sink_entered.wait, 0.5)
    fifth = asyncio.create_task(
        publisher.publish_committed_async(snapshots[4])
    )

    def release_after_observing_fifth() -> None:
        fifth_entered_before_release.append(
            fifth_proposal_entered.wait(timeout=0.1)
        )
        release_fourth_sink.set()

    observer = threading.Thread(target=release_after_observing_fifth)
    observer.start()
    await asyncio.gather(fourth, fifth)
    await asyncio.to_thread(observer.join, 1)

    assert not observer.is_alive()
    assert fifth_entered_before_release == [False]
    assert [record.transition_id for record in delivered] == [
        snapshots[0].transition.transition_id,
        snapshots[1].transition.transition_id,
        snapshots[2].transition.transition_id,
        fifth_id,
    ]
    assert len(delivered[-1].raw_text.encode("utf-8")) == 16 * 1024
    assert delivered[-1].audit_payload_capped is False
    assert store.audit_delivery_decision_count() == 4


@pytest.mark.asyncio
async def test_async_lock_waiter_cannot_starve_ledger_default_executor(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = _large_lifecycle_snapshots(store, deterministic_renderer)
    loop = asyncio.get_running_loop()
    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    contender_blocked = asyncio.Event()
    proposal_entered = asyncio.Event()
    first_acquisition = True
    real_lock = threading.Semaphore(1)

    class ObservedLock:
        """Expose deterministic acquisition progress for the starvation test."""

        def acquire(self) -> bool:
            if not real_lock.acquire(  # pylint: disable=consider-using-with
                blocking=False
            ):
                loop.call_soon_threadsafe(contender_blocked.set)
                real_lock.acquire()  # pylint: disable=consider-using-with
            return True

        def release(self) -> None:
            real_lock.release()

    observed_lock = ObservedLock()
    # pylint: disable-next=protected-access
    original_acquire = settings_audit_module._acquire_audit_lifecycle_lock
    original_proposal = store.propose_audit_delivery

    async def pause_first_holder(lock) -> None:
        nonlocal first_acquisition
        await original_acquire(lock)
        if first_acquisition:
            first_acquisition = False
            holder_acquired.set()
            await release_holder.wait()

    def observed_proposal(**kwargs):
        loop.call_soon_threadsafe(proposal_entered.set)
        return original_proposal(**kwargs)

    monkeypatch.setattr(
        settings_audit_module,
        "_audit_lifecycle_lock",
        lambda _audit_id: observed_lock,
    )
    monkeypatch.setattr(
        settings_audit_module,
        "_acquire_audit_lifecycle_lock",
        pause_first_holder,
    )
    monkeypatch.setattr(store, "propose_audit_delivery", observed_proposal)
    publisher = SettingsAuditPublisher(
        lambda _record: None,
        acceptance_ledger=store,
    )
    constrained_executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(constrained_executor)
    holder = asyncio.create_task(
        publisher.publish_committed_async(snapshots[0])
    )
    await holder_acquired.wait()
    contender = asyncio.create_task(
        publisher.publish_committed_async(snapshots[1])
    )
    await contender_blocked.wait()
    release_holder.set()

    ledger_progressed = False
    try:
        await asyncio.wait_for(proposal_entered.wait(), timeout=0.1)
        ledger_progressed = True
    except TimeoutError:
        real_lock.release()
    finally:
        results = await asyncio.gather(
            holder,
            contender,
            return_exceptions=True,
        )
        constrained_executor.shutdown(wait=True)

    assert ledger_progressed is True
    assert results == [None, None]


@pytest.mark.asyncio
async def test_cancelled_async_lock_acquisition_releases_acquired_stripe(
) -> None:
    loop = asyncio.get_running_loop()
    waiter_entered = asyncio.Event()
    real_lock = threading.Lock()
    real_lock.acquire()  # pylint: disable=consider-using-with

    class ObservedLock:
        """Expose deterministic acquisition progress for cancellation."""

        def acquire(self, blocking: bool = True) -> bool:
            loop.call_soon_threadsafe(waiter_entered.set)
            return real_lock.acquire(blocking=blocking)

        def release(self) -> None:
            real_lock.release()

    observed_lock = ObservedLock()
    acquisition = asyncio.create_task(
        settings_audit_module._acquire_audit_lifecycle_lock(  # pylint: disable=protected-access
            observed_lock  # type: ignore[arg-type]
        )
    )
    await waiter_entered.wait()

    acquisition.cancel()
    cancellation_turn = asyncio.Event()
    loop.call_soon(cancellation_turn.set)
    await cancellation_turn.wait()
    assert not acquisition.done()
    real_lock.release()

    with pytest.raises(asyncio.CancelledError):
        await acquisition
    assert real_lock.acquire(  # pylint: disable=consider-using-with
        blocking=False
    ) is True
    real_lock.release()


@pytest.mark.asyncio
async def test_async_audit_ledger_io_preserves_event_loop_heartbeat(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _large_lifecycle_snapshots(store, deterministic_renderer)[0]
    delivered_on: list[int] = []
    publisher = SettingsAuditPublisher(
        lambda _record: delivered_on.append(threading.get_ident()),
        acceptance_ledger=store,
    )
    entered = threading.Event()
    release = threading.Event()
    heartbeat = threading.Event()
    heartbeat_observed: list[bool] = []
    original = store.propose_audit_delivery

    def blocked_proposal(**kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original(**kwargs)

    monkeypatch.setattr(store, "propose_audit_delivery", blocked_proposal)
    loop = asyncio.get_running_loop()

    def release_after_heartbeat() -> None:
        assert entered.wait(timeout=1)
        loop.call_soon_threadsafe(heartbeat.set)
        heartbeat_observed.append(heartbeat.wait(timeout=0.25))
        release.set()

    releaser = threading.Thread(target=release_after_heartbeat)
    releaser.start()
    await publisher.publish_committed_async(snapshot)
    await asyncio.to_thread(releaser.join, 1)

    assert not releaser.is_alive()
    assert heartbeat_observed == [True]
    assert delivered_on == [threading.get_ident()]


@pytest.mark.asyncio
async def test_sync_durable_publish_is_rejected_on_running_loop(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    snapshot = _large_lifecycle_snapshots(store, deterministic_renderer)[0]
    publisher = SettingsAuditPublisher(
        lambda _record: None,
        acceptance_ledger=store,
    )

    with pytest.raises(RuntimeError, match="publish_committed_async"):
        publisher.publish_committed(snapshot)


def test_close_reopen_replays_identical_capped_terminal_without_new_row(
    tmp_path: Path,
    control_policy: ControlPolicy,
    deterministic_renderer: AttemptRenderer,
) -> None:
    path = tmp_path / "audit-replay.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    snapshots = _large_confirmed_lifecycle_snapshots(
        store, deterministic_renderer
    )
    first_delivery: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(
        first_delivery.append,
        acceptance_ledger=store,
    )
    for snapshot in snapshots:
        publisher.publish_committed(snapshot)
    terminal = first_delivery[-1]
    row_count = store.audit_delivery_decision_count()
    store.close()

    reopened = TwinCommandStore(path, policy=control_policy)
    reopened.open(now_ms=2)
    replayed: list[SettingsAuditRecord] = []
    replay_publisher = SettingsAuditPublisher(
        replayed.append,
        acceptance_ledger=reopened,
    )
    replay_publisher.publish_committed(snapshots[-1])

    assert terminal.raw_text == ""
    assert terminal.audit_payload_capped is True
    assert replayed == [terminal]
    assert reopened.audit_delivery_decision_count() == row_count
    assert replay_publisher.accounting_diagnostics.volatile_entries == 0
    reopened.close()


def test_sink_success_before_accept_replays_pending_identity_at_least_once(
    tmp_path: Path,
    control_policy: ControlPolicy,
    deterministic_renderer: AttemptRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "pending-replay.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    terminal = _large_confirmed_lifecycle_snapshots(
        store, deterministic_renderer
    )[-1]
    first_delivery: list[SettingsAuditRecord] = []

    def fail_acceptance(**_kwargs):
        raise RuntimeError("simulated crash before acceptance finalization")

    monkeypatch.setattr(store, "accept_audit_delivery", fail_acceptance)
    SettingsAuditPublisher(
        first_delivery.append,
        acceptance_ledger=store,
    ).publish_committed(terminal)
    decision = store.read_audit_delivery_decision(
        audit_id=terminal.command.audit_id,
        transition_id=terminal.transition.transition_id,
    )
    assert decision.state.value == "pending"
    store.close()

    reopened = TwinCommandStore(path, policy=control_policy)
    reopened.open(now_ms=2)
    replayed: list[SettingsAuditRecord] = []
    SettingsAuditPublisher(
        replayed.append,
        acceptance_ledger=reopened,
    ).publish_committed(terminal)
    accepted = reopened.read_audit_delivery_decision(
        audit_id=terminal.command.audit_id,
        transition_id=terminal.transition.transition_id,
    )

    assert replayed == first_delivery
    assert reopened.audit_delivery_decision_count() == 1
    assert accepted.state.value == "accepted"
    reopened.close()


def test_thousands_of_completed_audits_retain_no_volatile_payload_state(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    del deterministic_renderer
    _observe(store)
    publisher = SettingsAuditPublisher(
        lambda _record: None,
        acceptance_ledger=store,
    )
    last_command = None
    for offset in range(1001):
        result = _enqueue(store, received_at_ms=100 + offset)
        if last_command is not None:
            terminal = next(
                snapshot
                for snapshot in result.snapshots
                if snapshot.command.command_id == last_command.command_id
            )
            publisher.publish_committed(terminal)
        last_command = result.command
    assert last_command is not None
    expired = store.sweep_device_deadlines(
        device_id="123",
        now_ms=last_command.pending_expires_at_ms + 1,
    )
    publisher.publish_committed(expired.snapshots[0])

    assert store.audit_delivery_decision_count() == 1001
    assert publisher.accounting_diagnostics.volatile_entries == 0
    assert publisher.accounting_diagnostics.volatile_payload_bytes == 0
    assert not hasattr(publisher, "_accepted_records")
    assert not hasattr(publisher, "_accepted_raw_bytes")


def _validated_setting(
    *,
    device_id: str = "123",
    message_id: int = 700,
    id_set: int = 800,
    item_name: str = "MODE",
    value: str = "2",
    received_at_ms: int = 100,
) -> tuple[ValidatedFrame, FrameMetadata]:
    inner = (
        f"<ID>{message_id}</ID><ID_Device>{device_id}</ID_Device>"
        f"<ID_Set>{id_set}</ID_Set><TblName>tbl_box_prms</TblName>"
        f"<TblItem>{item_name}</TblItem><NewValue>{value}</NewValue>"
        "<Reason>Setting</Reason>"
    ).encode("ascii")
    crc = crc16_modbus(inner)
    raw = b"<Frame>" + inner + f"<CRC>{crc:05d}</CRC>".encode("ascii") + b"</Frame>\r\n"
    frame = ValidatedFrame(raw, received_at_ms, inner, crc, crc)
    metadata = FrameMetadata(
        result=None,
        table_name="tbl_box_prms",
        device_id=device_id,
        reason="Setting",
        todo=None,
        rdt=None,
        message_id=message_id,
        id_set=id_set,
        item_name=item_name,
        new_value=value,
        event_type=None,
        content=None,
    )
    return frame, metadata


def test_cloud_setting_observer_is_session_local_and_never_mutates_store(
    store: TwinCommandStore,
) -> None:
    _observe(store)
    before = store.status_snapshot()
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    frame, metadata = _validated_setting()

    observation = observer.setting_forwarded(
        session_id="session-a",
        frame=frame,
        metadata=metadata,
        observed_at_ms=100,
    )
    response = observer.box_response_forwarded(
        session_id="session-a",
        response=_response(b"ack"),
        observed_at_ms=110,
    )
    assert response is not None

    assert observation.wire_id == metadata.message_id
    assert observation.wire_id_set == metadata.id_set
    assert observation.raw_frame == frame.raw
    assert response.cloud_observation_id == observation.cloud_observation_id
    assert response.step == "box_ack_forwarded"
    assert store.status_snapshot() == before


def test_cloud_observation_identity_hashes_uuid_session_and_exact_bytes() -> None:
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    frame, metadata = _validated_setting()

    observation = observer.setting_forwarded(
        session_id="uuid-session",
        frame=frame,
        metadata=metadata,
        observed_at_ms=100,
    )

    expected = hashlib.sha256(b"uuid-session\0" + frame.raw).hexdigest()
    assert observation.cloud_observation_id == expected
    assert records == [observation]


def test_cloud_ack_sequence_is_fifo_with_multiple_settings() -> None:
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    first_frame, first_metadata = _validated_setting(message_id=1, id_set=11)
    second_frame, second_metadata = _validated_setting(
        message_id=2, id_set=12, item_name="BAT_AC", value="50"
    )
    first = observer.setting_forwarded(
        session_id="s",
        frame=first_frame,
        metadata=first_metadata,
        observed_at_ms=100,
    )
    second = observer.setting_forwarded(
        session_id="s",
        frame=second_frame,
        metadata=second_metadata,
        observed_at_ms=101,
    )

    first_ack = observer.box_response_forwarded(
        session_id="s", response=_response(b"a"), observed_at_ms=110
    )
    second_ack = observer.box_response_forwarded(
        session_id="s", response=_response(b"b"), observed_at_ms=111
    )
    assert first_ack is not None
    assert second_ack is not None

    assert [first_ack.cloud_observation_id, second_ack.cloud_observation_id] == [
        first.cloud_observation_id,
        second.cloud_observation_id,
    ]


def test_cloud_ack_then_event_preserves_identity_and_exact_event_bytes() -> None:
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    frame, metadata = _validated_setting()
    observation = observer.setting_forwarded(
        session_id="s", frame=frame, metadata=metadata, observed_at_ms=100
    )
    acknowledged = observer.box_response_forwarded(
        session_id="s", response=_response(b"ack"), observed_at_ms=110
    )
    assert acknowledged is not None

    event_record = observer.setting_event_observed(
        session_id="s",
        event=_event(),
        raw_frame=b"exact-cloud-event",
        observed_at_ms=120,
    )
    assert event_record is not None

    assert event_record.cloud_observation_id == observation.cloud_observation_id
    assert event_record.raw_frame == b"exact-cloud-event"
    assert event_record.step == "event_observed"


def test_cloud_nack_is_terminal_and_cannot_match_later_event() -> None:
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    frame, metadata = _validated_setting()
    observer.setting_forwarded(
        session_id="s", frame=frame, metadata=metadata, observed_at_ms=100
    )
    rejected = observer.box_response_forwarded(
        session_id="s",
        response=_response(b"nack", "NACK"),
        observed_at_ms=110,
    )

    event_record = observer.setting_event_observed(
        session_id="s",
        event=_event(),
        raw_frame=b"event-after-nack",
        observed_at_ms=120,
    )

    assert rejected is not None
    assert rejected.step == "box_nack_forwarded"
    assert event_record is None
    assert observer.close_session(session_id="s", observed_at_ms=130) == ()


def test_cloud_events_match_acked_targets_out_of_response_order() -> None:
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    first_frame, first_metadata = _validated_setting(message_id=1, id_set=11)
    second_frame, second_metadata = _validated_setting(
        message_id=2, id_set=12, item_name="BAT_AC", value="50"
    )
    first = observer.setting_forwarded(
        session_id="s", frame=first_frame, metadata=first_metadata, observed_at_ms=100
    )
    second = observer.setting_forwarded(
        session_id="s", frame=second_frame, metadata=second_metadata, observed_at_ms=101
    )
    observer.box_response_forwarded(
        session_id="s", response=_response(b"first-ack"), observed_at_ms=110
    )
    observer.box_response_forwarded(
        session_id="s", response=_response(b"second-ack"), observed_at_ms=111
    )

    second_event = observer.setting_event_observed(
        session_id="s",
        event=_event(item_name="BAT_AC", new_value="50"),
        raw_frame=b"second-event",
        observed_at_ms=120,
    )
    first_event = observer.setting_event_observed(
        session_id="s",
        event=_event(),
        raw_frame=b"first-event",
        observed_at_ms=121,
    )

    assert second_event is not None
    assert first_event is not None
    assert second_event.cloud_observation_id == second.cloud_observation_id
    assert first_event.cloud_observation_id == first.cloud_observation_id
    assert observer.close_session(session_id="s", observed_at_ms=130) == ()


@pytest.mark.parametrize("reason", ["session_closed", "session_timeout"])
def test_cloud_session_close_clears_pending_in_order(reason: str) -> None:
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    for message_id in (1, 2):
        frame, metadata = _validated_setting(
            message_id=message_id, id_set=message_id + 10
        )
        observer.setting_forwarded(
            session_id="s",
            frame=frame,
            metadata=metadata,
            observed_at_ms=100 + message_id,
        )
    acknowledged = observer.box_response_forwarded(
        session_id="s", response=_response(b"ack"), observed_at_ms=110
    )
    assert acknowledged is not None

    closed = observer.close_session(
        session_id="s", reason=reason, observed_at_ms=200
    )

    assert [record.wire_id for record in closed] == [1, 2]
    assert {record.step for record in closed} == {reason}
    assert observer.close_session(
        session_id="s", reason=reason, observed_at_ms=201
    ) == ()
