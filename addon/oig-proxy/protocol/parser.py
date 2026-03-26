#!/usr/bin/env python3
"""
XML parser pro OIG protokol.

Parsuje inner XML z OIG framů, extrahuje data fieldy.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

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
