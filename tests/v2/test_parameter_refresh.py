"""Exercise scheduler time and durable metadata without network or command builders."""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import json
import math

import pytest

from parameter_refresh import ParameterRefreshScheduler

START = 1_800_000_000.0


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
        "version": 1, "device_id": "1234567890", "baseline_at": START,
        "last_confirmed_at": None, "last_attempt_at": START + 86_400,
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


@pytest.mark.parametrize("admit", [False, True])
def test_attempt_retries_only_after_timeout_and_cooldown(tmp_path, admit):
    boundary = Boundary()
    boundary.admit = admit
    scheduler = boundary.create(tmp_path / "schedule.json")
    boundary.now += 86_400
    assert scheduler.poll() is admit
    for elapsed in (1, 179, 180, 299):
        boundary.now = START + 86_400 + elapsed
        assert not scheduler.poll()
    assert len(boundary.requests) == 1
    boundary.now += 1
    boundary.is_idle = False
    assert not scheduler.poll()
    boundary.is_idle = True
    assert scheduler.poll() is admit
    assert len(boundary.requests) == 2


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
    assert scheduler.poll()
    assert len(boundary.requests) == 2
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
        "version", "device_id", "baseline_at", "last_confirmed_at", "last_attempt_at"
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
    assert scheduler.poll()
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
    assert json.loads(path.read_text())["last_attempt_at"] == START + 86_400
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


@pytest.mark.parametrize("changes", [
    {"version": True}, {"baseline_at": None}, {"baseline_at": False},
    {"last_confirmed_at": START - 1}, {"last_attempt_at": True},
    {"last_confirmed_at": START + 100}, {"command": "forbidden intent"},
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
