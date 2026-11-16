# pylint: disable=missing-module-docstring,missing-function-docstring,too-few-public-methods,too-many-lines
from datetime import datetime, timedelta
import os
import sys
import time
from types import SimpleNamespace

import pytest

# pyright: reportMissingImports=false

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "addon", "oig-proxy")))

from unittest.mock import MagicMock  # pylint: disable=wrong-import-position

from protocol.ver_profile import ProfileStatus, VerProfile, native_frame_fields  # pylint: disable=wrong-import-position
from protocol.frame import build_frame  # pylint: disable=wrong-import-position
from protocol.frames import build_setting_frame, czech_local_datetime_from_epoch  # pylint: disable=wrong-import-position
from settings_constraints import PowerControlLimits  # pylint: disable=wrong-import-position
from telemetry.settings_audit import SettingResult, SettingStep, record_to_dict  # pylint: disable=wrong-import-position
from twin import delivery as delivery_module  # pylint: disable=wrong-import-position
from twin.delivery import TwinDelivery  # pylint: disable=wrong-import-position
from twin.state import TwinQueue  # pylint: disable=wrong-import-position
from tests.v2.test_integration import MODEL, TEST_BOX, _qualified_transport, _send_poll  # pylint: disable=wrong-import-position
from tests.v2.test_wire_profile_qualification import native  # pylint: disable=wrong-import-position


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


def _setting_ack_at(stamp=None):
    date = "" if stamp is None else f"<Rdt>{stamp}</Rdt>"
    return build_frame(f"<Result>ACK</Result><Reason>Setting</Reason>{date}").encode()


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
        pytest.param(("tbl_invertor_prm1", "P_CAL_R", 99, {"phase_calibration_enabled": True}, True),
                     id="phase-r-above-ninety"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_S", 75, {"phase_calibration_enabled": True}, True),
                     id="phase-s-restore"),
        pytest.param(("tbl_invertor_prm1", "P_CAL_T", 75, {"phase_calibration_enabled": True}, True),
                     id="phase-t-restore"),
    ],
)
async def test_deliver_pending_returns_twin_settings(case) -> None:
    table, key, value, limit_options, should_send = case
    queue = TwinQueue()
    options = {}
    if limit_options:
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
async def test_selected_unsent_intent_can_be_delivered_after_reconnect() -> None:
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
    assert session_record["result"] == SettingResult.PENDING.value
    assert session_record["audit_id"] == pending[0].audit_id
    assert session_record["raw_text"] == "<Frame><TblItem>BAT_MIN</TblItem><NewValue>22</NewValue></Frame>"
    recovered, = await delivery.deliver_pending("dev_1", session_id="sess_2")
    assert recovered.audit_id == pending[0].audit_id
    assert recovered.enqueued_at == pending[0].enqueued_at


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


async def _start_native_mode_write(  # pylint: disable=too-many-arguments
    monkeypatch, baseline_lag, *, baseline_age=0.25, refresh_counter=False,
    baseline_session="sender", prior_span=3.0, prior_clock_skew=0,
):
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(queue, on_result=outcomes.append)
    sender_epoch = int(time.time()) - 10
    clock = SimpleNamespace(now=100.0 - baseline_age)
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(
        time=lambda: sender_epoch + 0.55, monotonic=lambda: clock.now,
    ))
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: sender_epoch - 2)
    if prior_span is not None:
        clock.now -= prior_span
        source_epoch = sender_epoch - baseline_lag - int(baseline_age + prior_span) + prior_clock_skew
        delivery.observe_box_frame(
            TEST_BOX, 9, czech_local_datetime_from_epoch(source_epoch).isoformat(), session_id=baseline_session,
        )
        clock.now += prior_span
    delivery.observe_box_frame(
        TEST_BOX, 10,
        czech_local_datetime_from_epoch(sender_epoch - baseline_lag - int(baseline_age)).isoformat(),
        session_id=baseline_session,
    )
    clock.now = 100.0
    if refresh_counter:
        delivery.observe_box_frame(
            TEST_BOX, 10, czech_local_datetime_from_epoch(sender_epoch - baseline_lag).isoformat(),
            session_id=baseline_session,
        )
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id=TEST_BOX)
    first, = await delivery.deliver_pending(TEST_BOX, "sender", online_poll=b"poll-1")
    _bind(delivery, first, "sender")
    delivery.record_injected_box(first, TEST_BOX, "sender")
    return delivery, outcomes, clock, sender_epoch


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline_lag,receipt_delay,baseline_age", [(6, 1, 0.25), (4, 1, 0.25), (6, 30, 10.0)])
async def test_native_transition_confirms_before_wire_dt_and_cancels_replay(
    monkeypatch, baseline_lag, receipt_delay, baseline_age,
):
    delivery, outcomes, clock, sender_epoch = await _start_native_mode_write(
        monkeypatch, baseline_lag, baseline_age=baseline_age,
    )
    clock.now += receipt_delay
    delivery.poll_timeouts()
    assert delivery.has_pending(TEST_BOX) == (receipt_delay == 30)

    event_time = czech_local_datetime_from_epoch(sender_epoch - 5).isoformat()
    assert delivery.acknowledge_local_event(
        TEST_BOX, "tbl_box_prms", "MODE", 0, event_time,
        event_id_set=11, session_id="sender", previous_value="3",
    )
    assert [event["evidence"] for event in outcomes] == ["setting_event"]
    assert not delivery.acknowledge_local_event(
        TEST_BOX, "tbl_box_prms", "MODE", 0, event_time,
        event_id_set=11, session_id="sender", previous_value="3",
    )
    assert await delivery.deliver_pending(TEST_BOX, "sender", online_poll=b"poll-2") == []
    assert not delivery.has_pending(TEST_BOX)


