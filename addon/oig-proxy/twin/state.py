from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class TwinSetting:
    table: str
    key: str
    value: Any
    enqueued_at: float
    raw_text: str = ""
    audit_id: str = ""
    msg_id: int = 0
    id_set: int = 0
    confirm: str = "New"


class TwinQueue:
    def __init__(self) -> None:
        self._queue: dict[tuple[str, str], TwinSetting] = {}
        self._next_id_set = int(time.time())

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
    ) -> None:
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
        )
        self._queue[(table, key)] = setting

    def get_pending(self) -> list[TwinSetting]:
        return sorted(self._queue.values(), key=lambda s: s.enqueued_at)

    def acknowledge(self, table: str, key: str) -> bool:
        key_tuple = (table, key)
        if key_tuple in self._queue:
            del self._queue[key_tuple]
            return True
        return False

    def size(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        self._queue.clear()

    def get(self, table: str, key: str) -> TwinSetting | None:
        return self._queue.get((table, key))
