#!/usr/bin/env python3
"""
XML Parser pro OIG protokol.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class OIGDataParser:
    """Parser pro XML frames z OIG protokolu."""

    @staticmethod
    def parse_xml_frame(data: str) -> dict[str, Any]:
        """Parsuje XML frame z BOXu.

        Returns:
            dict s parsovanými daty. Speciální klíče začínají _:
            - _table: název tabulky (TblName)
            - _device_id: ID zařízení
            - _dt: timestamp z rámce
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

        # DT (timestamp pro měření latence)
        dt_match = re.search(r"<DT>([^<]+)</DT>", data)
        if dt_match:
            result["_dt"] = dt_match.group(1)

        # ID_SubD (bateriové banky)
        # OIG posílá tbl_batt_prms ve 3 variantách (0,1,2)
        # Publikujeme jen SubD=0 (aktivní banka)
        subframe_match = re.search(r"<ID_SubD>(\d+)</ID_SubD>", data)
        if subframe_match:
            subframe_id = int(subframe_match.group(1))
            if subframe_id > 0:
                logger.debug(
                    "SubD=%s ignored (inactive bank)",
                    subframe_id,
                )
                return {}  # Prázdný dict - nebude publikován

        # Všechny ostatní fieldy
        skip_fields = {
            "TblName", "ID_Device", "ID_Set", "Reason",
            "ver", "CRC", "DT", "ID_SubD"
        }

        for match in re.finditer(r"<(\w+)>([^<]*)</\1>", data):
            key, value = match.groups()
            if key in skip_fields:
                continue

            # Konverze na číslo pokud možno
            try:
                if "." in value:
                    result[key] = float(value)
                else:
                    result[key] = int(value)
            except ValueError:
                result[key] = value

        return result

    @staticmethod
    def parse_mode_from_event(content: str) -> int | None:
        """Parsuje MODE hodnotu z tbl_events Content fieldu.

        Očekává formát:
        'Remotely : tbl_box_prms / MODE: [old]->[new]'
        nebo 'Locally : tbl_box_prms / MODE: [old]->[new]'

        Returns:
            int: Nová MODE hodnota (0-5) nebo None
        """
        match = re.search(r'MODE:\s*\[(\d+)\]->\[(\d+)\]', content)
        if match:
            old_value = int(match.group(1))
            new_value = int(match.group(2))
            logger.info("MODE: Event detected: %s → %s", old_value, new_value)
            return new_value
        return None
