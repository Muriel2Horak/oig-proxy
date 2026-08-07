"""Semantic ownership and byte serialization tests for BOX writes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import pytest

from capture.frame_capture import AttemptCaptureLink
from proxy.dialog import DialogStateError
from proxy.writer import (
    BoxWriteOutcome,
    BoxWritePurpose,
    SerializedBoxWriter,
)
from twin.state import ActiveLocalAttempt, AttemptWriteOutcome


class RecordingStreamWriter:
    """Record exact write/drain order without external I/O."""

    def __init__(self, observed: list[str] | None = None) -> None:
        self.observed = observed if observed is not None else []
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))
        self.observed.append("write")

    async def drain(self) -> None:
        self.observed.append("drain")


class BlockingStreamWriter(RecordingStreamWriter):
    """Block the first drain to expose concurrent invocation order."""

    def __init__(self) -> None:
        super().__init__()
        self.first_drain_entered = asyncio.Event()
        self.release_first_drain = asyncio.Event()
        self._drain_count = 0

    async def drain(self) -> None:
        self._drain_count += 1
        if self._drain_count == 1:
            self.first_drain_entered.set()
            await self.release_first_drain.wait()
        self.observed.append("drain")


class RaisingWriteStreamWriter(RecordingStreamWriter):
    """Raise synchronously at the socket invocation boundary."""

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))
        self.observed.append("write")
        raise OSError("socket rejected write")


class RaisingDrainStreamWriter(RecordingStreamWriter):
    """Raise after write invocation while completion is uncertain."""

    async def drain(self) -> None:
        self.observed.append("drain")
        raise ConnectionError("drain lost connection")


def _clock(*values: int) -> Callable[[], int]:
    return iter(values).__next__


@pytest.mark.asyncio
async def test_dialogue_owner_blocks_getactual_until_release() -> None:
    raw = RecordingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=_clock(10, 11, 12, 13))
    await writer.acquire_dialogue("session-1")

    getactual = asyncio.create_task(
        writer.write_frame(
            b"getactual", purpose=BoxWritePurpose.LOCAL_GETACTUAL
        )
    )
    await asyncio.sleep(0)
    assert raw.writes == []

    await writer.write_frame(
        b"setting",
        purpose=BoxWritePurpose.LOCAL_SETTING,
        owner_session_id="session-1",
    )
    await writer.release_dialogue("session-1")
    await getactual

    assert raw.writes == [b"setting", b"getactual"]


@pytest.mark.asyncio
async def test_writer_runs_before_write_immediately_inside_lock(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    observed: list[str] = []
    raw = RecordingStreamWriter(observed)
    clock_values = iter((10, 11))

    def clock_ms() -> int:
        observed.append("clock")
        return next(clock_values)

    writer = SerializedBoxWriter(raw, clock_ms=clock_ms)
    await writer.acquire_dialogue("session-1")

    async def before_write() -> None:
        observed.append("durable-start")

    result = await writer.write_attempt(
        active_local_attempt, before_write=before_write
    )

    assert observed == [
        "durable-start",
        "clock",
        "write",
        "drain",
        "clock",
    ]
    assert result.outcome is AttemptWriteOutcome.DRAINED
    assert result.started_at_ms == 10
    assert result.drain_completed_at_ms == 11


@pytest.mark.asyncio
async def test_before_write_waits_for_the_serialization_lock(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    raw = BlockingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=_clock(10, 11, 12, 13))
    await writer.acquire_dialogue("session-1")
    first = asyncio.create_task(
        writer.write_frame(
            b"first-setting",
            purpose=BoxWritePurpose.LOCAL_SETTING,
            owner_session_id="session-1",
        )
    )
    await raw.first_drain_entered.wait()
    durable_start = asyncio.Event()

    async def before_write() -> None:
        durable_start.set()

    second = asyncio.create_task(
        writer.write_attempt(active_local_attempt, before_write=before_write)
    )
    await asyncio.sleep(0)

    assert durable_start.is_set() is False
    assert raw.writes == [b"first-setting"]
    raw.release_first_drain.set()
    await asyncio.gather(first, second)
    assert durable_start.is_set() is True
    assert raw.writes == [b"first-setting", b"local-setting-frame"]


@pytest.mark.asyncio
async def test_before_write_failure_prevents_socket_invocation(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    raw = RecordingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10)
    await writer.acquire_dialogue("session-1")

    async def fail_before_write() -> None:
        raise RuntimeError("durable transition failed")

    with pytest.raises(RuntimeError, match="durable transition failed"):
        await writer.write_attempt(
            active_local_attempt, before_write=fail_before_write
        )

    assert raw.writes == []


@pytest.mark.asyncio
async def test_attempt_sync_write_failure_is_unknown_after_durable_start(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    observed: list[str] = []
    raw = RaisingWriteStreamWriter(observed)
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10)
    await writer.acquire_dialogue("session-1")

    async def before_write() -> None:
        observed.append("durable-start")

    result = await writer.write_attempt(
        active_local_attempt, before_write=before_write
    )

    assert observed == ["durable-start", "write"]
    assert result.outcome is AttemptWriteOutcome.UNKNOWN
    assert result.error_text == "socket rejected write"


@pytest.mark.asyncio
async def test_concurrent_raw_writes_are_serialized_through_drain() -> None:
    raw = BlockingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=_clock(10, 11, 12, 13))

    first = asyncio.create_task(
        writer.write_frame(b"first", purpose=BoxWritePurpose.CLOUD_FORWARD)
    )
    await raw.first_drain_entered.wait()
    second = asyncio.create_task(
        writer.write_frame(b"second", purpose=BoxWritePurpose.CLOUD_FORWARD)
    )
    await asyncio.sleep(0)

    assert raw.writes == [b"first"]
    raw.release_first_drain.set()
    await asyncio.gather(first, second)
    assert raw.writes == [b"first", b"second"]


@pytest.mark.asyncio
async def test_same_owner_setting_then_deferred_end_preserves_order() -> None:
    raw = RecordingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=_clock(10, 11, 12, 13))
    await writer.acquire_dialogue("session-1")

    await writer.write_frame(
        b"setting",
        purpose=BoxWritePurpose.LOCAL_SETTING,
        owner_session_id="session-1",
    )
    await writer.write_frame(
        b"end",
        purpose=BoxWritePurpose.DEFERRED_END,
        owner_session_id="session-1",
    )

    assert raw.writes == [b"setting", b"end"]


@pytest.mark.asyncio
async def test_local_write_rejects_wrong_dialogue_owner() -> None:
    raw = RecordingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10)
    await writer.acquire_dialogue("session-1")

    with pytest.raises(DialogStateError, match="owner"):
        await writer.write_frame(
            b"setting",
            purpose=BoxWritePurpose.LOCAL_SETTING,
            owner_session_id="session-2",
        )

    assert raw.writes == []


@pytest.mark.asyncio
async def test_dialogue_release_rejects_wrong_owner() -> None:
    writer = SerializedBoxWriter(RecordingStreamWriter(), clock_ms=lambda: 10)
    await writer.acquire_dialogue("session-1")

    with pytest.raises(DialogStateError, match="current dialogue owner"):
        await writer.release_dialogue("session-2")

    await writer.release_dialogue("session-1")


@pytest.mark.asyncio
async def test_local_write_requires_dialogue_owner() -> None:
    writer = SerializedBoxWriter(RecordingStreamWriter(), clock_ms=lambda: 10)

    with pytest.raises(DialogStateError, match="requires a dialogue owner"):
        await writer.write_frame(
            b"setting",
            purpose=BoxWritePurpose.LOCAL_SETTING,
        )


@pytest.mark.asyncio
async def test_offline_response_rejects_wrong_dialogue_owner() -> None:
    writer = SerializedBoxWriter(RecordingStreamWriter(), clock_ms=lambda: 10)
    await writer.acquire_dialogue("session-1")

    with pytest.raises(DialogStateError, match="offline response"):
        await writer.write_frame(
            b"end",
            purpose=BoxWritePurpose.OFFLINE_RESPONSE,
            owner_session_id="session-2",
        )

    await writer.release_dialogue("session-1")


@pytest.mark.asyncio
async def test_writer_rejects_invalid_public_arguments() -> None:
    writer = SerializedBoxWriter(RecordingStreamWriter(), clock_ms=lambda: 10)

    with pytest.raises(ValueError, match="session_id must be a non-empty string"):
        await writer.acquire_dialogue("")
    with pytest.raises(TypeError, match="frame must be exact bytes"):
        await writer.write_frame(
            cast(bytes, "frame"),
            purpose=BoxWritePurpose.CLOUD_FORWARD,
        )
    with pytest.raises(TypeError, match="purpose must be a BoxWritePurpose"):
        await writer.write_frame(
            b"frame",
            purpose=cast(BoxWritePurpose, "cloud_forward"),
        )
    with pytest.raises(TypeError, match="attempt must be an ActiveLocalAttempt"):
        await writer.write_attempt(
            cast(ActiveLocalAttempt, object()),
            before_write=_no_op,
        )

    assert writer.invocation_count == 0


@pytest.mark.asyncio
async def test_synchronous_write_failure_is_failed() -> None:
    raw = RaisingWriteStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10)

    result = await writer.write_frame(
        b"frame", purpose=BoxWritePurpose.CLOUD_FORWARD
    )

    assert result.outcome is BoxWriteOutcome.FAILED
    assert result.started_at_ms == 10
    assert result.drain_completed_at_ms is None
    assert result.error_text == "socket rejected write"
    assert raw.observed == ["write"]


@pytest.mark.asyncio
async def test_drain_failure_is_unknown() -> None:
    raw = RaisingDrainStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10)

    result = await writer.write_frame(
        b"frame", purpose=BoxWritePurpose.CLOUD_FORWARD
    )

    assert result.outcome is BoxWriteOutcome.UNKNOWN
    assert result.started_at_ms == 10
    assert result.drain_completed_at_ms is None
    assert result.error_text == "drain lost connection"
    assert raw.observed == ["write", "drain"]


@pytest.mark.asyncio
async def test_observer_receives_exact_bytes_immediately_after_invocation() -> None:
    events: list[tuple[str, bytes]] = []

    class OrderedWriter(RecordingStreamWriter):
        def write(self, data: bytes) -> None:
            events.append(("write", bytes(data)))
            self.writes.append(bytes(data))

        async def drain(self) -> None:
            events.append(("drain", b""))

    def observe(
        raw: bytes,
        _purpose: BoxWritePurpose,
        _link: AttemptCaptureLink | None,
    ) -> None:
        events.append(("observe", raw))

    raw = OrderedWriter()
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10, on_invoked=observe)

    await writer.write_frame(
        b"\x00exact\r\n", purpose=BoxWritePurpose.CLOUD_FORWARD
    )

    assert events == [
        ("write", b"\x00exact\r\n"),
        ("observe", b"\x00exact\r\n"),
        ("drain", b""),
    ]


@pytest.mark.asyncio
async def test_observer_runs_even_when_write_raises() -> None:
    invoked: list[bytes] = []
    writer = SerializedBoxWriter(
        RaisingWriteStreamWriter(),
        clock_ms=lambda: 10,
        on_invoked=lambda raw, _purpose, _link: invoked.append(raw),
    )

    result = await writer.write_frame(
        b"attempted", purpose=BoxWritePurpose.CLOUD_FORWARD
    )

    assert result.outcome is BoxWriteOutcome.FAILED
    assert invoked == [b"attempted"]


@pytest.mark.asyncio
async def test_observer_failure_does_not_change_writer_result() -> None:
    def broken_observer(
        _raw: bytes,
        _purpose: BoxWritePurpose,
        _link: AttemptCaptureLink | None,
    ) -> None:
        raise RuntimeError("capture unavailable")

    raw = RecordingStreamWriter()
    writer = SerializedBoxWriter(
        raw, clock_ms=lambda: 10, on_invoked=broken_observer
    )

    result = await writer.write_frame(
        b"frame", purpose=BoxWritePurpose.CLOUD_FORWARD
    )

    assert result.outcome is BoxWriteOutcome.DRAINED
    assert raw.writes == [b"frame"]


@pytest.mark.asyncio
async def test_attempt_observer_receives_durable_capture_link(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    links: list[AttemptCaptureLink | None] = []
    writer = SerializedBoxWriter(
        RecordingStreamWriter(),
        clock_ms=lambda: 10,
        on_invoked=lambda _raw, _purpose, link: links.append(link),
    )
    await writer.acquire_dialogue("session-1")

    result = await writer.write_attempt(
        active_local_attempt, before_write=_no_op
    )

    assert result.outcome is AttemptWriteOutcome.DRAINED
    assert links == [AttemptCaptureLink("command-1", "audit-1", 1)]


@pytest.mark.asyncio
async def test_owner_release_is_idempotent_and_wakes_waiter() -> None:
    raw = RecordingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10)
    await writer.acquire_dialogue("session-1")
    waiting = asyncio.create_task(
        writer.write_frame(b"cloud", purpose=BoxWritePurpose.CLOUD_FORWARD)
    )
    await asyncio.sleep(0)

    await writer.release_dialogue("session-1")
    await writer.release_dialogue("session-1")
    await waiting

    assert raw.writes == [b"cloud"]


async def _no_op() -> None:
    return None
