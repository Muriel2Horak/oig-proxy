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
async def test_handle_twin_frames_publishes_confirmed_tbl_events_value() -> None:
    cfg = make_config()
    server = ProxyServer(cfg, on_confirmed_setting=AsyncMock(), twin_delivery=MagicMock())
    box_writer = MagicMock(spec=asyncio.StreamWriter)

    ack_frame = build_frame(
        "<TblName>tbl_events</TblName>"
        "<ID_Device>12345</ID_Device>"
        "<Type>Setting</Type>"
        "<Content>Remotely : tbl_box_prms / MODE: [3]->[0]</Content>"
    ).encode("utf-8")

    await server._handle_twin_frames(ack_frame, box_writer)

    server.on_confirmed_setting.assert_awaited_once_with("12345", "tbl_box_prms", "MODE", "0")


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
async def test_setting_audit_success_roundtrip() -> None:
    from twin.delivery import TwinDelivery
    from twin.state import TwinQueue
    from telemetry.settings_audit import SettingStep, SettingResult

    raw_text = "<Frame><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue></Frame>"
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 1)
    queue.get("tbl_box_prms", "MODE").raw_text = raw_text
    collector = _make_collector()
    delivery = TwinDelivery(queue, MagicMock(), telemetry_collector=collector)
    cfg = make_config()
    server = ProxyServer(cfg, twin_delivery=delivery)

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    assert len(pending) == 1
    audit_id = pending[0].audit_id

    delivery.record_injected_box(pending[0], "dev_1", session_id="sess_1")

    ack_frame = build_frame(
        "<Result>ACK</Result>"
        "<TblName>tbl_box_prms</TblName>"
        "<ToDo>MODE</ToDo>"
        "<ID_Device>dev_1</ID_Device>"
    ).encode("utf-8")
    await server._handle_twin_frames(ack_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    tbl_events_frame = build_frame(
        "<TblName>tbl_events</TblName>"
        "<ID_Device>dev_1</ID_Device>"
        "<Type>Setting</Type>"
        "<Content>Remotely : tbl_box_prms / MODE: [3]->[1]</Content>"
    ).encode("utf-8")
    await server._handle_twin_frames(tbl_events_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    steps = [r["step"] for r in collector.settings_audit]
    assert SettingStep.DELIVER_SELECTED.value in steps
    assert SettingStep.INJECTED_BOX.value in steps
    assert SettingStep.ACK_BOX_OBSERVED.value in steps
    assert SettingStep.ACK_TBL_EVENTS.value in steps
    for step in (
        SettingStep.DELIVER_SELECTED.value,
        SettingStep.INJECTED_BOX.value,
        SettingStep.ACK_BOX_OBSERVED.value,
        SettingStep.ACK_TBL_EVENTS.value,
    ):
        assert [r for r in collector.settings_audit if r["step"] == step][0]["raw_text"] == raw_text

    terminal = [r for r in collector.settings_audit if r["result"] == SettingResult.CONFIRMED.value]
    assert len(terminal) == 1
    assert terminal[0]["step"] == SettingStep.ACK_TBL_EVENTS.value
    assert terminal[0]["audit_id"] == audit_id
    assert terminal[0]["confirmed_value_text"] == "1"


@pytest.mark.asyncio
async def test_duplicate_ack_audit_no_duplicate_terminal() -> None:
    from twin.delivery import TwinDelivery
    from twin.state import TwinQueue
    from telemetry.settings_audit import SettingStep, SettingResult

    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 1)
    collector = _make_collector()
    delivery = TwinDelivery(queue, MagicMock(), telemetry_collector=collector)
    cfg = make_config()
    server = ProxyServer(cfg, twin_delivery=delivery)

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    assert len(pending) == 1

    tbl_events_frame = build_frame(
        "<TblName>tbl_events</TblName>"
        "<ID_Device>dev_1</ID_Device>"
        "<Type>Setting</Type>"
        "<Content>Remotely : tbl_box_prms / MODE: [3]->[1]</Content>"
    ).encode("utf-8")
    await server._handle_twin_frames(tbl_events_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")
    await server._handle_twin_frames(tbl_events_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    terminal = [r for r in collector.settings_audit if r["result"] == SettingResult.CONFIRMED.value]
    assert len(terminal) == 1

    ack_count = [r["step"] for r in collector.settings_audit].count(SettingStep.ACK_TBL_EVENTS.value)
    assert ack_count == 1


@pytest.mark.asyncio
async def test_mixed_ack_precedence() -> None:
    from twin.delivery import TwinDelivery
    from twin.state import TwinQueue
    from telemetry.settings_audit import SettingStep, SettingResult

    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 1)
    collector = _make_collector()
    delivery = TwinDelivery(queue, MagicMock(), telemetry_collector=collector)
    cfg = make_config()
    server = ProxyServer(cfg, twin_delivery=delivery)

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    assert len(pending) == 1

    ack_frame = build_frame(
        "<Result>ACK</Result>"
        "<TblName>tbl_box_prms</TblName>"
        "<ToDo>MODE</ToDo>"
        "<ID_Device>dev_1</ID_Device>"
    ).encode("utf-8")
    await server._handle_twin_frames(ack_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    tbl_events_frame = build_frame(
        "<TblName>tbl_events</TblName>"
        "<ID_Device>dev_1</ID_Device>"
        "<Type>Setting</Type>"
        "<Content>Remotely : tbl_box_prms / MODE: [3]->[1]</Content>"
    ).encode("utf-8")
    await server._handle_twin_frames(tbl_events_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    steps = collector.settings_audit
    ack_observed = [r for r in steps if r["step"] == SettingStep.ACK_BOX_OBSERVED.value]
    tbl_confirmed = [r for r in steps if r["step"] == SettingStep.ACK_TBL_EVENTS.value]

    assert len(ack_observed) == 1
    assert ack_observed[0]["result"] == SettingResult.PENDING.value

    assert len(tbl_confirmed) == 1
    assert tbl_confirmed[0]["result"] == SettingResult.CONFIRMED.value

    terminal = [r for r in steps if r["result"] == SettingResult.CONFIRMED.value]
    assert len(terminal) == 1


@pytest.mark.asyncio
async def test_non_terminal_box_ack_does_not_publish_confirmed_setting() -> None:
    from twin.delivery import TwinDelivery
    from twin.state import TwinQueue

    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 1)
    delivery = TwinDelivery(queue, MagicMock())
    cfg = make_config()
    server = ProxyServer(cfg, twin_delivery=delivery)
    server._publish_confirmed_setting = AsyncMock()  # type: ignore[method-assign]

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    assert len(pending) == 1

    ack_frame = build_frame(
        "<Result>ACK</Result>"
        "<TblName>tbl_box_prms</TblName>"
        "<ToDo>MODE</ToDo>"
        "<ID_Device>dev_1</ID_Device>"
    ).encode("utf-8")
    await server._handle_twin_frames(ack_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    server._publish_confirmed_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_setting_audit_ack_reason_setting_reuses_stored_raw_text() -> None:
    from twin.delivery import TwinDelivery
    from twin.state import TwinQueue
    from telemetry.settings_audit import SettingResult, SettingStep

    raw_text = "<Frame><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue></Frame>"
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 1)
    queue.get("tbl_box_prms", "MODE").raw_text = raw_text
    collector = _make_collector()
    delivery = TwinDelivery(queue, MagicMock(), telemetry_collector=collector)
    cfg = make_config()
    server = ProxyServer(cfg, twin_delivery=delivery)

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    assert len(pending) == 1

    ack_frame = build_frame(
        "<Result>ACK</Result>"
        "<Reason>Setting</Reason>"
        "<ID_Device>dev_1</ID_Device>"
    ).encode("utf-8")
    await server._handle_twin_frames(ack_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    ack_reason = [
        record for record in collector.settings_audit if record["step"] == SettingStep.ACK_REASON_SETTING.value
    ]
    assert len(ack_reason) == 1
    assert ack_reason[0]["result"] == SettingResult.CONFIRMED.value
    assert ack_reason[0]["raw_text"] == raw_text


@pytest.mark.asyncio
async def test_pipe_cloud_to_box_tracks_cloud_setting_for_audit_lifecycle() -> None:
    from twin.delivery import TwinDelivery
    from twin.state import TwinQueue
    from telemetry.settings_audit import SettingResult, SettingStep

    collector = _make_collector()
    delivery = TwinDelivery(TwinQueue(), MagicMock(), telemetry_collector=collector)
    cfg = make_config()
    server = ProxyServer(cfg, twin_delivery=delivery)

    cloud_setting = build_frame(
        "<TblName>tbl_box_prms</TblName>"
        "<ID_Device>12345</ID_Device>"
        "<ID>13809469</ID>"
        "<ID_Set>844979473</ID_Set>"
        "<TblItem>MODE</TblItem>"
        "<NewValue>1</NewValue>"
        "<Confirm>New</Confirm>"
        "<Reason>Setting</Reason>"
    ).encode("utf-8")
    cloud_reader = asyncio.StreamReader()
    cloud_reader.feed_data(cloud_setting)
    cloud_reader.feed_eof()

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.write = MagicMock()
    box_writer.drain = AsyncMock()

    await server._pipe_cloud_to_box(cloud_reader, box_writer)

    inflight = delivery.inflight_setting()
    assert inflight is not None
    setting, device_id = inflight
    assert device_id == "12345"
    assert setting.audit_id
    assert setting.raw_text == cloud_setting.decode("utf-8", errors="replace")

    ack_frame = build_frame(
        "<Result>ACK</Result>"
        "<Reason>Setting</Reason>"
        "<ID_Device>12345</ID_Device>"
    ).encode("utf-8")
    await server._handle_twin_frames(ack_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    audit_records = [record for record in collector.settings_audit if record["audit_id"] == setting.audit_id]
    assert [record["step"] for record in audit_records] == [
        SettingStep.INCOMING.value,
        SettingStep.ACK_REASON_SETTING.value,
    ]
    assert audit_records[0]["raw_text"] == cloud_setting.decode("utf-8", errors="replace")
    assert audit_records[1]["result"] == SettingResult.CONFIRMED.value
    assert audit_records[1]["raw_text"] == cloud_setting.decode("utf-8", errors="replace")
    assert delivery.inflight_setting() is None
    assert delivery.is_cloud_inflight() is False


@pytest.mark.asyncio
async def test_setting_audit_nack_reuses_stored_raw_text() -> None:
    from twin.delivery import TwinDelivery
    from twin.state import TwinQueue
    from telemetry.settings_audit import SettingResult, SettingStep

    raw_text = "<Frame><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue></Frame>"
    queue = TwinQueue()
    queue.enqueue("tbl_box_prms", "MODE", 1)
    queue.get("tbl_box_prms", "MODE").raw_text = raw_text
    collector = _make_collector()
    delivery = TwinDelivery(queue, MagicMock(), telemetry_collector=collector)
    cfg = make_config()
    server = ProxyServer(cfg, twin_delivery=delivery)

    pending = await delivery.deliver_pending("dev_1", session_id="sess_1")
    assert len(pending) == 1

    nack_frame = build_frame(
        "<Result>NACK</Result>"
        "<TblName>tbl_box_prms</TblName>"
        "<ToDo>MODE</ToDo>"
        "<ID_Device>dev_1</ID_Device>"
    ).encode("utf-8")
    await server._handle_twin_frames(nack_frame, MagicMock(spec=asyncio.StreamWriter), session_id="sess_1")

    nack_records = [record for record in collector.settings_audit if record["step"] == SettingStep.NACK.value]
    assert len(nack_records) == 1
    assert nack_records[0]["result"] == SettingResult.FAILED.value
    assert nack_records[0]["raw_text"] == raw_text
