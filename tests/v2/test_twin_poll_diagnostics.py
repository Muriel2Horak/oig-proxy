"""Explain eligible poll slots skipped while local work waits."""

from datetime import datetime
import logging
import time
from types import SimpleNamespace

import pytest

from protocol.frames import build_setting_frame, czech_local_datetime_from_epoch
from protocol.ver_profile import VerProfile, native_frame_fields
from twin import delivery as delivery_module
from twin.state import TwinQueue

from .test_twin_delivery import _bind, _qualified_delivery, _setting_ack_at


_DEVICE = "12345"
_SESSION = "open"
_EPOCH = 1_794_854_000


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromtimestamp(_EPOCH, tz)


def _skip_messages(caplog):
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.INFO and record.getMessage().startswith("Setting poll skipped ")]


@pytest.fixture
def log_poll_skips(caplog):
    """Show operator diagnostics at the default application log level."""
    caplog.set_level(logging.INFO, logger="twin.delivery")
    return caplog


@pytest.mark.asyncio
async def test_repeated_local_inflight_skip_logs_one_entry_per_interval(monkeypatch, log_poll_skips):
    """Polling or reconnecting repeatedly does not flood logs for one blocked request."""
    clock = time.monotonic()
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock))
    queue = TwinQueue()
    delivery = _qualified_delivery(queue, inflight_timeout_s=120)
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id=_DEVICE)
    queue.enqueue("tbl_batt_prms", "BAT_MIN", 22, device_id=_DEVICE)
    selected, = await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=b"original")
    _bind(delivery, selected, _SESSION)
    delivery.record_injected_box(selected, _DEVICE, _SESSION)

    for session in (_SESSION, "reconnected-1", "reconnected-2"):
        assert await delivery.deliver_pending(_DEVICE, session, online_poll=b"next") == []
    entry, = _skip_messages(log_poll_skips)
    assert "stage=delivery" in entry and "reason=local_setting_inflight" in entry
    assert "target=tbl_batt_prms:BAT_MIN" in entry and "inflight=local:tbl_box_prms:MODE" in entry
    assert "box_id=12345" in entry and "session=open" in entry
    assert "=22" not in entry

    clock += 35
    assert await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=b"later") == []
    assert len(_skip_messages(log_poll_skips)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_cloud_owner_is_reported_only_when_local_work_waits(legacy, log_poll_skips):
    """Cloud's ordinary poll traffic stays quiet until it blocks a local command."""
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    if legacy:
        delivery.set_cloud_inflight("cloud")
    else:
        raw = build_setting_frame(
            device_id=_DEVICE, table="tbl_batt_prms", key="BAT_MIN", value=22,
            id_set=int(time.time()) - 3, msg_id=13_900_000, profile=VerProfile(39, 0x1940),
        ).decode()
        delivery.begin_cloud_setting(_DEVICE, "tbl_batt_prms", "BAT_MIN", 22, raw, session_id="cloud")
    assert await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=b"idle") == []
    assert _skip_messages(log_poll_skips) == []

    queue.enqueue("tbl_box_prms", "MODE", 1, device_id=_DEVICE)
    assert await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=b"with-work") == []
    entry, = _skip_messages(log_poll_skips)
    assert "reason=cloud_setting_inflight" in entry and "target=tbl_box_prms:MODE" in entry
    assert ("inflight=cloud:legacy" if legacy else "inflight=cloud:tbl_batt_prms:BAT_MIN") in entry


@pytest.mark.asyncio
async def test_cloud_poll_diagnostic_keeps_untrusted_table_on_one_line(log_poll_skips):
    """Cloud identifiers may appear in logs without creating a new log entry."""
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    cloud_table = "tbl_batt_prms\nforged log entry"
    raw = build_setting_frame(
        device_id=_DEVICE, table=cloud_table, key="BAT_MIN", value=22,
        id_set=int(time.time()) - 3, msg_id=13_900_000, profile=VerProfile(39, 0x1940),
    ).decode()
    delivery.begin_cloud_setting(_DEVICE, cloud_table, "BAT_MIN", 22, raw, session_id="cloud")
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id=_DEVICE)

    assert await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=b"poll") == []
    entry, = _skip_messages(log_poll_skips)
    assert len(entry.splitlines()) == 1
    assert "reason=cloud_setting_inflight" in entry and "inflight=cloud:tbl_batt_prms" in entry


@pytest.mark.asyncio
@pytest.mark.parametrize(("receipt", "next_poll", "reason"), [
    ("dated", b"original", "duplicate_poll"),
    ("dated", b"distinct", "receipt_clock_fence"),
    ("undated", b"distinct", "reply_quarantine"),
])
async def test_poll_skip_identifies_receipt_fence(receipt, next_poll, reason, monkeypatch, log_poll_skips):
    """Keep a subsequent request queued and label the fence on the used connection."""
    monkeypatch.setattr(delivery_module, "datetime", _FrozenDateTime)
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id=_DEVICE)
    selected, = await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=b"original")
    wire = native_frame_fields(_bind(delivery, selected, _SESSION))
    assert wire is not None
    stamp = czech_local_datetime_from_epoch(int(wire["ID_Set"])) if receipt == "dated" else None
    assert delivery.local_protocol_reply(_setting_ack_at(stamp), _SESSION) is not None
    assert delivery.acknowledge("tbl_box_prms", "MODE", _SESSION, receipt_dt=stamp.isoformat() if stamp else None)

    queued = queue.enqueue("tbl_batt_prms", "BAT_MIN", 22, device_id=_DEVICE)
    assert await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=next_poll) == []
    assert queue.get_pending(unsent_only=True) == [queued]
    entry, = _skip_messages(log_poll_skips)
    assert f"reason={reason}" in entry and "target=tbl_batt_prms:BAT_MIN" in entry


@pytest.mark.asyncio
@pytest.mark.parametrize(("counter", "reason"), [
    ("missing", "msg_id_missing"),
    ("exhausted", "msg_id_exhausted"),
    ("future", "id_set_unavailable"),
])
async def test_poll_skip_identifies_outbound_counter_wait(counter, reason, monkeypatch, log_poll_skips):
    """Identify the counter that blocks a queued setting without allocating a wire ID."""
    monkeypatch.setattr(delivery_module, "datetime", _FrozenDateTime)
    queue = TwinQueue()
    delivery = _qualified_delivery(queue)
    if counter == "missing":
        delivery._counters.clear()  # pylint: disable=protected-access
    elif counter == "exhausted":
        delivery.observe_msg_id(2**31 - 1, _DEVICE)
    else:
        delivery.observe_id_set(_EPOCH + 10, _DEVICE)
    queued = queue.enqueue("tbl_box_prms", "MODE", 0, device_id=_DEVICE)

    assert await delivery.deliver_pending(_DEVICE, _SESSION, online_poll=b"next") == []
    assert queue.get_pending(unsent_only=True) == [queued]
    entry, = _skip_messages(log_poll_skips)
    assert f"reason={reason}" in entry and "target=tbl_box_prms:MODE" in entry
