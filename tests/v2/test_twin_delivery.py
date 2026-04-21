# pylint: disable=missing-module-docstring,missing-function-docstring,too-few-public-methods
import os
import sys
import time

import pytest

# pyright: reportMissingImports=false

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "addon", "oig-proxy")))

from unittest.mock import MagicMock  # pylint: disable=wrong-import-position

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


@pytest.mark.asyncio
async def test_deliver_pending_returns_twin_settings() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    queue.enqueue("tbl_set", "T_Mode", "AUTO")
    delivery = TwinDelivery(queue, _MQTTStub())

    pending = await delivery.deliver_pending("12345")

    assert len(pending) == 1
    assert pending[0].table == "tbl_set"
    assert pending[0].key == "T_Room"
    assert pending[0].value == 22


@pytest.mark.asyncio
async def test_deliver_pending_blocks_until_ack() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    queue.enqueue("tbl_set", "T_Mode", "AUTO")
    delivery = TwinDelivery(queue, _MQTTStub())

    first = await delivery.deliver_pending("12345")
    second = await delivery.deliver_pending("12345")

    assert len(first) == 1
    assert second == []

    delivery.acknowledge("tbl_set", "T_Room")
    third = await delivery.deliver_pending("12345")
    assert len(third) == 1
    assert third[0].key == "T_Mode"


@pytest.mark.asyncio
async def test_deliver_pending_drops_after_inflight_timeout() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "SA", 1)
    delivery = TwinDelivery(queue, _MQTTStub(), inflight_timeout_s=0.0)

    first = await delivery.deliver_pending("12345")
    assert len(first) == 1
    assert first[0].key == "SA"

    second = await delivery.deliver_pending("12345")
    assert second == []
    assert queue.size() == 0


def test_build_setting_frame_format() -> None:
    frame = build_setting_frame(
        device_id="2206237016",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        id_set=844979473,
        msg_id=12345678,
    )
    frame_str = frame.decode("utf-8")
    assert "<ID>12345678</ID>" in frame_str
    assert "<ID_Device>2206237016</ID_Device>" in frame_str
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
    queue.enqueue("tbl_set", "T_Room", 22)
    delivery = TwinDelivery(queue, _MQTTStub())

    _ = await delivery.deliver_pending("12345")
    delivery.acknowledge("tbl_set", "T_Room")

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
    queue.enqueue("tbl_set", "T_Room", 22)
    queue.get("tbl_set", "T_Room").raw_text = "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"
    collector = _make_collector()
    delivery = TwinDelivery(queue, _MQTTStub(), telemetry_collector=collector)

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")

    assert len(pending) == 1
    assert len(collector.settings_audit) == 1
    record = collector.settings_audit[0]
    assert record["step"] == SettingStep.DELIVER_SELECTED.value
    assert record["result"] == SettingResult.PENDING.value
    assert record["audit_id"] == pending[0].audit_id
    assert record["session_id"] == "sess_1"
    assert record["raw_text"] == "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"


@pytest.mark.asyncio
async def test_timeout_records_timeout_step() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    queue.get("tbl_set", "T_Room").raw_text = "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"
    collector = _make_collector()
    delivery = TwinDelivery(queue, _MQTTStub(), inflight_timeout_s=0.0, telemetry_collector=collector)

    _ = await delivery.deliver_pending("dev_1", session_id="sess_1")
    _ = await delivery.deliver_pending("dev_1", session_id="sess_1")

    assert len(collector.settings_audit) == 2
    timeout_record = collector.settings_audit[1]
    assert timeout_record["step"] == SettingStep.TIMEOUT.value
    assert timeout_record["result"] == SettingResult.FAILED.value
    assert timeout_record["raw_text"] == "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"


@pytest.mark.asyncio
async def test_clear_session_records_session_cleared() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    queue.get("tbl_set", "T_Room").raw_text = "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"
    collector = _make_collector()
    delivery = TwinDelivery(queue, _MQTTStub(), telemetry_collector=collector)

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    delivery.clear_session("sess_1")

    assert len(collector.settings_audit) == 2
    session_record = collector.settings_audit[1]
    assert session_record["step"] == SettingStep.SESSION_CLEARED.value
    assert session_record["result"] == SettingResult.INCOMPLETE.value
    assert session_record["audit_id"] == pending[0].audit_id
    assert session_record["raw_text"] == "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"


@pytest.mark.asyncio
async def test_shutdown_records_session_cleared_for_global_inflight() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    queue.get("tbl_set", "T_Room").raw_text = "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"
    collector = _make_collector()
    delivery = TwinDelivery(queue, _MQTTStub(), telemetry_collector=collector)

    pending = await delivery.deliver_pending("dev_1")
    delivery.shutdown()

    assert len(collector.settings_audit) == 2
    session_record = collector.settings_audit[1]
    assert session_record["step"] == SettingStep.SESSION_CLEARED.value
    assert session_record["result"] == SettingResult.INCOMPLETE.value
    assert session_record["audit_id"] == pending[0].audit_id
    assert session_record["raw_text"] == "<Frame><TblItem>T_Room</TblItem><NewValue>22</NewValue></Frame>"


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
        raw_text="<Frame><TblItem>MODE</TblItem><NewValue>1</NewValue></Frame>",
        msg_id=12345678,
        id_set=844979473,
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
        raw_text="frame-1",
        msg_id=111,
        id_set=1001,
    )
    delivery.mark_cloud_reason_setting("dev_1")

    delivery.begin_cloud_setting(
        device_id="dev_1",
        table="tbl_box_prms",
        key="MODE",
        value=0,
        raw_text="frame-2",
        msg_id=222,
        id_set=1002,
    )
    delivery.mark_cloud_reason_setting("dev_1")

    matched = delivery.match_cloud_tbl_events(
        "dev_1",
        "tbl_box_prms",
        "MODE",
        confirmed_value="0",
    )
    assert matched is not None
    setting, _ = matched
    assert setting.value == 0

    confirmed = [
        record for record in collector.settings_audit if record["step"] == SettingStep.ACK_TBL_EVENTS.value
    ]
    assert len(confirmed) == 1
    assert confirmed[0]["id_set"] == 1002


def test_cloud_reason_setting_expires_to_terminal_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
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
        raw_text="frame-1",
        msg_id=111,
        id_set=1001,
    )
    delivery.mark_cloud_reason_setting("dev_1")

    original_monotonic = time.monotonic
    monkeypatch.setattr("twin.delivery.time.monotonic", lambda: original_monotonic() + 2.0)

    assert delivery.has_pending_or_inflight() is False

    reason_steps = [
        record for record in collector.settings_audit if record["step"] == SettingStep.ACK_REASON_SETTING.value
    ]
    assert [record["result"] for record in reason_steps] == [
        SettingResult.PENDING.value,
        SettingResult.CONFIRMED.value,
    ]


@pytest.mark.asyncio
async def test_session_timeout_records_timeout_step() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    collector = _make_collector()
    delivery = TwinDelivery(queue, _MQTTStub(), inflight_timeout_s=0.0, telemetry_collector=collector)

    _ = await delivery.deliver_pending("dev_1", session_id="sess_1")
    _ = await delivery.deliver_pending("dev_1", session_id="sess_1")

    assert len(collector.settings_audit) == 2
    timeout_record = collector.settings_audit[1]
    assert timeout_record["step"] == SettingStep.TIMEOUT.value
    assert timeout_record["result"] == SettingResult.FAILED.value
