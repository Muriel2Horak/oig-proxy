"""Passive qualification tests with independent exact-wire CRC fixtures."""
from __future__ import annotations

from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import pytest

from protocol import ver_profile
from protocol.frames import build_setting_frame


BOX_A = "TEST-BOX-A"
BOX_B = "TEST-BOX-B"
FW_A = "v.3.25.43.1219"
FW_B = "v.4.15.26.0518"
PRAGUE = ZoneInfo("Europe/Prague")
LEGACY_ACTUAL_TAGS = (
    "ENBL VIZ Temp Humid ACO_P FV_P1 FV_P2 ACI_WR ACI_WS ACI_WT BAT_P FV_PROC "
    "BAT_C MSC_SELF STAT FV_AD FVE_PROD INFLOW LOAD MAXFG STATX ACINB_WR ACINB_WS "
    "ACINB_WT ACO_PR ACO_PS ACO_PT"
).split()


def crc(data: bytes) -> int:
    """Independent bitwise reference for wire CRC and the ver fixture."""
    result = 0xFFFF
    for byte in data:
        result ^= byte
        for _ in range(8):
            result = (result >> 1) ^ (0xA001 if result & 1 else 0)
    return result


def native(prefix: str, model: tuple[int, int]) -> bytes:
    """Build native fixture bytes without the product serializer."""
    body = prefix.encode()
    count, key = model
    value = crc(body + bytes(count)) ^ key
    body += f"<ver>{value:05d}</ver>".encode()
    return b"<Frame>" + body + f"<CRC>{crc(body):05d}</CRC></Frame>\r\n".encode()


class Clock:
    """Control capture time independently of the production wall clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 12, 10, tzinfo=timezone.utc).timestamp()
        self.count = 0

    def __call__(self) -> float:
        return self.now

    def step(self, seconds: int = 1) -> None:
        """Advance clock and fixture sequence."""
        self.now += seconds
        self.count += 1

    def stamp(self, utc: bool = False, offset: int = 0) -> str:
        """Render the native protocol's local or UTC clock."""
        return datetime.fromtimestamp(self.now + offset, timezone.utc if utc else PRAGUE).strftime("%Y-%m-%d %H:%M:%S")


def learner(clock, **kwargs):
    """Create a learner on the fixture clock."""
    return ver_profile.VerProfiles(clock=clock, **kwargs)


def observation(clock, device, model, *, firmware=None, same=False, offset=0):  # pylint: disable=too-many-arguments
    """Create a dated BOX observation with a different CRC state."""
    if not same:
        clock.step()
    table = "tbl_box_prms" if firmware else "tbl_actual"
    payload = (
        f"<TblName>{table}</TblName><ID_Set>{1234000 + clock.count}</ID_Set>"
        f"<DT>{clock.stamp(offset=offset)}</DT><Reason>Table</Reason><ID_Device>{device}</ID_Device>"
        f"<Sample>{clock.count}</Sample>"
    )
    if firmware:
        payload += f"<SW>{firmware}</SW>"
    return native(payload, model)


def box_proof(profiles, clock, device, model, firmware, *, samples=6, prefix="box"):  # pylint: disable=too-many-arguments
    """Supply ordinary native frames on prior BOX sockets."""
    for index in range(samples):
        session = f"{prefix}-{device}-{index}"
        profiles.observe_box(
            observation(clock, device, model, firmware=firmware if index == 0 else None),
            session,
        )
        profiles.close_session(session)


def cloud_setting(clock, device, model, *, include_tto, server_id, tsec_offset=0):  # pylint: disable=too-many-arguments
    """Build the native Cloud wire independently of the product serializer."""
    fields = (
        f"<ID>{14800000+clock.count}</ID><ID_Device>{device}</ID_Device><ID_Set>{int(clock.now)}</ID_Set>"
        f"<ID_SubD>0</ID_SubD><DT>{clock.stamp()}</DT><NewValue>1</NewValue><Confirm>New</Confirm>"
        "<TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem>"
    )
    if include_tto:
        fields += "<TTO_P>0,000</TTO_P><TTO_E>0,000</TTO_E>"
    fields += (
        f"<ID_Server>{server_id}</ID_Server><mytimediff>0</mytimediff>"
        f"<Reason>Setting</Reason><TSec>{clock.stamp(True, tsec_offset)}</TSec>"
    )
    return native(fields, model)


