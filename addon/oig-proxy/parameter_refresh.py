"""Timer-only admission of parameter refreshes, independent of MQTT recovery."""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Callable
import uuid

_LOGGER = logging.getLogger(__name__)
_BOOT_GRACE_S = 300.0
_RETRY_COOLDOWN_S = 300.0
_PENDING_TIMEOUT_S = 180.0
_OPERATION_DEBOUNCE_S = 60.0
_FUTURE_SKEW_S = 5.0
_EXCLUDED_OPERATIONS = frozenset({
    ("tbl_box_prms", "MODE"), ("tbl_box_prms", "MODE1"),
    ("tbl_box_prm2", "APP"), ("proxy_control", "PROXY_MODE"),
})


def _timestamp(value: object) -> bool:
    """Reject JSON booleans and nonfinite or negative scheduler timestamps."""
    return (
        isinstance(value, (float, int)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    )


class ParameterRefreshScheduler:  # pylint: disable=too-many-instance-attributes
    """Persist timestamps only; the owner admits requests and reports real SA data.

    Call all methods from the same owner event loop. ``request`` must perform
    immediate admission only; it must not block waiting for a device response.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self, path: Path, device_id: str, *, request: Callable[[str], bool],
        idle: Callable[[], bool], interval_s: float = 86400,
        clock: Callable[[], float] = time.time,
        after_operations: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        if not _timestamp(interval_s) or interval_s <= 0:
            raise ValueError("interval_s must be finite and positive")
        self._path = Path(path)
        self._device_id = device_id
        self._request = request
        self._idle = idle
        self._clock = clock
        self._interval_s = interval_s
        self._started_at = self._now()
        self._baseline_at = self._started_at
        self._last_confirmed_at: float | None = None
        self._last_attempt_at: float | None = None
        self._pending_at: float | None = None
        self._persistence_blocked = False
        self._operations: dict[tuple[str, str], float] = {}
        self._operation_at: float | None = None
        self._after_operations = frozenset(
            (table.strip().lower(), key.strip().upper()) for table, key in after_operations
        )
        self._load()

    def _now(self) -> float:
        now = self._clock()
        if not _timestamp(now):
            raise ValueError("clock must return a finite nonnegative timestamp")
        return float(now)

    def _load(self) -> None:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._persist()
            return
        except (OSError, ValueError, UnicodeError):
            self._preserve_invalid()
            return
        if not self._valid_marker(document):
            self._preserve_invalid()
            return
        self._baseline_at = document["baseline_at"]
        self._last_confirmed_at = document["last_confirmed_at"]
        self._last_attempt_at = document["last_attempt_at"]

    def _valid_marker(self, document: object) -> bool:
        if not isinstance(document, dict) or set(document) != {
            "version", "device_id", "baseline_at", "last_confirmed_at", "last_attempt_at"
        }:
            return False
        if (
            not isinstance(document["version"], int)
            or isinstance(document["version"], bool) or document["version"] != 1
        ):
            return False
        if document["device_id"] != self._device_id:
            return False
        baseline = document["baseline_at"]
        if not _timestamp(baseline):
            return False
        for field in ("baseline_at", "last_confirmed_at", "last_attempt_at"):
            value = document[field]
            if value is None and field != "baseline_at":
                continue
            if not _timestamp(value) or not baseline <= value <= self._started_at + _FUTURE_SKEW_S:
                return False
        return True

    def _preserve_invalid(self) -> None:
        """Keep bad bytes before replacing the active marker with a new baseline."""
        evidence = self._path.with_name(f"{self._path.name}.invalid-{uuid.uuid4().hex}")
        try:
            os.replace(self._path, evidence)
        except OSError:
            self._persistence_blocked = True
            _LOGGER.warning("Cannot preserve invalid parameter-refresh marker", exc_info=True)
            return
        self._persist()

    def _persist(self) -> bool:
        if self._persistence_blocked:
            return False
        temporary: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._path.parent,
                prefix=f".{self._path.name}.", delete=False,
            ) as stream:
                temporary = stream.name
                json.dump({
                    "version": 1, "device_id": self._device_id,
                    "baseline_at": self._baseline_at,
                    "last_confirmed_at": self._last_confirmed_at,
                    "last_attempt_at": self._last_attempt_at,
                }, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            return True
        except OSError:
            _LOGGER.warning("Cannot persist parameter-refresh marker", exc_info=True)
            return False
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def poll(self) -> bool:
        """Admit due work after grace/cooldown and only while the owner is idle."""
        now = self._now()
        if now - self._started_at < _BOOT_GRACE_S:
            return False
        if self._pending_at is not None:
            if now - self._pending_at < _PENDING_TIMEOUT_S:
                return False
            self._pending_at = None
        if self._last_attempt_at is not None and now - self._last_attempt_at < _RETRY_COOLDOWN_S:
            return False
        baseline = (
            self._last_confirmed_at if self._last_confirmed_at is not None else self._baseline_at
        )
        daily_due = now - baseline >= self._interval_s
        operation_due = (
            self._operation_at is not None and now - self._operation_at >= _OPERATION_DEBOUNCE_S
        )
        if not (daily_due or operation_due) or not self._idle():
            return False
        # Durable cooldown precedes admission: crashes never replay a command intent.
        self._last_attempt_at = now
        if not self._persist():
            return False
        self._pending_at = now
        try:
            admitted = self._request("daily" if daily_due else "after_operation")
        except Exception:  # pylint: disable=broad-exception-caught
            # An arbitrary owner admission callback must not kill its timer.
            _LOGGER.warning("Parameter-refresh admission failed", exc_info=True)
            admitted = False
        if not admitted:
            self._pending_at = None
        return bool(admitted)

    def note_operation(self, table: str, key: str) -> None:
        """Debounce only explicitly configured, confirmed exceptional operations."""
        operation = (table.strip().lower(), key.strip().upper())
        if (
            operation[1] == "SA" or operation in _EXCLUDED_OPERATIONS
            or operation not in self._after_operations or operation in self._operations
        ):
            return
        self._operation_at = self._now()
        self._operations[operation] = self._operation_at

    def note_sa_confirmed(self, at: float) -> None:
        """Accept fresh actual SA confirmation without issuing any request."""
        now = self._now()
        previous = (
            self._last_confirmed_at if self._last_confirmed_at is not None else self._baseline_at
        )
        if (
            not _timestamp(at) or at > now + _FUTURE_SKEW_S or at <= previous
            or (self._last_attempt_at is not None and at < self._last_attempt_at)
        ):
            return
        self._last_confirmed_at = float(at)
        self._pending_at = None
        self._operations = {
            operation: when for operation, when in self._operations.items() if when > at
        }
        self._operation_at = max(self._operations.values(), default=None)
        self._persist()
