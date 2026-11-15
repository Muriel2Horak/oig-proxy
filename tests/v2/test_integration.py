"""Integration tests for proxy server."""
# pylint: disable=protected-access,missing-function-docstring,invalid-name,too-many-locals
from __future__ import annotations

import asyncio
import importlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from protocol.frame import build_frame
from proxy.server import ProxyServer
from twin.delivery import TwinDelivery
from twin.state import TwinQueue
from tests.v2.test_wire_profile_qualification import Clock, box_proof, cloud_transaction, learner, native


TEST_BOX = "12345"
MODEL = (39, 0xBB59)
FIRMWARE = "v.3.25.43.1219"
POLL = native(f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device>", MODEL)
ACK = native("<Result>ACK</Result><Reason>Setting</Reason>", MODEL)


def _qualified_transport(config, *, callback=None, collector=None, timeout=60.0, firmware=FIRMWARE):
    """Supply real native proof and an observed outbound counter before testing transport."""
    clock = Clock()
    clock.now = time.time() - 20
    queue = TwinQueue()
    delivery = TwinDelivery(queue, MagicMock(), telemetry_collector=collector, inflight_timeout_s=timeout)
    profiles = learner(clock)
    box_proof(profiles, clock, TEST_BOX, MODEL, firmware)
    cloud_transaction(profiles, clock, TEST_BOX, MODEL)
    delivery._profiles = profiles
    status = delivery.profile_status(TEST_BOX)
    assert status.profile is not None
    queue.set_profile_epoch(status.epoch, device_id=TEST_BOX)
    delivery.observe_msg_id(13800000, TEST_BOX)
    proxy = ProxyServer(config, on_confirmed_setting=callback, twin_delivery=delivery,
                        device_id_provider=lambda: TEST_BOX)
    return proxy, delivery, queue, clock


def _setting_event(clock, value, *, id_set=1234100):
    clock.now = time.time() + 1
    return native(
        f"<TblName>tbl_events</TblName><ID_Device>{TEST_BOX}</ID_Device>"
        f"<ID_Set>{id_set}</ID_Set><DT>{clock.stamp()}</DT><Reason>Table</Reason><Type>Setting</Type>"
        f"<Content>Remotely : tbl_box_prms / MODE: [3]->[{value}]</Content>", MODEL,
    )


def _combined_ack(clock):
    """Build the observed ACK envelope and actual-table suffix with synthetic data."""
    clock.now = time.time()
    stamp = clock.stamp()
    table_fields = (
        ("ENBL", 1), ("VIZ", 9), ("Temp", 24), ("Humid", 45), ("ACO_P", 450),
        ("FV_P1", 0), ("FV_P2", 0), ("ACI_WR", 150), ("ACI_WS", 150), ("ACI_WT", 150),
        ("BAT_P", 0), ("FV_PROC", 0), ("BAT_C", 83), ("MSC_SELF", 0), ("STAT", 0),
        ("FV_AD", 0), ("FVE_PROD", 0), ("INFLOW", 0), ("LOAD", 450), ("MAXFG", 5000),
        ("STATX", 0), ("ACINB_WR", 0), ("ACINB_WS", 0), ("ACINB_WT", 0),
        ("ACO_PR", 150), ("ACO_PS", 150), ("ACO_PT", 150),
    )
    return native(
        f"<Result>ACK</Result><Rdt>{stamp}</Rdt><Reason>Setting</Reason>"
        f"<TblName>tbl_actual</TblName><ID_Set>1234321</ID_Set><DT>{stamp}</DT>"
        f"<Reason>Table</Reason><ID_Device>{TEST_BOX}</ID_Device>"
        + "".join(f"<{tag}>{value}</{tag}>" for tag, value in table_fields), MODEL,
    )


async def _send_poll(proxy, writer, *, session_id="box-flow", poll=POLL):
    reader = asyncio.StreamReader()
    reader.feed_data(poll)
    reader.feed_eof()
    cloud_writer = MagicMock(spec=asyncio.StreamWriter)
    cloud_writer.drain = AsyncMock()
    await proxy._pipe_box_to_cloud(reader, cloud_writer, writer, session_id=session_id)
    return cloud_writer


@pytest.mark.enable_socket
@pytest.mark.asyncio
@pytest.mark.parametrize("firmware", [FIRMWARE, None])
async def test_offline_combined_ack_retires_receipt_and_publishes_actual_table(make_config, firmware) -> None:
    confirmed = AsyncMock()
    telemetry = AsyncMock()
    proxy, delivery, queue, clock = _qualified_transport(
        make_config(proxy_mode="offline"), callback=confirmed, timeout=0.75, firmware=firmware,
    )
    proxy.on_frame = telemetry
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    listener = await asyncio.start_server(proxy._handle_box_connection, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
    try:
        writer.write(POLL)
        await writer.drain()
        setting = await asyncio.wait_for(reader.readuntil(b"</Frame>\r\n"), 2)
        assert b"<TblName>tbl_box_prms</TblName>" in setting
        assert b"<ID_Set>1234321</ID_Set>" not in setting

        ack = _combined_ack(clock)
        writer.write(ack.replace(b"<BAT_C>83</BAT_C>", b"<BAT_C>42</BAT_C>") + ack)
        await writer.drain()
        end = await asyncio.wait_for(reader.readuntil(b"</Frame>\r\n"), 2)
        assert b"<Result>END</Result>" in end
        assert await asyncio.wait_for(reader.read(), 2) == b""

        assert delivery.inflight() is None and queue.size() == 0
        confirmed.assert_not_awaited()
        telemetry.assert_awaited_once()
        table, = telemetry.await_args.args
        assert (table["_device_id"], table["_table"], table["BAT_C"]) == (TEST_BOX, "tbl_actual", 83)
        assert "Result" not in table and "Rdt" not in table
    finally:
        writer.close()
        await writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await proxy.stop()


def _frame(table: str, device_id: str, **fields: object) -> bytes:
    build_frame = importlib.import_module("protocol.frame").build_frame
    inner = [f"<TblName>{table}</TblName>", f"<ID_Device>{device_id}</ID_Device>"]
    inner.extend(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return build_frame("".join(inner)).encode("utf-8")


@pytest.mark.asyncio
async def test_box_stream_forwards_fragments_and_coalesced_frames_exactly_once(
    make_config, dummy_writer_factory,
) -> None:
    proxy = importlib.import_module("proxy.server").ProxyServer(make_config())
    first = _frame("tbl_actual", "12345", Temp=25)
    second = _frame("tbl_batt_prms", "12345", BAT_MIN=20)
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read.side_effect = [first[:15], first[15:] + second + b"tail", b""]
    writer = dummy_writer_factory()

    await proxy._pipe_box_to_cloud(reader, writer)

    assert b"".join(writer.written) == first + second + b"tail"


@pytest.mark.asyncio
async def test_bad_box_crc_is_forwarded_without_updating_state(
    make_config, stream_reader_from_chunks, dummy_writer_factory,
) -> None:
    callback = AsyncMock()
    proxy = ProxyServer(make_config(), on_frame=callback)
    original = _frame("tbl_box_prms", TEST_BOX, MODE=3)
    bad_crc = original.replace(b"<MODE>3</MODE>", b"<MODE>0</MODE>")
    writer = dummy_writer_factory()

    await proxy._pipe_box_to_cloud(stream_reader_from_chunks(bad_crc), writer)

    assert writer.written == [bad_crc]
    callback.assert_not_awaited()


@pytest.mark.enable_socket
@pytest.mark.asyncio
@pytest.mark.parametrize("new_mode", ["online", "hybrid"])
async def test_online_mode_change_closes_existing_idle_offline_socket(
    make_config, new_mode,
) -> None:
    proxy = importlib.import_module("proxy.server").ProxyServer(make_config(proxy_mode="offline"))
    listener = await asyncio.start_server(proxy._handle_box_connection, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(_frame("tbl_actual", "12345", Temp=25))
        await writer.drain()
        ack = await asyncio.wait_for(reader.readuntil(b"</Frame>"), 2)
        assert b"<Result>ACK</Result>" in ack
        await asyncio.wait_for(reader.readexactly(2), 2)

        assert await proxy.apply_configured_mode(new_mode)

        assert await asyncio.wait_for(reader.read(1), 2) == b""
        assert proxy.mode_manager.should_try_cloud()
    finally:
        writer.close()
        await writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await proxy.stop()


@pytest.mark.asyncio
async def test_full_flow_box_to_cloud(
    make_config,
    stream_reader_from_chunks,
    dummy_writer_factory,
) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    cfg = make_config(proxy_mode="online")
    published: list[dict] = []

    async def on_frame(data: dict) -> None:
        published.append(data)

    server = ProxyServer(cfg, on_frame=on_frame)
    box_reader = stream_reader_from_chunks(_frame("tbl_actual", "12345", Temp=25))
    cloud_writer = dummy_writer_factory()

    await server._pipe_box_to_cloud(box_reader, cloud_writer)

    assert cloud_writer.written, "expected forwarded bytes to cloud"
    assert published and published[0]["_table"] == "tbl_actual"
    assert published[0]["_device_id"] == "12345"
    assert published[0]["Temp"] == 25


@pytest.mark.asyncio
async def test_offline_mode_local_ack(
    make_config,
    stream_reader_from_chunks,
    dummy_writer_factory,
) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    cfg = make_config(proxy_mode="offline")
    server = ProxyServer(cfg)
    box_reader = stream_reader_from_chunks(_frame("tbl_actual", "12345", Temp=20))
    box_writer = dummy_writer_factory()

    await server._pipe_box_offline(box_reader, box_writer, ("127.0.0.1", 5555))

    ack_payload = b"".join(box_writer.written)
    assert b"<Result>ACK</Result>" in ack_payload


@pytest.mark.asyncio
async def test_hybrid_mode_transition(make_config, dummy_writer_factory) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    cfg = make_config(proxy_mode="hybrid", hybrid_fail_threshold=1)
    server = ProxyServer(cfg)

    box_reader = asyncio.StreamReader()
    box_reader.feed_eof()
    box_writer = dummy_writer_factory()

    with patch("proxy.server.asyncio.open_connection", side_effect=OSError("cloud down")):
        await server._handle_box_connection(box_reader, box_writer)

    assert server.mode_manager.is_offline() is True

    cloud_reader = asyncio.StreamReader()
    cloud_reader.feed_eof()
    cloud_writer = dummy_writer_factory()
    box_reader_recovered = asyncio.StreamReader()
    box_reader_recovered.feed_eof()
    box_writer_recovered = dummy_writer_factory()

    async def _open_ok(*_args, **_kwargs):
        return cloud_reader, cloud_writer

    with patch("proxy.server.asyncio.open_connection", new=_open_ok):
        await server._handle_box_connection(box_reader_recovered, box_writer_recovered)

    assert server.mode_manager.is_offline() is False


@pytest.mark.asyncio
async def test_twin_setting_delivery(make_config, dummy_writer_factory) -> None:
    server, _delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_boiler_prms", "ISON", 1, device_id=TEST_BOX, require_profile=True)
    box_writer = dummy_writer_factory()
    cloud_writer = await _send_poll(server, box_writer)
    cloud_writer.write.assert_not_called()

    delivered, = box_writer.written
    assert b"<TblName>tbl_boiler_prms</TblName>" in delivered
    assert b"<ID_Device>12345</ID_Device>" in delivered
    assert b"<ID_Set>" in delivered
    assert b"<Reason>Setting</Reason>" in delivered
    assert b"<TblItem>ISON</TblItem>" in delivered
    assert b"<NewValue>1</NewValue>" in delivered
    assert b"<ID_SubD>0</ID_SubD>" in delivered
    assert b"<ID_Server>9</ID_Server>" in delivered
    assert b"<Confirm>New</Confirm>" in delivered


@pytest.mark.asyncio
async def test_twin_tbl_events_ack_removes_pending(make_config, dummy_writer_factory) -> None:
    confirmed = AsyncMock()
    proxy, _delivery, queue, clock = _qualified_transport(make_config(), callback=confirmed)
    queue.enqueue("tbl_box_prms", "MODE", 0, device_id=TEST_BOX, require_profile=True)
    await _send_poll(proxy, dummy_writer_factory())
    assert queue.size() == 1

    event = _setting_event(clock, 0)
    await proxy._handle_twin_frames(event, "box-flow")

    assert queue.size() == 0
    confirmed.assert_awaited_once_with(TEST_BOX, "tbl_box_prms", "MODE", "0", source_dt=clock.stamp())


@pytest.mark.asyncio
async def test_twin_reason_setting_ack_retires_only_its_bound_session(make_config, dummy_writer_factory) -> None:
    confirmed = AsyncMock()
    proxy, delivery, queue, _clock = _qualified_transport(make_config(), callback=confirmed)
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    await _send_poll(proxy, dummy_writer_factory())
    assert delivery.inflight() == ("tbl_box_prms", "MODE")

    # A native ACK without a device field cannot inherit another connection's identity.
    await proxy._handle_twin_frames(POLL, "other-flow")
    assert await proxy._handle_twin_frames(ACK, "other-flow") is None
    assert queue.size() == 1
    assert await proxy._handle_twin_frames(ACK, "box-flow") == "ack"
    assert queue.size() == 0
    assert delivery.inflight() is None
    confirmed.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_setting_tracks_only_native_target_on_bound_flow(make_config, dummy_writer_factory) -> None:
    proxy, delivery, _queue, _clock = _qualified_transport(make_config())
    writer = dummy_writer_factory()
    await proxy._handle_twin_frames(POLL, "box-flow")
    own = _frame("tbl_box_prms", TEST_BOX, TblItem="MODE", NewValue=3, Reason="Setting")
    foreign = _frame("tbl_box_prms", "98765", TblItem="MODE", NewValue=3, Reason="Setting")
    for session, setting, tracked in [("unbound", own, False), ("box-flow", foreign, False), ("box-flow", own, True)]:
        reader = asyncio.StreamReader()
        reader.feed_data(setting)
        reader.feed_eof()
        await proxy._pipe_cloud_to_box(reader, writer, session_id=session)
        assert delivery.is_cloud_inflight(TEST_BOX) is tracked
    assert writer.written == [own, foreign, own]


@pytest.mark.asyncio
async def test_cloud_end_clears_only_its_bound_flow(make_config, dummy_writer_factory) -> None:
    proxy, delivery, _queue, _clock = _qualified_transport(make_config())
    await proxy._handle_twin_frames(POLL, "box-flow")
    await proxy._handle_twin_frames(POLL, "other-flow")
    delivery.set_cloud_inflight("box-flow")

    for session, in_flight in [("other-flow", True), ("box-flow", False)]:
        reader = asyncio.StreamReader()
        reader.feed_data(build_frame("<Result>END</Result>").encode())
        reader.feed_eof()
        await proxy._pipe_cloud_to_box(reader, dummy_writer_factory(), session_id=session)
        assert delivery.is_cloud_inflight() is in_flight


@pytest.mark.asyncio
async def test_local_setting_frame_uses_monotonic_msg_id(make_config, dummy_writer_factory) -> None:
    proxy, _delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    writer = dummy_writer_factory()
    await _send_poll(proxy, writer)
    assert b"<ID>13800001</ID>" in b"".join(writer.written)


@pytest.mark.asyncio
async def test_stale_replay_file_is_ignored_during_normal_delivery(
    make_config, dummy_writer_factory, tmp_path, monkeypatch,
) -> None:
    proxy, _delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    monkeypatch.chdir(tmp_path)
    replay = tmp_path / "replay_setting_frame.xml"
    replay.write_bytes(_frame("tbl_box_prms", TEST_BOX, TblItem="MODE", NewValue=1, ID=77777777))
    writer = dummy_writer_factory()

    await _send_poll(proxy, writer)

    sent, = writer.written
    assert b"<ID>13800001</ID>" in sent
    assert b"<NewValue>3</NewValue>" in sent
    assert b"<ID>77777777</ID>" not in sent
    assert replay.exists()


@pytest.mark.enable_socket
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ack", "event_then_ack", "timeout"])
async def test_switch_online_preserves_inflight_offline_exchange_until_terminal(make_config, outcome) -> None:
    event_seen = asyncio.Event()

    async def on_confirmed(*_args, **_kwargs):
        event_seen.set()

    proxy, delivery, queue, clock = _qualified_transport(
        make_config(proxy_mode="offline"), callback=on_confirmed, timeout=0.25 if outcome == "timeout" else 2.0,
    )
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    listener = await asyncio.start_server(proxy._handle_box_connection, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        idle_writer.write(_frame("tbl_actual", TEST_BOX, Temp=25))
        await idle_writer.drain()
        assert b"<Result>ACK</Result>" in await asyncio.wait_for(idle_reader.readuntil(b"</Frame>\r\n"), 2)
        writer.write(POLL)
        await writer.drain()
        setting = await asyncio.wait_for(reader.readuntil(b"</Frame>\r\n"), 2)
        assert b"<Reason>Setting</Reason>" in setting
        assert b"<NewValue>3</NewValue>" in setting

        assert await proxy.apply_configured_mode("online")
        assert await asyncio.wait_for(idle_reader.read(), 2) == b""
        assert delivery.inflight() == ("tbl_box_prms", "MODE")
        if outcome == "event_then_ack":
            writer.write(_setting_event(clock, 3))
            await writer.drain()
            await asyncio.wait_for(event_seen.wait(), 1)
            assert queue.size() == 0
        if outcome != "timeout":
            writer.write(ACK)
            await writer.drain()
            end = await asyncio.wait_for(reader.readuntil(b"</Frame>\r\n"), 2)
            assert b"<Result>END</Result>" in end
        assert await asyncio.wait_for(reader.read(), 2) == b""
        assert delivery.inflight() is None
        assert queue.size() == 0
    finally:
        for connection in (writer, idle_writer):
            connection.close()
            await connection.wait_closed()
        listener.close()
        await listener.wait_closed()
        await proxy.stop()


@pytest.mark.asyncio
async def test_device_id_validation(make_config) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    cfg = make_config(proxy_mode="online")
    published: list[dict] = []
    device_id: str | None = None

    async def on_frame(data: dict) -> None:
        nonlocal device_id
        frame_device_id = data.get("_device_id")
        table = data.get("_table")
        if not frame_device_id or not table:
            return
        if device_id is None:
            device_id = frame_device_id
        elif frame_device_id != device_id:
            return
        published.append(data)

    server = ProxyServer(cfg, on_frame=on_frame)
    first = _frame("tbl_actual", "1001", Temp=30)
    second = _frame("tbl_actual", "9999", Temp=31)
    box_reader = asyncio.StreamReader()
    box_reader.feed_data(first + second)
    box_reader.feed_eof()
    cloud_writer = MagicMock()
    cloud_writer.write = MagicMock()
    cloud_writer.drain = AsyncMock()

    await server._pipe_box_to_cloud(box_reader, cloud_writer)

    assert len(published) == 1
    assert published[0]["_device_id"] == "1001"
    assert published[0]["Temp"] == 30


@pytest.mark.asyncio
async def test_telemetry_collection() -> None:
    TelemetryCollector = importlib.import_module("telemetry.collector").TelemetryCollector
    collector = TelemetryCollector(
        interval_s=1,
        version="2.0.0",
        telemetry_enabled=True,
        telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
        telemetry_interval_s=1,
        device_id="device123",
        mqtt_namespace="oig_local",
        mqtt_publisher=MagicMock(is_ready=MagicMock(return_value=True)),
    )
    collector.init()
    assert collector.client is not None

    sent_payloads: list[dict] = []

    async def _send_telemetry(payload: dict) -> bool:
        sent_payloads.append(payload)
        return True

    collector.client.provision = AsyncMock(return_value=True)
    collector.client.send_telemetry = AsyncMock(side_effect=_send_telemetry)

    original_sleep = asyncio.sleep

    async def _fast_sleep(_seconds: float) -> None:
        await original_sleep(0)

    with patch("telemetry.collector.asyncio.sleep", side_effect=_fast_sleep):
        task = asyncio.create_task(collector.loop())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert sent_payloads, "expected telemetry payload publish"
    assert all("timestamp" in p for p in sent_payloads)
    assert all("window_metrics" in p for p in sent_payloads)
