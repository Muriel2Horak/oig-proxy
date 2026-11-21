"""Summarize native BOX warnings separately from historical diagnostic evidence."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any

_MASK_TABLE = "tbl_invertor_prms"
_MASK_FIELDS = ("ERR_AC", "ERR_BATT", "ERR_ELSE", "ERR_GRID", "ERR_PV")
_CODE_TABLE = "tbl_invertor_prm2"
_EVENT_TABLE = "tbl_events"
_EVENT_LABELS = {
    "Warning": "Varování boxu",
    "Error": "Chyba boxu",
    "Fault": "Porucha boxu",
    "Alarm": "Alarm boxu",
}
_PHASE_OVERLOAD = re.compile(r"Power on phase ([123]) exceeded the limit \(([0-9]+)W\) 0x")
_CODE_LABELS = {
    1: "Přepětí stejnosměrné sběrnice",
    2: "Nízké napětí stejnosměrné sběrnice",
    3: "Překročen čas náběhu stejnosměrné sběrnice",
    4: "Překročen čas náběhu střídače",
    5: "Nadproud střídače",
    6: "Přehřátí",
    7: "Porucha relé",
    8: "Porucha měření proudu (CT)",
    11: "Nadproud FV vstupu",
    12: "Zemní porucha (GFCI)",
    13: "Porucha izolace FV vstupu",
    14: "Odchylka stejnosměrného proudu",
    16: "Porucha snímače zemní ochrany (GFCI)",
    22: "Přepětí baterie",
    23: "Přetížení",
    26: "Zkrat na výstupu",
    27: "Zablokovaný ventilátor",
    32: "Porucha stejnosměrné části",
    33: "Nízké napětí střídače",
    # Reference tables disagree on the assignment of these two code labels.
    34: "Porucha napětí nebo zapojení střídače",
    35: "Porucha napětí nebo zapojení střídače",
    36: "Porucha výstupního napětí",
}
_HISTORY_LIMIT = 10
_RECORD_KINDS = frozenset({"warning_mask", "unknown_mask", "fault_code", "box_event"})
Record = dict[str, Any]


def _box_time(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if ":" not in value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            stamp = datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
        except ValueError:
            return None
    if stamp.year <= 1970:
        return None
    try:
        return _utc_if_aware(stamp)
    except OverflowError:
        return None


def _utc_if_aware(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo is not None else value


def _timestamps(source_at: str | None, received_at: datetime) -> dict[str, str | None]:
    box_at = _box_time(source_at)
    received = _utc_if_aware(received_at).isoformat()
    return {
        "source_at": source_at,
        "received_at": received,
        "at": box_at.isoformat() if box_at is not None else received,
        "time_basis": "box" if box_at is not None else "proxy",
    }


def _mask_weight(definition: object, width: int) -> int:
    if not isinstance(definition, dict) or not isinstance(definition.get("key"), str):
        return 0
    if "mask" in definition:
        mask = definition["mask"]
        return mask if isinstance(mask, int) and not isinstance(mask, bool) and mask > 0 else 0
    bit = definition.get("bit")
    if isinstance(bit, int) and not isinstance(bit, bool) and 0 <= bit < width:
        return 1 << bit
    return 0


class FaultSummary:
    """Keep current warning masks and diagnostic history for one BOX identity."""

    def __init__(self) -> None:
        self._live_masks: dict[str, int] = {}
        self._active: dict[str, dict[int, Record]] = {}
        self._unknown: dict[str, Record] = {}
        self._history: deque[Record] = deque(maxlen=_HISTORY_LIMIT)
        self._dated_events: deque[tuple[str, str, str]] = deque(maxlen=_HISTORY_LIMIT)
        self._last_code: Record | None = None
        self._last_raw_code: int | None = None

    def observe(  # pylint: disable=too-many-arguments
        self,
        table: str,
        data: dict[str, Any],
        *,
        received_at: datetime,
        source_at: str | None,
        warning_definitions: dict[str, list[dict[str, Any]]] | None = None,
        source_table: str | None = None,
    ) -> bool:
        """Accept one physical frame or its accepted sparse mirror fields."""
        base = {"source_table": source_table or table, **_timestamps(source_at, received_at)}
        if table == _CODE_TABLE:
            return self._observe_code(data.get("CODE_ERR"), base)
        if table == _EVENT_TABLE:
            return self._observe_event(data, base)
        if table == _MASK_TABLE:
            changed = False
            for field in _MASK_FIELDS:
                value = data.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    definitions = (warning_definitions or {}).get(field, [])
                    changed = self._observe_mask(field, value, definitions, base) or changed
            return changed
        return False

    def _observe_mask(
        self, field: str, value: int, definitions: list[dict[str, Any]], base: Record
    ) -> bool:
        previous_value = self._live_masks.get(field)
        if previous_value == value:
            return False
        self._live_masks[field] = value
        previous = self._active.get(field, {})
        current: dict[int, Record] = {}
        known = 0
        for definition in definitions:
            mask = _mask_weight(definition, value.bit_length())
            known |= mask
            if not value & mask or definition.get("kind") == "status" or mask in current:
                continue
            record = previous.get(mask)
            if record is None:
                label = definition.get("remark_cs") or definition.get("remark") or definition["key"]
                record = {
                    **base, "kind": "warning_mask", "field": field, "value": value & mask,
                    "label": str(label), "warning_code": definition.get("warning_code"),
                }
                self._history.appendleft(record)
            current[mask] = record
        self._active[field] = current

        unknown = value & ~known
        previous_unknown = self._unknown.get(field)
        if unknown and (previous_unknown is None or previous_unknown["value"] != unknown):
            record = {
                **base, "kind": "unknown_mask", "field": field, "value": unknown,
                "label": f"Neznámé příznaky {field}: 0x{unknown:X}",
            }
            self._unknown[field] = record
            self._history.appendleft(record)
        elif not unknown:
            self._unknown.pop(field, None)
        return (
            previous_value is None or current != previous
            or self._unknown.get(field) != previous_unknown
        )

    def _observe_code(self, value: Any, base: Record) -> bool:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
        if value == self._last_raw_code:
            return False
        self._last_raw_code = value
        if value == 0:
            return False
        record = {
            **base, "kind": "fault_code", "field": "CODE_ERR", "value": value,
            "label": _CODE_LABELS.get(value, f"Neznámý kód {value}"),
        }
        self._history.appendleft(record)
        self._last_code = record
        return True

    def _observe_event(self, data: dict[str, Any], base: Record) -> bool:
        event_type, content = data.get("Type"), data.get("Content")
        if not isinstance(event_type, str) or event_type not in _EVENT_LABELS:
            return False
        if not isinstance(content, str) or not content.strip():
            return False
        if base["time_basis"] == "box":
            identity = base["at"], event_type, content
            if identity in self._dated_events:
                return False
            self._dated_events.appendleft(identity)
        overload = _PHASE_OVERLOAD.fullmatch(content)
        label = (
            f"Přetížení na fázi L{overload[1]} ({overload[2]} W)" if overload
            else f"{_EVENT_LABELS[event_type]}: {content}"
        )
        record = {
            **base, "kind": "box_event", "field": "Content", "value": content,
            "label": label, "event_type": event_type,
        }
        self._history.appendleft(record)
        return True

    def mark_unavailable(self) -> bool:
        """Invalidate the live observations while preserving diagnostic history."""
        had_live_masks = bool(self._live_masks)
        self._live_masks.clear()
        self._active.clear()
        self._unknown.clear()
        return had_live_masks

    def snapshot(self) -> dict[str, Any]:
        """Return a detached snapshot for Home Assistant and diagnostic clients."""
        active = [
            record for field in _MASK_FIELDS for record in self._active.get(field, {}).values()
        ]
        unverified = [self._unknown[field] for field in _MASK_FIELDS if field in self._unknown]
        recent = [record for record in self._history if record not in active and record not in unverified]
        if active:
            labels = dict.fromkeys(record["label"] for record in active)
            summary = f"Aktivní varování: {', '.join(labels)}"
        elif unverified:
            summary = "Neověřené příznaky střídače"
        elif len(self._live_masks) == len(_MASK_FIELDS):
            summary = "Bez aktivních varování"
        elif self._live_masks:
            summary = "Bez varování v přijatých údajích"
        else:
            summary = "Aktuální stav neznámý"

        if not active and recent:
            latest = recent[0]
            if latest["kind"] == "fault_code":
                summary += f" · poslední kód {latest['value']}: {latest['label']} (neověřeno)"
            elif latest["kind"] == "box_event":
                summary += f" · poslední hlášení: {latest['label']}"
            else:
                summary += f" · dřívější hlášení: {latest['label']}"
        if len(summary) > 255:
            summary = summary[:254] + "…"
        return {
            "recent_records": deepcopy(recent),
            "last_code": deepcopy(self._last_code),
            "summary": summary,
            "active_faults": deepcopy(active),
            "unverified_signs": deepcopy(unverified),
            "has_live_masks": bool(self._live_masks),
        }

    def persisted(self) -> dict[str, Any]:
        """Return only diagnostic history suitable for storage across restarts."""
        return {
            "recent_records": deepcopy(list(self._history)), "last_code": deepcopy(self._last_code),
        }

    def restore(self, history: list[dict[str, Any]], last_code: dict[str, Any] | None) -> None:
        """Restore persisted diagnostic records without establishing current state."""
        self.mark_unavailable()
        self._history.clear()
        self._history.extend(
            deepcopy(record) for record in history[:_HISTORY_LIMIT] if _valid_record(record)
        )
        self._dated_events.clear()
        self._dated_events.extend(
            (record["at"], record["event_type"], record["value"]) for record in self._history
            if record["kind"] == "box_event" and record["time_basis"] == "box"
        )
        self._last_code = (
            deepcopy(last_code) if _valid_record(last_code, kind="fault_code") else None
        )
        self._last_raw_code = self._last_code["value"] if self._last_code is not None else None


def _valid_record(record: Record | None, *, kind: str | None = None) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("kind"), str):
        return False
    if record["kind"] not in _RECORD_KINDS or (kind is not None and record["kind"] != kind):
        return False
    keys = ("label", "at", "received_at", "time_basis", "field", "source_table")
    if not all(isinstance(record.get(key), str) for key in keys):
        return False
    value = record.get("value")
    if record["kind"] == "box_event":
        return isinstance(value, str) and isinstance(record.get("event_type"), str)
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
