"""Exercise scheduler time and durable metadata without network or command builders."""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from parameter_refresh import ParameterReadbackRefresh, ParameterRefreshScheduler
from config import Config


def test_unscheduled_sa_is_default_with_explicit_periodic_opt_in(monkeypatch):
    monkeypatch.delenv("PARAMETER_REFRESH_INTERVAL_S", raising=False)
    monkeypatch.delenv("FULL_REFRESH_INTERVAL_H", raising=False)
    assert Config().parameter_refresh_interval_s == 0
    monkeypatch.setenv("FULL_REFRESH_INTERVAL_H", "24")
    assert Config().parameter_refresh_interval_s == 86400
    monkeypatch.setenv("PARAMETER_REFRESH_INTERVAL_S", "0")
    assert Config().parameter_refresh_interval_s == 0

START = 1_800_000_000.0


def native(table, offset, **values):
    stamp = datetime.fromtimestamp(START + offset, timezone.utc).isoformat()
    return {"Reason": "Table", "TblName": table, "DT": stamp, **values}


def hg(mode, offset=0):
    return native("tbl_events", offset, Type="Factory", Content=f"HG {mode} Start")


def readback_for(boundary):
    return ParameterReadbackRefresh(clock=lambda: boundary.now, wall_clock=lambda: boundary.now + 1)


def send_if_due(readback, send):
    if not readback.is_due() or not send():
        return False
    readback.mark_sent()
    return True


def readback_outcomes(caplog):
    return [
        (record.readback_table, record.readback_key, record.readback_outcome)
        for record in caplog.records
        if record.name == "parameter_refresh" and hasattr(record, "readback_outcome")
    ]

@pytest.mark.parametrize("table_mode, table_offset, expects_sa", [
    (None, 0, True),
    (0, 1, True),
    (1, -1, True),
    (1, 0, False),
    (1, 1, False),
])
def test_mode_readback_requires_matching_native_table(table_mode, table_offset, expects_sa):
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("II"))
    if table_mode is not None:
        readback.observe(native("tbl_box_prms", table_offset, MODE=str(table_mode)))
    boundary.now += 59
    assert not send_if_due(readback, lambda: boundary.request("mode"))
    boundary.now += 1
    assert send_if_due(readback, lambda: boundary.request("mode")) is expects_sa
    boundary.now += 1
    assert not send_if_due(readback, lambda: boundary.request("mode"))
    assert len(boundary.requests) == int(expects_sa)


def test_recent_native_table_can_precede_mode_event():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(native("tbl_box_prms", 0, MODE="3"))
    readback.observe(hg("IV-UPS"))
    boundary.now += 120
    assert not send_if_due(readback, lambda: pytest.fail("matching table already received"))


@pytest.mark.parametrize(("values", "offset", "expects_sa"), [
    ({}, 1, True),
    ({"P_CAL_S": "90"}, 1, True),
    ({"P_CAL_S": "75"}, -1, True),
    ({"P_CAL_S": "75.0"}, 1, False),
])
def test_matched_ack_observes_correct_native_parameter_before_one_sa(values, offset, expects_sa):
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.note_ack("tbl_invertor_prm1", "P_CAL_S", "75", box_time=(1, START))
    readback.observe(native("tbl_invertor_prm1", offset, **values), box_time=(1, START + offset))
    boundary.now += 59
    assert not readback.is_due()
    boundary.now += 1
    assert send_if_due(readback, lambda: boundary.request("matched ACK")) is expects_sa
    boundary.now += 200
    assert not readback.is_due()
    assert len(boundary.requests) == int(expects_sa)


def test_ack_and_later_events_share_one_deadline_and_exclude_command_only_fields():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.note_ack("tbl_box_prms", "MODE", 1, box_time=(1, None))
    boundary.now += 40
    readback.observe(hg("II", 40), box_time=(1, START + 40))
    boundary.now += 20
    assert send_if_due(readback, lambda: boundary.request("first ACK"))
    readback.note_ack("tbl_box_prms", "SA", 1, box_time=(1, START + 60))
    readback.note_ack("tbl_box_prms", "RQRESET", 1, box_time=(1, START + 61))
    readback.observe(hg("II", 100), box_time=(1, START + 100))
    boundary.now += 200
    assert not readback.is_due()


