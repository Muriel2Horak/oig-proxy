"""
Testy pro proxy/server.py — ProxyServer (TCP transparent proxy).
"""
from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# sys.path nastaven v conftest.py
from proxy.server import ProxyServer
from config import Config
from protocol.frame import build_frame


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
async def test_pipe_cloud_to_box_processes_parsed_frames():
    cfg = make_config()
    server = ProxyServer(cfg)

    frame = build_frame(
        "<TblName>tbl_boiler_prms</TblName><ID_Device>2206237016</ID_Device><ISON>1</ISON>"
    ).encode("utf-8")
    cloud_reader = MagicMock(spec=asyncio.StreamReader)
    cloud_reader.read = AsyncMock(side_effect=[frame, b""])

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.write = MagicMock()
    box_writer.drain = AsyncMock()

    server._handle_twin_frames = AsyncMock()
    server._process_frame = AsyncMock()

    await server._pipe_cloud_to_box(cloud_reader, box_writer)

    server._handle_twin_frames.assert_called_once()
    server._process_frame.assert_called_once()


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
async def test_handle_twin_frames_delivers_on_isnewset_only():
    cfg = make_config()
    server = ProxyServer(cfg)
    server.twin_delivery = MagicMock()
    server._deliver_pending_for_isnewset = AsyncMock()

    frame = build_frame(
        "<TblName>IsNewSet</TblName><ID_Device>2206237016</ID_Device><DT>1</DT>"
    ).encode("utf-8")
    box_writer = MagicMock(spec=asyncio.StreamWriter)

    await server._handle_twin_frames(frame, box_writer)

    server._deliver_pending_for_isnewset.assert_called_once()


@pytest.mark.asyncio
async def test_handle_twin_frames_delivers_when_result_isnewset_overrides_tbl_actual():
    cfg = make_config()
    server = ProxyServer(cfg)
    server.twin_delivery = MagicMock()
    server._deliver_pending_for_isnewset = AsyncMock()

    frame = build_frame(
        "<Result>IsNewSet</Result><ID_Device>2206237016</ID_Device><TblName>tbl_actual</TblName><DT>1</DT>"
    ).encode("utf-8")
    box_writer = MagicMock(spec=asyncio.StreamWriter)

    await server._handle_twin_frames(frame, box_writer)

    server._deliver_pending_for_isnewset.assert_called_once()


@pytest.mark.asyncio
async def test_handle_twin_frames_skips_delivery_for_other_tables():
    cfg = make_config()
    server = ProxyServer(cfg)
    server.twin_delivery = MagicMock()
    server._deliver_pending_for_isnewset = AsyncMock()

    frame = build_frame(
        "<TblName>tbl_dc_in</TblName><ID_Device>2206237016</ID_Device><P1>10</P1>"
    ).encode("utf-8")
    box_writer = MagicMock(spec=asyncio.StreamWriter)

    await server._handle_twin_frames(frame, box_writer)

    server._deliver_pending_for_isnewset.assert_not_called()


@pytest.mark.asyncio
async def test_handle_twin_frames_skips_delivery_for_tbl_actual():
    cfg = make_config()
    server = ProxyServer(cfg)
    server.twin_delivery = MagicMock()
    server._deliver_pending_for_isnewset = AsyncMock()

    frame = build_frame(
        "<TblName>tbl_actual</TblName><ID_Device>2206237016</ID_Device><P1>10</P1>"
    ).encode("utf-8")
    box_writer = MagicMock(spec=asyncio.StreamWriter)

    await server._handle_twin_frames(frame, box_writer)

    server._deliver_pending_for_isnewset.assert_not_called()


# ---------------------------------------------------------------------------
# Připojení k cloudu selhá
# ---------------------------------------------------------------------------

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

        # Server by měl zavřít spojení (cloud nedostupný)
        data = await asyncio.wait_for(reader.read(100), timeout=2.0)
        assert data == b""  # EOF — spojení zavřeno
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
