from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

from datetime import datetime, timezone


@dataclass
class TwinSetting:
    table: str
    key: str
    value: Any
    enqueued_at: float
    msg_id: int = 0
    id_set: int = 0
    confirm: str = "New"

    def build_setting_xml(
        self,
        device_id: str,
        id_set: int,
        msg_id: int = 0,
        confirm: str = "New",
    ) -> str:
        """Build XML payload for setting delivery – generic form (matches cloud)."""
        if msg_id == 0:
            msg_id = secrets.randbelow(90_000_000) + 10_000_000

        now_local = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        ver = secrets.randbelow(90_000) + 10_000

        # Výpočet CRC nad XML bez samotného <CRC> elementu (stejně jako cloud).
        xml_without_crc = (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{now_local}</DT>"
            f"<NewValue>{self.value}</NewValue>"
            f"<Confirm>{confirm}</Confirm>"
            f"<TblName>{self.table}</TblName>"
            f"<TblItem>{self.key}</TblItem>"
            "<ID_Server>9</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{now_utc}</TSec>"
            f"<ver>{ver:05d}</ver>"
        )
        crc = self._calculate_crc16(xml_without_crc)

        return f"{xml_without_crc}<CRC>{crc}</CRC></Frame>"

    def _calculate_crc16(self, data: str) -> int:
        """Compute CRC‑16‑Modbus over the supplied ASCII string."""
        crc = 0xFFFF
        for byte in data.encode('ascii'):
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF


class TwinQueue:
    def __init__(self) -> None:
        self._queue: dict[tuple[str, str], TwinSetting] = {}
        self._next_id_set = int(time.time_ns() % 900_000_000) + 100_000_000

    def _generate_msg_id(self) -> int:
        return secrets.randbelow(90_000_000) + 10_000_000

    def _generate_id_set(self) -> int:
        id_set = self._next_id_set
        self._next_id_set += 1
        if self._next_id_set > 999_999_999:
            self._next_id_set = 100_000_000
        return id_set

    def enqueue(self, table: str, key: str, value: Any, confirm: str = "New") -> None:
        setting = TwinSetting(
            table=table,
            key=key,
            value=value,
            enqueued_at=time.time(),
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