def test_a_later_acknowledged_write_can_rearm_a_timed_out_readback():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.note_ack("tbl_box_prms", "MODE", 1, box_time=(1, START))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("first write"))
    boundary.now += 120
    assert not readback.is_due()
    readback.note_ack("tbl_box_prms", "MODE", 1, box_time=(1, START + 180))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("new write"))


def test_response_deadline_requires_a_sent_sa_and_matching_expectation():
    boundary = Boundary()
    readback = readback_for(boundary)
    table, key = "tbl_invertor_prm1", "P_CAL_R"
    readback.note_ack(table, key, 92, box_time=(1, START))
    boundary.now += 160
    assert readback.is_due()
    assert readback.response_deadline_for(table, key, 92, box_epoch=1) is None
    readback.mark_sent()
    assert readback.response_deadline_for(table, key, "92.0", box_epoch=1) == boundary.now + 120
    assert readback.response_deadline_for(table, "P_CAL_S", 92, box_epoch=1) is None
    assert readback.response_deadline_for(table, key, 91, box_epoch=1) is None
    assert readback.response_deadline_for(table, key, 92, box_epoch=2) is None
    boundary.now += 120
    assert readback.response_deadline_for(table, key, 92, box_epoch=1) is None


def test_qualified_box_clock_keeps_event_and_readback_in_same_epoch(caplog):
    caplog.set_level("INFO", logger="parameter_refresh")
    boundary = Boundary()
    readback = readback_for(boundary)
    future_mode = hg("II", 250)
    readback.observe(future_mode, box_time=(0, None))
    boundary.now += 60
    assert not readback.is_due()

    readback.observe(future_mode, box_time=(1, START))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("qualified mode"))
    readback.observe(native("tbl_box_prms", 210, MODE="1"), box_time=(1, START - 40))
    assert not readback_outcomes(caplog)
    readback.observe(native("tbl_box_prms", 252, MODE="1"), box_time=(1, START + 2))
    assert readback_outcomes(caplog) == [("tbl_box_prms", "MODE", "confirmed")]


def test_clock_epoch_reset_retires_an_sa_wait_and_rejects_old_epoch():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("II", 250), box_time=(1, START))
    boundary.now += 60
    assert readback.is_due()
    readback.observe(native("tbl_actual", 61), box_time=(2, None))
    readback.observe(hg("II", 255), box_time=(1, START + 5))
    boundary.now += 120
    assert not readback.is_due()
    readback.observe(hg("I", 180), box_time=(2, START + 180))
    boundary.now += 60
    assert readback.is_due()


def test_duplicate_and_older_events_leave_deadline_and_new_mode_supersedes():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("IV-UPS"))
    boundary.now += 35
    readback.observe(hg("IV-UPS", 35))
    readback.observe(hg("I", -1))
    boundary.now += 25
    boundary.admit = False
    assert not send_if_due(readback, lambda: boundary.request("busy"))
    assert len(boundary.requests) == 1
    readback.observe(hg("II", 60))
    boundary.admit = True
    readback.observe(native("tbl_box_prms", 61, MODE="3"))
    boundary.now += 59
    assert not send_if_due(readback, lambda: pytest.fail("must wait for latest mode"))
    boundary.now += 1
    assert send_if_due(readback, lambda: boundary.request("mode"))


@pytest.mark.parametrize("kind, prefix", [("Change", "Input"), ("Setting", "Remotely")])
def test_other_parameters_use_numeric_matching_and_batch_missing_readbacks(kind, prefix):
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(native("tbl_events", 0, Type=kind,
                            Content=f"{prefix} : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[100.0]"))
    readback.observe(native("tbl_invertor_prm1", 1, AAC_MAX_CHRG="100"))
    boundary.now += 60
    assert not send_if_due(readback, lambda: pytest.fail("same numeric value already reported"))

    readback.observe(native("tbl_events", 60, Type=kind,
                            Content=f"{prefix} : tbl_invertor_prm1 / AAC_MAX_CHRG: [100]->[50]"))
    readback.observe(native("tbl_events", 60, Type=kind,
                            Content=f"{prefix} : tbl_invertor_prms / TO_GRID: [0]->[1]"))
    readback.observe(native("tbl_invertor_prm1", 60, AAC_MAX_CHRG="100"))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("generic"))
    readback.observe(native("tbl_events", 60, Type=kind,
                            Content=f"{prefix} : tbl_invertor_prms / TO_GRID: [0]->[1]"))
    boundary.now += 60
    assert not send_if_due(readback, lambda: pytest.fail("one SA covers the batch"))


