"""Exercise Setting serialization and receipt correlation with proven BOX clock drift."""

from datetime import datetime, timezone
import time

import pytest

from protocol.frames import czech_local_datetime_from_epoch
from protocol.ver_profile import native_frame_fields
from twin.state import TwinQueue

from .test_twin_delivery import _bind, _qualified_delivery, _setting_ack_at


@pytest.mark.asyncio
async def test_sa_uses_proven_box_clock_after_cloud_counter_ahead_of_proxy(monkeypatch):
    """Advance the wire counter without awaiting the proxy wall clock."""
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    monkeypatch.setattr(delivery._profiles, "box_clock_offset", lambda _device: 250.0)  # pylint: disable=protected-access
    monkeypatch.setattr(delivery._profiles, "box_timestamp", lambda _device, _date: time.time())  # pylint: disable=protected-access
    before = int(time.time())
    cloud_id_set = before + 245
    delivery.observe_id_set(cloud_id_set, "12345")
    queue.enqueue("tbl_box_prms", "SA", 1, device_id="12345")

    setting, = await delivery.deliver_pending("12345", "open", online_poll=b"is-new-set")
    wire = native_frame_fields(_bind(delivery, setting, "open"))
    assert wire is not None and (wire["TblItem"], wire["NewValue"]) == ("SA", "1")
    wire_time = datetime.strptime(wire["TSec"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    assert before + 250 <= wire_time.timestamp() <= int(time.time()) + 250
    assert cloud_id_set < int(wire["ID_Set"]) <= wire_time.timestamp()
    assert wire["DT"] == czech_local_datetime_from_epoch(int(wire["ID_Set"])).strftime("%d.%m.%Y %H:%M:%S")
    reply = _setting_ack_at(czech_local_datetime_from_epoch(int(wire_time.timestamp())))
    assert delivery.local_protocol_reply(reply, "open") is not None


@pytest.mark.asyncio
async def test_shifted_box_ack_releases_next_poll_on_open_socket(monkeypatch):
    """Use a fresh dated receipt and reject the old one on the next poll."""
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    monkeypatch.setattr(delivery._profiles, "box_clock_offset", lambda _device: 250.0, raising=False)  # pylint: disable=protected-access
    monkeypatch.setattr(delivery._profiles, "box_timestamp", lambda _device, _date: time.time(), raising=False)  # pylint: disable=protected-access
    wire_epoch = int(time.time()) - 40
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch + 250)
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id="12345")
    first, = await delivery.deliver_pending("12345", "open", online_poll=b"first-poll")
    _bind(delivery, first, "open")
    previous_ack = czech_local_datetime_from_epoch(wire_epoch + 250)
    assert delivery.local_protocol_reply(_setting_ack_at(previous_ack), "open") is not None
    assert delivery.acknowledge("tbl_box_prms", "MODE", "open", receipt_dt=previous_ack.isoformat())

    wire_epoch += 10
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id="12345")
    next_setting, = await delivery.deliver_pending("12345", "open", online_poll=b"second-poll")
    _bind(delivery, next_setting, "open")
    assert delivery.local_protocol_reply(_setting_ack_at(previous_ack), "open") is None
    assert delivery.local_protocol_reply(_setting_ack_at(czech_local_datetime_from_epoch(wire_epoch + 250)), "open")
