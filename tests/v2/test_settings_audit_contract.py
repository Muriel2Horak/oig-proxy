# pylint: disable=missing-module-docstring,missing-function-docstring,invalid-name
from __future__ import annotations

import importlib


def _clear_audit_tracking_state(settings_audit: object) -> None:
    for attr_name in ("_audit_raw_bytes", "_audit_last_seen"):
        attr = getattr(settings_audit, attr_name, None)
        if isinstance(attr, dict):
            attr.clear()


def test_settings_audit_contract_canonical_record_serializes_deterministically() -> None:
    """Canonical audit record serializes deterministically with all required fields."""
    settings_audit = importlib.import_module("telemetry.settings_audit")
    make_incoming_record = settings_audit.make_incoming_record
    record_to_dict = settings_audit.record_to_dict
    SettingStep = settings_audit.SettingStep
    SettingResult = settings_audit.SettingResult

    record = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE",
        raw_text="<Frame><NewValue>1</NewValue></Frame>",
        value=1,
        session_id="sess_001",
        msg_id=12345,
        id_set=67890,
    )

    d = record_to_dict(record)

    # Core identity and lifecycle fields
    assert d["audit_id"].startswith("aud_")
    assert d["step"] == SettingStep.INCOMING.value
    assert d["result"] == SettingResult.PENDING.value
    assert d["raw_text"] == "<Frame><NewValue>1</NewValue></Frame>"
    assert d["raw_text_truncated"] is False

    # Stable device/table fields
    assert d["device_id"] == "dev_001"
    assert d["table"] == "tbl_box_prms"
    assert d["key"] == "MODE"

    # Correlation fields
    assert d["session_id"] == "sess_001"
    assert d["msg_id"] == 12345
    assert d["id_set"] == 67890

    # Value fields
    assert d["value_text"] == "1"
    assert d["value_kind"] == "int"
    assert d["confirmed_value_text"] == ""
    assert d["confirmed_value_kind"] == ""
    assert d["value_num_float"] == 1.0
    assert d["confirmed_value_num_float"] is None

    # Timestamp is present and stable ISO-8601 UTC format ending in Z
    assert isinstance(d["timestamp"], str)
    assert d["timestamp"].endswith("Z")

    # All expected keys present and no extras
    expected_keys = {
        "timestamp",
        "device_id",
        "table",
        "step",
        "result",
        "audit_id",
        "key",
        "session_id",
        "msg_id",
        "id_set",
        "value_text",
        "confirmed_value_text",
        "value_kind",
        "confirmed_value_kind",
        "value_num_float",
        "confirmed_value_num_float",
        "raw_text",
        "raw_text_truncated",
        "raw_text_bytes_original",
        "audit_payload_capped",
    }
    assert set(d.keys()) == expected_keys


def test_raw_text_truncation_oversized_text_truncated_safely() -> None:
    """Oversized raw text is truncated safely at the 16 KiB boundary."""
    settings_audit = importlib.import_module("telemetry.settings_audit")
    truncate_raw_text = settings_audit.truncate_raw_text
    _MAX_RAW_TEXT_BYTES = settings_audit._MAX_RAW_TEXT_BYTES
    make_incoming_record = settings_audit.make_incoming_record

    # Exact boundary: should NOT be truncated
    exact_boundary = "x" * _MAX_RAW_TEXT_BYTES
    truncated_exact, info_exact = truncate_raw_text(exact_boundary)
    assert info_exact.was_truncated is False
    assert info_exact.original_bytes == _MAX_RAW_TEXT_BYTES
    assert len(truncated_exact.encode("utf-8")) == _MAX_RAW_TEXT_BYTES

    # One byte over boundary: SHOULD be truncated
    one_over = "x" * (_MAX_RAW_TEXT_BYTES + 1)
    truncated_over, info_over = truncate_raw_text(one_over)
    assert info_over.was_truncated is True
    assert info_over.original_bytes == _MAX_RAW_TEXT_BYTES + 1
    assert len(truncated_over.encode("utf-8")) <= _MAX_RAW_TEXT_BYTES

    # Significantly oversized text
    oversized = "x" * (_MAX_RAW_TEXT_BYTES + 500)
    truncated, info = truncate_raw_text(oversized)

    # Original byte count is preserved
    assert info.original_bytes == len(oversized.encode("utf-8"))
    assert info.was_truncated is True

    # Stored raw text stays within 16384 bytes
    assert len(truncated.encode("utf-8")) <= _MAX_RAW_TEXT_BYTES

    # Test via make_incoming_record integration
    record = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE",
        raw_text=oversized,
        value=1,
    )
    assert record.raw_text_truncated is True
    assert record.raw_text_bytes_original == len(oversized.encode("utf-8"))
    assert len(record.raw_text.encode("utf-8")) <= _MAX_RAW_TEXT_BYTES


