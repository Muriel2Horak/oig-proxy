#!/usr/bin/env python3
"""
XML parser pro OIG protokol.

Parsuje inner XML z OIG framů, extrahuje data fieldy.
"""
# pylint: disable=too-many-instance-attributes

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from .frame import AssembledFrame, ValidatedFrame, validate_frame

logger = logging.getLogger(__name__)

# Fieldy které se přeskočí při parsování dat
_SKIP_FIELDS = frozenset({
    "TblName", "ID_Device", "ID_Set", "Reason",
    "ver", "CRC", "DT", "ID_SubD",
})

_ROUTING_TAGS = {
    "Result": "result",
    "TblName": "table_name",
    "ID_Device": "device_id",
    "Reason": "reason",
    "ToDo": "todo",
    "Rdt": "rdt",
    "ID": "message_id",
    "ID_Set": "id_set",
    "TblItem": "item_name",
    "NewValue": "new_value",
    "Type": "event_type",
    "Content": "content",
}
_INTEGER_TAGS = frozenset({"ID", "ID_Set"})
_STRICT_INTEGER_RE = re.compile(r"[0-9]+")
_MAX_SIGNED_64 = (1 << 63) - 1


@dataclass(frozen=True, slots=True)
class FrameMetadata:
    """Direct-child routing fields extracted from validated evidence."""

    result: str | None
    table_name: str | None
    device_id: str | None
    reason: str | None
    todo: str | None
    rdt: str | None
    message_id: int | None
    id_set: int | None
    item_name: str | None
    new_value: str | None
    event_type: str | None
    content: str | None

    @property
    def is_isnewset(self) -> bool:
        """Return whether the exact direct result requests settings."""
        return self.result == "IsNewSet"


def parse_frame_metadata(  # pylint: disable=too-many-return-statements
    frame: ValidatedFrame,
) -> FrameMetadata | None:
    """Extract unambiguous direct routing fields from validated raw bytes."""
    revalidation = validate_frame(AssembledFrame(frame.raw, frame.received_at_ms))
    if revalidation.validated != frame:
        return None

    try:
        root = ET.fromstring(frame.raw[:-2])
    except (ET.ParseError, ValueError):
        return None
    if root.tag != "Frame":
        return None

    direct_values: dict[str, str] = {}
    for child in root:
        if child.tag not in _ROUTING_TAGS:
            continue
        if child.tag in direct_values or len(child) != 0:
            return None
        direct_values[child.tag] = child.text or ""

    integers: dict[str, int] = {}
    for tag in _INTEGER_TAGS:
        if tag not in direct_values:
            continue
        value = direct_values[tag]
        if _STRICT_INTEGER_RE.fullmatch(value) is None:
            return None
        parsed = int(value)
        if parsed > _MAX_SIGNED_64:
            return None
        integers[tag] = parsed

    def text(tag: str) -> str | None:
        return direct_values.get(tag)

    return FrameMetadata(
        result=text("Result"),
        table_name=text("TblName"),
        device_id=text("ID_Device"),
        reason=text("Reason"),
        todo=text("ToDo"),
        rdt=text("Rdt"),
        message_id=integers.get("ID"),
        id_set=integers.get("ID_Set"),
        item_name=text("TblItem"),
        new_value=text("NewValue"),
        event_type=text("Type"),
        content=text("Content"),
    )


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