@pytest.mark.asyncio
@pytest.mark.parametrize("event_lag", [1, 3])
async def test_local_noop_still_confirms_within_existing_timestamp_bounds(monkeypatch, event_lag):
    delivery, outcomes, clock, sender_epoch = await _start_native_mode_write(monkeypatch, 6)
    clock.now += 1
    event_time = czech_local_datetime_from_epoch(sender_epoch - event_lag).isoformat()
    assert delivery.acknowledge_local_event(
        TEST_BOX, "tbl_box_prms", "MODE", 0, event_time,
        event_id_set=11, session_id="sender", previous_value="0.0",
    )
    assert [event["evidence"] for event in outcomes] == ["setting_event"]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_lag,counter,session,previous_value", [
    pytest.param(15, 11, "sender", "3", id="historical-event"),
    pytest.param(5, 10, "sender", "3", id="old-early-counter"),
    pytest.param(5, 11, "other", "3", id="other-socket"),
    pytest.param(5, 11, "sender", "0.0", id="same-value"),
    pytest.param(5, 11, "sender", "", id="missing-old-value"),
])
async def test_local_event_rejects_evidence_that_cannot_confirm_the_write(
    monkeypatch, event_lag, counter, session, previous_value,
):
    delivery, outcomes, clock, sender_epoch = await _start_native_mode_write(monkeypatch, 6)
    clock.now += 1
    event_time = czech_local_datetime_from_epoch(sender_epoch - event_lag).isoformat()
    assert not delivery.acknowledge_local_event(
        TEST_BOX, "tbl_box_prms", "MODE", 0, event_time,
        event_id_set=counter, session_id=session, previous_value=previous_value,
    )
    assert delivery.local_inflight_setting() is not None
    assert outcomes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline_lag,event_lag,options", [
    pytest.param(29, 28, {}, id="recently-received-buffered-source"),
    pytest.param(5, 5, {"prior_span": None}, id="single-buffered-observation"),
    pytest.param(5, 5, {"prior_span": 0.25}, id="buffered-burst"),
    pytest.param(5, 5, {"prior_clock_skew": -3}, id="inconsistent-clock-rate"),
    pytest.param(6, 5, {"prior_clock_skew": -2}, id="historical-earlier-sample"),
    pytest.param(6, 5, {"baseline_age": 31.0, "refresh_counter": True}, id="repeated-old-counter"),
    pytest.param(6, 5, {"baseline_session": "other"}, id="unrelated-anchor"),
])
async def test_early_transition_requires_recent_native_anchor(monkeypatch, baseline_lag, event_lag, options):
    delivery, outcomes, clock, sender_epoch = await _start_native_mode_write(monkeypatch, baseline_lag, **options)
    clock.now += 1
    event_time = czech_local_datetime_from_epoch(sender_epoch - event_lag).isoformat()
    assert not delivery.acknowledge_local_event(
        TEST_BOX, "tbl_box_prms", "MODE", 0, event_time,
        event_id_set=11, session_id="sender", previous_value="3",
    )
    assert delivery.local_inflight_setting() is not None
    assert outcomes == []


