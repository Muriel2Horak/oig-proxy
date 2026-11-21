"""Fault summary keeps live observations distinct from diagnostic records."""
# pylint: disable=missing-function-docstring

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sensor.fault_summary import FaultSummary
from sensor.loader import SensorMapLoader

RECEIVED = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
SOURCE = "21.11.2026 11:00:00"
MASK_TABLE = "tbl_invertor_prms"
CODE_TABLE = "tbl_invertor_prm2"
ZERO_MASKS = dict.fromkeys(("ERR_AC", "ERR_BATT", "ERR_ELSE", "ERR_GRID", "ERR_PV"), 0)


@pytest.fixture
def stock_definitions():
    """Use the shipped dictionary for wire-mask regressions."""
    addon = Path(__file__).parents[2] / "addon" / "oig-proxy"
    loader = SensorMapLoader(str(addon / "sensor_map.json"))
    loader.load()
    return {key: loader.lookup(MASK_TABLE, key)["warnings_3f"] for key in ZERO_MASKS}


def test_pv_input_bits_leave_the_summary_clear(stock_definitions):
    summary = FaultSummary()
    assert summary.observe(
        MASK_TABLE, {**ZERO_MASKS, "ERR_PV": 12}, received_at=RECEIVED,
        source_at=SOURCE, warning_definitions=stock_definitions,
    )
    result = summary.snapshot()
    assert result["summary"] == "Bez aktivních varování"
    assert result["active_faults"] == result["recent_records"] == result["unverified_signs"] == []
    assert result["has_live_masks"]


def test_sparse_masks_keep_other_active_warnings_and_clear_only_the_named_field(stock_definitions):
    summary = FaultSummary()
    summary.observe(MASK_TABLE, {"ERR_PV": 2, "ERR_ELSE": 4}, received_at=RECEIVED,
                    source_at=SOURCE, warning_definitions=stock_definitions, source_table="tbl_actual")
    assert [(item["field"], item["warning_code"]) for item in summary.snapshot()["active_faults"]] == [
        ("ERR_ELSE", 11), ("ERR_PV", 17),
    ]
    summary.observe(MASK_TABLE, {"ERR_PV": 0}, received_at=RECEIVED + timedelta(seconds=5),
                    source_at=SOURCE, warning_definitions=stock_definitions)
    result = summary.snapshot()
    assert [item["label"] for item in result["active_faults"]] == ["Přetížení"]
    historic = next(item for item in result["recent_records"] if item["field"] == "ERR_PV")
    assert historic["source_table"] == "tbl_actual"
    assert historic["kind"] == "warning_mask"
    assert historic["at"] == "2026-11-21T11:00:00"
    assert historic["time_basis"] == "box"
    assert historic["source_at"] == SOURCE


def test_unknown_mask_is_unverified_and_keeps_raw_evidence(stock_definitions):
    summary = FaultSummary()
    summary.observe(MASK_TABLE, {"ERR_PV": 28}, received_at=RECEIVED, source_at=SOURCE,
                    warning_definitions=stock_definitions)
    result = summary.snapshot()
    assert result["active_faults"] == []
    assert result["summary"] == "Neověřené příznaky střídače"
    sign = result["unverified_signs"][0]
    assert sign["kind"] == "unknown_mask"
    assert sign["value"] == 16
    assert sign["field"] == "ERR_PV"


def test_custom_index_masks_and_partial_snapshots(stock_definitions):
    summary = FaultSummary()
    definitions = {"ERR_PV": [{"bit": 8, "key": "moon", "remark_cs": "Vlastní hlášení"}]}
    summary.observe(MASK_TABLE, {"ERR_PV": 256}, received_at=RECEIVED,
                    source_at=SOURCE, warning_definitions=definitions)
    assert [item["label"] for item in summary.snapshot()["active_faults"]] == ["Vlastní hlášení"]
    assert summary.snapshot()["unverified_signs"] == []
    summary.observe(MASK_TABLE, {"ERR_PV": 0}, received_at=RECEIVED, source_at=SOURCE,
                    warning_definitions=stock_definitions)
    assert summary.snapshot()["summary"] == (
        "Bez varování v přijatých údajích · dřívější hlášení: Vlastní hlášení"
    )


@pytest.mark.parametrize("value", [False, -1, "?", 1.25, None])
def test_malformed_mask_never_supplies_live_evidence(value, stock_definitions):
    summary = FaultSummary()
    assert not summary.observe(MASK_TABLE, {"ERR_PV": value}, received_at=RECEIVED,
                               source_at=SOURCE, warning_definitions=stock_definitions)
    assert not summary.snapshot()["has_live_masks"]


@pytest.mark.parametrize(("value", "label"), [
    (18, "Neznámý kód 18"),
    (23, "Přetížení"),
    (32, "Porucha stejnosměrné části"),
    (34, "Porucha napětí nebo zapojení střídače"),
    (35, "Porucha napětí nebo zapojení střídače"),
    (250, "Neznámý kód 250"),
])
def test_standalone_code_is_historical_even_when_recognized(value, label):
    summary = FaultSummary()
    assert summary.observe(CODE_TABLE, {"CODE_ERR": value}, received_at=RECEIVED, source_at=SOURCE)
    result = summary.snapshot()
    assert result["active_faults"] == []
    assert not result["has_live_masks"]
    assert result["last_code"]["label"] == label
    assert result["last_code"]["value"] == value
    assert result["last_code"]["source_table"] == CODE_TABLE
    assert "neověřeno" in result["summary"]
    assert not summary.observe(CODE_TABLE, {"CODE_ERR": value}, received_at=RECEIVED + timedelta(seconds=1),
                               source_at="21.11.2026 11:00:01")
    assert not summary.observe(CODE_TABLE, {"CODE_ERR": 0}, received_at=RECEIVED, source_at=SOURCE)
    assert summary.snapshot()["last_code"] == result["last_code"]
    assert summary.observe(CODE_TABLE, {"CODE_ERR": value}, received_at=RECEIVED, source_at=SOURCE,
                           source_table="tbl_actual")
    assert summary.snapshot()["last_code"]["source_table"] == "tbl_actual"