def legacy_actual_receipt(clock, device, model, *, second_reason="Table"):
    """Use the exact ordered combined ACK and actual table shape from captures."""
    prefix = (
        f"<Result>ACK</Result><Rdt>{clock.stamp()}</Rdt><Reason>Setting</Reason>"
        f"<TblName>tbl_actual</TblName><ID_Set>865000001</ID_Set><DT>{clock.stamp()}</DT>"
        f"<Reason>{second_reason}</Reason><ID_Device>{device}</ID_Device>"
    )
    prefix += "".join(f"<{tag}>0</{tag}>" for tag in LEGACY_ACTUAL_TAGS)
    return native(prefix, model)


def cloud_transaction(  # pylint: disable=too-many-arguments
    profiles, clock, device, model, *, include_tto=True, ack=True, local=False,
    session=None, reply_session=None, server_id=9, corrupt_reply=False, close=True, bad_echo=None,
):
    """Use a natural observed Cloud Setting and a BOX reply on the actual flow."""
    clock.step()
    session = session or f"cloud-{device}-{clock.count}"
    profiles.observe_box(native(f"<Result>IsNewSet</Result><ID_Device>{device}</ID_Device><Lat>47</Lat>", model), session)
    if local:
        profiles.note_local_send(session)
    profiles.observe_cloud(cloud_setting(clock, device, model, include_tto=include_tto, server_id=server_id), session)
    if ack:
        reply_fields = f"<Result>ACK</Result><Rdt>{clock.stamp()}</Rdt><Reason>Setting</Reason>"
        if bad_echo:
            reply_fields += f"<{bad_echo}>1</{bad_echo}>"
        reply = native(reply_fields, model)
        if corrupt_reply:
            reply = reply.replace(b"<Rdt>", b"<Rdx>").replace(b"</Rdt>", b"</Rdx>")
        profiles.observe_box(reply, reply_session or session)
    if close:
        profiles.close_session(session)


@pytest.mark.parametrize(("device", "firmware", "model", "with_tto"), [
    (BOX_A, FW_A, (39, 0xBB59), True),
    (BOX_B, FW_B, (42, 0x003D), False),
])
def test_fresh_box_and_acknowledged_natural_cloud_evidence_qualify(device, firmware, model, with_tto):
    """Prove independently fitted devices and layouts without bundled seeds."""
    clock = Clock()
    profiles = learner(clock)
    box_proof(profiles, clock, device, model, firmware)
    assert profiles.status(device).profile is None
    cloud_transaction(profiles, clock, device, model, include_tto=with_tto)
    status = profiles.status(device)
    assert status.profile == ver_profile.VerProfile(*model)
    assert status.include_tto is with_tto
    assert profiles.status(BOX_B if device == BOX_A else BOX_A).profile is None


def shifted_actuals(profiles, clock, device, model, offset, *, samples=9):  # pylint: disable=too-many-arguments
    """Feed dated actual frames with increasing native sequence and receipt times."""
    for _ in range(samples):
        clock.step(10)
        session = f"shifted-{device}-{clock.count}"
        profiles.observe_box(observation(clock, device, model, same=True, offset=offset), session)
        profiles.close_session(session)


