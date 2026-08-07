"""Cloud-first ONLINE semantic routing tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from protocol.frame import (
    AssembledFrame,
    FrameDirection,
    FrameStreamAssembler,
    build_frame,
)
from proxy.dialog import SessionRoute, SettingDialog
from proxy.server import (
    ProxyConnectionContext,
    ProxyServer,
    StreamFrameEvent,
    StreamTimeoutEvent,
    StreamTimeoutKind,
)
from proxy.writer import SerializedBoxWriter
from telemetry.settings_audit import CloudSettingAuditObserver
from twin.delivery import TwinCoordinator
from twin.state import (
    ActiveLocalAttempt,
    AttemptWriteOutcome,
    CommandState,
    ControlIngress,
    ConfirmedSetting,
)
from twin.store import TwinCommandStore


class RecordingWriter:
    """In-memory StreamWriter boundary with exact invocation bytes."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, raw: bytes) -> None:
        self.writes.append(bytes(raw))

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _frame(
    *,
    result: str | None = None,
    device_id: str | None = None,
    table: str | None = None,
    item: str | None = None,
    value: str | None = None,
    reason: str | None = None,
    message_id: int | None = None,
    id_set: int | None = None,
    extra: str = "",
) -> bytes:
    tags = []
    if result is not None:
        tags.append(f"<Result>{result}</Result>")
    if message_id is not None:
        tags.append(f"<ID>{message_id}</ID>")
    if device_id is not None:
        tags.append(f"<ID_Device>{device_id}</ID_Device>")
    if id_set is not None:
        tags.append(f"<ID_Set>{id_set}</ID_Set>")
    if value is not None:
        tags.append(f"<NewValue>{value}</NewValue>")
    if reason is not None:
        tags.append(f"<Reason>{reason}</Reason>")
    if table is not None:
        tags.append(f"<TblName>{table}</TblName>")
    if item is not None:
        tags.append(f"<TblItem>{item}</TblItem>")
    tags.append(extra)
    return build_frame("".join(tags)).encode("utf-8")


def _enqueue(store: TwinCommandStore, *, value: str = "2") -> None:
    store.observe_device(
        device_id="123",
        observed_at_ms=90,
        observed_wire_id=14_000_000,
        observed_wire_id_set=1_786_000_000,
    )
    ingress = ControlIngress(
        "ingress-1",
        100,
        "oig/123/control/set",
        "123",
        False,
        '{"value":"2"}',
    )
    store.enqueue_command(
        ingress,
        device_id="123",
        table_name="tbl_box_prms",
        item_name="MODE",
        value_text=value,
    )


def _event(*, value: str, id_set: int = 1_786_000_010) -> bytes:
    return _frame(
        device_id="123",
        id_set=id_set,
        table="tbl_events",
        extra=(
            "<DT>06.08.2026 10:12:01</DT><Type>Setting</Type>"
            "<Content>Remotely : tbl_box_prms / MODE: "
            f"[1]-&gt;[{value}]</Content>"
        ),
    )


def _enqueue_second(store: TwinCommandStore) -> None:
    store.enqueue_command(
        ControlIngress(
            "ingress-2",
            101,
            "oig/123/control/set",
            "123",
            False,
            '{"value":"1"}',
        ),
        device_id="123",
        table_name="tbl_box_prms",
        item_name="BAT_AC",
        value_text="1",
    )


@dataclass(slots=True)
class OnlineHarness:
    server: ProxyServer
    context: ProxyConnectionContext
    store: TwinCommandStore
    raw_box: RecordingWriter
    raw_cloud: RecordingWriter
    valid_observations: list[tuple[str, int | None, int | None]]
    confirmations: list[ConfirmedSetting]
    times: dict[str, float]
    next_received_at_ms: int = 200

    async def box(self, raw: bytes) -> None:
        self.next_received_at_ms += 1
        await self.server.route_stream_event(
            self.context,
            StreamFrameEvent(
                FrameDirection.BOX_TO_PROXY,
                AssembledFrame(raw, self.next_received_at_ms),
            ),
        )

    async def cloud(self, raw: bytes) -> None:
        self.next_received_at_ms += 1
        await self.server.route_stream_event(
            self.context,
            StreamFrameEvent(
                FrameDirection.CLOUD_TO_PROXY,
                AssembledFrame(raw, self.next_received_at_ms),
            ),
        )

    async def open_cycle(self) -> bytes:
        poll = _frame(
            result="IsNewSet",
            device_id="123",
            message_id=14_000_000,
            id_set=1_786_000_000,
        )
        await self.box(poll)
        return poll


