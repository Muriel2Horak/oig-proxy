"""Passive per-device qualification of the OIG Setting wire profile."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
import xml.etree.ElementTree as ET  # nosec B405
from zoneinfo import ZoneInfo

from .crc import _crc16_table, crc16_modbus

_MAX_AGE = 900
_SESSION_WINDOW = 60
_MAX_DEVICES = 128
_CRC_PERIOD = 32_767
_LOCAL_ZONE = ZoneInfo("Europe/Prague")
_POLLS = {"IsNewFW", "IsNewSet", "IsNewWeather"}
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
    if len(raw) > 65_536 or not raw.startswith(b"<Frame>") or b"<!DOCTYPE" in raw:
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
    return (wire is not None and wire.fields.get("Result") == "IsNewSet"
            and wire.fields.get("ID_Device") == device_id and wire.matches(profile))


def parse_setting_reply(raw: bytes, device_id: str, profile: VerProfile) -> dict[str, str] | None:
    """Validate a flat native response and its exact checksums before correlation."""
    wire = _parse(raw, allow_repeated_identity=False)
    if wire is None or wire.fields.get("ID_Device", device_id) != device_id:
        return None
    if wire.fields["Result"] == "ACK" and wire.ver is not None and not wire.matches(profile):
        return None
    return _receipt_fields(wire)


def _timestamp(value: str, now: float, utc: bool = False) -> float | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
        except ValueError:
            return None
    try:
        if parsed.tzinfo is not None:
            stamps = {parsed.timestamp()}
        elif utc:
            stamps = {parsed.replace(tzinfo=timezone.utc).timestamp()}
        else:
            stamps = set()
            for fold in (0, 1):
                stamp = parsed.replace(tzinfo=_LOCAL_ZONE, fold=fold).timestamp()
                if datetime.fromtimestamp(stamp, _LOCAL_ZONE).replace(tzinfo=None) == parsed:
                    stamps.add(stamp)
    except (OSError, OverflowError, ValueError):
        return None
    valid = [stamp for stamp in stamps if -2 <= now - stamp <= _MAX_AGE]
    return valid[0] if len(valid) == 1 else None


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


@dataclass
class _Flow:
    device_id: str
    occupied: bool = False
    quarantined: bool = False
    last_native: _Sample | None = None
    last_dated: _Sample | None = None
    firmware_poll: _Sample | None = None
    pending: _Pending | None = None


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
    ) -> None:
        self._clock = clock
        self._started = clock()
        self._state_path = Path(state_path) if state_path is not None else None
        self._proofs: dict[str, _DeviceProof] = {}
        self._flows: dict[str, _Flow] = {}
        self._enrollments = self._load_enrollments()

    def _load_enrollments(self) -> dict[str, _Enrollment]:
        path = self._state_path
        if path is None:
            return {}
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
            return result
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError):
            _LOGGER.warning("Ignoring invalid retained wire profile state")
            return {}

    def _save_enrollments(self) -> None:
        path = self._state_path
        if path is None:
            return
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
            # An older on-disk entry could otherwise survive a profile revocation.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                _LOGGER.warning("Could not invalidate previous wire profile state")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    _LOGGER.debug("Could not remove temporary wire profile state")

    def _discard_enrollment(self, device_id: str) -> None:
        if self._enrollments.pop(device_id, None) is not None:
            self._save_enrollments()

    def _withdraw_layout(self, device_id: str, proof: _DeviceProof) -> None:
        proof.ambiguous_layout = True
        self._discard_enrollment(device_id)
        self.status(device_id)

    def _retire(self, device_id: str, proof: _DeviceProof, reason: str) -> None:
        self._discard_enrollment(device_id)
        proof.epoch += 1
        proof.candidate = None
        proof.basis = frozenset()
        proof.firmware = None
        proof.samples.clear()
        proof.cutoff = max(proof.cutoff, proof.latest_box)
        proof.ready = proof.ever_ready = proof.ambiguous_layout = False
        proof.block = reason
        for flow in self._flows.values():
            if flow.device_id == device_id:
                flow.pending = flow.firmware_poll = flow.last_dated = None
                flow.last_native = None

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
        reason = self._native_reason(proof, self._clock())
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

    def note_local_send(self, session_id: str | None) -> None:
        """Prevent a locally generated Setting from proving a Cloud layout."""
        if session_id is not None and (flow := self._flows.get(session_id)) is not None:
            flow.occupied = True
            flow.pending = None

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
        if result in _POLLS:
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
        stamp = _timestamp(fields.get("DT", ""), now)
        if stamp is None or stamp <= self._started or stamp < proof.cutoff:
            return None
        signature = (wire.state, wire.ver, stamp)
        if len(proof.seen) >= 4096:
            proof.seen = {key: at for key, at in proof.seen.items() if now - at <= _MAX_AGE}
        if signature in proof.seen or len(proof.seen) >= 4096:
            return None
        proof.seen[signature] = stamp
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
        counter = fields.get("ID_Set", "")
        if self.status(device_id).profile is not None and counter.isascii() and counter.isdecimal() and len(counter) <= 19:
            return int(counter) or None
        return None

    def observe_cloud(self, raw: bytes, session_id: str | None) -> None:
        """Track an exact natural Setting on its actual BOX flow."""
        flow = self._flows.get(session_id) if session_id is not None else None
        if flow is None or flow.quarantined:
            return
        if b"<Reason>Setting</Reason>" not in raw and b"<Result>NACK</Result>" not in raw:
            return
        wire = _parse(raw, allow_repeated_identity=False)
        if wire is None or wire.fields.get("Result") == "NACK":
            flow.occupied = True
            flow.pending = None
            return
        occupied = flow.occupied
        flow.occupied = True
        flow.pending = None
        device_id = flow.device_id
        proof = self._proofs.get(device_id)
        if proof is None:
            return
        now = self._clock()
        fields = wire.fields
        stamp = _timestamp(fields.get("TSec", ""), now, utc=True)
        include_tto = _setting_layout(wire)
        if (fields.get("Reason") != "Setting" or fields.get("ID_Device") != device_id
                or flow.last_native is None or now - flow.last_native.at > _SESSION_WINDOW
                or stamp is not None and (stamp < proof.cutoff or now - stamp > _SESSION_WINDOW)):
            return
        existing = self._enrollments.get(device_id)
        if existing is not None and include_tto != existing.include_tto:
            self._withdraw_layout(device_id, proof)
            return
        profile = proof.candidate
        if profile is None:
            return
        if not wire.matches(profile):
            self._retire(device_id, proof, "cloud_profile_changed")
            return
        if (occupied or include_tto is None or stamp is None
                or self._native_reason(proof, now) != "ready" or not flow.last_native.matches(profile)
                or wire.state in {sample.wire.state for sample in proof.samples}):
            return
        self.status(device_id)
        flow.pending = _Pending(
            _Sample(wire, stamp, session_id), now, proof.epoch, include_tto,
        )

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
        stamp = _timestamp(fields.get("Rdt", ""), now)
        command = pending.sample.wire.fields
        if (profile is None or proof.ambiguous_layout or pending.epoch != proof.epoch
                or stamp is None or stamp < pending.sample.at - 2
                or not 0 <= now - pending.received <= _SESSION_WINDOW
                or wire.ver is not None and not wire.matches(profile)
                or self._native_reason(proof, now) != "ready"
                or any(fields.get(tag, command[expected]) != command[expected]
                       for tag, expected in _SETTING_REPLY_ECHOES.items())):
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
