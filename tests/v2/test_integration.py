"""Integration tests for proxy server."""
# pylint: disable=protected-access,missing-function-docstring,invalid-name,too-many-locals
from __future__ import annotations

import asyncio
import importlib
from unittest.mock import patch
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _frame(table: str, device_id: str, **fields: object) -> bytes:
    build_frame = importlib.import_module("protocol.frame").build_frame
    inner = [f"<TblName>{table}</TblName>", f"<ID_Device>{device_id}</ID_Device>"]
    inner.extend(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return build_frame("".join(inner)).encode("utf-8")


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
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery
    TwinQueue = importlib.import_module("twin.state").TwinQueue
    build_frame = importlib.import_module("protocol.frame").build_frame
    cfg = make_config()
    queue = TwinQueue()
    queue.enqueue("tbl_set", "T_Room", 22)
    twin_delivery = TwinDelivery(queue, MagicMock())
    server = ProxyServer(cfg, twin_delivery=twin_delivery)
    box_writer = dummy_writer_factory()

    await server._deliver_pending_for_isnewset(
        build_frame("<TblName>IsNewSet</TblName><ID_Device>12345</ID_Device>"),
        box_writer,
    )

    delivered = b"".join(box_writer.written)
    assert b"<TblName>tbl_set</TblName>" in delivered
    assert b"<ID_Device>12345</ID_Device>" in delivered
    assert b"<ID_Set>" in delivered
    assert b"<Reason>Setting</Reason>" in delivered
    assert b"<TblItem>T_Room</TblItem>" in delivered
    assert b"<NewValue>22</NewValue>" in delivered
    assert b"<ID_SubD>0</ID_SubD>" in delivered
    assert b"<ID_Server>9</ID_Server>" in delivered
    assert b"<Confirm>New</Confirm>" in delivered


@pytest.mark.asyncio
async def test_twin_tbl_events_ack_removes_pending(make_config, dummy_writer_factory) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery
    TwinQueue = importlib.import_module("twin.state").TwinQueue
    cfg = make_config()
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 0)
    twin_delivery = TwinDelivery(queue, MagicMock())
    on_confirmed_setting = AsyncMock()
    server = ProxyServer(
        cfg,
        twin_delivery=twin_delivery,
        on_confirmed_setting=on_confirmed_setting,
    )
    box_writer = dummy_writer_factory()

    await server._deliver_pending_for_isnewset(
        _frame("IsNewSet", "12345").decode("utf-8"),
        box_writer,
    )
    assert queue.size() == 1

    ack_frame = _frame(
        "tbl_events",
        "12345",
        Type="Setting",
        Content="Remotely : tbl_box_prms / MODE: [3]->[0]",
    )
    await server._handle_twin_frames(ack_frame, box_writer)

    assert queue.size() == 0
    on_confirmed_setting.assert_awaited_once_with("12345", "tbl_box_prms", "MODE", "0")


@pytest.mark.asyncio
async def test_twin_reason_setting_ack_removes_pending(make_config, dummy_writer_factory) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery
    TwinQueue = importlib.import_module("twin.state").TwinQueue
    cfg = make_config()
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 3)
    twin_delivery = TwinDelivery(queue, MagicMock())
    on_confirmed_setting = AsyncMock()
    server = ProxyServer(
        cfg,
        twin_delivery=twin_delivery,
        on_confirmed_setting=on_confirmed_setting,
    )
    box_writer = dummy_writer_factory()

    await server._deliver_pending_for_isnewset(
        _frame("IsNewSet", "12345").decode("utf-8"),
        box_writer,
    )
    assert queue.size() == 1
    assert twin_delivery.inflight() == ("tbl_box_prms", "MODE")

    reason_ack = b"<Frame><Result>ACK</Result><Reason>Setting</Reason></Frame>"
    await server._handle_twin_frames(reason_ack, box_writer)

    assert queue.size() == 0
    assert twin_delivery.inflight() is None
    on_confirmed_setting.assert_awaited_once_with("12345", "tbl_box_prms", "MODE", 3)