@pytest.mark.asyncio
async def test_dated_setting_event_can_follow_an_unrelated_native_observation(monkeypatch):
    delivery, outcomes, clock, sender_epoch = await _start_native_mode_write(monkeypatch, 6)
    clock.now += 1
    stamp = czech_local_datetime_from_epoch(sender_epoch).isoformat()
    delivery.observe_box_frame(TEST_BOX, 11, stamp, session_id="other")
    assert delivery.acknowledge_local_event(
        TEST_BOX, "tbl_box_prms", "MODE", 0, stamp,
        event_id_set=12, session_id="sender", previous_value="3",
    )
    assert [event["evidence"] for event in outcomes] == ["setting_event"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidation", ["counter-reset", "clock-change-before-bind", "clock-change", "cloud-write"])
async def test_local_transition_rejects_a_changed_evidence_epoch(monkeypatch, invalidation):
    if invalidation == "clock-change-before-bind":
        monkeypatch.setattr(delivery_module.VerProfiles, "box_clock_epoch",
                            lambda _profiles, _device: int(delivery_module.time.monotonic() >= 100.0))
    delivery, outcomes, clock, sender_epoch = await _start_native_mode_write(monkeypatch, 6)
    clock.now += 1
    event_time = czech_local_datetime_from_epoch(sender_epoch - 5).isoformat()
    if invalidation == "counter-reset":
        delivery.observe_box_frame(TEST_BOX, 9, event_time, session_id="sender")
    elif invalidation == "clock-change":
        monkeypatch.setattr(delivery._profiles, "box_clock_epoch", lambda _device: 1)  # pylint: disable=protected-access
    elif invalidation == "cloud-write":
        cloud = build_setting_frame(
            device_id=TEST_BOX, table="tbl_box_prms", key="MODE", value=3,
            msg_id=13_800_010, id_set=sender_epoch, profile=VerProfile(39, 0x1940),
        )
        delivery.begin_cloud_setting(TEST_BOX, "tbl_box_prms", "MODE", 3, cloud.decode(), session_id="other")
    assert not delivery.acknowledge_local_event(
        TEST_BOX, "tbl_box_prms", "MODE", 0, event_time,
        event_id_set=11, session_id="sender", previous_value="3",
    )
    assert delivery.local_inflight_setting() is not None
    assert outcomes == []


async def _send_timing_anchors(proxy, writer, clock, sender_epoch, *, coalesced):
    baselines = []
    for elapsed, counter in ((-3, 1234099), (0, 1234100)):
        clock.now = sender_epoch + elapsed
        baseline = native(
            f"<TblName>tbl_actual</TblName><ID_Device>{TEST_BOX}</ID_Device>"
            f"<ID_Set>{counter}</ID_Set><DT>{clock.stamp(offset=-6)}</DT><Reason>Table</Reason>", MODEL,
        )
        if coalesced:
            baselines.append(baseline)
        else:
            await _send_poll(proxy, writer, poll=baseline)
    if coalesced:
        last_stamp = clock.stamp(offset=-6)

        async def process_frame(fields):
            if fields.get("_dt") == last_stamp:
                clock.now = sender_epoch

        clock.now = sender_epoch - 3
        proxy.on_frame = process_frame
        await _send_poll(proxy, writer, poll=b"".join(baselines))
        proxy.on_frame = None


@pytest.mark.asyncio
@pytest.mark.parametrize("coalesced", [False, True], ids=["independent-reads", "coalesced-with-callback-delay"])
async def test_signed_box_transition_uses_independent_timing_samples(
    make_config, dummy_writer_factory, monkeypatch, coalesced,
):
    proxy, delivery, queue, clock = _qualified_transport(make_config())
    sender_epoch = int(time.time())
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(
        time=lambda: clock.now + 0.55, monotonic=lambda: clock.now,
    ))
    monkeypatch.setattr("proxy.server.time", SimpleNamespace(time=time.time, monotonic=lambda: clock.now))
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: sender_epoch - 2)
    writer = dummy_writer_factory()
    await _send_timing_anchors(proxy, writer, clock, sender_epoch, coalesced=coalesced)
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id=TEST_BOX)
    await _send_poll(proxy, writer)
    assert len(writer.written) == 1
    event = native(
        f"<TblName>tbl_events</TblName><ID_Device>{TEST_BOX}</ID_Device>"
        f"<ID_Set>1234101</ID_Set><DT>{clock.stamp(offset=-5)}</DT><Reason>Table</Reason><Type>Setting</Type>"
        "<Content>Remotely : tbl_box_prms / MODE: [3]->[0]</Content>", MODEL,
    )
    await _send_poll(proxy, writer, poll=event.replace(b"[3]", b"[2]"))
    assert delivery.local_inflight_setting() is not None
    await _send_poll(proxy, writer, poll=event)
    assert (delivery.local_inflight_setting() is not None) == coalesced
    assert queue.size() == int(coalesced)


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
async def test_dated_wire_ack_opens_later_online_poll_for_next_queued_parameter(monkeypatch):
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(
        queue, on_result=outcomes.append, power_limits=PowerControlLimits(ac_charge_max_a=125),
    )
    wire_epoch = int(time.time()) - 30
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch)
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id="12345")
    queue.enqueue("tbl_invertor_prm1", "AAC_MAX_CHRG", 75, device_id="12345")

    first, = await delivery.deliver_pending("12345", "sender", online_poll=b"poll-1")
    first_fields = native_frame_fields(_bind(delivery, first, "sender"))
    assert first_fields is not None and first.key == "MODE"
    stamp = datetime.strptime(first_fields["DT"], "%d.%m.%Y %H:%M:%S")
    ack = "<Result>ACK</Result><Reason>Setting</Reason>"
    first_reply = build_frame(ack + f"<Rdt>{stamp.isoformat()}</Rdt>").encode()

    assert await delivery.deliver_pending("12345", "sender", online_poll=b"poll-2") == []
    assert delivery.local_protocol_reply(first_reply, "sender") is not None
    assert delivery.acknowledge("tbl_box_prms", "MODE", "sender", receipt_dt=stamp.isoformat())
    assert [request.key for request in queue.get_pending()] == ["AAC_MAX_CHRG"]
    assert await delivery.deliver_pending("12345", "sender", online_poll=b"poll-2") == []

    wire_epoch += 10
    assert await delivery.deliver_pending("12345", "sender", online_poll=b"poll-1") == []
    second, = await delivery.deliver_pending("12345", "sender", online_poll=b"poll-2")
    second_fields = native_frame_fields(_bind(delivery, second, "sender"))
    assert second_fields is not None and second.key == "AAC_MAX_CHRG"
    assert second_fields["ID_Set"] != first_fields["ID_Set"]
    assert delivery.local_protocol_reply(first_reply, "sender") is None
    assert delivery.local_protocol_reply(build_frame(ack).encode(), "sender") is None
    assert delivery.local_protocol_reply(
        build_frame(ack + f'<ID>{first_fields["ID"]}</ID>').encode(), "sender",
    ) is None
    second_stamp = stamp + timedelta(seconds=10)
    second_reply = build_frame(ack + f"<Rdt>{second_stamp.isoformat()}</Rdt>").encode()
    assert delivery.local_protocol_reply(second_reply, "another-sender") is None
    assert delivery.local_protocol_reply(second_reply, "sender") is not None
    assert delivery.acknowledge(
        "tbl_invertor_prm1", "AAC_MAX_CHRG", "sender", receipt_dt=second_stamp.isoformat(),
    )
    assert [result["key"] for result in outcomes] == ["MODE", "AAC_MAX_CHRG"]
    assert queue.size() == 0


