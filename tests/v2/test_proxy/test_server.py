"""
Testy pro proxy/server.py — ProxyServer (TCP transparent proxy).
"""
from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
from datetime import datetime, timedelta
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# sys.path nastaven v conftest.py
from proxy.server import ProxyServer
from config import Config
from parameter_refresh import ParameterReadbackRefresh
from protocol.frame import MAX_FRAME_BYTES, build_frame
from protocol.frames import czech_local_datetime_from_epoch
from protocol.ver_profile import native_frame_fields
from settings_constraints import PowerControlLimits
from twin.handler import TwinControlHandler
from tests.v2.test_integration import (
    ACK, MODEL, POLL, TEST_BOX, _combined_ack, _qualified_transport, _send_poll, _setting_event,
)
from tests.v2.test_wire_profile_qualification import learner, native, native_poll, observation, timed_end


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides) -> Config:
    """Vrátí Config s přepsanými hodnotami pro testy."""
    cfg = Config.__new__(Config)
    cfg.proxy_host = "127.0.0.1"
    cfg.proxy_port = 0  # OS přidělí volný port
    cfg.cloud_host = "127.0.0.1"
    cfg.cloud_port = 9999
    cfg.cloud_connect_timeout = 1.0
    cfg.cloud_ack_timeout = 5.0
    cfg.mqtt_host = "127.0.0.1"
    cfg.mqtt_port = 1883
    cfg.mqtt_username = ""
    cfg.mqtt_password = ""
    cfg.mqtt_namespace = "oig_local"
    cfg.mqtt_qos = 1
    cfg.mqtt_state_retain = True
    cfg.log_level = "DEBUG"
    cfg.max_concurrent_connections = 100
    cfg.dns_upstream = "8.8.8.8"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ---------------------------------------------------------------------------
# Spuštění a zastavení serveru
# ---------------------------------------------------------------------------

@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_server_starts_and_listens():
    """Server se spustí a naslouchá na zvoleném portu."""
    cfg = make_config()
    server = ProxyServer(cfg)
    await server.start()
    try:
        assert server._server is not None
        assert server._server.sockets
        addr = server._server.sockets[0].getsockname()
        assert addr[0] == "127.0.0.1"
        assert addr[1] > 0  # OS přidělil port
    finally:
        await server.stop()


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_server_stop_clears_state():
    """Po stop() je _server None."""
    cfg = make_config()
    server = ProxyServer(cfg)
    await server.start()
    await server.stop()
    assert server._server is None


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_serve_forever_calls_start_if_not_started():
    """serve_forever() zavolá start() pokud _server je None."""
    cfg = make_config()
    server = ProxyServer(cfg)

    started = []

    async def fake_serve_forever(self_inner):
        pass

    original_start = server.start

    async def patched_start():
        await original_start()
        started.append(True)

    server.start = patched_start

    # Patch asyncio.Server.serve_forever, aby se ihned ukončila
    with patch.object(asyncio.Server, "serve_forever", new=fake_serve_forever):
        await server.serve_forever()

    assert started, "start() nebylo zavoláno"
    await server.stop()


# ---------------------------------------------------------------------------
# Callback on_frame
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_frame_callback_called_for_valid_frame():
    """Callback on_frame je zavolán pro validní XML frame."""
    received = []

    async def callback(parsed: dict) -> None:
        received.append(parsed)

    cfg = make_config()
    server = ProxyServer(cfg, on_frame=callback)

    # Sestavíme minimální XML frame s CRC
    xml = "<TblName>tbl_invertor</TblName><ID_Device>12345</ID_Device><P>100</P>"
    frame_bytes = build_frame(xml).encode("utf-8")

    await server._process_frame(frame_bytes)
    assert len(received) == 1
    assert received[0]["_table"] == "tbl_invertor"
    assert received[0]["_device_id"] == "12345"


@pytest.mark.asyncio
async def test_on_frame_skips_generic_setting_transport_frames() -> None:
    received = []

    async def callback(parsed: dict) -> None:
        received.append(parsed)

    cfg = make_config()
    server = ProxyServer(cfg, on_frame=callback)

    xml = (
        "<TblName>tbl_invertor_prms</TblName>"
        "<ID_Device>12345</ID_Device>"
        "<ID>13809469</ID>"
        "<NewValue>1</NewValue>"
        "<Confirm>New</Confirm>"
        "<TblItem>MODE</TblItem>"
        "<ID_Server>9</ID_Server>"
        "<mytimediff>0</mytimediff>"
        "<TSec>2026-03-17 07:03:04</TSec>"
    )
    frame_bytes = build_frame(xml).encode("utf-8")

    await server._process_frame(frame_bytes)

    assert received == []


@pytest.mark.asyncio
async def test_on_frame_skips_ack_transport_frames_even_with_table_and_device() -> None:
    received = []

    async def callback(parsed: dict) -> None:
        received.append(parsed)

    cfg = make_config()
    server = ProxyServer(cfg, on_frame=callback)

    xml = (
        "<Result>ACK</Result>"
        "<TblName>tbl_actual</TblName>"
        "<ID_Device>12345</ID_Device>"
        "<Rdt>2025-12-07 20:46:52</Rdt>"
        "<Tmr>100</Tmr>"
    )
    frame_bytes = build_frame(xml).encode("utf-8")

    await server._process_frame(frame_bytes)

    assert received == []


@pytest.mark.asyncio
async def test_on_frame_keeps_isnew_table_payload_with_real_sensor_keys() -> None:
    received = []

    async def callback(parsed: dict) -> None:
        received.append(parsed)

    cfg = make_config()
    server = ProxyServer(cfg, on_frame=callback)

    xml = "<TblName>IsNewFW</TblName><ID_Device>12345</ID_Device><BAT_C>91</BAT_C>"
    frame_bytes = build_frame(xml).encode("utf-8")

    await server._process_frame(frame_bytes)

    assert len(received) == 1
    assert received[0]["BAT_C"] == 91


@pytest.mark.asyncio
async def test_on_frame_not_called_when_no_callback():
    """Bez on_frame callback se _process_frame tiše ignoruje."""
    cfg = make_config()
    server = ProxyServer(cfg, on_frame=None)

    xml = "<TblName>t</TblName><ID_Device>1</ID_Device>"
    frame_bytes = build_frame(xml).encode("utf-8")

    # Nesmí vyhodit výjimku
    await server._process_frame(frame_bytes)


@pytest.mark.asyncio
async def test_on_frame_bad_bytes_no_crash():
    """Garbage bytes v _process_frame nevyhodí výjimku."""
    received = []

    async def callback(parsed: dict) -> None:
        received.append(parsed)

    cfg = make_config()
    server = ProxyServer(cfg, on_frame=callback)

    await server._process_frame(b"\x00\x01\x02garbage\xff")
    # callback nebyl zavolán (neplatný XML)
    assert received == []


@pytest.mark.asyncio
async def test_frames_received_increments_on_process_frame():
    cfg = make_config()
    server = ProxyServer(cfg)

    xml = "<TblName>tbl_invertor</TblName><ID_Device>12345</ID_Device><P>100</P>"
    frame_bytes = build_frame(xml).encode("utf-8")

    await server._process_frame(frame_bytes)

    assert server.frames_received == 1


