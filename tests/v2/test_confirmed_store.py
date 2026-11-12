"""Durable observations survive reload without accepting foreign or invalid JSON."""

# Test names describe the contracts being exercised.
# pylint: disable=missing-function-docstring
from __future__ import annotations

import json

import pytest

from sensor.confirmed_store import ConfirmedStateStore



def test_roundtrip_preserves_original_provenance_and_baseline(tmp_path):
    path = tmp_path / "confirmed.json"
    store = ConfirmedStateStore(path, "1234567890")
    assert store.record(
        "tbl_box_prms",
        {"MODE": 1, "OTHER": "0012"},
        source_dt="2026-11-05 10:00:00",
        priority=0,
        origin="direct",
        baseline=True,
    )
    restored = ConfirmedStateStore(path, "1234567890").snapshot()
    assert restored["version"] == 1
    assert restored["namespace"] == "oig_local"
    table = restored["tables"]["tbl_box_prms"]
    assert table["baseline"] is True
    assert table["fields"]["MODE"]["value"] == 1
    assert table["fields"]["MODE"]["sourceDT"] == "2026-11-05 10:00:00"
    assert table["fields"]["MODE"]["priority"] == 0
    assert table["fields"]["MODE"]["origin"] == "direct"
    assert table["fields"]["MODE"]["received_at"]
    assert table["fields"]["OTHER"]["value"] == "0012"


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), {1: "bad"}, {"x": float("inf")}, object()]
)
def test_rejects_nonfinite_or_non_json_values_without_replacing_file(tmp_path, value):
    path = tmp_path / "confirmed.json"
    store = ConfirmedStateStore(path, "1234567890")
    assert store.record(
        "tbl_box_prms", {"MODE": 1}, source_dt=None, priority=0, origin="direct", baseline=True
    )
    before = path.read_bytes()
    assert not store.record(
        "tbl_box_prms", {"MODE": value}, source_dt=None, priority=0, origin="direct", baseline=True
    )
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "truncated",
        "version",
        "device_id",
        "namespace",
        "finite",
        "baseline",
        "priority",
        "sourceDT",
        "origin",
        "missing_sourceDT",
        "missing_ordering_sourceDT",
    ],
)
def test_invalid_cache_remains_as_evidence_and_never_restores_tables(tmp_path, damage):
    path = tmp_path / "confirmed.json"
    store = ConfirmedStateStore(path, "1234567890")
    store.record(
        "tbl_box_prms", {"MODE": 1}, source_dt=None, priority=0, origin="direct", baseline=True
    )
    document = json.loads(path.read_text())
    if damage in {"version", "device_id", "namespace"}:
        document[damage] = "foreign"
    elif damage == "baseline":
        document["tables"]["tbl_box_prms"]["baseline"] = "true"
    elif damage in {"priority", "sourceDT", "origin"}:
        document["tables"]["tbl_box_prms"]["fields"]["MODE"][damage] = []
    elif damage.startswith("missing_"):
        del document["tables"]["tbl_box_prms"]["fields"]["MODE"][damage.removeprefix("missing_")]
    elif damage == "finite":
        document["tables"]["tbl_box_prms"]["fields"]["MODE"]["value"] = float("nan")
    path.write_text("{" if damage == "truncated" else json.dumps(document))
    bad = path.read_bytes()
    restored = ConfirmedStateStore(path, "1234567890")
    assert restored.snapshot()["tables"] == {}
    restored.record(
        "tbl_box_prms", {"MODE": 2}, source_dt=None, priority=1, origin="confirmed_event"
    )
    assert bad in [item.read_bytes() for item in tmp_path.iterdir() if item != path]
    assert ConfirmedStateStore(path, "1234567890").snapshot()["tables"]["tbl_box_prms"]["baseline"] is False


def test_profile_change_and_explicit_reset_preserve_values_and_original_times(tmp_path):
    path = tmp_path / "confirmed.json"
    store = ConfirmedStateStore(path, "1234567890")
    store.record(
        "tbl_box_prms",
        {"MODE": 1},
        source_dt="2026-11-05 10:00:00",
        priority=0,
        origin="direct",
        baseline=True,
    )
    store.reset_ordering_epoch("clock service")
    snapshot = ConfirmedStateStore(path, "1234567890").snapshot()
    assert snapshot["ordering_epoch"] == 1
    assert snapshot["tables"]["tbl_box_prms"]["baseline"] is True
    assert snapshot["tables"]["tbl_box_prms"]["fields"]["MODE"]["sourceDT"] == "2026-11-05 10:00:00"
    assert store.set_context(firmware="new-fw", profile="new-profile")
    assert store.snapshot()["tables"]["tbl_box_prms"]["baseline"] is False
    restored = ConfirmedStateStore(path, "1234567890")
    assert restored.snapshot()["tables"]["tbl_box_prms"]["baseline"] is False


def test_runtime_and_command_tables_are_never_durable(tmp_path):
    store = ConfirmedStateStore(tmp_path / "confirmed.json", "1234567890")
    for table in (
        "proxy_status",
        "twin_state",
        "proxy_control",
        "tbl_events",
        "tbl_actions",
        "tbl_device",
    ):
        assert not store.record(
            table, {"MODE": 1}, source_dt=None, priority=0, origin="direct", baseline=True
        )
    assert store.snapshot()["tables"] == {}


def test_atomic_write_failure_preserves_previous_snapshot_and_retries(tmp_path, monkeypatch):
    path = tmp_path / "confirmed.json"
    store = ConfirmedStateStore(path, "1234567890")
    store.record(
        "tbl_box_prms", {"MODE": 1}, source_dt=None, priority=0, origin="direct", baseline=True
    )
    original = path.read_bytes()
    import os

    replace = os.replace

    def failed_replace(_source, _target):
        raise OSError("simulated atomic replacement failure")

    with monkeypatch.context() as context:
        context.setattr(os, "replace", failed_replace)
        assert not store.record(
            "tbl_box_prms", {"MODE": 2}, source_dt=None, priority=1, origin="confirmed_event"
        )
        assert path.read_bytes() == original
        assert (
            ConfirmedStateStore(path, "1234567890").snapshot()["tables"]["tbl_box_prms"]["fields"]["MODE"]["value"]
            == 1
        )
    assert os.replace is replace
    assert store.flush()
    assert (
        ConfirmedStateStore(path, "1234567890").snapshot()["tables"]["tbl_box_prms"]["fields"]["MODE"]["value"] == 2
    )
    assert not list(tmp_path.glob(".confirmed.json.*"))


def test_sparse_overwrite_does_not_refresh_sibling_provenance(tmp_path):
    store = ConfirmedStateStore(tmp_path / "confirmed.json", "1234567890")
    store.record(
        "tbl_box_prms",
        {"MODE": 1, "OTHER": 90},
        source_dt="2026-11-05 10:00:00",
        priority=0,
        origin="direct",
        baseline=True,
    )
    old = store.snapshot()["tables"]["tbl_box_prms"]["fields"]["OTHER"]
    store.record(
        "tbl_box_prms",
        {"MODE": 2},
        source_dt="2026-11-05 11:00:00",
        priority=1,
        origin="confirmed_event",
    )
    assert store.snapshot()["tables"]["tbl_box_prms"]["fields"]["OTHER"] == old
