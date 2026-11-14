# pylint: disable=missing-module-docstring,missing-function-docstring,too-few-public-methods
from datetime import datetime, timedelta
import os
import sys
import time

import pytest

# pyright: reportMissingImports=false

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "addon", "oig-proxy")))

from unittest.mock import MagicMock  # pylint: disable=wrong-import-position

from protocol.ver_profile import ProfileStatus, VerProfile, native_frame_fields  # pylint: disable=wrong-import-position
from protocol.frame import build_frame  # pylint: disable=wrong-import-position
from protocol.frames import build_setting_frame  # pylint: disable=wrong-import-position
from telemetry.settings_audit import SettingResult, SettingStep, record_to_dict  # pylint: disable=wrong-import-position
from twin.delivery import TwinDelivery  # pylint: disable=wrong-import-position
from twin.state import TwinQueue  # pylint: disable=wrong-import-position


class _MQTTStub:
    pass


def _make_collector():
    collector = MagicMock()
    collector.settings_audit = []
    collector.record_setting_audit_step = lambda r: collector.settings_audit.append(record_to_dict(r))
    return collector


def _qualified_delivery(queue, device_id="12345", **options):
    delivery = TwinDelivery(queue, _MQTTStub(), **options)
    status = ProfileStatus(VerProfile(39, 0x1940), "ready", 2, include_tto=False)
    delivery._profiles.status = lambda device: (  # pylint: disable=protected-access
        status if device == device_id else ProfileStatus(None, "waiting_for_box", 0)
    )
    delivery._refresh_profile_status(device_id)  # pylint: disable=protected-access
    delivery.observe_msg_id(13_800_000, device_id)
    return delivery


def _bind(delivery, setting, session_id=None, **options):
    frame = delivery.build_local_frame(setting, session_id)
    delivery.bind_local_wire(setting, frame, session_id, **options)
    return frame


def _cloud_mode_frame(value, msg_id, id_set):
    return build_setting_frame(
        device_id="dev_1", table="tbl_box_prms", key="MODE", value=value,
        id_set=id_set, msg_id=msg_id, profile=VerProfile(42, 0xABCD),
    ).decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("tbl_batt_prms", "BAT_MIN", 22, {}, True), id="battery"),
        pytest.param(("tbl_invertor_prm1", "AAC_MAX_CHRG", 100, {}, False), id="disabled-ac"),
        pytest.param(("tbl_invertor_prm1", "AAC_MAX_CHRG", 100, {"ac_charge_max_a": 125}, True), id="qualified-ac"),
        pytest.param(("tbl_invertor_prm1", "AAC_MAX_CHRG", 100, {"ac_charge_max_a": 80}, False), id="ac-over-limit"),
        pytest.param(("tbl_invertor_prm1", "P_MAX_FEED_GRID", 5400, {"grid_export_max_w": 5400}, True),
                     id="qualified-grid"),
        pytest.param(("tbl_invertor_prm1", "P_MAX_FEED_GRID", 5400, {"grid_export_max_w": 5000}, False),
                     id="grid-over-limit"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_R", 90, {}, False), id="phase-disabled"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_R", 90, {"phase_calibration_enabled": True}, True),
                     id="phase-r-wire"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_S", 90, {"phase_calibration_enabled": True}, True),
                     id="phase-s-wire"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_T", 90, {"phase_calibration_enabled": True}, True),
                     id="phase-t-wire"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_R", 85, {"phase_calibration_enabled": True}, False),
                     id="phase-r-unlisted"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_S", 75, {"phase_calibration_enabled": True}, False),
                     id="phase-s-unqualified"),
    ],
)
async def test_deliver_pending_returns_twin_settings(case) -> None:
    table, key, value, limit_options, should_send = case
    queue = TwinQueue()
    options = {}
    if limit_options:
        from settings_constraints import PowerControlLimits

        options["power_limits"] = PowerControlLimits(**limit_options)
    delivery = _qualified_delivery(queue, **options)
    queue.enqueue(table, key, value, device_id="12345")
    if should_send:
        queue.enqueue("tbl_box_prms", "SA", 1, device_id="12345")

    pending = await delivery.deliver_pending("12345", "sender")

    if not should_send:
        assert pending == []
        assert delivery.local_inflight_setting() is None
        assert queue.size() == 0
        return

    selected, = pending
    assert (selected.table, selected.key, selected.value) == (table, key, value)
    wire = native_frame_fields(_bind(delivery, selected, "sender"))
    assert wire is not None
    assert (wire["ID_Device"], wire["TblName"], wire["TblItem"], wire["NewValue"]) == (
        "12345", table, key, str(value),
    )