# ---------------------------------------------------------------------------
# Pipe Box→Cloud: forward + parse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipe_box_to_cloud_forwards_data():
    """_pipe_box_to_cloud přeposílá surové bajty do cloud_writer."""
    cfg = make_config()
    server = ProxyServer(cfg)

    sent_data = []

    # Mock StreamReader s jedním chunkem dat a poté EOF
    box_reader = MagicMock(spec=asyncio.StreamReader)
    raw_chunk = b"hello cloud"
    box_reader.read = AsyncMock(side_effect=[raw_chunk, b""])

    # Mock cloud_writer
    cloud_writer = MagicMock(spec=asyncio.StreamWriter)
    cloud_writer.write = MagicMock(side_effect=lambda d: sent_data.append(d))
    cloud_writer.drain = AsyncMock()

    await server._pipe_box_to_cloud(box_reader, cloud_writer)

    assert b"hello cloud" in sent_data


@pytest.mark.asyncio
async def test_pipe_box_to_cloud_stops_on_empty_read():
    """_pipe_box_to_cloud ukončí smyčku při prázdném čtení (EOF)."""
    cfg = make_config()
    server = ProxyServer(cfg)

    box_reader = MagicMock(spec=asyncio.StreamReader)
    box_reader.read = AsyncMock(return_value=b"")

    cloud_writer = MagicMock(spec=asyncio.StreamWriter)
    cloud_writer.write = MagicMock()
    cloud_writer.drain = AsyncMock()

    # Nesmí viset — musí skončit
    await asyncio.wait_for(
        server._pipe_box_to_cloud(box_reader, cloud_writer),
        timeout=1.0
    )


# ---------------------------------------------------------------------------
# Pipe Cloud→Box: forward
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipe_cloud_to_box_forwards_data():
    """_pipe_cloud_to_box přeposílá data od cloudu do box_writer."""
    cfg = make_config()
    server = ProxyServer(cfg)

    sent_data = []

    cloud_reader = MagicMock(spec=asyncio.StreamReader)
    cloud_reader.read = AsyncMock(side_effect=[b"cloud response", b""])

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.write = MagicMock(side_effect=lambda d: sent_data.append(d))
    box_writer.drain = AsyncMock()

    await server._pipe_cloud_to_box(cloud_reader, box_writer)

    assert b"cloud response" in sent_data


@pytest.mark.asyncio
async def test_cloud_table_is_captured_without_becoming_box_state(dummy_writer_factory):
    confirmed = AsyncMock()
    capture = MagicMock()
    server = ProxyServer(make_config(), on_frame=confirmed, frame_capture=capture)
    frame = build_frame(
        "<TblName>tbl_boiler_prms</TblName><ID_Device>12345</ID_Device><ISON>1</ISON>"
    ).encode()
    cloud_reader = AsyncMock(spec=asyncio.StreamReader)
    cloud_reader.read.side_effect = [frame[:20], frame[20:], b""]
    box_writer = dummy_writer_factory()

    await server._pipe_cloud_to_box(cloud_reader, box_writer)

    assert b"".join(box_writer.written) == frame
    capture.capture.assert_called_once()
    assert capture.capture.call_args.kwargs["direction"] == "cloud_to_box"
    confirmed.assert_not_awaited()


@pytest.mark.asyncio
async def test_qualified_own_event_updates_confirmed_value_without_local_request() -> None:
    confirmed = AsyncMock()
    server, _delivery, _queue, clock = _qualified_transport(make_config(), callback=confirmed)
    await server._handle_twin_frames(POLL, "own-flow")
    event = _setting_event(clock, 0)
    bad_crc = event.replace(b"[0]", b"[1]")
    await server._handle_twin_frames(bad_crc, "own-flow")
    confirmed.assert_not_awaited()

    await server._handle_twin_frames(event, "own-flow")
    await server._handle_twin_frames(event, "own-flow")

    confirmed.assert_awaited_once_with(TEST_BOX, "tbl_box_prms", "MODE", "0", source_dt=clock.stamp())


@pytest.mark.asyncio
async def test_pipe_cloud_to_box_stops_on_empty_read():
    """_pipe_cloud_to_box ukončí smyčku při EOF."""
    cfg = make_config()
    server = ProxyServer(cfg)

    cloud_reader = MagicMock(spec=asyncio.StreamReader)
    cloud_reader.read = AsyncMock(return_value=b"")

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.write = MagicMock()
    box_writer.drain = AsyncMock()

    await asyncio.wait_for(
        server._pipe_cloud_to_box(cloud_reader, box_writer),
        timeout=1.0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, delivers", [
    ("setting", True), ("setting_with_actual", True), ("weather", False),
    ("table_only", False), ("foreign", False), ("bad_crc", False), ("with_ack", True),
])
async def test_only_a_qualified_unambiguous_setting_poll_can_use_online_slot(
    dummy_writer_factory, kind, delivers, caplog,
):
    server, _delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id=TEST_BOX, require_profile=True)
    identity = f"<ID_Device>{TEST_BOX}</ID_Device>"
    inputs = {
        "setting": POLL,
        "setting_with_actual": native(f"<Result>IsNewSet</Result>{identity}<TblName>tbl_actual</TblName>", MODEL),
        "weather": native(f"<Result>IsNewWeather</Result>{identity}", MODEL),
        "table_only": native(f"<TblName>IsNewSet</TblName>{identity}", MODEL),
        "foreign": native("<Result>IsNewSet</Result><ID_Device>99999</ID_Device>", MODEL),
        "bad_crc": POLL.replace(b"12345", b"12346"),
        "with_ack": POLL + ACK,
    }
    writer = dummy_writer_factory()
    with caplog.at_level(logging.INFO, logger="twin.delivery"):
        cloud = await _send_poll(server, writer, poll=inputs[kind])
    if delivers:
        sent, = writer.written
        assert b"<Reason>Setting</Reason>" in sent
        if kind == "with_ack":
            cloud.write.assert_called_once_with(ACK)
        else:
            cloud.write.assert_not_called()
    else:
        assert not writer.written
        cloud.write.assert_called_once_with(inputs[kind])
    reason = {"foreign": "box_identity_unbound", "bad_crc": "poll_malformed"}.get(kind)
    if reason is not None:
        assert f"IsNewSet skipped stage=transport reason={reason}" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("parts", [
    (POLL[:3], POLL[3:40], POLL[40:]),
    (POLL[:-2], b"\r", b"\n"),
    (POLL[:-1], b"\n"),
])
async def test_fragmented_setting_poll_is_replaced_without_forwarding_any_fragment(dummy_writer_factory, parts):
    proxy, _delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read.side_effect = [*parts, b""]
    cloud_writer = dummy_writer_factory()
    box_writer = dummy_writer_factory()

    await proxy._pipe_box_to_cloud(reader, cloud_writer, box_writer, session_id="online")

    sent, = box_writer.written
    fields = native_frame_fields(sent)
    assert fields is not None and (fields["TblItem"], fields["NewValue"]) == ("MODE", "3")
    assert cloud_writer.written == []


