"""Recover same-Box outbound evidence from the local legacy frame archive."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET  # nosec B405

from .ver_profile import (
    BOX_POLL_RESULTS, VerProfile, _FIRMWARE_ID, _SESSION_WINDOW, _SETTING_REPLY_ECHOES, _Wire,
    _parse, _receipt_fields, _setting_layout, recent_protocol_timestamp,
)

_ARCHIVE_DAYS = 7
_MAX_ROWS = 250_000
_MAX_FRAME_BYTES = 65_536
_BOX_DIRECTION = "box_to_cloud"
_OFFLINE_DIRECTION = "box_to_proxy"
_INBOUND = {_BOX_DIRECTION, _OFFLINE_DIRECTION}
_CLOUD_DIRECTION = "cloud_to_box"
_OUTBOUND = {_CLOUD_DIRECTION, "proxy_to_box"}
_REASON_TAG = b"<Reason"
_RESULT_TAG = b"<Result"
_LEGACY_WEATHER_HEADER = b"<Frame><Reason>Weather</Reason><Result>Weather</Result>"


@dataclass(frozen=True)
class ArchiveEnrollment:
    """An accepted outbound layout and both observed outbound counter maxima."""

    include_tto: bool
    msg_id: int
    id_set: int
    accepted_at: float
    firmware: str | None = None


@dataclass(frozen=True)
class _Packet:
    capture_time: float
    conn_id: int
    wire: _Wire
    row_id: int


@dataclass(frozen=True)
class _MatchedSetting:
    enrollment: ArchiveEnrollment
    ack_order: tuple[float, int]


@dataclass(frozen=True)
class _FirmwareReport:
    order: tuple[float, int]
    value: str


@dataclass
class _Bracket:
    ack: _Packet
    cloud_conn_id: int | None = None
    setting: _Packet | None = None


@dataclass(frozen=True)
class _CaptureRow:  # pylint: disable=too-many-instance-attributes
    row_id: int
    stamp: str
    device: str | None
    raw: str | None
    encoded: str | None
    direction: str | None
    task: int | None
    peer: str | None
    length: int | None


@lru_cache(maxsize=512)
def _valid_peer(peer: object) -> bool:
    if not isinstance(peer, str) or len(peer) > 100:
        return False
    address, separator, port = peer.rpartition(":")
    if not separator or not port.isascii() or not port.isdecimal() or not 0 < int(port) <= 65535:
        return False
    try:
        ip_address(address)
    except ValueError:
        return False
    return True


def _raw_bytes(raw: object, encoded: object, length: object) -> bytes | None:
    try:
        if encoded is not None:
            if not isinstance(encoded, str) or len(encoded) > 2 * _MAX_FRAME_BYTES:
                return None
            wire = b64decode(encoded, validate=True)
        elif isinstance(raw, str) and len(raw) <= _MAX_FRAME_BYTES:
            wire = raw.encode()
        else:
            return None
    except (Base64Error, UnicodeError, ValueError):
        return None
    if len(wire) > _MAX_FRAME_BYTES or (length is not None and length != len(wire)):
        return None
    return wire


def _capture_time(stamp: object) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _counters(fields: dict[str, str]) -> tuple[int, int] | None:
    msg_id = fields.get("ID", "")
    id_set = fields.get("ID_Set", "")
    for value, maximum in ((msg_id, 2**31 - 1), (id_set, 9_999_999_999)):
        if not (value.isascii() and value.isdecimal() and len(value) <= 10
                and 0 < int(value) <= maximum):
            return None
    return int(msg_id), int(id_set)


def _complete_frame(data: bytes | None) -> bool:
    if data is None:
        return False
    return data.startswith(b"<Frame>") and data.rstrip(b"\r\n").endswith(b"</Frame>")


def _may_hide_setting(data: bytes | None) -> bool:
    """Classify an unparsed outbound frame without trusting counters from it."""
    if data is None or not _complete_frame(data) or b"<!DOCTYPE" in data:
        return True
    if _REASON_TAG not in data:
        return False
    try:
        # Weather can be well-formed XML with repeated native fields.
        frame = ET.fromstring(data)  # nosec B314
    except ET.ParseError:
        # Legacy Weather also embeds broken XML in Bin. Its exact header
        # supplies the only Reason and Result, leaving no Setting to recover.
        return not (
            data.startswith(_LEGACY_WEATHER_HEADER)
            and data.count(_REASON_TAG) == 1
            and data.count(_RESULT_TAG) == 1
        )
    return any(node.tag == "Reason" and (len(node) or (node.text or "").strip() == "Setting")
               for node in frame)


def _ack_matches(setting: _Packet, ack: _Packet) -> bool:
    receipt = _receipt_fields(ack.wire)
    tsec = recent_protocol_timestamp(
        setting.wire.fields.get("TSec", ""), setting.capture_time, utc=True
    )
    rdt = recent_protocol_timestamp(ack.wire.fields.get("Rdt", ""), ack.capture_time)
    return bool(
        receipt is not None and tsec is not None and rdt is not None
        and 0 <= ack.capture_time - setting.capture_time <= _SESSION_WINDOW
        and setting.capture_time - tsec <= _SESSION_WINDOW
        and rdt >= tsec - 2
        and all(
            name not in receipt or receipt[name] == setting.wire.fields[field]
            for name, field in _SETTING_REPLY_ECHOES.items()
        )
    )


def _reported_firmware(
    row: _CaptureRow, capture_time: float, device_id: str, profile: VerProfile,
) -> _FirmwareReport | None:
    if row.direction not in _INBOUND or row.device not in (device_id, None, "unknown"):
        return None
    if row.encoded is None and (not isinstance(row.raw, str)
                                or ("<Fw" not in row.raw and "<SW" not in row.raw)):
        return None
    data = _raw_bytes(row.raw, row.encoded, row.length)
    wire = (_parse(data, allow_repeated_identity=True)
            if data is not None and (b"<Fw" in data or b"<SW" in data) else None)
    if wire is None or wire.fields.get("ID_Device") != device_id or not wire.matches(profile):
        return None
    fields = wire.fields
    stamp: float | None = capture_time
    if fields.get("Result") == "IsNewFW":
        value = fields.get("Fw", "")
    elif fields.get("TblName") == "tbl_box_prms" and fields.get("Reason") == "Table":
        stamp = recent_protocol_timestamp(fields.get("DT", ""), capture_time)
        value = fields.get("SW", "")
    else:
        return None
    if stamp is None or not _FIRMWARE_ID.fullmatch(value):
        return None
    return _FirmwareReport((min(stamp, capture_time), row.row_id), value)


class _Scan:  # pylint: disable=too-few-public-methods
    """Walk backward so any later candidate or missing evidence fences recovery."""

    def __init__(self, device_id: str, profile: VerProfile) -> None:
        self.device_id = device_id
        self.profile = profile
        self.brackets: dict[str, _Bracket] = {}
        self.layouts: set[bool] = set()
        self.high_water = (0, 0)
        self.contradiction = False

    def _observe_outbound(self, wire: _Wire) -> None:
        """Account for every exact later outbound Setting, including unconfirmed attempts."""
        layout = _setting_layout(wire)
        counters = _counters(wire.fields)
        if layout is None or counters is None or not wire.matches(self.profile):
            self.contradiction = True
            return
        self.layouts.add(layout)
        self.high_water = max(self.high_water[0], counters[0]), max(self.high_water[1], counters[1])

    def observe_row(self, row: _CaptureRow, capture_time: float) -> _MatchedSetting | None:
        """Classify one capture record and inspect bytes where they can affect proof."""
        direction = row.direction
        endpoint = row.peer if _valid_peer(row.peer) else None
        own_capture = row.device in (self.device_id, None, "unknown")
        if direction == _BOX_DIRECTION and not own_capture:
            self._break_bracket(endpoint, own_capture)
            return None
        if direction is None or (direction not in _OUTBOUND and direction != _BOX_DIRECTION):
            self._break_bracket(endpoint, own_capture)
            self.contradiction = own_capture and direction != _OFFLINE_DIRECTION
            return None
        data = _raw_bytes(row.raw, row.encoded, row.length)
        has_reason_tag = data is not None and _REASON_TAG in data
        if not self._should_parse(direction, endpoint, data, has_reason_tag):
            if direction in _OUTBOUND and own_capture and not _complete_frame(data):
                self.contradiction = True
            return None
        wire = _parse(data, allow_repeated_identity=direction == _BOX_DIRECTION) if data else None
        if wire is None:
            if direction in _OUTBOUND and own_capture and _may_hide_setting(data):
                self.contradiction = True
            self._break_bracket(endpoint, own_capture)
            return None
        if (direction in _OUTBOUND and wire.fields.get("ID_Device") == self.device_id
                and wire.fields.get("Reason") == "Setting"):
            self._observe_outbound(wire)
            if not own_capture or len(self.layouts) > 1:
                self.contradiction = True
        if endpoint is None or not isinstance(row.task, int) or row.task <= 0:
            self._break_bracket(endpoint, own_capture)
            return None
        return self._observe(endpoint, direction, _Packet(capture_time, row.task, wire, row.row_id))

    def _should_parse(
        self, direction: str, peer: str | None, data: bytes | None, has_reason_tag: bool,
    ) -> bool:
        if peer in self.brackets or (peer is None and self.brackets):
            return True
        if direction in _OUTBOUND:
            return has_reason_tag
        return data is not None and has_reason_tag and _RESULT_TAG in data

    def _break_bracket(self, peer: str | None, own_capture: bool) -> None:
        if peer is None:
            if own_capture:
                self.brackets.clear()
        else:
            self.brackets.pop(peer, None)

    def _observe(self, peer: str, direction: str, packet: _Packet) -> _MatchedSetting | None:
        wire = packet.wire
        fields = wire.fields
        result = fields.get("Result")
        receipt = _receipt_fields(wire)
        candidate = self.brackets.get(peer)
        outside = candidate is not None and not (
            0 <= candidate.ack.capture_time - packet.capture_time <= _SESSION_WINDOW
        )
        identity = fields.get("ID_Device")
        if outside or identity not in (self.device_id, None):
            self.brackets.pop(peer, None)
            candidate = None
        if result in {"END", "NACK"} or identity not in (self.device_id, None):
            self.brackets.pop(peer, None)
        elif (direction == _BOX_DIRECTION and result == "ACK"
              and receipt is not None and receipt.get("Reason") == "Setting"):
            self.brackets.pop(peer, None)
            if wire.ver is None or wire.matches(self.profile):
                self.brackets[peer] = _Bracket(packet)
        elif candidate is not None:
            return self._continue(peer, direction, packet, candidate)
        return None

    def _continue(
        self, peer: str, direction: str, packet: _Packet, bracket: _Bracket
    ) -> _MatchedSetting | None:
        fields = packet.wire.fields
        if direction == _CLOUD_DIRECTION:
            if (packet.conn_id == bracket.ack.conn_id
                    or bracket.cloud_conn_id not in (None, packet.conn_id)):
                self.brackets.pop(peer, None)
                return None
            bracket.cloud_conn_id = packet.conn_id
        if direction in _OUTBOUND and fields.get("Reason") == "Setting":
            if (direction != _CLOUD_DIRECTION or bracket.setting is not None
                    or fields.get("ID_Device") != self.device_id
                    or not _ack_matches(packet, bracket.ack)):
                self.brackets.pop(peer, None)
            else:
                bracket.setting = packet
        elif direction == _BOX_DIRECTION:
            changed_model = packet.wire.ver is not None and not packet.wire.matches(self.profile)
            if packet.conn_id != bracket.ack.conn_id or changed_model:
                self.brackets.pop(peer, None)
            elif fields.get("Result") in BOX_POLL_RESULTS:
                self.brackets.pop(peer, None)
                setting = bracket.setting
                if (setting is not None and packet.wire.matches(self.profile)
                        and fields.get("ID_Device") == self.device_id
                        and 0 <= setting.capture_time - packet.capture_time <= _SESSION_WINDOW
                        and len(self.layouts) == 1):
                    layout = _setting_layout(setting.wire)
                    if layout is not None:
                        return _MatchedSetting(
                            ArchiveEnrollment(layout, *self.high_water, bracket.ack.capture_time),
                            (bracket.ack.capture_time, bracket.ack.row_id),
                        )
        return None


_READ = """
SELECT id, ts, device_id, raw, raw_b64, direction, conn_id, peer, length
FROM frames
WHERE id <= (
    SELECT max(id) FROM frames WHERE ts < ? AND ts >= ?
)
ORDER BY id DESC LIMIT ?
"""


def _contiguous_rows(
    rows: sqlite3.Cursor, start: float, cutoff: float,
) -> Iterator[tuple[_CaptureRow, float]]:
    previous = None
    for fields in rows:
        row = _CaptureRow(*fields)
        at = _capture_time(row.stamp)
        if ((previous is not None and row.row_id != previous - 1)
                or at is None or not start <= at < cutoff):
            break
        previous = row.row_id
        yield row, at


def _with_firmware(
    match: _MatchedSetting, reports: list[_FirmwareReport],
    preceding: Iterator[tuple[_CaptureRow, float]], archive: _Scan,
) -> ArchiveEnrollment | None:
    prior = max((report for report in reports if report.order <= match.ack_order),
                key=lambda report: report.order, default=None)
    later = {report.value for report in reports if report.order > match.ack_order}
    if len(later) > 1:
        return None
    for row, at in preceding:
        if prior is not None and at < prior.order[0]:
            break
        report = _reported_firmware(row, at, archive.device_id, archive.profile)
        if report is None:
            continue
        if report.order > match.ack_order:
            later.add(report.value)
        elif prior is None or report.order > prior.order:
            prior = report
    if len(later) > 1:
        return None
    if prior is None:
        return match.enrollment
    if later and prior.value not in later:
        return None
    return replace(match.enrollment, firmware=prior.value)


def _scan_rows(
    rows: sqlite3.Cursor, archive: _Scan, start: float, cutoff: float
) -> ArchiveEnrollment | None:
    captures = _contiguous_rows(rows, start, cutoff)
    reports = []
    for row, at in captures:
        report = _reported_firmware(row, at, archive.device_id, archive.profile)
        if report is not None:
            reports.append(report)
        evidence = archive.observe_row(row, at)
        if archive.contradiction:
            break
        if evidence is not None:
            return _with_firmware(evidence, reports, captures, archive)
    return None


def scan_archive_enrollment(
    path: str | Path, device_id: str, profile: VerProfile, *, cutoff: float
) -> ArchiveEnrollment | None:
    """Scan a bounded read-only SQLite snapshot before the exclusive UTC cutoff.

    Legacy capture stores the Box IP and port in both directions. Task IDs
    identify a pipe, so the Box poll and ACK must share a task while the
    intervening Cloud Setting must use one different Cloud task.
    """
    try:
        end = datetime.fromtimestamp(cutoff, timezone.utc).replace(microsecond=0)
        start = end - timedelta(days=_ARCHIVE_DAYS)
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as connection:
            cursor = connection.execute(_READ, (end.isoformat(), start.isoformat(), _MAX_ROWS))
            return _scan_rows(cursor, _Scan(device_id, profile), start.timestamp(), end.timestamp())
    except (sqlite3.Error, OSError, ValueError, OverflowError):
        return None