@pytest.mark.asyncio
async def test_deliver_pending_blocks_until_ack() -> None:
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    queue.enqueue("tbl_batt_prms", "BAT_MIN", 22, device_id="12345")
    queue.enqueue("tbl_box_prms", "SA", 1, device_id="12345")

    first = await delivery.deliver_pending("12345")
    second = await delivery.deliver_pending("12345")

    assert len(first) == 1
    assert second == []

    frame = _bind(delivery, first[0])
    assert b"<TTO_P>" not in frame and b"<TTO_E>" not in frame
    assert delivery.acknowledge("tbl_batt_prms", "BAT_MIN")
    third = await delivery.deliver_pending("12345")
    assert len(third) == 1
    assert third[0].key == "SA"


@pytest.mark.asyncio
async def test_deliver_pending_drops_after_inflight_timeout() -> None:
    queue = TwinQueue()
    delivery = _qualified_delivery(queue, inflight_timeout_s=0.0)
    queue.enqueue("tbl_box_prms", "SA", 1, device_id="12345")

    first = await delivery.deliver_pending("12345")
    assert len(first) == 1
    assert first[0].key == "SA"
    _bind(delivery, first[0])

    second = await delivery.deliver_pending("12345")
    assert second == []
    assert queue.size() == 0


def test_build_setting_frame_format() -> None:
    frame = build_setting_frame(
        device_id="12345",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        id_set=844979473,
        msg_id=12345678,
        profile=VerProfile(44, 0x1940),
    )
    frame_str = frame.decode("utf-8")
    assert "<ID>12345678</ID>" in frame_str
    assert "<ID_Device>12345</ID_Device>" in frame_str
    assert "<ID_Set>844979473</ID_Set>" in frame_str
    assert "<ID_SubD>0</ID_SubD>" in frame_str
    assert "<NewValue>1</NewValue>" in frame_str
    assert "<Confirm>New</Confirm>" in frame_str
    assert "<TblName>tbl_box_prms</TblName>" in frame_str
    assert "<TblItem>MODE</TblItem>" in frame_str
    assert "<ID_Server>9</ID_Server>" in frame_str
    assert "<mytimediff>0</mytimediff>" in frame_str
    assert "<Reason>Setting</Reason>" in frame_str
    assert "<ver>" in frame_str and "</ver>" in frame_str
    assert "<TSec>" in frame_str and "</TSec>" in frame_str
    assert "<DT>" in frame_str and "</DT>" in frame_str
    assert "<CRC>" in frame_str and "</CRC>" in frame_str
    assert frame_str.endswith("\r\n")


@pytest.mark.asyncio
async def test_acknowledge_removes_setting_from_queue() -> None:
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    queue.enqueue("tbl_batt_prms", "BAT_MIN", 22, device_id="12345")

    selected = await delivery.deliver_pending("12345")
    _bind(delivery, selected[0])
    assert delivery.acknowledge("tbl_batt_prms", "BAT_MIN")

    assert queue.size() == 0


def test_next_id_set_starts_at_epoch_range() -> None:
    queue = TwinQueue()
    delivery = TwinDelivery(queue, _MQTTStub())

    next_id = delivery.next_id_set()

    assert next_id >= 1_700_000_000


def test_next_id_set_clamps_when_observed_is_older() -> None:
    queue = TwinQueue()
    delivery = TwinDelivery(queue, _MQTTStub())
    delivery.observe_id_set(845_125_850)

    next_id = delivery.next_id_set()

    assert next_id >= 1_700_000_000


