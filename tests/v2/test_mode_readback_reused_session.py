"""Exercise a native MODE event and its follow-up readback on one BOX session."""

# pylint: disable=protected-access
from datetime import datetime, timedelta
import logging
from types import SimpleNamespace
import time
from unittest.mock import Mock

import pytest

from protocol.ver_profile import native_frame_fields
from proxy import server as server_module
from twin import delivery as delivery_module
from tests.v2.test_integration import MODEL, POLL, TEST_BOX, _send_poll
from tests.v2.test_main_integration import _prepare_mode_receipt
from tests.v2.test_wire_profile_qualification import native


def _poll(marker):
    return native(
        f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device><Lat>{marker}</Lat>", MODEL,
    )


def _setting_ack(date, *, sa_id=None):
    echo = ""
    if sa_id is not None:
        echo = f"<ID>{sa_id}</ID><TblName>tbl_box_prms</TblName><TblItem>SA</TblItem>"
    return native(
        f"<Result>ACK</Result><Rdt>{date.isoformat()}</Rdt><Reason>Setting</Reason>{echo}", MODEL,
    )


async def _expect_forwarded(app, writer, frame):
    forwarded = await _send_poll(app.proxy, writer, poll=frame)
    forwarded.write.assert_called_once_with(frame)


async def _confirm_mode_by_event(make_config, monkeypatch, writer):
    clock = Mock(return_value=0.0)
    wall_start = time.time()

    def wall_time():
        return wall_start + clock()

    class ReplayDatetime(datetime):
        """Keep the Setting allocator aligned with the replay clock."""

        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(wall_time(), tz)

    clocks = SimpleNamespace(monotonic=clock, time=wall_time)
    monkeypatch.setattr(delivery_module, "time", clocks)
    monkeypatch.setattr(delivery_module, "datetime", ReplayDatetime)
    monkeypatch.setattr(server_module, "time", clocks)
    app, results = await _prepare_mode_receipt(
        make_config(control_mqtt_enabled=True), clock, "local", writer,
    )

    def advance(seconds):
        clock.return_value = seconds
        app.twin_delivery._profiles._clock.now = wall_time()

    advance(0.0)
    audit = []
    app.twin_delivery._telemetry_collector = SimpleNamespace(record_setting_audit_step=audit.append)
    assert app.twin_delivery.inflight() == ("tbl_box_prms", "MODE")
    fields = native_frame_fields(writer.written[0])
    assert fields is not None
    event_date = datetime.strptime(fields["DT"], "%d.%m.%Y %H:%M:%S") + timedelta(seconds=1)
    event = native(
        f"<TblName>tbl_events</TblName><ID_Device>{TEST_BOX}</ID_Device>"
        f"<ID_Set>1234100</ID_Set><DT>{event_date.isoformat()}</DT><Reason>Table</Reason>"
        "<Type>Setting</Type><Content>Remotely : tbl_box_prms / MODE: [3]-&gt;[1]</Content>",
        MODEL,
    )
    await _expect_forwarded(app, writer, event)
    assert [(result["key"], result["result"], result["evidence"]) for result in results] == [
        ("MODE", "ack", "setting_event"),
    ]
    assert [(record.key, record.step) for record in audit] == [("MODE", "ack_tbl_events")]
    assert app.twin_delivery.inflight() is None
    assert len(writer.written) == 1
    return app, advance, results