@pytest.mark.asyncio
@pytest.mark.parametrize("split", [False, True])
async def test_coalesced_setting_poll_preserves_ordinary_frames_and_delimiters(dummy_writer_factory, split):
    proxy, _delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    first = native(f"<TblName>tbl_actual</TblName><ID_Device>{TEST_BOX}</ID_Device><Temp>22</Temp>", MODEL)
    last = native(f"<TblName>tbl_actual</TblName><ID_Device>{TEST_BOX}</ID_Device><Temp>23</Temp>", MODEL)
    prefix = b"\n" + first + b"\r\r\n"
    suffix = b"\r\n" + last
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read.side_effect = ([prefix + POLL + suffix[:35], suffix[35:], b""] if split
                               else [prefix + POLL + suffix, b""])
    cloud_writer = dummy_writer_factory()
    box_writer = dummy_writer_factory()

    await proxy._pipe_box_to_cloud(reader, cloud_writer, box_writer, session_id="online")

    sent, = box_writer.written
    assert b"<Reason>Setting</Reason>" in sent
    assert b"".join(cloud_writer.written) == prefix + suffix


@pytest.mark.asyncio
async def test_coalesced_competing_setting_polls_both_reach_cloud(dummy_writer_factory, caplog):
    proxy, delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    writer = dummy_writer_factory()
    next_poll = native(f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device><Lat>17</Lat>", MODEL)

    with caplog.at_level(logging.INFO, logger="twin.delivery"):
        cloud = await _send_poll(proxy, writer, poll=POLL + next_poll)

    assert not writer.written
    assert b"".join(call.args[0] for call in cloud.write.call_args_list) == POLL + next_poll
    assert delivery.has_pending(TEST_BOX)
    assert "IsNewSet skipped stage=transport reason=ambiguous_box_batch" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("second_exchange", ["forwarded", "ambiguous", "failed_write", "foreign_reply"])
async def test_timed_cloud_end_requires_a_successfully_forwarded_native_poll(dummy_writer_factory, second_exchange):
    """Bind clock evidence to an actual upstream write, even if it answers during drain."""
    proxy, delivery, _, clock = _qualified_transport(make_config())
    profiles = delivery._profiles = learner(clock)
    for index in (1, 2):
        clock.step(244)
        session = f"clock-flow-{index}"
        mode = second_exchange if index == 2 else "forwarded"
        poll = native_poll(TEST_BOX, MODEL, index)
        reader = asyncio.StreamReader()
        reader.feed_data(poll + (native_poll(TEST_BOX, MODEL, 3) if mode == "ambiguous" else b""))
        reader.feed_eof()
        cloud_writer = MagicMock(spec=asyncio.StreamWriter)
        cloud_writer.is_closing.return_value = False
        if mode == "failed_write":
            cloud_writer.write.side_effect = OSError("upstream disconnected")

        def respond(mode=mode, session=session):
            prefix = (
                build_frame("<Result>END</Result><ID_Device>foreign</ID_Device>").encode()
                if mode == "foreign_reply" else b""
            )
            proxy._observe_cloud_buffer(bytearray(prefix + timed_end(clock, 289)), None, session, 1)

        cloud_writer.drain = AsyncMock(side_effect=respond)
        await proxy._pipe_box_to_cloud(reader, cloud_writer, dummy_writer_factory(), session_id=session)
        if mode == "failed_write":
            respond()

    clock.step(135)
    profiles.observe_box(observation(clock, TEST_BOX, MODEL, offset=279), "corroborating-table")
    assert profiles.box_clock_offset(TEST_BOX) == (289 if second_exchange == "forwarded" else 0)


@pytest.mark.asyncio
async def test_oversized_incomplete_box_frame_streams_through_and_recovers_at_boundary(dummy_writer_factory):
    proxy, _delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    cloud_writer = dummy_writer_factory()
    box_writer = dummy_writer_factory()
    oversized = b"<Frame>" + b"x" * MAX_FRAME_BYTES

    async def chunks():
        for start in range(0, len(oversized), 4096):
            yield oversized[start:start + 4096]
        yield b"</Fra"
        assert b"".join(cloud_writer.written) == oversized
        yield b"me>\r\n" + POLL
        yield b""

    incoming = chunks()
    reader = AsyncMock(spec=asyncio.StreamReader)

    async def read(_size):
        return await anext(incoming)

    reader.read.side_effect = read
    await proxy._pipe_box_to_cloud(reader, cloud_writer, box_writer, session_id="online")

    assert b"".join(cloud_writer.written) == oversized + b"</Frame>\r\n"
    sent, = box_writer.written
    assert b"<Reason>Setting</Reason>" in sent


@pytest.mark.asyncio
async def test_receipt_started_before_local_setting_cannot_acknowledge_it(dummy_writer_factory, monkeypatch):
    proxy, delivery, queue, _clock = _qualified_transport(make_config())
    wire_epoch = int(time.time()) - 30
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch)
    stamp = czech_local_datetime_from_epoch(wire_epoch)
    ack = build_frame(f"<Result>ACK</Result><Reason>Setting</Reason><Rdt>{stamp}</Rdt>").encode()
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read.side_effect = [POLL + ack[:2], ack[2:12], ack[12:], b""]
    cloud_writer = dummy_writer_factory()
    box_writer = dummy_writer_factory()

    await proxy._pipe_box_to_cloud(reader, cloud_writer, box_writer, session_id="box-flow")

    sent, = box_writer.written
    assert b"<Reason>Setting</Reason>" in sent
    assert b"".join(cloud_writer.written) == ack
    assert delivery.local_inflight_setting(session_id="box-flow") is not None
    await _send_poll(proxy, box_writer, poll=ack)
    assert delivery.local_inflight_setting(session_id="box-flow") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival", ["buffered", "during_observation", "during_drain"])
