from __future__ import annotations

import re
from typing import Any

_RESULT_RE = re.compile(rb"<Result>(ACK|END|NACK)</Result>")
_TABLE_RE = re.compile(rb"<TblName>([^<]+)</TblName>")
_TODO_RE = re.compile(rb"<ToDo>([^<]+)</ToDo>")
_DT_RE = re.compile(rb"<DT>([^<]+)</DT>")
_REASON_RE = re.compile(rb"<Reason>([^<]+)</Reason>")
_TBL_EVENT_CONTENT_RE = re.compile(
    r"Remotely\s*:\s*([A-Za-z0-9_]+)\s*/\s*([A-Za-z0-9_]+)\s*:\s*\[[^\]]*\]->\[([^\]]*)\]"
)


def parse_box_ack(xml_bytes: bytes) -> dict[str, str] | None:
    result_match = _RESULT_RE.search(xml_bytes)
    if not result_match:
        return None

    result: dict[str, str] = {"result": result_match.group(1).decode("utf-8", errors="replace")}

    table_match = _TABLE_RE.search(xml_bytes)
    if table_match:
        result["table"] = table_match.group(1).decode("utf-8", errors="replace")

    todo_match = _TODO_RE.search(xml_bytes)
    if todo_match:
        result["todo"] = todo_match.group(1).decode("utf-8", errors="replace")

    dt_match = _DT_RE.search(xml_bytes)
    if dt_match:
        result["timestamp"] = dt_match.group(1).decode("utf-8", errors="replace")

    reason_match = _REASON_RE.search(xml_bytes)
    if reason_match:
        result["reason"] = reason_match.group(1).decode("utf-8", errors="replace")

    return result


def parse_tbl_events_ack(parsed_frame: dict[str, Any]) -> dict[str, str] | None:
    table_name = parsed_frame.get("_table")
    if table_name != "tbl_events":
        return None

    event_type = parsed_frame.get("Type")
    if event_type != "Setting":
        return None

    content = parsed_frame.get("Content")
    if not isinstance(content, str):
        return None

    m = _TBL_EVENT_CONTENT_RE.search(content)
    if not m:
        return None

    return {
        "table": m.group(1),
        "key": m.group(2),
        "value": m.group(3),
    }