def test_ignores_actions_no_op_replayed_and_invalid_events():
    boundary = Boundary()
    readback = readback_for(boundary)
    for table, key, old, new in (
        ("tbl_box_prms", "SA", 0, 1),
        ("tbl_box_prms", "RQRESET", 0, 1),
        ("tbl_box_prm2", "APP", 0, 1),
        ("tbl_invertor_prm1", "AAC_MAX_CHRG", 50, "50.0"),
    ):
        readback.observe(native("tbl_events", 0, Type="Setting",
                                Content=f"Remotely : {table} / {key}: [{old}]->[{new}]"))
    readback.observe(hg("II", -3600))
    readback.observe(hg("I") | {"DT": "invalid"})
    boundary.now += 60
    assert not send_if_due(readback, lambda: pytest.fail("no persistent setting change"))


@pytest.mark.parametrize("key", ["ERR_PV", "ERR_ELSE", "ERR_AC", "ERR_GRID", "ERR_BATT"])
def test_warning_transitions_and_ack_do_not_schedule_parameter_refresh(key):
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(native("tbl_events", 0, Type="Change",
                            Content=f"Input : tbl_invertor_prms / {key}: [0]->[4]"))
    boundary.now += 60
    assert not readback.is_due()
    readback.observe(native("tbl_events", 60, Type="Change",
                            Content=f"Input : tbl_invertor_prms / {key}: [4]->[0]"))
    readback.note_ack("tbl_invertor_prms", key, 0, box_time=(1, START + 60))
    boundary.now += 60
    assert not readback.is_due()


def test_same_target_rearms_after_fresh_contradictory_table():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("II"))
    readback.observe(native("tbl_box_prms", 1, MODE="1"))
    boundary.now += 20
    readback.observe(native("tbl_box_prms", 20, MODE="0"))
    boundary.now += 20
    readback.observe(hg("II", 40))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("mode"))


def test_response_timeout_starts_when_sa_is_sent_and_reports_once(caplog):
    caplog.set_level("INFO", logger="parameter_refresh")
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("II"))
    boundary.now += 60
    assert readback.is_due()
    boundary.now += 20
    assert send_if_due(readback, lambda: boundary.request("mode"))
    boundary.now += 119
    readback.poll_timeouts()
    assert not readback_outcomes(caplog)
    boundary.now += 1
    readback.poll_timeouts()
    assert not readback.is_due()
    assert readback_outcomes(caplog) == [("tbl_box_prms", "MODE", "timeout")]

    readback.observe(native("tbl_box_prms", 200, MODE="1"))
    readback.observe(hg("II", 200))
    boundary.now += 60
    assert not readback.is_due()
    assert readback_outcomes(caplog) == [("tbl_box_prms", "MODE", "timeout")]
    assert len(boundary.requests) == 1


def test_mode_table_at_observed_100_second_sa_latency_does_not_time_out(caplog):
    caplog.set_level("INFO", logger="parameter_refresh")
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("II"))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("mode"))

    boundary.now += 100
    readback.observe(native("tbl_box_prms", 90, MODE="1"))
    boundary.now += 20
    readback.poll_timeouts()
    assert readback_outcomes(caplog) == [("tbl_box_prms", "MODE", "confirmed")]
    assert not readback.is_due()


def test_batch_records_matching_and_missing_fields_independently(caplog):
    caplog.set_level("INFO", logger="parameter_refresh")
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("II"))
    readback.observe(native("tbl_events", 0, Type="Setting",
                            Content="Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[100.0]"))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("both"))
    boundary.now += 30
    readback.mark_sent()
    readback.observe(hg("II", 90))
    readback.observe(native("tbl_box_prms", -1, MODE="1"))
    assert not readback_outcomes(caplog)
    readback.observe(native("tbl_box_prms", 1, MODE="1"))
    readback.observe(native("tbl_box_prms", 1, MODE="1"))
    assert readback_outcomes(caplog) == [("tbl_box_prms", "MODE", "confirmed")]

    boundary.now += 90
    assert not readback.is_due()
    assert readback_outcomes(caplog) == [
        ("tbl_box_prms", "MODE", "confirmed"),
        ("tbl_invertor_prm1", "AAC_MAX_CHRG", "timeout"),
    ]
    assert len(boundary.requests) == 1