def test_consistent_ahead_box_actual_tables_qualify_without_affecting_other_boxes():
    """A repeatable offset permits normal BOX tables and a same-flow native Rdt."""
    clock = Clock()
    model = (39, 0xBB59)
    profiles = learner(clock)
    shifted_actuals(profiles, clock, BOX_A, model, 256, samples=2)
    assert profiles.status(BOX_A).profile is None
    shifted_actuals(profiles, clock, BOX_A, model, 256, samples=7)
    session = "strict-utc-cloud"
    cloud_transaction(profiles, clock, BOX_A, model, session=session, ack=False, close=False)
    ack = f"<Result>ACK</Result><Rdt>{clock.stamp(offset=256)}</Rdt><Reason>Setting</Reason>"
    profiles.observe_box(native(ack, model), session)
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)
    assert profiles.box_timestamp(BOX_A, clock.stamp(offset=256)) == clock.now
    assert profiles.box_timestamp(BOX_B, clock.stamp(offset=256)) is None
    assert profiles.box_clock_offset(BOX_A) == 256
    assert profiles.box_clock_offset(BOX_B) == 0
    assert profiles.box_clock_epoch(BOX_A) == 1


def test_clock_learning_ignores_repeated_corrupt_or_out_of_bound_actuals():
    """A replay, bad outer CRC, and unsupported offsets do not establish a clock."""
    clock = Clock()
    model = (39, 0xBB59)
    profiles = learner(clock)
    repeat = observation(clock, BOX_A, model, offset=256)
    for index in range(6):
        clock.step(10)
        profiles.observe_box(repeat, f"replay-{index}")
        corrupt = observation(clock, BOX_A, model, offset=256).replace(b"<Sample>", b"<SamplX>")
        profiles.observe_box(corrupt, f"corrupt-{index}")
    shifted_actuals(profiles, clock, BOX_A, model, 310)
    assert profiles.box_timestamp(BOX_A, clock.stamp(offset=256)) is None
    assert profiles.box_clock_epoch(BOX_A) == 0
    shifted_actuals(profiles, clock, BOX_A, model, 256)
    assert profiles.box_timestamp(BOX_A, clock.stamp(offset=256)) == clock.now


def test_clock_revision_requalifies_native_proof_and_rejects_older_tables(tmp_path):
    """A common-ver clock step invalidates prior timing without discarding the layout."""
    clock = Clock()
    model = (39, 0xBB59)
    profiles = learner(clock, state_path=tmp_path / "profile.json")
    shifted_actuals(profiles, clock, BOX_A, model, 256)
    session = "establish"
    cloud_transaction(profiles, clock, BOX_A, model, session=session, ack=False, close=False)
    native_ack = f"<Result>ACK</Result><Rdt>{clock.stamp(offset=256)}</Rdt><Reason>Setting</Reason>"
    profiles.observe_box(native(native_ack, model), session)
    assert profiles.status(BOX_A).profile is not None
    retained = observation(clock, BOX_A, model, offset=256)
    first_epoch = profiles.box_clock_epoch(BOX_A)

    shifted_actuals(profiles, clock, BOX_A, model, 280, samples=3)
    assert profiles.box_clock_epoch(BOX_A) == first_epoch + 1
    assert profiles.status(BOX_A).profile is None
    assert profiles.observe_box(retained, "older-observation") is None
    shifted_actuals(profiles, clock, BOX_A, model, 280, samples=6)
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)
    assert profiles.box_clock_offset(BOX_A) == 280


def test_future_cloud_tsec_needs_matching_box_clock_and_same_flow_ack():
    """An ahead cloud stamp can only enroll via coherent immutable wire evidence."""
    clock = Clock()
    model = (39, 0xBB59)
    profiles = learner(clock)
    shifted_actuals(profiles, clock, BOX_A, model, 256)
    poll = native(f"<Result>IsNewSet</Result><ID_Device>{BOX_A}</ID_Device><Lat>47</Lat>", model)
    profiles.observe_box(poll, "bad-cloud")
    profiles.observe_cloud(cloud_setting(clock, BOX_A, model, include_tto=True, server_id=9, tsec_offset=350),
                           "bad-cloud")
    ack = f"<Result>ACK</Result><Rdt>{clock.stamp(offset=257)}</Rdt><Reason>Setting</Reason>"
    profiles.observe_box(native(ack, model), "bad-cloud")
    assert profiles.status(BOX_A).profile is None

    clock.step(10)
    profiles.observe_box(poll, "coherent-cloud")
    profiles.observe_cloud(cloud_setting(clock, BOX_A, model, include_tto=True, server_id=9, tsec_offset=266),
                           "coherent-cloud")
    clock.step(12)
    reply = native(
        f"<Result>ACK</Result><Rdt>{clock.stamp(offset=256)}</Rdt><Reason>Setting</Reason>", model,
    )
    profiles.observe_box(reply, "bad-cloud")
    assert profiles.status(BOX_A).profile is None
    profiles.observe_box(reply, "coherent-cloud")
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)