def test_observed_msg_id_increments_monotonic() -> None:
    queue = TwinQueue()
    delivery = TwinDelivery(queue, _MQTTStub())

    delivery.observe_msg_id(13_800_000)
    first = delivery.next_msg_id()
    second = delivery.next_msg_id()

    assert first == 13_800_001
    assert second == 13_800_002


@pytest.mark.asyncio
async def test_deliver_pending_records_selected_step() -> None:
    queue = TwinQueue()
    collector = _make_collector()
    delivery = _qualified_delivery(queue, "dev_1", telemetry_collector=collector)
    queue.enqueue(
        "tbl_batt_prms", "BAT_MIN", 22, device_id="dev_1",
        raw_text="<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>",
    )

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")

    assert len(pending) == 1
    assert len(collector.settings_audit) == 1
    record = collector.settings_audit[0]
    assert record["step"] == SettingStep.DELIVER_SELECTED.value
    assert record["result"] == SettingResult.PENDING.value
    assert record["audit_id"] == pending[0].audit_id
    assert record["session_id"] == "sess_1"
    assert record["raw_text"] == "<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>"


@pytest.mark.asyncio
async def test_timeout_records_timeout_step() -> None:
    queue = TwinQueue()
    collector = _make_collector()
    delivery = _qualified_delivery(queue, "dev_1", inflight_timeout_s=0.0, telemetry_collector=collector)
    queue.enqueue(
        "tbl_batt_prms", "BAT_MIN", 22, device_id="dev_1",
        raw_text="<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>",
    )

    selected = await delivery.deliver_pending("dev_1", session_id="sess_1")
    _bind(delivery, selected[0], "sess_1")
    assert await delivery.deliver_pending("dev_1", session_id="sess_1") == []

    assert len(collector.settings_audit) == 2
    timeout_record = collector.settings_audit[1]
    assert timeout_record["step"] == SettingStep.TIMEOUT.value
    assert timeout_record["result"] == SettingResult.FAILED.value
    assert timeout_record["raw_text"] == "<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>"


@pytest.mark.asyncio
async def test_clear_session_records_session_cleared() -> None:
    queue = TwinQueue()
    collector = _make_collector()
    delivery = _qualified_delivery(queue, "dev_1", telemetry_collector=collector)
    queue.enqueue(
        "tbl_batt_prms", "BAT_MIN", 22, device_id="dev_1",
        raw_text="<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>",
    )

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    delivery.clear_session("sess_1")

    assert len(collector.settings_audit) == 2
    session_record = collector.settings_audit[1]
    assert session_record["step"] == SettingStep.SESSION_CLEARED.value
    assert session_record["result"] == SettingResult.INCOMPLETE.value
    assert session_record["audit_id"] == pending[0].audit_id
    assert session_record["raw_text"] == "<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>"


@pytest.mark.asyncio
async def test_shutdown_records_session_cleared_for_global_inflight() -> None:
    queue = TwinQueue()
    collector = _make_collector()
    delivery = _qualified_delivery(queue, "dev_1", telemetry_collector=collector)
    queue.enqueue(
        "tbl_batt_prms", "BAT_MIN", 22, device_id="dev_1",
        raw_text="<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>",
    )

    pending = await delivery.deliver_pending("dev_1")
    delivery.shutdown()

    assert len(collector.settings_audit) == 2
    session_record = collector.settings_audit[1]
    assert session_record["step"] == SettingStep.SESSION_CLEARED.value
    assert session_record["result"] == SettingResult.INCOMPLETE.value
    assert session_record["audit_id"] == pending[0].audit_id
    assert session_record["raw_text"] == "<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>"


def test_terminal_deduplication_prevents_duplicate_success() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    collector = _make_collector()
    delivery = TwinDelivery(queue, _MQTTStub(), telemetry_collector=collector)

    setting = queue.get("tbl_set", "T_Room")
    assert setting is not None

    delivery.record_ack_box_observed(setting, "dev_1")
    delivery.record_ack_box_observed(setting, "dev_1")

    assert len(collector.settings_audit) == 2
    assert collector.settings_audit[0]["step"] == SettingStep.ACK_BOX_OBSERVED.value
    assert collector.settings_audit[1]["step"] == SettingStep.ACK_BOX_OBSERVED.value

    delivery.record_ack_tbl_events(setting, "dev_1", confirmed_value="23")
    delivery.record_ack_tbl_events(setting, "dev_1", confirmed_value="23")

    assert len(collector.settings_audit) == 3
    assert collector.settings_audit[2]["step"] == SettingStep.ACK_TBL_EVENTS.value
    assert collector.settings_audit[2]["result"] == SettingResult.CONFIRMED.value


