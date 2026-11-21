"""Decode the inverter's warning masks and per-field custom bit indices."""

from typing import Any


def _active_definitions(value: object, definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return []
    active = []
    for entry in definitions:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            continue
        if "mask" in entry:
            mask = entry["mask"]
        else:
            bit = entry.get("bit")
            if not isinstance(bit, int) or isinstance(bit, bool) or not 0 <= bit < value.bit_length():
                continue
            mask = 1 << bit
        if isinstance(mask, int) and not isinstance(mask, bool) and mask > 0 and value & mask:
            active.append(entry)
    return active


def decode_warnings(field_value: int, warnings_list: list[dict[str, Any]]) -> list[str]:
    """Return the keys of the active warnings."""
    return [entry["key"] for entry in _active_definitions(field_value, warnings_list)
            if entry.get("kind") != "status"]


def decode_warning_details(field_value: int, warnings_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return warning keys, optional display text and LCD codes."""
    return [{name: entry.get(name) for name in ("key", "warning_code", "remark", "remark_cs")}
            for entry in _active_definitions(field_value, warnings_list) if entry.get("kind") != "status"]


def warning_state(key: str, value: object, definitions: Any) -> dict[str, list[Any]]:
    """Derive a complete MQTT warning snapshot, including empty lists on clear."""
    if not isinstance(definitions, list) or not definitions:
        return {}
    active = _active_definitions(value, definitions)
    warnings = [entry for entry in active if entry.get("kind") != "status"]
    statuses = [entry for entry in active if entry.get("kind") == "status"]
    return {
        f"{key}_warnings": [entry["key"] for entry in warnings],
        f"{key}_warnings_cs": [entry.get("remark_cs") or entry.get("remark") or entry["key"] for entry in warnings],
        f"{key}_warning_codes": [entry["warning_code"] for entry in warnings if entry.get("warning_code") is not None],
        f"{key}_status_cs": [entry.get("remark_cs") or entry.get("remark") or entry["key"] for entry in statuses],
    }
