"""Thread-safe manual-setting queue with request-identity removal."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from protocol.parser import same_setting_value

logger = logging.getLogger(__name__)
_HEAD_WAIT_S = 300
_BATCH_WAIT_S = 1800


@dataclass
class TwinSetting:
    """Public mutable request DTO retained for existing control consumers."""

    table: str
    key: str
    value: Any
    enqueued_at: float
    raw_text: str = ""
    audit_id: str = ""
    msg_id: int = 0
    id_set: int = 0
    confirm: str = "New"
    profile_epoch: int | None = None
    device_id: str = ""
    retried: bool = False


@dataclass
class _QueueBatch:
    started_at: float
    last_admitted: TwinSetting | None = None
    head: TwinSetting | None = None
    head_wait: float = 0.0
    ready_since: float | None = None

    def update_head(self, head: TwinSetting | None, now: float, *, blocked: bool) -> bool:
        """Advance one waiting head's elapsed time under the current ownership."""
        if self.head is not head:
            self.head = head
            self.head_wait = 0.0
            self.ready_since = None
        if self.ready_since is not None:
            self.head_wait += now - self.ready_since
        self.ready_since = now if head is not None and not blocked else None
        return head is not None and self.head_wait >= _HEAD_WAIT_S


class TwinQueue:
    """Keep the latest manual request per key across MQTT and delivery threads."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._queue: dict[tuple[str, str, str], TwinSetting] = {}
        self._next_id_set = int(time.time())
        self._lock = threading.RLock()
        self._clock = clock
        self._batches: dict[str, _QueueBatch] = {}
        self._blocked_device_id: str | None = None
        self._sent_requests: dict[int, TwinSetting] = {}
        self._profile_epochs: dict[str, int] = {}
        self._on_drop: Callable[[TwinSetting, str], None] | None = None

    def set_profile_epoch(self, epoch: int | None, *, device_id: str = "") -> None:
        """Snapshot the device proof and retire its old unsent requests."""
        with self._lock:
            if epoch is None:
                self._profile_epochs.pop(device_id, None)
            else:
                self._profile_epochs[device_id] = epoch
            retired = [
                setting for setting in self._queue.values()
                if setting.device_id == device_id
                and (epoch is None or setting.profile_epoch != epoch)
                and id(setting) not in self._sent_requests
            ]
            for setting in retired:
                del self._queue[self._identity(setting)]
            self._discard_drained_batch(device_id)
        for setting in retired:
            self._notify_drop(setting, "profile_epoch_changed")

    @staticmethod
    def _identity(setting: TwinSetting) -> tuple[str, str, str]:
        return setting.device_id, setting.table, setting.key

    def _discard_drained_batch(self, device_id: str) -> None:
        if not any(setting.device_id == device_id for setting in self._queue.values()) and not any(
            setting.device_id == device_id for setting in self._sent_requests.values()
        ):
            self._batches.pop(device_id, None)

    def expire_waiting(self, *, blocked_device_id: str | None = None) -> None:
        """Expire unsent intent and promote the next eligible head per device."""
        dropped: list[tuple[TwinSetting, str]] = []
        with self._lock:
            now = self._clock()
            self._blocked_device_id = blocked_device_id
            waiting: dict[str, list[TwinSetting]] = {}
            for setting in self._queue.values():
                if id(setting) not in self._sent_requests:
                    waiting.setdefault(setting.device_id, []).append(setting)
            for device_id, batch in self._batches.items():
                pending = waiting.get(device_id, [])
                batch_deadline = batch.started_at + _BATCH_WAIT_S
                blocked = device_id == blocked_device_id
                if batch.update_head(pending[0] if pending else None, min(now, batch_deadline), blocked=blocked):
                    dropped.append((pending.pop(0), "expired"))
                    batch.update_head(pending[0] if pending else None, now, blocked=blocked)
                if now >= batch_deadline:
                    dropped.extend((setting, "batch_expired") for setting in pending)
                    batch.update_head(None, now, blocked=True)
            for setting, _reason in dropped:
                del self._queue[self._identity(setting)]
                self._discard_drained_batch(setting.device_id)
        for setting, reason in dropped:
            self._notify_drop(replace(setting), reason)

    def set_drop_callback(self, callback: Callable[[TwinSetting, str], None]) -> None:
        """Attach the delivery owner's unsent-disposition observer."""
        with self._lock:
            self._on_drop = callback

    def _notify_drop(self, setting: TwinSetting, reason: str) -> None:
        callback = self._on_drop
        if callback is not None:
            try:
                callback(setting, reason)
            except Exception:  # Result reporting cannot break command admission/removal.
                logger.exception("Failed to report dropped manual setting")

    def mark_sent(self, expected: TwinSetting) -> bool:
        """Reserve send ownership atomically against replacement and explicit clear."""
        with self._lock:
            identity = self._identity(expected)
            if self._queue.get(identity) is not expected or id(expected) in self._sent_requests:
                return False
            batch = self._batches[expected.device_id]
            now = self._clock()
            batch_deadline = batch.started_at + _BATCH_WAIT_S
            reason = "batch_expired" if now >= batch_deadline else None
            if batch.head is expected:
                if batch.update_head(expected, min(now, batch_deadline), blocked=True):
                    reason = "expired"
                batch.update_head(None, now, blocked=True)
            if reason is None:
                self._sent_requests[id(expected)] = expected
                return True
            del self._queue[identity]
            self._discard_drained_batch(expected.device_id)
        self._notify_drop(replace(expected), reason)
        return False

    def is_sent(self, expected: TwinSetting) -> bool:
        """Distinguish queued intent from a request already bound to its actual send."""
        with self._lock:
            return id(expected) in self._sent_requests

    def release_sent(self, expected: TwinSetting) -> None:
        """Forget completed send ownership even if a newer same-key request exists."""
        with self._lock:
            self._sent_requests.pop(id(expected), None)
            self._discard_drained_batch(expected.device_id)

    def _generate_msg_id(self) -> int:
        return secrets.randbelow(1_000_000) + 14_000_000

    def _generate_id_set(self) -> int:
        id_set = self._next_id_set
        self._next_id_set += 1
        if self._next_id_set > 9_999_999_999:
            self._next_id_set = int(time.time())
        return id_set

    def _generate_audit_id(self) -> str:
        now_epoch = int(time.time() * 1000)
        return f"aud_{now_epoch:014d}_{secrets.randbelow(1_000_000):06d}"

    def enqueue(
        self,
        table: str,
        key: str,
        value: Any,
        confirm: str = "New",
        audit_id: str = "",
        raw_text: str = "",
        *,
        device_id: str = "",
        require_profile: bool = False,
        only_if_empty: bool = False,
    ) -> TwinSetting | None:
        """Admit a request, returning the original object for an adjacent duplicate."""
        setting, _duplicate = self.admit(
            table, key, value, confirm, audit_id, raw_text, device_id=device_id,
            require_profile=require_profile, only_if_empty=only_if_empty,
        )
        return setting

    def admit(
        self,
        table: str,
        key: str,
        value: Any,
        confirm: str = "New",
        audit_id: str = "",
        raw_text: str = "",
        *,
        device_id: str = "",
        require_profile: bool = False,
        only_if_empty: bool = False,
    ) -> tuple[TwinSetting | None, bool]:
        """Admit one intent and report whether it duplicates the last admission."""
        dropped = None
        identity = (device_id, table, key)
        with self._lock:
            if only_if_empty and (identity in self._queue or self._has_unsent()):
                return None, False
            profile_epoch = self._profile_epochs.get(device_id)
            accepted = not require_profile or profile_epoch is not None
            previous = self._queue.get(identity) if accepted else None
            batch = self._batches.get(device_id)
            last = batch.last_admitted if batch is not None else None
            if accepted and last is not None and (previous is last or id(last) in self._sent_requests):
                same_request = (self._identity(last), last.confirm, last.profile_epoch) == (
                    identity, confirm, profile_epoch,
                )
                same_value = same_setting_value(last.value, value) or (
                    type(last.value) is type(value) and last.value == value
                )
                if same_request and same_value:
                    return last, True
            if previous is not None and id(previous) not in self._sent_requests:
                dropped = replace(previous)
            if not audit_id:
                audit_id = self._generate_audit_id()
            setting = TwinSetting(
                table=table,
                key=key,
                value=value,
                enqueued_at=time.time(),
                raw_text=raw_text,
                audit_id=audit_id,
                msg_id=self._generate_msg_id(),
                id_set=self._generate_id_set(),
                confirm=confirm,
                profile_epoch=profile_epoch,
                device_id=device_id,
            )
            if accepted:
                # Replacement is a new admission. Ordered iteration stays correct if wall time changes.
                self._queue.pop(identity, None)
                self._queue[identity] = setting
                if batch is None:
                    now = self._clock()
                    batch = self._batches[device_id] = _QueueBatch(now)
                    batch.update_head(setting, now, blocked=device_id == self._blocked_device_id)
                batch.last_admitted = setting
        if not accepted:
            self._notify_drop(setting, "profile_unready")
            return None, False
        if dropped is not None:
            self._notify_drop(dropped, "replaced")
        return setting, False

    def get_pending(self, *, device_id: str | None = None, unsent_only: bool = False) -> list[TwinSetting]:
        """Return pending references ordered by admission time."""
        with self._lock:
            return [setting for setting in self._queue.values()
                    if (device_id is None or setting.device_id == device_id)
                    and (not unsent_only or id(setting) not in self._sent_requests)]

    def _has_unsent(self) -> bool:
        return any(id(setting) not in self._sent_requests for setting in self._queue.values())

    def retry_once(self, expected: TwinSetting) -> bool:
        """Return a positively reconciled request to the queue once."""
        with self._lock:
            identity = self._identity(expected)
            if (self._queue.get(identity) is not expected or id(expected) not in self._sent_requests
                    or expected.retried or self._clock() - self._batches[expected.device_id].started_at >= _BATCH_WAIT_S):
                return False
            replay = replace(expected, retried=True)
            self._queue[identity] = replay
            self._sent_requests.pop(id(expected))
            batch = self._batches[expected.device_id]
            if batch.last_admitted is expected:
                batch.last_admitted = replay
            return True

    def acknowledge(self, table: str, key: str, *, device_id: str | None = None) -> bool:
        """Preserve the legacy explicit queue-removal API."""
        with self._lock:
            setting = self.get(table, key, device_id=device_id)
            if setting is not None:
                del self._queue[self._identity(setting)]
                self._discard_drained_batch(setting.device_id)
            dropped = replace(setting) if setting is not None and id(setting) not in self._sent_requests else None
        if dropped is not None:
            self._notify_drop(dropped, "cleared")
        return setting is not None

    def discard(self, expected: TwinSetting, reason: str) -> bool:
        """Remove this exact unsent intent and report its explicit disposition."""
        with self._lock:
            identity = self._identity(expected)
            if self._queue.get(identity) is not expected or id(expected) in self._sent_requests:
                return False
            dropped = replace(expected)
            del self._queue[identity]
            self._discard_drained_batch(expected.device_id)
        self._notify_drop(dropped, reason)
        return True

    def remove_if_current(self, expected: TwinSetting) -> bool:
        """Remove only this exact request, never a same-key MQTT replacement."""
        with self._lock:
            identity = self._identity(expected)
            if self._queue.get(identity) is not expected:
                return False
            del self._queue[identity]
            self._discard_drained_batch(expected.device_id)
            return True

    def is_current(self, expected: TwinSetting) -> bool:
        """Check request identity under the same lock used by replacement/removal."""
        with self._lock:
            return self._queue.get(self._identity(expected)) is expected

    def size(self, *, device_id: str | None = None) -> int:
        """Return the current number of pending key identities."""
        with self._lock:
            if device_id is None:
                return len(self._queue)
            return sum(setting.device_id == device_id for setting in self._queue.values())

    def clear(self, *, reason: str = "cleared") -> None:
        """Explicitly discard all pending manual requests."""
        with self._lock:
            dropped = [replace(setting) for setting in self._queue.values()
                       if id(setting) not in self._sent_requests]
            self._queue.clear()
            for device_id in tuple(self._batches):
                self._discard_drained_batch(device_id)
        for setting in dropped:
            self._notify_drop(setting, reason)

    def get(self, table: str, key: str, *, device_id: str | None = None) -> TwinSetting | None:
        """Return the current request when its identity resolves unambiguously."""
        with self._lock:
            if device_id is not None:
                return self._queue.get((device_id, table, key))
            matches = [setting for (_, setting_table, setting_key), setting in self._queue.items()
                       if (setting_table, setting_key) == (table, key)]
            return matches[0] if len(matches) == 1 else None