@pytest.fixture
def online_harness(
    store: TwinCommandStore,
    make_config: Any,
) -> OnlineHarness:
    _enqueue(store)
    coordinator = TwinCoordinator(
        store,
        clock_ms=lambda: 202,
    )
    raw_box = RecordingWriter()
    raw_cloud = RecordingWriter()
    observations: list[tuple[str, int | None, int | None]] = []
    confirmations: list[ConfirmedSetting] = []
    times = {"wall_ms": 202.0, "monotonic": 10.0}

    async def valid_device(
        device_id: str, message_id: int | None, id_set: int | None
    ) -> bool:
        observations.append((device_id, message_id, id_set))
        return True

    async def confirmed(confirmation: ConfirmedSetting) -> None:
        confirmations.append(confirmation)

    server = ProxyServer(
        make_config(),
        twin_coordinator=coordinator,
        on_valid_device=valid_device,
        on_committed_confirmation=confirmed,
        clock_ms=lambda: int(times["wall_ms"]),
        monotonic=lambda: times["monotonic"],
    )
    dialog = SettingDialog("session-1", SessionRoute.ONLINE)
    context = ProxyConnectionContext(
        session_id="session-1",
        route=SessionRoute.ONLINE,
        dialog=dialog,
        box_assembler=FrameStreamAssembler(),
        cloud_assembler=FrameStreamAssembler(),
        box_writer=SerializedBoxWriter(raw_box, clock_ms=lambda: 202),
        cloud_audit=CloudSettingAuditObserver(None),
        semantic_events=asyncio.Queue(maxsize=1),
        cloud_writer=raw_cloud,
        close_requested=asyncio.Event(),
    )
    return OnlineHarness(
        server,
        context,
        store,
        raw_box,
        raw_cloud,
        observations,
        confirmations,
        times,
    )


@pytest.mark.asyncio
async def test_poll_reaches_cloud_and_queue_stays_pending_until_terminal_end(
    online_harness: OnlineHarness,
) -> None:
    poll = await online_harness.open_cycle()

    assert online_harness.raw_cloud.writes == [poll]
    assert online_harness.raw_box.writes == []
    assert online_harness.store.single_nonterminal("123").state is CommandState.PENDING
    assert online_harness.valid_observations == [
        ("123", 14_000_000, 1_786_000_000)
    ]


@pytest.mark.asyncio
async def test_cloud_setting_and_box_ack_round_trip_before_local_batch(
    online_harness: OnlineHarness,
) -> None:
    poll = await online_harness.open_cycle()
    cloud_setting = _frame(
        result="Setting",
        device_id="123",
        table="tbl_box_prms",
        item="MODE",
        value="1",
        message_id=14_000_001,
        id_set=1_786_000_001,
    )
    ack = _frame(result="ACK", reason="Setting")

    await online_harness.cloud(cloud_setting)
    await online_harness.box(ack)

    assert online_harness.raw_box.writes == [cloud_setting]
    assert online_harness.raw_cloud.writes == [poll, ack]
    assert online_harness.store.single_nonterminal("123").state is CommandState.PENDING
    assert online_harness.context.dialog.expectation_count == 1


