"""Parameter-refresh scheduling from time and native BOX readback."""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable
import uuid

from protocol.parser import parse_parameter_change, same_setting_value
from protocol.ver_profile import recent_protocol_timestamp
from sensor.confirmed_store import observation_table

_LOGGER = logging.getLogger(__name__)
_BOOT_GRACE_S = 300.0
_RETRY_COOLDOWN_S = 300.0
_PENDING_TIMEOUT_S = 180.0
_OPERATION_DEBOUNCE_S = 60.0
_FUTURE_SKEW_S = 5.0
_READBACK_WAIT_S = 60.0
SA_READBACK_RESPONSE_S = 120.0
_NATIVE_TABLE_PERIOD_S = {"tbl_batt_prms": 300.0, "tbl_invertor_prms": 600.0}
_SA_ONLY_TABLES = frozenset({"tbl_boiler_prms"})
# Passive confirmation has its own evidence age when no SA is sent.
PARAMETER_READBACK_EVIDENCE_MAX_AGE_S = 150.0
_COMMAND_ONLY_FIELDS = frozenset({
    ("tbl_box_prms", "SA"), ("tbl_box_prms", "RQRESET"), ("tbl_box_prm2", "APP"),
})
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


@dataclass
class _ExpectedField:
    value: str | int
    event_at: float
    changed_at: float
    due: float | None
    confirmed: bool = False
    response_deadline: float | None = None