@pytest.mark.asyncio
async def test_aac_retries_one_bound_nack_with_new_wire_ids():
    queue = TwinQueue()
    outcomes = []
    collector = _make_collector()
    delivery = _qualified_delivery(
        queue, on_result=outcomes.append, telemetry_collector=collector,
        power_limits=PowerControlLimits(ac_charge_max_a=125),
    )
    queue.enqueue("tbl_invertor_prm1", "AAC_MAX_CHRG", 75, device_id="12345")
    first, = await delivery.deliver_pending("12345", "first", online_poll=b"poll-1")
    first_wire = native_frame_fields(_bind(delivery, first, "first"))
    assert first_wire is not None
    nack = build_frame("<Result>NACK</Result><Reason>Setting</Reason>").encode()
    assert delivery.local_protocol_reply(nack, "first") == {"result": "NACK", "reason": "Setting"}
    assert delivery.acknowledge("tbl_invertor_prm1", "AAC_MAX_CHRG", "first", result="nack")
    assert not outcomes
    assert await delivery.deliver_pending("12345", "first", online_poll=b"poll-2") == []

    second, = await delivery.deliver_pending("12345", "second", online_poll=b"poll-3")
    assert second.audit_id == first.audit_id and second.enqueued_at == first.enqueued_at
    second_wire = native_frame_fields(_bind(delivery, second, "second"))
    assert second_wire is not None
    assert second_wire["ID"] != first_wire["ID"] and second_wire["ID_Set"] != first_wire["ID_Set"]
    assert delivery.acknowledge("tbl_invertor_prm1", "AAC_MAX_CHRG", "second", result="nack")
    assert [outcome["result"] for outcome in outcomes] == ["nack"]
    assert [item["result"] for item in collector.settings_audit if item["step"] == SettingStep.NACK.value] == [
        SettingResult.PENDING.value, SettingResult.FAILED.value,
    ]
    assert queue.size() == 0