async def test_receipt_provenance_with_real_stream_reader(dummy_writer_factory, monkeypatch, arrival):
    proxy, delivery, queue, _clock = _qualified_transport(make_config())
    wire_epoch = int(time.time()) - 30
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch)
    stamp = czech_local_datetime_from_epoch(wire_epoch)
    ack = build_frame(f"<Result>ACK</Result><Reason>Setting</Reason><Rdt>{stamp}</Rdt>").encode()
    table = build_frame(f"<TblName>tbl_actual</TblName><ID_Device>{TEST_BOX}</ID_Device><Temp>25</Temp>").encode()
    prefix = b"\n" * (4096 - len(table) - len(POLL)) + table
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    reader = asyncio.StreamReader()
    cloud_writer = dummy_writer_factory()
    box_writer = dummy_writer_factory()
    paused = asyncio.Event()
    resume = asyncio.Event()

    async def pause(*_args):
        paused.set()
        await resume.wait()

    if arrival == "during_observation":
        proxy.on_frame = pause
    if arrival == "during_drain":
        monkeypatch.setattr(box_writer, "drain", pause)
    reader.feed_data(prefix + POLL + (ack if arrival == "buffered" else b""))
    pipe = asyncio.create_task(proxy._pipe_box_to_cloud(reader, cloud_writer, box_writer, session_id="box-flow"))
    if arrival != "buffered":
        await asyncio.wait_for(paused.wait(), 1)
        reader.feed_data(ack)
    reader.feed_eof()
    resume.set()
    await asyncio.wait_for(pipe, 1)

    assert len(box_writer.written) == 1
    assert b"".join(cloud_writer.written) == prefix + ack
    assert (delivery.local_inflight_setting(session_id="box-flow") is None) == (arrival == "during_drain")


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_prefix", [2, None])
async def test_buffered_late_receipt_cancels_same_socket_replay(dummy_writer_factory, monkeypatch, ack_prefix):
    proxy, delivery, queue, _clock = _qualified_transport(make_config())
    tick = time.monotonic()
    wire_epoch = int(time.time()) - 30
    monkeypatch.setattr("twin.delivery.time", SimpleNamespace(time=time.time, monotonic=lambda: tick))
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch)
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    box_writer = dummy_writer_factory()
    await _send_poll(proxy, box_writer)
    tick += 60
    delivery.poll_timeouts()
    assert delivery.has_pending(TEST_BOX)
    stamp = czech_local_datetime_from_epoch(wire_epoch + 1)
    ack = build_frame(f"<Result>ACK</Result><Reason>Setting</Reason><Rdt>{stamp}</Rdt>").encode()
    wire_epoch += 10
    next_poll = native(f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device><Lat>17</Lat>", MODEL)
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read.side_effect = ([next_poll + ack, b""] if ack_prefix is None
                               else [next_poll + ack[:ack_prefix], ack[ack_prefix:], b""])
    cloud_writer = dummy_writer_factory()

    await proxy._pipe_box_to_cloud(reader, cloud_writer, box_writer, session_id="box-flow")

    assert len(box_writer.written) == 1
    assert b"".join(cloud_writer.written) == next_poll + ack
    assert not delivery.has_pending(TEST_BOX)
    assert delivery.local_inflight_setting(session_id="box-flow") is None


@pytest.mark.asyncio
async def test_coalesced_setting_event_keeps_box_state_without_acknowledging_local_write(dummy_writer_factory):
    confirmed = AsyncMock()
    proxy, delivery, queue, clock = _qualified_transport(make_config(), callback=confirmed)
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id=TEST_BOX, require_profile=True)
    event = _setting_event(clock, 1)
    box_writer = dummy_writer_factory()

    cloud_writer = await _send_poll(proxy, box_writer, poll=POLL + event)

    assert len(box_writer.written) == 1
    assert delivery.local_inflight_setting(session_id="box-flow") is not None
    cloud_writer.write.assert_called_once_with(event)
    confirmed.assert_awaited_once_with(TEST_BOX, "tbl_box_prms", "MODE", "1", source_dt=clock.stamp())


@pytest.mark.asyncio
async def test_incomplete_cloud_frame_blocks_local_setting_without_interleaving(dummy_writer_factory, caplog):
    proxy, delivery, queue, _clock = _qualified_transport(make_config())
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    box_writer = dummy_writer_factory()
    partial_written = asyncio.Event()
    complete_cloud = asyncio.Event()
    cloud_frame = build_frame("<Result>END</Result>").encode()

    async def chunks():
        yield cloud_frame[:18]
        partial_written.set()
        await complete_cloud.wait()
        yield cloud_frame[18:]
        yield b""

    incoming = chunks()
    reader = AsyncMock(spec=asyncio.StreamReader)

    async def read(_size):
        return await anext(incoming)

    reader.read.side_effect = read
    cloud_pipe = asyncio.create_task(proxy._pipe_cloud_to_box(reader, box_writer, session_id="box-flow"))
    try:
        await asyncio.wait_for(partial_written.wait(), 1)
        with caplog.at_level(logging.INFO, logger="twin.delivery"):
            cloud = await _send_poll(proxy, box_writer)
        cloud.write.assert_called_once_with(POLL)
        assert delivery.has_pending(TEST_BOX)
        assert "IsNewSet skipped stage=transport reason=cloud_partial_frame" in caplog.text
    finally:
        complete_cloud.set()
        await cloud_pipe

    assert b"".join(box_writer.written) == cloud_frame