def test_same_native_frame_stays_a_replay_after_clock_shift_resets():
    """An unchanged raw frame cannot obtain new evidence from another normalization."""
    clock = Clock()
    model = (39, 0xBB59)
    profiles = learner(clock)
    shifted_actuals(profiles, clock, BOX_A, model, 256)
    session = "establish"
    cloud_transaction(profiles, clock, BOX_A, model, session=session, ack=False, close=False)
    receipt = f"<Result>ACK</Result><Rdt>{clock.stamp(offset=256)}</Rdt><Reason>Setting</Reason>"
    profiles.observe_box(native(receipt, model), session)
    old = observation(clock, BOX_A, model, offset=256)
    old_source = clock.now + 256
    assert profiles.observe_box(old, "before-clock-reset") is not None

    shifted_actuals(profiles, clock, BOX_A, model, 0)
    assert profiles.box_clock_offset(BOX_A) == 0
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)
    clock.now = old_source
    assert profiles.observe_box(old, "clock-has-caught-up") is None


def test_coherent_reduced_clock_shift_fences_old_readbacks():
    """A step back with a new counter epoch revises the clock and fences old events."""
    clock = Clock()
    profiles = learner(clock)
    model = (39, 0xBB59)
    shifted_actuals(profiles, clock, BOX_A, model, 256)
    initial = profiles.box_clock_epoch(BOX_A)
    shifted_actuals(profiles, clock, BOX_A, model, 200, samples=3)
    assert profiles.box_clock_offset(BOX_A) == 200
    assert profiles.box_clock_epoch(BOX_A) == initial + 1
    old_dt = clock.stamp(offset=199)
    assert profiles.box_timestamp(BOX_A, old_dt) == clock.now - 1
    assert profiles.box_readback_timestamp(BOX_A, old_dt) is None
    # Source timestamps overlap between the old and new epochs during the step back.
    assert profiles.box_readback_timestamp(BOX_A, clock.stamp(offset=200)) is None
    clock.step(30)
    assert profiles.box_readback_timestamp(BOX_A, clock.stamp(offset=200)) == clock.now


def test_future_cloud_layout_does_not_revoke_before_same_flow_corroboration():
    """Reject uncorroborated future layout contradictions before any revocation."""
    clock = Clock()
    profiles = learner(clock)
    model = (39, 0xBB59)
    shifted_actuals(profiles, clock, BOX_A, model, 256)
    initial = "initial"
    cloud_transaction(profiles, clock, BOX_A, model, session=initial, ack=False, close=False)
    ack = f"<Result>ACK</Result><Rdt>{clock.stamp(offset=256)}</Rdt><Reason>Setting</Reason>"
    profiles.observe_box(native(ack, model), initial)
    ready = profiles.status(BOX_A)
    assert ready.profile is not None

    poll = native(f"<Result>IsNewSet</Result><ID_Device>{BOX_A}</ID_Device><Lat>47</Lat>", model)
    clock.step()
    profiles.observe_box(poll, "out-of-range")
    bad = cloud_setting(clock, BOX_A, model, include_tto=False, server_id=9, tsec_offset=350)
    profiles.observe_cloud(bad, "out-of-range")
    assert profiles.status(BOX_A) == ready

    clock.step()
    profiles.observe_box(poll, "coherent")
    contrary = cloud_setting(clock, BOX_A, model, include_tto=False, server_id=9, tsec_offset=266)
    profiles.observe_cloud(contrary, "coherent")
    assert profiles.status(BOX_A) == ready
    clock.step(12)
    ack = f"<Result>ACK</Result><Rdt>{clock.stamp(offset=256)}</Rdt><Reason>Setting</Reason>"
    profiles.observe_box(native(ack, model), "coherent")
    assert profiles.status(BOX_A).reason == "ambiguous_setting_layout"


