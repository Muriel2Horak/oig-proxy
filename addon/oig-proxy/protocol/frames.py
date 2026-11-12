#!/usr/bin/env python3
"""
Frame builders for OIG protocol – offline ACK and response frames.

Extends protocol.frame with higher-level frame builders for local/offline mode.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from datetime import datetime, timezone, timedelta

try:
    from .frame import build_frame, RESULT_ACK, RESULT_END
except ImportError:
    from frame import build_frame, RESULT_ACK, RESULT_END  # type: ignore[no-redef]

if TYPE_CHECKING:
    from .ver_profile import VerProfile


def _last_sunday(year: int, month: int) -> datetime:
    day = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def czech_local_datetime_from_utc(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to Czech civil time without relying on host tzdata."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    utc_dt = utc_dt.astimezone(timezone.utc)
    summer_start = _last_sunday(utc_dt.year, 3).replace(hour=1, minute=0, second=0, microsecond=0)
    summer_end = _last_sunday(utc_dt.year, 10).replace(hour=1, minute=0, second=0, microsecond=0)
    offset_hours = 2 if summer_start <= utc_dt < summer_end else 1
    return (utc_dt + timedelta(hours=offset_hours)).replace(tzinfo=None)


def czech_local_datetime_from_epoch(epoch_seconds: int) -> datetime:
    return czech_local_datetime_from_utc(datetime.fromtimestamp(epoch_seconds, tz=timezone.utc))


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


def build_end_time_frame(*, getactual: bool = True) -> bytes:
    """Sestaví END frame s aktuálním časem (local + UTC) a příkazem GetActual.

    Returns:
        END frame with Time, UTCTime, and ToDo=GetActual
    """
    now_utc = datetime.now(timezone.utc)
    now_local = czech_local_datetime_from_utc(now_utc)
    inner = (
        f"{RESULT_END}"
        f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
        f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
    )
    if getactual:
        inner += "<ToDo>GetActual</ToDo>"
    return build_frame(inner).encode("utf-8")


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_setting_frame(
    device_id: str,
    table: str,
    key: str,
    value: str | int | float,
    id_set: int,
    msg_id: int | None = None,
    todo: str = "New",
    confirm: str = "New",
    *,
    profile: VerProfile,
    include_tto: bool = True,
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
    if not device_id:
        raise ValueError("Setting frame requires a device identity")
    now_utc = datetime.now(timezone.utc)
    now_epoch = int(now_utc.timestamp())
    if id_set > now_epoch:
        raise ValueError("ID_Set must not be in the future")
    tsec_utc = now_utc
    setting_dt_cz = czech_local_datetime_from_epoch(id_set)
    if msg_id is None:
        msg_id = secrets.randbelow(1_000_000) + 14_000_000
    tto = "<TTO_P>0,000</TTO_P><TTO_E>0,000</TTO_E>" if include_tto else ""
    inner = (
        f"<ID>{msg_id}</ID>"
        f"<ID_Device>{_xml_text(device_id)}</ID_Device>"
        f"<ID_Set>{id_set}</ID_Set>"
        "<ID_SubD>0</ID_SubD>"
        f"<DT>{setting_dt_cz.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
        f"<NewValue>{_xml_text(str(value))}</NewValue>"
        f"<Confirm>{_xml_text(confirm)}</Confirm>"
        f"<TblName>{_xml_text(table)}</TblName>"
        f"<TblItem>{_xml_text(key)}</TblItem>"
        f"{tto}"
        "<ID_Server>9</ID_Server>"
        "<mytimediff>0</mytimediff>"
        "<Reason>Setting</Reason>"
        f"<TSec>{tsec_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
    )
    ver = profile.compute(inner.encode("utf-8"))
    return build_frame(f"{inner}<ver>{ver:05d}</ver>").encode("utf-8")
