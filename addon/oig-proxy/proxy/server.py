#!/usr/bin/env python3
"""
TCP Proxy Server pro OIG Box ↔ Cloud.

Přijímá TCP spojení od OIG Boxu, forwarduje data do cloudu
a zpět. Paralelně parsuje XML framy a předává je do callbacku.

Supports ONLINE, HYBRID, and OFFLINE modes with local ACK generation.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from typing import TYPE_CHECKING

try:
    from ..capture.frame_capture import AttemptCaptureLink, FrameCapture
    from ..config import Config
    from ..protocol.frame import (
        AssembledFrame,
        FrameDirection,
        FrameStreamAssembler,
        FrameStreamError,
        StreamErrorCode,
        build_frame,
        extract_frame_from_buffer,
        infer_table_name,
        validate_frame,
    )
    from ..protocol.frames import (
        build_getactual_frame,
        build_setting_frame,
        czech_local_datetime_from_epoch,
    )
    from ..protocol.parser import parse_frame_metadata, parse_xml_frame
    from ..telemetry.settings_audit import CloudSettingAuditObserver
    from ..twin.ack_parser import (
        parse_box_ack,
        parse_setting_event,
        parse_setting_response,
        parse_tbl_events_ack,
    )
    from ..twin.delivery import TwinCoordinator, TwinDelivery
    from ..twin.state import (
        ActiveLocalAttempt,
        ConfirmedSetting,
        DeliveryDisposition,
        EvidenceContext,
        LocalResponseDisposition,
        RegisteredEventToken,
        RetryReason,
    )
    from .dialog import (
        CyclePhase,
        DialogStateError,
        RequestKind,
        SessionRoute,
        SettingDialog,
    )
    from .dns_resolve import DEFAULT_DNS_SERVER, resolve_a_record
    from .mode import ModeManager
    from .local_ack import build_local_ack
    from .writer import BoxWriteOutcome, BoxWritePurpose, SerializedBoxWriter
except ImportError:
    from capture.frame_capture import AttemptCaptureLink, FrameCapture  # type: ignore[no-redef]
    from config import Config  # type: ignore[no-redef]
    from protocol.frame import (  # type: ignore[no-redef]
        AssembledFrame,
        FrameDirection,
        FrameStreamAssembler,
        FrameStreamError,
        StreamErrorCode,
        build_frame,
        extract_frame_from_buffer,
        infer_table_name,
        validate_frame,
    )
    from protocol.frames import (  # type: ignore[no-redef]
        build_getactual_frame,
        build_setting_frame,
        czech_local_datetime_from_epoch,
    )
    from protocol.parser import parse_frame_metadata, parse_xml_frame  # type: ignore[no-redef]
    from telemetry.settings_audit import CloudSettingAuditObserver  # type: ignore[no-redef]
    from twin.ack_parser import (  # type: ignore[no-redef]
        parse_box_ack,
        parse_setting_event,
        parse_setting_response,
        parse_tbl_events_ack,
    )
    from twin.delivery import TwinCoordinator, TwinDelivery  # type: ignore[no-redef]
    from twin.state import (  # type: ignore[no-redef]
        ActiveLocalAttempt,
        ConfirmedSetting,
        DeliveryDisposition,
        EvidenceContext,
        LocalResponseDisposition,
        RegisteredEventToken,
        RetryReason,
    )
    from proxy.dialog import (  # type: ignore[no-redef]
        CyclePhase,
        DialogStateError,
        RequestKind,
        SessionRoute,
        SettingDialog,
    )
    from proxy.dns_resolve import DEFAULT_DNS_SERVER, resolve_a_record  # type: ignore[no-redef]
    from proxy.mode import ModeManager  # type: ignore[no-redef]
    from proxy.local_ack import build_local_ack  # type: ignore[no-redef]
    from proxy.writer import (  # type: ignore[no-redef]
        BoxWriteOutcome,
        BoxWritePurpose,
        SerializedBoxWriter,
    )

if TYPE_CHECKING:
    try:
        from ..telemetry.collector import TelemetryCollector
    except ImportError:
        from telemetry.collector import TelemetryCollector  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


def _extract_id_set(frame_text: str) -> int | None:
    marker_open = "<ID_Set>"
    marker_close = "</ID_Set>"
    start = frame_text.find(marker_open)
    if start == -1:
        return None
    start += len(marker_open)
    end = frame_text.find(marker_close, start)
    if end == -1:
        return None
    try:
        return int(frame_text[start:end])
    except ValueError:
        return None


def _extract_msg_id(frame_text: str) -> int | None:
    marker_open = "<ID>"
    marker_close = "</ID>"
    start = frame_text.find(marker_open)
    if start == -1:
        return None
    start += len(marker_open)
    end = frame_text.find(marker_close, start)
    if end == -1:
        return None
    try:
        return int(frame_text[start:end])
    except ValueError:
        return None


def _read_replay_frame_once(path: str) -> bytes | None:
    replay_path = Path(path)
    if not replay_path.exists():
        return None
    try:
        payload = replay_path.read_bytes()
    except OSError:
        return None
    if not payload:
        try:
            replay_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    try:
        replay_path.unlink(missing_ok=True)
    except OSError:
        pass
    return payload


TRACE_LEVEL = 5
TRANSPORT_RESULT_VALUES = frozenset({"ACK", "END"})
POLL_RESULT_VALUES = frozenset({"IsNewSet", "IsNewWeather", "IsNewFW"})
# NOTE: kept in sync with sensor/processor.py:TRANSPORT_METADATA_KEYS. Not extracted
# to a shared module: the HAOS git-addon rebuild runs `git clean`, which deletes any
# new untracked file before the image is built.
TRANSPORT_METADATA_KEYS = frozenset(
    {
        "Confirm",
        "ID",
        "ID_Server",
        "NewValue",
        "Rdt",
        "Result",
        "TSec",
        "TblItem",
        "Tmr",
        "ToDo",
        "mytimediff",
    }
)

# Typ callbacku volaného při parsování frame
FrameCallback = Callable[[dict[str, Any]], Awaitable[None]]
ConfirmedSettingCallback = Callable[[str, str, str, Any], Awaitable[None]]
CommittedConfirmationCallback = Callable[[ConfirmedSetting], Awaitable[None]]
ValidDeviceCallback = Callable[
    [str, int | None, int | None], Awaitable[bool]
]


@dataclass(frozen=True, slots=True)
class StreamFrameEvent:
    """One exact assembled frame queued for the semantic router."""

    direction: FrameDirection
    frame: AssembledFrame
    registered_event: RegisteredEventToken | None = None


@dataclass(frozen=True, slots=True)
class StreamClosedEvent:
    """One clean or bounded-error EOF observed by a read pump."""

    direction: FrameDirection
    error_code: StreamErrorCode | None


class StreamTimeoutKind(str, Enum):
    """Semantic deadline whose immutable identity must still be current."""

    CLOUD_RESPONSE = "cloud_response"
    LOCAL_ACK = "local_ack"


@dataclass(frozen=True, slots=True)
class StreamTimeoutEvent:
    """One absolute deadline callback routed through the sole mutator."""

    kind: StreamTimeoutKind
    expectation_sequence: int | None = None
    command_id: str | None = None
    attempt_number: int | None = None
    session_id: str | None = None
    deadline_ms: int | None = None


StreamEvent = StreamFrameEvent | StreamClosedEvent | StreamTimeoutEvent


@dataclass(slots=True)
class ProxyConnectionContext:
    """All mutable semantic state owned by one BOX/cloud connection pair."""

    session_id: str
    route: SessionRoute
    dialog: SettingDialog
    box_assembler: FrameStreamAssembler
    cloud_assembler: FrameStreamAssembler
    box_writer: SerializedBoxWriter
    cloud_audit: CloudSettingAuditObserver
    semantic_events: asyncio.Queue[StreamEvent]
    cloud_writer: asyncio.StreamWriter | None
    close_requested: asyncio.Event
    cloud_timer: asyncio.TimerHandle | None = None
    ack_timer: asyncio.TimerHandle | None = None
    timer_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    local_eligible_sequences: set[int] = field(default_factory=set)
    conn_id: int | None = None
    peer: str | None = None


class ProxyServer:
    """
    TCP proxy server.

    Přijímá spojení od Boxu na proxy_port.
    Pro každé spojení otevře spojení do cloudu.
    Forwarduje data oběma směry a parsuje framy z Boxu.
    """

    def __init__(
        self,
        config: Config,
        on_frame: FrameCallback | None = None,
        on_confirmed_setting: ConfirmedSettingCallback | None = None,
        twin_delivery: TwinDelivery | None = None,
        frame_capture: FrameCapture | None = None,
        telemetry_collector: "TelemetryCollector | None" = None,
        twin_coordinator: TwinCoordinator | None = None,
        on_valid_device: ValidDeviceCallback | None = None,
        on_committed_confirmation: CommittedConfirmationCallback | None = None,
        clock_ms: Callable[[], int] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.on_frame = on_frame
        self.on_confirmed_setting = on_confirmed_setting
        self.twin_delivery = twin_delivery
        self.frame_capture = frame_capture
        self.telemetry_collector = telemetry_collector
        self.twin_coordinator = twin_coordinator
        self.on_valid_device = on_valid_device
        self.on_committed_confirmation = on_committed_confirmation
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._monotonic = monotonic or time.monotonic
        self._server: asyncio.Server | None = None
        self._active_connections: set[asyncio.Task[None]] = set()
        self.mode_manager = ModeManager(config)

        self._start_time: float = time.time()
        self.frames_received: int = 0
        self.frames_forwarded: int = 0
        self.cloud_connects: int = 0
        self.cloud_disconnects: int = 0
        self.cloud_timeouts: int = 0
        self.cloud_errors: int = 0
        self._box_connected: bool = False
        self.box_peer: str | None = None
        self._cloud_connected: bool = False
        self._active_connection_count: int = 0
        self._cloud_ip: str = self.config.cloud_host

    async def start(self) -> None:
        """Spustí TCP server."""
        dns_upstream = getattr(self.config, "dns_upstream", DEFAULT_DNS_SERVER)
        resolved = resolve_a_record(self.config.cloud_host, dns_upstream)
        if resolved:
            self._cloud_ip = resolved
            logger.info(
                "☁️ Cloud host %s resolved to %s via %s",
                self.config.cloud_host, resolved, dns_upstream,
            )
        else:
            self._cloud_ip = self.config.cloud_host
            logger.warning(
                "⚠️ Could not resolve %s via %s, using hostname directly",
                self.config.cloud_host, dns_upstream,
            )
        self._server = await asyncio.start_server(
            self._handle_box_connection,
            self.config.proxy_host,
            self.config.proxy_port,
        )
        addr = self._server.sockets[0].getsockname() if self._server.sockets else "?"
        logger.info("🚀 OIG Proxy v2 naslouchá na %s:%s", *addr[:2])

    async def serve_forever(self) -> None:
        """Blokuje dokud není server zastaven."""
        if self._server is None:
            await self.start()
        server = self._server
        if server is None:
            return
        async with server:
            await server.serve_forever()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in list(self._active_connections):
            task.cancel()
        if self._active_connections:
            await asyncio.gather(*self._active_connections, return_exceptions=True)
        if self.twin_delivery is not None:
            self.twin_delivery.shutdown()
        logger.info("OIG Proxy v2 zastavena")

    def is_box_connected(self) -> bool:
        return self._box_connected

    def is_cloud_connected(self) -> bool:
        return self._cloud_connected

    def uptime_s(self) -> float:
        return time.time() - self._start_time

    async def route_stream_event(
        self,
        context: ProxyConnectionContext,
        event: StreamEvent,
    ) -> None:
        """Mutate one connection dialogue from the sole semantic router."""
        if isinstance(event, StreamClosedEvent):
            context.close_requested.set()
            return
        if isinstance(event, StreamTimeoutEvent):
            await self._route_timeout(context, event)
            return
        if event.direction is FrameDirection.BOX_TO_PROXY:
            await self._route_box_frame(context, event)
            return
        await self._route_cloud_frame(context, event)

    async def pump_stream_events(
        self,
        context: ProxyConnectionContext,
        reader: asyncio.StreamReader,
        *,
        direction: FrameDirection,
    ) -> None:
        """Assemble one bounded stream and backpressure its semantic queue."""
        assembler = (
            context.box_assembler
            if direction is FrameDirection.BOX_TO_PROXY
            else context.cloud_assembler
        )
        try:
            while True:
                chunk = await reader.read(65_536)
                if not chunk:
                    error_code: StreamErrorCode | None = None
                    try:
                        assembler.finish()
                    except FrameStreamError as error:
                        error_code = error.code
                    await context.semantic_events.put(
                        StreamClosedEvent(direction, error_code)
                    )
                    return
                received_at_ms = self._clock_ms()
                frames = assembler.feed(
                    chunk, received_at_ms=received_at_ms
                )
                for frame in frames:
                    token = self._register_event_before_await(
                        context, direction, frame
                    )
                    await context.semantic_events.put(
                        StreamFrameEvent(direction, frame, token)
                    )
        except FrameStreamError as error:
            await context.semantic_events.put(
                StreamClosedEvent(direction, error.code)
            )
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            assembler.reset()
            await context.semantic_events.put(
                StreamClosedEvent(direction, None)
            )

    def _register_event_before_await(
        self,
        context: ProxyConnectionContext,
        direction: FrameDirection,
        frame: AssembledFrame,
    ) -> RegisteredEventToken | None:
        coordinator = self.twin_coordinator
        if coordinator is None or direction is not FrameDirection.BOX_TO_PROXY:
            return None
        validation = validate_frame(frame)
        if validation.validated is None:
            return None
        event = parse_setting_event(
            validation.validated, direction=direction
        )
        if event is None:
            return None
        return coordinator.register_setting_event(
            event=event,
            context=EvidenceContext(
                direction,
                context.session_id,
                event.device_id,
                frame.received_at_ms,
                frame.raw,
            ),
        )

    async def _route_box_frame(
        self,
        context: ProxyConnectionContext,
        event: StreamFrameEvent,
    ) -> None:
        frame = event.frame
        self._capture_frame(
            frame.raw,
            "box_to_cloud",
            conn_id=context.conn_id,
            peer=context.peer,
        )
        validation = validate_frame(frame)
        validated = validation.validated
        if validated is None:
            if context.dialog.active_attempt is not None:
                await self._abort_active_dialogue(
                    context, RetryReason.STREAM_ERROR, frame.received_at_ms
                )
                context.close_requested.set()
                return
            await self._write_cloud(context, frame.raw)
            return

        metadata = parse_frame_metadata(validated)
        if metadata is None:
            if context.dialog.active_attempt is not None:
                await self._abort_active_dialogue(
                    context, RetryReason.UNEXPECTED_RESPONSE, frame.received_at_ms
                )
                context.close_requested.set()
                return
            await self._write_cloud(context, frame.raw)
            return

        registered_event = event.registered_event
        if registered_event is None and self.twin_coordinator is not None:
            setting_event = parse_setting_event(
                validated, direction=FrameDirection.BOX_TO_PROXY
            )
            if setting_event is not None:
                registered_event = self.twin_coordinator.register_setting_event(
                    event=setting_event,
                    context=EvidenceContext(
                        FrameDirection.BOX_TO_PROXY,
                        context.session_id,
                        setting_event.device_id,
                        frame.received_at_ms,
                        frame.raw,
                    ),
                )

        identity_accepted = False
        if metadata.device_id:
            try:
                context.dialog.bind_device(metadata.device_id)
            except DialogStateError:
                identity_accepted = False
            else:
                if self.on_valid_device is not None:
                    identity_accepted = await self.on_valid_device(
                        metadata.device_id,
                        metadata.message_id,
                        metadata.id_set,
                    )

        if registered_event is not None:
            await self._route_registered_event(
                context, event, registered_event
            )
            return

        if context.dialog.active_attempt is not None:
            response = parse_setting_response(
                validated, direction=FrameDirection.BOX_TO_PROXY
            )
            if response is None:
                context.dialog.hold_box_frame(frame)
                return
            await self._route_local_response(context, frame, response)
            return

        expectation = context.dialog.current_expectation()
        if (
            expectation is not None
            and expectation.phase is CyclePhase.WAITING_BOX_CLOUD_ACK
        ):
            response = parse_setting_response(
                validated, direction=FrameDirection.BOX_TO_PROXY
            )
            if response is None:
                context.dialog.taint_current_cycle()
                context.close_requested.set()
                return
            if not await self._write_cloud(context, frame.raw):
                return
            context.dialog.mark_cloud_setting_ack_forwarded(frame.raw)
            context.cloud_audit.box_response_forwarded(
                session_id=context.session_id,
                response=response,
                observed_at_ms=frame.received_at_ms,
            )
            await self._process_frame(frame.raw)
            return

        if metadata.result == "IsNewSet":
            expectation = context.dialog.open_forwarded_request(
                kind=RequestKind.IS_NEW_SET,
                request_raw=frame.raw,
                opened_at_monotonic=self._monotonic(),
                cloud_timeout_s=float(
                    getattr(self.config, "cloud_dialog_timeout_s", 30.0)
                ),
            )
            if (
                identity_accepted
                and metadata.message_id is not None
                and metadata.message_id >= 0
                and metadata.id_set is not None
                and metadata.id_set >= 0
            ):
                context.local_eligible_sequences.add(expectation.sequence)
            self._sync_cloud_timer(context)
        else:
            context.dialog.open_forwarded_request(
                kind=RequestKind.SINGLE_RESPONSE,
                request_raw=frame.raw,
                opened_at_monotonic=self._monotonic(),
                cloud_timeout_s=None,
            )
        if await self._write_cloud(context, frame.raw):
            await self._process_frame(frame.raw)

    async def _route_cloud_frame(
        self,
        context: ProxyConnectionContext,
        event: StreamFrameEvent,
    ) -> None:
        frame = event.frame
        self._capture_frame(
            frame.raw,
            "cloud_to_box",
            conn_id=context.conn_id,
            peer=context.peer,
        )
        expectation = context.dialog.current_expectation()
        validation = validate_frame(frame)
        validated = validation.validated
        if validated is None:
            if context.dialog.active_attempt is not None:
                await self._abort_active_dialogue(
                    context,
                    RetryReason.STREAM_ERROR,
                    frame.received_at_ms,
                )
                await context.box_writer.write_frame(
                    frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
                )
                context.close_requested.set()
                return
            await context.box_writer.write_frame(
                frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
            )
            if expectation is not None:
                context.dialog.close_current_expectation()
                self._sync_cloud_timer(context)
            context.close_requested.set()
            return
        metadata = parse_frame_metadata(validated)
        if metadata is None and context.dialog.active_attempt is not None:
            await self._abort_active_dialogue(
                context,
                RetryReason.UNEXPECTED_RESPONSE,
                frame.received_at_ms,
            )
            await context.box_writer.write_frame(
                frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
            )
            context.close_requested.set()
            return
        if expectation is None or metadata is None:
            await context.box_writer.write_frame(
                frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
            )
            if expectation is not None:
                context.dialog.close_current_expectation()
                self._sync_cloud_timer(context)
                context.close_requested.set()
            return
        if context.dialog.active_attempt is not None:
            context.dialog.hold_cloud_frame(frame)
            return
        if expectation.kind is RequestKind.SINGLE_RESPONSE:
            await context.box_writer.write_frame(
                frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
            )
            context.local_eligible_sequences.discard(expectation.sequence)
            context.dialog.close_current_expectation()
            self._sync_cloud_timer(context)
            await self._process_frame(frame.raw)
            return
        if expectation.phase is CyclePhase.WAITING_BOX_CLOUD_ACK:
            context.dialog.hold_cloud_frame(frame)
            return
        if metadata.result == "Setting":
            result = await context.box_writer.write_frame(
                frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
            )
            if result.outcome is not BoxWriteOutcome.DRAINED:
                context.close_requested.set()
                return
            context.dialog.mark_cloud_setting(frame.raw)
            try:
                context.cloud_audit.setting_forwarded(
                    session_id=context.session_id,
                    frame=validated,
                    metadata=metadata,
                    observed_at_ms=frame.received_at_ms,
                )
            except ValueError:
                context.dialog.taint_current_cycle()
                context.close_requested.set()
            await self._process_frame(frame.raw)
            return
        if metadata.result == "END":
            self._cancel_cloud_timer(context)
            if (
                expectation.sequence not in context.local_eligible_sequences
                or self.twin_coordinator is None
                or context.dialog.bound_device_id is None
            ):
                await context.box_writer.write_frame(
                    frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
                )
                context.local_eligible_sequences.discard(expectation.sequence)
                context.dialog.close_current_expectation()
                self._sync_cloud_timer(context)
                return
            trigger = context.dialog.defer_correlated_terminal_end(frame.raw)
            await context.box_writer.acquire_dialogue(context.session_id)
            decision = await self.twin_coordinator.claim_and_write_next(
                device_id=context.dialog.bound_device_id,
                session_id=context.session_id,
                received_at_ms=frame.received_at_ms,
                trigger=trigger,
                writer=context.box_writer,
            )
            if decision.disposition is DeliveryDisposition.SENT:
                if decision.active_attempt is None:
                    raise RuntimeError("sent delivery omitted its active attempt")
                context.dialog.begin_local_attempt(decision.active_attempt)
                self._arm_ack_timer(context, decision.active_attempt)
                return
            if decision.disposition in {
                DeliveryDisposition.NO_ELIGIBLE,
                DeliveryDisposition.CONTROL_DISABLED,
                DeliveryDisposition.ACTIVE_DELIVERY_ELSEWHERE,
                DeliveryDisposition.RENDER_FAILED,
            }:
                raw_end = context.dialog.take_deferred_end_and_close_cycle()
                context.local_eligible_sequences.discard(expectation.sequence)
                await context.box_writer.release_dialogue(context.session_id)
                await context.box_writer.write_frame(
                    raw_end, purpose=BoxWritePurpose.CLOUD_FORWARD
                )
                self._sync_cloud_timer(context)
                return
            context.dialog.clear_socket_state()
            await context.box_writer.release_dialogue(context.session_id)
            context.close_requested.set()
            return

        await context.box_writer.write_frame(
            frame.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
        )
        context.local_eligible_sequences.discard(expectation.sequence)
        context.dialog.close_current_expectation()
        self._sync_cloud_timer(context)
        if metadata.result != "NACK":
            context.close_requested.set()

    async def _route_local_response(
        self,
        context: ProxyConnectionContext,
        frame: AssembledFrame,
        response: Any,
    ) -> None:
        active = context.dialog.active_attempt
        coordinator = self.twin_coordinator
        if active is None or coordinator is None:
            raise RuntimeError("local response lost its active coordinator")
        decision = await coordinator.handle_local_response(
            active=active,
            response=response,
            context=EvidenceContext(
                FrameDirection.BOX_TO_PROXY,
                context.session_id,
                context.dialog.bound_device_id or active.device_id,
                frame.received_at_ms,
                frame.raw,
            ),
            writer=context.box_writer,
        )
        if decision.next_attempt is not None:
            context.dialog.replace_local_attempt(decision.next_attempt)
            self._arm_ack_timer(context, decision.next_attempt)
            return
        if decision.send_final_end:
            self._cancel_ack_timer(context)
            raw_end = context.dialog.deferred_end
            if raw_end is None:
                raise RuntimeError("local completion lost its deferred END")
            result = await context.box_writer.write_frame(
                raw_end,
                purpose=BoxWritePurpose.DEFERRED_END,
                owner_session_id=context.session_id,
            )
            if result.outcome is not BoxWriteOutcome.DRAINED:
                context.dialog.clear_socket_state()
                await context.box_writer.release_dialogue(context.session_id)
                context.close_requested.set()
                return
            expectation = context.dialog.current_expectation()
            context.dialog.take_deferred_end_and_close_cycle()
            self._sync_cloud_timer(context)
            if expectation is not None:
                context.local_eligible_sequences.discard(expectation.sequence)
            await context.box_writer.release_dialogue(context.session_id)
            for held in context.dialog.drain_held_cloud():
                await context.box_writer.write_frame(
                    held.raw, purpose=BoxWritePurpose.CLOUD_FORWARD
                )
            for held in context.dialog.drain_held_box():
                if not await self._write_cloud(context, held.raw):
                    break
            return
        if decision.close_connection:
            self._cancel_ack_timer(context)
            context.dialog.clear_socket_state()
            await context.box_writer.release_dialogue(context.session_id)
            context.close_requested.set()
            return
        if decision.disposition in {
            LocalResponseDisposition.DUPLICATE,
            LocalResponseDisposition.REJECTED,
            LocalResponseDisposition.TIMED_OUT,
        }:
            context.close_requested.set()

    async def _route_registered_event(
        self,
        context: ProxyConnectionContext,
        event: StreamFrameEvent,
        token: RegisteredEventToken,
    ) -> None:
        coordinator = self.twin_coordinator
        if coordinator is None:
            await self._write_cloud(context, event.frame.raw)
            return
        result = await coordinator.handle_registered_event(token)
        if result.confirmation is not None and self.on_committed_confirmation is not None:
            await self.on_committed_confirmation(result.confirmation)
        if context.dialog.active_attempt is None:
            await self._write_cloud(context, event.frame.raw)
            await self._process_frame(event.frame.raw)
            return
        active = context.dialog.active_attempt
        if result.command is not None and result.command.command_id == active.command_id:
            self._cancel_ack_timer(context)
            raw_end = context.dialog.deferred_end
            if raw_end is not None:
                await context.box_writer.write_frame(
                    raw_end,
                    purpose=BoxWritePurpose.DEFERRED_END,
                    owner_session_id=context.session_id,
                )
            context.dialog.clear_socket_state()
            await context.box_writer.release_dialogue(context.session_id)
            context.close_requested.set()
            return
        await self._abort_active_dialogue(
            context,
            RetryReason.UNEXPECTED_RESPONSE,
            event.frame.received_at_ms,
        )
        context.close_requested.set()

    async def _abort_active_dialogue(
        self,
        context: ProxyConnectionContext,
        reason: RetryReason,
        occurred_at_ms: int,
    ) -> None:
        self._cancel_ack_timer(context)
        active = context.dialog.active_attempt
        if active is not None and self.twin_coordinator is not None:
            await self.twin_coordinator.abort_dialogue(
                active=active,
                occurred_at_ms=occurred_at_ms,
                reason=reason,
            )
        context.dialog.clear_socket_state()
        await context.box_writer.release_dialogue(context.session_id)

    async def _route_timeout(
        self,
        context: ProxyConnectionContext,
        event: StreamTimeoutEvent,
    ) -> None:
        if event.kind is StreamTimeoutKind.CLOUD_RESPONSE:
            expectation = context.dialog.current_expectation()
            if (
                expectation is None
                or expectation.sequence != event.expectation_sequence
                or expectation.deadline_monotonic is None
                or self._monotonic() < expectation.deadline_monotonic
            ):
                return
            context.cloud_timer = None
            context.dialog.taint_current_cycle()
            context.close_requested.set()
            return

        active = context.dialog.active_attempt
        if (
            active is None
            or active.command_id != event.command_id
            or active.attempt_number != event.attempt_number
            or active.session_id != event.session_id
            or active.ack_deadline_ms != event.deadline_ms
            or self._clock_ms() < active.ack_deadline_ms
        ):
            return
        context.ack_timer = None
        await self._abort_active_dialogue(
            context,
            RetryReason.ACK_TIMEOUT,
            self._clock_ms(),
        )
        context.close_requested.set()

    def _sync_cloud_timer(self, context: ProxyConnectionContext) -> None:
        self._cancel_cloud_timer(context)
        expectation = context.dialog.current_expectation()
        if expectation is None or expectation.deadline_monotonic is None:
            return
        event = StreamTimeoutEvent(
            StreamTimeoutKind.CLOUD_RESPONSE,
            expectation_sequence=expectation.sequence,
        )
        delay = max(0.0, expectation.deadline_monotonic - self._monotonic())
        context.cloud_timer = asyncio.get_running_loop().call_later(
            delay,
            self._enqueue_timeout,
            context,
            event,
        )

    def _arm_ack_timer(
        self,
        context: ProxyConnectionContext,
        active: ActiveLocalAttempt,
    ) -> None:
        self._cancel_ack_timer(context)
        event = StreamTimeoutEvent(
            StreamTimeoutKind.LOCAL_ACK,
            command_id=active.command_id,
            attempt_number=active.attempt_number,
            session_id=active.session_id,
            deadline_ms=active.ack_deadline_ms,
        )
        delay = max(0.0, (active.ack_deadline_ms - self._clock_ms()) / 1000)
        context.ack_timer = asyncio.get_running_loop().call_later(
            delay,
            self._enqueue_timeout,
            context,
            event,
        )

    @staticmethod
    def _enqueue_timeout(
        context: ProxyConnectionContext,
        event: StreamTimeoutEvent,
    ) -> None:
        if context.close_requested.is_set():
            return
        task = asyncio.create_task(context.semantic_events.put(event))
        context.timer_tasks.add(task)
        task.add_done_callback(context.timer_tasks.discard)

    @staticmethod
    def _cancel_cloud_timer(context: ProxyConnectionContext) -> None:
        if context.cloud_timer is not None:
            context.cloud_timer.cancel()
            context.cloud_timer = None

    @staticmethod
    def _cancel_ack_timer(context: ProxyConnectionContext) -> None:
        if context.ack_timer is not None:
            context.ack_timer.cancel()
            context.ack_timer = None

    async def _write_cloud(
        self, context: ProxyConnectionContext, raw: bytes
    ) -> bool:
        writer = context.cloud_writer
        if writer is None:
            context.close_requested.set()
            return False
        try:
            writer.write(raw)
            await writer.drain()
        except (OSError, ConnectionResetError):
            context.close_requested.set()
            return False
        self.frames_forwarded += 1
        return True

    def _create_online_context(
        self,
        *,
        session_id: str,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter,
        conn_id: int,
        peer: str,
    ) -> ProxyConnectionContext:
        def capture_invocation(
            raw: bytes,
            _purpose: BoxWritePurpose,
            attempt_link: AttemptCaptureLink | None,
        ) -> None:
            self._capture_frame(
                raw,
                "proxy_to_box",
                conn_id=conn_id,
                peer=peer,
                attempt_link=attempt_link,
            )

        semantic_writer = SerializedBoxWriter(
            box_writer,
            clock_ms=self._clock_ms,
            on_invoked=capture_invocation,
        )
        return ProxyConnectionContext(
            session_id=session_id,
            route=SessionRoute.ONLINE,
            dialog=SettingDialog(session_id, SessionRoute.ONLINE),
            box_assembler=FrameStreamAssembler(),
            cloud_assembler=FrameStreamAssembler(),
            box_writer=semantic_writer,
            cloud_audit=CloudSettingAuditObserver(None),
            semantic_events=asyncio.Queue(maxsize=1),
            cloud_writer=cloud_writer,
            close_requested=asyncio.Event(),
            conn_id=conn_id,
            peer=peer,
        )

    async def run_connection_context(
        self,
        context: ProxyConnectionContext,
        box_reader: asyncio.StreamReader,
        cloud_reader: asyncio.StreamReader,
    ) -> None:
        """Run two bounded read pumps and one sole semantic router."""
        pumps = (
            asyncio.create_task(
                self.pump_stream_events(
                    context,
                    box_reader,
                    direction=FrameDirection.BOX_TO_PROXY,
                ),
                name=f"box-pump-{context.session_id}",
            ),
            asyncio.create_task(
                self.pump_stream_events(
                    context,
                    cloud_reader,
                    direction=FrameDirection.CLOUD_TO_PROXY,
                ),
                name=f"cloud-pump-{context.session_id}",
            ),
        )
        getactual = self._start_semantic_getactual_task(context)
        try:
            while not context.close_requested.is_set():
                event = await context.semantic_events.get()
                await self.route_stream_event(context, event)
        finally:
            for pump in pumps:
                if not pump.done():
                    pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            await self._stop_local_getactual_task(getactual)
            await self._cleanup_connection_context(context)

    def _start_semantic_getactual_task(
        self, context: ProxyConnectionContext
    ) -> asyncio.Task[None] | None:
        if not bool(getattr(self.config, "local_getactual_enabled", False)):
            return None
        return asyncio.create_task(
            self._semantic_getactual_loop(context),
            name=f"local-getactual-{context.session_id}",
        )

    async def _semantic_getactual_loop(
        self, context: ProxyConnectionContext
    ) -> None:
        interval_s = self._local_getactual_interval_s()
        while not context.close_requested.is_set():
            result = await context.box_writer.write_frame(
                build_getactual_frame(),
                purpose=BoxWritePurpose.LOCAL_GETACTUAL,
            )
            if result.outcome is not BoxWriteOutcome.DRAINED:
                context.close_requested.set()
                return
            await asyncio.sleep(interval_s)

    async def _cleanup_connection_context(
        self, context: ProxyConnectionContext
    ) -> None:
        for timer in (context.cloud_timer, context.ack_timer):
            if timer is not None:
                timer.cancel()
        for task in context.timer_tasks:
            task.cancel()
        if context.timer_tasks:
            await asyncio.gather(
                *context.timer_tasks, return_exceptions=True
            )
        coordinator = self.twin_coordinator
        if coordinator is not None:
            results = await coordinator.flush_registered_events(
                session_id=context.session_id
            )
            if self.on_committed_confirmation is not None:
                for result in results:
                    if result.confirmation is not None:
                        await self.on_committed_confirmation(
                            result.confirmation
                        )
            active = context.dialog.active_attempt
            if active is not None:
                try:
                    command = await coordinator.read_command(active.command_id)
                except Exception:  # noqa: BLE001
                    command = None
                if (
                    command is not None
                    and command.state.value == "awaiting_ack"
                    and command.active_session_id == context.session_id
                ):
                    await coordinator.abort_dialogue(
                        active=active,
                        occurred_at_ms=self._clock_ms(),
                        reason=RetryReason.DISCONNECT,
                    )
        context.cloud_audit.close_session(session_id=context.session_id)
        context.dialog.clear_socket_state()
        await context.box_writer.release_dialogue(context.session_id)

    def _record_telemetry_connection_end(
        self,
        *,
        box_connected_since_epoch: float | None,
        box_reason: str,
        box_peer: str | None,
        cloud_connected_since_epoch: float | None,
        cloud_reason: str,
    ) -> None:
        collector = self.telemetry_collector
        if collector is None:
            return
        collector.record_box_session_end(
            connected_since_epoch=box_connected_since_epoch,
            reason=box_reason,
            peer=box_peer,
        )
        if cloud_connected_since_epoch is not None:
            collector.record_cloud_session_end(
                connected_since_epoch=cloud_connected_since_epoch,
                reason=cloud_reason,
            )

    def _record_cloud_connect_failure(
        self,
        *,
        conn_id: int,
        failure_type: str,
        failure_detail: str,
        peer: str,
        will_go_offline: bool,
    ) -> None:
        collector = self.telemetry_collector
        if collector is None:
            return
        if failure_type == "timeout":
            collector.record_timeout(conn_id=conn_id)
        else:
            collector.record_response("", source="error", conn_id=conn_id)
        collector.record_error_context(
            event_type=f"error_cloud_connect_{failure_type}",
            details={
                "cloud_host": self.config.cloud_host,
                "cloud_port": self.config.cloud_port,
                "peer": peer,
                "error": failure_detail,
                "offline_fallback": will_go_offline,
            },
        )
        if will_go_offline:
            collector.record_offline_event(
                reason=f"cloud_connect_{failure_type}",
                local_ack=True,
                mode=str(self.mode_manager.runtime_mode.value),
            )

    def _local_getactual_interval_s(self) -> int:
        try:
            interval_s = int(getattr(self.config, "local_getactual_interval_s", 10))
        except (TypeError, ValueError):
            interval_s = 10
        return max(10, interval_s)

    def _start_local_getactual_task(
        self,
        box_writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> asyncio.Task[None] | None:
        if not bool(getattr(self.config, "local_getactual_enabled", False)):
            return None
        return asyncio.create_task(
            self._local_getactual_loop(box_writer, conn_id=conn_id, peer=peer),
            name=f"local-getactual-{conn_id}",
        )

    async def _stop_local_getactual_task(
        self,
        task: asyncio.Task[None] | None,
    ) -> None:
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _local_getactual_loop(
        self,
        box_writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> None:
        if not bool(getattr(self.config, "local_getactual_enabled", False)):
            return

        interval_s = self._local_getactual_interval_s()
        while not box_writer.is_closing():
            frame = build_getactual_frame()
            try:
                box_writer.write(frame)
                await box_writer.drain()
            except OSError as exc:
                logger.debug("Local GetActual stopped for %s: %s", peer or "unknown", exc)
                break

            self._capture_frame(frame, "proxy_to_box", conn_id=conn_id, peer=peer)
            if self.telemetry_collector is not None:
                self.telemetry_collector.record_frame_direction("proxy_to_box")
            logger.debug(
                "📤 Sent local GetActual to BOX peer=%s conn_id=%s interval=%ss",
                peer or "unknown",
                conn_id,
                interval_s,
            )
            await asyncio.sleep(interval_s)

    async def _run_box_offline_session(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        peer: Any,
        *,
        session_id: str,
        conn_id: int,
        peer_str: str,
        box_connected_since_epoch: float,
        box_disconnect_reason: str,
        cloud_disconnect_reason: str,
        current: asyncio.Task[Any] | None,
    ) -> None:
        local_getactual_task = self._start_local_getactual_task(
            box_writer,
            conn_id=conn_id,
            peer=peer_str,
        )
        try:
            await self._pipe_box_offline(
                box_reader,
                box_writer,
                peer,
                session_id=session_id,
            )
        finally:
            await self._stop_local_getactual_task(local_getactual_task)
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=None,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self.box_peer = None
            if current is not None:
                self._active_connections.discard(current)

    async def _handle_box_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        """Handler pro nové připojení od Boxu.

        Generates unique session ID for tracking twin delivery per TCP session.
        Cloud-initiated settings take priority over local queue.
        """
        current = asyncio.current_task()
        if current is not None:
            self._active_connections.add(current)
        session_conn_id = id(current)

        peer = box_writer.get_extra_info("peername", ("?", "?"))
        peer_str = f"{peer[0]}:{peer[1]}"

        # Reject excess connections immediately to prevent reconnect storm / FD exhaustion
        if self._active_connection_count >= self.config.max_concurrent_connections:
            logger.warning(
                "⚠️ Max concurrent connections (%d) reached, rejecting %s:%s",
                self.config.max_concurrent_connections,
                *peer[:2],
            )
            box_writer.close()
            try:
                await box_writer.wait_closed()
            except Exception as exc:  # noqa: BLE001
                logger.debug("wait_closed error during rejection: %s", exc)
            if current is not None:
                self._active_connections.discard(current)
            return
        self._active_connection_count += 1

        import uuid
        session_id = str(uuid.uuid4())

        self._box_connected = True
        self.box_peer = peer_str
        box_connected_since_epoch = time.time()
        cloud_connected_since_epoch: float | None = None
        cloud_disconnect_reason = "not_connected"
        box_disconnect_reason = "eof"
        logger.info("📦 BOX připojen z %s:%s (session=%s)", *peer[:2], session_id)

        # Check if we should try cloud connection
        if not self.mode_manager.should_try_cloud():
            logger.info("☁️ Skipping cloud connection (mode=%s)", self.mode_manager.configured_mode)
            box_disconnect_reason = "offline_mode"
            if self.telemetry_collector is not None:
                self.telemetry_collector.record_offline_event(
                    reason="configured_offline_mode",
                    local_ack=True,
                    mode=str(self.mode_manager.runtime_mode.value),
                )
            await self._run_box_offline_session(
                box_reader,
                box_writer,
                peer,
                session_id=session_id,
                conn_id=session_conn_id,
                peer_str=peer_str,
                box_connected_since_epoch=box_connected_since_epoch,
                box_disconnect_reason=box_disconnect_reason,
                cloud_disconnect_reason=cloud_disconnect_reason,
                current=current,
            )
            return

        # Otevřeme spojení do cloudu
        cloud_reader: asyncio.StreamReader | None = None
        cloud_writer: asyncio.StreamWriter | None = None
        try:
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(self._cloud_ip, self.config.cloud_port),
                timeout=self.config.cloud_connect_timeout,
            )
            logger.info(
                "☁️ Cloud spojen: %s:%s (session=%s)",
                self.config.cloud_host,
                self.config.cloud_port,
                session_id,
            )
            self.cloud_connects += 1
            self._cloud_connected = True
            cloud_connected_since_epoch = time.time()
            cloud_disconnect_reason = "eof"
            self.mode_manager.record_success()
        except asyncio.TimeoutError as exc:
            self.cloud_timeouts += 1
            logger.error("❌ Cloud nedostupný: %s", exc)
            self.mode_manager.record_failure(reason=str(exc))
            self._record_cloud_connect_failure(
                conn_id=session_conn_id,
                failure_type="timeout",
                failure_detail=str(exc),
                peer=peer_str,
                will_go_offline=self.mode_manager.is_offline(),
            )
            if self.mode_manager.is_offline():
                box_disconnect_reason = "offline_fallback_timeout"
                await self._run_box_offline_session(
                    box_reader,
                    box_writer,
                    peer,
                    session_id=session_id,
                    conn_id=session_conn_id,
                    peer_str=peer_str,
                    box_connected_since_epoch=box_connected_since_epoch,
                    box_disconnect_reason=box_disconnect_reason,
                    cloud_disconnect_reason=cloud_disconnect_reason,
                    current=current,
                )
                return
            box_disconnect_reason = "cloud_connect_timeout"
            box_writer.close()
            await box_writer.wait_closed()
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=None,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self.box_peer = None
            if current is not None:
                self._active_connections.discard(current)
            return
        except OSError as exc:
            self.cloud_errors += 1
            logger.error("❌ Cloud nedostupný: %s", exc)
            self.mode_manager.record_failure(reason=str(exc))
            self._record_cloud_connect_failure(
                conn_id=session_conn_id,
                failure_type="oserror",
                failure_detail=str(exc),
                peer=peer_str,
                will_go_offline=self.mode_manager.is_offline(),
            )
            if self.mode_manager.is_offline():
                box_disconnect_reason = "offline_fallback_oserror"
                await self._run_box_offline_session(
                    box_reader,
                    box_writer,
                    peer,
                    session_id=session_id,
                    conn_id=session_conn_id,
                    peer_str=peer_str,
                    box_connected_since_epoch=box_connected_since_epoch,
                    box_disconnect_reason=box_disconnect_reason,
                    cloud_disconnect_reason=cloud_disconnect_reason,
                    current=current,
                )
                return
            box_disconnect_reason = "cloud_connect_oserror"
            box_writer.close()
            await box_writer.wait_closed()
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=None,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self.box_peer = None
            if current is not None:
                self._active_connections.discard(current)
            return

        if cloud_reader is None or cloud_writer is None:
            logger.error("❌ Cloud connection missing stream endpoints after successful connect")
            box_writer.close()
            await box_writer.wait_closed()
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason="cloud_connect_missing_stream",
                box_peer=peer_str,
                cloud_connected_since_epoch=None,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self.box_peer = None
            if current is not None:
                self._active_connections.discard(current)
            return

        local_getactual_task: asyncio.Task[None] | None = None
        try:
            if self.twin_coordinator is not None:
                context = self._create_online_context(
                    session_id=session_id,
                    box_writer=box_writer,
                    cloud_writer=cloud_writer,
                    conn_id=session_conn_id,
                    peer=peer_str,
                )
                await self.run_connection_context(
                    context,
                    box_reader,
                    cloud_reader,
                )
            else:
                local_getactual_task = self._start_local_getactual_task(
                    box_writer,
                    conn_id=session_conn_id,
                    peer=peer_str,
                )
                await self._run_legacy_online_pipes(
                    box_reader,
                    box_writer,
                    cloud_reader,
                    cloud_writer,
                    peer=peer,
                    session_id=session_id,
                )
        finally:
            await self._stop_local_getactual_task(local_getactual_task)
            if self.twin_delivery is not None:
                self.twin_delivery.clear_session(session_id)
            for writer in (box_writer, cloud_writer):
                if writer and not writer.is_closing():
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("wait_closed error in pipe cleanup: %s", exc)
            self._record_telemetry_connection_end(
                box_connected_since_epoch=box_connected_since_epoch,
                box_reason=box_disconnect_reason,
                box_peer=peer_str,
                cloud_connected_since_epoch=cloud_connected_since_epoch,
                cloud_reason=cloud_disconnect_reason,
            )
            self._active_connection_count -= 1
            self._box_connected = False
            self._cloud_connected = False
            self.box_peer = None
            self.cloud_disconnects += 1
            if current is not None:
                self._active_connections.discard(current)
            logger.info("🔌 BOX odpojen: %s:%s (session=%s)", *peer[:2], session_id)

    async def _run_legacy_online_pipes(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter,
        *,
        peer: tuple[Any, ...],
        session_id: str,
    ) -> None:
        """Run the compatibility forwarding path without semantic control."""
        pipe_tasks = (
            asyncio.create_task(
                self._pipe_box_to_cloud(
                    box_reader,
                    cloud_writer,
                    box_writer,
                    peer=peer,
                    session_id=session_id,
                )
            ),
            asyncio.create_task(
                self._pipe_cloud_to_box(
                    cloud_reader,
                    box_writer,
                    peer=peer,
                    session_id=session_id,
                )
            ),
        )
        try:
            _done, pending = await asyncio.wait(
                pipe_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            for task in pipe_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pipe_tasks, return_exceptions=True)
            raise

    async def _pipe_box_to_cloud(
        self,
        box_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter,
        box_writer: asyncio.StreamWriter | None = None,
        peer: tuple | None = None,
        session_id: str | None = None,
    ) -> None:
        """Čte data od Boxu, parsuje framy a forwarduje do cloudu."""
        peer_str = f"{peer[0]}:{peer[1]}" if peer and len(peer) >= 2 else None
        conn_id = id(asyncio.current_task())
        buf = bytearray()
        while True:
            try:
                data = await box_reader.read(4096)
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            if not data:
                try:
                    can_write_eof = getattr(cloud_writer, "can_write_eof", None)
                    if callable(can_write_eof) and can_write_eof():
                        cloud_writer.write_eof()
                except (OSError, ConnectionResetError):
                    pass
                break

            buf.extend(data)
            forward_chunks: list[bytes] = []
            withheld_chunks = False
            while True:
                frame_bytes = extract_frame_from_buffer(buf)
                if frame_bytes is None:
                    break
                self._capture_frame(frame_bytes, "box_to_cloud", conn_id=conn_id, peer=peer_str)
                await self._handle_twin_frames(frame_bytes, box_writer, run_isnewset_hook=False)
                await self._process_frame(frame_bytes)

                parsed_frame = parse_xml_frame(frame_bytes.decode("utf-8", errors="replace"))
                table_name = self._effective_table_name(parsed_frame, frame_bytes.decode("utf-8", errors="replace"))
                device_id = str(parsed_frame.get("_device_id") or "")
                observed_id_set = _extract_id_set(frame_bytes.decode("utf-8", errors="replace"))
                observed_msg_id = _extract_msg_id(frame_bytes.decode("utf-8", errors="replace"))
                if self.twin_delivery is not None:
                    self.twin_delivery.observe_id_set(observed_id_set)
                    self.twin_delivery.observe_msg_id(observed_msg_id)

                if self.telemetry_collector is not None:
                    self.telemetry_collector.record_request(table_name or None, conn_id)
                    self.telemetry_collector.record_frame_direction("box_to_proxy")

                if table_name in {"IsNewSet", "IsNewFW", "IsNewWeather"}:
                    pending = self.twin_delivery.has_pending() if self.twin_delivery else False
                    cloud_inf = self.twin_delivery.is_cloud_inflight() if self.twin_delivery else False
                    logger.debug(
                        "IsNew* poll: table=%s twin_delivery=%s has_pending=%s cloud_inflight=%s box_writer=%s",
                        table_name,
                        self.twin_delivery is not None,
                        pending,
                        cloud_inf,
                        box_writer is not None,
                    )
                if (
                    self.twin_delivery is not None
                    and self.twin_delivery.has_pending()
                    and not self.twin_delivery.is_cloud_inflight()
                    and table_name in {"IsNewSet", "IsNewFW", "IsNewWeather"}
                    and box_writer is not None
                ):
                    replay_frame = _read_replay_frame_once("/data/replay_setting_frame.xml")
                    if replay_frame is not None:
                        try:
                            box_writer.write(replay_frame)
                            await box_writer.drain()
                            self._capture_frame(replay_frame, "proxy_to_box", conn_id=conn_id, peer=peer_str)
                            logger.info("📤 Replayed raw Setting frame to BOX from /data/replay_setting_frame.xml")
                            withheld_chunks = True
                            continue
                        except (OSError, ConnectionResetError) as exc:
                            logger.error("Failed to replay raw Setting frame to BOX: %s", exc)

                    pending_settings = await self.twin_delivery.deliver_pending(
                        device_id,
                        session_id=session_id,
                    )
                    setting = pending_settings[0] if pending_settings else None
                    logger.debug("deliver_pending returned: %s", setting)
                    if setting is not None:
                        audit_session_id = session_id or ""
                        next_id_set = self.twin_delivery.next_id_set()
                        next_msg_id = self.twin_delivery.next_msg_id()
                        now_utc = datetime.now(timezone.utc)
                        tsec_utc = (
                            now_utc
                            if int(now_utc.timestamp()) >= next_id_set
                            else datetime.fromtimestamp(next_id_set, tz=timezone.utc)
                        )
                        wire_dt = czech_local_datetime_from_epoch(next_id_set)
                        rendered_setting = build_setting_frame(
                            device_id=device_id,
                            table_name=setting.table,
                            item_name=setting.key,
                            value_text=str(setting.value),
                            wire_id=next_msg_id,
                            wire_id_set=next_id_set,
                            wire_dt=wire_dt.strftime("%d.%m.%Y %H:%M:%S"),
                            tsec_text=tsec_utc.strftime("%Y-%m-%d %H:%M:%S"),
                            ver_text=f"{secrets.randbelow(65_536):05d}",
                        )
                        setting_frame = rendered_setting.wire_frame
                        logger.debug(
                            "Setting frame to BOX: %s",
                            setting_frame.decode("utf-8", errors="replace"),
                        )
                        try:
                            box_writer.write(setting_frame)
                            await box_writer.drain()
                            self._capture_frame(setting_frame, "proxy_to_box", conn_id=conn_id, peer=peer_str)
                            logger.info(
                                "📤 Injected local Setting to BOX: %s:%s=%s",
                                setting.table,
                                setting.key,
                                setting.value,
                            )
                            self.twin_delivery.record_injected_box(
                                setting,
                                device_id,
                                session_id=audit_session_id,
                            )
                            withheld_chunks = True
                            continue
                        except (OSError, ConnectionResetError) as exc:
                            logger.error("Failed to inject Setting to BOX: %s", exc)

                forward_chunks.append(frame_bytes)

            if withheld_chunks:
                continue

            if forward_chunks:
                payload = b"".join(forward_chunks)
                try:
                    cloud_writer.write(payload)
                    await cloud_writer.drain()
                    self.frames_forwarded += len(forward_chunks)
                except (OSError, ConnectionResetError) as exc:
                    self.mode_manager.record_failure(reason=str(exc))
                    if self.mode_manager.is_offline():
                        if box_writer is not None:
                            offline_buf = bytearray(payload)
                            await self._handle_offline_frames(offline_buf, box_writer)
                    break
            elif data:
                try:
                    cloud_writer.write(data)
                    await cloud_writer.drain()
                except (OSError, ConnectionResetError) as exc:
                    self.mode_manager.record_failure(reason=str(exc))
                    if self.mode_manager.is_offline():
                        if box_writer is not None:
                            offline_buf = bytearray(data)
                            await self._handle_offline_frames(offline_buf, box_writer)
                    break

    async def _pipe_cloud_to_box(
        self,
        cloud_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        peer: tuple | None = None,
        session_id: str | None = None,
    ) -> None:
        """
        Čte data z cloudu a forwarduje do Boxu.
        """
        peer_str = f"{peer[0]}:{peer[1]}" if peer and len(peer) >= 2 else None
        conn_id = id(asyncio.current_task())
        buf = bytearray()
        while True:
            try:
                data = await cloud_reader.read(4096)
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            if not data:
                try:
                    can_write_eof = getattr(box_writer, "can_write_eof", None)
                    if callable(can_write_eof) and can_write_eof():
                        box_writer.write_eof()
                except (OSError, ConnectionResetError):
                    pass
                break

            try:
                box_writer.write(data)
                await box_writer.drain()
            except (OSError, ConnectionResetError):
                break

            buf.extend(data)
            while True:
                frame_bytes = extract_frame_from_buffer(buf)
                if frame_bytes is None:
                    break
                self._capture_frame(frame_bytes, "cloud_to_box", conn_id=conn_id, peer=peer_str)

                frame_text = frame_bytes.decode("utf-8", errors="replace")
                parsed_frame = parse_xml_frame(frame_text)
                table_name = self._effective_table_name(parsed_frame, frame_text)
                observed_id_set = _extract_id_set(frame_text)
                observed_msg_id = _extract_msg_id(frame_text)
                if self.twin_delivery is not None:
                    self.twin_delivery.observe_id_set(observed_id_set)
                    self.twin_delivery.observe_msg_id(observed_msg_id)

                if self.telemetry_collector is not None:
                    self.telemetry_collector.record_response(frame_text, source="cloud", conn_id=conn_id)
                    self.telemetry_collector.record_frame_direction("cloud_to_proxy")

                if self.twin_delivery is not None:
                    if (
                        table_name == "Setting"
                        or (
                            "<Reason>Setting</Reason>" in frame_text
                            and "<TblName>" in frame_text
                            and "<TblItem>" in frame_text
                            and "<NewValue>" in frame_text
                        )
                    ):
                        setting_table = str(parsed_frame.get("_table") or "")
                        setting_key = str(parsed_frame.get("TblItem") or "")
                        setting_value = parsed_frame.get("NewValue")
                        setting_device_id = str(parsed_frame.get("_device_id") or "")
                        if setting_table and setting_key and setting_value is not None and setting_device_id:
                            self.twin_delivery.begin_cloud_setting(
                                device_id=setting_device_id,
                                table=setting_table,
                                key=setting_key,
                                value=setting_value,
                                raw_text=frame_text,
                                msg_id=observed_msg_id or 0,
                                id_set=observed_id_set or 0,
                                confirm=str(parsed_frame.get("Confirm") or "New"),
                            )
                            logger.info(
                                "☁️ Cloud Setting detected, tracking inflight audit for %s:%s",
                                setting_table,
                                setting_key,
                            )
                        else:
                            self.twin_delivery.set_cloud_inflight()
                            logger.info("☁️ Cloud Setting detected, marking cloud inflight")
                    elif table_name == "END":
                        self.twin_delivery.clear_cloud_inflight()
                        logger.debug("☁️ Cloud END received, clearing cloud inflight")

                await self._handle_twin_frames(frame_bytes, box_writer, session_id=session_id)
                await self._process_frame(frame_bytes)

    async def _handle_twin_frames(
        self,
        frame_bytes: bytes,
        box_writer: asyncio.StreamWriter | None,
        session_id: str | None = None,
        run_isnewset_hook: bool = True,
    ) -> None:
        if not self.twin_delivery:
            return

        audit_session_id = session_id or ""
        frame_text = frame_bytes.decode("utf-8", errors="replace")
        parsed_frame = parse_xml_frame(frame_text)
        table_name = self._effective_table_name(parsed_frame, frame_text)

        if run_isnewset_hook and table_name == "IsNewSet" and box_writer is not None:
            await self._deliver_pending_for_isnewset(frame_text, box_writer)

        inflight_setting = self.twin_delivery.inflight_setting() if self.twin_delivery else None
        confirmed_published = False

        def _unpack_inflight():
            if inflight_setting is None:
                return None
            try:
                setting, device_id = inflight_setting
                return setting, device_id
            except Exception:
                return None

        parsed_ack = parse_box_ack(frame_bytes)
        if (
            parsed_ack
            and parsed_ack.get("result") == "ACK"
            and parsed_ack.get("table")
            and parsed_ack.get("todo")
        ):
            matched_inflight = False
            pair = _unpack_inflight()
            if pair is not None:
                setting, inflight_device_id = pair
                if (setting.table, setting.key) == (parsed_ack["table"], parsed_ack["todo"]):
                    matched_inflight = True
                    self.twin_delivery.record_ack_box_observed(
                        setting,
                        inflight_device_id,
                        session_id=audit_session_id,
                    )
            logger.info(
                "✅ BOX ACK received: %s:%s payload=%s",
                parsed_ack["table"],
                parsed_ack["todo"],
                frame_text,
            )
            if not matched_inflight:
                self.twin_delivery.acknowledge(
                    parsed_ack["table"],
                    parsed_ack["todo"],
                    session_id=session_id,
                )

        event_ack = parse_tbl_events_ack(parsed_frame)
        if event_ack and event_ack.get("table") and event_ack.get("key"):
            await self._publish_confirmed_setting(
                str(parsed_frame.get("_device_id") or ""),
                event_ack["table"],
                event_ack["key"],
                event_ack.get("value"),
            )
            confirmed_published = True
            logger.info(
                "✅ BOX ACK received (tbl_events): %s:%s payload=%s",
                event_ack["table"],
                event_ack["key"],
                frame_text,
            )
            cloud_pair = self.twin_delivery.match_cloud_tbl_events(
                str(parsed_frame.get("_device_id") or ""),
                event_ack["table"],
                event_ack["key"],
                event_ack.get("value"),
                session_id=audit_session_id,
            )
            if cloud_pair is None:
                pair = _unpack_inflight()
            else:
                pair = None
            if pair is not None:
                setting, inflight_device_id = pair
                if (setting.table, setting.key) == (event_ack["table"], event_ack["key"]):
                    self.twin_delivery.record_ack_tbl_events(
                        setting,
                        inflight_device_id,
                        confirmed_value=event_ack.get("value"),
                        session_id=audit_session_id,
                    )
            if cloud_pair is None:
                self.twin_delivery.acknowledge(
                    event_ack["table"],
                    event_ack["key"],
                    session_id=session_id,
                )

        if parsed_ack and parsed_ack.get("result") == "ACK" and parsed_ack.get("reason") == "Setting":
            cloud_pair = self.twin_delivery.mark_cloud_reason_setting(
                str(parsed_frame.get("_device_id") or ""),
                session_id=audit_session_id,
            )
            pair = None
            if cloud_pair is not None:
                setting, inflight_device_id = cloud_pair
                logger.info(
                    "✅ BOX ACK received (Reason=Setting), provisional inflight %s:%s payload=%s",
                    setting.table,
                    setting.key,
                    frame_text,
                )
            else:
                pair = _unpack_inflight()
            if cloud_pair is None and pair is not None:
                setting, inflight_device_id = pair
                if not confirmed_published:
                    await self._publish_confirmed_setting(
                        inflight_device_id,
                        setting.table,
                        setting.key,
                        setting.value,
                    )
                self.twin_delivery.record_ack_reason_setting(
                    setting,
                    inflight_device_id,
                    session_id=audit_session_id,
                )
                table, key = setting.table, setting.key
                logger.info(
                    "✅ BOX ACK received (Reason=Setting), acknowledging inflight %s:%s payload=%s",
                    table,
                    key,
                    frame_text,
                )
                self.twin_delivery.acknowledge(table, key, session_id=session_id)

        if parsed_ack and parsed_ack.get("result") == "NACK":
            pair = _unpack_inflight()
            if pair is not None:
                setting, inflight_device_id = pair
                self.twin_delivery.record_nack(
                    setting,
                    inflight_device_id,
                    session_id=audit_session_id,
                )
                logger.info(
                    "❌ BOX NACK received for inflight %s:%s payload=%s",
                    setting.table,
                    setting.key,
                    frame_text,
                )
                self.twin_delivery.acknowledge(
                    setting.table,
                    setting.key,
                    session_id=session_id,
                )

    async def _publish_confirmed_setting(
        self,
        device_id: str | None,
        table: str,
        key: str,
        value: Any,
    ) -> None:
        if self.on_confirmed_setting is None or not device_id:
            return
        try:
            await self.on_confirmed_setting(device_id, table, key, value)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Confirmed setting publish failed for %s:%s=%s: %s",
                table,
                key,
                value,
                exc,
            )

    async def _deliver_pending_for_isnewset(
        self,
        frame_text: str,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        if self.twin_delivery is None:
            return
        parsed_frame = parse_xml_frame(frame_text)
        device_id = str(parsed_frame.get("_device_id") or "")
        pending = await self.twin_delivery.deliver_pending(device_id)
        for setting in pending:
            id_set = self.twin_delivery.next_id_set()
            payload = self.twin_delivery.build_setting_xml(
                setting.table,
                setting.key,
                setting.value,
                device_id=device_id,
                id_set=id_set,
            )
            try:
                frame = build_frame(payload).encode("utf-8", errors="replace")
                box_writer.write(frame)
                await box_writer.drain()
                self.twin_delivery.record_injected_box(setting, device_id)
            except (OSError, ConnectionResetError):
                break

    async def _process_frame(self, frame_bytes: bytes) -> None:
        """Parsuje frame a volá callback."""
        self.frames_received += 1
        if not self.on_frame:
            return
        try:
            text = frame_bytes.decode("utf-8", errors="replace")
            parsed = parse_xml_frame(text)
            if parsed and not self._is_transport_frame(parsed):
                await self.on_frame(parsed)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Frame parse error: %s", exc)

    def _capture_frame(
        self,
        frame_bytes: bytes,
        direction: str,
        conn_id: int | None = None,
        peer: str | None = None,
        attempt_link: AttemptCaptureLink | None = None,
    ) -> None:
        if self.frame_capture is None:
            self._log_frame_payload(frame_bytes, direction, conn_id=conn_id, peer=peer)
            return
        try:
            raw = frame_bytes.decode("utf-8", errors="replace")
            parsed = parse_xml_frame(raw)
            device_id = str(parsed.get("_device_id") or "")
            table = self._effective_table_name(parsed, raw)
            self._log_frame_payload(frame_bytes, direction, conn_id=conn_id, peer=peer)
            self.frame_capture.capture(
                device_id=device_id or None,
                table=table or None,
                raw=raw,
                raw_bytes=frame_bytes,
                parsed=parsed,
                direction=direction,
                conn_id=conn_id,
                peer=peer,
                length=len(frame_bytes),
                attempt_link=attempt_link,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_capture_frame error: %s", exc)

    def _log_frame_payload(
        self,
        frame_bytes: bytes,
        direction: str,
        conn_id: int | None = None,
        peer: str | None = None,
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return

        payload = frame_bytes.decode("utf-8", errors="replace")
        parsed = parse_xml_frame(payload)
        table = self._effective_table_name(parsed, payload)
        device_id = str(parsed.get("_device_id") or "")

        logger.debug(
            "📦 FRAME direction=%s table=%s device_id=%s peer=%s conn_id=%s len=%d payload=%s",
            direction,
            table or "unknown",
            device_id or "unknown",
            peer or "unknown",
            conn_id,
            len(frame_bytes),
            payload,
        )

        if logger.isEnabledFor(TRACE_LEVEL):
            logger.log(
                TRACE_LEVEL,
                "📦 FRAME RAW direction=%s table=%s bytes_hex=%s",
                direction,
                table or "unknown",
                frame_bytes.hex(),
            )

    @staticmethod
    def _effective_table_name(parsed: dict[str, Any], payload: str) -> str:
        result = str(parsed.get("Result") or "")
        if result in POLL_RESULT_VALUES | TRANSPORT_RESULT_VALUES:
            return result
        return str(parsed.get("_table") or infer_table_name(payload) or "")

    @staticmethod
    def _is_transport_frame(parsed: dict[str, Any]) -> bool:
        result = str(parsed.get("Result") or "")
        if result in TRANSPORT_RESULT_VALUES:
            return True

        if result in POLL_RESULT_VALUES:
            publishable_keys = [
                key
                for key in parsed
                if not key.startswith("_") and key not in TRANSPORT_METADATA_KEYS
            ]
            return not publishable_keys

        keys = {key for key in parsed if not key.startswith("_")}
        if {"TblItem", "NewValue"}.issubset(keys) and keys & {
            "Confirm",
            "ID",
            "ID_Server",
            "TSec",
            "mytimediff",
        }:
            return True

        return False

    async def _pipe_box_offline(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        peer: tuple,
        session_id: str | None = None,
    ) -> None:
        """Handle Box connection in offline mode - send local ACKs."""
        logger.info("📴 OFFLINE mode: handling Box connection from %s:%s (session=%s)", *peer[:2], session_id)
        buf = bytearray()
        try:
            while True:
                data = await box_reader.read(4096)
                if not data:
                    break
                buf.extend(data)
                await self._handle_offline_frames(buf, box_writer)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass
        finally:
            box_writer.close()
            try:
                await box_writer.wait_closed()
            except Exception as exc:  # noqa: BLE001
                logger.debug("wait_closed error in offline pipe: %s", exc)
            logger.info("🔌 BOX odpojen (offline): %s:%s", *peer[:2])

    async def _handle_offline_frames(
        self,
        buf: bytearray,
        box_writer: asyncio.StreamWriter,
        session_id: str | None = None,
    ) -> None:
        """Process frames from buffer and send local ACKs."""
        while True:
            frame_bytes = extract_frame_from_buffer(buf)
            if frame_bytes is None:
                break
            # Parse frame to get table name
            try:
                text = frame_bytes.decode("utf-8", errors="replace")
                table_name = infer_table_name(text) or ""
            except Exception:  # noqa: BLE001
                table_name = ""
            # Build and send local ACK
            ack_frame = build_local_ack(table_name)
            try:
                box_writer.write(ack_frame)
                await box_writer.drain()
                logger.debug("📤 Sent local ACK for %s", table_name or "unknown")
            except (OSError, ConnectionResetError):
                break
            await self._handle_twin_frames(frame_bytes, box_writer, session_id=session_id)
            # Process frame for MQTT publishing
            await self._process_frame(frame_bytes)