def test_raw_repeats_bad_crc_unacknowledged_and_local_frames_cannot_enroll():
    """Reject a checksum-only fit, duplicate states, and non-natural observations."""
    clock = Clock()
    profiles = learner(clock)
    model = (43, 0x7546)
    box_proof(profiles, clock, BOX_A, model, FW_A, samples=5)
    same = observation(clock, BOX_A, model, same=True)
    for _ in range(8):
        profiles.observe_box(same, "duplicate")
    bad = observation(clock, BOX_A, model).replace(b"<Sample>", b"<SamplX>")
    profiles.observe_box(bad, "corrupt")
    cloud_transaction(profiles, clock, BOX_A, model)
    assert profiles.status(BOX_A).profile is None
    profiles.observe_box(observation(clock, BOX_A, model), "sixth")
    cloud_transaction(profiles, clock, BOX_A, model, ack=False)
    cloud_transaction(profiles, clock, BOX_A, model, local=True)
    assert profiles.status(BOX_A).profile is None
    cloud_transaction(profiles, clock, BOX_A, model)
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)


def test_firmware_or_ver_drift_retires_the_previous_epoch():
    """Require fresh proof after same-firmware rotation and firmware change."""
    clock = Clock()
    profiles = learner(clock)
    old = (44, 0x7BFC)
    new = (44, 0xFEC0)
    box_proof(profiles, clock, BOX_A, old, FW_A)
    cloud_transaction(profiles, clock, BOX_A, old)
    first = profiles.status(BOX_A)
    assert first.profile == ver_profile.VerProfile(*old)
    new_poll = native(f"<Result>IsNewSet</Result><ID_Device>{BOX_A}</ID_Device><Lat>47</Lat>", new)
    profiles.observe_box(new_poll, "drift")
    retired = profiles.status(BOX_A)
    assert retired.profile is None and retired.epoch > first.epoch
    box_proof(profiles, clock, BOX_A, new, FW_A, prefix="rotated")
    cloud_transaction(profiles, clock, BOX_A, new)
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*new)
    profiles.observe_box(observation(clock, BOX_A, new, firmware=FW_B), "new-firmware")
    assert profiles.status(BOX_A).profile is None


def test_enrollment_restarts_unready_and_revalidates_only_same_context(tmp_path):
    """Reload an earned layout, while requiring fresh native proof after restart."""
    clock = Clock()
    state_path = tmp_path / "wire-profile.json"
    model = (42, 0x1234)
    profiles = learner(clock, state_path=state_path)
    box_proof(profiles, clock, BOX_B, model, FW_B)
    cloud_transaction(profiles, clock, BOX_B, model, include_tto=False)
    assert profiles.status(BOX_B).profile == ver_profile.VerProfile(*model)
    assert state_path.exists()

    restored = learner(clock, state_path=state_path)
    assert restored.status(BOX_B).profile is None
    box_proof(restored, clock, BOX_B, model, None, samples=5, prefix="restore")
    assert restored.status(BOX_B).profile is None
    restored.observe_box(observation(clock, BOX_B, model), "final-holdout")
    assert restored.status(BOX_B).profile == ver_profile.VerProfile(*model)
    assert not restored.status(BOX_B).include_tto
    clock.step(901)
    assert restored.status(BOX_B).profile is None


def test_enrollment_without_firmware_survives_restart(tmp_path):
    """Qualify without a firmware field and revalidate the retained layout offline."""
    clock = Clock()
    state_path = tmp_path / "wire-profile.json"
    model = (42, 0x1234)
    profiles = learner(clock, state_path=state_path)
    box_proof(profiles, clock, BOX_B, model, None)
    cloud_transaction(profiles, clock, BOX_B, model, include_tto=False, ack=False)
    assert profiles.status(BOX_B).reason == "cloud_evidence_pending"
    cloud_transaction(profiles, clock, BOX_B, model, include_tto=False)
    assert profiles.status(BOX_B).profile == ver_profile.VerProfile(*model)

    restored = learner(clock, state_path=state_path)
    assert restored.status(BOX_B).profile is None
    box_proof(restored, clock, BOX_B, model, None, prefix="restore")
    status = restored.status(BOX_B)
    assert status.profile == ver_profile.VerProfile(*model)
    assert not status.include_tto