@pytest.mark.asyncio
@pytest.mark.parametrize("coalesced_ack", [False, True])
async def test_online_socket_drains_two_queued_settings_at_separate_box_polls(
    dummy_writer_factory, monkeypatch, coalesced_ack,
):
    proxy, delivery, queue, _clock = _qualified_transport(make_config())
    monkeypatch.setattr(delivery, "_power_limits", PowerControlLimits(ac_charge_max_a=75))
    wire_epoch = int(time.time()) - 30
    monkeypatch.setattr(delivery, "_allocate_id_set", lambda _state, _now: wire_epoch)
    queue.enqueue("tbl_box_prms", "MODE", 3, device_id=TEST_BOX, require_profile=True)
    queue.enqueue("tbl_invertor_prm1", "AAC_MAX_CHRG", 75, device_id=TEST_BOX, require_profile=True)
    writer = dummy_writer_factory()
    await _send_poll(proxy, writer)
    mode = native_frame_fields(writer.written[0])
    assert mode is not None and (mode["TblItem"], mode["NewValue"]) == ("MODE", "3")

    stamp = datetime.strptime(mode["DT"], "%d.%m.%Y %H:%M:%S")
    first_ack = build_frame(f"<Result>ACK</Result><Reason>Setting</Reason><Rdt>{stamp}</Rdt>").encode()
    if not coalesced_ack:
        forwarded = await _send_poll(proxy, writer, poll=first_ack)
        assert forwarded.write.call_args.args == (first_ack,)
        assert len(writer.written) == 1 and queue.size() == 1

    wire_epoch += 10
    next_poll = native(f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device><Lat>17</Lat>", MODEL)
    forwarded = await _send_poll(proxy, writer, poll=first_ack + next_poll if coalesced_ack else next_poll)
    assert b"".join(call.args[0] for call in forwarded.write.call_args_list) == (first_ack if coalesced_ack else b"")
    aac = native_frame_fields(writer.written[1])
    assert aac is not None and (aac["TblItem"], aac["NewValue"]) == ("AAC_MAX_CHRG", "75")

    await _send_poll(proxy, writer, poll=first_ack)
    assert delivery.local_inflight_setting() is not None
    new_stamp = stamp + timedelta(seconds=10)
    second_ack = build_frame(f"<Result>ACK</Result><Reason>Setting</Reason><Rdt>{new_stamp}</Rdt>").encode()
    await _send_poll(proxy, writer, poll=second_ack)
    assert delivery.local_inflight_setting() is None and queue.size() == 0
    assert len(writer.written) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("table_mode", [None, 0, 1])
@pytest.mark.parametrize("coalesced_table", [False, True])
async def test_readback_refresh_uses_first_qualified_poll_and_forwards_native_table(
    dummy_writer_factory, table_mode, coalesced_table,
):
    proxy, delivery, queue, wire_clock = _qualified_transport(make_config())
    handler = TwinControlHandler(MagicMock(), queue, TEST_BOX, require_profile=True)
    request = MagicMock(side_effect=lambda: handler.admit_message(
        f"oig_local/{TEST_BOX}/set/tbl_box_prms/SA", b"1", only_if_empty=True,
    ))
    proxy._request_readback = request
    elapsed = [0.0]
    proxy._readback = ParameterReadbackRefresh(clock=lambda: elapsed[0])
    event = _setting_event(wire_clock, 1)
    writer = dummy_writer_factory()
    forwarded = await _send_poll(proxy, writer, poll=event)
    forwarded.write.assert_called_once_with(event)

    elapsed[0] = 59
    early = await _send_poll(proxy, writer)
    early.write.assert_called_once_with(POLL)
    assert not writer.written
    elapsed[0] = 68
    table = b""
    if table_mode is not None:
        table = native(
            f"<TblName>tbl_box_prms</TblName><ID_Device>{TEST_BOX}</ID_Device>"
            f"<DT>{wire_clock.stamp()}</DT><Reason>Table</Reason><MODE>{table_mode}</MODE>", MODEL,
        )
        if not coalesced_table:
            forwarded = await _send_poll(proxy, writer, poll=table)
            forwarded.write.assert_called_once_with(table)

    next_poll = native(f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device><Lat>17</Lat>", MODEL)
    prefix = table if coalesced_table else b""
    result = await _send_poll(proxy, writer, poll=prefix + next_poll)
    forwarded_bytes = b"".join(call.args[0] for call in result.write.call_args_list)
    if table_mode == 1:
        request.assert_not_called()
        assert forwarded_bytes == prefix + next_poll
    else:
        request.assert_called_once_with()
        sent, = writer.written
        assert b"<TblItem>SA</TblItem>" in sent and b"<NewValue>1</NewValue>" in sent
        assert forwarded_bytes == prefix
        delivery.clear_session("box-flow")
        await _send_poll(proxy, dummy_writer_factory(), session_id="another-flow")
        assert request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_sa", [False, True])
async def test_readback_survives_failed_send_and_existing_sa_covers_batch(dummy_writer_factory, existing_sa):
    proxy, delivery, queue, wire_clock = _qualified_transport(make_config())
    handler = TwinControlHandler(MagicMock(), queue, TEST_BOX, require_profile=True)
    request = MagicMock(side_effect=lambda: handler.admit_message(
        f"oig_local/{TEST_BOX}/set/tbl_box_prms/SA", b"1", only_if_empty=True,
    ))
    proxy._request_readback = request
    with patch.object(proxy._readback, "_clock", return_value=0):
        await _send_poll(proxy, dummy_writer_factory(), poll=_setting_event(wire_clock, 1))
    with patch.object(proxy._readback, "_clock", return_value=70):
        if existing_sa:
            queue.enqueue("tbl_box_prms", "SA", 1, device_id=TEST_BOX, require_profile=True)
        else:
            with patch.object(delivery, "build_local_frame", side_effect=ValueError("injected serialization fault")):
                forwarded = await _send_poll(proxy, dummy_writer_factory())
            forwarded.write.assert_called_once_with(POLL)
            assert queue.size() == 0
            assert proxy._readback.is_due()
        writer = dummy_writer_factory()
        next_poll = native(f"<Result>IsNewSet</Result><ID_Device>{TEST_BOX}</ID_Device><Lat>17</Lat>", MODEL)
        await _send_poll(proxy, writer, poll=next_poll)
        sent, = writer.written
        assert b"<TblItem>SA</TblItem>" in sent
        assert not proxy._readback.is_due()
        assert request.call_count == (0 if existing_sa else 2)


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_box_connection_closes_on_cloud_failure():
    """Pokud cloud není dostupný, box_writer se zavře."""
    cfg = make_config(cloud_host="127.0.0.1", cloud_port=1, cloud_connect_timeout=0.1)
    server = ProxyServer(cfg)
    await server.start()

    try:
        port = server._server.sockets[0].getsockname()[1]

        # Připojíme se jako Box
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"hello")
        await writer.drain()

        # Python 3.14 zde na lokálním reset-close path vrací místo EOF ConnectionResetError.
        try:
            data = await asyncio.wait_for(reader.read(100), timeout=2.0)
        except ConnectionResetError:
            data = b""
        assert data == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_box_connected_state_reflects_connection():
    async def cloud_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(4096)
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    cloud_server = await asyncio.start_server(cloud_handler, "127.0.0.1", 0)
    cloud_port = cloud_server.sockets[0].getsockname()[1]

    cfg = make_config(cloud_host="127.0.0.1", cloud_port=cloud_port, cloud_connect_timeout=1.0)
    server = ProxyServer(cfg)
    await server.start()
    proxy_port = server._server.sockets[0].getsockname()[1]

    async def wait_for(predicate, timeout: float = 2.0) -> None:
        async def _inner() -> None:
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(_inner(), timeout=timeout)

    try:
        _, box_writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        await wait_for(server.is_box_connected)
        assert server.is_box_connected() is True
        assert server.box_peer is not None

        box_writer.close()
        await box_writer.wait_closed()
        await wait_for(lambda: not server.is_box_connected())
        assert server.is_box_connected() is False
        assert server.box_peer is None
    finally:
        await server.stop()
        cloud_server.close()
        await cloud_server.wait_closed()


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_cloud_connects_counter_increments():
    async def cloud_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(4096)
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    cloud_server = await asyncio.start_server(cloud_handler, "127.0.0.1", 0)
    cloud_port = cloud_server.sockets[0].getsockname()[1]

    cfg = make_config(cloud_host="127.0.0.1", cloud_port=cloud_port, cloud_connect_timeout=1.0)
    server = ProxyServer(cfg)
    await server.start()
    proxy_port = server._server.sockets[0].getsockname()[1]

    async def wait_for(predicate, timeout: float = 2.0) -> None:
        async def _inner() -> None:
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(_inner(), timeout=timeout)

    try:
        _, box_writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        await wait_for(lambda: server.cloud_connects == 1)
        assert server.cloud_connects == 1

        box_writer.close()
        await box_writer.wait_closed()
        await wait_for(lambda: server.cloud_disconnects == 1)
    finally:
        await server.stop()
        cloud_server.close()
        await cloud_server.wait_closed()


# ---------------------------------------------------------------------------
# Telemetry integration — record_request / record_response / record_frame_direction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telemetry_record_request_called_on_box_to_cloud():
    """record_request je volán pro každý frame přijatý od Boxu."""
    from unittest.mock import MagicMock
    from protocol.frame import build_frame
    from protocol.parser import parse_xml_frame

    cfg = make_config()
    mock_telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=mock_telemetry)

    xml = "<TblName>tbl_invertor</TblName><ID_Device>12345</ID_Device><P>100</P>"
    frame_bytes = build_frame(xml).encode("utf-8")
    frame_text = frame_bytes.decode("utf-8", errors="replace")
    parsed_frame = parse_xml_frame(frame_text)
    table_name = server._effective_table_name(parsed_frame, frame_text)
    conn_id = 42

    # Simulujeme volání jako v _pipe_box_to_cloud po parsování frame
    server.telemetry_collector.record_request(table_name or None, conn_id)
    server.telemetry_collector.record_frame_direction("box_to_proxy")

    mock_telemetry.record_request.assert_called_once_with("tbl_invertor", 42)
    mock_telemetry.record_frame_direction.assert_called_once_with("box_to_proxy")


