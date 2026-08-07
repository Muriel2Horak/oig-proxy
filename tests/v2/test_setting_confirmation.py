"""Behavioral confirmation tests for durable local-setting evidence."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from twin.ack_parser import (
    SettingEvent,
    SettingResponse,
    derive_event_evidence_id,
)
from twin.state import (
    AttemptRenderer,
    CommandState,
    ControlIngress,
    ControlPolicy,
    EventDisposition,
)
from twin.store import TwinCommandStore


def _enqueue(
    store: TwinCommandStore,
    *,
    value_text: str = "2",
    received_at_ms: int = 100,
):
    try:
        store.read_device("123")
    except LookupError:
        store.observe_device(
            device_id="123",
            observed_at_ms=90,
            observed_wire_id=14_000_000,
            observed_wire_id_set=1_786_000_000,
        )
    return store.enqueue_command(
        ControlIngress(
            f"ing-{received_at_ms}-{value_text}",
            received_at_ms,
            "oig/123/control/set",
            "123",
            False,
            f'{{"value":{value_text}}}',
        ),
        device_id="123",
        table_name="tbl_box_prms",
        item_name="MODE",
        value_text=value_text,
    ).command


def _prepare(
    store: TwinCommandStore,
    renderer: AttemptRenderer,
    *,
    session: str = "a",
    now_ms: int = 200,
):
    command = _enqueue(store, received_at_ms=max(100, now_ms - 100))
    prepared = store.prepare_next_attempt(
        device_id="123",
        session_id=session,
        prepared_at_ms=now_ms,
        render=renderer,
    )
    assert prepared.attempt is not None
    return command, prepared.attempt


def _awaiting_event(
    store: TwinCommandStore,
    renderer: AttemptRenderer,
    *,
    response_rdt: str | None = "06.08.2026 10:12:00",
):
    command, attempt = _prepare(store, renderer)
    store.mark_write_started(
        command_id=command.command_id,
        attempt_number=1,
        session_id="a",
        started_at_ms=201,
    )
    store.mark_attempt_drained(
        command_id=command.command_id,
        attempt_number=1,
        session_id="a",
        drained_at_ms=202,
    )
    result = store.acknowledge_and_prepare_next(
        command_id=command.command_id,
        attempt_number=1,
        session_id="a",
        response=SettingResponse(
            result="ACK",
            reason="Setting",
            rdt_text=response_rdt,
            fingerprint=hashlib.sha256(b"ack").hexdigest(),
        ),
        received_at_ms=300,
        evidence_frame=b"ack",
        render=renderer,
    )
    assert result.accepted_command is not None
    return result.accepted_command, attempt


# pylint: disable-next=too-many-arguments
def _event(
    *,
    device_id: str = "123",
    event_id_set: int = 1_786_000_001,
    device_dt: str = "06.08.2026 10:12:01",
    table_name: str = "tbl_box_prms",
    item_name: str = "MODE",
    old_value_text: str = "1",
    new_value_text: str = "2",
) -> SettingEvent:
    content = (
        f"Remotely : {table_name} / {item_name}: "
        f"[{old_value_text}]->[{new_value_text}]"
    )
    return SettingEvent(
        evidence_id=derive_event_evidence_id(
            device_id, event_id_set, device_dt, content
        ),
        device_id=device_id,
        event_id_set=event_id_set,
        device_dt=device_dt,
        content_text=content,
        table_name=table_name,
        item_name=item_name,
        old_value_text=old_value_text,
        new_value_text=new_value_text,
    )


def _event_with(event: SettingEvent, **changes: object) -> SettingEvent:
    changed = replace(event, **changes)  # type: ignore[arg-type]
    content = (
        f"Remotely : {changed.table_name} / {changed.item_name}: "
        f"[{changed.old_value_text}]->[{changed.new_value_text}]"
    )
    changed = replace(changed, content_text=content)
    return replace(
        changed,
        evidence_id=derive_event_evidence_id(
            changed.device_id,
            changed.event_id_set,
            changed.device_dt,
            changed.content_text,
        ),
    )


def test_matcher_requires_exact_device_table_key_and_canonical_value(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    _awaiting_event(store, deterministic_renderer)
    store.observe_device(
        device_id="foreign",
        observed_at_ms=100,
        observed_wire_id=10,
        observed_wire_id_set=10,
    )
    exact = _event()
    changed_events = (
        _event_with(exact, device_id="foreign", event_id_set=2),
        _event_with(exact, table_name="tbl_boiler_prms", event_id_set=3),
        _event_with(exact, item_name="SA", event_id_set=4),
        _event_with(exact, new_value_text="3", event_id_set=5),
    )

    for changed in changed_events:
        result = store.record_event(
            evidence=changed,
            received_at_ms=500,
            evidence_frame=changed.content_text.encode("utf-8"),
        )
        assert result.disposition is EventDisposition.UNMATCHED
        assert result.confirmation is None


def test_forged_typed_event_fields_are_durably_unmatched_by_content_identity(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    command, _attempt = _awaiting_event(store, deterministic_renderer)
    exact = _event(new_value_text="3")
    forged = replace(exact, new_value_text="2")

    unmatched = store.record_event(
        evidence=forged,
        received_at_ms=500,
        evidence_frame=b"forged-event",
    )
    replay = store.record_event(
        evidence=exact,
        received_at_ms=501,
        evidence_frame=b"forged-event",
    )

    assert unmatched.disposition is EventDisposition.UNMATCHED
    assert unmatched.evidence.new_value_text == "2"
    assert replay.disposition is EventDisposition.DUPLICATE
    assert replay.confirmation is None
    assert store.read_command(command.command_id).state is CommandState.AWAITING_EVENT


def test_non_remote_event_content_is_durably_unmatched(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    command, _attempt = _awaiting_event(store, deterministic_renderer)
    exact = _event()
    content = exact.content_text.replace("Remotely", "Locally", 1)
    evidence = replace(
        exact,
        content_text=content,
        evidence_id=derive_event_evidence_id(
            exact.device_id,
            exact.event_id_set,
            exact.device_dt,
            content,
        ),
    )

    result = store.record_event(
        evidence=evidence,
        received_at_ms=500,
        evidence_frame=b"local-event",
    )

    assert result.disposition is EventDisposition.UNMATCHED
    assert result.confirmation is None
    assert store.read_command(command.command_id).state is CommandState.AWAITING_EVENT


def test_unknown_device_event_is_persisted_before_observation_and_stays_duplicate(
    store: TwinCommandStore,
) -> None:
    evidence = _event(device_id="unseen-device")

    unmatched = store.record_event(
        evidence=evidence,
        received_at_ms=100,
        evidence_frame=b"unknown-device-event",
    )
    store.observe_device(
        device_id="unseen-device",
        observed_at_ms=200,
        observed_wire_id=10,
        observed_wire_id_set=10,
    )
    replay = store.record_event(
        evidence=evidence,
        received_at_ms=201,
        evidence_frame=b"unknown-device-event",
    )

    assert unmatched.disposition is EventDisposition.UNMATCHED
    assert unmatched.evidence.device_id == "unseen-device"
    assert replay.disposition is EventDisposition.DUPLICATE
    assert replay.confirmation is None
    assert replay.evidence.duplicate_count == 1


def test_event_canonicalizes_value_without_changing_raw_receipt(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    command, _attempt = _awaiting_event(store, deterministic_renderer)
    evidence = _event(new_value_text="2.0")

    result = store.record_event(
        evidence=evidence,
        received_at_ms=500,
        evidence_frame=b"raw-event-frame",
    )

    assert result.disposition is EventDisposition.CONFIRMED
    assert result.command is not None
    assert result.command.command_id == command.command_id
    assert result.command.state is CommandState.CONFIRMED
    assert result.confirmation is not None
    assert result.confirmation.value_text == "2"
    assert result.evidence.new_value_text == "2.0"
    assert result.evidence.evidence_frame == b"raw-event-frame"


def test_awaiting_event_accepts_evidence_at_exact_deadline_with_unparseable_dates(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    command, _attempt = _awaiting_event(
        store,
        deterministic_renderer,
        response_rdt="not-a-protocol-date",
    )
    assert command.event_deadline_ms is not None
    evidence = _event(device_dt="also-not-a-protocol-date", event_id_set=22)

    result = store.record_event(
        evidence=evidence,
        received_at_ms=command.event_deadline_ms,
        evidence_frame=b"deadline-event",
    )

    assert result.disposition is EventDisposition.CONFIRMED
    assert result.command is not None
    assert result.command.command_id == command.command_id


@pytest.mark.parametrize(
    ("table_name", "item_name", "new_value_text"),
    [
        ("tbl_box_prms", "MODE", "NaN"),
        ("tbl_boiler_prms", "P_SET", "150"),
        ("tbl_box_prms", "MODE", "6"),
        ("tbl_unknown", "MODE", "2"),
    ],
)
def test_invalid_or_unauthorized_event_value_remains_unmatched(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
    table_name: str,
    item_name: str,
    new_value_text: str,
) -> None:
    _awaiting_event(store, deterministic_renderer)
    evidence = _event(
        table_name=table_name,
        item_name=item_name,
        new_value_text=new_value_text,
    )

    result = store.record_event(
        evidence=evidence,
        received_at_ms=500,
        evidence_frame=b"event",
    )

    assert result.disposition is EventDisposition.UNMATCHED
    assert result.confirmation is None
    assert store.read_event_receipt(evidence.evidence_id) == result.evidence


def test_duplicate_event_is_idempotent_after_reopen(
    tmp_path: Path,
    control_policy: ControlPolicy,
    deterministic_renderer: AttemptRenderer,
) -> None:
    path = tmp_path / "event-reopen.db"
    first = TwinCommandStore(path, policy=control_policy)
    first.open(now_ms=1)
    _awaiting_event(first, deterministic_renderer)
    evidence = _event()
    matched = first.record_event(
        evidence=evidence,
        received_at_ms=500,
        evidence_frame=b"event",
    )
    first.close()

    second = TwinCommandStore(path, policy=control_policy)
    second.open(now_ms=600)
    duplicate = second.record_event(
        evidence=evidence,
        received_at_ms=600,
        evidence_frame=b"event",
    )

    assert matched.disposition is EventDisposition.CONFIRMED
    assert duplicate.disposition is EventDisposition.DUPLICATE
    assert duplicate.confirmation is None
    assert duplicate.evidence.duplicate_count == 1
    second.close()


def test_direct_event_confirms_awaiting_ack_at_inclusive_deadline(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    command, attempt = _prepare(store, deterministic_renderer)
    evidence = _event()

    result = store.record_event(
        evidence=evidence,
        received_at_ms=attempt.ack_deadline_ms,
        evidence_frame=b"event",
    )

    assert result.disposition is EventDisposition.CONFIRMED
    assert result.prior_state is CommandState.AWAITING_ACK
    assert result.active_session_id == "a"
    assert result.command is not None
    assert result.command.command_id == command.command_id
    assert result.command.state is CommandState.CONFIRMED


def test_event_time_and_parseable_rdt_order_must_support_execution(
    store_factory,
    deterministic_renderer: AttemptRenderer,
) -> None:
    before_store = store_factory(8)
    _command, attempt = _prepare(before_store, deterministic_renderer)
    before = before_store.record_event(
        evidence=_event(event_id_set=2),
        received_at_ms=attempt.prepared_at_ms - 1,
        evidence_frame=b"early",
    )
    assert before.disposition is EventDisposition.UNMATCHED

    after_store = store_factory(8)
    command, _attempt = _awaiting_event(after_store, deterministic_renderer)
    assert command.event_deadline_ms is not None
    after = after_store.record_event(
        evidence=_event(event_id_set=3),
        received_at_ms=command.event_deadline_ms + 1,
        evidence_frame=b"late",
    )
    assert after.disposition is EventDisposition.UNMATCHED

    rdt_store = store_factory(8)
    _awaiting_event(rdt_store, deterministic_renderer)
    earlier_device_dt = rdt_store.record_event(
        evidence=_event(
            event_id_set=4,
            device_dt="06.08.2026 10:11:59",
        ),
        received_at_ms=500,
        evidence_frame=b"rdt-order",
    )
    assert earlier_device_dt.disposition is EventDisposition.UNMATCHED


def test_unmatched_receipt_never_confirms_a_future_command(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    store.observe_device(
        device_id="123",
        observed_at_ms=90,
        observed_wire_id=14_000_000,
        observed_wire_id_set=1_786_000_000,
    )
    evidence = _event()
    unmatched = store.record_event(
        evidence=evidence,
        received_at_ms=150,
        evidence_frame=b"early-event",
    )
    command, _attempt = _prepare(store, deterministic_renderer, now_ms=200)

    duplicate = store.record_event(
        evidence=evidence,
        received_at_ms=201,
        evidence_frame=b"early-event",
    )

    assert unmatched.disposition is EventDisposition.UNMATCHED
    assert duplicate.disposition is EventDisposition.DUPLICATE
    assert duplicate.confirmation is None
    assert store.read_command(command.command_id).state is CommandState.AWAITING_ACK


def test_two_distinct_evidence_ids_confirm_two_fifo_commands_once_each(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    first, first_attempt = _prepare(store, deterministic_renderer)
    first_event = _event(event_id_set=10)
    first_match = store.record_event(
        evidence=first_event,
        received_at_ms=first_attempt.prepared_at_ms,
        evidence_frame=b"first",
    )
    second, second_attempt = _prepare(
        store,
        deterministic_renderer,
        session="b",
        now_ms=300,
    )
    second_event = _event(event_id_set=11)
    second_match = store.record_event(
        evidence=second_event,
        received_at_ms=second_attempt.prepared_at_ms,
        evidence_frame=b"second",
    )

    assert first_match.confirmation is not None
    assert second_match.confirmation is not None
    assert first_match.confirmation.command_id == first.command_id
    assert second_match.confirmation.command_id == second.command_id
    assert first_event.evidence_id != second_event.evidence_id
