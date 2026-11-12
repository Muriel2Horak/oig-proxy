"""Thread-safe manual-setting queue with request-identity removal."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

logger = logging.getLogger(__name__)


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


class TwinQueue:
    """Keep the latest manual request per key across MQTT and delivery threads."""

    def __init__(self) -> None:
        self._queue: dict[tuple[str, str, str], TwinSetting] = {}
        self._next_id_set = int(time.time())
        self._lock = threading.RLock()
        self._sent_requests: set[int] = set()
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
        for setting in retired:
            self._notify_drop(setting, "profile_epoch_changed")

    @staticmethod
    def _identity(setting: TwinSetting) -> tuple[str, str, str]:
        return setting.device_id, setting.table, setting.key

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
            if self._queue.get(self._identity(expected)) is not expected:
                return False
            self._sent_requests.add(id(expected))
            return True

    def is_sent(self, expected: TwinSetting) -> bool:
        """Distinguish queued intent from a request already bound to its actual send."""
        with self._lock:
            return id(expected) in self._sent_requests

    def release_sent(self, expected: TwinSetting) -> None:
        """Forget completed send ownership even if a newer same-key request exists."""
        with self._lock:
            self._sent_requests.discard(id(expected))

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
    ) -> TwinSetting | None:
        """Atomically replace one pending request and allocate its correlation IDs."""
        dropped = None
        identity = (device_id, table, key)
        with self._lock:
            profile_epoch = self._profile_epochs.get(device_id)
            accepted = not require_profile or profile_epoch is not None
            previous = self._queue.get(identity) if accepted else None
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
                self._queue[identity] = setting
        if not accepted:
            self._notify_drop(setting, "profile_unready")
            return None
        if dropped is not None:
            self._notify_drop(dropped, "replaced")
        return setting

    def get_pending(self, *, device_id: str | None = None) -> list[TwinSetting]:
        """Return pending references ordered by admission time."""
        with self._lock:
            settings = (setting for setting in self._queue.values()
                        if device_id is None or setting.device_id == device_id)
            return sorted(settings, key=lambda setting: setting.enqueued_at)

    def acknowledge(self, table: str, key: str, *, device_id: str | None = None) -> bool:
        """Preserve the legacy explicit queue-removal API."""
        with self._lock:
            setting = self.get(table, key, device_id=device_id)
            if setting is not None:
                del self._queue[self._identity(setting)]
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
        self._notify_drop(dropped, reason)
        return True

    def remove_if_current(self, expected: TwinSetting) -> bool:
        """Remove only this exact request, never a same-key MQTT replacement."""
        with self._lock:
            identity = self._identity(expected)
            if self._queue.get(identity) is not expected:
                return False
            del self._queue[identity]
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