def test_settings_audit_superseded_same_key_overwrite_terminates_earlier_audit() -> None:
    """Same-key overwrite terminates the earlier audit deterministically."""
    settings_audit = importlib.import_module("telemetry.settings_audit")
    twin_state = importlib.import_module("twin.state")

    make_incoming_record = settings_audit.make_incoming_record
    make_superseded_record = settings_audit.make_superseded_record
    SettingStep = settings_audit.SettingStep
    SettingResult = settings_audit.SettingResult
    TwinQueue = twin_state.TwinQueue

    queue = TwinQueue()

    # Enqueue first setting
    queue.enqueue("tbl_box_prms", "MODE", 1)
    first = queue.get("tbl_box_prms", "MODE")
    assert first is not None
    audit_id_1 = first.audit_id

    # Enqueue second setting (same key – overwrites)
    queue.enqueue("tbl_box_prms", "MODE", 2)
    second = queue.get("tbl_box_prms", "MODE")
    assert second is not None
    audit_id_2 = second.audit_id

    # Only one live queue slot remains
    assert queue.size() == 1

    # Second setting keeps a different audit_id
    assert audit_id_2 != audit_id_1

    # First pending setting ends as superseded via contract helper
    record1 = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE",
        raw_text="first",
        value=1,
    )
    # Bind the record to the original queue audit_id so the contract is deterministic
    record1.audit_id = audit_id_1

    superseded = make_superseded_record(record1)
    assert superseded.step == SettingStep.SUPERSEDED
    assert superseded.result == SettingResult.SUPERSEDED
    assert superseded.audit_id == audit_id_1
    assert superseded.device_id == "dev_001"
    assert superseded.table == "tbl_box_prms"
    assert superseded.key == "MODE"

    # Verify a fresh incoming record for the same key gets yet another audit_id
    record2 = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE",
        raw_text="second",
        value=2,
    )
    assert record2.audit_id != audit_id_1
    assert record2.audit_id != audit_id_2


def test_make_incoming_record_sets_value_num_float_for_int_values() -> None:
    settings_audit = importlib.import_module("telemetry.settings_audit")
    make_incoming_record = settings_audit.make_incoming_record

    record = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE",
        raw_text="<Frame><NewValue>1</NewValue></Frame>",
        value=1,
    )

    assert record.value_kind == "int"
    assert record.value_num_float == 1.0


def test_make_incoming_record_sets_value_num_float_for_float_values() -> None:
    settings_audit = importlib.import_module("telemetry.settings_audit")
    make_incoming_record = settings_audit.make_incoming_record

    record = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="TARGET_TEMP",
        raw_text="<Frame><NewValue>21.5</NewValue></Frame>",
        value=21.5,
    )

    assert record.value_kind == "float"
    assert record.value_num_float == 21.5


def test_make_incoming_record_keeps_value_num_float_none_for_string_values() -> None:
    settings_audit = importlib.import_module("telemetry.settings_audit")
    make_incoming_record = settings_audit.make_incoming_record

    record = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE_LABEL",
        raw_text="<Frame><NewValue>eco</NewValue></Frame>",
        value="eco",
    )

    assert record.value_kind == "string"
    assert record.value_num_float is None


