"""Single serialized semantic write boundary for the BOX socket."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from capture.frame_capture import AttemptCaptureLink
from proxy.dialog import DialogStateError
from twin.state import (
    ActiveLocalAttempt,
    AttemptWriteOutcome,
    AttemptWriteResult,
)

logger = logging.getLogger(__name__)


class BoxWritePurpose(str, Enum):
    """Semantic origin of one exact frame written toward the BOX."""

    CLOUD_FORWARD = "cloud_forward"
    LOCAL_SETTING = "local_setting"
    LOCAL_GETACTUAL = "local_getactual"
    OFFLINE_RESPONSE = "offline_response"
    DEFERRED_END = "deferred_end"


class BoxWriteOutcome(str, Enum):
    """Transport certainty after exactly one writer invocation attempt."""

    DRAINED = "drained"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BoxWriteResult:
    """Explicit result of one generic BOX frame write."""

    outcome: BoxWriteOutcome
    started_at_ms: int
    drain_completed_at_ms: int | None
    error_text: str | None


_LOCAL_OWNER_PURPOSES = frozenset(
    {BoxWritePurpose.LOCAL_SETTING, BoxWritePurpose.DEFERRED_END}
)


class SerializedBoxWriter:
    """Serialize bytes and enforce one dialogue's semantic ownership."""

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        *,
        clock_ms: Callable[[], int],
        on_invoked: Callable[
            [bytes, BoxWritePurpose, AttemptCaptureLink | None], None
        ]
        | None = None,
    ) -> None:
        self._writer = writer
        self._clock_ms = clock_ms
        self._on_invoked = on_invoked
        self._write_lock = asyncio.Lock()
        self._owner_changed = asyncio.Condition(self._write_lock)
        self._dialogue_owner: str | None = None
        self._invocation_count = 0

    @property
    def invocation_count(self) -> int:
        """Return attempted underlying writer calls, including failures."""
        return self._invocation_count

    async def acquire_dialogue(self, session_id: str) -> None:
        """Wait for and claim exclusive semantic output ownership."""
        _require_session_id(session_id)
        async with self._owner_changed:
            await self._owner_changed.wait_for(
                lambda: self._dialogue_owner in {None, session_id}
            )
            self._dialogue_owner = session_id

    async def release_dialogue(self, session_id: str) -> None:
        """Release exact ownership; repeating the completed release is safe."""
        _require_session_id(session_id)
        async with self._owner_changed:
            if self._dialogue_owner is None:
                return
            if self._dialogue_owner != session_id:
                raise DialogStateError(
                    "only the current dialogue owner may release ownership"
                )
            self._dialogue_owner = None
            self._owner_changed.notify_all()

    async def write_frame(
        self,
        frame: bytes,
        *,
        purpose: BoxWritePurpose,
        owner_session_id: str | None = None,
    ) -> BoxWriteResult:
        """Invoke and drain one exact generic frame without byte interleaving."""
        _require_frame(frame)
        if not isinstance(purpose, BoxWritePurpose):
            raise TypeError("purpose must be a BoxWritePurpose")
        async with self._owner_changed:
            await self._authorize_or_wait(purpose, owner_session_id)
            return await self._write_locked(
                frame,
                purpose=purpose,
                attempt_link=None,
                before_write=None,
            )

    async def write_attempt(
        self,
        attempt: ActiveLocalAttempt,
        *,
        before_write: Callable[[], Awaitable[None]],
    ) -> AttemptWriteResult:
        """Commit write-start immediately before one local Setting invocation."""
        if not isinstance(attempt, ActiveLocalAttempt):
            raise TypeError("attempt must be an ActiveLocalAttempt")
        link = AttemptCaptureLink(
            attempt.command_id,
            attempt.audit_id,
            attempt.attempt_number,
        )
        async with self._owner_changed:
            await self._authorize_or_wait(
                BoxWritePurpose.LOCAL_SETTING, attempt.session_id
            )
            result = await self._write_locked(
                attempt.wire_frame,
                purpose=BoxWritePurpose.LOCAL_SETTING,
                attempt_link=link,
                before_write=before_write,
            )
        attempt_outcome = (
            AttemptWriteOutcome.UNKNOWN
            if result.outcome is BoxWriteOutcome.FAILED
            else AttemptWriteOutcome(result.outcome.value)
        )
        return AttemptWriteResult(
            outcome=attempt_outcome,
            started_at_ms=result.started_at_ms,
            drain_completed_at_ms=result.drain_completed_at_ms,
            error_text=result.error_text,
        )

    async def _authorize_or_wait(
        self,
        purpose: BoxWritePurpose,
        owner_session_id: str | None,
    ) -> None:
        if purpose in _LOCAL_OWNER_PURPOSES:
            if owner_session_id is None:
                raise DialogStateError("local write requires a dialogue owner")
            if self._dialogue_owner != owner_session_id:
                raise DialogStateError(
                    "local write does not belong to the current dialogue owner"
                )
            return
        if (
            purpose is BoxWritePurpose.OFFLINE_RESPONSE
            and owner_session_id is not None
        ):
            if self._dialogue_owner != owner_session_id:
                raise DialogStateError(
                    "offline response does not belong to the current dialogue owner"
                )
            return
        await self._owner_changed.wait_for(
            lambda: self._dialogue_owner is None
        )

    async def _write_locked(
        self,
        frame: bytes,
        *,
        purpose: BoxWritePurpose,
        attempt_link: AttemptCaptureLink | None,
        before_write: Callable[[], Awaitable[None]] | None,
    ) -> BoxWriteResult:
        if before_write is not None:
            await before_write()
        started_at_ms = self._clock_ms()

        write_error: Exception | None = None
        self._invocation_count += 1
        try:
            self._writer.write(frame)
        except Exception as error:  # noqa: BLE001
            write_error = error
        self._observe_invocation(frame, purpose, attempt_link)

        if write_error is not None:
            return BoxWriteResult(
                outcome=BoxWriteOutcome.FAILED,
                started_at_ms=started_at_ms,
                drain_completed_at_ms=None,
                error_text=_error_text(write_error),
            )

        try:
            await self._writer.drain()
        except Exception as error:  # noqa: BLE001
            return BoxWriteResult(
                outcome=BoxWriteOutcome.UNKNOWN,
                started_at_ms=started_at_ms,
                drain_completed_at_ms=None,
                error_text=_error_text(error),
            )
        return BoxWriteResult(
            outcome=BoxWriteOutcome.DRAINED,
            started_at_ms=started_at_ms,
            drain_completed_at_ms=self._clock_ms(),
            error_text=None,
        )

    def _observe_invocation(
        self,
        frame: bytes,
        purpose: BoxWritePurpose,
        attempt_link: AttemptCaptureLink | None,
    ) -> None:
        if self._on_invoked is None:
            return
        try:
            self._on_invoked(frame, purpose, attempt_link)
        except Exception as error:  # noqa: BLE001
            logger.debug("BOX write observer failed: %s", error)


def _require_session_id(session_id: object) -> None:
    if type(session_id) is not str or not session_id:
        raise ValueError("session_id must be a non-empty string")


def _require_frame(frame: object) -> None:
    if type(frame) is not bytes:
        raise TypeError("frame must be exact bytes")


def _error_text(error: Exception) -> str:
    return str(error) or type(error).__name__