def test_begin_cloud_setting_records_incoming_and_exposes_inflight() -> None:
    collector = _make_collector()
    delivery = TwinDelivery(TwinQueue(), _MQTTStub(), telemetry_collector=collector)
    raw_text = (
        "<Frame><TblName>tbl_box_prms</TblName><ID_Device>dev_1</ID_Device>"
        "<ID>12345678</ID><ID_Set>844979473</ID_Set><TblItem>MODE</TblItem>"
        "<NewValue>1</NewValue><Confirm>New</Confirm><Reason>Setting</Reason></Frame>"
    )

    delivery.begin_cloud_setting(
        device_id="dev_1",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        raw_text=raw_text,
        msg_id=12345678,
        id_set=844979473,
    )

    assert delivery.is_cloud_inflight() is True
    inflight = delivery.inflight_setting()
    assert inflight is not None
    setting, device_id = inflight
    assert device_id == "dev_1"
    assert setting.table == "tbl_box_prms"
    assert setting.key == "MODE"
    assert setting.value == 1
    assert setting.msg_id == 12345678
    assert setting.id_set == 844979473
    assert setting.raw_text == raw_text
    assert setting.audit_id

    assert len(collector.settings_audit) == 1
    record = collector.settings_audit[0]
    assert record["step"] == SettingStep.INCOMING.value
    assert record["result"] == SettingResult.PENDING.value
    assert record["audit_id"] == setting.audit_id
    assert record["msg_id"] == 12345678
    assert record["id_set"] == 844979473
    assert record["raw_text"] == raw_text


def test_cloud_reason_setting_stays_provisional_until_tbl_events() -> None:
    collector = _make_collector()
    delivery = TwinDelivery(TwinQueue(), _MQTTStub(), telemetry_collector=collector)

    delivery.begin_cloud_setting(
        device_id="dev_1",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        raw_text=_cloud_mode_frame(1, 12345678, 844979473),
        msg_id=12345678,
        id_set=844979473,
        session_id="sess_1",
    )

    pending = delivery.mark_cloud_reason_setting("dev_1", session_id="sess_1")
    assert pending is not None
    assert delivery.is_cloud_inflight() is True

    delivery.match_cloud_tbl_events(
        "dev_1",
        "tbl_box_prms",
        "MODE",
        confirmed_value="1",
        session_id="sess_1",
        source_dt="2026-11-12 20:00:01",
    )

    assert [record["step"] for record in collector.settings_audit] == [
        SettingStep.INCOMING.value,
        SettingStep.ACK_REASON_SETTING.value,
        SettingStep.ACK_TBL_EVENTS.value,
    ]
    assert collector.settings_audit[1]["result"] == SettingResult.PENDING.value
    assert collector.settings_audit[2]["result"] == SettingResult.CONFIRMED.value
    assert delivery.is_cloud_inflight() is False


def test_cloud_tbl_events_matches_correct_pending_value() -> None:
    collector = _make_collector()
    delivery = TwinDelivery(TwinQueue(), _MQTTStub(), telemetry_collector=collector)

    delivery.begin_cloud_setting(
        device_id="dev_1",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        raw_text=_cloud_mode_frame(1, 111, 1001),
        msg_id=111,
        id_set=1001,
        session_id="sess_1",
    )
    assert delivery.mark_cloud_reason_setting("dev_1", "sess_1") is not None

    delivery.begin_cloud_setting(
        device_id="dev_1",
        table="tbl_box_prms",
        key="MODE",
        value=0,
        raw_text=_cloud_mode_frame(0, 222, 1002),
        msg_id=222,
        id_set=1002,
        session_id="sess_2",
    )
    assert delivery.mark_cloud_reason_setting("dev_1", "sess_2") is not None

    matched = delivery.match_cloud_tbl_events(
        "dev_1",
        "tbl_box_prms",
        "MODE",
        confirmed_value="0",
        session_id="sess_2",
        source_dt="2026-11-12 20:00:03",
    )
    assert matched is not None
    setting, _ = matched
    assert setting.value == 0

    confirmed = [
        record for record in collector.settings_audit if record["step"] == SettingStep.ACK_TBL_EVENTS.value
    ]
    assert len(confirmed) == 1
    assert confirmed[0]["id_set"] == 1002


