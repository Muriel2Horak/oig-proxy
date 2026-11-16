"""Verify archive recovery from recorded physical wire evidence."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from protocol.archive_enrollment import scan_archive_enrollment
from protocol.frames import build_frame
from protocol.ver_profile import VerProfile
from tests.v2.test_wire_profile_qualification import (
    BOX_A, FW_A, FW_B, Clock, box_proof, cloud_setting, cloud_transaction, learner,
    legacy_actual_receipt, native, observation,
)


BOX = "2206000001"
OTHER_BOX = "2206000002"
PEER = "192.0.2.10:30401"
MODEL = VerProfile(44, 0x5136)
OTHER_MODEL = VerProfile(42, 0x1234)
BASE = datetime(2026, 11, 5, 6, 30, tzinfo=timezone.utc)
PRAGUE = ZoneInfo("Europe/Prague")
CLOUD_TASK = 103
BOX_TASK = 104


def wire(inner, model=MODEL):
    """Serialize a fixture with the selected ver and frame CRC."""
    return build_frame(f"{inner}<ver>{model.compute(inner.encode()):05d}</ver>")


def poll(result="IsNewSet"):
    """Build a native poll from the test BOX."""
    return wire(f"<Result>{result}</Result><ID_Device>{BOX}</ID_Device><Lat>2314</Lat>")


def setting(second=1, *, include_tto=True, msg_id=14800012):
    """Build a Cloud Setting with independently selectable layout and wire ID."""
    instant = BASE + timedelta(seconds=second)
    local = instant.astimezone(PRAGUE).strftime("%d.%m.%Y %H:%M:%S")
    tto = "<TTO_P>0,000</TTO_P><TTO_E>0,000</TTO_E>" if include_tto else ""
    return wire(
        f"<ID>{msg_id}</ID><ID_Device>{BOX}</ID_Device>"
        f"<ID_Set>{int(instant.timestamp())}</ID_Set><ID_SubD>0</ID_SubD><DT>{local}</DT>"
        "<NewValue>1</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName>"
        f"<TblItem>MODE</TblItem>{tto}<ID_Server>9</ID_Server><mytimediff>0</mytimediff>"
        f"<Reason>Setting</Reason><TSec>{instant:%Y-%m-%d %H:%M:%S}</TSec>"
    )


def ack(second=4, *, echoes=""):
    """Build a Setting ACK with optional native correlation echoes."""
    local = (BASE + timedelta(seconds=second)).astimezone(PRAGUE)
    return wire(
        f"<Result>ACK</Result><Rdt>{local:%Y-%m-%d %H:%M:%S}</Rdt>"
        f"<Reason>Setting</Reason>{echoes}"
    )


def firmware_report(firmware, *, second=None, device=BOX, model=MODEL):
    """Build a firmware poll or a dated parameter-table firmware report."""
    if second is None:
        return wire(f"<Result>IsNewFW</Result><ID_Device>{device}</ID_Device><Fw>{firmware}</Fw>", model)
    local = (BASE + timedelta(seconds=second)).astimezone(PRAGUE)
    return wire(
        f"<TblName>tbl_box_prms</TblName><DT>{local:%Y-%m-%d %H:%M:%S}</DT>"
        f"<Reason>Table</Reason><ID_Device>{device}</ID_Device><SW>{firmware}</SW>",
        model,
    )


@pytest.fixture(name="database")
def archive_database(tmp_path):
    """Create a real SQLite archive with the production capture columns used here."""
    path = tmp_path / "payloads.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE frames(id INTEGER PRIMARY KEY, ts TEXT, device_id TEXT, "
        "raw TEXT, raw_b64 TEXT, direction TEXT, conn_id INTEGER, peer TEXT, length INTEGER)"
    )

    def insert(  # pylint: disable=too-many-arguments
        number, second, direction, task, content, *, peer=PEER, device=None,
    ):
        stamp = (BASE + timedelta(seconds=second)).isoformat()
        connection.execute(
            "INSERT INTO frames VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)",
            (number, stamp, device, content, direction, task, peer, len(content.encode())),
        )
        connection.commit()

    yield path, insert
    connection.close()


def scan(path, model=MODEL):
    """Scan test captures made before the fixed recording cutoff."""
    return scan_archive_enrollment(
        path, BOX, model, cutoff=(BASE + timedelta(seconds=30)).timestamp()
    )


def seed(insert, *, include_tto=True):
    """Insert an acknowledged same-peer exchange with the requested layout."""
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(include_tto=include_tto), device=BOX)
    insert(3, 4, "box_to_cloud", BOX_TASK, ack())


@pytest.mark.parametrize("include_tto", [False, True])
def test_original_box_poll_and_ack_bracket_proves_cloud_layout_and_counters(database, include_tto):
    """Recover both wire counters and either layout only under the same native ver model."""
    path, insert = database
    seed(insert, include_tto=include_tto)
    evidence = scan(path)
    assert evidence is not None
    assert evidence.include_tto is include_tto
    assert evidence.msg_id == 14800012
    assert evidence.id_set == int(BASE.timestamp()) + 1
    assert evidence.accepted_at == BASE.timestamp() + 4
    assert evidence.firmware is None
    assert scan(path, OTHER_MODEL) is None


@pytest.mark.parametrize("dated", [False, True])
def test_archived_setting_is_retired_by_known_later_firmware_change(database, dated):
    """Reject even with identical N/K when native evidence proves a later firmware change."""
    path, insert = database
    insert(1, -3, "box_to_cloud", 95, firmware_report(FW_A), device=BOX)
    insert(2, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(3, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(4, 4, "box_to_cloud", BOX_TASK, ack())
    content = firmware_report(FW_B, second=8 if dated else None)
    insert(5, 8, "box_to_cloud", 98, content, device=BOX)
    assert scan(path) is None


def test_archived_setting_returns_latest_valid_firmware_at_exchange(database):
    """Choose the newest earlier report and ignore an older table delivered late."""
    path, insert = database
    insert(1, -5, "box_to_cloud", 95, firmware_report(FW_A), device=BOX)
    insert(2, -1, "box_to_cloud", 96, firmware_report(FW_B, second=-1), device=BOX)
    insert(3, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(4, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(5, 4, "box_to_cloud", BOX_TASK, ack())
    insert(6, 7, "box_to_cloud", 98, firmware_report(FW_A, second=-4), device=BOX)
    evidence = scan(path)
    assert evidence is not None
    assert evidence.firmware == FW_B


def test_firmware_report_only_after_setting_does_not_guess_historical_version(database):
    """Leave the historical firmware unknown when only one later report exists."""
    path, insert = database
    seed(insert)
    insert(4, 8, "box_to_proxy", 98, firmware_report(FW_B), device=BOX)
    evidence = scan(path)
    assert evidence is not None
    assert evidence.firmware is None


@pytest.mark.parametrize("failure", ["model", "device", "crc"])
def test_untrusted_firmware_report_does_not_retire_historical_setting(database, failure):
    """Ignore other identities or reports without matching ver and CRC."""
    path, insert = database
    insert(1, -3, "box_to_cloud", 95, firmware_report(FW_A), device=BOX)
    insert(2, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(3, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(4, 4, "box_to_cloud", BOX_TASK, ack())
    content = firmware_report(
        FW_B, device=OTHER_BOX if failure == "device" else BOX,
        model=OTHER_MODEL if failure == "model" else MODEL,
    )
    if failure == "crc":
        content = content.replace(f"<Fw>{FW_B}", f"<Fw>{FW_B}1")
    insert(5, 8, "box_to_cloud", 98, content, device=BOX)
    evidence = scan(path)
    assert evidence is not None
    assert evidence.firmware == FW_A


@pytest.mark.parametrize(
    "failure", ["different_peer", "different_task", "counter_echo", "capture_gap", "capture_identity"]
)
def test_ambiguous_or_contradictory_ack_never_enrolls(database, failure):
    """Require matching receipt evidence, stable connection, and continuous capture rows."""
    path, insert = database
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(
        4 if failure == "capture_gap" else 3,
        4,
        "box_to_cloud",
        BOX_TASK + 1 if failure == "different_task" else BOX_TASK,
        ack(echoes="<ID>14800013</ID>" if failure == "counter_echo" else ""),
        peer="192.0.2.10:30402" if failure == "different_peer" else PEER,
        device=OTHER_BOX if failure == "capture_identity" else None,
    )
    assert scan(path) is None


def test_later_exact_writes_raise_independent_counter_high_water(database):
    """Include later exact writes in either direction of origin up to the cutoff."""
    path, insert = database
    seed(insert)
    insert(4, 8, "proxy_to_box", 111, setting(8, msg_id=14800020), device=BOX)
    insert(5, 9, "cloud_to_box", 112, setting(9, msg_id=14800015), device=BOX)
    insert(6, 31, "cloud_to_box", 113, setting(31, msg_id=14800099), device=BOX)
    evidence = scan(path)
    assert evidence is not None
    assert evidence.msg_id == 14800020
    assert evidence.id_set == int(BASE.timestamp()) + 9


def replace_wire_fields(frame, replacements):
    """Recalculate ver and CRC after changing raw XML in a test frame."""
    inner = frame.split("<ver>", 1)[0].removeprefix("<Frame>")
    for original, replacement in replacements:
        inner = inner.replace(original, replacement)
    return wire(inner)


@pytest.mark.parametrize("alternate_reason", ["<Reason >Setting", "<Reason>S&#101;tting"])
def test_xml_equivalent_later_setting_updates_both_counters(database, alternate_reason):
    """Inspect XML-equivalent Settings even when no ACK bracket is open."""
    path, insert = database
    seed(insert)
    content = replace_wire_fields(
        setting(8, msg_id=14800021), [("<Reason>Setting", alternate_reason)]
    )
    insert(4, 8, "cloud_to_box", CLOUD_TASK, content, device=BOX)
    evidence = scan(path)
    assert evidence is not None
    assert evidence.msg_id == 14800021
    assert evidence.id_set == int(BASE.timestamp()) + 8


def test_xml_equivalent_ack_is_a_positive_receipt(database):
    """Build a bracket from a parsed ACK with an equivalent XML spelling."""
    path, insert = database
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    content = replace_wire_fields(ack(), [("<Result>ACK", "<Result >A&#67;K")])
    insert(3, 4, "box_to_cloud", BOX_TASK, content)
    assert scan(path) is not None


def test_conflicting_captured_identity_breaks_open_bracket(database):
    """Fence a same-endpoint exchange when stored device identity conflicts."""
    path, insert = database
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(3, 2, "box_to_cloud", BOX_TASK, wire("<Result>ACK</Result>"), device=OTHER_BOX)
    insert(4, 4, "box_to_cloud", BOX_TASK, ack())
    assert scan(path) is None


@pytest.mark.parametrize("failure", ["later_layout_conflict", "missing_archive_row"])
def test_incomplete_or_conflicting_later_history_fences_archive(database, failure):
    """Reject archive recovery if the later layout is uncertain or changed."""
    path, insert = database
    seed(insert)
    if failure == "later_layout_conflict":
        insert(4, 8, "cloud_to_box", 111, setting(8, include_tto=False), device=BOX)
    else:
        insert(5, 8, "proxy_to_box", 111, setting(8), device=BOX)
    assert scan(path) is None


def test_exact_combined_legacy_receipt_is_positive_ack(database):
    """Keep telemetry in an attached table out of receipt correlation."""
    path, insert = database
    clock = Clock()
    clock.now = BASE.timestamp() + 4
    content = legacy_actual_receipt(clock, BOX, (MODEL.zero_count, MODEL.xor_key)).decode()
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(3, 4, "box_to_cloud", BOX_TASK, content, device=BOX)
    assert scan(path) is not None


@pytest.mark.parametrize(
    "boundary", ["END", "NACK", "Cloud Setting", "box_to_proxy", "opposing_native_model"]
)
def test_conflicting_flow_inside_receipt_bracket_fences_evidence(database, boundary):
    """Reject a transaction interrupted by another flow, command, or native model."""
    path, insert = database
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    if boundary == "Cloud Setting":
        insert(3, 2, "cloud_to_box", CLOUD_TASK, setting(2, msg_id=14800013), device=BOX)
    elif boundary == "box_to_proxy":
        insert(3, 2, "box_to_proxy", BOX_TASK + 1, poll(), device=BOX)
    elif boundary == "opposing_native_model":
        table = wire(f"<TblName>tbl_actual</TblName><ID_Device>{BOX}</ID_Device>", OTHER_MODEL)
        insert(3, 2, "box_to_cloud", BOX_TASK, table, device=BOX)
    else:
        insert(3, 2, "box_to_cloud", BOX_TASK, wire(f"<Result>{boundary}</Result>"))
    insert(4, 4, "box_to_cloud", BOX_TASK, ack())
    assert scan(path) is None


def test_historical_clock_advance_without_live_proof_is_not_an_archive_witness(database):
    """Reject conflicting capture, Cloud, and Box timestamps in archived proof."""
    path, insert = database
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(150), device=BOX)
    insert(3, 4, "box_to_cloud", BOX_TASK, ack(153))
    assert scan(path) is None


def test_firmware_poll_on_original_box_task_can_bound_cloud_exchange(database):
    """Allow an exact native firmware poll as the first frame of the proof."""
    path, insert = database
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(result="IsNewFW"), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(3, 4, "box_to_cloud", BOX_TASK, ack())
    assert scan(path) is not None


@pytest.mark.parametrize("malformed_setting", [False, True])
def test_unparsed_later_outbound_without_setting_marker_preserves_history(database, malformed_setting):
    """Fence potentially corrupt Settings while skipping unrelated malformed Weather."""
    path, insert = database
    seed(insert)
    if malformed_setting:
        content = setting(8).replace("<Confirm>New", "<Confirm>Bad")
    else:
        content = "<Frame><Result>Weather</Result><CRC>00000</CRC></Frame>\r\n"
    insert(4, 8, "cloud_to_box", CLOUD_TASK + 1, content)
    assert (scan(path) is None) is malformed_setting


@pytest.mark.parametrize("reason", ["Weather", "S&#101;tting", "Weather</Result>"])
def test_unparsed_outbound_with_reason_requires_unambiguous_xml_to_skip(database, reason):
    """Skip real Weather XML with duplicate native fields, while fencing ambiguous frames."""
    path, insert = database
    seed(insert)
    content = wire(
        f"<Result>Weather</Result><Reason>{reason}</Reason><Detail>1</Detail><Detail>2</Detail>"
    )
    insert(4, 8, "cloud_to_box", CLOUD_TASK + 1, content)
    assert (scan(path) is not None) is (reason == "Weather")


def test_unparsed_weather_inside_proof_bracket_fences_same_peer(database):
    """Drop an open bracket containing an unverified same-peer Cloud frame."""
    path, insert = database
    insert(1, 0, "box_to_cloud", BOX_TASK, poll(), device=BOX)
    insert(2, 1, "cloud_to_box", CLOUD_TASK, setting(), device=BOX)
    insert(3, 2, "cloud_to_box", CLOUD_TASK, "<Frame><Result>Weather</Result></Frame>")
    insert(4, 4, "box_to_cloud", BOX_TASK, ack())
    assert scan(path) is None


@pytest.mark.parametrize(("tail", "blocks_recovery"), [
    ("<Bin><forecast></Bin>", False),
    ("<Bin><forecast></Bin><Reason >Setti&#110;g</Reason>", True),
])
def test_legacy_weather_with_broken_payload_fences_only_possible_setting(database, tail, blocks_recovery):
    """The captured Weather header identifies traffic with no possible Setting reason."""
    path, insert = database
    seed(insert)
    weather = f"<Frame><Reason>Weather</Reason><Result>Weather</Result>{tail}</Frame>"
    insert(4, 8, "cloud_to_box", CLOUD_TASK + 1, weather)
    assert (scan(path) is None) is blocks_recovery


def test_incomplete_later_outbound_frame_can_hide_a_setting_and_blocks_recovery(database):
    """Reject incomplete later outbound frames that can hide counter advances."""
    path, insert = database
    seed(insert)
    insert(4, 8, "cloud_to_box", CLOUD_TASK + 1, "<Frame><ID>14800013</ID>")
    assert scan(path) is None


def test_profile_adoption_requires_fresh_native_proof_and_does_not_mutate_on_check(tmp_path):
    """Separate validation from persistence and recheck the live epoch and freshness."""
    clock = Clock()
    path = tmp_path / "wire-profile.json"
    profiles = learner(clock, state_path=path)
    model = (42, 0x1234)
    profile = VerProfile(*model)
    assert profiles.archive_basis(BOX_A) is None
    box_proof(profiles, clock, BOX_A, model, FW_A)
    assert profiles.status(BOX_A).reason == "cloud_evidence_pending"
    assert profiles.archive_basis(BOX_A) == (profile, 0)
    assert not profiles.can_adopt_archive_profile(BOX_A, profile, 1, True)
    assert profiles.can_adopt_archive_profile(BOX_A, profile, 0, False)
    assert not path.exists()
    assert profiles.status(BOX_A).reason == "cloud_evidence_pending"
    assert profiles.adopt_archive_profile(BOX_A, profile, 0, False)
    assert path.exists()
    assert profiles.status(BOX_A).profile == profile
    assert profiles.archive_basis(BOX_A) == (profile, 0)
    assert profiles.adopt_archive_profile(BOX_A, profile, 0, False)
    assert not profiles.can_adopt_archive_profile(BOX_A, profile, 0, True)
    clock.step(901)
    assert not profiles.can_adopt_archive_profile(BOX_A, profile, 0, False)


@pytest.mark.parametrize("disagreement", ["layout", "model"])
def test_live_conflict_before_native_qualification_prevents_archive_adoption(disagreement):
    """Retain an earlier contradictory live Cloud frame across native qualification."""
    clock = Clock()
    profiles = learner(clock)
    model = (42, 0x1234)
    cloud_transaction(
        profiles, clock, BOX_A, (44, 0x5136) if disagreement == "model" else model,
        include_tto=disagreement != "layout", ack=False,
    )
    box_proof(profiles, clock, BOX_A, model, FW_A)
    assert profiles.archive_basis(BOX_A) is None


def test_live_cloud_setting_still_pending_prevents_archive_adoption():
    """Defer archive recovery while a live Cloud Setting awaits its receipt."""
    clock = Clock()
    profiles = learner(clock)
    model = (42, 0x1234)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    cloud_transaction(profiles, clock, BOX_A, model, ack=False, session="cloud", close=False)
    assert profiles.archive_basis(BOX_A) is None
    profiles.close_session("cloud")
    assert profiles.archive_basis(BOX_A) is None
    cloud_transaction(profiles, clock, BOX_A, model)
    assert profiles.status(BOX_A).profile == VerProfile(*model)


@pytest.mark.parametrize("enrolled", [False, True])
def test_firmware_retirement_fences_unchanged_affine_model_across_restart(tmp_path, enrolled):
    """An unchanged native affine model does not erase a known firmware change."""
    clock = Clock()
    state_path = tmp_path / "ver-profile.json"
    model = (42, 0x1234)
    profiles = learner(clock, state_path=state_path)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    if enrolled:
        cloud_transaction(profiles, clock, BOX_A, model)
    profiles.observe_box(observation(clock, BOX_A, model, firmware=FW_B), "new-fw")
    box_proof(profiles, clock, BOX_A, model, FW_B, prefix="changed")
    assert profiles.archive_basis(BOX_A) is None
    assert json.loads(state_path.read_text()) == {"version": 1, "enrollments": {}}

    restarted = learner(clock, state_path=state_path)
    box_proof(restarted, clock, BOX_A, model, FW_B, prefix="restart")
    assert restarted.archive_basis(BOX_A) is None
    cloud_transaction(restarted, clock, BOX_A, model)
    assert restarted.status(BOX_A).profile == VerProfile(*model)


def test_matching_retained_layout_allows_archive_counter_recovery_after_restart(tmp_path):
    """Permit a matching saved enrollment to recover counters after fresh native proof."""
    clock = Clock()
    state_path = tmp_path / "ver-profile.json"
    model = (42, 0x1234)
    original = learner(clock, state_path=state_path)
    box_proof(original, clock, BOX_A, model, FW_A)
    cloud_transaction(original, clock, BOX_A, model, include_tto=False)
    restarted = learner(clock, state_path=state_path)
    box_proof(restarted, clock, BOX_A, model, FW_A, prefix="restart")
    basis = restarted.archive_basis(BOX_A)
    assert basis is not None
    assert restarted.can_adopt_archive_profile(BOX_A, *basis, False)
    assert not restarted.can_adopt_archive_profile(BOX_A, *basis, True)


def test_later_compatible_unacknowledged_cloud_does_not_reopen_archive():
    """Keep boot-local Cloud priority when a compatible Setting follows a conflict."""
    clock = Clock()
    profiles = learner(clock)
    model = (42, 0x1234)
    cloud_transaction(profiles, clock, BOX_A, model, include_tto=False, ack=False)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    cloud_transaction(profiles, clock, BOX_A, model, include_tto=True, ack=False)
    assert profiles.archive_basis(BOX_A) is None
    assert profiles.status(BOX_A).reason == "cloud_evidence_pending"


@pytest.mark.parametrize("alternate_reason", ["<Reason >Setting", "<Reason>S&#101;tting"])
def test_xml_equivalent_live_cloud_setting_takes_priority(alternate_reason):
    """Fence the archive for exact Cloud bytes with equivalent XML spellings."""
    clock = Clock()
    profiles = learner(clock)
    model = (MODEL.zero_count, MODEL.xor_key)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    clock.step()
    profiles.observe_box(native(f"<Result>IsNewSet</Result><ID_Device>{BOX_A}</ID_Device>", model), "cloud")
    payload = cloud_setting(clock, BOX_A, model, include_tto=True, server_id=9).decode()
    content = replace_wire_fields(payload, [("<Reason>Setting", alternate_reason)])
    profiles.observe_cloud(content.encode(), "cloud")
    profiles.close_session("cloud")
    assert profiles.archive_basis(BOX_A) is None


def test_failed_archive_persistence_never_enables_the_profile(tmp_path):
    """Keep the in-memory profile unavailable if a configured archive save fails."""
    clock = Clock()
    state_path = tmp_path / "missing-parent" / "ver-profile.json"
    profiles = learner(clock, state_path=state_path)
    model = (42, 0x1234)
    box_proof(profiles, clock, BOX_A, model, FW_A)
    basis = profiles.archive_basis(BOX_A)
    assert basis is not None
    assert not profiles.adopt_archive_profile(BOX_A, *basis, True)
    assert profiles.status(BOX_A).reason == "cloud_evidence_pending"

    cloud_transaction(profiles, clock, BOX_A, model)
    assert profiles.status(BOX_A).profile == VerProfile(*model)
