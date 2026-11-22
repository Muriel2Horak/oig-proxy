"""Verify archive recovery commits only proof that is current for this BOX."""

# pylint: disable=protected-access
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.v2.test_wire_profile_qualification import (
    BOX_A, FW_A, FW_B, Clock, box_proof, cloud_setting, cloud_transaction, learner, shifted_actuals,
)
from twin.delivery import TwinDelivery
from twin.state import TwinQueue

MODEL = (39, 0xBB59)


@pytest.mark.asyncio
@pytest.mark.parametrize("race", [None, "live_setting", "box_clock"])
async def test_archive_restore_persists_maximum_counters_and_fences_new_evidence(
    tmp_path, monkeypatch, race,
):
    """Save counters and publish the profile only if the worker's proof is still current."""
    clock = Clock()
    capture_path = tmp_path / "payloads.db"
    counter_path = tmp_path / "counters.json"
    counter_path.write_text(json.dumps({
        "version": 1, "devices": {BOX_A: {"msg_id": 12500002, "id_set": 1700000002}},
    }), encoding="utf-8")
    queue = TwinQueue()
    delivery = TwinDelivery(queue, Mock(), counter_state_path=counter_path, archive_path=capture_path)
    delivery._profiles = learner(clock)
    box_proof(delivery._profiles, clock, BOX_A, MODEL, FW_A)

    def archived(*_args, **_kwargs):
        return SimpleNamespace(msg_id=12500001, id_set=1700000003, include_tto=True, firmware=None)

    async def read_on_worker(scan, path, device_id, profile, **options):
        assert scan is archived and path == capture_path and device_id == BOX_A
        assert profile.zero_count == MODEL[0]
        assert options == {"cutoff": delivery._archive_cutoff, "box_clock_offset": 0}
        if race == "live_setting":
            raw = cloud_setting(clock, BOX_A, MODEL, include_tto=True, server_id=9)
            delivery.begin_cloud_setting(
                device_id=BOX_A, table="tbl_box_prms", key="MODE", value=1, raw_text=raw.decode(),
            )
        elif race == "box_clock":
            shifted_actuals(delivery._profiles, clock, BOX_A, MODEL, 281)
            assert delivery._profiles.box_clock_offset(BOX_A) == 281
        return scan()

    monkeypatch.setattr("twin.delivery.scan_archive_enrollment_with_clock", archived)
    monkeypatch.setattr("twin.delivery.asyncio.to_thread", read_on_worker)
    assert await delivery.restore_archived_control(BOX_A) is (race is None)
    expected_reason = "cloud_evidence_pending" if race else "ready"
    assert delivery.profile_status(BOX_A).reason == expected_reason
    expected_id_set = 1700000002 if race else 1700000003
    assert json.loads(counter_path.read_text(encoding="utf-8"))["devices"][BOX_A] == {
        "msg_id": 12500002, "id_set": expected_id_set,
    }


def test_failed_atomic_revocation_fences_the_archive_on_restart(tmp_path, monkeypatch):
    """A failed replacement leaves an initialized registry instead of a cold install."""
    clock = Clock()
    path = tmp_path / "control_ver_profile.json"
    profiles = learner(clock, state_path=path)
    box_proof(profiles, clock, BOX_A, MODEL, FW_A)
    cloud_transaction(profiles, clock, BOX_A, MODEL)
    assert profiles.status(BOX_A).reason == "ready"

    def unavailable_rename(*_args):
        raise OSError("rename unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", unavailable_rename)
        box_proof(profiles, clock, BOX_A, MODEL, FW_B, prefix="changed")
    assert profiles.status(BOX_A).reason == "cloud_evidence_pending"
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1, "enrollments": {}}

    restarted = learner(clock, state_path=path)
    box_proof(restarted, clock, BOX_A, MODEL, FW_B, prefix="restarted")
    assert restarted.archive_basis(BOX_A) is None


def test_known_historical_firmware_conflict_fences_cold_archive(tmp_path):
    """A known different firmware witnesses a change since the archived command."""
    clock = Clock()
    profiles = learner(clock, state_path=tmp_path / "control_ver_profile.json")
    box_proof(profiles, clock, BOX_A, MODEL, FW_B)
    model, epoch = profiles.archive_basis(BOX_A)
    assert not profiles.can_adopt_archive_profile(BOX_A, model, epoch, True, firmware=FW_A)
    assert profiles.can_adopt_archive_profile(BOX_A, model, epoch, True, firmware=None)
