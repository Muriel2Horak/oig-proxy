"""Integration tests for proxy server."""
# pylint: disable=protected-access,missing-function-docstring,invalid-name,too-many-locals
from __future__ import annotations

import asyncio
import importlib
import socket
import struct
import time
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from protocol.frame import build_frame
from protocol.ver_profile import native_frame_fields
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


@asynccontextmanager
async def _live_proxy(make_config, cloud_peer, *, mode="hybrid", on_frame=None, **config):
    cloud = await asyncio.start_server(cloud_peer, "127.0.0.1", 0)
    proxy = ProxyServer(
        make_config(proxy_mode=mode, cloud_port=cloud.sockets[0].getsockname()[1], **config),
        on_frame=on_frame,
    )
    listener = await asyncio.start_server(proxy._handle_box_connection, "127.0.0.1", 0)
    try:
        yield proxy, listener.sockets[0].getsockname()[1]
    finally:
        listener.close()
        await listener.wait_closed()
        await proxy.stop()
        cloud.close()
        await cloud.wait_closed()


@asynccontextmanager
async def _live_box(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def _expect_local_table_ack(reader, writer, *, temp):
    writer.write(_frame("tbl_actual", TEST_BOX, Temp=temp))
    await writer.drain()
    reply = await asyncio.wait_for(reader.readuntil(b"</Frame>\r\n"), 2)
    assert b"<Result>ACK</Result>" in reply


@pytest.mark.enable_socket
@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", ["fin", "rst"])
async def test_hybrid_empty_cloud_disconnect_preserves_delayed_box_table(
    make_config, disconnect,
) -> None:
    cloud_closing = asyncio.Event()
    table_published = asyncio.get_running_loop().create_future()

    async def cloud_peer(_reader, writer):
        if disconnect == "rst":
            cloud_socket = writer.get_extra_info("socket")
            cloud_socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            writer.transport.abort()
        else:
            writer.close()
        await writer.wait_closed()
        cloud_closing.set()

    async def publish_table(table):
        table_published.set_result(table)

    async with (
        _live_proxy(make_config, cloud_peer, on_frame=publish_table) as (proxy, port),
        _live_box(port) as box,
    ):
        await asyncio.wait_for(cloud_closing.wait(), 2)
        await asyncio.sleep(0.025)
        await _expect_local_table_ack(*box, temp=24)
        table = await asyncio.wait_for(table_published, 2)
        assert (table["_device_id"], table["_table"], table["Temp"]) == (TEST_BOX, "tbl_actual", 24)
        assert proxy.mode_manager.runtime_mode.value == "offline"
        assert proxy.mode_manager.fail_count == 1


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_hybrid_empty_cloud_failures_respect_threshold_and_cooldown(make_config) -> None:
    closed_probes = asyncio.Queue()
    cloud_connections = 0

    async def cloud_peer(_reader, writer):
        nonlocal cloud_connections
        cloud_connections += 1
        writer.close()
        await writer.wait_closed()
        closed_probes.put_nowait(cloud_connections)

    async with _live_proxy(
        make_config, cloud_peer, hybrid_fail_threshold=2, hybrid_retry_interval=60,
    ) as (proxy, port), AsyncExitStack() as boxes:
        first_box = await boxes.enter_async_context(_live_box(port))
        assert await asyncio.wait_for(closed_probes.get(), 2) == 1
        await asyncio.sleep(0.025)
        await _expect_local_table_ack(*first_box, temp=24)
        assert proxy.mode_manager.runtime_mode.value == "online"
        assert proxy.mode_manager.fail_count == 1

        second_box = await boxes.enter_async_context(_live_box(port))
        assert await asyncio.wait_for(closed_probes.get(), 2) == 2
        await asyncio.sleep(0.025)
        await _expect_local_table_ack(*second_box, temp=25)
        assert proxy.mode_manager.runtime_mode.value == "offline"
        assert proxy.mode_manager.fail_count == 2

        third_box = await boxes.enter_async_context(_live_box(port))
        await _expect_local_table_ack(*third_box, temp=26)
        await _expect_local_table_ack(*first_box, temp=27)
        assert cloud_connections == 2
        assert proxy.mode_manager.fail_count == 2


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_hybrid_recovery_waits_for_cloud_reply_after_retry_handshake(make_config) -> None:
    retry_received_table = asyncio.Event()
    send_reply = asyncio.Event()
    send_valid_reply = asyncio.Event()
    request = _frame("tbl_actual", TEST_BOX, Temp=25)
    response = build_frame("<Result>ACK</Result>").encode()
    invalid_response = response.replace(b"<Result>ACK</Result>", b"<Result>NACK</Result>")

    async def cloud_peer(reader, writer):
        try:
            assert await reader.readexactly(len(request)) == request
            retry_received_table.set()
            await send_reply.wait()
            writer.write(invalid_response)
            await writer.drain()
            await send_valid_reply.wait()
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with _live_proxy(make_config, cloud_peer) as (proxy, port):
        proxy.mode_manager.record_failure("test setup")
        async with _live_box(port) as (reader, writer):
            writer.write(request)
            await writer.drain()
            try:
                await asyncio.wait_for(retry_received_table.wait(), 2)
                assert proxy.mode_manager.runtime_mode.value == "offline"
                assert proxy.mode_manager.fail_count == 1
                send_reply.set()
                forwarded = await asyncio.wait_for(reader.readexactly(len(invalid_response)), 2)
                assert forwarded == invalid_response
                assert proxy.mode_manager.runtime_mode.value == "offline"
                assert proxy.mode_manager.fail_count == 1
            finally:
                send_reply.set()
                send_valid_reply.set()
            assert await asyncio.wait_for(reader.readexactly(len(response)), 2) == response
            assert proxy.mode_manager.runtime_mode.value == "online"
            assert proxy.mode_manager.fail_count == 0


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_hybrid_unanswered_recovery_probe_keeps_other_box_connection_local(make_config) -> None:
    request = _frame("tbl_actual", TEST_BOX, Temp=25)
    forwarded = asyncio.get_running_loop().create_future()
    cloud_connections = 0

    async def cloud_peer(reader, writer):
        nonlocal cloud_connections
        cloud_connections += 1
        try:
            if cloud_connections == 1:
                forwarded.set_result(await reader.readexactly(len(request)))
                await reader.read()
            else:
                writer.write(build_frame("<Result>END</Result>").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with _live_proxy(make_config, cloud_peer, cloud_ack_timeout=5) as (proxy, port):
        proxy.mode_manager.record_failure("test setup")
        async with _live_box(port) as (_reader, writer):
            writer.write(request)
            await writer.drain()
            assert await asyncio.wait_for(forwarded, 2) == request

            async with _live_box(port) as other_box:
                await _expect_local_table_ack(*other_box, temp=26)
            assert cloud_connections == 1
            assert proxy.mode_manager.runtime_mode.value == "offline"
            assert proxy.mode_manager.fail_count == 1


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_hybrid_unanswered_recovery_probe_times_out_without_local_replay(make_config, monkeypatch) -> None:
    clock = Clock()
    monkeypatch.setattr("proxy.mode.time", SimpleNamespace(time=clock, monotonic=clock))
    request = _frame("tbl_actual", TEST_BOX, Temp=25)
    forwarded = asyncio.Queue()
    cloud_connections = 0

    async def cloud_peer(reader, writer):
        nonlocal cloud_connections
        cloud_connections += 1
        try:
            forwarded.put_nowait(await reader.readexactly(len(request)))
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    async with _live_proxy(
        make_config, cloud_peer, hybrid_retry_interval=60, cloud_ack_timeout=0.25,
    ) as (proxy, port):
        proxy.mode_manager.record_failure("test setup")
        clock.now += 61
        async with _live_box(port) as (reader, writer):
            writer.write(request)
            await writer.drain()
            assert await asyncio.wait_for(forwarded.get(), 2) == request
            assert await asyncio.wait_for(reader.read(), 1) == b""

        assert proxy.mode_manager.runtime_mode.value == "offline"
        assert proxy.mode_manager.fail_count == 2
        async with _live_box(port) as box:
            await _expect_local_table_ack(*box, temp=26)
        assert cloud_connections == 1


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_hybrid_live_cloud_reply_clears_a_concurrent_failure(make_config) -> None:
    first_reply = build_frame("<Result>ACK</Result>").encode()
    second_reply = build_frame("<Result>END</Result>").encode()
    subsequent_request = _frame("tbl_actual", TEST_BOX, Temp=27)
    forwarded = asyncio.Queue()
    send_second_reply = asyncio.Event()
    went_offline = asyncio.Event()
    cloud_connections = 0

    async def cloud_peer(reader, writer):
        nonlocal cloud_connections
        cloud_connections += 1
        try:
            if cloud_connections == 1:
                writer.write(first_reply)
                await writer.drain()
                await send_second_reply.wait()
                writer.write(second_reply)
                await writer.drain()
                forwarded.put_nowait(await reader.readexactly(len(subsequent_request)))
                await reader.read()
        except asyncio.IncompleteReadError as exc:
            forwarded.put_nowait(exc.partial)
        finally:
            writer.close()
            await writer.wait_closed()

    async with _live_proxy(make_config, cloud_peer) as (proxy, port):
        proxy.mode_manager.on_hybrid_transition = lambda *_details: went_offline.set()
        async with _live_box(port) as (reader, writer):
            try:
                assert await asyncio.wait_for(reader.readexactly(len(first_reply)), 2) == first_reply
                async with _live_box(port) as (fallback_reader, fallback_writer):
                    await asyncio.wait_for(went_offline.wait(), 2)
                    await _expect_local_table_ack(fallback_reader, fallback_writer, temp=26)
                    assert proxy.mode_manager.fail_count == 1
                    assert proxy.mode_manager.runtime_mode.value == "offline"
                    assert proxy.is_cloud_connected()
                    send_second_reply.set()

                    assert await asyncio.wait_for(reader.readexactly(len(second_reply)), 2) == second_reply
                    assert await asyncio.wait_for(fallback_reader.read(), 2) == b""
            finally:
                send_second_reply.set()

            assert proxy.mode_manager.fail_count == 0
            assert proxy.mode_manager.runtime_mode.value == "online"
            assert proxy.is_cloud_connected()
            writer.write(subsequent_request)
            await writer.drain()
            assert await asyncio.wait_for(forwarded.get(), 2) == subsequent_request


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_hybrid_failed_retry_restarts_cooldown_after_forwarding_box_table(
    make_config, monkeypatch,
) -> None:
    clock = Clock()
    monkeypatch.setattr("proxy.mode.time", SimpleNamespace(time=clock, monotonic=clock))
    request = _frame("tbl_actual", TEST_BOX, Temp=25)
    forwarded = asyncio.Queue()

    async def cloud_peer(reader, writer):
        try:
            forwarded.put_nowait(await reader.readexactly(len(request)))
        finally:
            writer.close()
            await writer.wait_closed()

    async with _live_proxy(make_config, cloud_peer, hybrid_retry_interval=60) as (proxy, port):
        proxy.mode_manager.record_failure("test setup")
        clock.now += 61
        async with _live_box(port) as (reader, writer):
            writer.write(request)
            await writer.drain()
            assert await asyncio.wait_for(forwarded.get(), 2) == request
            assert await asyncio.wait_for(reader.read(), 2) == b""

        async with _live_box(port) as box:
            await _expect_local_table_ack(*box, temp=26)
        assert forwarded.empty()
        assert proxy.mode_manager.runtime_mode.value == "offline"
        assert proxy.mode_manager.fail_count == 2


@pytest.mark.enable_socket
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "first_sender"), [("hybrid", "box"), ("hybrid", "cloud"), ("online", None)],
)
async def test_cloud_disconnect_does_not_fallback_after_traffic_or_in_online_mode(
    make_config, mode, first_sender,
) -> None:
    request = _frame("tbl_actual", TEST_BOX, Temp=25)
    cloud_prefix = b"<Frame><Result>"
    forwarded = asyncio.get_running_loop().create_future()

    async def cloud_peer(reader, writer):
        try:
            if first_sender == "box":
                forwarded.set_result(await reader.readexactly(len(request)))
            elif first_sender == "cloud":
                writer.write(cloud_prefix)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with _live_proxy(make_config, cloud_peer, mode=mode, hybrid_fail_threshold=2) as (proxy, port):
        async with _live_box(port) as (reader, writer):
            if first_sender == "box":
                writer.write(request)
                await writer.drain()
                assert await asyncio.wait_for(forwarded, 2) == request
            elif first_sender == "cloud":
                forwarded_prefix = await asyncio.wait_for(reader.readexactly(len(cloud_prefix)), 2)
                assert forwarded_prefix == cloud_prefix
            assert await asyncio.wait_for(reader.read(), 2) == b""
        assert proxy.mode_manager.runtime_mode.value == "online"
        assert proxy.mode_manager.fail_count == (1 if mode == "hybrid" else 0)


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