@pytest.mark.asyncio
async def test_cloud_setting_marks_cloud_inflight_for_tbl_payload(make_config, dummy_writer_factory) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery
    TwinQueue = importlib.import_module("twin.state").TwinQueue
    cfg = make_config()
    queue = TwinQueue()
    twin_delivery = TwinDelivery(queue, MagicMock())
    server = ProxyServer(cfg, twin_delivery=twin_delivery)

    cloud_setting = _frame(
        "tbl_box_prms",
        "12345",
        TblItem="MODE",
        NewValue=3,
        Reason="Setting",
    )
    box_writer = dummy_writer_factory()
    cloud_reader = asyncio.StreamReader()
    cloud_reader.feed_data(cloud_setting)
    cloud_reader.feed_eof()

    assert twin_delivery.is_cloud_inflight() is False
    await server._pipe_cloud_to_box(cloud_reader, box_writer)
    assert twin_delivery.is_cloud_inflight() is True


@pytest.mark.asyncio
async def test_cloud_end_clears_cloud_inflight(make_config, dummy_writer_factory) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery
    TwinQueue = importlib.import_module("twin.state").TwinQueue
    build_frame = importlib.import_module("protocol.frame").build_frame
    cfg = make_config()
    queue = TwinQueue()
    twin_delivery = TwinDelivery(queue, MagicMock())
    twin_delivery.set_cloud_inflight()
    server = ProxyServer(cfg, twin_delivery=twin_delivery)

    box_writer = dummy_writer_factory()
    cloud_reader = asyncio.StreamReader()
    cloud_reader.feed_data(build_frame("<Result>END</Result>").encode("utf-8"))
    cloud_reader.feed_eof()

    assert twin_delivery.is_cloud_inflight() is True
    await server._pipe_cloud_to_box(cloud_reader, box_writer)
    assert twin_delivery.is_cloud_inflight() is False


@pytest.mark.asyncio
async def test_local_setting_frame_uses_monotonic_msg_id(make_config, stream_reader_from_chunks, dummy_writer_factory) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery
    TwinQueue = importlib.import_module("twin.state").TwinQueue
    cfg = make_config()
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 3)
    twin_delivery = TwinDelivery(queue, MagicMock())
    twin_delivery.observe_msg_id(13800000)
    server = ProxyServer(cfg, twin_delivery=twin_delivery)

    box_writer = dummy_writer_factory()
    cloud_writer = dummy_writer_factory()

    isnewset = _frame("IsNewSet", "12345")
    box_reader = stream_reader_from_chunks(isnewset)

    await server._pipe_box_to_cloud(box_reader, cloud_writer, box_writer)

    sent = b"".join(box_writer.written).decode("utf-8", errors="replace")
    assert "<ID>13800001</ID>" in sent


@pytest.mark.asyncio
async def test_replay_file_frame_sent_before_queue_delivery(
    make_config,
    stream_reader_from_chunks,
    dummy_writer_factory,
    tmp_path,
) -> None:
    ProxyServer = importlib.import_module("proxy.server").ProxyServer
    TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery
    TwinQueue = importlib.import_module("twin.state").TwinQueue
    server_module = importlib.import_module("proxy.server")

    cfg = make_config()
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 3)
    twin_delivery = TwinDelivery(queue, MagicMock())
    server = ProxyServer(cfg, twin_delivery=twin_delivery)

    replay_payload = _frame(
        "tbl_box_prms",
        "12345",
        TblItem="MODE",
        NewValue=3,
        Reason="Setting",
        ID=77777777,
        ID_Set=1773669999,
        Confirm="New",
        ID_Server=9,
        ver=54321,
    )
    replay_path = tmp_path / "replay_setting_frame.xml"
    replay_path.write_bytes(replay_payload)

    with patch.object(
        server_module,
        "_read_replay_frame_once",
        side_effect=lambda _path: replay_path.read_bytes() if replay_path.exists() else None,
    ):
        box_writer = dummy_writer_factory()
        cloud_writer = dummy_writer_factory()
        box_reader = stream_reader_from_chunks(_frame("IsNewSet", "12345"))

        await server._pipe_box_to_cloud(box_reader, cloud_writer, box_writer)

    sent = b"".join(box_writer.written)
    assert b"<ID>77777777</ID>" in sent


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