def test_event_uses_reception_time_after_clock_reset_and_is_never_active():
    summary = FaultSummary()
    content = "Power on phase 1 exceeded the limit (6372W) 0x"
    assert summary.observe("tbl_events", {"Type": "Warning", "Content": content},
                           received_at=RECEIVED, source_at="01.01.1970 00:00:19")
    result = summary.snapshot()
    assert result["active_faults"] == []
    assert not result["has_live_masks"]
    event = result["recent_records"][0]
    assert event["label"] == "Přetížení na fázi L1 (6372 W)"
    assert event["value"] == content
    assert event["event_type"] == "Warning"
    assert event["time_basis"] == "proxy"
    assert event["at"] == "2026-11-21T10:00:00+00:00"
    assert event["source_at"] == "01.01.1970 00:00:19"


@pytest.mark.parametrize("source_at", ["2026-11-21", "9999-12-31T23:59:59-01:00"])
def test_event_with_unusable_timestamp_keeps_raw_time_and_falls_back_to_proxy(source_at):
    summary = FaultSummary()
    summary.observe("tbl_events", {"Type": "Error", "Content": "Unparsed diagnostic"},
                    received_at=RECEIVED, source_at=source_at)
    event = summary.snapshot()["recent_records"][0]
    assert event["source_at"] == source_at
    assert event["time_basis"] == "proxy"
    assert event["at"] == event["received_at"]


def test_dated_event_replay_is_ignored_even_after_a_different_event():
    summary = FaultSummary()
    first = {"Type": "Warning", "Content": "Power on phase 1 exceeded the limit (6372W) 0x"}
    second = {"Type": "Alarm", "Content": "New raw alarm 42"}
    assert summary.observe("tbl_events", first, received_at=RECEIVED, source_at=SOURCE)
    assert summary.observe("tbl_events", second, received_at=RECEIVED + timedelta(seconds=5),
                           source_at="21.11.2026 11:00:05")
    assert not summary.observe("tbl_events", first, received_at=RECEIVED + timedelta(seconds=10),
                               source_at="2026-11-21T11:00:00")
    assert [record["label"] for record in summary.snapshot()["recent_records"]] == [
        "Alarm boxu: New raw alarm 42", "Přetížení na fázi L1 (6372 W)",
    ]


@pytest.mark.parametrize("event_type", ["Factory", "Setting", "Invertor ACK", "Change"])
def test_unrelated_events_do_not_create_records(event_type):
    summary = FaultSummary()
    assert not summary.observe("tbl_events", {"Type": event_type, "Content": "Invertor ACK : ^S005EDB0"},
                               received_at=RECEIVED, source_at=SOURCE)
    assert summary.snapshot()["recent_records"] == []


def test_unavailable_and_restore_keep_records_without_reviving_live_state(stock_definitions):
    summary = FaultSummary()
    summary.observe(MASK_TABLE, {"ERR_ELSE": 4}, received_at=RECEIVED, source_at=SOURCE,
                    warning_definitions=stock_definitions)
    summary.observe(CODE_TABLE, {"CODE_ERR": 23}, received_at=RECEIVED, source_at=SOURCE)
    persisted = summary.persisted()
    restored = FaultSummary()
    restored.restore(persisted["recent_records"], persisted["last_code"])
    assert restored.snapshot()["active_faults"] == []
    assert not restored.snapshot()["has_live_masks"]
    assert restored.persisted() == persisted
    assert summary.mark_unavailable()
    assert not summary.mark_unavailable()
    assert summary.snapshot() == restored.snapshot()


def test_current_warnings_and_recent_history_are_not_truncated_together(stock_definitions):
    summary = FaultSummary()
    summary.observe(MASK_TABLE, {"ERR_ELSE": 4}, received_at=RECEIVED, source_at=SOURCE,
                    warning_definitions=stock_definitions)
    for code in range(30, 42):
        summary.observe(CODE_TABLE, {"CODE_ERR": code}, received_at=RECEIVED, source_at=SOURCE)
    result = summary.snapshot()
    assert [item["value"] for item in result["recent_records"]] == list(range(41, 31, -1))
    assert result["active_faults"][0]["label"] == "Přetížení"


def test_summary_text_fits_home_assistant_and_original_event_is_retained():
    summary = FaultSummary()
    event = {"Type": "Fault", "Content": "Dlouhé původní hlášení. " * 40}
    assert summary.observe("tbl_events", event, received_at=RECEIVED, source_at="invalid time")
    result = summary.snapshot()
    assert len(result["summary"]) <= 255
    assert result["recent_records"][0]["value"] == event["Content"]
    assert result["recent_records"][0]["time_basis"] == "proxy"
