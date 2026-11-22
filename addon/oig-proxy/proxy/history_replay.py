"""Classify native history and upload one measurement on its own Cloud socket."""

from __future__ import annotations

import asyncio
from datetime import datetime

from protocol.frame import FRAME_END, MAX_FRAME_BYTES
from protocol.ver_profile import native_frame_fields

HISTORY_TABLES = frozenset({
    "tbl_ac_in", "tbl_ac_in2", "tbl_ac_in_b", "tbl_ac_out", "tbl_batt",
    "tbl_box", "tbl_dc_in", "tbl_invertor",
})
HISTORY_TAGS = tuple(f"<TblName>{table}</TblName>".encode("ascii") for table in HISTORY_TABLES)
REPLY_TIMEOUT_S = 10.0


def history_device(frame: bytes) -> str | None:
    """Return the origin of a CRC-valid, timestamped measurement table."""
    fields = native_frame_fields(frame, allow_repeated_identity=False)
    if not fields or fields.get("TblName") not in HISTORY_TABLES:
        return None
    if fields.get("Reason") != "Table" or "Result" in fields or "TblItem" in fields:
        return None
    device = fields.get("ID_Device")
    sequence = fields.get("ID_Set", "")
    if not device or not sequence.isascii() or not sequence.isdecimal():
        return None
    try:
        datetime.fromisoformat(fields.get("DT", ""))
    except ValueError:
        return None
    return device


async def upload_history(host: str, port: int, frame: bytes, connect_timeout: float) -> bytes:
    """Use a fresh connection because Cloud measurement ACKs are anonymous."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, limit=MAX_FRAME_BYTES),
        timeout=connect_timeout,
    )
    try:
        # The optional delimiter may have arrived in a separate TCP read on intake.
        writer.write(frame.rstrip(b"\r\n") + b"\r\n")
        await asyncio.wait_for(writer.drain(), timeout=REPLY_TIMEOUT_S)
        return await asyncio.wait_for(reader.readuntil(FRAME_END), timeout=REPLY_TIMEOUT_S)
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=REPLY_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError):
            pass


def acknowledges_history(reply: bytes) -> bool:
    """Accept only the known bare measurement ACK or its GetActual variant."""
    fields = native_frame_fields(reply, allow_repeated_identity=False)
    return (
        fields is not None and fields.get("Result") == "ACK"
        and fields.get("ToDo", "GetActual") == "GetActual"
        and fields.keys() <= {"Result", "ToDo", "CRC"}
    )