def test_current_box_frames_keep_qualification_after_firmware_ages_out():
    """Refresh native proof while the last firmware value grows old."""
    clock = Clock()
    model = (39, 0xBB59)
    profiles = learner(clock)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    cloud_transaction(profiles, clock, BOX_A, model)
    first = profiles.status(BOX_A)
    assert first.profile == ver_profile.VerProfile(*model)

    clock.step(450)
    box_proof(profiles, clock, BOX_A, model, None, prefix="refresh-1")
    clock.step(450)
    box_proof(profiles, clock, BOX_A, model, None, prefix="refresh-2")
    current = profiles.status(BOX_A)
    assert current.profile == first.profile
    assert current.epoch == first.epoch


def test_retained_enrollment_rejects_replayed_pre_start_table_bytes(tmp_path):
    """A newly started learner requires post-start device timestamps."""
    clock = Clock()
    state_path = tmp_path / "wire-profile.json"
    model = (42, 0x1234)
    original = learner(clock, state_path=state_path)
    box_proof(original, clock, BOX_B, model, FW_B)
    cloud_transaction(original, clock, BOX_B, model)
    assert original.status(BOX_B).profile is not None
    previous = [observation(clock, BOX_B, model, firmware=FW_B)]
    previous.extend(observation(clock, BOX_B, model) for _ in range(5))

    clock.step(2)
    restored = learner(clock, state_path=state_path)
    for index, raw in enumerate(previous):
        restored.observe_box(raw, f"replay-{index}")
    assert restored.status(BOX_B).profile is None
    box_proof(restored, clock, BOX_B, model, FW_B, prefix="post-start")
    assert restored.status(BOX_B).profile == ver_profile.VerProfile(*model)


def test_cloud_proof_requires_same_flow_valid_ack_and_serializer_constants():
    """An unrelated or malformed reply cannot enroll a different wire layout."""
    clock = Clock()
    profiles = learner(clock)
    model = (39, 0xABCD)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    profiles.observe_box(observation(clock, BOX_B, model), "other-flow")
    cloud_transaction(profiles, clock, BOX_A, model, reply_session="other-flow")
    cloud_transaction(profiles, clock, BOX_A, model, corrupt_reply=True)
    assert profiles.status(BOX_A).profile is None
    cloud_transaction(profiles, clock, BOX_A, model, server_id=7)
    assert profiles.status(BOX_A).profile is None
    cloud_transaction(profiles, clock, BOX_A, model)
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)
    assert profiles.status(BOX_B).profile is None


@pytest.mark.parametrize("echo", ["ID", "ID_Set"])
def test_cloud_setting_ack_with_mismatched_echo_cannot_enroll(echo):
    """Preserve generic ACK support while checking explicit native identifiers."""
    clock = Clock()
    profiles = learner(clock)
    model = (39, 0xABCD)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    cloud_transaction(profiles, clock, BOX_A, model, bad_echo=echo)
    assert profiles.status(BOX_A).profile is None
    cloud_transaction(profiles, clock, BOX_A, model)
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)