def test_mode_a_b_a_starts_a_new_wait_and_cannot_reuse_an_older_table(caplog):
    caplog.set_level("INFO", logger="parameter_refresh")
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.observe(hg("II"))
    boundary.now += 60
    assert send_if_due(readback, lambda: boundary.request("first A"))
    boundary.now += 1
    readback.observe(hg("I", 61))
    boundary.now += 1
    readback.observe(hg("II", 62))
    assert readback_outcomes(caplog) == [("tbl_box_prms", "MODE", "superseded")]

    readback.observe(native("tbl_box_prms", 1, MODE="1"))
    boundary.now += 59
    assert not readback.is_due()
    boundary.now += 1
    assert send_if_due(readback, lambda: boundary.request("second A"))
    readback.observe(native("tbl_box_prms", 122, MODE="1"))
    assert readback_outcomes(caplog) == [
        ("tbl_box_prms", "MODE", "superseded"),
        ("tbl_box_prms", "MODE", "confirmed"),
    ]


@pytest.mark.parametrize("table, latest_sample, change_at, due_at", [
    ("tbl_batt_prms", 0, 100, 360),
    ("tbl_invertor_prms", 0, 100, 660),
    ("tbl_batt_prms", 0, 400, 660),
    ("tbl_batt_prms", None, 100, 460),
    ("tbl_invertor_prms", None, 100, 760),
    ("tbl_boiler_prms", None, 100, 100),
    ("tbl_invertor_prm1", 0, 100, 160),
])
def test_reactive_sa_due_tracks_native_table_cadence(table, latest_sample, change_at, due_at):
    boundary = Boundary()
    readback = ParameterReadbackRefresh(clock=lambda: boundary.now, wall_clock=lambda: boundary.now)
    if latest_sample is not None:
        readback.observe(native(table, latest_sample, LIMIT="1"), box_time=(1, START + latest_sample))
    boundary.now = START + change_at
    readback.note_ack(table, "LIMIT", 2, box_time=(1, boundary.now))
    if due_at > change_at:
        boundary.now = START + due_at - 1
        readback.observe(native(table, due_at - 1, LIMIT="1"), box_time=(1, boundary.now))
        assert not readback.is_due()
    boundary.now = START + due_at
    assert readback.is_due()


def test_any_sent_sa_batches_early_fields_and_blocks_another_while_response_is_pending():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.note_ack("tbl_batt_prms", "LIMIT", 2, box_time=(1, START))
    boundary.now += 10
    assert not readback.is_due()
    assert not readback.has_active_batch()
    assert readback.mark_sent()
    assert readback.has_active_batch()
    assert readback.response_deadline_for("tbl_batt_prms", "LIMIT", 2, box_epoch=1) == START + 130

    boundary.now += 10
    readback.note_ack("tbl_boiler_prms", "HEAT", 1, box_time=(1, boundary.now))
    assert not readback.is_due()
    boundary.now += 30
    assert readback.mark_sent()
    assert readback.response_deadline_for("tbl_batt_prms", "LIMIT", 2, box_epoch=1) == START + 130
    assert readback.response_deadline_for("tbl_boiler_prms", "HEAT", 1, box_epoch=1) == START + 170
    boundary.now = START + 170
    assert not readback.has_active_batch()
    assert not readback.is_due()
    assert not readback.mark_sent()


def test_fresh_native_match_confirms_before_cadence_deadline():
    boundary = Boundary()
    readback = readback_for(boundary)
    readback.note_ack("tbl_invertor_prms", "TO_GRID", 1, box_time=(1, START))
    boundary.now += 400
    readback.observe(native("tbl_invertor_prms", 395, TO_GRID="1"), box_time=(1, START + 395))
    boundary.now += 400
    assert not readback.is_due()


@pytest.mark.parametrize("source, offset, aliases, expects_sa", [
    ("tbl_actual", 1, None, True),
    ("IsNewSet", 1, {("tbl_invertor_prm1", "P_MAX_FEED_GRID"): "2000"}, True),
    ("tbl_actual", -1, {("tbl_invertor_prm1", "P_MAX_FEED_GRID"): "2000"}, True),
    ("tbl_actual", 1, {("tbl_invertor_prm1", "P_MAX_FEED_GRID"): "2000"}, False),
])
def test_reactive_readback_accepts_only_explicit_fresh_native_actual_alias(source, offset, aliases, expects_sa):
    boundary = Boundary()
    readback = readback_for(boundary)
    table, key = "tbl_invertor_prm1", "P_MAX_FEED_GRID"
    readback.note_ack(table, key, 2000, box_time=(1, START))
    readback.observe_with_aliases(
        native(source, offset, P_MAX_FEED_GRID="2000", MAXFG="2000"),
        box_time=(1, START + offset), actual_aliases=aliases,
    )
    boundary.now += 60
    assert readback.is_due() is expects_sa