class ParameterReadbackRefresh:
    """Request one full readback after a native change or a matched BOX acknowledgment."""

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._expected: dict[tuple[str, str], _ExpectedField] = {}
        self._tables: dict[str, tuple[float, dict[str, tuple[float, str]]]] = {}
        self._actual_aliases: dict[tuple[str, str], tuple[float, str]] = {}
        self._box_clock_epoch: int | None = None

    def _accept_box_epoch(self, epoch: int) -> bool:
        if self._box_clock_epoch is not None and epoch < self._box_clock_epoch:
            return False
        if epoch != self._box_clock_epoch:
            for (table, key), pending in self._expected.items():
                self._report_outcome(table, key, pending, "clock_changed")
            self._expected.clear()
            self._tables.clear()
            self._actual_aliases.clear()
            self._box_clock_epoch = epoch
        return True

    def note_ack(self, table: str, key: str, value: str | int, *,
                 box_time: tuple[int, float | None]) -> None:
        """Expect native readback of an already matched setting, without confirming its value."""
        epoch, stamp = box_time
        if self._accept_box_epoch(epoch):
            self.poll_timeouts()
            # Undated ACKs have arrival time at the native table's second resolution.
            self._note_change(
                (table, key), value, stamp if stamp is not None else math.floor(self._wall_clock()),
                acknowledged=True,
            )

    def observe(
        self, fields: dict[str, str], *, box_time: tuple[int, float | None] | None = None,
    ) -> None:
        """Observe native data, optionally in the shared BOX clock epoch."""
        self._observe(fields, box_time, None)

    def observe_with_aliases(
        self, fields: dict[str, str], *, box_time: tuple[int, float | None] | None,
        actual_aliases: dict[tuple[str, str], str] | None,
    ) -> None:
        """Observe native data and any caller-qualified aliases from a physical Actual table."""
        self._observe(fields, box_time, actual_aliases)

    def _observe(
        self, fields: dict[str, str], box_time: tuple[int, float | None] | None,
        actual_aliases: dict[tuple[str, str], str] | None,
    ) -> None:
        if box_time is not None:
            epoch, stamp = box_time
            if not self._accept_box_epoch(epoch):
                return
        else:
            stamp = recent_protocol_timestamp(fields.get("DT", ""), self._wall_clock())
        self.poll_timeouts()
        table = fields.get("TblName", "")
        if stamp is None or fields.get("Reason") != "Table":
            return
        if table == "tbl_events":
            change = parse_parameter_change(fields.get("Type", ""), fields.get("Content", ""))
            if change is not None:
                changed_table, key, value = change
                self._note_change((changed_table, key), value, stamp)
        elif observation_table(table):
            self._observe_table(table, fields, stamp, actual_aliases)

    def _observe_table(
        self, table: str, fields: dict[str, str], stamp: float,
        actual_aliases: dict[tuple[str, str], str] | None,
    ) -> None:
        previous = self._tables.get(table)
        if previous is not None and stamp < previous[0]:
            return
        snapshot = previous[1] if previous is not None else {}
        snapshot.update({key: (stamp, value) for key, value in fields.items()})
        self._tables[table] = stamp, snapshot
        for identity in self._expected:
            if identity[0] == table and identity[1] in fields:
                self._confirm_readback(identity)

        if table == "tbl_actual" and actual_aliases and "Result" not in fields:
            for identity, value in actual_aliases.items():
                latest = self._latest_readback(identity)
                if latest is None or stamp > latest[0]:
                    self._actual_aliases[identity] = stamp, value
                    self._confirm_readback(identity)

    def _latest_readback(self, identity: tuple[str, str]) -> tuple[float, str] | None:
        table, key = identity
        native = self._tables.get(table)
        direct = native[1].get(key) if native is not None else None
        alias = self._actual_aliases.get(identity)
        if direct is not None and (alias is None or direct[0] >= alias[0]):
            return direct
        return alias

    def _confirm_readback(self, identity: tuple[str, str]) -> None:
        expected = self._expected.get(identity)
        seen = self._latest_readback(identity)
        if (expected is not None and seen is not None and seen[0] >= expected.changed_at
                and same_setting_value(expected.value, seen[1])):
            expected.due = None
            expected.confirmed = True
            self._report_outcome(*identity, expected, "confirmed")

    def _schedule_readback(self, table: str, stamp: float) -> float:
        now = self._clock()
        if table in _SA_ONLY_TABLES:
            return now
        period = _NATIVE_TABLE_PERIOD_S.get(table)
        if period is None:
            return now + _READBACK_WAIT_S
        previous = self._tables.get(table)
        if previous is None:
            return now + period + _READBACK_WAIT_S
        last_sample = previous[0]
        next_sample = last_sample + (max(0, (stamp - last_sample) // period) + 1) * period
        return now + max(0, next_sample + _READBACK_WAIT_S - self._wall_clock())

    def _note_change(
        self, identity: tuple[str, str], value: str | int, stamp: float, *,
        acknowledged: bool = False,
    ) -> None:
        table, key = identity
        if (not observation_table(table) or identity in _COMMAND_ONLY_FIELDS
                or key.upper().startswith("ERR_")):
            return
        previous = self._expected.get(identity)
        seen = self._latest_readback(identity)
        if previous is not None:
            if stamp <= previous.event_at:
                return
            if same_setting_value(previous.value, value):
                intervening_change = (
                    previous.confirmed and seen is not None and seen[0] > previous.changed_at
                    and not same_setting_value(value, seen[1])
                )
                new_write_after_readback = (
                    acknowledged and previous.due is None and previous.response_deadline is None
                )
                if not intervening_change and not new_write_after_readback:
                    previous.event_at = stamp
                    return
            self._report_outcome(table, key, previous, "superseded")
        confirmed = seen is not None and seen[0] >= stamp and same_setting_value(value, seen[1])
        self._expected[identity] = _ExpectedField(
            value=value, event_at=stamp, changed_at=stamp,
            due=None if confirmed else self._schedule_readback(table, stamp), confirmed=confirmed,
        )

    def is_due(self) -> bool:
        """Return whether a readback is due and no sent batch still awaits data."""
        return not self.has_active_batch() and self._has_due_field()

    def _has_due_field(self) -> bool:
        now = self._clock()
        return any(field.due is not None and field.due <= now for field in self._expected.values())

    def has_active_batch(self) -> bool:
        """Return whether a wire-sent SA still has unresolved response expectations."""
        self.poll_timeouts()
        return any(field.response_deadline is not None for field in self._expected.values())

    def mark_sent(self) -> bool:
        """Batch outstanding fields on wire-send and keep earlier deadlines intact."""
        self.poll_timeouts()
        deadline = self._clock() + SA_READBACK_RESPONSE_S
        claimed = False
        for expected in self._expected.values():
            if expected.due is not None:
                expected.due = None
                expected.response_deadline = deadline
                claimed = True
        return claimed

    def response_deadline_for(
        self, table: str, key: str, value: str | int, *, box_epoch: int,
    ) -> float | None:
        """Return the active sent-SA deadline for this value and BOX clock epoch."""
        expected = self._expected.get((table, key))
        if (
            box_epoch == self._box_clock_epoch
            and expected is not None
            and expected.response_deadline is not None
            and self._clock() < expected.response_deadline
            and same_setting_value(expected.value, value)
        ):
            return expected.response_deadline
        return None

    def poll_timeouts(self) -> None:
        """Report unresolved SA readbacks even while the BOX sends no data."""
        now = self._clock()
        for (table, key), expected in self._expected.items():
            if expected.response_deadline is not None and expected.response_deadline <= now:
                self._report_outcome(table, key, expected, "timeout")

    @staticmethod
    def _report_outcome(table: str, key: str, expected: _ExpectedField, outcome: str) -> None:
        if expected.response_deadline is None:
            return
        expected.response_deadline = None
        _LOGGER.log(
            logging.WARNING if outcome == "timeout" else logging.INFO,
            "Parameter readback %s for %s:%s",
            outcome, table, key,
            extra={"readback_outcome": outcome, "readback_table": table, "readback_key": key},
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
        self._last_daily_attempt_at: float | None = None
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
        self._last_daily_attempt_at = self._restore_daily_attempt(
            document["version"], document.get("last_daily_attempt_at"),
        )

    def _restore_daily_attempt(self, version: int, daily: float | None) -> float | None:
        attempt = self._last_attempt_at
        if version != 1 or attempt is None:
            return daily
        # Old markers did not distinguish admission outcomes or request causes.
        # An old attempt after the daily deadline could have been admitted.
        baseline = self._last_confirmed_at or self._baseline_at
        return attempt if attempt - baseline >= self._interval_s else daily

    def _valid_marker(self, document: object) -> bool:
        if not isinstance(document, dict):
            return False
        version = document.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version not in (1, 2):
            return False
        fields = {
            "version", "device_id", "baseline_at", "last_confirmed_at", "last_attempt_at"
        }
        if version == 2:
            fields.add("last_daily_attempt_at")
        if document.keys() != fields or document["device_id"] != self._device_id:
            return False
        return self._valid_marker_timestamps(document, fields - {"version", "device_id"})

    def _valid_marker_timestamps(self, document: dict[str, Any], fields: set[str]) -> bool:
        baseline = document["baseline_at"]
        if not _timestamp(baseline):
            return False
        for field in fields:
            value = document[field]
            if value is None and field != "baseline_at":
                continue
            if not _timestamp(value) or not baseline <= value <= self._started_at + _FUTURE_SKEW_S:
                return False
        daily = document.get("last_daily_attempt_at")
        attempt = document["last_attempt_at"]
        return daily is None or (attempt is not None and daily <= attempt)

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
                    "version": 2, "device_id": self._device_id,
                    "baseline_at": self._baseline_at,
                    "last_confirmed_at": self._last_confirmed_at,
                    "last_attempt_at": self._last_attempt_at,
                    "last_daily_attempt_at": self._last_daily_attempt_at,
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
        baseline = max(
            self._last_confirmed_at or self._baseline_at,
            self._last_daily_attempt_at or self._baseline_at,
        )
        daily_due = now - baseline >= self._interval_s
        operation_due = (
            self._operation_at is not None and now - self._operation_at >= _OPERATION_DEBOUNCE_S
        )
        if not (daily_due or operation_due) or not self._idle():
            return False
        return self._admit_due(now, daily_due=daily_due)

    def _admit_due(self, now: float, *, daily_due: bool) -> bool:
        # Reserve the daily slot before admission, so an interrupted callback
        # cannot cause another daily request on restart.
        self._last_attempt_at = now
        previous_daily = self._last_daily_attempt_at
        if daily_due:
            self._last_daily_attempt_at = now
        if not self._persist():
            self._last_daily_attempt_at = previous_daily
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
            if daily_due:
                self._last_daily_attempt_at = previous_daily
                if not self._persist():
                    # The durable reservation may still be on disk.
                    self._last_daily_attempt_at = now
        return bool(admitted)

    def note_operation(self, table: str, key: str) -> None:
        """Debounce only explicitly configured, confirmed exceptional operations."""
        operation = (table.strip().lower(), key.strip().upper())
        if (
            operation[1] == "SA" or operation in _EXCLUDED_OPERATIONS
            or operation[1].startswith("ERR_")
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