@pytest.mark.enable_socket
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["online", "hybrid"])
async def test_live_cloud_socket_replaces_split_poll_and_correlates_forwarded_traffic(make_config, mode) -> None:
    first = _frame("tbl_actual", TEST_BOX, Temp=25)
    last = _frame("tbl_batt_prms", TEST_BOX, BAT_MIN=20)
    reply = build_frame("<Result>ACK</Result>").encode()
    cloud_tail = asyncio.get_running_loop().create_future()

    async def cloud_peer(reader, writer):
        try:
            assert await reader.readexactly(len(first)) == first
            writer.write(reply)
            await writer.drain()
            cloud_tail.set_result(await reader.read())
        finally:
            writer.close()
            await writer.wait_closed()

    cloud = await asyncio.start_server(cloud_peer, "127.0.0.1", 0)
    config = make_config(proxy_mode=mode, cloud_port=cloud.sockets[0].getsockname()[1])
    proxy, _delivery, queue, _clock = _qualified_transport(config)
    collector = MagicMock()
    proxy.telemetry_collector = collector
    capture = MagicMock()
    proxy.frame_capture = capture
    listener = await asyncio.start_server(proxy._handle_box_connection, "127.0.0.1", 0)
    box_reader, box_writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
    try:
        box_writer.write(first + POLL[:13])
        await box_writer.drain()
        assert await asyncio.wait_for(box_reader.readexactly(len(reply)), 2) == reply
        queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
        box_writer.write(POLL[13:] + last[:19])
        await box_writer.drain()
        setting = await asyncio.wait_for(box_reader.readuntil(b"</Frame>\r\n"), 2)
        fields = native_frame_fields(setting)
        assert fields is not None and (fields["Reason"], fields["TblItem"], fields["NewValue"]) == ("Setting", "MODE", "3")
        box_writer.write(last[19:] + b"<Frame>incomplete")
        await box_writer.drain()
        box_writer.write_eof()
        assert await asyncio.wait_for(cloud_tail, 2) == last + b"<Frame>incomplete"
        requests = [call.args for call in collector.record_request.call_args_list]
        assert [table for table, _ in requests] == ["tbl_actual", "tbl_batt_prms"]
        assert requests[0][1] == requests[1][1] == collector.record_response.call_args.kwargs["conn_id"]
        capture_ids = {call.kwargs["direction"]: call.kwargs["conn_id"] for call in capture.capture.call_args_list}
        assert capture_ids["box_to_cloud"] != capture_ids["cloud_to_box"]
    finally:
        box_writer.close()
        await box_writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await proxy.stop()
        cloud.close()
        await cloud.wait_closed()


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_cloud_half_close_preserves_partial_box_frame(make_config) -> None:
    first = _frame("tbl_actual", TEST_BOX, Temp=25)
    partial = _frame("tbl_batt_prms", TEST_BOX, BAT_MIN=20)[:-12]
    cloud_tail = asyncio.get_running_loop().create_future()

    async def cloud_peer(reader, writer):
        try:
            assert await reader.readexactly(len(first)) == first
            writer.write_eof()
            cloud_tail.set_result(await reader.read())
        finally:
            writer.close()
            await writer.wait_closed()

    cloud = await asyncio.start_server(cloud_peer, "127.0.0.1", 0)
    proxy = ProxyServer(make_config(proxy_mode="online", cloud_port=cloud.sockets[0].getsockname()[1]))
    listener = await asyncio.start_server(proxy._handle_box_connection, "127.0.0.1", 0)
    box_reader, box_writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
    try:
        box_writer.write(first + partial)
        await box_writer.drain()
        assert await asyncio.wait_for(box_reader.read(), 2) == b""
        assert await asyncio.wait_for(cloud_tail, 2) == partial
    finally:
        box_writer.close()
        await box_writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await proxy.stop()
        cloud.close()
        await cloud.wait_closed()


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
async def test_hybrid_connection_error_switches_to_offline(make_config, dummy_writer_factory) -> None:
    cfg = make_config(proxy_mode="hybrid", hybrid_fail_threshold=1)
    server = ProxyServer(cfg)

    box_reader = asyncio.StreamReader()
    box_reader.feed_eof()
    box_writer = dummy_writer_factory()

    with patch("proxy.server.asyncio.open_connection", side_effect=OSError("cloud down")):
        await server._handle_box_connection(box_reader, box_writer)

    assert server.mode_manager.is_offline() is True
    assert server.mode_manager.fail_count == 1


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_hybrid_refused_cloud_connect_uses_local_ack_below_threshold(make_config) -> None:
    async with await asyncio.start_server(lambda *_args: None, "127.0.0.1", 0) as cloud:
        cloud_port = cloud.sockets[0].getsockname()[1]
        proxy = ProxyServer(make_config(proxy_mode="hybrid", cloud_port=cloud_port, hybrid_fail_threshold=2))
        async with await asyncio.start_server(proxy._handle_box_connection, "127.0.0.1", 0) as listener:
            cloud.close()
            await cloud.wait_closed()
            try:
                async with _live_box(listener.sockets[0].getsockname()[1]) as box:
                    await _expect_local_table_ack(*box, temp=24)
                    assert proxy.cloud_errors == 1
                    assert proxy.mode_manager.fail_count == 1
                    assert proxy.mode_manager.runtime_mode.value == "online"
            finally:
                await proxy.stop()


@pytest.mark.asyncio
async def test_stop_cleans_up_box_while_connecting_to_cloud(make_config, dummy_writer_factory) -> None:
    proxy = ProxyServer(make_config(proxy_mode="hybrid", cloud_connect_timeout=60))
    box_writer = dummy_writer_factory()
    connecting = asyncio.Event()

    async def delayed_cloud_connect(*_args):
        connecting.set()
        await asyncio.Future()

    with patch("proxy.server.asyncio.open_connection", new=delayed_cloud_connect):
        connection = asyncio.create_task(proxy._handle_box_connection(asyncio.StreamReader(), box_writer))
        try:
            await asyncio.wait_for(connecting.wait(), 2)
            assert proxy.is_box_connected()
        finally:
            await asyncio.wait_for(proxy.stop(), 2)

    assert connection.cancelled()
    assert box_writer.is_closing()
    assert not proxy.is_box_connected()
    assert not proxy._active_connections
    assert proxy._active_connection_count == 0


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
        waiting = queue.get_pending(device_id=TEST_BOX, unsent_only=True)
        if outcome == "timeout":
            replay, = waiting
            assert replay.retried and (replay.table, replay.key, replay.value) == ("tbl_box_prms", "MODE", 3)
        else:
            assert not waiting
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
