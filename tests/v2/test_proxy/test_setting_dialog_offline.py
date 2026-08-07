"""Exactly-once OFFLINE setting dialogue tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from protocol.frame import AssembledFrame, FrameDirection, build_frame
from proxy.server import ProxyConnectionContext, ProxyServer, StreamFrameEvent
from twin.delivery import TwinCoordinator
from twin.state import CommandState, ControlIngress, ConfirmedSetting
from twin.store import TwinCommandStore


class RecordingWriter:
    """Count every write invocation, including uncertain drains."""

    def __init__(self) -> None:
        self.invocations: list[bytes] = []
        self.drain_error: Exception | None = None
        self.closed = False

    def write(self, raw: bytes) -> None:
        self.invocations.append(bytes(raw))

    async def drain(self) -> None:
        error = self.drain_error
        self.drain_error = None
        if error is not None:
            raise error

    def fail_drain_once(self, error: Exception) -> None:
        self.drain_error = error

    def clear(self) -> None:
        self.invocations.clear()

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
    if reason is not None:
        tags.append(f"<Reason>{reason}</Reason>")
    tags.append(extra)
    return build_frame("".join(tags)).encode()


def _enqueue(
    store: TwinCommandStore,
    *,
    ingress_id: str = "ingress-1",
    item_name: str = "MODE",
    value_text: str = "2",
) -> None:
    store.enqueue_command(
        ControlIngress(
            ingress_id,
            100,
            "oig/123/control/set",
            "123",
            False,
            f'{{"value":"{value_text}"}}',
        ),
        device_id="123",
        table_name="tbl_box_prms",
        item_name=item_name,
        value_text=value_text,
    )


@dataclass(slots=True)
class OfflineHarness:
    server: ProxyServer
    context: ProxyConnectionContext
    store: TwinCommandStore
    writer: RecordingWriter
    confirmations: list[ConfirmedSetting]
    received_at_ms: int = 200

    async def box(self, raw: bytes) -> None:
        self.received_at_ms += 1
        await self.server.route_offline_stream_event(
            self.context,
            StreamFrameEvent(
                FrameDirection.BOX_TO_PROXY,
                AssembledFrame(raw, self.received_at_ms),
            ),
        )

    async def begin(self) -> Any:
        await self.box(
            _frame(
                result="IsNewSet",
                device_id="123",
                message_id=14_000_000,
                id_set=1_786_000_000,
            )
        )
        active = self.context.dialog.active_attempt
        assert active is not None
        return active


def _make_harness(
    store: TwinCommandStore,
    make_config: Any,
    *,
    with_work: bool,
    capture: Any | None = None,
) -> OfflineHarness:
    store.observe_device(
        device_id="123",
        observed_at_ms=90,
        observed_wire_id=13_999_999,
        observed_wire_id_set=1_785_999_999,
    )
    if with_work:
        _enqueue(store)
    confirmations: list[ConfirmedSetting] = []

    async def observe(
        device_id: str,
        message_id: int | None,
        id_set: int | None,
    ) -> bool:
        if message_id is None or id_set is None:
            return False
        store.observe_device(
            device_id=device_id,
            observed_at_ms=200,
            observed_wire_id=message_id,
            observed_wire_id_set=id_set,
        )
        return True

    async def confirmed(item: ConfirmedSetting) -> None:
        confirmations.append(item)

    coordinator = TwinCoordinator(store, clock_ms=lambda: 202)
    server = ProxyServer(
        make_config(),
        twin_coordinator=coordinator,
        on_valid_device=observe,
        on_committed_confirmation=confirmed,
        frame_capture=capture,
        clock_ms=lambda: 202,
    )
    writer = RecordingWriter()
    context = server._create_offline_context(
        session_id="offline-session",
        box_writer=writer,
        conn_id=7,
        peer="box:1",
    )
    return OfflineHarness(server, context, store, writer, confirmations)


def _second_session(
    owner: OfflineHarness,
    *,
    session_id: str = "offline-session-2",
) -> OfflineHarness:
    writer = RecordingWriter()
    context = owner.server._create_offline_context(
        session_id=session_id,
        box_writer=writer,
        conn_id=8,
        peer="box:2",
    )
    return OfflineHarness(
        owner.server,
        context,
        owner.store,
        writer,
        owner.confirmations,
    )


@pytest.fixture
def offline_harness(store: TwinCommandStore, make_config: Any) -> OfflineHarness:
    return _make_harness(store, make_config, with_work=True)


@pytest.fixture
def empty_offline_harness(
    store: TwinCommandStore,
    make_config: Any,
) -> OfflineHarness:
    return _make_harness(store, make_config, with_work=False)


@pytest.mark.asyncio
async def test_offline_isnewset_with_work_writes_exactly_one_setting(
    offline_harness: OfflineHarness,
) -> None:
    active = await offline_harness.begin()

    assert offline_harness.writer.invocations == [active.wire_frame]
    assert b"<Reason>Setting</Reason>" in active.wire_frame
    assert b"<Result>END</Result>" not in active.wire_frame


@pytest.mark.asyncio
async def test_offline_isnewset_without_work_writes_exactly_one_end(
    empty_offline_harness: OfflineHarness,
) -> None:
    await empty_offline_harness.box(
        _frame(
            result="IsNewSet",
            device_id="123",
            message_id=14_000_000,
            id_set=1_786_000_000,
        )
    )

    assert len(empty_offline_harness.writer.invocations) == 1
    assert b"<Result>END</Result>" in empty_offline_harness.writer.invocations[0]


@pytest.mark.asyncio
async def test_offline_final_ack_writes_exactly_one_end(
    offline_harness: OfflineHarness,
) -> None:
    active = await offline_harness.begin()
    offline_harness.writer.clear()

    await offline_harness.box(_frame(result="ACK", reason="Setting"))

    assert len(offline_harness.writer.invocations) == 1
    assert b"<Result>END</Result>" in offline_harness.writer.invocations[0]
    assert offline_harness.store.read_command(active.command_id).state is CommandState.AWAITING_EVENT


@pytest.mark.asyncio
async def test_offline_unknown_write_never_attempts_fallback_end(
    offline_harness: OfflineHarness,
) -> None:
    offline_harness.writer.fail_drain_once(ConnectionResetError("reset"))

    await offline_harness.box(
        _frame(
            result="IsNewSet",
            device_id="123",
            message_id=14_000_000,
            id_set=1_786_000_000,
        )
    )

    assert len(offline_harness.writer.invocations) == 1
    assert offline_harness.context.close_requested.is_set()


@pytest.mark.asyncio
async def test_offline_ack_writes_successor_or_end_but_never_both(
    offline_harness: OfflineHarness,
) -> None:
    _enqueue(
        offline_harness.store,
        ingress_id="ingress-2",
        item_name="BAT_AC",
        value_text="1",
    )
    first = await offline_harness.begin()
    offline_harness.writer.clear()

    await offline_harness.box(_frame(result="ACK", reason="Setting"))

    second = offline_harness.context.dialog.active_attempt
    assert second is not None
    assert second.command_id != first.command_id
    assert offline_harness.writer.invocations == [second.wire_frame]

    offline_harness.writer.clear()
    await offline_harness.box(
        _frame(
            result="ACK",
            reason="Setting",
            extra="<Rdt>06.08.2026 10:12:03</Rdt>",
        )
    )
    assert len(offline_harness.writer.invocations) == 1
    assert b"<Result>END</Result>" in offline_harness.writer.invocations[0]


@pytest.mark.asyncio
async def test_offline_nack_is_suppressed_and_writes_one_end(
    offline_harness: OfflineHarness,
) -> None:
    active = await offline_harness.begin()
    offline_harness.writer.clear()
    nack = _frame(result="NACK", reason="WC")

    await offline_harness.box(nack)

    assert offline_harness.writer.invocations != [nack]
    assert len(offline_harness.writer.invocations) == 1
    assert b"<Result>END</Result>" in offline_harness.writer.invocations[0]
    assert offline_harness.store.read_command(active.command_id).state is CommandState.FAILED


@pytest.mark.asyncio
async def test_unowned_ack_gets_one_generic_response_without_claim(
    offline_harness: OfflineHarness,
) -> None:
    await offline_harness.box(_frame(result="ACK", reason="Other"))

    assert len(offline_harness.writer.invocations) == 1
    assert b"<Reason>Setting</Reason>" not in offline_harness.writer.invocations[0]
    assert offline_harness.store.single_nonterminal("123").state is CommandState.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ("IsNewFW", "IsNewWeather"))
async def test_firmware_and_weather_never_claim_local_work(
    offline_harness: OfflineHarness,
    result: str,
) -> None:
    await offline_harness.box(_frame(result=result, device_id="123"))

    assert len(offline_harness.writer.invocations) == 1
    assert b"<Reason>Setting</Reason>" not in offline_harness.writer.invocations[0]
    assert offline_harness.store.single_nonterminal("123").state is CommandState.PENDING


@pytest.mark.asyncio
async def test_invalid_crc_never_claims_and_emits_at_most_one_generic_response(
    offline_harness: OfflineHarness,
) -> None:
    valid = _frame(result="IsNewSet", device_id="123")
    marker = valid.rfind(b"<CRC>") + len(b"<CRC>")
    invalid = valid[:marker] + b"00000" + valid[marker + 5:]

    await offline_harness.box(invalid)

    assert len(offline_harness.writer.invocations) <= 1
    assert offline_harness.store.single_nonterminal("123").state is CommandState.PENDING


@pytest.mark.asyncio
async def test_offline_setting_capture_has_exact_attempt_identity(
    store: TwinCommandStore,
    make_config: Any,
) -> None:
    capture = MagicMock()
    harness = _make_harness(store, make_config, with_work=True, capture=capture)

    active = await harness.begin()

    local = next(
        call.kwargs
        for call in capture.capture.call_args_list
        if call.kwargs.get("attempt_link") is not None
    )
    assert local["direction"] == "proxy_to_box"
    assert local["raw_bytes"] == active.wire_frame
    assert local["attempt_link"].command_id == active.command_id
    assert local["attempt_link"].audit_id == active.audit_id


@pytest.mark.asyncio
async def test_active_owner_elsewhere_gets_one_end_without_second_claim(
    offline_harness: OfflineHarness,
) -> None:
    active = await offline_harness.begin()
    contender = _second_session(offline_harness)

    await contender.box(
        _frame(
            result="IsNewSet",
            device_id="123",
            message_id=14_000_001,
            id_set=1_786_000_001,
        )
    )

    assert len(contender.writer.invocations) == 1
    assert b"<Result>END</Result>" in contender.writer.invocations[0]
    assert contender.context.dialog.active_attempt is None
    assert offline_harness.store.read_command(active.command_id).state is CommandState.AWAITING_ACK


@pytest.mark.asyncio
async def test_wrong_session_ack_cannot_advance_owned_attempt(
    offline_harness: OfflineHarness,
) -> None:
    active = await offline_harness.begin()
    other = _second_session(offline_harness)

    await other.box(_frame(result="ACK", reason="Setting"))

    assert len(other.writer.invocations) == 1
    assert b"<Reason>Setting</Reason>" not in other.writer.invocations[0]
    assert offline_harness.store.read_command(active.command_id).state is CommandState.AWAITING_ACK
    assert offline_harness.context.dialog.active_attempt == active


@pytest.mark.asyncio
async def test_preinvocation_claim_failure_selects_one_end(
    offline_harness: OfflineHarness,
) -> None:
    coordinator = MagicMock()
    coordinator.claim_and_write_next = AsyncMock(
        side_effect=RuntimeError("store unavailable")
    )
    offline_harness.server.twin_coordinator = coordinator

    await offline_harness.box(
        _frame(
            result="IsNewSet",
            device_id="123",
            message_id=14_000_000,
            id_set=1_786_000_000,
        )
    )

    assert len(offline_harness.writer.invocations) == 1
    assert b"<Result>END</Result>" in offline_harness.writer.invocations[0]


@pytest.mark.asyncio
async def test_invalid_crc_during_active_attempt_closes_without_second_write(
    offline_harness: OfflineHarness,
) -> None:
    active = await offline_harness.begin()
    offline_harness.writer.clear()
    valid = _frame(result="ACK", reason="Setting")
    marker = valid.rfind(b"<CRC>") + len(b"<CRC>")
    invalid = valid[:marker] + b"00000" + valid[marker + 5:]

    await offline_harness.box(invalid)

    assert offline_harness.writer.invocations == []
    assert offline_harness.context.close_requested.is_set()
    assert offline_harness.store.read_command(active.command_id).state is CommandState.AWAITING_ACK
