"""Unit tests for the sole semantic ProxyServer runtime path."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from protocol.frame import build_frame
from proxy.server import ProxyServer


def _frame(inner: str) -> bytes:
    return build_frame(inner).encode("utf-8")


@pytest.mark.asyncio
async def test_server_starts_and_stops_listener(make_config) -> None:
    config = make_config(proxy_host="127.0.0.1", proxy_port=0)
    server = ProxyServer(config)
    listener = MagicMock()
    socket = MagicMock()
    socket.getsockname.return_value = ("127.0.0.1", 5710)
    listener.sockets = [socket]
    listener.wait_closed = AsyncMock()

    with patch("proxy.server.resolve_a_record", return_value="127.0.0.1"), patch(
        "proxy.server.asyncio.start_server",
        new=AsyncMock(return_value=listener),
    ) as start_server:
        await server.start()
        await server.stop()

    start_server.assert_awaited_once_with(
        server._handle_box_connection,
        "127.0.0.1",
        0,
    )
    listener.close.assert_called_once_with()
    listener.wait_closed.assert_awaited_once_with()
    assert server._server is None


@pytest.mark.asyncio
async def test_stop_cancels_active_connection_tasks(make_config) -> None:
    server = ProxyServer(make_config())
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    server._active_connections.add(task)

    await server.stop()

    assert task.cancelled()
    assert server._active_connections == {task}


@pytest.mark.asyncio
async def test_process_frame_publishes_sensor_payload(make_config) -> None:
    callback = AsyncMock()
    server = ProxyServer(make_config(), on_frame=callback)
    frame = _frame(
        "<TblName>tbl_actual</TblName><ID_Device>DEV01</ID_Device><P>42</P>"
    )

    await server._process_frame(frame)

    callback.assert_awaited_once()
    assert callback.await_args.args[0]["P"] == 42
    assert server.frames_received == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inner",
    [
        "<Result>ACK</Result><Reason>Setting</Reason>",
        (
            "<TblName>tbl_box_prms</TblName><ID_Device>DEV01</ID_Device>"
            "<TblItem>MODE</TblItem><NewValue>2</NewValue><Confirm>New</Confirm>"
        ),
    ],
)
async def test_process_frame_does_not_publish_transport_evidence(
    make_config,
    inner,
) -> None:
    callback = AsyncMock()
    server = ProxyServer(make_config(), on_frame=callback)

    await server._process_frame(_frame(inner))

    callback.assert_not_awaited()


def test_constructor_has_no_legacy_delivery_or_confirmation_api(make_config) -> None:
    with pytest.raises(TypeError):
        ProxyServer(make_config(), twin_delivery=object())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ProxyServer(make_config(), on_confirmed_setting=object())  # type: ignore[call-arg]


def test_semantic_context_owns_bounded_queue_and_serialized_writer(
    make_config,
    dummy_writer_factory,
) -> None:
    server = ProxyServer(make_config())
    context = server._create_online_context(
        session_id="session",
        box_writer=dummy_writer_factory(),
        cloud_writer=dummy_writer_factory(),
        conn_id=1,
        peer="127.0.0.1:1",
    )

    assert context.semantic_events.maxsize == 1
    assert context.session_id == "session"
    assert context.cloud_writer is not None


@pytest.mark.asyncio
async def test_online_connection_always_uses_semantic_context_without_coordinator(
    make_config,
    dummy_writer_factory,
) -> None:
    server = ProxyServer(make_config(proxy_mode="online"))
    server.run_connection_context = AsyncMock()
    cloud_reader = asyncio.StreamReader()
    cloud_reader.feed_eof()
    cloud_writer = dummy_writer_factory()
    box_reader = asyncio.StreamReader()
    box_reader.feed_eof()
    box_writer = dummy_writer_factory()

    async def open_cloud(*_args, **_kwargs):
        return cloud_reader, cloud_writer

    with patch("proxy.server.asyncio.open_connection", new=open_cloud):
        await server._handle_box_connection(box_reader, box_writer)

    server.run_connection_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_offline_connection_always_uses_semantic_context_without_coordinator(
    make_config,
    dummy_writer_factory,
) -> None:
    server = ProxyServer(make_config(proxy_mode="offline"))
    server.run_offline_context = AsyncMock()
    box_reader = asyncio.StreamReader()
    box_reader.feed_eof()
    box_writer = dummy_writer_factory()

    await server._handle_box_connection(box_reader, box_writer)

    server.run_offline_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_semantic_getactual_uses_serialized_writer(make_config) -> None:
    config = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    server = ProxyServer(config)
    context = MagicMock()
    context.close_requested.is_set.side_effect = [False, True]
    context.box_writer.write_frame = AsyncMock(
        return_value=MagicMock(outcome=server_module_box_outcome())
    )

    with patch("proxy.server.asyncio.sleep", new=AsyncMock()):
        await server._semantic_getactual_loop(context)

    context.box_writer.write_frame.assert_awaited_once()


def server_module_box_outcome():
    """Import lazily to keep the assertion tied to the public writer enum."""
    from proxy.writer import BoxWriteOutcome

    return BoxWriteOutcome.DRAINED


def test_connection_state_accessors_are_passive(make_config) -> None:
    server = ProxyServer(make_config())
    server._box_connected = True
    server._cloud_connected = True

    assert server.is_box_connected() is True
    assert server.is_cloud_connected() is True
    assert server.uptime_s() >= 0


def test_telemetry_connection_end_records_both_sessions(make_config) -> None:
    collector = MagicMock()
    server = ProxyServer(make_config(), telemetry_collector=collector)

    server._record_telemetry_connection_end(
        box_connected_since_epoch=1.0,
        box_reason="eof",
        box_peer="127.0.0.1:1",
        cloud_connected_since_epoch=2.0,
        cloud_reason="closed",
    )

    collector.record_box_session_end.assert_called_once()
    collector.record_cloud_session_end.assert_called_once()
