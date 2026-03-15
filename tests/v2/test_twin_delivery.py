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
    xml = TwinDelivery.build_setting_xml("tbl_box_prms", "MODE", 1)
    assert xml == "<TblName>tbl_box_prms</TblName><MODE>1</MODE>"


@pytest.mark.asyncio
async def test_acknowledge_removes_setting_from_queue() -> None:
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    delivery = TwinDelivery(queue, _MQTTStub())

    _ = await delivery.deliver_pending("12345")
    delivery.acknowledge("tbl_set", "T_Room")

    assert queue.size() == 0
