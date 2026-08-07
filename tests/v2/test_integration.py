"""Runtime integration boundaries after the durable cutover."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from protocol.frame import FrameDirection, build_frame
from proxy.server import ProxyServer, StreamFrameEvent
from telemetry.collector import TelemetryCollector


def _frame(table: str, device_id: str, **fields: object) -> bytes:
    inner = [f"<TblName>{table}</TblName>", f"<ID_Device>{device_id}</ID_Device>"]
    inner.extend(f"<{key}>{value}</{key}>" for key, value in fields.items())
    return build_frame("".join(inner)).encode("utf-8")


def test_legacy_local_setting_paths_are_removed() -> None:
    root = Path(__file__).parents[2] / "addon" / "oig-proxy"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "twin" / "state.py",
            root / "twin" / "delivery.py",
            root / "proxy" / "server.py",
        )
    )

    for forbidden in (
        "class TwinQueue",
        "class TwinDelivery",
        "_inflight_key",
        "replay_setting_frame.xml",
        "build_setting_xml",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_online_frame_uses_semantic_router_and_preserves_wire_bytes(
    make_config,
    dummy_writer_factory,
) -> None:
    published: list[dict] = []

    async def on_frame(data: dict) -> None:
        published.append(data)

    server = ProxyServer(make_config(proxy_mode="online"), on_frame=on_frame)
    box_writer = dummy_writer_factory()
    cloud_writer = dummy_writer_factory()
    context = server._create_online_context(
        session_id="session",
        box_writer=box_writer,
        cloud_writer=cloud_writer,
        conn_id=1,
        peer="127.0.0.1:1",
    )
    raw = _frame("tbl_actual", "12345", Temp=25)
    assembled = context.box_assembler.feed(raw, received_at_ms=100)[0]

    await server.route_stream_event(
        context,
        StreamFrameEvent(FrameDirection.BOX_TO_PROXY, assembled),
    )

    assert cloud_writer.written == [raw]
    assert published[0]["_device_id"] == "12345"
    assert published[0]["Temp"] == 25


@pytest.mark.asyncio
async def test_offline_frame_has_exactly_one_semantic_response(
    make_config,
    dummy_writer_factory,
) -> None:
    server = ProxyServer(make_config(proxy_mode="offline"))
    box_writer = dummy_writer_factory()
    context = server._create_offline_context(
        session_id="session",
        box_writer=box_writer,
        conn_id=1,
        peer="127.0.0.1:1",
    )
    raw = _frame("tbl_actual", "12345", Temp=20)
    assembled = context.box_assembler.feed(raw, received_at_ms=100)[0]

    await server.route_offline_stream_event(
        context,
        StreamFrameEvent(FrameDirection.BOX_TO_PROXY, assembled),
    )

    assert len(box_writer.written) == 1
    assert b"<Result>ACK</Result>" in box_writer.written[0]


@pytest.mark.asyncio
async def test_hybrid_cloud_failure_falls_back_through_semantic_offline_context(
    make_config,
    dummy_writer_factory,
) -> None:
    server = ProxyServer(
        make_config(proxy_mode="hybrid", hybrid_fail_threshold=1)
    )
    box_reader = asyncio.StreamReader()
    box_reader.feed_eof()
    box_writer = dummy_writer_factory()
    server.run_offline_context = AsyncMock()

    with patch(
        "proxy.server.asyncio.open_connection",
        side_effect=OSError("cloud down"),
    ):
        await server._handle_box_connection(box_reader, box_writer)

    assert server.mode_manager.is_offline() is True
    server.run_offline_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_telemetry_collection_loop_remains_passive() -> None:
    collector = TelemetryCollector(
        interval_s=1,
        version="2.0.0",
        telemetry_enabled=True,
        telemetry_mqtt_broker="telemetry.invalid:1883",
        telemetry_interval_s=1,
        device_id="device123",
        mqtt_namespace="oig_local",
        mqtt_publisher=MagicMock(is_ready=MagicMock(return_value=True)),
    )
    collector.init()
    assert collector.client is not None
    sent_payloads: list[dict] = []
    collector.client.provision = AsyncMock(return_value=True)
    collector.client.send_telemetry = AsyncMock(
        side_effect=lambda payload: sent_payloads.append(payload) or True
    )
    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float) -> None:
        await original_sleep(0)

    with patch("telemetry.collector.asyncio.sleep", side_effect=fast_sleep):
        task = asyncio.create_task(collector.loop())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert sent_payloads
    assert all("window_metrics" in payload for payload in sent_payloads)
