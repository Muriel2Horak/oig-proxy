import os
import sys

import pytest

# pyright: reportMissingImports=false

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "addon", "oig-proxy-v2")))

from twin.delivery import TwinDelivery
from twin.state import TwinQueue


class _MQTTStub:
    pass


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


def test_build_setting_xml_format() -> None:
    xml = TwinDelivery.build_setting_xml(
        "tbl_box_prms", "MODE", 1, "2206237016", 844979473, msg_id=12345678, confirm="New"
    )
    assert "<ID>12345678</ID>" in xml
    assert "<ID_Device>2206237016</ID_Device>" in xml
    assert "<ID_Set>844979473</ID_Set>" in xml
    assert "<ID_SubD>0</ID_SubD>" in xml
    assert "<NewValue>1</NewValue>" in xml
    assert "<Confirm>New</Confirm>" in xml
    assert "<TblName>tbl_box_prms</TblName>" in xml
    assert "<TblItem>MODE</TblItem>" in xml
    assert "<ID_Server>9</ID_Server>" in xml
    assert "<mytimediff>0</mytimediff>" in xml
    assert "<Reason>Setting</Reason>" in xml
    assert "<ver>" in xml and "</ver>" in xml
    assert "<TSec>" in xml and "</TSec>" in xml
    assert "<DT>" in xml and "</DT>" in xml


@pytest.mark.asyncio
async def test_acknowledge_removes_setting_from_queue() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    delivery = TwinDelivery(queue, _MQTTStub())

    _ = await delivery.deliver_pending("12345")
    delivery.acknowledge("tbl_set", "T_Room")

    assert queue.size() == 0


def test_enqueue_assigns_stable_id_set() -> None:
    queue = TwinQueue()

    queue.enqueue("tbl_box_prms", "MODE", 1)
    first = queue.get("tbl_box_prms", "MODE")
    assert first is not None

    queue.enqueue("tbl_box_prms", "MODE", 0)
    second = queue.get("tbl_box_prms", "MODE")
    assert second is not None

    assert isinstance(first.id_set, int)
    assert isinstance(second.id_set, int)
    assert second.id_set != first.id_set


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