@pytest.mark.asyncio
async def test_cloud_frame_held_before_setting_ack_is_rerouted_after_ack(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    first_setting = _frame(
        result="Setting",
        device_id="123",
        table="tbl_box_prms",
        item="MODE",
        value="1",
    )
    second_setting = _frame(
        result="Setting",
        device_id="123",
        table="tbl_box_prms",
        item="BAT_AC",
        value="0",
    )

    await online_harness.cloud(first_setting)
    await online_harness.cloud(second_setting)

    assert online_harness.raw_box.writes == [first_setting]

    ack = _frame(result="ACK", reason="Setting")
    await online_harness.box(ack)

    assert online_harness.raw_cloud.writes[-1] == ack
    assert online_harness.raw_box.writes == [first_setting, second_setting]


@pytest.mark.asyncio
async def test_box_request_after_cloud_setting_ack_waits_for_cycle_completion(
    online_harness: OnlineHarness,
) -> None:
    poll = await online_harness.open_cycle()
    cloud_setting = _frame(
        result="Setting",
        device_id="123",
        table="tbl_box_prms",
        item="MODE",
        value="1",
        message_id=14_000_001,
        id_set=1_786_000_001,
    )
    cloud_ack = _frame(result="ACK", reason="Setting")
    later_box = _frame(result="IsNewWeather", device_id="123")
    raw_end = _frame(result="END", extra="<Marker>cloud-batch</Marker>")

    await online_harness.cloud(cloud_setting)
    await online_harness.box(cloud_ack)
    await online_harness.box(later_box)

    assert online_harness.raw_cloud.writes == [poll, cloud_ack]

    await online_harness.cloud(raw_end)
    local_setting = online_harness.raw_box.writes[-1]
    await online_harness.box(_frame(result="ACK", reason="Setting"))

    assert online_harness.raw_box.writes[-2:] == [local_setting, raw_end]
    assert online_harness.raw_cloud.writes == [poll, cloud_ack, later_box]


@pytest.mark.asyncio
async def test_held_isnewset_is_rerouted_with_a_new_expectation(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    cloud_setting = _frame(
        result="Setting",
        device_id="123",
        table="tbl_box_prms",
        item="MODE",
        value="1",
    )
    await online_harness.cloud(cloud_setting)
    await online_harness.box(_frame(result="ACK", reason="Setting"))
    later_poll = _frame(
        result="IsNewSet",
        device_id="123",
        message_id=14_000_010,
        id_set=1_786_000_010,
    )
    await online_harness.box(later_poll)

    first_end = _frame(result="END", extra="<Marker>first-cycle</Marker>")
    await online_harness.cloud(first_end)
    await online_harness.box(_frame(result="ACK", reason="Setting"))

    assert online_harness.raw_cloud.writes[-1] == later_poll
    assert online_harness.context.dialog.expectation_count == 1

    _enqueue_second(online_harness.store)
    later_end = _frame(result="END", extra="<Marker>second-cycle</Marker>")
    await online_harness.cloud(later_end)

    assert b"<TblItem>BAT_AC</TblItem>" in online_harness.raw_box.writes[-1]
    assert online_harness.raw_box.writes[-1] != later_end


@pytest.mark.asyncio
async def test_correlated_end_is_replaced_and_local_ack_returns_exact_end(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    raw_end = _frame(result="END", extra="<Marker>exact</Marker>")

    await online_harness.cloud(raw_end)

    assert len(online_harness.raw_box.writes) == 1
    local_setting = online_harness.raw_box.writes[0]
    assert b"<Reason>Setting</Reason>" in local_setting
    assert local_setting != raw_end
    active = online_harness.context.dialog.active_attempt
    assert active is not None

    ack = _frame(result="ACK", reason="Setting")
    await online_harness.box(ack)

    assert online_harness.raw_box.writes == [local_setting, raw_end]
    assert ack not in online_harness.raw_cloud.writes
    command = online_harness.store.read_command(active.command_id)
    assert command.state is CommandState.AWAITING_EVENT
    assert online_harness.confirmations == []
    assert online_harness.context.dialog.current_expectation() is None


@pytest.mark.asyncio
async def test_no_eligible_command_forwards_exact_end(
    online_harness: OnlineHarness,
) -> None:
    pending = online_harness.store.single_nonterminal("123")
    online_harness.store.sweep_device_deadlines(
        device_id="123", now_ms=pending.pending_expires_at_ms + 1
    )
    await online_harness.open_cycle()
    raw_end = _frame(result="END", extra="<Marker>no-work</Marker>")

    await online_harness.cloud(raw_end)

    assert online_harness.raw_box.writes == [raw_end]
    assert online_harness.context.dialog.active_attempt is None
    assert online_harness.context.dialog.current_expectation() is None


@pytest.mark.asyncio
async def test_no_eligible_command_releases_held_box_request_after_end(
    online_harness: OnlineHarness,
) -> None:
    pending = online_harness.store.single_nonterminal("123")
    online_harness.store.sweep_device_deadlines(
        device_id="123", now_ms=pending.pending_expires_at_ms + 1
    )
    poll = await online_harness.open_cycle()
    cloud_setting = _frame(
        result="Setting",
        device_id="123",
        table="tbl_box_prms",
        item="MODE",
        value="1",
    )
    ack = _frame(result="ACK", reason="Setting")
    later_box = _frame(result="IsNewWeather", device_id="123")
    raw_end = _frame(result="END", extra="<Marker>no-work-held</Marker>")

    await online_harness.cloud(cloud_setting)
    await online_harness.box(ack)
    await online_harness.box(later_box)
    await online_harness.cloud(raw_end)

    assert online_harness.raw_box.writes == [cloud_setting, raw_end]
    assert online_harness.raw_cloud.writes == [poll, ack, later_box]


@pytest.mark.asyncio
async def test_invalid_cloud_crc_end_is_forwarded_without_substitution(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    valid = _frame(result="END")
    marker = valid.rfind(b"<CRC>") + len(b"<CRC>")
    corrupt = valid[:marker] + b"00000" + valid[marker + 5:]

    await online_harness.cloud(corrupt)

    assert online_harness.raw_box.writes == [corrupt]
    assert online_harness.store.single_nonterminal("123").state is CommandState.PENDING
    assert online_harness.context.dialog.active_attempt is None
    assert online_harness.context.close_requested.is_set()


@pytest.mark.asyncio
async def test_cloud_timeout_is_absolute_and_rechecks_cycle_identity(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    expectation = online_harness.context.dialog.current_expectation()
    assert expectation is not None
    deadline = expectation.deadline_monotonic
    assert deadline == 11.0

    await online_harness.server.route_stream_event(
        online_harness.context,
        StreamTimeoutEvent(
            StreamTimeoutKind.CLOUD_RESPONSE,
            expectation_sequence=expectation.sequence + 1,
        ),
    )
    assert not online_harness.context.close_requested.is_set()

    online_harness.times["monotonic"] = 10.999
    await online_harness.server.route_stream_event(
        online_harness.context,
        StreamTimeoutEvent(
            StreamTimeoutKind.CLOUD_RESPONSE,
            expectation_sequence=expectation.sequence,
        ),
    )
    assert not online_harness.context.close_requested.is_set()

    online_harness.times["monotonic"] = deadline
    await online_harness.server.route_stream_event(
        online_harness.context,
        StreamTimeoutEvent(
            StreamTimeoutKind.CLOUD_RESPONSE,
            expectation_sequence=expectation.sequence,
        ),
    )
    assert online_harness.context.close_requested.is_set()


@pytest.mark.asyncio
async def test_cloud_setting_does_not_extend_cloud_deadline(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    expectation = online_harness.context.dialog.current_expectation()
    assert expectation is not None
    deadline = expectation.deadline_monotonic
    timer = online_harness.context.cloud_timer

    online_harness.times["monotonic"] = 10.5
    await online_harness.cloud(
        _frame(
            result="Setting",
            device_id="123",
            table="tbl_box_prms",
            item="MODE",
            value="1",
        )
    )

    assert expectation.deadline_monotonic == deadline
    assert online_harness.context.cloud_timer is timer


@pytest.mark.asyncio
async def test_ack_timeout_rechecks_full_attempt_identity_before_abort(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    await online_harness.cloud(_frame(result="END"))
    active = online_harness.context.dialog.active_attempt
    assert active is not None

    online_harness.times["wall_ms"] = active.ack_deadline_ms
    await online_harness.server.route_stream_event(
        online_harness.context,
        StreamTimeoutEvent(
            StreamTimeoutKind.LOCAL_ACK,
            command_id=active.command_id,
            attempt_number=active.attempt_number + 1,
            session_id=active.session_id,
            deadline_ms=active.ack_deadline_ms,
        ),
    )
    assert online_harness.context.dialog.active_attempt == active

    await online_harness.server.route_stream_event(
        online_harness.context,
        StreamTimeoutEvent(
            StreamTimeoutKind.LOCAL_ACK,
            command_id=active.command_id,
            attempt_number=active.attempt_number,
            session_id=active.session_id,
            deadline_ms=active.ack_deadline_ms,
        ),
    )
    command = online_harness.store.read_command(active.command_id)
    assert command.state in {CommandState.RETRY_PENDING, CommandState.FAILED}
    assert online_harness.context.close_requested.is_set()


@pytest.mark.asyncio
async def test_valid_later_frames_are_held_until_local_final_end_drains(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    raw_end = _frame(result="END", extra="<Marker>owner</Marker>")
    await online_harness.cloud(raw_end)
    local_setting = online_harness.raw_box.writes[-1]

    later_box = _frame(result="IsNewWeather", device_id="123")
    later_cloud = _frame(result="ACK", reason="Other")
    await online_harness.box(later_box)
    await online_harness.cloud(later_cloud)

    assert later_box not in online_harness.raw_cloud.writes
    assert later_cloud not in online_harness.raw_box.writes

    await online_harness.box(
        _frame(
            result="ACK",
            reason="Setting",
            extra="<Rdt>06.08.2026 10:12:02</Rdt>",
        )
    )

    assert online_harness.raw_box.writes == [
        local_setting,
        raw_end,
        later_cloud,
    ]
    assert online_harness.raw_cloud.writes[-1] == later_box


@pytest.mark.asyncio
async def test_invalid_cloud_frame_aborts_active_owner_before_raw_forward(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    await online_harness.cloud(_frame(result="END"))
    active = online_harness.context.dialog.active_attempt
    assert active is not None
    valid = _frame(result="ACK", reason="Other")
    marker = valid.rfind(b"<CRC>") + len(b"<CRC>")
    corrupt = valid[:marker] + b"00000" + valid[marker + 5:]

    await online_harness.cloud(corrupt)

    assert online_harness.raw_box.writes[-1] == corrupt
    command = online_harness.store.read_command(active.command_id)
    assert command.state in {CommandState.RETRY_PENDING, CommandState.FAILED}
    assert online_harness.context.close_requested.is_set()


@pytest.mark.asyncio
async def test_local_nack_is_suppressed_and_returns_exact_deferred_end(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    raw_end = _frame(result="END", extra="<Marker>nack</Marker>")
    await online_harness.cloud(raw_end)
    active = online_harness.context.dialog.active_attempt
    assert active is not None
    nack = _frame(result="NACK", reason="WC")

    await online_harness.box(nack)

    assert nack not in online_harness.raw_cloud.writes
    assert online_harness.raw_box.writes[-1] == raw_end
    assert online_harness.store.read_command(active.command_id).state is CommandState.FAILED


@pytest.mark.asyncio
async def test_exact_direct_event_confirms_without_forwarding_local_evidence(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    raw_end = _frame(result="END", extra="<Marker>event</Marker>")
    await online_harness.cloud(raw_end)
    local_setting = online_harness.raw_box.writes[-1]
    active = online_harness.context.dialog.active_attempt
    assert active is not None
    exact_event = _event(value="2")

    await online_harness.box(exact_event)

    command = online_harness.store.read_command(active.command_id)
    assert command.state is CommandState.CONFIRMED
    assert exact_event not in online_harness.raw_cloud.writes
    assert online_harness.raw_box.writes == [local_setting]
    assert [item.command_id for item in online_harness.confirmations] == [
        active.command_id
    ]
    assert online_harness.context.close_requested.is_set()


@pytest.mark.asyncio
async def test_cross_device_ack_cannot_advance_active_local_command(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    raw_end = _frame(result="END", extra="<Marker>cross-device</Marker>")
    await online_harness.cloud(raw_end)
    active = online_harness.context.dialog.active_attempt
    assert active is not None
    local_setting = online_harness.raw_box.writes[-1]
    foreign_ack = _frame(
        result="ACK",
        reason="Setting",
        device_id="999",
    )

    await online_harness.box(foreign_ack)

    command = online_harness.store.read_command(active.command_id)
    assert command.state in {CommandState.RETRY_PENDING, CommandState.FAILED}
    assert online_harness.raw_box.writes == [local_setting]
    assert foreign_ack not in online_harness.raw_cloud.writes
    assert online_harness.context.close_requested.is_set()


@pytest.mark.asyncio
async def test_unowned_event_is_committed_then_forwarded_byte_exact(
    online_harness: OnlineHarness,
) -> None:
    event = _event(value="9")

    await online_harness.box(event)

    assert online_harness.raw_cloud.writes == [event]
    assert online_harness.store.single_nonterminal("123").state is CommandState.PENDING
    assert online_harness.confirmations == []


@pytest.mark.asyncio
async def test_cloud_nack_for_isnewset_never_claims_local_work(
    online_harness: OnlineHarness,
) -> None:
    await online_harness.open_cycle()
    cloud_nack = _frame(result="NACK", reason="Busy")

    await online_harness.cloud(cloud_nack)

    assert online_harness.raw_box.writes == [cloud_nack]
    assert online_harness.store.single_nonterminal("123").state is CommandState.PENDING
    assert online_harness.context.dialog.active_attempt is None


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ("IsNewFW", "IsNewWeather"))
async def test_nonsetting_polls_are_single_response_and_never_claim(
    online_harness: OnlineHarness,
    result: str,
) -> None:
    poll = _frame(result=result, device_id="123")
    response = _frame(result="END", extra=f"<Marker>{result}</Marker>")

    await online_harness.box(poll)
    await online_harness.cloud(response)

    assert online_harness.raw_cloud.writes == [poll]
    assert online_harness.raw_box.writes == [response]
    assert online_harness.store.single_nonterminal("123").state is CommandState.PENDING


@pytest.mark.asyncio
async def test_local_ack_atomically_writes_next_command_before_final_end(
    online_harness: OnlineHarness,
) -> None:
    _enqueue_second(online_harness.store)
    await online_harness.open_cycle()
    raw_end = _frame(result="END", extra="<Marker>batch</Marker>")
    await online_harness.cloud(raw_end)
    first = online_harness.context.dialog.active_attempt
    assert first is not None
    first_wire = online_harness.raw_box.writes[-1]

    await online_harness.box(_frame(result="ACK", reason="Setting"))

    second = online_harness.context.dialog.active_attempt
    assert second is not None
    assert second.command_id != first.command_id
    assert online_harness.raw_box.writes == [first_wire, second.wire_frame]
    assert raw_end not in online_harness.raw_box.writes

    await online_harness.box(
        _frame(
            result="ACK",
            reason="Setting",
            extra="<Rdt>06.08.2026 10:12:03</Rdt>",
        )
    )

    assert online_harness.raw_box.writes[-1] == raw_end
    assert online_harness.context.dialog.active_attempt is None


@pytest.mark.asyncio
async def test_online_local_setting_capture_retains_exact_attempt_identity(
    make_config: Any,
) -> None:
    capture = MagicMock()
    server = ProxyServer(make_config(), frame_capture=capture, clock_ms=lambda: 7)
    raw_box = RecordingWriter()
    context = server._create_online_context(
        session_id="capture-session",
        box_writer=raw_box,
        cloud_writer=RecordingWriter(),
        conn_id=9,
        peer="box:1",
    )
    wire = _frame(result="Setting", extra="<Marker>capture</Marker>")
    attempt = ActiveLocalAttempt(
        "command-1",
        "audit-1",
        "123",
        2,
        "capture-session",
        100,
        wire,
        AttemptWriteOutcome.PREPARED,
    )
    await context.box_writer.acquire_dialogue("capture-session")

    async def before_write() -> None:
        return None

    await context.box_writer.write_attempt(attempt, before_write=before_write)

    kwargs = capture.capture.call_args.kwargs
    assert kwargs["raw_bytes"] == wire
    assert kwargs["direction"] == "proxy_to_box"
    assert kwargs["attempt_link"].command_id == "command-1"
    assert kwargs["attempt_link"].audit_id == "audit-1"
    assert kwargs["attempt_link"].attempt_number == 2