def test_cloud_receipt_timeout_does_not_claim_setting_application(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = _make_collector()
    delivery = TwinDelivery(
        TwinQueue(),
        _MQTTStub(),
        inflight_timeout_s=1.0,
        telemetry_collector=collector,
    )

    delivery.begin_cloud_setting(
        device_id="dev_1",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        raw_text=_cloud_mode_frame(1, 111, 1001),
        msg_id=111,
        id_set=1001,
        session_id="sess_1",
    )
    assert delivery.mark_cloud_reason_setting("dev_1", "sess_1") is not None

    original_monotonic = time.monotonic
    monkeypatch.setattr("twin.delivery.time.monotonic", lambda: original_monotonic() + 2.0)

    assert delivery.has_pending_or_inflight() is False

    reason_steps = [
        record for record in collector.settings_audit if record["step"] == SettingStep.ACK_REASON_SETTING.value
    ]
    assert [record["result"] for record in reason_steps] == [SettingResult.PENDING.value]
    assert collector.settings_audit[-1]["step"] == SettingStep.TIMEOUT.value
    assert collector.settings_audit[-1]["result"] == SettingResult.INCOMPLETE.value


@pytest.mark.asyncio
async def test_session_timeout_records_timeout_step() -> None:
    queue = TwinQueue()
    collector = _make_collector()
    delivery = _qualified_delivery(queue, "dev_1", inflight_timeout_s=0.0,
                                   telemetry_collector=collector)
    queue.enqueue("tbl_batt_prms", "BAT_MIN", 22, device_id="dev_1")

    selected = await delivery.deliver_pending("dev_1", session_id="sess_1")
    _bind(delivery, selected[0], "sess_1")
    assert await delivery.deliver_pending("dev_1", session_id="sess_1") == []

    assert len(collector.settings_audit) == 2
    timeout_record = collector.settings_audit[1]
    assert timeout_record["step"] == SettingStep.TIMEOUT.value
    assert timeout_record["result"] == SettingResult.FAILED.value


def test_queue_keeps_same_setting_for_distinct_devices() -> None:
    queue = TwinQueue()
    queue.set_profile_epoch(3, device_id="10001")
    queue.set_profile_epoch(7, device_id="20002")

    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="10001")
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id="20002")

    first = queue.get("tbl_box_prms", "MODE", device_id="10001")
    second = queue.get("tbl_box_prms", "MODE", device_id="20002")
    assert first is not None and (first.value, first.profile_epoch) == (1, 3)
    assert second is not None and (second.value, second.profile_epoch) == (0, 7)
    assert queue.get_pending(device_id="20002") == [second]


def test_outbound_msg_counter_survives_restart_without_capture(tmp_path) -> None:
    state_path = tmp_path / "setting-counters.json"
    delivery = TwinDelivery(TwinQueue(), _MQTTStub(), counter_state_path=state_path)
    delivery.observe_msg_id(13_800_000, "10001")
    assert delivery.next_msg_id("10001") == 13_800_001

    restored = TwinDelivery(TwinQueue(), _MQTTStub(), counter_state_path=state_path)
    assert restored.next_msg_id("10001") == 13_800_002
    with pytest.raises(ValueError, match="observed message counter"):
        restored.next_msg_id("20002")