@pytest.mark.asyncio
async def test_aac_nack_does_not_restore_a_value_superseded_by_cloud():
    queue = TwinQueue()
    delivery = _qualified_delivery(queue, power_limits=PowerControlLimits(ac_charge_max_a=125))
    table, key, device_id = "tbl_invertor_prm1", "AAC_MAX_CHRG", "12345"
    queue.enqueue(table, key, 75, device_id=device_id)
    first, = await delivery.deliver_pending(device_id, "local", online_poll=b"poll")
    _bind(delivery, first, "local")
    cloud = build_setting_frame(device_id=device_id, table=table, key=key, value=100,
                                id_set=int(time.time()), msg_id=13_800_010, profile=VerProfile(39, 0x1940))
    delivery.begin_cloud_setting(device_id, table, key, 100, cloud.decode(), session_id="cloud")
    assert delivery.acknowledge(table, key, "local", result="nack")
    assert queue.size() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter, terminal, echo", [
    ("MODE", "ack", None),
    ("MODE", "ack", "ID"),
    ("MODE", "nack", "ID"),
    ("MODE", "nack", "ID_Set"),
    ("AAC_MAX_CHRG", "timeout", None),
])
async def test_local_timeout_replays_once_on_same_session(monkeypatch, parameter, terminal, echo):  # pylint: disable=too-many-locals
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(
        queue, on_result=outcomes.append, power_limits=PowerControlLimits(ac_charge_max_a=125),
    )
    wire_epoch = int(time.time()) - 40
    timer = time.monotonic()
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(time=time.time, monotonic=lambda: timer))
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch)
    table, value = ("tbl_box_prms", 3) if parameter == "MODE" else ("tbl_invertor_prm1", 75)
    queue.enqueue(table, parameter, value, device_id="12345")
    first, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-1")
    first_wire = native_frame_fields(_bind(delivery, first, "open"))
    assert first_wire is not None
    delivery.record_injected_box(first, "12345", "open")

    timer += 29
    assert await delivery.deliver_pending("12345", "open", online_poll=b"poll-2") == []
    timer += 1
    delivery.poll_timeouts()
    assert not outcomes and delivery.has_pending("12345")
    nack_body = "<Result>NACK</Result><Reason>Setting</Reason>"
    original_nack = build_frame(nack_body + f'<ID>{first_wire["ID"]}</ID>').encode()
    assert delivery.local_protocol_reply(original_nack, "open") is None
    assert delivery.has_pending("12345")
    assert await delivery.deliver_pending("12345", "open", online_poll=b"poll-1") == []

    wire_epoch += 30
    retry, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-3")
    assert retry.audit_id == first.audit_id
    retry_wire = native_frame_fields(_bind(delivery, retry, "open"))
    assert retry_wire is not None and retry_wire["ID_Set"] != first_wire["ID_Set"]
    delivery.record_injected_box(retry, "12345", "open")
    assert delivery.local_protocol_reply(_setting_ack_at(czech_local_datetime_from_epoch(wire_epoch - 30)), "open") is None
    assert delivery.local_protocol_reply(_setting_ack_at(), "open") is None
    stamp = czech_local_datetime_from_epoch(wire_epoch + 1).isoformat()
    delayed_nack = build_frame(nack_body + f"<Rdt>{stamp}</Rdt>").encode()
    assert delivery.local_protocol_reply(delayed_nack, "open") is None
    assert delivery.local_protocol_reply(original_nack, "open") is None
    assert not outcomes and delivery.local_inflight_setting(session_id="open") is not None
    if terminal == "timeout":
        timer += 30
        delivery.poll_timeouts()
    else:
        echoed_id = f"<{echo}>{retry_wire[echo]}</{echo}>" if echo else ""
        rdt = "" if terminal == "nack" else f"<Rdt>{stamp}</Rdt>"
        body = f"<Result>{terminal.upper()}</Result><Reason>Setting</Reason>{rdt}{echoed_id}"
        reply = delivery.local_protocol_reply(build_frame(body).encode(), "open")
        assert reply is not None and reply["result"] == terminal.upper()
        assert delivery.acknowledge(table, parameter, "open", result=terminal, receipt_dt=reply.get("rdt"))
    assert [event["result"] for event in outcomes] == [terminal]
    assert await delivery.deliver_pending("12345", "open", online_poll=b"poll-4") == []
    assert await delivery.deliver_pending("12345", "new", online_poll=b"poll-5") == []