def test_older_alias_does_not_override_newer_direct_readback():
    boundary = Boundary()
    readback = readback_for(boundary)
    table, key = "tbl_invertor_prm1", "P_MAX_FEED_GRID"
    readback.note_ack(table, key, 2000, box_time=(1, START))
    readback.observe(native(table, 30, P_MAX_FEED_GRID="5333"), box_time=(1, START + 30))
    readback.observe_with_aliases(
        native("tbl_actual", 10, MAXFG="2000"), box_time=(1, START + 10),
        actual_aliases={(table, key): "2000"},
    )
    boundary.now += 60
    assert readback.is_due()


def test_missing_field_in_newer_native_table_does_not_admit_an_older_alias():
    boundary = Boundary()
    readback = readback_for(boundary)
    table, key = "tbl_invertor_prm1", "P_MAX_FEED_GRID"
    readback.note_ack(table, key, 2000, box_time=(1, START))
    readback.observe(native(table, 30, P_MAX_FEED_GRID="5333"), box_time=(1, START + 30))
    readback.observe(native(table, 40, AAC_MAX_CHRG="100"), box_time=(1, START + 40))
    readback.observe_with_aliases(
        native("tbl_actual", 20, MAXFG="2000"), box_time=(1, START + 20),
        actual_aliases={(table, key): "2000"},
    )
    boundary.now += 60
    assert readback.is_due()


class Boundary:
    """Control time, admission and idle status at scheduler-owned boundaries."""

    def __init__(self):
        self.now = START
        self.is_idle = True
        self.admit = True
        self.requests = []

    def request(self, reason):
        self.requests.append((self.now, reason))
        return self.admit

    def create(self, path, **kwargs):
        return ParameterRefreshScheduler(
            path, "1234567890", request=self.request,
            idle=lambda: self.is_idle, clock=lambda: self.now, **kwargs
        )


