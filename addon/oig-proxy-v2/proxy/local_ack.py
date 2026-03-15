#!/usr/bin/env python3
"""
Local ACK builder – generuje lokální ACK odpovědi pro offline režim.

Podle typu tabulky vrací různé ACK framy:
- tbl_* → ACK frame
- IsNewSet → END frame s timestampem (DT)
- IsNewWeather, IsNewFW → END frame
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from ..protocol.frames import (
        build_ack_only_frame,
        build_getactual_frame,
        build_end_time_frame,
        build_end_frame_with_timestamp,
        build_end_only_frame,
    )
except ImportError:
    from protocol.frames import (  # type: ignore[no-redef]
        build_ack_only_frame,
        build_getactual_frame,
        build_end_time_frame,
        build_end_frame_with_timestamp,
        build_end_only_frame,
    )

if TYPE_CHECKING:
    pass


def build_local_ack(table_name: str, *, has_queued_data: bool = False) -> bytes:
    """Sestaví lokální ACK odpověď podle typu tabulky.

    Args:
        table_name: Název tabulky z TblName tagu
        has_queued_data: True pokud je ve frontě data k odeslání (pro IsNewSet)

    Returns:
        ACK frame bytes s CRC a CRLF

    Logika:
        - tbl_* → ACK frame (s GetActual pro tbl_actual)
        - IsNewSet → END + DT timestamp (pokud je ve frontě data)
        - IsNewWeather, IsNewFW → END frame
        - END → END + Time + UTCTime + GetActual
    """
    # Special tables that need END response
    if table_name == "END":
        return build_end_time_frame()

    # IsNewSet - queue status check
    if table_name == "IsNewSet":
        if has_queued_data:
            # Queue has data - return END with timestamp
            return build_end_frame_with_timestamp()
        # Queue empty - return ACK
        return build_ack_only_frame()

    # Weather and FW check - always return END
    if table_name in ("IsNewWeather", "IsNewFW"):
        return build_end_only_frame()

    # All tbl_* tables get ACK
    if table_name.startswith("tbl_"):
        # tbl_actual gets GetActual command
        if table_name == "tbl_actual":
            return build_getactual_frame()
        # Other tables get simple ACK
        return build_ack_only_frame()

    # Default fallback - ACK
    return build_ack_only_frame()


def should_queue_frame(table_name: str) -> bool:
    """Určí, zda se frame má uložit do fronty pro pozdější odeslání.

    Args:
        table_name: Název tabulky

    Returns:
        True pokud se má frame uložit do fronty
    """
    # Only data tables (tbl_*) should be queued
    # Control/ack tables should not
    return table_name.startswith("tbl_")
