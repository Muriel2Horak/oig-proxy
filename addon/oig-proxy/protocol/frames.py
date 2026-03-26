#!/usr/bin/env python3
"""
Frame builders for OIG protocol – offline ACK and response frames.

Extends protocol.frame with higher-level frame builders for local/offline mode.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta

try:
    from .frame import build_frame, RESULT_ACK, RESULT_END
except ImportError:
    from frame import build_frame, RESULT_ACK, RESULT_END  # type: ignore[no-redef]


def build_ack_only_frame() -> bytes:
    """Sestaví prostý ACK frame.

    Returns:
        ACK frame bytes with CRC and CRLF
    """
    return build_frame(RESULT_ACK).encode("utf-8")


def build_getactual_frame() -> bytes:
    """Sestaví ACK frame s příkazem GetActual.

    Returns:
        ACK frame with ToDo=GetActual command
    """
    inner = f"{RESULT_ACK}<ToDo>GetActual</ToDo>"
    return build_frame(inner).encode("utf-8")


def build_end_time_frame() -> bytes:
    """Sestaví END frame s aktuálním časem (local + UTC) a příkazem GetActual.

    Returns:
        END frame with Time, UTCTime, and ToDo=GetActual
    """
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    inner = (
        f"{RESULT_END}"
        f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
        f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
        "<ToDo>GetActual</ToDo>"
    )
    return build_frame(inner).encode("utf-8")


def build_end_frame_with_timestamp() -> bytes:
    """Sestaví END frame s aktuálním časem (pro IsNewSet odpověď).

    Returns:
        END frame with DT timestamp
    """
    now = datetime.now()
    inner = (
        f"{RESULT_END}"
        f"<DT>{now.strftime('%Y-%m-%d %H:%M:%S')}</DT>"
    )
    return build_frame(inner).encode("utf-8")


def build_end_only_frame() -> bytes:
    """Sestaví prostý END frame bez timestampu.

    Returns:
        Simple END frame (for IsNewWeather, IsNewFW)
    """
    return build_frame(RESULT_END).encode("utf-8")


def build_setting_frame(
    device_id: str,
    table: str,
    key: str,
    value: str | int | float,
    id_set: int,
    msg_id: int | None = None,
    todo: str = "New",
    confirm: str = "New",
) -> bytes:
    """Sestaví Setting frame pro odeslání do BOXu.

    Args:
        device_id: ID zařízení
        table: Název tabulky (např. tbl_box_prms)
        key: Klíč nastavení (např. MODE)
        value: Hodnota nastavení
        id_set: Unikátní ID settingu
        todo: ToDo příkaz (default "New")
        confirm: Confirm flag (default "New")

    Returns:
        Setting frame bytes
    """
    _ = todo
    now_utc = datetime.now(timezone.utc)
    # DT musí být v CZ local time (UTC+1) – BOX validuje ID_Set = unix_timestamp(DT - 1h)
    now_local_cz = now_utc + timedelta(hours=1)
    if msg_id is None:
        # Cloud ID roste sekvenčně od ~13.6M, generujeme v bezpečném rozsahu 13M–14M
        msg_id = secrets.randbelow(1_000_000) + 13_000_000
    ver = secrets.randbelow(65_535)
    inner = (
        f"<ID>{msg_id}</ID>"
        f"<ID_Device>{device_id}</ID_Device>"
        f"<ID_Set>{id_set}</ID_Set>"
        "<ID_SubD>0</ID_SubD>"
        f"<DT>{now_local_cz.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
        f"<NewValue>{value}</NewValue>"
        f"<Confirm>{confirm}</Confirm>"
        f"<TblName>{table}</TblName>"
        f"<TblItem>{key}</TblItem>"
        "<ID_Server>9</ID_Server>"
        "<mytimediff>0</mytimediff>"
        "<Reason>Setting</Reason>"
        f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
        f"<ver>{ver:05d}</ver>"
    )
    return build_frame(inner).encode("utf-8")