def _assert_post_replay_receipt_fence(delivery, replay_ack, prior_wires, following, wire_epoch):
    current_frame = _bind(delivery, following, "open")
    delivery.record_injected_box(following, "12345", "open")
    ambiguous_dates = (
        czech_local_datetime_from_epoch(wire_epoch - 1),
        czech_local_datetime_from_epoch(wire_epoch),
        czech_local_datetime_from_epoch(wire_epoch + 2) + timedelta(milliseconds=900),
    )
    for stale in (replay_ack, _setting_ack_at(), *(_setting_ack_at(date) for date in ambiguous_dates)):
        assert delivery.local_protocol_reply(stale, "open") is None
    stamp = czech_local_datetime_from_epoch(wire_epoch + 3)
    assert delivery.local_protocol_reply(_setting_ack_at(stamp), "open") is None
    delayed_nack = build_frame(f"<Result>NACK</Result><Reason>Setting</Reason><Rdt>{stamp}</Rdt>").encode()
    assert delivery.local_protocol_reply(delayed_nack, "open") is None
    for prior in prior_wires:
        stale_echo = build_frame(
            f'<Result>ACK</Result><Reason>Setting</Reason><Rdt>{stamp}</Rdt><ID>{prior["ID"]}</ID>',
        ).encode()
        assert delivery.local_protocol_reply(stale_echo, "open") is None
    _assert_bound_post_replay_receipts(delivery, current_frame, ambiguous_dates[-1], stamp)
    return current_frame


def _assert_bound_post_replay_receipts(delivery, current_frame, same_second, fresh_stamp):
    current_wire = native_frame_fields(current_frame)
    assert current_wire is not None
    exact_echo = build_frame(
        f'<Result>ACK</Result><Reason>Setting</Reason><ID_Set>{current_wire["ID_Set"]}</ID_Set>'
        f"<Rdt>{same_second}</Rdt>",
    ).encode()
    echoed_reply = delivery.local_protocol_reply(exact_echo, "open")
    assert echoed_reply is not None and echoed_reply["wire_echo"] == "true"
    named_ack = build_frame(
        f"<Result>ACK</Result><TblName>tbl_batt_prms</TblName><ToDo>BAT_MIN</ToDo><Rdt>{fresh_stamp}</Rdt>",
    ).encode()
    named_reply = delivery.local_protocol_reply(named_ack, "open")
    assert named_reply is not None and named_reply["todo"] == "BAT_MIN"


async def _bind_second_replay(delivery, following, prior_wire_epoch):
    delivery.poll_timeouts()
    prewire_ack = _setting_ack_at(czech_local_datetime_from_epoch(prior_wire_epoch - 1))
    assert delivery.local_protocol_reply(prewire_ack, "open") is None
    candidates = await delivery.deliver_pending("12345", "open", online_poll=b"poll-4")
    assert [candidate.audit_id for candidate in candidates] == [following.audit_id]
    frame = _bind(delivery, candidates[0], "open")
    delivery.record_injected_box(candidates[0], "12345", "open")
    return frame


