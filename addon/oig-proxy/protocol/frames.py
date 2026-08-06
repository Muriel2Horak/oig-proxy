#!/usr/bin/env python3
"""
Frame builders for OIG protocol – offline ACK and response frames.

Extends protocol.frame with higher-level frame builders for local/offline mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import html
import re

try:
    from .frame import build_frame, RESULT_ACK, RESULT_END
    from .crc import crc16_modbus
except ImportError:
    from frame import build_frame, RESULT_ACK, RESULT_END  # type: ignore[no-redef]
    from crc import crc16_modbus  # type: ignore[no-redef]


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


def is_xml_1_0_text(text: str) -> bool:
    """Return whether every code point is allowed by XML 1.0."""
    return all(
        char in "\t\n\r"
        or "\x20" <= char <= "\ud7ff"
        or "\ue000" <= char <= "\ufffd"
        or "\U00010000" <= char <= "\U0010ffff"
        for char in text
    )


def escape_xml_text(text: str) -> str:
    """Escape one dynamic XML text-node value exactly once."""
    return html.escape(text, quote=True)


@dataclass(frozen=True, slots=True)
class RenderedSettingFrame:
    """Serialized Setting bytes with their CRC and exact wire length."""

    wire_frame: bytes
    crc_text: str
    wire_length: int


# pylint: disable=too-many-arguments,too-many-locals
def build_setting_frame(
    *,
    device_id: str,
    table_name: str,
    item_name: str,
    value_text: str,
    wire_id: int,
    wire_id_set: int,
    wire_dt: str,
    tsec_text: str,
    ver_text: str,
) -> RenderedSettingFrame:
    """Render one deterministic, XML-safe Setting wire frame."""
    supplied_dynamic = (device_id, table_name, item_name, value_text, wire_dt, tsec_text)
    if not all(isinstance(text, str) for text in supplied_dynamic):
        raise ValueError("dynamic Setting text is not valid XML 1.0")
    dynamic = tuple(str.__str__(text) for text in supplied_dynamic)
    if not all(is_xml_1_0_text(text) for text in dynamic):
        raise ValueError("dynamic Setting text is not valid XML 1.0")
    device_id, table_name, item_name, value_text, wire_dt, tsec_text = dynamic
    if (
        not isinstance(ver_text, str)
        or not re.fullmatch(r"[0-9]{5}", ver_text)
        or int(ver_text) > 65535
    ):
        raise ValueError("ver_text must be a zero-padded uint16 decimal")
    ver_text = str.__str__(ver_text)
    for field_name, field_value in (
        ("wire_id", wire_id),
        ("wire_id_set", wire_id_set),
    ):
        if isinstance(field_value, bool) or not isinstance(field_value, int) or field_value < 0:
            raise ValueError(f"{field_name} must be a non-negative integer")

    try:
        inner = (
            f"<ID>{wire_id}</ID>"
            f"<ID_Device>{escape_xml_text(device_id)}</ID_Device>"
            f"<ID_Set>{wire_id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{escape_xml_text(wire_dt)}</DT>"
            f"<NewValue>{escape_xml_text(value_text)}</NewValue>"
            "<Confirm>New</Confirm>"
            f"<TblName>{escape_xml_text(table_name)}</TblName>"
            f"<TblItem>{escape_xml_text(item_name)}</TblItem>"
            "<ID_Server>9</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{escape_xml_text(tsec_text)}</TSec>"
            f"<ver>{ver_text}</ver>"
        ).encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("dynamic Setting text is not valid XML 1.0") from error
    crc_text = f"{crc16_modbus(inner):05d}"
    wire = (
        b"<Frame>"
        + inner
        + b"<CRC>"
        + crc_text.encode("ascii")
        + b"</CRC></Frame>\r\n"
    )
    return RenderedSettingFrame(wire, crc_text, len(wire))
# pylint: enable=too-many-arguments,too-many-locals
