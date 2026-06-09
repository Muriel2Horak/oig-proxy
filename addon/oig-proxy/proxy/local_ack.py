#!/usr/bin/env python3
"""
Local ACK builder – generuje lokální ACK odpovědi pro offline režim.

Podle typu tabulky vrací různé ACK framy:
- tbl_* → ACK frame
- IsNewSet → END frame s časem + GetActual (jako cloud)
- IsNewWeather, IsNewFW → END frame s časem + GetActual (jako cloud)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from ..protocol.frames import (
        build_ack_only_frame,
        build_getactual_frame,
        build_end_time_frame,
    )
except ImportError:
    from protocol.frames import (  # type: ignore[no-redef]
        build_ack_only_frame,
        build_getactual_frame,
        build_end_time_frame,
    )

if TYPE_CHECKING:
    pass


def build_local_ack(table_name: str) -> bytes:
    """Sestaví lokální ACK odpověď podle typu tabulky.

    Args:
        table_name: Název tabulky z TblName tagu

    Returns:
        ACK frame bytes s CRC a CRLF

    Logika:
        - tbl_* → ACK frame (s GetActual pro tbl_actual)
        - IsNewSet → END + Time + UTCTime + GetActual
        - IsNewWeather, IsNewFW → END + Time + UTCTime + GetActual
        - END → END + Time + UTCTime + GetActual
    """
    # Special tables that need END response
    if table_name == "END":
        return build_end_time_frame()

    # IsNewSet - cloud always answers with END + time + GetActual
    if table_name == "IsNewSet":
        return build_end_time_frame()

    # Weather and FW check - return END with time + GetActual (matches cloud response)
    if table_name in ("IsNewWeather", "IsNewFW"):
        return build_end_time_frame()

    # All tbl_* tables get ACK
    if table_name.startswith("tbl_"):
        # tbl_actual gets GetActual command
        if table_name == "tbl_actual":
            return build_getactual_frame()
        # Other tables get simple ACK
        return build_ack_only_frame()

    # Default fallback - ACK
    return build_ack_only_frame()