@pytest.mark.asyncio
async def test_profile_rotation_retires_only_its_device_pending_requests() -> None:
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(queue, on_result=outcomes.append)
    original_status = delivery._profiles.status  # pylint: disable=protected-access
    queue.set_profile_epoch(8, device_id="20002")
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="12345", audit_id="first")
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id="20002", audit_id="second")
    selected = await delivery.deliver_pending("12345", "session")
    assert len(selected) == 1
    delivery._profiles.status = lambda device: (  # pylint: disable=protected-access
        ProfileStatus(VerProfile(43, 0x1910), "ready", 3) if device == "12345" else original_status(device)
    )

    with pytest.raises(ValueError, match="Profile changed"):
        delivery.build_local_frame(selected[0], "session")
    assert [event["audit_id"] for event in outcomes] == ["first"]
    assert outcomes[0]["reason"] == "profile_epoch_changed"
    assert queue.get("tbl_box_prms", "MODE", device_id="20002").value == 0


def test_cloud_counter_requires_exact_device_and_crc(tmp_path) -> None:
    state_path = tmp_path / "setting-counters.json"
    delivery = TwinDelivery(TwinQueue(), _MQTTStub(), counter_state_path=state_path)
    frame = build_setting_frame(
        device_id="10001", table="tbl_box_prms", key="MODE", value=1,
        id_set=1_700_000_000, msg_id=13_800_000, profile=VerProfile(42, 0xABCD),
    ).decode()
    delivery.begin_cloud_setting(
        "20002", "tbl_box_prms", "MODE", 1, frame,
        id_set=1_700_000_000, msg_id=13_800_000,
    )
    delivery.begin_cloud_setting(
        "10001", "tbl_box_prms", "MODE", 1, frame.replace("<NewValue>1", "<NewValue>2"),
        id_set=1_700_000_000, msg_id=13_800_000,
    )
    assert not state_path.exists()
    delivery.begin_cloud_setting(
        "10001", "tbl_box_prms", "MODE", 1, frame,
        id_set=1_700_000_000, msg_id=13_800_000,
    )
    restored = TwinDelivery(TwinQueue(), _MQTTStub(), counter_state_path=state_path)
    assert restored.next_msg_id("10001") == 13_800_001
    with pytest.raises(ValueError, match="observed message counter"):
        restored.next_msg_id("20002")


@pytest.mark.asyncio
async def test_local_application_event_requires_the_sending_session():
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="12345")
    selected = await delivery.deliver_pending("12345", "sender")
    raw = _bind(delivery, selected[0], "sender")
    fields = native_frame_fields(raw)
    assert fields is not None
    setting_time = datetime.strptime(fields["DT"], "%d.%m.%Y %H:%M:%S")
    event_time = (setting_time + timedelta(seconds=1)).isoformat()

    assert not delivery.acknowledge_local_event(
        "12345", "tbl_box_prms", "MODE", 1, event_time,
        event_id_set=10, session_id="other-session",
    )
    assert delivery.local_inflight_setting() is not None
    assert delivery.acknowledge_local_event(
        "12345", "tbl_box_prms", "MODE", 1, event_time,
        event_id_set=10, session_id="sender",
    )


@pytest.mark.asyncio
async def test_wire_reply_checks_explicit_echoes_and_reuses_no_session():
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="12345")
    selected = await delivery.deliver_pending("12345", "sender")
    fields = native_frame_fields(_bind(delivery, selected[0], "sender"))
    assert fields is not None
    ack = "<Result>ACK</Result><Reason>Setting</Reason>"
    wrong = build_frame(ack + f'<ID>{int(fields["ID"]) + 1}</ID>').encode()
    assert delivery.local_protocol_reply(wrong, "sender") is None
    wrong = build_frame(ack + f'<ID_Set>{int(fields["ID_Set"]) + 1}</ID_Set>').encode()
    assert delivery.local_protocol_reply(wrong, "sender") is None
    exact = build_frame(ack + f'<ID>{fields["ID"]}</ID><ID_Set>{fields["ID_Set"]}</ID_Set>').encode()
    assert delivery.local_protocol_reply(exact, "sender") == {"result": "ACK", "reason": "Setting"}
    assert delivery.acknowledge("tbl_box_prms", "MODE", "sender")

    queue.enqueue("tbl_box_prms", "MODE", 0, device_id="12345")
    assert await delivery.deliver_pending("12345", "sender") == []
    assert delivery.local_protocol_reply(build_frame(ack).encode(), "sender") is None
    assert len(await delivery.deliver_pending("12345", "new-sender")) == 1