def test_first_boot_requires_full_day_and_admission_is_not_confirmation(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    assert not boundary.requests
    boundary.now = START + 86_399
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()
    assert boundary.requests == [(START + 86_400, "daily")]
    marker = json.loads(path.read_text())
    assert marker == {
        "version": 2, "device_id": "1234567890", "baseline_at": START,
        "last_confirmed_at": None, "last_attempt_at": START + 86_400,
        "last_daily_attempt_at": START + 86_400,
    }
    assert not scheduler.poll()


def test_restart_has_grace_even_with_overdue_persisted_deadline(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    boundary.create(path)
    boundary.now += 172_800
    scheduler = boundary.create(path)
    assert not boundary.requests
    assert not scheduler.poll()
    boundary.now += 299
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()
    assert boundary.requests == [(START + 173_100, "daily")]


def test_busy_or_offline_gate_postpones_daily_request(tmp_path):
    boundary = Boundary()
    scheduler = boundary.create(tmp_path / "schedule.json")
    boundary.now += 86_400
    boundary.is_idle = False
    assert not scheduler.poll()
    boundary.now += 1000
    assert not scheduler.poll()
    boundary.is_idle = True
    assert scheduler.poll()
    assert boundary.requests == [(START + 87_400, "daily")]


def test_rejected_daily_admission_retries_only_after_cooldown(tmp_path):
    boundary = Boundary()
    boundary.admit = False
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 86_400
    assert not scheduler.poll()
    assert json.loads(path.read_text())["last_daily_attempt_at"] is None
    for elapsed in (1, 179, 180, 299):
        boundary.now = START + 86_400 + elapsed
        assert not scheduler.poll()
    assert len(boundary.requests) == 1
    boundary.now += 1
    boundary.is_idle = False
    assert not scheduler.poll()
    boundary.is_idle = True
    boundary.admit = True
    assert scheduler.poll()
    assert len(boundary.requests) == 2


@pytest.mark.parametrize("interval", [900, 86_400])
def test_unconfirmed_daily_admission_keeps_interval_across_restart(tmp_path, interval):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path, interval_s=interval)
    boundary.now += interval
    assert scheduler.poll()
    assert json.loads(path.read_text())["last_daily_attempt_at"] == START + interval
    boundary.now += 300
    assert not scheduler.poll()
    scheduler = boundary.create(path, interval_s=interval)
    boundary.now = START + 2 * interval - 1
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()
    assert boundary.requests == [(START + interval, "daily"), (START + 2 * interval, "daily")]


def test_outcome_unknown_at_interrupted_admission_keeps_durable_daily_reservation(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"

    class Interrupted(BaseException):
        """Model loss of the process during an immediate admission callback."""

    def interrupted(reason):
        boundary.request(reason)
        raise Interrupted

    scheduler = ParameterRefreshScheduler(
        path, "1234567890", request=interrupted,
        idle=lambda: True, clock=lambda: boundary.now,
    )
    boundary.now += 86_400
    with pytest.raises(Interrupted):
        scheduler.poll()
    scheduler = boundary.create(path)
    boundary.now += 300
    assert not scheduler.poll()
    assert boundary.requests == [(START + 86_400, "daily")]


def test_failed_reservation_rollback_keeps_durable_daily_limit(tmp_path, monkeypatch):
    boundary = Boundary()
    path = tmp_path / "schedule.json"

    def fail_replace(_source, _target):
        raise OSError("injected reservation rollback failure")

    def reject(reason):
        boundary.request(reason)
        monkeypatch.setattr("os.replace", fail_replace)
        return False

    scheduler = ParameterRefreshScheduler(
        path, "1234567890", request=reject,
        idle=lambda: True, clock=lambda: boundary.now,
    )
    boundary.now += 86_400
    assert not scheduler.poll()
    assert json.loads(path.read_text())["last_daily_attempt_at"] == START + 86_400
    boundary.now += 300
    assert not scheduler.poll()
    assert boundary.requests == [(START + 86_400, "daily")]


@pytest.mark.parametrize("prior_attempt", [START + 600, START + 86_400])
def test_legacy_marker_guards_possible_daily_admission(tmp_path, prior_attempt):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({
        "version": 1, "device_id": "1234567890", "baseline_at": START,
        "last_confirmed_at": None, "last_attempt_at": prior_attempt,
    }))
    boundary.now += 86_400
    scheduler = boundary.create(path)
    boundary.now += 300
    if prior_attempt >= START + 86_400:
        assert not scheduler.poll()
        boundary.now = prior_attempt + 86_400
    assert scheduler.poll()
    assert boundary.requests == [(boundary.now, "daily")]
    assert json.loads(path.read_text())["version"] == 2
    assert not list(tmp_path.glob("*.invalid-*"))


def test_actual_confirmation_clears_pending_and_restarts_interval(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 86_400
    assert scheduler.poll()
    boundary.now += 30
    scheduler.note_sa_confirmed(boundary.now)
    assert len(boundary.requests) == 1
    assert json.loads(path.read_text())["last_confirmed_at"] == START + 86_430
    boundary.now += 86_399
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()
    assert len(boundary.requests) == 2


@pytest.mark.parametrize("timestamp", [math.nan, math.inf, -1, START - 1, START + 90_000])
def test_invalid_or_stale_confirmation_does_not_postpone_due_work(tmp_path, timestamp):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 86_400
    scheduler.note_sa_confirmed(timestamp)
    assert not boundary.requests
    assert json.loads(path.read_text())["last_confirmed_at"] is None
    assert scheduler.poll()


def test_confirmation_before_active_attempt_cannot_clear_pending_work(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 86_400
    assert scheduler.poll()
    boundary.now += 60
    scheduler.note_sa_confirmed(START + 86_399)
    boundary.now = START + 86_700
    assert not scheduler.poll()
    assert len(boundary.requests) == 1
    assert json.loads(path.read_text())["last_confirmed_at"] is None


def test_duplicate_or_older_confirmation_cannot_move_deadline_back(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 400
    scheduler.note_sa_confirmed(boundary.now)
    scheduler.note_sa_confirmed(boundary.now)
    scheduler.note_sa_confirmed(boundary.now - 10)
    assert json.loads(path.read_text())["last_confirmed_at"] == START + 400
    boundary.now = START + 86_799
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()


@pytest.mark.parametrize("damage", ["truncated", "version", "device_id", "nan", "future", "shape"])
def test_invalid_marker_is_preserved_and_starts_new_daily_baseline(tmp_path, damage):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    boundary.create(path)
    marker = json.loads(path.read_text())
    if damage in {"version", "device_id"}:
        marker[damage] = "foreign"
    elif damage == "nan":
        marker["last_attempt_at"] = math.nan
    elif damage == "future":
        marker["baseline_at"] = START + 999_999
    elif damage == "shape":
        marker = []
    path.write_text("{" if damage == "truncated" else json.dumps(marker))
    original = path.read_bytes()
    boundary.now += 10_000
    scheduler = boundary.create(path)
    assert not boundary.requests
    assert original in [item.read_bytes() for item in tmp_path.iterdir() if item != path]
    boundary.now += 86_399
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()


def test_exceptional_operations_debounce_distinct_and_coalesce_duplicates(tmp_path):
    boundary = Boundary()
    scheduler = boundary.create(
        tmp_path / "schedule.json",
        after_operations=frozenset({("tbl_box_prms", "LIMIT"), ("tbl_box_prms", "ENABLE")}),
    )
    boundary.now += 300
    scheduler.note_operation("tbl_box_prms", "LIMIT")
    boundary.now += 20
    scheduler.note_operation("tbl_box_prms", "ENABLE")
    boundary.now += 20
    scheduler.note_operation("tbl_box_prms", "LIMIT")
    assert not boundary.requests
    boundary.now = START + 379
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()
    assert boundary.requests == [(START + 380, "after_operation")]
    boundary.now += 1
    scheduler.note_sa_confirmed(boundary.now)
    boundary.now += 300
    assert not scheduler.poll()


@pytest.mark.parametrize("operation", [
    ("tbl_box_prms", "MODE"), ("tbl_box_prms", "MODE1"),
    ("tbl_box_prm2", "APP"), ("proxy_control", "PROXY_MODE"),
    ("tbl_box_prms", "SA"), ("tbl_box_prm2", "SA"),
])
def test_modes_and_sa_never_trigger_even_when_configured(tmp_path, operation):
    boundary = Boundary()
    scheduler = boundary.create(tmp_path / "schedule.json", after_operations=frozenset({operation}))
    boundary.now += 300
    scheduler.note_operation(*operation)
    boundary.now += 60
    assert not scheduler.poll()
    assert not boundary.requests


def test_defaults_ignore_all_operations_and_restart_discards_exception_intent(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 300
    scheduler.note_operation("tbl_box_prms", "LIMIT")
    boundary.now += 60
    assert not scheduler.poll()
    scheduler = boundary.create(path, after_operations=frozenset({("tbl_box_prms", "LIMIT")}))
    scheduler.note_operation("tbl_box_prms", "LIMIT")
    scheduler = boundary.create(path, after_operations=frozenset({("tbl_box_prms", "LIMIT")}))
    boundary.now += 400
    assert not scheduler.poll()
    assert not boundary.requests
    assert set(json.loads(path.read_text())) == {
        "version", "device_id", "baseline_at", "last_confirmed_at", "last_attempt_at",
        "last_daily_attempt_at",
    }


def test_failed_atomic_attempt_write_admits_nothing_and_preserves_marker(tmp_path, monkeypatch):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    original = path.read_bytes()
    boundary.now += 86_400

    def fail_replace(_source, _target):
        raise OSError("injected atomic write failure")

    with monkeypatch.context() as context:
        context.setattr("os.replace", fail_replace)
        assert not scheduler.poll()
        assert not boundary.requests
        assert path.read_bytes() == original
    boundary.now += 300
    assert scheduler.poll()
    assert not list(tmp_path.glob(".schedule.json.*"))


def test_invalid_evidence_preservation_failure_never_overwrites_bad_bytes(tmp_path, monkeypatch):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    path.write_text("{invalid evidence")
    original = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("injected evidence preservation failure")

    with monkeypatch.context() as context:
        context.setattr("os.replace", fail_replace)
        scheduler = boundary.create(path)
    boundary.now += 86_400
    assert not scheduler.poll()
    assert not boundary.requests
    assert path.read_bytes() == original


def test_confirmation_older_than_attempt_is_rejected_after_pending_timeout(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 86_400
    assert scheduler.poll()
    boundary.now += 180
    assert not scheduler.poll()
    scheduler.note_sa_confirmed(START + 86_399)
    boundary.now += 120
    assert not scheduler.poll()
    assert json.loads(path.read_text())["last_confirmed_at"] is None


def test_pending_operation_after_confirmation_timestamp_remains_due(tmp_path):
    boundary = Boundary()
    scheduler = boundary.create(
        tmp_path / "schedule.json", after_operations=frozenset({("tbl_box_prms", "LIMIT")})
    )
    boundary.now += 300
    scheduler.note_operation("tbl_box_prms", "LIMIT")
    boundary.now += 10
    scheduler.note_sa_confirmed(START + 299)
    boundary.now = START + 360
    assert scheduler.poll()
    assert boundary.requests == [(START + 360, "after_operation")]


@pytest.mark.parametrize("interval", [0, -1, math.nan, math.inf, True])
def test_invalid_interval_is_rejected_without_marker_or_request(tmp_path, interval):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    with pytest.raises(ValueError, match="interval_s"):
        boundary.create(path, interval_s=interval)
    assert not path.exists()
    assert not boundary.requests


def test_nonfinite_clock_cannot_admit_work(tmp_path):
    boundary = Boundary()
    scheduler = boundary.create(tmp_path / "schedule.json")
    boundary.now = math.nan
    with pytest.raises(ValueError, match="clock"):
        scheduler.poll()
    assert not boundary.requests


def test_admission_exception_uses_durable_retry_cooldown(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"

    def admission_failure(_reason):
        raise OSError("injected admission failure")

    scheduler = ParameterRefreshScheduler(
        path, "1234567890", request=admission_failure,
        idle=lambda: True, clock=lambda: boundary.now,
    )
    boundary.now += 86_400
    assert not scheduler.poll()
    marker = json.loads(path.read_text())
    assert marker["last_attempt_at"] == START + 86_400
    assert marker["last_daily_attempt_at"] is None
    scheduler = boundary.create(path)
    boundary.now += 299
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()
    assert boundary.requests == [(START + 86_700, "daily")]


def test_exceptional_operation_obeys_startup_grace_idle_and_retry_gates(tmp_path):
    boundary = Boundary()
    boundary.admit = False
    scheduler = boundary.create(
        tmp_path / "schedule.json", after_operations=frozenset({("tbl_box_prms", "LIMIT")})
    )
    scheduler.note_operation("tbl_box_prms", "LIMIT")
    boundary.now += 299
    assert not scheduler.poll()
    boundary.now += 1
    boundary.is_idle = False
    assert not scheduler.poll()
    boundary.is_idle = True
    assert not scheduler.poll()
    assert boundary.requests == [(START + 300, "after_operation")]
    boundary.now += 299
    assert not scheduler.poll()
    boundary.now += 1
    boundary.admit = True
    assert scheduler.poll()
    assert boundary.requests[-1] == (START + 600, "after_operation")


def test_unconfirmed_daily_attempt_does_not_suppress_exceptional_work(tmp_path):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    operation = ("tbl_box_prms", "LIMIT")
    scheduler = boundary.create(path, after_operations=frozenset({operation}))
    boundary.now += 86_400
    assert scheduler.poll()
    boundary.now += 300
    scheduler.note_operation(*operation)
    boundary.now += 60
    assert scheduler.poll()
    assert boundary.requests == [(START + 86_400, "daily"), (START + 86_760, "after_operation")]
    assert json.loads(path.read_text())["last_daily_attempt_at"] == START + 86_400


@pytest.mark.parametrize("changes", [
    {"version": True}, {"baseline_at": None}, {"baseline_at": False},
    {"last_confirmed_at": START - 1}, {"last_attempt_at": True},
    {"last_confirmed_at": START + 100}, {"command": "forbidden intent"},
    {"last_daily_attempt_at": START}, {"last_daily_attempt_at": False},
])
def test_malformed_marker_cannot_supply_confirmation_or_command_intent(tmp_path, changes):
    boundary = Boundary()
    path = tmp_path / "schedule.json"
    boundary.create(path)
    marker = json.loads(path.read_text())
    marker.update(changes)
    path.write_text(json.dumps(marker))
    original = path.read_bytes()
    boundary.create(path)
    assert not boundary.requests
    assert original in [item.read_bytes() for item in tmp_path.iterdir() if item != path]
    assert json.loads(path.read_text())["last_confirmed_at"] is None


def test_roundtrip_confirmation_retains_day_deadline_and_no_replay(tmp_path):
    boundary = Boundary()
    path = tmp_path / "nested/schedule.json"
    scheduler = boundary.create(path)
    boundary.now += 500
    scheduler.note_sa_confirmed(boundary.now)
    boundary.now += 100
    scheduler = boundary.create(path)
    assert not boundary.requests
    boundary.now = START + 86_899
    assert not scheduler.poll()
    boundary.now += 1
    assert scheduler.poll()
    assert boundary.requests == [(START + 86_900, "daily")]