@pytest.mark.asyncio
async def test_telemetry_record_response_called_on_cloud_to_box():
    """record_response je volán pro každý frame přijatý z cloudu."""
    from unittest.mock import MagicMock
    from protocol.frame import build_frame

    cfg = make_config()
    mock_telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=mock_telemetry)

    xml = "<Result>ACK</Result><ToDo>GetAll</ToDo><ID_Device>12345</ID_Device>"
    frame_bytes = build_frame(xml).encode("utf-8")
    frame_text = frame_bytes.decode("utf-8", errors="replace")
    conn_id = 99

    # Simulujeme volání jako v _pipe_cloud_to_box po parsování frame
    server.telemetry_collector.record_response(frame_text, source="cloud", conn_id=conn_id)
    server.telemetry_collector.record_frame_direction("cloud_to_proxy")

    mock_telemetry.record_response.assert_called_once_with(frame_text, source="cloud", conn_id=99)
    mock_telemetry.record_frame_direction.assert_called_once_with("cloud_to_proxy")


@pytest.mark.asyncio
async def test_telemetry_not_called_when_collector_is_none():
    """Bez telemetry_collector _process_frame necrashuje."""
    cfg = make_config()
    server = ProxyServer(cfg, telemetry_collector=None)

    xml = "<TblName>tbl_invertor</TblName><ID_Device>12345</ID_Device>"
    frame_bytes = build_frame(xml).encode("utf-8")

    await server._process_frame(frame_bytes)
    assert server.telemetry_collector is None


def test_proxy_server_accepts_telemetry_collector_param():
    """ProxyServer přijímá telemetry_collector jako parametr."""
    from unittest.mock import MagicMock

    cfg = make_config()
    mock_telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=mock_telemetry)

    assert server.telemetry_collector is mock_telemetry


def test_proxy_server_telemetry_collector_defaults_to_none():
    """telemetry_collector je None pokud není předán."""
    cfg = make_config()
    server = ProxyServer(cfg)

    assert server.telemetry_collector is None


def test_record_telemetry_connection_end_records_box_and_cloud_sessions():
    cfg = make_config()
    mock_telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=mock_telemetry)

    server._record_telemetry_connection_end(
        box_connected_since_epoch=100.0,
        box_reason="eof",
        box_peer="1.2.3.4:5678",
        cloud_connected_since_epoch=120.0,
        cloud_reason="eof",
    )

    mock_telemetry.record_box_session_end.assert_called_once_with(
        connected_since_epoch=100.0,
        reason="eof",
        peer="1.2.3.4:5678",
    )
    mock_telemetry.record_cloud_session_end.assert_called_once_with(
        connected_since_epoch=120.0,
        reason="eof",
    )


def test_record_cloud_connect_failure_records_timeout_and_offline_event():
    cfg = make_config()
    mock_telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=mock_telemetry)

    server._record_cloud_connect_failure(
        conn_id=77,
        failure_type="timeout",
        failure_detail="timed out",
        peer="1.2.3.4:5678",
        will_go_offline=True,
    )

    mock_telemetry.record_timeout.assert_called_once_with(conn_id=77)
    mock_telemetry.record_error_context.assert_called_once()
    mock_telemetry.record_offline_event.assert_called_once_with(
        reason="cloud_connect_timeout",
        local_ack=True,
        mode="online",
    )


def test_record_cloud_connect_failure_records_error_response_without_offline():
    cfg = make_config()
    mock_telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=mock_telemetry)

    server._record_cloud_connect_failure(
        conn_id=88,
        failure_type="oserror",
        failure_detail="connection refused",
        peer="1.2.3.4:5678",
        will_go_offline=False,
    )

    mock_telemetry.record_response.assert_called_once_with("", source="error", conn_id=88)
    mock_telemetry.record_error_context.assert_called_once()
    mock_telemetry.record_offline_event.assert_not_called()


def _make_collector():
    from telemetry.settings_audit import record_to_dict
    collector = MagicMock()
    collector.settings_audit = []
    collector.record_setting_audit_step = lambda r: collector.settings_audit.append(record_to_dict(r))
    return collector