@pytest.mark.asyncio
@pytest.mark.parametrize("echo_receipt", [
    pytest.param(False, id="distinct-poll-timeout"),
    pytest.param(True, id="identical-poll-echoed-ack"),
])
async def test_event_confirmed_mode_gets_one_due_readback_on_same_session(
    make_config, monkeypatch, dummy_writer_factory, caplog, echo_receipt,
):
    """Send SA at the deadline and keep an anonymous prior reply uncorrelated."""
    writer = dummy_writer_factory()
    app, advance, results = await _confirm_mode_by_event(make_config, monkeypatch, writer)

    advance(59.0)
    await _expect_forwarded(app, writer, _poll(59))
    assert len(writer.written) == 1

    advance(60.0)
    due_poll = POLL if echo_receipt else _poll(60)
    forwarded = await _send_poll(app.proxy, writer, poll=due_poll)
    assert len(writer.written) == 2, "Expected SA on the first qualified IsNew at the deadline"
    forwarded.write.assert_not_called()
    sa = native_frame_fields(writer.written[-1])
    assert sa is not None
    assert (sa["TblName"], sa["TblItem"], sa["NewValue"]) == ("tbl_box_prms", "SA", "1")
    assert app.twin_delivery.inflight() == ("tbl_box_prms", "SA")
    box_epoch = app.twin_delivery._profiles.box_clock_epoch(TEST_BOX)
    assert app.proxy._readback.response_deadline_for(
        "tbl_box_prms", "MODE", 1, box_epoch=box_epoch,
    ) == 180.0

    advance(61.0)
    late_date = datetime.strptime(sa["DT"], "%d.%m.%Y %H:%M:%S") + timedelta(seconds=1)
    await _send_poll(app.proxy, writer, poll=_setting_ack(late_date))
    assert [result["key"] for result in results] == ["MODE"]
    assert app.twin_delivery.inflight() == ("tbl_box_prms", "SA")

    table = native(
        f"<TblName>tbl_box_prms</TblName><ID_Device>{TEST_BOX}</ID_Device>"
        f"<ID_Set>1234101</ID_Set><DT>{late_date.isoformat()}</DT>"
        "<Reason>Table</Reason><MODE>1</MODE>",
        MODEL,
    )
    with caplog.at_level(logging.INFO, logger="parameter_refresh"):
        await _expect_forwarded(app, writer, table)
    assert app.proxy._readback.response_deadline_for(
        "tbl_box_prms", "MODE", 1, box_epoch=box_epoch,
    ) is None
    assert "Parameter readback confirmed for tbl_box_prms:MODE" in caplog.messages

    advance(62.0)
    if echo_receipt:
        await _send_poll(app.proxy, writer, poll=_setting_ack(late_date, sa_id=sa["ID"]))
        assert [(result["key"], result["result"]) for result in results] == [
            ("MODE", "ack"), ("SA", "ack"),
        ]
        assert app.twin_delivery.inflight() is None

    await _expect_forwarded(app, writer, due_poll if echo_receipt else _poll(62))
    assert len(writer.written) == 2

    advance(121.0)
    await _expect_forwarded(app, writer, _poll(121))
    assert app.twin_delivery.inflight() is None
    assert len(writer.written) == 2


@pytest.mark.asyncio
async def test_event_confirmed_socket_still_blocks_generic_queued_mutation(
    make_config, monkeypatch, dummy_writer_factory, caplog,
):
    """Keep the same-session exception exclusive to the automatic SA readback."""
    writer = dummy_writer_factory()
    app, advance, _ = await _confirm_mode_by_event(make_config, monkeypatch, writer)
    setting = app.twin_handler.admit_message(f"oig_local/{TEST_BOX}/set/tbl_box_prms/MODE", b"0")
    assert setting is not None

    advance(60.0)
    with caplog.at_level(logging.INFO, logger="twin.delivery"):
        await _expect_forwarded(app, writer, _poll(61))
    assert len(writer.written) == 1
    assert app.twin_queue.get("tbl_box_prms", "MODE", device_id=TEST_BOX) is setting
    assert any("reason=reply_quarantine" in message for message in caplog.messages)

    other_socket = dummy_writer_factory()
    await _send_poll(app.proxy, other_socket, session_id="fresh-flow", poll=_poll(62))
    sent, = other_socket.written
    fields = native_frame_fields(sent)
    assert fields is not None
    assert (fields["TblItem"], fields["NewValue"]) == ("MODE", "0")
