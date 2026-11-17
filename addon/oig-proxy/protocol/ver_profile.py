"""Passive per-device qualification of the OIG Setting wire profile."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
from math import ceil
import os
from pathlib import Path
import re
import tempfile
import time
import xml.etree.ElementTree as ET  # nosec B405
from zoneinfo import ZoneInfo

from .crc import _crc16_table, crc16_modbus
from .frame import MAX_FRAME_BYTES

_MAX_AGE = 900
_SESSION_WINDOW = 60
_MAX_CLOCK_ADVANCE = 300
_CLOCK_SAMPLE_SPAN = (10, 120)
_CLOCK_SAMPLE_SPREAD = 5
_CLOUD_CLOCK_DIFFERENCE = 20
_CLOUD_CLOCK_ROUNDTRIP = 10
_CLOUD_CLOCK_ORDER = ("Result", "Time", "UTCTime", "CRC")
_MAX_DEVICES = 128
_CRC_PERIOD = 32_767
_LOCAL_ZONE = ZoneInfo("Europe/Prague")
BOX_POLL_RESULTS = frozenset({"IsNewFW", "IsNewSet", "IsNewWeather"})
_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_FIRMWARE_ID = re.compile(r"[!-~]{1,64}")
_TAIL = re.compile(rb"(?:(<ver>)([0-9]{1,5})</ver>)?<CRC>([0-9]{1,5})</CRC></Frame>$")
_SETTING_HEAD = (
    "ID", "ID_Device", "ID_Set", "ID_SubD", "DT", "NewValue", "Confirm", "TblName",
    "TblItem",
)
_SETTING_TAIL = ("ID_Server", "mytimediff", "Reason", "TSec", "ver", "CRC")
_SETTING_WITH_TTO = _SETTING_HEAD + ("TTO_P", "TTO_E") + _SETTING_TAIL
_SETTING_WITHOUT_TTO = _SETTING_HEAD + _SETTING_TAIL
_SETTING_CONSTANTS = {"ID_SubD": "0", "Confirm": "New", "ID_Server": "9", "mytimediff": "0"}
_SETTING_REPLY_ECHOES = {
    "ID": "ID", "ID_Set": "ID_Set", "TblName": "TblName", "TblItem": "TblItem", "ToDo": "TblItem",
}
_COMBINED_ACTUAL_ORDER = (
    "Result", "Rdt", "Reason", "TblName", "ID_Set", "DT", "Reason", "ID_Device",
    "ENBL", "VIZ", "Temp", "Humid", "ACO_P", "FV_P1", "FV_P2", "ACI_WR", "ACI_WS", "ACI_WT",
    "BAT_P", "FV_PROC", "BAT_C", "MSC_SELF", "STAT", "FV_AD", "FVE_PROD", "INFLOW", "LOAD", "MAXFG",
    "STATX", "ACINB_WR", "ACINB_WS", "ACINB_WT", "ACO_PR", "ACO_PS", "ACO_PT", "ver", "CRC",
)
_COMBINED_RECEIPT_TAGS = ("Result", "Rdt", "ID_Device", "ver", "CRC")
_LOGGER = logging.getLogger(__name__)


def _linear_apply(columns: tuple[int, ...], state: int) -> int:
    value = 0
    while state:
        bit = state & -state
        value ^= columns[bit.bit_length() - 1]
        state ^= bit
    return value


@lru_cache(maxsize=64)
def _advance_columns(count: int) -> tuple[int, ...]:
    if count == 0:
        return tuple(1 << i for i in range(16))
    half = _advance_columns(count // 2)
    squared = tuple(_linear_apply(half, column) for column in half)
    if count % 2:
        table = _crc16_table()
        return tuple((state >> 8) ^ table[state & 255] for state in squared)
    return squared


@dataclass(frozen=True)
class VerProfile:
    """A canonical zero-byte CRC advance followed by a fixed XOR."""

    zero_count: int
    xor_key: int

    def __post_init__(self) -> None:
        if not (0 <= self.zero_count < _CRC_PERIOD and 0 <= self.xor_key <= 65535):
            raise ValueError("ver profile is outside the canonical CRC family")

    def compute(self, prefix: bytes) -> int:
        return self._state_value(crc16_modbus(prefix))

    def _state_value(self, state: int) -> int:
        return _linear_apply(_advance_columns(self.zero_count), state) ^ self.xor_key


@dataclass(frozen=True)
class ProfileStatus:
    profile: VerProfile | None
    reason: str
    epoch: int
    include_tto: bool = True


@dataclass(frozen=True)
class _Wire:
    fields: dict[str, str]
    state: int | None
    ver: int | None
    order: tuple[str, ...]
    combined_actual: bool = False

    def matches(self, profile: VerProfile) -> bool:
        return self.state is not None and self.ver == profile._state_value(self.state)


def _parse(raw: bytes, *, allow_repeated_identity: bool) -> _Wire | None:
    raw = raw.rstrip(b"\r\n")
    if len(raw) > MAX_FRAME_BYTES or not raw.startswith(b"<Frame>") or b"<!DOCTYPE" in raw:
        return None
    tail = _TAIL.search(raw)
    if tail is None:
        return None
    crc_at = tail.start(3) - len(b"<CRC>")
    if crc16_modbus(raw[7:crc_at]) != int(tail[3]):
        return None
    try:
        # The bounded native frame starts at the root and permits no DTD.
        frame = ET.fromstring(raw)  # nosec B314
    except ET.ParseError:
        return None
    order = tuple(node.tag for node in frame)
    # In this legacy frame the first Reason belongs to the receipt and the
    # second Reason belongs to the actual table. Other duplicate Reasons are invalid.
    combined_actual = (
        order == _COMBINED_ACTUAL_ORDER and frame[0].text == "ACK" and frame[2].text == "Setting"
        and frame[3].text == "tbl_actual" and frame[6].text == "Table"
        and _DEVICE_ID.fullmatch(frame[7].text or "") is not None
    )
    fields: dict[str, str] = {}
    for node in frame:
        value = node.text or ""
        if node.tag in fields and not (
            (allow_repeated_identity and node.tag == "ID_Device" and fields[node.tag] == value)
            or (combined_actual and node.tag == "Reason")
        ):
            return None
        if len(node) or node.attrib:
            return None
        fields[node.tag] = value
    if frame.tag != "Frame" or frame.attrib:
        return None
    if tail[1] is None:
        return _Wire(fields, None, None, order) if "ver" not in fields else None
    ver = int(tail[2])
    if ver > 65535 or fields.get("ver") != tail[2].decode("ascii"):
        return None
    return _Wire(fields, crc16_modbus(raw[7:tail.start(1)]), ver, order, combined_actual)


def _receipt_fields(wire: _Wire) -> dict[str, str] | None:
    if wire.fields.get("Result") not in {"ACK", "NACK"}:
        return None
    if not wire.combined_actual:
        return wire.fields
    return {**{tag: wire.fields[tag] for tag in _COMBINED_RECEIPT_TAGS}, "Reason": "Setting"}


def native_frame_fields(raw: bytes, *, allow_repeated_identity: bool = True) -> dict[str, str] | None:
    """Decode an exact CRC-valid native frame for transport classification."""
    wire = _parse(raw, allow_repeated_identity=allow_repeated_identity)
    return wire.fields if wire is not None else None


def native_receipt_fields(raw: bytes) -> dict[str, str] | None:
    """Read receipt fields from CRC-valid native bytes, excluding attached telemetry."""
    wire = _parse(raw, allow_repeated_identity=False)
    return _receipt_fields(wire) if wire is not None else None


def native_frame_identity(raw: bytes) -> str | None:
    """Return a native identity only after validating the complete frame."""
    fields = native_frame_fields(raw)
    device_id = fields.get("ID_Device") if fields is not None else None
    return device_id if device_id and _DEVICE_ID.fullmatch(device_id) else None


def matches_setting_poll(raw: bytes, device_id: str, profile: VerProfile) -> bool:
    """Accept only an exact native poll carrying the selected ver profile."""
    wire = _parse(raw, allow_repeated_identity=False)
    return (wire is not None and wire.fields.get("Result") in BOX_POLL_RESULTS
            and wire.fields.get("ID_Device") == device_id and wire.matches(profile))


def parse_setting_reply(raw: bytes, device_id: str, profile: VerProfile) -> dict[str, str] | None:
    """Validate a flat native response and its exact checksums before correlation."""
    wire = _parse(raw, allow_repeated_identity=False)
    if wire is None or wire.fields.get("ID_Device", device_id) != device_id:
        return None
    if wire.fields["Result"] == "ACK" and wire.ver is not None and not wire.matches(profile):
        return None
    return _receipt_fields(wire)


def _protocol_stamps(value: str, utc: bool = False, *, naive_only: bool = False) -> set[float]:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
        except ValueError:
            return set()
    try:
        if parsed.tzinfo is not None:
            stamps = set() if naive_only else {parsed.timestamp()}
        elif utc:
            stamps = {parsed.replace(tzinfo=timezone.utc).timestamp()}
        else:
            stamps = set()
            for fold in (0, 1):
                stamp = parsed.replace(tzinfo=_LOCAL_ZONE, fold=fold).timestamp()
                if datetime.fromtimestamp(stamp, _LOCAL_ZONE).replace(tzinfo=None) == parsed:
                    stamps.add(stamp)
    except (OSError, OverflowError, ValueError):
        return set()
    return stamps


def _unique_stamp(stamps: set[float], earliest: float, latest: float) -> float | None:
    valid = [stamp for stamp in stamps if earliest <= stamp <= latest]
    return valid[0] if len(valid) == 1 else None


def recent_protocol_timestamp(value: str, now: float, utc: bool = False) -> float | None:
    """Resolve a recent native timestamp on the protocol clock."""
    return _unique_stamp(_protocol_stamps(value, utc), now - _MAX_AGE, now + 2)


def _positive_counter(value: str) -> int | None:
    return (int(value) or None) if len(value) <= 19 and value.isascii() and value.isdecimal() else None


@dataclass(frozen=True)
class _Sample:
    wire: _Wire
    at: float
    session: str | None

    def matches(self, profile: VerProfile) -> bool:
        return self.wire.matches(profile)


@dataclass(frozen=True)
class _Pending:
    sample: _Sample
    received: float
    epoch: int
    include_tto: bool
    future_cloud_stamp: float | None = None


def _identify(samples: list[_Sample]) -> tuple[VerProfile | None, bool]:
    """Return the unique operator or whether the observations are inconsistent."""
    states = {sample.wire.state: sample.wire.ver for sample in samples}
    if len(states) < len({(sample.wire.state, sample.wire.ver) for sample in samples}):
        return None, True
    anchor = samples[0].wire
    if anchor.state is None or anchor.ver is None:
        return None, True
    for state, value in states.items():
        if state is None or value is None:
            return None, True
        delta = anchor.state ^ state
        if delta not in (0, 0xC001):
            break
        if delta != anchor.ver ^ value:
            return None, True
    else:
        return None, False
    target = anchor.ver ^ value
    table = _crc16_table()
    survivors = []
    for count in range(_CRC_PERIOD):
        if delta == target:
            profile = VerProfile(count, anchor.ver ^ _linear_apply(_advance_columns(count), anchor.state))
            if all(sample.matches(profile) for sample in samples):
                survivors.append(profile)
        delta = (delta >> 8) ^ table[delta & 255]
    if len(survivors) == 1:
        return survivors[0], False
    return None, not survivors


@dataclass(frozen=True)
class _Enrollment:
    profile: VerProfile
    firmware: str | None
    include_tto: bool


@dataclass(frozen=True)
class _ClockReading:
    received: float
    source: float
    counter: int

    @property
    def advance(self) -> float:
        return self.source - self.received


@dataclass(frozen=True)
class _CloudClockReading:
    poll: _Sample
    received: float
    source: float
    wall_bias: float

    @property
    def advance(self) -> float:
        return self.source - self.received


@dataclass
class _DeviceProof:
    epoch: int = 0
    candidate: VerProfile | None = None
    basis: frozenset[int] = frozenset()
    firmware: _Sample | None = None
    samples: deque[_Sample] = field(default_factory=lambda: deque(maxlen=256))
    seen: dict[tuple[int, int, float], float] = field(default_factory=dict)
    latest_box: float = 0
    cutoff: float = 0
    ready: bool = False
    ever_ready: bool = False
    ambiguous_layout: bool = False
    block: str = "waiting_for_box"
    clock_offset: float = 0.0
    clock_epoch: int = 0
    clock_readings: deque[_ClockReading] = field(default_factory=lambda: deque(maxlen=3))
    clock_step_back: bool = False
    readback_source_floor: float = 0.0
    cloud_clock_readings: deque[_CloudClockReading] = field(default_factory=lambda: deque(maxlen=2))
    cloud_clock_polls: dict[tuple[int, int], float] = field(default_factory=dict)
    cloud_wall_bias: float | None = None
    future_cloud_highwater: float = 0.0
    live_cloud_seen: bool = False


@dataclass
class _Flow:
    device_id: str
    occupied: bool = False
    quarantined: bool = False
    last_native: _Sample | None = None
    last_dated: _Sample | None = None
    firmware_poll: _Sample | None = None
    pending: _Pending | None = None
    clock_poll: tuple[_Sample, float] | None = None
    clock_poll_used: bool = False


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate profile registry key")
    return result


def _setting_layout(wire: _Wire) -> bool | None:
    """Identify a wire layout only when its fixed fields match the serializer."""
    if any(wire.fields.get(tag) != value for tag, value in _SETTING_CONSTANTS.items()):
        return None
    if wire.order == _SETTING_WITHOUT_TTO:
        return False
    if wire.order == _SETTING_WITH_TTO and wire.fields["TTO_P"] == wire.fields["TTO_E"] == "0,000":
        return True
    return None


class VerProfiles:
    """Learn native BOX models and enroll acknowledged Cloud Setting layouts."""

    def __init__(
        self, *, clock: Callable[[], float] = time.time, state_path: Path | str | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock
        self._monotonic = monotonic or (time.monotonic if clock is time.time else clock)
        self._started = clock()
        self._state_path = Path(state_path) if state_path is not None else None
        self._proofs: dict[str, _DeviceProof] = {}
        self._flows: dict[str, _Flow] = {}
        self._enrollments, self._archive_registry_cold = self._load_enrollments()

    def _load_enrollments(self) -> tuple[dict[str, _Enrollment], bool]:
        path = self._state_path
        if path is None:
            return {}, True
        try:
            with path.open("rb") as stream:
                raw = stream.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("profile state is too large")
            data = json.loads(raw, object_pairs_hook=_unique_json_object)
            if (not isinstance(data, dict) or set(data) != {"version", "enrollments"}
                    or type(data["version"]) is not int or data["version"] != 1):
                raise ValueError("unsupported profile state version")
            records = data.get("enrollments")
            if not isinstance(records, dict) or len(records) > _MAX_DEVICES:
                raise ValueError("invalid profile state registry")
            result = {}
            for device_id, record in records.items():
                if (not _DEVICE_ID.fullmatch(device_id) or not isinstance(record, dict)
                        or set(record) != {"N", "K", "firmware", "include_tto"}):
                    raise ValueError("invalid enrolled device")
                n, k = record.get("N"), record.get("K")
                firmware, include_tto = record.get("firmware"), record.get("include_tto")
                if (type(n) is not int or type(k) is not int or type(include_tto) is not bool
                        or (firmware is not None
                            and (not isinstance(firmware, str) or not _FIRMWARE_ID.fullmatch(firmware)))):
                    raise ValueError("invalid enrolled profile")
                result[device_id] = _Enrollment(VerProfile(n, k), firmware, include_tto)
            return result, False
        except FileNotFoundError:
            return {}, True
        except (OSError, ValueError, RecursionError):
            _LOGGER.warning("Ignoring invalid retained wire profile state")
            return {}, False

    def _save_enrollments(self, *, invalidate_on_failure: bool = True) -> bool:
        path = self._state_path
        if path is None:
            self._archive_registry_cold = False
            return True
        records = {
            device: {"N": item.profile.zero_count, "K": item.profile.xor_key,
                     "firmware": item.firmware, "include_tto": item.include_tto}
            for device, item in self._enrollments.items()
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".ver-profile-", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "enrollments": records}, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            _LOGGER.warning("Could not save wire profile state")
            if invalidate_on_failure:
                # An empty or interrupted registry remains initialized on restart.
                # Removing it would make revoked archive evidence eligible again.
                try:
                    with path.open("w", encoding="utf-8") as tombstone:
                        json.dump({"version": 1, "enrollments": {}}, tombstone)
                        tombstone.flush()
                        os.fsync(tombstone.fileno())
                except OSError:
                    _LOGGER.warning("Could not invalidate previous wire profile state")
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    _LOGGER.debug("Could not remove temporary wire profile state")
        self._archive_registry_cold = False
        return True

    def _discard_enrollment(self, device_id: str) -> None:
        if self._enrollments.pop(device_id, None) is not None or self._archive_registry_cold:
            # Preserve the initialized registry, even when the last entry is removed.
            self._archive_registry_cold = False
            self._save_enrollments()

    def _withdraw_layout(self, device_id: str, proof: _DeviceProof) -> None:
        proof.ambiguous_layout = True
        self._discard_enrollment(device_id)
        self.status(device_id)

    def _retire(self, device_id: str, proof: _DeviceProof, reason: str) -> None:
        self._discard_enrollment(device_id)
        proof.ambiguous_layout = False
        proof.clock_epoch += 1
        proof.clock_readings.clear()
        proof.cloud_clock_readings.clear()
        proof.clock_step_back = False
        self._reset_native_proof(device_id, proof, reason)

    def _reset_native_proof(self, device_id: str, proof: _DeviceProof, reason: str) -> None:
        proof.epoch += 1
        proof.candidate = None
        proof.basis = frozenset()
        proof.firmware = None
        proof.samples.clear()
        proof.cutoff = max(proof.cutoff, proof.latest_box)
        proof.ready = proof.ever_ready = False
        proof.block = reason
        for flow in self._flows.values():
            if flow.device_id == device_id:
                flow.pending = flow.firmware_poll = flow.last_dated = None
                flow.last_native = flow.clock_poll = None

    def box_clock_offset(self, device_id: str) -> float:
        """Return the learned positive BOX clock shift, or zero without evidence."""
        proof = self._proofs.get(device_id)
        if proof is None:
            return 0.0
        self._check_wall_clock(device_id, proof, self._clock())
        return proof.clock_offset

    def box_clock_epoch(self, device_id: str) -> int:
        """Return the device-clock and wire-proof revision for readback correlation."""
        proof = self._proofs.get(device_id)
        if proof is None:
            return 0
        self._check_wall_clock(device_id, proof, self._clock())
        return proof.clock_epoch

    def box_timestamp(self, device_id: str, value: str) -> float | None:
        """Resolve BOX DT/Rdt with the current clock shift without learning from it."""
        shift = self.box_clock_offset(device_id)
        source = self._box_source_timestamp(value, self._clock(), shift)
        return source - shift if source is not None else None

    def box_readback_timestamp(self, device_id: str, value: str) -> float | None:
        """Resolve only native data from the running process and current proof epoch."""
        stamp = self.box_timestamp(device_id, value)
        proof = self._proofs.get(device_id)
        if stamp is None or stamp <= self._started:
            return None
        if proof is not None and (stamp < proof.cutoff
                                  or stamp + proof.clock_offset <= proof.readback_source_floor):
            return None
        return stamp

    @staticmethod
    def _box_source_timestamp(value: str, now: float, shift: float) -> float | None:
        return _unique_stamp(_protocol_stamps(value), now + shift - _MAX_AGE, now + shift + 2)

    def _observe_clock(self, device_id: str, proof: _DeviceProof, wire: _Wire, now: float) -> None:
        fields = wire.fields
        if (now <= self._started or fields.get("TblName") != "tbl_actual" or fields.get("ID_SubD", "0") != "0"
                or (proof.candidate is not None and not wire.matches(proof.candidate))):
            return
        source = _unique_stamp(_protocol_stamps(fields.get("DT", "")), now - 2, now + _MAX_CLOCK_ADVANCE)
        sequence = _positive_counter(fields.get("ID_Set", ""))
        if source is None or sequence is None:
            return
        readings = proof.clock_readings
        sample = _ClockReading(now, source, sequence)
        if readings and now - readings[-1].received > _MAX_AGE:
            readings.clear()
            proof.clock_step_back = False
        if readings:
            previous = readings[-1]
            if now <= previous.received or sequence <= previous.counter:
                return
            if source <= previous.source:
                # Recognize a clock step back only on a newer native sequence.
                if sample.advance >= proof.clock_offset - 2:
                    return
                readings.clear()
                proof.clock_step_back = True
        if sample.advance >= proof.clock_offset - 2:
            proof.clock_step_back = False
        readings.append(sample)
        if len(readings) < 3:
            return
        duration = now - readings[0].received
        advances = [reading.advance for reading in readings]
        low, high = min(advances), max(advances)
        earliest, latest = _CLOCK_SAMPLE_SPAN
        if not earliest <= duration <= latest or high - low > _CLOCK_SAMPLE_SPREAD:
            return
        offset = high if low > 2 else 0.0 if high <= 2 else proof.clock_offset
        if (abs(offset - proof.clock_offset) <= 2
                or (offset < proof.clock_offset and not proof.clock_step_back)):
            return
        self._change_clock(device_id, proof, offset, now, wall_bias=proof.cloud_wall_bias if offset else None)

    def _change_clock(
        self, device_id: str, proof: _DeviceProof, offset: float, now: float, *, wall_bias: float | None = None,
    ) -> None:
        if offset < proof.clock_offset:
            # Old and current raw dates overlap until the BOX clock passes its old high-water mark.
            proof.readback_source_floor = max(proof.readback_source_floor, proof.latest_box + proof.clock_offset)
        proof.clock_offset = offset
        proof.clock_epoch += 1
        proof.clock_step_back = False
        proof.cloud_clock_readings.clear()
        proof.cloud_wall_bias = wall_bias
        self._reset_native_proof(device_id, proof, "box_clock_changed")
        # Earlier observations were never qualified in this revised clock domain.
        proof.cutoff = max(proof.cutoff, now)

    def _check_wall_clock(self, device_id: str, proof: _DeviceProof, now: float) -> None:
        """Invalidate clock samples from before a proxy wall clock correction."""
        readings = proof.cloud_clock_readings
        bias = proof.cloud_wall_bias if proof.cloud_wall_bias is not None else (
            readings[-1].wall_bias if readings else None
        )
        if bias is None or abs(now - self._monotonic() - bias) <= _CLOCK_SAMPLE_SPREAD:
            return
        readings.clear()
        if proof.cloud_wall_bias is not None:
            proof.clock_readings.clear()
            self._change_clock(device_id, proof, 0.0, now)

    def _observe_cloud_assisted_clock(self, device_id: str, proof: _DeviceProof, wire: _Wire, now: float) -> None:
        readings = proof.cloud_clock_readings
        if (proof.clock_offset or proof.ready or len(readings) < 2 or wire.fields.get("ID_SubD", "0") != "0"
                or _positive_counter(wire.fields.get("ID_Set", "")) is None):
            return
        first, second = readings
        offset = ceil(max(first.advance, second.advance))
        if not second.received <= now <= first.received + _MAX_AGE:
            return
        source = _unique_stamp(
            _protocol_stamps(wire.fields.get("DT", "")),
            now + max(3, offset - _CLOUD_CLOCK_DIFFERENCE), now + min(offset + 2, _MAX_CLOCK_ADVANCE),
        )
        if source is None or wire.state is None or wire.ver is None or (wire.state, wire.ver, source) in proof.seen:
            return
        stamp = source - offset
        if stamp <= self._started or stamp < proof.cutoff:
            return
        # The Cloud clocks provide a time reference only. Match their two
        # native polls to this independently dated BOX frame before using it.
        profile = proof.candidate
        if profile is None:
            profile, _ = _identify([first.poll, second.poll, _Sample(wire, now, None)])
        if profile is None or not all(item.matches(profile) for item in (first.poll, second.poll, wire)):
            return
        proof.clock_readings.clear()
        self._change_clock(device_id, proof, offset, now, wall_bias=second.wall_bias)

    @staticmethod
    def _fresh(sample: _Sample, now: float) -> bool:
        return -2 <= now - sample.at <= _MAX_AGE

    def _native_reason(self, proof: _DeviceProof, now: float) -> str:
        profile = proof.candidate
        if profile is None:
            return proof.block
        fresh = {sample.wire.state for sample in proof.samples if self._fresh(sample, now)}
        if len(fresh) < 6 or len(fresh - proof.basis) < 3:
            return "box_evidence_stale" if proof.ever_ready else "box_profile_unconfirmed"
        return "ready"

    @staticmethod
    def _firmware_value(sample: _Sample | None) -> str | None:
        if sample is None:
            return None
        return sample.wire.fields.get("Fw") or sample.wire.fields.get("SW")

    def status(self, device_id: str) -> ProfileStatus:
        """Return a fresh enrollment for this device and its qualified layout."""
        proof = self._proofs.get(device_id)
        if proof is None:
            return ProfileStatus(None, "waiting_for_box", 0)
        now = self._clock()
        self._check_wall_clock(device_id, proof, now)
        reason = self._native_reason(proof, now)
        enrollment = self._enrollments.get(device_id)
        if proof.ambiguous_layout:
            reason = "ambiguous_setting_layout"
        elif reason == "ready" and (enrollment is None or enrollment.profile != proof.candidate):
            reason = "cloud_evidence_pending"
        if proof.ready and reason != "ready":
            proof.epoch += 1
        proof.ready = reason == "ready"
        proof.ever_ready |= proof.ready
        if proof.ready and enrollment is not None:
            return ProfileStatus(enrollment.profile, reason, proof.epoch, enrollment.include_tto)
        return ProfileStatus(None, reason, proof.epoch)

    def archive_basis(self, device_id: str) -> tuple[VerProfile, int] | None:
        """Return the current independently qualified native model for an archive scan."""
        proof = self._proofs.get(device_id)
        if proof is not None:
            self._check_wall_clock(device_id, proof, self._clock())
        if (proof is None or proof.candidate is None or proof.ambiguous_layout
                or proof.live_cloud_seen or self._native_reason(proof, self._clock()) != "ready"):
            return None
        existing = self._enrollments.get(device_id)
        if existing is None and not self._archive_registry_cold:
            return None
        if existing is not None and existing.profile != proof.candidate:
            return None
        return proof.candidate, proof.epoch

    def can_adopt_archive_profile(
        self, device_id: str, profile: VerProfile, epoch: int, include_tto: bool, *,
        firmware: str | None = None,
    ) -> bool:
        """Check historical proof against current evidence without mutating state."""
        if self.archive_basis(device_id) != (profile, epoch):
            return False
        existing = self._enrollments.get(device_id)
        live_firmware = self._firmware_value(self._proofs[device_id].firmware) or (
            existing.firmware if existing is not None else None
        )
        if firmware is not None and live_firmware is not None and live_firmware != firmware:
            return False
        if existing is None:
            return len(self._enrollments) < _MAX_DEVICES
        return existing.include_tto is include_tto

    def adopt_archive_profile(
        self, device_id: str, profile: VerProfile, epoch: int, include_tto: bool, *,
        firmware: str | None = None,
    ) -> bool:
        """Restore an accepted historical layout after its counters are retained."""
        if not self.can_adopt_archive_profile(device_id, profile, epoch, include_tto, firmware=firmware):
            return False
        if device_id not in self._enrollments:
            known_firmware = self._firmware_value(self._proofs[device_id].firmware) or firmware
            self._enrollments[device_id] = _Enrollment(profile, known_firmware, include_tto)
            if not self._save_enrollments(invalidate_on_failure=False):
                del self._enrollments[device_id]
                return False
        return True

    def note_local_send(self, session_id: str | None) -> None:
        """Prevent a locally generated Setting from proving a Cloud layout."""
        if session_id is not None and (flow := self._flows.get(session_id)) is not None:
            flow.occupied = True
            flow.pending = None
            flow.clock_poll = None

    def note_forwarded_clock_poll(self, raw: bytes | None, session_id: str | None) -> None:
        """Arm one clock response for an actual, unambiguous upstream native poll."""
        flow = self._flows.get(session_id) if session_id is not None else None
        if flow is None:
            return
        flow.clock_poll = None
        sample = flow.last_native
        if (raw is None or flow.occupied or flow.quarantined or flow.clock_poll_used or sample is None
                or sample.wire.fields.get("Result") != "IsNewSet"):
            return
        now = self._clock()
        if (now <= self._started or not 0 <= now - sample.at <= _CLOUD_CLOCK_ROUNDTRIP
                or _parse(raw, allow_repeated_identity=False) != sample.wire):
            return
        flow.clock_poll_used = True
        flow.clock_poll = (_Sample(sample.wire, now, session_id), now - self._monotonic())

    def close_session(self, session_id: str | None) -> None:
        if session_id is not None:
            self._flows.pop(session_id, None)

    def _bind(self, session_id: str | None, device_id: str) -> _Flow | None:
        if session_id is None:
            return None
        flow = self._flows.get(session_id)
        if flow is None:
            flow = self._flows[session_id] = _Flow(device_id)
        elif flow.device_id != device_id:
            flow.quarantined = True
            flow.pending = None
            flow.clock_poll = None
        return flow

    def _learn(self, device_id: str, proof: _DeviceProof, now: float) -> None:
        if proof.candidate is not None:
            return
        distinct: dict[int, _Sample] = {}
        for sample in proof.samples:
            if sample.wire.state is not None and self._fresh(sample, now):
                distinct.setdefault(sample.wire.state, sample)
        if len(distinct) < 3:
            return
        basis_states = list(distinct)[-3:]
        basis = [distinct[state] for state in basis_states]
        model, inconsistent = _identify(basis)
        if inconsistent:
            # Keep the last observation as the beginning of a possible new epoch.
            latest = basis[-1]
            self._retire(device_id, proof, "unsupported_profile")
            if latest.at >= proof.cutoff:
                proof.samples.append(latest)
            return
        if model is not None:
            proof.candidate = model
            proof.basis = frozenset(basis_states)
            enrollment = self._enrollments.get(device_id)
            if enrollment is not None and enrollment.profile != model:
                self._discard_enrollment(device_id)

    def _observe_firmware(
        self, device_id: str, proof: _DeviceProof, sample: _Sample, flow: _Flow | None,
    ) -> None:
        if proof.firmware is not None and sample.at < proof.firmware.at:
            return
        value = self._firmware_value(sample)
        if not value or not _FIRMWARE_ID.fullmatch(value):
            return
        enrollment = self._enrollments.get(device_id)
        prior = self._firmware_value(proof.firmware) or (enrollment.firmware if enrollment else None)
        if prior and prior != value:
            self._retire(device_id, proof, "firmware_changed")
        if sample.wire.fields.get("Result") == "IsNewFW":
            if flow is not None:
                flow.firmware_poll = sample
                self._bind_firmware_poll(device_id, proof, flow)
        else:
            self._remember_firmware(device_id, proof, sample)

    def _remember_firmware(self, device_id: str, proof: _DeviceProof, sample: _Sample) -> None:
        if proof.firmware is not None and sample.at < proof.firmware.at:
            return
        proof.firmware = sample
        enrollment = self._enrollments.get(device_id)
        if enrollment is not None and enrollment.firmware is None:
            self._enrollments[device_id] = replace(enrollment, firmware=self._firmware_value(sample))
            self._save_enrollments()

    def _bind_firmware_poll(self, device_id: str, proof: _DeviceProof, flow: _Flow) -> None:
        poll, dated = flow.firmware_poll, flow.last_dated
        if poll is not None and dated is not None and abs(poll.at - dated.at) <= _SESSION_WINDOW:
            bound = _Sample(poll.wire, min(poll.at, dated.at), poll.session)
            self._remember_firmware(device_id, proof, bound)

    def observe_box(self, raw: bytes, session_id: str | None) -> int | None:
        """Consume native BOX evidence and expose a qualified counter if present."""
        wire = _parse(raw, allow_repeated_identity=True)
        if wire is None:
            return None
        now = self._clock()
        fields = wire.fields
        result = fields.get("Result")
        if result in {"ACK", "NACK"}:
            self._observe_reply(wire, session_id, now)
            return None
        device_id = fields.get("ID_Device", "")
        if not _DEVICE_ID.fullmatch(device_id) or wire.state is None or wire.ver is None:
            return None
        flow = self._bind(session_id, device_id)
        if flow is not None and flow.quarantined:
            return None
        if device_id not in self._proofs:
            if len(self._proofs) >= _MAX_DEVICES:
                return None
            self._proofs[device_id] = _DeviceProof()
        proof = self._proofs[device_id]
        self.status(device_id)
        if result in BOX_POLL_RESULTS:
            if proof.candidate is not None and not wire.matches(proof.candidate):
                self._retire(device_id, proof, "profile_changed")
            sample = _Sample(wire, now, session_id)
            if flow is not None:
                flow.last_native = sample
            if result == "IsNewFW":
                self._observe_firmware(device_id, proof, sample, flow)
            return None

        if not fields.get("TblName") or fields.get("Reason") != "Table":
            return None
        if flow is not None:
            self._observe_clock(device_id, proof, wire, now)
            self._observe_cloud_assisted_clock(device_id, proof, wire, now)
        source_stamp = self._box_source_timestamp(fields.get("DT", ""), now, proof.clock_offset)
        if source_stamp is None:
            return None
        stamp = source_stamp - proof.clock_offset
        if stamp <= self._started or stamp < proof.cutoff:
            return None
        signature = (wire.state, wire.ver, source_stamp)
        if len(proof.seen) >= 4096:
            proof.seen = {key: at for key, at in proof.seen.items() if now - at <= _MAX_AGE}
        if signature in proof.seen or len(proof.seen) >= 4096:
            return None
        proof.seen[signature] = now
        if proof.candidate is not None and not wire.matches(proof.candidate):
            self._retire(device_id, proof, "profile_changed")
            if stamp < proof.cutoff:
                return None
        sample = _Sample(wire, min(now, stamp), session_id)
        if fields.get("TblName") == "tbl_box_prms" and fields.get("SW"):
            self._observe_firmware(device_id, proof, sample, flow)
        if stamp < proof.cutoff:
            return None
        proof.latest_box = max(proof.latest_box, stamp)
        proof.samples.append(sample)
        proof.block = "box_profile_unconfirmed"
        if flow is not None:
            flow.last_native = _Sample(wire, now, session_id)
            flow.last_dated = sample
            if not fields.get("SW"):
                self._bind_firmware_poll(device_id, proof, flow)
        self._learn(device_id, proof, now)
        if self.status(device_id).profile is not None:
            return _positive_counter(fields.get("ID_Set", ""))
        return None

    def observe_cloud(self, raw: bytes, session_id: str | None) -> None:
        """Track an exact natural Setting or clock response on its actual BOX flow."""
        flow = self._flows.get(session_id) if session_id is not None else None
        if flow is None or flow.quarantined:
            return
        clock_poll, flow.clock_poll = flow.clock_poll, None
        has_reason_tag = b"<Reason" in raw
        if not has_reason_tag and b"<Result" not in raw:
            return
        wire = _parse(raw, allow_repeated_identity=False)
        if wire is None:
            if has_reason_tag or b"<Result>NACK</Result>" in raw:
                flow.occupied = True
                flow.pending = None
            return
        fields = wire.fields
        if wire.order == _CLOUD_CLOCK_ORDER and fields.get("Result") == "END" and clock_poll is not None:
            self._observe_cloud_clock(flow, wire, clock_poll)
            return
        if fields.get("Result") == "NACK":
            flow.occupied = True
            flow.pending = None
            return
        if fields.get("Reason") != "Setting":
            return
        occupied = flow.occupied
        flow.occupied = True
        flow.pending = None
        device_id = flow.device_id
        proof = self._proofs.get(device_id)
        if proof is None:
            return
        now = self._clock()
        self._check_wall_clock(device_id, proof, now)
        if (fields.get("ID_Device") != device_id or flow.last_native is None
                or now - flow.last_native.at > _SESSION_WINDOW):
            return
        # Live traffic owns enrollment for the rest of this boot, even without an ACK.
        proof.live_cloud_seen = True
        stamp = recent_protocol_timestamp(fields.get("TSec", ""), now, utc=True)
        include_tto = _setting_layout(wire)
        if stamp is not None and (stamp < proof.cutoff or now - stamp > _SESSION_WINDOW):
            return
        future_cloud_stamp = None
        if stamp is None:
            future_cloud_stamp = self._compatible_future_cloud_stamp(proof, fields, now)
            if future_cloud_stamp is None:
                return
            stamp = now
        existing = self._enrollments.get(device_id)
        if existing is not None and include_tto != existing.include_tto and future_cloud_stamp is None:
            self._withdraw_layout(device_id, proof)
            return
        profile = proof.candidate
        if profile is None:
            return
        if not wire.matches(profile):
            if future_cloud_stamp is None:
                self._retire(device_id, proof, "cloud_profile_changed")
            return
        if (occupied or include_tto is None
                or self._native_reason(proof, now) != "ready" or not flow.last_native.matches(profile)
                or wire.state in {sample.wire.state for sample in proof.samples}):
            return
        if future_cloud_stamp is not None:
            proof.future_cloud_highwater = future_cloud_stamp
        self.status(device_id)
        flow.pending = _Pending(
            _Sample(wire, stamp, session_id), now, proof.epoch, include_tto, future_cloud_stamp,
        )

    def _observe_cloud_clock(self, flow: _Flow, wire: _Wire, clock_poll: tuple[_Sample, float]) -> None:
        proof = self._proofs.get(flow.device_id)
        if proof is None or flow.occupied:
            return
        now, monotonic = self._clock(), self._monotonic()
        self._check_wall_clock(flow.device_id, proof, now)
        poll, forwarded_bias = clock_poll
        bias = now - monotonic
        if (proof.clock_offset or proof.ready or abs(bias - forwarded_bias) > _CLOCK_SAMPLE_SPREAD
                or not 0 <= now - poll.at <= _CLOUD_CLOCK_ROUNDTRIP):
            return
        source = _unique_stamp(
            _protocol_stamps(wire.fields["Time"], naive_only=True)
            & _protocol_stamps(wire.fields["UTCTime"], utc=True, naive_only=True),
            now + 3, now + _MAX_CLOCK_ADVANCE,
        )
        state, ver = poll.wire.state, poll.wire.ver
        if source is None or state is None or ver is None:
            return
        # Keep duplicates out even if they are resent on different sockets.
        seen = proof.cloud_clock_polls
        signature = state, ver
        if monotonic - seen.get(signature, float("-inf")) <= _MAX_AGE:
            return
        if len(seen) >= 256:
            seen = proof.cloud_clock_polls = {item: at for item, at in seen.items() if monotonic - at <= _MAX_AGE}
        if len(seen) >= 256:
            return
        readings = proof.cloud_clock_readings
        sample = _CloudClockReading(poll, now, source, bias)
        if readings:
            previous = readings[-1]
            if (now <= previous.received or source <= previous.source or poll.session == previous.poll.session):
                return
            if (now - previous.received > _MAX_AGE
                    or abs(sample.advance - previous.advance) > _CLOCK_SAMPLE_SPREAD):
                readings.clear()
            elif now - previous.received < _CLOCK_SAMPLE_SPAN[0]:
                return
        readings.append(sample)
        seen[signature] = monotonic

    @staticmethod
    def _compatible_future_cloud_stamp(proof: _DeviceProof, fields: dict[str, str], now: float) -> float | None:
        """Retain a future UTC candidate only for same-flow corroboration by the BOX."""
        if not proof.clock_offset or not -2 <= now - proof.latest_box <= _MAX_AGE or now < proof.cutoff:
            return None
        stamp = _unique_stamp(_protocol_stamps(fields.get("TSec", ""), utc=True), now + 3,
                              now + _MAX_CLOCK_ADVANCE)
        if (stamp is None or stamp <= proof.future_cloud_highwater
                or abs((stamp - now) - proof.clock_offset) > _CLOUD_CLOCK_DIFFERENCE):
            return None
        return stamp

    def _observe_reply(self, wire: _Wire, session_id: str | None, now: float) -> None:
        flow = self._flows.get(session_id) if session_id is not None else None
        if flow is None or flow.quarantined or flow.pending is None:
            return
        fields = _receipt_fields(wire)
        if fields is None or fields.get("Result") == "NACK" or fields.get("ID_Device", flow.device_id) != flow.device_id:
            flow.pending = None
            return
        if fields.get("Reason") != "Setting":
            return
        pending, flow.pending = flow.pending, None
        proof = self._proofs[flow.device_id]
        profile = proof.candidate
        stamp = self.box_timestamp(flow.device_id, fields.get("Rdt", ""))
        command = pending.sample.wire.fields
        if (profile is None or proof.ambiguous_layout or pending.epoch != proof.epoch
                or stamp is None or stamp < pending.sample.at - 2
                or not 0 <= now - pending.received <= _SESSION_WINDOW
                or wire.ver is not None and not wire.matches(profile)
                or self._native_reason(proof, now) != "ready"
                or any(fields.get(tag, command[expected]) != command[expected]
                       for tag, expected in _SETTING_REPLY_ECHOES.items())):
            return
        if pending.future_cloud_stamp is not None:
            raw_box_stamp = stamp + proof.clock_offset
            if not -2 <= raw_box_stamp - pending.future_cloud_stamp <= _SESSION_WINDOW:
                return
        existing = self._enrollments.get(flow.device_id)
        if existing is not None:
            if existing.profile != profile or existing.include_tto != pending.include_tto:
                self._withdraw_layout(flow.device_id, proof)
        elif len(self._enrollments) < _MAX_DEVICES:
            self._enrollments[flow.device_id] = _Enrollment(
                profile, self._firmware_value(proof.firmware), pending.include_tto,
            )
            self._save_enrollments()