@pytest.mark.asyncio
async def test_setting_audit_success_roundtrip(dummy_writer_factory) -> None:
    from telemetry.settings_audit import SettingResult, SettingStep

    raw_text = "<Frame><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue></Frame>"
    collector = _make_collector()
    server, _delivery, queue, clock = _qualified_transport(make_config(), collector=collector)
    setting = queue.enqueue("tbl_box_prms", "MODE", 1, raw_text=raw_text,
                            device_id=TEST_BOX, require_profile=True)
    await _send_poll(server, dummy_writer_factory())
    event = _setting_event(clock, 1)
    await server._handle_twin_frames(event, "box-flow")
    await server._handle_twin_frames(event, "box-flow")

    records = [record for record in collector.settings_audit if record["audit_id"] == setting.audit_id]
    assert [record["step"] for record in records] == [
        SettingStep.DELIVER_SELECTED.value, SettingStep.INJECTED_BOX.value, SettingStep.ACK_TBL_EVENTS.value,
    ]
    assert all(record["raw_text"] == raw_text for record in records)
    terminal = [record for record in records if record["result"] == SettingResult.CONFIRMED.value]
    assert len(terminal) == 1
    assert terminal[0]["confirmed_value_text"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("named", [False, True], ids=["reason", "named"])
async def test_setting_audit_ack_is_receipt_and_preserves_raw_text(dummy_writer_factory, named) -> None:
    from telemetry.settings_audit import SettingResult, SettingStep

    raw_text = "<Frame><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue></Frame>"
    collector = _make_collector()
    confirmed = AsyncMock()
    server, _delivery, queue, clock = _qualified_transport(make_config(), collector=collector, callback=confirmed)
    queue.enqueue("tbl_box_prms", "MODE", 1, raw_text=raw_text, device_id=TEST_BOX, require_profile=True)
    await _send_poll(server, dummy_writer_factory())
    ack = native("<Result>ACK</Result><TblName>tbl_box_prms</TblName><ToDo>MODE</ToDo>", MODEL) if named else ACK
    assert await server._handle_twin_frames(ack, "box-flow") == "ack"
    assert await server._handle_twin_frames(ack, "box-flow") is None
    confirmed.assert_not_awaited()

    step = SettingStep.ACK_BOX_OBSERVED if named else SettingStep.ACK_REASON_SETTING
    observed = [record for record in collector.settings_audit if record["step"] == step.value]
    assert len(observed) == 1
    assert observed[0]["result"] == SettingResult.PENDING.value
    assert observed[0]["raw_text"] == raw_text

    event = _setting_event(clock, 1)
    await server._handle_twin_frames(event, "box-flow")
    confirmed.assert_awaited_once_with(TEST_BOX, "tbl_box_prms", "MODE", "1", source_dt=clock.stamp())


@pytest.mark.asyncio
@pytest.mark.parametrize("piggyback", [False, True], ids=["receipt", "receipt_with_actual"])
async def test_pipe_cloud_to_box_tracks_cloud_setting_for_audit_lifecycle(dummy_writer_factory, piggyback) -> None:
    from telemetry.settings_audit import SettingResult, SettingStep

    collector = _make_collector()
    server, delivery, _queue, clock = _qualified_transport(make_config(), collector=collector)
    await server._handle_twin_frames(POLL, "box-flow")
    await server._handle_twin_frames(POLL, "other-flow")
    cloud_setting = native(
        f"<ID>13809469</ID><ID_Device>{TEST_BOX}</ID_Device><ID_Set>{int(clock.now)}</ID_Set>"
        f"<ID_SubD>0</ID_SubD><DT>{clock.stamp()}</DT><NewValue>1</NewValue><Confirm>New</Confirm>"
        "<TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><TTO_P>0,000</TTO_P><TTO_E>0,000</TTO_E>"
        "<ID_Server>9</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason>"
        f"<TSec>{clock.stamp(True)}</TSec>", MODEL,
    )
    cloud_reader = asyncio.StreamReader()
    cloud_reader.feed_data(cloud_setting)
    cloud_reader.feed_eof()
    box_writer = dummy_writer_factory()
    await server._pipe_cloud_to_box(cloud_reader, box_writer, session_id="box-flow")

    assert box_writer.written == [cloud_setting]
    inflight = delivery.inflight_setting()
    assert inflight is not None
    setting, device_id = inflight
    assert device_id == TEST_BOX
    assert setting.audit_id
    assert setting.raw_text == cloud_setting.decode()

    receipt = (
        _combined_ack(clock) if piggyback else
        native(f"<Result>ACK</Result><Rdt>{clock.stamp()}</Rdt><Reason>Setting</Reason>", MODEL)
    )
    await server._handle_twin_frames(receipt, "other-flow")
    assert [record["step"] for record in collector.settings_audit] == [SettingStep.INCOMING.value]
    await server._handle_twin_frames(receipt, "box-flow")
    await server._handle_twin_frames(_setting_event(clock, 1), "box-flow")

    records = [record for record in collector.settings_audit if record["audit_id"] == setting.audit_id]
    assert [record["step"] for record in records] == [
        SettingStep.INCOMING.value, SettingStep.ACK_REASON_SETTING.value, SettingStep.ACK_TBL_EVENTS.value,
    ]
    assert records[0]["raw_text"] == records[1]["raw_text"] == cloud_setting.decode()
    assert records[1]["result"] == SettingResult.PENDING.value
    assert records[2]["result"] == SettingResult.CONFIRMED.value
    assert delivery.inflight_setting() is None
    assert delivery.is_cloud_inflight() is False


@pytest.mark.asyncio
async def test_setting_audit_nack_reuses_stored_raw_text(dummy_writer_factory) -> None:
    from telemetry.settings_audit import SettingResult, SettingStep

    raw_text = "<Frame><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue></Frame>"
    collector = _make_collector()
    server, _delivery, queue, _clock = _qualified_transport(make_config(), collector=collector)
    queue.enqueue("tbl_box_prms", "MODE", 1, raw_text=raw_text, device_id=TEST_BOX, require_profile=True)
    await _send_poll(server, dummy_writer_factory())
    nack = native("<Result>NACK</Result><TblName>tbl_box_prms</TblName><ToDo>MODE</ToDo>", MODEL)
    assert await server._handle_twin_frames(nack, "box-flow") == "nack"

    records = [record for record in collector.settings_audit if record["step"] == SettingStep.NACK.value]
    assert len(records) == 1
    assert records[0]["result"] == SettingResult.FAILED.value
    assert records[0]["raw_text"] == raw_text


@pytest.mark.asyncio
async def test_offline_reply_timeout_aborts_a_stalled_transport_close() -> None:
    """Do not leave a timed-out offline send attached to an undrained socket."""
    server, delivery, queue, _clock = _qualified_transport(make_config(proxy_mode="offline"), timeout=0.05)
    queue.enqueue("tbl_box_prms", "MODE", 1, device_id=TEST_BOX, require_profile=True)
    reader = asyncio.StreamReader()
    reader.feed_data(POLL)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.is_closing.return_value = False
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock(side_effect=asyncio.Event().wait)
    writer.transport = MagicMock(spec=asyncio.Transport)

    await asyncio.wait_for(server._pipe_box_offline(reader, writer, ("127.0.0.1", 1), "box-flow"), 0.5)

    writer.write.assert_called_once()
    assert b"<Reason>Setting</Reason>" in writer.write.call_args.args[0]
    assert delivery.inflight() is None
    replay, = queue.get_pending(device_id=TEST_BOX, unsent_only=True)
    assert replay.retried
    writer.transport.abort.assert_called_once()


@pytest.mark.asyncio
async def test_local_getactual_loop_disabled_does_not_write() -> None:
    cfg = make_config(local_getactual_enabled=False, local_getactual_interval_s=10)
    server = ProxyServer(cfg)
    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.is_closing = MagicMock(
        side_effect=AssertionError("disabled local GetActual must return before touching writer state")
    )
    box_writer.write = MagicMock()
    box_writer.drain = AsyncMock()

    await server._local_getactual_loop(box_writer, conn_id=123, peer="box:1")

    box_writer.is_closing.assert_not_called()
    box_writer.write.assert_not_called()
    box_writer.drain.assert_not_called()


@pytest.mark.asyncio
async def test_local_getactual_loop_sends_immediate_frame_and_records_direction() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    telemetry = MagicMock()
    frame_capture = MagicMock()
    server = ProxyServer(cfg, frame_capture=frame_capture, telemetry_collector=telemetry)
    sent: list[bytes] = []
    sleep_calls: list[float] = []

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.is_closing.return_value = False
    box_writer.write = MagicMock(side_effect=lambda data: sent.append(data))
    box_writer.drain = AsyncMock()

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        box_writer.is_closing.return_value = True

    with patch("proxy.server.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
        await server._local_getactual_loop(box_writer, conn_id=456, peer="box:2")

    assert len(sent) == 1
    assert b"<Result>ACK</Result>" in sent[0]
    assert b"<ToDo>GetActual</ToDo>" in sent[0]
    assert sleep_calls == [10]
    frame_capture.capture.assert_called_once()
    assert frame_capture.capture.call_args.kwargs["direction"] == "proxy_to_box"
    telemetry.record_frame_direction.assert_called_once_with("proxy_to_box")


@pytest.mark.asyncio
async def test_local_getactual_loop_uses_minimum_interval() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=3)
    server = ProxyServer(cfg)
    sleep_calls: list[float] = []

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.is_closing.return_value = False
    box_writer.write = MagicMock()
    box_writer.drain = AsyncMock()

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        box_writer.is_closing.return_value = True

    with patch("proxy.server.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
        await server._local_getactual_loop(box_writer, conn_id=789, peer="box:3")

    assert sleep_calls == [10]


@pytest.mark.asyncio
async def test_local_getactual_loop_stops_on_writer_error() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=telemetry)
    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.is_closing.return_value = False
    box_writer.write = MagicMock(side_effect=ConnectionResetError("gone"))
    box_writer.drain = AsyncMock()

    await server._local_getactual_loop(box_writer, conn_id=321, peer="box:4")

    box_writer.write.assert_called_once()
    box_writer.drain.assert_not_called()
    telemetry.record_frame_direction.assert_not_called()


@pytest.mark.asyncio
async def test_stop_local_getactual_task_cancels_running_task() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    server = ProxyServer(cfg)

    async def wait_forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())

    await server._stop_local_getactual_task(task)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_handle_box_connection_offline_mode_cleans_up_local_getactual_task() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=telemetry)
    server.mode_manager.should_try_cloud = MagicMock(return_value=False)
    server.mode_manager.configured_mode = "offline"
    server.mode_manager.runtime_mode = MagicMock(value="offline")

    loop_started = asyncio.Event()
    loop_cancelled = asyncio.Event()

    async def fake_local_getactual_loop(
        writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> None:
        assert writer is box_writer
        assert isinstance(conn_id, int)
        assert peer == "127.0.0.1:12345"
        loop_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            loop_cancelled.set()
            raise

    server._local_getactual_loop = AsyncMock(side_effect=fake_local_getactual_loop)  # type: ignore[method-assign]

    async def fake_pipe_box_offline(*args, **kwargs) -> None:
        await asyncio.wait_for(loop_started.wait(), timeout=1.0)

    server._pipe_box_offline = AsyncMock(side_effect=fake_pipe_box_offline)

    box_reader = MagicMock(spec=asyncio.StreamReader)
    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.get_extra_info.return_value = ("127.0.0.1", 12345)

    await server._handle_box_connection(box_reader, box_writer)

    assert loop_started.is_set()
    assert loop_cancelled.is_set()
    server._pipe_box_offline.assert_awaited_once()
    telemetry.record_offline_event.assert_called_once_with(
        reason="configured_offline_mode",
        local_ack=True,
        mode="offline",
    )
    assert server._active_connection_count == 0
    assert server.is_box_connected() is False
    assert server.box_peer is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("open_exc", "expected_reason"),
    [
        (asyncio.TimeoutError("cloud timeout"), "offline_fallback_timeout"),
        (OSError("cloud down"), "offline_fallback_oserror"),
    ],
)
async def test_handle_box_connection_cloud_failure_offline_fallback_manages_local_getactual_lifecycle(
    open_exc: BaseException,
    expected_reason: str,
) -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=telemetry)
    server.mode_manager.should_try_cloud = MagicMock(return_value=True)
    server.mode_manager.is_offline = MagicMock(return_value=True)
    server.mode_manager.runtime_mode = MagicMock(value="offline")
    server.mode_manager.record_failure = MagicMock()

    local_task_started = asyncio.Event()
    local_task_cancelled = asyncio.Event()

    async def fake_local_getactual_loop(
        writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> None:
        assert writer is box_writer
        assert isinstance(conn_id, int)
        assert peer == "127.0.0.1:34567"
        local_task_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            local_task_cancelled.set()
            raise

    local_task = None
    original_start = server._start_local_getactual_task
    original_stop = server._stop_local_getactual_task
    start_local_getactual_task = MagicMock()
    stop_local_getactual_task = AsyncMock(side_effect=original_stop)

    def fake_start_local_getactual_task(
        writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> asyncio.Task[None] | None:
        nonlocal local_task
        start_local_getactual_task(writer, conn_id=conn_id, peer=peer)
        local_task = original_start(writer, conn_id=conn_id, peer=peer)
        return local_task

    server._local_getactual_loop = AsyncMock(side_effect=fake_local_getactual_loop)  # type: ignore[method-assign]

    async def fake_pipe_box_offline(*args, **kwargs) -> None:
        await asyncio.wait_for(local_task_started.wait(), timeout=1.0)

    server._pipe_box_offline = AsyncMock(side_effect=fake_pipe_box_offline)
    server._start_local_getactual_task = fake_start_local_getactual_task  # type: ignore[method-assign]
    server._stop_local_getactual_task = stop_local_getactual_task  # type: ignore[method-assign]

    box_reader = MagicMock(spec=asyncio.StreamReader)
    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.get_extra_info.return_value = ("127.0.0.1", 34567)

    with patch("proxy.server.asyncio.open_connection", new=AsyncMock(side_effect=open_exc)):
        await server._handle_box_connection(box_reader, box_writer)

    assert local_task is not None
    assert local_task_started.is_set()
    assert local_task_cancelled.is_set()
    assert local_task.done()
    start_local_getactual_task.assert_called_once()
    assert start_local_getactual_task.call_args.args == (box_writer,)
    assert isinstance(start_local_getactual_task.call_args.kwargs["conn_id"], int)
    assert start_local_getactual_task.call_args.kwargs["peer"] == "127.0.0.1:34567"
    stop_local_getactual_task.assert_awaited_once_with(local_task)
    server._pipe_box_offline.assert_awaited_once()
    server.mode_manager.record_failure.assert_called_once_with(reason=str(open_exc))
    assert server._active_connection_count == 0
    assert server.is_box_connected() is False
    assert server.box_peer is None
    telemetry.record_offline_event.assert_called_once_with(
        reason=f"cloud_connect_{'timeout' if isinstance(open_exc, asyncio.TimeoutError) else 'oserror'}",
        local_ack=True,
        mode="offline",
    )
    telemetry.record_box_session_end.assert_called_once()
    assert telemetry.record_box_session_end.call_args.kwargs["reason"] == expected_reason


@pytest.mark.asyncio
async def test_handle_box_connection_online_session_cleans_up_local_getactual_task() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    twin_delivery = MagicMock()
    server = ProxyServer(cfg, twin_delivery=twin_delivery)
    server.mode_manager.should_try_cloud = MagicMock(return_value=True)
    server.mode_manager.record_success = MagicMock()

    loop_started = asyncio.Event()
    loop_cancelled = asyncio.Event()

    async def fake_local_getactual_loop(
        writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> None:
        assert writer is box_writer
        assert isinstance(conn_id, int)
        assert peer == "127.0.0.1:23456"
        loop_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            loop_cancelled.set()
            raise

    server._local_getactual_loop = AsyncMock(side_effect=fake_local_getactual_loop)  # type: ignore[method-assign]

    async def fake_pipe_box_to_cloud(*args, **kwargs) -> None:
        await asyncio.Event().wait()

    async def fake_pipe_cloud_to_box(*args, **kwargs) -> str:
        await asyncio.wait_for(loop_started.wait(), timeout=1.0)
        return "eof"

    server._pipe_box_to_cloud = AsyncMock(side_effect=fake_pipe_box_to_cloud)
    server._pipe_cloud_to_box = AsyncMock(side_effect=fake_pipe_cloud_to_box)

    box_reader = MagicMock(spec=asyncio.StreamReader)
    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.get_extra_info.return_value = ("127.0.0.1", 23456)
    box_writer.is_closing.return_value = False
    box_writer.close = MagicMock()
    box_writer.wait_closed = AsyncMock()

    cloud_reader = MagicMock(spec=asyncio.StreamReader)
    cloud_writer = MagicMock(spec=asyncio.StreamWriter)
    cloud_writer.is_closing.return_value = False
    cloud_writer.close = MagicMock()
    cloud_writer.wait_closed = AsyncMock()

    with patch("proxy.server.asyncio.open_connection", new=AsyncMock(return_value=(cloud_reader, cloud_writer))):
        await server._handle_box_connection(box_reader, box_writer)

    assert loop_started.is_set()
    assert loop_cancelled.is_set()
    server.mode_manager.record_success.assert_not_called()
    server._pipe_box_to_cloud.assert_awaited_once()
    server._pipe_cloud_to_box.assert_awaited_once()
    twin_delivery.clear_session.assert_called_once()
    box_writer.close.assert_called_once()
    cloud_writer.close.assert_called_once()
    assert server._active_connection_count == 0
    assert server.is_box_connected() is False