def _finish_unrelated_setting(delivery, frame, fresh_stamp, *, use_echo):
    assert delivery.local_protocol_reply(_setting_ack_at(fresh_stamp), "open") is None
    outbound = native_frame_fields(frame)
    assert outbound is not None
    receipt = (
        f'<Reason>Setting</Reason><ID_Set>{outbound["ID_Set"]}</ID_Set>' if use_echo else
        f'<TblName>tbl_batt_prms</TblName><ToDo>BAT_MIN</ToDo><Rdt>{fresh_stamp}</Rdt>'
    )
    reply_bytes = build_frame(f"<Result>ACK</Result>{receipt}").encode()
    assert delivery.local_protocol_reply(reply_bytes, "other") is None
    reply = delivery.local_protocol_reply(reply_bytes, "open")
    assert reply is not None and reply["result"] == "ACK"
    assert delivery.acknowledge(
        "tbl_batt_prms", "BAT_MIN", "open", receipt_dt=reply.get("rdt"),
        receipt_echoed=reply.get("wire_echo") == "true",
    )


async def _assert_old_named_target_still_fenced(delivery, queue, wire_epoch):
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id="12345")
    new_mode, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-5")
    _bind(delivery, new_mode, "open")
    delivery.record_injected_box(new_mode, "12345", "open")
    late_stamp = czech_local_datetime_from_epoch(wire_epoch + 3)
    delayed_named = build_frame(
        f"<Result>ACK</Result><TblName>tbl_box_prms</TblName><ToDo>MODE</ToDo><Rdt>{late_stamp}</Rdt>",
    ).encode()
    assert delivery.local_protocol_reply(delayed_named, "open") is None
    assert delivery.inflight() == ("tbl_box_prms", "MODE")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "echo, replay_following, box_offset", [(None, False, 0), ("ID", False, 120.5), ("ID_Set", True, 0)],
)
async def test_acknowledged_replay_reopens_flow_for_unrelated_setting(  # pylint: disable=too-many-locals
    monkeypatch, echo, replay_following, box_offset,
):
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(queue, on_result=outcomes.append)
    wire_epoch = int(time.time()) - 90
    timer = time.monotonic()
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(
        time=lambda: wire_epoch - box_offset + 2.25, monotonic=lambda: timer,
    ))
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch)
    monkeypatch.setattr(delivery._profiles, "box_clock_offset", lambda _device: box_offset)  # pylint: disable=protected-access

    queue.enqueue("tbl_box_prms", "MODE", 3, device_id="12345")
    first, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-1")
    first_wire = native_frame_fields(_bind(delivery, first, "open"))
    assert first_wire is not None
    delivery.record_injected_box(first, "12345", "open")
    timer += 30
    wire_epoch += 30

    replay, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-2")
    replay_wire = native_frame_fields(_bind(delivery, replay, "open"))
    assert replay_wire is not None and replay.audit_id == first.audit_id
    delivery.record_injected_box(replay, "12345", "open")
    replay_stamp = czech_local_datetime_from_epoch(wire_epoch + 3)
    ack = "<Result>ACK</Result><Reason>Setting</Reason>"
    if echo is None:
        replay_ack = _setting_ack_at(replay_stamp)
    else:
        replay_ack = build_frame(ack + f"<{echo}>{replay_wire[echo]}</{echo}>").encode()
    reply = delivery.local_protocol_reply(replay_ack, "open")
    assert reply is not None
    echoed = reply.get("wire_echo") == "true"
    assert echoed is (echo is not None)
    assert delivery.acknowledge(
        "tbl_box_prms", "MODE", "open", receipt_dt=reply.get("rdt"), receipt_echoed=echoed,
    )

    queue.enqueue("tbl_batt_prms", "BAT_MIN", 25, device_id="12345")
    wire_epoch += 10
    assert await delivery.deliver_pending("12345", "open", online_poll=b"poll-2") == []
    candidates = await delivery.deliver_pending("12345", "open", online_poll=b"poll-3")
    assert len(candidates) == 1, "A confirmed replay must release a later distinct poll for another setting"
    following = candidates[0]
    assert following.key == "BAT_MIN" and following.audit_id != replay.audit_id
    frame = _assert_post_replay_receipt_fence(delivery, replay_ack, (first_wire, replay_wire), following, wire_epoch)
    if replay_following:
        timer += 30
        wire_epoch += 30
        frame = await _bind_second_replay(delivery, following, wire_epoch - 30)
    fresh_stamp = czech_local_datetime_from_epoch(wire_epoch + 3)
    _finish_unrelated_setting(delivery, frame, fresh_stamp, use_echo=echo is not None)
    assert [(result["key"], result["result"]) for result in outcomes] == [("MODE", "ack"), ("BAT_MIN", "ack")]
    assert queue.size() == 0
    if replay_following:
        wire_epoch += 10
        await _assert_old_named_target_still_fenced(delivery, queue, wire_epoch)