def test_step_records_cap_raw_text_after_audit_id_exceeds_aggregate_limit() -> None:
    settings_audit = importlib.import_module("telemetry.settings_audit")
    make_incoming_record = settings_audit.make_incoming_record
    make_step_record = settings_audit.make_step_record
    SettingStep = settings_audit.SettingStep
    _MAX_RAW_TEXT_BYTES = settings_audit._MAX_RAW_TEXT_BYTES

    _clear_audit_tracking_state(settings_audit)

    payload = "x" * _MAX_RAW_TEXT_BYTES
    record = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE",
        raw_text=payload,
        value=1,
    )
    assert record.audit_payload_capped is False

    first = make_step_record(record, SettingStep.ENQUEUED, raw_text=payload)
    second = make_step_record(record, SettingStep.DELIVER_SELECTED, raw_text=payload)
    third = make_step_record(record, SettingStep.INJECTED_BOX, raw_text=payload)
    capped = make_step_record(record, SettingStep.ACK_BOX_OBSERVED, raw_text=payload)

    assert first.audit_payload_capped is False
    assert second.audit_payload_capped is False
    assert third.audit_payload_capped is False
    assert capped.audit_payload_capped is True
    assert capped.raw_text_bytes_original == len(payload.encode("utf-8"))
    assert capped.raw_text_truncated is True
    assert capped.raw_text == ""


def test_aggregate_raw_text_cap_is_tracked_independently_per_audit_id() -> None:
    settings_audit = importlib.import_module("telemetry.settings_audit")
    make_incoming_record = settings_audit.make_incoming_record
    make_step_record = settings_audit.make_step_record
    SettingStep = settings_audit.SettingStep
    _MAX_RAW_TEXT_BYTES = settings_audit._MAX_RAW_TEXT_BYTES

    _clear_audit_tracking_state(settings_audit)

    payload = "y" * _MAX_RAW_TEXT_BYTES
    first_audit = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="MODE",
        raw_text=payload,
        value=1,
    )
    second_audit = make_incoming_record(
        device_id="dev_002",
        table="tbl_box_prms",
        key="MODE",
        raw_text=payload,
        value=2,
    )

    for step in (
        SettingStep.ENQUEUED,
        SettingStep.DELIVER_SELECTED,
        SettingStep.INJECTED_BOX,
    ):
        make_step_record(first_audit, step, raw_text=payload)

    capped_first = make_step_record(first_audit, SettingStep.ACK_BOX_OBSERVED, raw_text=payload)
    uncapped_second = make_step_record(second_audit, SettingStep.ENQUEUED, raw_text=payload)

    assert capped_first.audit_payload_capped is True
    assert capped_first.raw_text == ""
    assert uncapped_second.audit_payload_capped is False
    assert uncapped_second.raw_text == payload


def test_make_step_record_redacts_raw_text_for_sensitive_keys() -> None:
    settings_audit = importlib.import_module("telemetry.settings_audit")
    make_incoming_record = settings_audit.make_incoming_record
    make_step_record = settings_audit.make_step_record
    SettingStep = settings_audit.SettingStep

    _clear_audit_tracking_state(settings_audit)

    record = make_incoming_record(
        device_id="dev_001",
        table="tbl_box_prms",
        key="API_TOKEN",
        raw_text="<Frame><NewValue>initial-secret</NewValue></Frame>",
        value="initial-secret",
    )

    step = make_step_record(
        record,
        SettingStep.ACK_REASON_SETTING,
        raw_text="<Frame><NewValue>rotated-secret</NewValue></Frame>",
        confirmed_value="rotated-secret",
    )

    assert step.raw_text == "[REDACTED]"
    assert step.raw_text_truncated is False
    assert step.raw_text_bytes_original == len("[REDACTED]".encode("utf-8"))
    assert step.confirmed_value_text == "[REDACTED]"
