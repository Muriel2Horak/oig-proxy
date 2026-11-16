#!/usr/bin/env python3
"""
XML parser pro OIG protokol.

Parsuje inner XML z OIG framů, extrahuje data fieldy.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)
_MODE_BY_EVENT = {"I": 0, "II": 1, "III": 2, "IV-UPS": 3}
_MODE_START = re.compile(r"HG\s+(I|II|III|IV-UPS)\s+Start(?=\s*(?:\(|$))")
_PARAMETER_CHANGE = re.compile(
    r"(Remotely|Input)\s*:\s*(tbl_[A-Za-z0-9_]+)\s*/\s*([A-Za-z0-9_]+)"
    r"\s*:\s*\[([^\]]*)\]\s*->\s*\[([^\]]*)\]"
)


def parse_mode_start(event_type: Any, content: Any) -> int | None:
    """Decode the mode from a native Factory start event."""
    if event_type == "Factory" and isinstance(content, str) and (match := _MODE_START.match(content)):
        return _MODE_BY_EVENT[match[1]]
    return None


def same_setting_value(expected: Any, observed: Any) -> bool:
    """Compare native scalar settings across their numeric wire encodings."""
    try:
        left, right = Decimal(str(expected)), Decimal(str(observed))
        return left.is_finite() and right.is_finite() and left == right
    except (InvalidOperation, ValueError, TypeError):
        return isinstance(expected, str) and expected == observed


def parse_parameter_change(event_type: str, content: str) -> tuple[str, str, str | int] | None:
    """Identify an explicit BOX parameter change without attributing a command."""
    mode = parse_mode_start(event_type, content)
    if mode is not None:
        return "tbl_box_prms", "MODE", mode
    prefix = {"Setting": "Remotely", "Change": "Input"}.get(event_type)
    match = _PARAMETER_CHANGE.match(content) if prefix is not None else None
    if match is None or match[1] != prefix or same_setting_value(match[4], match[5]):
        return None
    return match[2], match[3], match[5]

# Fieldy které se přeskočí při parsování dat
_SKIP_FIELDS = frozenset({
    "TblName", "ID_Device", "ID_Set", "Reason",
    "ver", "CRC", "DT", "ID_SubD",
})


def parse_xml_frame(data: str) -> dict[str, Any]:
    """
    Parsuje XML inner content OIG frame.

    Returns:
        dict s parsovanými daty. Speciální klíče začínají _:
        - _table: název tabulky (TblName)
        - _device_id: ID zařízení
        - _dt: timestamp z rámce
        Vrátí prázdný dict pro SubD > 0 (neaktivní bateriové banky).
    """
    result: dict[str, Any] = {}

    # TblName
    tbl_match = re.search(r"<TblName>([^<]+)</TblName>", data)
    if tbl_match:
        result["_table"] = tbl_match.group(1)

    # ID_Device
    id_match = re.search(r"<ID_Device>(\d+)</ID_Device>", data)
    if id_match:
        result["_device_id"] = id_match.group(1)

    # DT (timestamp)
    dt_match = re.search(r"<DT>([^<]+)</DT>", data)
    if dt_match:
        result["_dt"] = dt_match.group(1)

    # ID_SubD – filtrujeme neaktivní bateriové banky (SubD > 0)
    subframe_match = re.search(r"<ID_SubD>(\d+)</ID_SubD>", data)
    if subframe_match:
        subframe_id = int(subframe_match.group(1))
        if subframe_id > 0:
            logger.debug("SubD=%s ignored (inactive battery bank)", subframe_id)
            return {}

    # Všechna ostatní datová pole
    for match in re.finditer(r"<(\w+)>([^<]*)</\1>", data):
        key, value = match.groups()
        if key in _SKIP_FIELDS:
            continue

        # Auto-konverze na int/float
        try:
            if "." in value:
                result[key] = float(value)
            else:
                result[key] = int(value)
        except ValueError:
            result[key] = value

    return result