@pytest.mark.asyncio
async def test_correlated_confirmation_before_replay_cancels_it(monkeypatch):
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(queue, on_result=outcomes.append)
    timer = time.monotonic()
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(time=time.time, monotonic=lambda: timer))
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id="12345")
    first, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-1")
    wire = native_frame_fields(_bind(delivery, first, "open"))
    assert wire is not None
    delivery.record_injected_box(first, "12345", "open")
    timer += 30
    delivery.poll_timeouts()
    assert delivery.has_pending("12345")
    stamp = datetime.strptime(wire["DT"], "%d.%m.%Y %H:%M:%S").isoformat()
    assert delivery.local_protocol_reply(_setting_ack_at(stamp), "open") is not None
    assert delivery.acknowledge("tbl_box_prms", "MODE", "open", receipt_dt=stamp)
    assert not delivery.has_pending("12345")
    assert [event["result"] for event in outcomes] == ["ack"]


@pytest.mark.asyncio
async def test_timeout_after_disconnect_keeps_retry_without_restoring_closed_session(monkeypatch):
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    timer = time.monotonic()
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(time=time.time, monotonic=lambda: timer))
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id="12345")
    first, = await delivery.deliver_pending("12345", "closed", online_poll=b"poll-1")
    _bind(delivery, first, "closed")
    delivery.record_injected_box(first, "12345", "closed")
    delivery.clear_session("closed")
    timer += 30
    delivery.poll_timeouts()
    assert "closed" not in delivery._online_receipts  # pylint: disable=protected-access
    retry, = await delivery.deliver_pending("12345", "new", online_poll=b"poll-2")
    assert retry.audit_id == first.audit_id


@pytest.mark.asyncio
async def test_timed_out_socket_cannot_send_a_replacement_intent(monkeypatch):
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    timer = time.monotonic()
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(time=time.time, monotonic=lambda: timer))
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id="12345")
    first, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-1")
    _bind(delivery, first, "open")
    delivery.record_injected_box(first, "12345", "open")
    timer += 30
    delivery.poll_timeouts()
    latest = queue.enqueue("tbl_box_prms", "MODE", 0, device_id="12345")
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: int(time.time()) + 30)
    assert await delivery.deliver_pending("12345", "open", online_poll=b"poll-2") == []
    chosen, = await delivery.deliver_pending("12345", "new", online_poll=b"poll-3")
    assert chosen.audit_id == latest.audit_id


@pytest.mark.asyncio
async def test_cloud_reply_on_original_socket_cannot_confirm_a_queued_local_retry(monkeypatch):
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    timer = time.monotonic()
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(time=time.time, monotonic=lambda: timer))
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id="12345")
    first, = await delivery.deliver_pending("12345", "open", online_poll=b"poll-1")
    wire = native_frame_fields(_bind(delivery, first, "open"))
    assert wire is not None
    delivery.record_injected_box(first, "12345", "open")
    timer += 30
    delivery.poll_timeouts()
    cloud = build_setting_frame(device_id="12345", table="tbl_batt_prms", key="BAT_MIN", value=25,
                                id_set=int(time.time()), msg_id=13_800_010, profile=VerProfile(39, 0x1940))
    delivery.begin_cloud_setting("12345", "tbl_batt_prms", "BAT_MIN", 25, cloud.decode(), session_id="open")
    stamp = datetime.strptime(wire["DT"], "%d.%m.%Y %H:%M:%S") + timedelta(seconds=31)
    assert delivery.local_protocol_reply(_setting_ack_at(stamp), "open") is None
    assert delivery.has_pending("12345")


@pytest.mark.asyncio
async def test_closed_aac_socket_with_a_sent_frame_does_not_replay():
    queue = TwinQueue()
    outcomes = []
    delivery = _qualified_delivery(
        queue, on_result=outcomes.append, inflight_timeout_s=0.0,
        power_limits=PowerControlLimits(ac_charge_max_a=125),
    )
    queue.enqueue("tbl_invertor_prm1", "AAC_MAX_CHRG", 75, device_id="12345")
    first, = await delivery.deliver_pending("12345", "first", online_poll=b"poll-1")
    _bind(delivery, first, "first")
    delivery.clear_session("first")
    assert await delivery.deliver_pending("12345", "second", online_poll=b"poll-2") == []
    assert [outcome["result"] for outcome in outcomes] == ["timeout"]


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