@pytest.mark.asyncio
async def test_event_confirmed_offline_write_accepts_one_matching_wire_receipt():
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(queue, on_result=outcomes.append)
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="12345")
    selected = await delivery.deliver_pending("12345", "sender")
    fields = native_frame_fields(_bind(delivery, selected[0], "sender", retain_receipt=True))
    assert fields is not None
    assert delivery.acknowledge_local_event(
        "12345", "tbl_box_prms", "MODE", 1, fields["DT"],
        event_id_set=10, session_id="sender",
    )
    ack = build_frame("<Result>ACK</Result><Reason>Setting</Reason>").encode()
    assert delivery.local_wire_deadline("sender") is not None
    assert delivery.local_protocol_reply(ack, "wrong-sender") is None
    assert delivery.local_protocol_reply(ack, "sender") == {
        "result": "ACK", "reason": "Setting", "event_confirmed": "true",
    }
    assert delivery.local_protocol_reply(ack, "sender") is None
    assert len(outcomes) == 1
    assert outcomes[0]["evidence"] == "setting_event"


def test_transparent_cloud_reply_requires_consistent_explicit_evidence():
    outcomes = []
    delivery = TwinDelivery(TwinQueue(), _MQTTStub(), on_result=outcomes.append)
    delivery.begin_cloud_setting(
        "dev_1", "tbl_box_prms", "MODE", 1, _cloud_mode_frame(1, 111, 1001),
        msg_id=111, id_set=1001, session_id="sender",
    )
    assert delivery.mark_cloud_reason_setting("dev_1", "sender", reply_fields={"ID": "112"}) is None
    assert delivery.mark_cloud_reason_setting("dev_1", "sender", reply_fields={"ID_Set": "1002"}) is None
    assert not delivery.acknowledge_cloud("dev_1", session_id="sender", reply_fields={"ToDo": "SA"})
    assert not outcomes
    assert delivery.mark_cloud_reason_setting(
        "dev_1", "sender", reply_fields={"ID": "111", "ID_Set": "1001", "TblName": "tbl_box_prms"},
    ) is not None
    assert len(outcomes) == 1
    assert outcomes[0]["evidence"] == "protocol_ack"


@pytest.mark.asyncio
async def test_disabled_delivery_never_selects_a_ready_request():
    queue = TwinQueue()
    delivery = _qualified_delivery(queue, box_writes_enabled=False)
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="12345")

    assert await delivery.deliver_pending("12345", "sender") == []
    assert delivery.local_inflight_setting() is None


@pytest.mark.asyncio
async def test_wire_is_unavailable_when_counter_reservation_cannot_persist(tmp_path, monkeypatch):
    queue = TwinQueue()
    state_path = tmp_path / "setting-counters.json"
    delivery = _qualified_delivery(queue, counter_state_path=state_path)
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="12345")
    selected = await delivery.deliver_pending("12345", "sender")

    def unavailable(_source, _target):
        raise OSError("simulated read-only counter directory")

    with monkeypatch.context() as failure:
        failure.setattr("twin.delivery.os.replace", unavailable)
        with pytest.raises(ValueError, match="persist outbound Setting counters"):
            delivery.build_local_frame(selected[0], "sender")

    assert delivery.local_wire_deadline("sender") is None
    restored = TwinDelivery(TwinQueue(), _MQTTStub(), counter_state_path=state_path)
    assert restored.next_msg_id("12345") == 13_800_001


def test_corrupt_cloud_setting_cannot_receive_a_transport_result():
    outcomes = []
    delivery = TwinDelivery(TwinQueue(), _MQTTStub(), on_result=outcomes.append)
    raw = _cloud_mode_frame(1, 111, 1001).replace("<NewValue>1", "<NewValue>2")
    delivery.begin_cloud_setting(
        "dev_1", "tbl_box_prms", "MODE", 2, raw,
        msg_id=111, id_set=1001, session_id="sender",
    )
    assert delivery.mark_cloud_reason_setting("dev_1", "sender") is None
    assert not outcomes