def test_legacy_combined_ack_preserves_telemetry_without_false_echoes():
    """Interpret the unrelated table ID_Set and TblName only as readback."""
    clock = Clock()
    model = (44, 0x1234)
    profile = ver_profile.VerProfile(*model)
    raw = legacy_actual_receipt(clock, BOX_A, model)
    readback = ver_profile.native_frame_fields(raw)
    assert readback is not None
    assert (readback["Reason"], readback["TblName"], readback["ID_Set"]) == ("Table", "tbl_actual", "865000001")
    assert readback["MAXFG"] == "0"
    reply = ver_profile.parse_setting_reply(raw, BOX_A, profile)
    assert reply is not None
    assert (reply["Result"], reply["Reason"], reply["ID_Device"]) == ("ACK", "Setting", BOX_A)
    assert not {"TblName", "ID_Set", "DT", "MAXFG"} & reply.keys()
    assert ver_profile.parse_setting_reply(raw, BOX_B, profile) is None
    assert ver_profile.parse_setting_reply(raw, BOX_A, ver_profile.VerProfile(44, 0x1235)) is None
    assert ver_profile.parse_setting_reply(raw.replace(b"<MAXFG>0", b"<MAXFG>1"), BOX_A, profile) is None
    assert ver_profile.native_frame_fields(legacy_actual_receipt(clock, BOX_A, model, second_reason="Setting")) is None


def test_cloud_exchange_can_enroll_from_same_flow_legacy_combined_ack():
    """A genuine combined receipt qualifies without treating telemetry as an echo."""
    clock = Clock()
    profiles = learner(clock)
    model = (44, 0x1234)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    session = "legacy-ack"
    cloud_transaction(profiles, clock, BOX_A, model, session=session, ack=False, close=False)
    profiles.observe_box(legacy_actual_receipt(clock, BOX_A, model), "unbound")
    assert profiles.status(BOX_A).profile is None
    profiles.observe_box(legacy_actual_receipt(clock, BOX_A, model), session)
    assert profiles.status(BOX_A).profile == ver_profile.VerProfile(*model)


def test_both_setting_layouts_in_same_epoch_revoke_the_enrollment(tmp_path):
    """The serializer cannot choose between equally evidenced layouts."""
    clock = Clock()
    state_path = tmp_path / "wire-profile.json"
    profiles = learner(clock, state_path=state_path)
    model = (44, 0x4321)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    cloud_transaction(profiles, clock, BOX_A, model)
    first = profiles.status(BOX_A)
    assert first.profile is not None
    cloud_transaction(profiles, clock, BOX_A, model, include_tto=False)
    ambiguous = profiles.status(BOX_A)
    assert ambiguous.reason == "ambiguous_setting_layout"
    assert ambiguous.profile is None and ambiguous.epoch > first.epoch
    cloud_transaction(profiles, clock, BOX_A, model)
    assert profiles.status(BOX_A).profile is None
    restored = learner(clock, state_path=state_path)
    box_proof(restored, clock, BOX_A, model, FW_A, prefix="restored")
    assert restored.status(BOX_A).profile is None


@pytest.mark.parametrize("second_on_flow", [False, True])
def test_unsupported_natural_layout_withdraws_existing_enrollment(tmp_path, second_on_flow):
    """Withdraw on a contrary raw Setting even when that flow cannot enroll a model."""
    clock = Clock()
    state_path = tmp_path / "wire-profile.json"
    profiles = learner(clock, state_path=state_path)
    model = (44, 0x4321)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    cloud_transaction(profiles, clock, BOX_A, model)
    first = profiles.status(BOX_A)
    assert first.profile is not None
    session = "occupied-flow"
    if second_on_flow:
        cloud_transaction(profiles, clock, BOX_A, model, session=session, ack=False, close=False)
    cloud_transaction(profiles, clock, BOX_A, model, session=session, server_id=7, ack=False)
    withdrawn = profiles.status(BOX_A)
    assert withdrawn.reason == "ambiguous_setting_layout"
    assert withdrawn.profile is None and withdrawn.epoch > first.epoch
    restored = learner(clock, state_path=state_path)
    box_proof(restored, clock, BOX_A, model, FW_A)
    assert restored.status(BOX_A).profile is None


