"""Bounded semantic stream-pump tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from protocol.frame import (
    FrameDirection,
    FrameStreamAssembler,
    StreamErrorCode,
    build_frame,
)
from proxy.dialog import SessionRoute, SettingDialog
from proxy.server import (
    ProxyConnectionContext,
    ProxyServer,
    StreamClosedEvent,
    StreamFrameEvent,
)
from proxy.writer import SerializedBoxWriter
from telemetry.settings_audit import CloudSettingAuditObserver


class NullWriter:
    """Minimal in-process writer used only to complete the context."""

    def write(self, _raw: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


def _context() -> ProxyConnectionContext:
    return ProxyConnectionContext(
        session_id="session-1",
        route=SessionRoute.ONLINE,
        dialog=SettingDialog("session-1", SessionRoute.ONLINE),
        box_assembler=FrameStreamAssembler(),
        cloud_assembler=FrameStreamAssembler(),
        box_writer=SerializedBoxWriter(NullWriter(), clock_ms=lambda: 1),
        cloud_audit=CloudSettingAuditObserver(None),
        semantic_events=asyncio.Queue(maxsize=1),
        cloud_writer=NullWriter(),
        close_requested=asyncio.Event(),
    )


@pytest.mark.asyncio
async def test_stream_pump_preserves_partial_and_coalesced_frame_bytes(
    stream_reader_from_chunks: Any,
    make_config: Any,
) -> None:
    first = build_frame("<Result>ACK</Result>").encode()
    second = build_frame("<Result>END</Result>").encode()
    joined = first + second
    reader = stream_reader_from_chunks(joined[:9], joined[9:31], joined[31:])
    context = _context()
    server = ProxyServer(make_config())

    pump = asyncio.create_task(
        server.pump_stream_events(
            context, reader, direction=FrameDirection.CLOUD_TO_PROXY
        )
    )
    first_event = await context.semantic_events.get()
    second_event = await context.semantic_events.get()
    closed_event = await context.semantic_events.get()
    await pump

    assert isinstance(first_event, StreamFrameEvent)
    assert isinstance(second_event, StreamFrameEvent)
    assert first_event.frame.raw == first
    assert second_event.frame.raw == second
    assert isinstance(closed_event, StreamClosedEvent)
    assert closed_event.error_code is None


@pytest.mark.asyncio
async def test_queue_capacity_one_backpressures_second_frame(
    stream_reader_from_chunks: Any,
    make_config: Any,
) -> None:
    first = build_frame("<Result>ACK</Result>").encode()
    second = build_frame("<Result>END</Result>").encode()
    context = _context()
    server = ProxyServer(make_config())
    reader = stream_reader_from_chunks(first + second)

    pump = asyncio.create_task(
        server.pump_stream_events(
            context, reader, direction=FrameDirection.CLOUD_TO_PROXY
        )
    )
    await asyncio.sleep(0)

    assert context.semantic_events.qsize() == 1
    assert pump.done() is False
    assert (await context.semantic_events.get()).frame.raw == first
    assert (await context.semantic_events.get()).frame.raw == second
    assert isinstance(await context.semantic_events.get(), StreamClosedEvent)
    await pump


@pytest.mark.asyncio
async def test_partial_eof_emits_exact_error_and_discards_buffer(
    stream_reader_from_chunks: Any,
    make_config: Any,
) -> None:
    context = _context()
    server = ProxyServer(make_config())
    reader = stream_reader_from_chunks(b"<Frame><Result>ACK</Result>")

    await server.pump_stream_events(
        context, reader, direction=FrameDirection.BOX_TO_PROXY
    )
    event = await context.semantic_events.get()

    assert event == StreamClosedEvent(
        FrameDirection.BOX_TO_PROXY, StreamErrorCode.EOF_PARTIAL
    )


@pytest.mark.asyncio
async def test_one_byte_frame_overflow_emits_bounded_error(
    stream_reader_from_chunks: Any,
    make_config: Any,
) -> None:
    context = _context()
    server = ProxyServer(make_config())
    reader = stream_reader_from_chunks(b"<Frame>" + b"x" * 1_048_570)

    await server.pump_stream_events(
        context, reader, direction=FrameDirection.BOX_TO_PROXY
    )
    event = await context.semantic_events.get()

    assert event == StreamClosedEvent(
        FrameDirection.BOX_TO_PROXY, StreamErrorCode.BUFFER_OVERFLOW
    )
