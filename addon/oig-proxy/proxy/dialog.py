"""Connection-local state for cloud-first Setting dialogues."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, FrozenInstanceError
from enum import Enum

from protocol.frame import AssembledFrame
from twin.state import ActiveLocalAttempt, DeliveryTrigger


class SessionRoute(str, Enum):
    """Route selected once for the lifetime of a proxy connection."""

    ONLINE = "online"
    OFFLINE = "offline"


class RequestKind(str, Enum):
    """Cloud response shape expected for one forwarded BOX request."""

    IS_NEW_SET = "is_new_set"
    SINGLE_RESPONSE = "single_response"


class CyclePhase(str, Enum):
    """Current phase of the FIFO-head cloud dialogue."""

    WAITING_CLOUD = "waiting_cloud"
    WAITING_BOX_CLOUD_ACK = "waiting_box_cloud_ack"
    LOCAL_AWAITING_ACK = "local_awaiting_ack"
    TAINTED = "tainted"


class DialogStateError(RuntimeError):
    """Raised when a frame would violate dialogue ordering or ownership."""


class HeldFrameOverflow(DialogStateError):
    """Raised before a held-frame queue exceeds its byte limit."""


@dataclass(slots=True)
class ResponseExpectation:
    """One forwarded request awaiting its sequential cloud response."""

    sequence: int
    kind: RequestKind
    request_raw: bytes
    opened_at_monotonic: float
    deadline_monotonic: float | None
    phase: CyclePhase
    cloud_setting_count: int = 0


class BoundedFrameQueue:
    """FIFO of exact assembled frames with an atomic byte bound."""

    def __init__(self, *, max_bytes: int = 1_048_576) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be an integer")
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        self._max_bytes = max_bytes
        self._frames: deque[AssembledFrame] = deque()
        self._byte_count = 0

    def append(self, frame: AssembledFrame) -> None:
        """Append one exact frame unless it would cross the queue limit."""
        if not isinstance(frame, AssembledFrame):
            raise TypeError("frame must be an AssembledFrame")
        new_byte_count = self._byte_count + len(frame.raw)
        if new_byte_count > self._max_bytes:
            raise HeldFrameOverflow(
                f"held frame queue exceeds {self._max_bytes} bytes"
            )
        self._frames.append(frame)
        self._byte_count = new_byte_count

    def drain(self) -> tuple[AssembledFrame, ...]:
        """Return all frames in arrival order and empty the queue."""
        frames = tuple(self._frames)
        self.clear()
        return frames

    def clear(self) -> None:
        """Discard every held frame and reset accounting."""
        self._frames.clear()
        self._byte_count = 0

    @property
    def byte_count(self) -> int:
        """Return the exact total bytes currently held."""
        return self._byte_count


@dataclass(slots=True)
class SettingDialog:
    """State that exists only for one BOX/cloud TCP connection pair."""

    session_id: str
    route: SessionRoute
    bound_device_id: str | None = None
    deferred_end: bytes | None = None
    active_attempt: ActiveLocalAttempt | None = None
    _expectations: deque[ResponseExpectation] = field(
        default_factory=deque, init=False, repr=False
    )
    _held_box: BoundedFrameQueue = field(
        default_factory=BoundedFrameQueue, init=False, repr=False
    )
    _held_cloud: BoundedFrameQueue = field(
        default_factory=BoundedFrameQueue, init=False, repr=False
    )
    _next_sequence: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(self.route, SessionRoute):
            raise TypeError("route must be a SessionRoute")

    def __setattr__(self, name: str, value: object) -> None:
        if name == "route" and hasattr(self, "route"):
            raise FrozenInstanceError("route cannot change after dialog creation")
        object.__setattr__(self, name, value)

    @property
    def expectation_count(self) -> int:
        """Return the number of forwarded requests still awaiting closure."""
        return len(self._expectations)

    def bind_device(self, device_id: str) -> bool:
        """Bind the first exact device identity and reject later changes."""
        if type(device_id) is not str or not device_id:
            raise ValueError("device_id must be a non-empty string")
        if self.bound_device_id is None:
            self.bound_device_id = device_id
            return True
        if self.bound_device_id != device_id:
            raise DialogStateError("connection device identity cannot change")
        return False

    def open_forwarded_request(
        self,
        *,
        kind: RequestKind,
        request_raw: bytes,
        opened_at_monotonic: float,
        cloud_timeout_s: float | None,
    ) -> ResponseExpectation:
        """Append one response token before forwarding its request."""
        if not isinstance(kind, RequestKind):
            raise TypeError("kind must be a RequestKind")
        _require_bytes(request_raw, "request_raw")
        deadline = (
            None
            if cloud_timeout_s is None
            else opened_at_monotonic + cloud_timeout_s
        )
        expectation = ResponseExpectation(
            sequence=self._next_sequence,
            kind=kind,
            request_raw=request_raw,
            opened_at_monotonic=opened_at_monotonic,
            deadline_monotonic=deadline,
            phase=CyclePhase.WAITING_CLOUD,
        )
        self._next_sequence += 1
        self._expectations.append(expectation)
        return expectation

    def current_expectation(self) -> ResponseExpectation | None:
        """Return only the FIFO head eligible for response correlation."""
        return self._expectations[0] if self._expectations else None

    def close_current_expectation(self) -> ResponseExpectation:
        """Close and return only the FIFO-head non-local response token."""
        self._require_head()
        if self.active_attempt is not None:
            raise DialogStateError("cannot close an active local attempt")
        if self.deferred_end is not None:
            raise DialogStateError("deferred END requires explicit cycle closure")
        return self._expectations.popleft()

    def mark_cloud_setting(self, raw: bytes) -> None:
        """Mark one cloud Setting in the current cloud-owned batch."""
        _require_bytes(raw, "raw")
        expectation = self._require_head()
        if expectation.kind is not RequestKind.IS_NEW_SET:
            raise DialogStateError("cloud Setting is not part of an IsNewSet cycle")
        if expectation.phase is CyclePhase.TAINTED:
            raise DialogStateError("current cycle is tainted")
        if expectation.phase is not CyclePhase.WAITING_CLOUD:
            raise DialogStateError("cloud Setting is out of sequence")
        expectation.phase = CyclePhase.WAITING_BOX_CLOUD_ACK
        expectation.cloud_setting_count += 1

    def mark_cloud_setting_ack_forwarded(self, raw: bytes) -> None:
        """Continue the same request token after forwarding its BOX ACK."""
        _require_bytes(raw, "raw")
        expectation = self._require_head()
        if expectation.phase is CyclePhase.TAINTED:
            raise DialogStateError("current cycle is tainted")
        if expectation.phase is not CyclePhase.WAITING_BOX_CLOUD_ACK:
            raise DialogStateError("cloud Setting ACK is out of sequence")
        expectation.phase = CyclePhase.WAITING_CLOUD

    def defer_correlated_terminal_end(self, raw: bytes) -> DeliveryTrigger:
        """Retain an exact eligible terminal END for local substitution."""
        _require_bytes(raw, "raw")
        expectation = self._require_head()
        if expectation.phase is CyclePhase.TAINTED:
            raise DialogStateError("current cycle is tainted")
        if expectation.kind is not RequestKind.IS_NEW_SET:
            raise DialogStateError("current response is not an IsNewSet cycle")
        if expectation.phase is not CyclePhase.WAITING_CLOUD:
            raise DialogStateError("terminal END is out of sequence")
        if self.deferred_end is not None:
            raise DialogStateError("terminal END is already deferred")
        self.deferred_end = raw
        return DeliveryTrigger.CORRELATED_CLOUD_END

    def begin_local_attempt(self, attempt: ActiveLocalAttempt) -> None:
        """Attach the one local attempt selected for this dialogue."""
        self._validate_attempt(attempt)
        if self.active_attempt is not None:
            raise DialogStateError("a local attempt is already active")
        if self.deferred_end is None:
            raise DialogStateError("local attempt requires a deferred END")
        expectation = self._require_head()
        if expectation.phase is CyclePhase.TAINTED:
            raise DialogStateError("current cycle is tainted")
        if expectation.kind is not RequestKind.IS_NEW_SET:
            raise DialogStateError("local attempt requires an IsNewSet cycle")
        self.active_attempt = attempt
        expectation.phase = CyclePhase.LOCAL_AWAITING_ACK

    def replace_local_attempt(self, attempt: ActiveLocalAttempt) -> None:
        """Replace an ACKed attempt with its same-dialogue successor."""
        self._validate_attempt(attempt)
        if self.active_attempt is None:
            raise DialogStateError("no local attempt is active")
        if self.deferred_end is None:
            raise DialogStateError("local attempt requires a deferred END")
        expectation = self._require_head()
        if expectation.phase is CyclePhase.TAINTED:
            raise DialogStateError("current cycle is tainted")
        self.active_attempt = attempt
        expectation.phase = CyclePhase.LOCAL_AWAITING_ACK

    def take_deferred_end_and_close_cycle(self) -> bytes:
        """Return the exact END and erase all state for the FIFO-head cycle."""
        if self.deferred_end is None:
            raise DialogStateError("no terminal END is deferred")
        self._require_head()
        raw = self.deferred_end
        self.deferred_end = None
        self.active_attempt = None
        self._expectations.popleft()
        return raw

    def taint_current_cycle(self) -> None:
        """Permanently prohibit substitution on the FIFO-head token."""
        self._require_head().phase = CyclePhase.TAINTED

    def hold_box_frame(self, frame: AssembledFrame) -> None:
        """Hold later BOX input without mixing it with cloud output."""
        self._held_box.append(frame)

    def hold_cloud_frame(self, frame: AssembledFrame) -> None:
        """Hold later cloud output without mixing it with BOX input."""
        self._held_cloud.append(frame)

    def drain_held_box(self) -> tuple[AssembledFrame, ...]:
        """Drain held BOX frames in their original order."""
        return self._held_box.drain()

    def drain_held_cloud(self) -> tuple[AssembledFrame, ...]:
        """Drain held cloud frames in their original order."""
        return self._held_cloud.drain()

    def is_cloud_deadline_expired(self, now_monotonic: float) -> bool:
        """Check the immutable deadline opened with the FIFO-head request."""
        expectation = self.current_expectation()
        return bool(
            expectation is not None
            and expectation.deadline_monotonic is not None
            and now_monotonic >= expectation.deadline_monotonic
        )

    def clear_socket_state(self) -> None:
        """Erase every mutable value that is valid only for this socket."""
        self.bound_device_id = None
        self.deferred_end = None
        self.active_attempt = None
        self._expectations.clear()
        self._held_box.clear()
        self._held_cloud.clear()

    def _require_head(self) -> ResponseExpectation:
        if not self._expectations:
            raise DialogStateError("no forwarded request is awaiting a response")
        return self._expectations[0]

    def _validate_attempt(self, attempt: ActiveLocalAttempt) -> None:
        if not isinstance(attempt, ActiveLocalAttempt):
            raise TypeError("attempt must be an ActiveLocalAttempt")
        if attempt.session_id != self.session_id:
            raise DialogStateError("local attempt belongs to a different session")
        if (
            self.bound_device_id is not None
            and attempt.device_id != self.bound_device_id
        ):
            raise DialogStateError("local attempt belongs to a different device")


def _require_bytes(value: object, name: str) -> None:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be exact bytes")