def test_duplicate_retained_identity_cannot_authorize_writes(tmp_path):
    """Reject a structurally ambiguous registry even with matching fresh BOX evidence."""
    clock = Clock()
    state_path = tmp_path / "wire-profile.json"
    state_path.write_text(
        '{"version":1,"enrollments":{'
        f'"{BOX_A}":{{"N":39,"K":2,"firmware":"{FW_A}","include_tto":true}},'
        f'"{BOX_A}":{{"N":39,"K":1,"firmware":"{FW_A}","include_tto":false}}'
        '}}', encoding="utf-8",
    )
    profiles = learner(clock, state_path=state_path)
    box_proof(profiles, clock, BOX_A, (39, 1), FW_A)
    assert profiles.status(BOX_A).profile is None


@pytest.mark.parametrize("poll_first", [True, False])
def test_late_firmware_baseline_survives_restart_and_detects_change(tmp_path, poll_first):
    """Record a flow-bound firmware value without requiring a new Cloud Setting."""
    clock = Clock()
    state_path = tmp_path / "wire-profile.json"
    profiles = learner(clock, state_path=state_path)
    model = (42, 123)
    box_proof(profiles, clock, BOX_B, model, None)
    cloud_transaction(profiles, clock, BOX_B, model)
    first = profiles.status(BOX_B)
    assert first.profile == ver_profile.VerProfile(*model)

    fw = native(f"<Result>IsNewFW</Result><ID_Device>{BOX_B}</ID_Device><Fw>{FW_B}</Fw>", model)
    if poll_first:
        profiles.observe_box(fw, "firmware-flow")
    profiles.observe_box(observation(clock, BOX_B, model), "firmware-flow")
    if not poll_first:
        profiles.observe_box(fw, "firmware-flow")
    assert profiles.status(BOX_B) == first

    restored = learner(clock, state_path=state_path)
    box_proof(restored, clock, BOX_B, model, None, prefix="restore")
    ready = restored.status(BOX_B)
    assert ready.profile == first.profile
    restored.observe_box(observation(clock, BOX_B, model, firmware=FW_A), "new-firmware")
    changed = restored.status(BOX_B)
    assert changed.profile is None and changed.epoch > ready.epoch
    box_proof(restored, clock, BOX_B, model, None, prefix="after-change")
    assert restored.status(BOX_B).reason == "cloud_evidence_pending"


def test_delayed_firmware_report_does_not_revoke_current_profile():
    """Ignore delayed telemetry for an earlier firmware value on the same profile."""
    clock = Clock()
    profiles = learner(clock)
    model = (42, 123)
    box_proof(profiles, clock, BOX_B, model, None)
    cloud_transaction(profiles, clock, BOX_B, model)
    ready = profiles.status(BOX_B)
    assert ready.profile == ver_profile.VerProfile(*model)

    delayed = observation(clock, BOX_B, model, firmware=FW_A)
    profiles.observe_box(observation(clock, BOX_B, model, firmware=FW_B), "current")
    profiles.observe_box(delayed, "delayed")
    assert profiles.status(BOX_B) == ready


@pytest.mark.parametrize("with_tto", [True, False])
def test_serializer_emits_exact_layout_and_checksums_for_configured_device(with_tto):
    """Serialize either verified layout, without selecting a per-device seed."""
    profile = ver_profile.VerProfile(39, 0xBB59)
    wire = build_setting_frame(
        device_id=BOX_A, table="tbl_box_prms", key="MODE", value=2,
        id_set=int(datetime.now(timezone.utc).timestamp()) - 2,
        msg_id=14800200, profile=profile, include_tto=with_tto,
    )
    frame = ET.fromstring(wire)
    assert frame.findtext("ID_Device") == BOX_A
    keys = [field.tag for field in frame]
    assert ("TTO_P" in keys) is with_tto
    assert ("TTO_E" in keys) is with_tto
    if with_tto:
        assert keys.index("TblItem") + 1 == keys.index("TTO_P")
        assert keys.index("TTO_E") + 1 == keys.index("ID_Server")
    else:
        assert keys.index("TblItem") + 1 == keys.index("ID_Server")
    assert int(frame.findtext("ver")) == crc(wire[7:wire.index(b"<ver>")] + bytes(39)) ^ 0xBB59
    assert int(frame.findtext("CRC")) == crc(wire[7:wire.index(b"<CRC>")])
