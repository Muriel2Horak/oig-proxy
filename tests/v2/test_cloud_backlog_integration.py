"""Wire-level coverage for replay of native historical measurements."""
# pylint: disable=protected-access,missing-function-docstring,too-many-locals
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from protocol.frame import build_frame
from proxy.server import ProxyServer


DEVICE = "1234567890"
OTHER_DEVICE = "1234567891"
ACK = build_frame("<Result>ACK</Result>").encode()
NACK = build_frame("<Result>NACK</Result><ToDo>OneMore</ToDo>").encode()


def measurement(sequence: int, device: str = DEVICE) -> bytes:
    return build_frame(
        f"<TblName>tbl_ac_in</TblName><ID_Set>{sequence}</ID_Set>"
        f"<DT>2026-11-22 12:{sequence * 5:02}:00</DT><Reason>Table</Reason>"
        f"<ID_Device>{device}</ID_Device><WR>317</WR>"
    ).encode()


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"</Frame>\r\n"), 2)


@asynccontextmanager
async def running_proxy(config, *, device: str = DEVICE, **kwargs):
    proxy = ProxyServer(config, device_id_provider=lambda: device, **kwargs)
    await proxy.start()
    try:
        yield proxy, proxy._server.sockets[0].getsockname()[1]
    finally:
        async with asyncio.timeout(2):
            await proxy.stop()


@asynccontextmanager
async def box_connection(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
# pylint: disable=too-many-statements
async def test_cloud_backlog_survives_restart_and_drains_in_order(
    make_config, tmp_path, monkeypatch,
):
    """New data stays behind a NACKed head, while native poll traffic passes through."""
    monkeypatch.setattr("proxy.server.resolve_a_record", lambda *_: "127.0.0.1")
    monkeypatch.setattr("proxy.server.CLOUD_REPLAY_INTERVAL_S", 0.10, raising=False)
    first, second, third, current = (measurement(i) for i in range(1, 5))
    poll = build_frame(f"<Result>IsNewWeather</Result><ID_Device>{DEVICE}</ID_Device>").encode()
    legacy = build_frame(
        "<Result>Weather</Result><Humidity>7</Humidity><Humidity>8</Humidity>",
    ).encode()
    upstream_frames = asyncio.Queue()
    gates = [asyncio.Event() for _ in range(4)]
    finish_cloud_partial = asyncio.Event()
    sent_at: list[float] = []

    async def upstream(reader, writer):
        try:
            while True:
                frame = await read_frame(reader)
                if frame in {first, second, third}:
                    index = len(sent_at)
                    sent_at.append(asyncio.get_running_loop().time())
                    await upstream_frames.put(frame)
                    await gates[index].wait()
                    reply = NACK if index == 0 else ACK
                else:
                    await upstream_frames.put(frame)
                    if frame == poll:
                        writer.write(legacy + ACK[:13])
                        await writer.drain()
                        await finish_cloud_partial.wait()
                        writer.write(ACK[13:])
                        await writer.drain()
                        continue
                    reply = ACK
                writer.write(reply[:13])
                await writer.drain()
                await asyncio.sleep(0)
                writer.write(reply[13:])
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    cloud = await asyncio.start_server(upstream, "127.0.0.1", 0)
    config = make_config(
        proxy_mode="offline", cloud_port=cloud.sockets[0].getsockname()[1],
        cloud_backlog_path=str(tmp_path / "cloud.db"),
    )
    try:
        async with running_proxy(config) as (_, port), box_connection(port) as (reader, writer):
            writer.write(first + second)
            await writer.drain()
            assert await read_frame(reader) == ACK
            assert await read_frame(reader) == ACK
            assert upstream_frames.empty()
        config.proxy_mode = "hybrid"
        async with running_proxy(config) as (_, port), box_connection(port) as (reader, writer):
            assert await asyncio.wait_for(upstream_frames.get(), 2) == first
            writer.write(poll + third[:45])
            await writer.drain()
            await asyncio.sleep(0)
            writer.write(third[45:])
            await writer.drain()
            assert await read_frame(reader) == legacy
            assert await asyncio.wait_for(reader.readexactly(13), 2) == ACK[:13]
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(reader.readexactly(1), 0.05)
            finish_cloud_partial.set()
            assert ACK[:13] + await read_frame(reader) == ACK
            assert await read_frame(reader) == ACK
            assert await asyncio.wait_for(upstream_frames.get(), 2) == poll
            for index, frame in enumerate((first, second, third)):
                gates[index].set()
                assert await asyncio.wait_for(upstream_frames.get(), 2) == frame
            gates[3].set()
            await asyncio.sleep(0.05)
            assert all(later - earlier >= 0.09 for earlier, later in zip(sent_at, sent_at[1:]))
            writer.write(current)
            await writer.drain()
            assert await asyncio.wait_for(upstream_frames.get(), 2) == current
            assert await read_frame(reader) == ACK
        async with running_proxy(config):
            await asyncio.sleep(0.15)
            assert upstream_frames.empty()
    finally:
        for gate in gates:
            gate.set()
        finish_cloud_partial.set()
        cloud.close()
        await cloud.wait_closed()


@pytest.mark.asyncio
async def test_local_history_is_durable_before_slow_observation(make_config, tmp_path, monkeypatch):
    """A suspended telemetry callback cannot let fresh Cloud history pass the old row."""
    monkeypatch.setattr("proxy.server.resolve_a_record", lambda *_: "127.0.0.1")
    monkeypatch.setattr("proxy.server.CLOUD_REPLAY_INTERVAL_S", 0.10)
    first, second = measurement(1), measurement(2)
    observing = asyncio.Event()
    finish = asyncio.Event()
    prior_ack = asyncio.Event()
    observed_cloud = asyncio.Queue()

    async def observe(frame):
        if frame["_dt"] == "2026-11-22 12:05:00":
            observing.set()
            await finish.wait()

    async def upstream(reader, writer):
        try:
            frame = await read_frame(reader)
            await observed_cloud.put(frame)
            if frame == first:
                await prior_ack.wait()
            writer.write(ACK)
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    cloud = await asyncio.start_server(upstream, "127.0.0.1", 0)
    config = make_config(
        proxy_mode="offline", cloud_port=cloud.sockets[0].getsockname()[1],
        cloud_backlog_path=str(tmp_path / "cloud.db"),
    )
    try:
        async with (
            running_proxy(config, on_frame=observe) as (proxy, port),
            box_connection(port) as (_, writer),
        ):
            writer.write(first)
            await writer.drain()
            await asyncio.wait_for(observing.wait(), 2)
            assert proxy._cloud_backlog.pending
            assert await proxy.apply_configured_mode("hybrid")
            async with box_connection(port) as (reader, next_writer):
                next_writer.write(second)
                await next_writer.drain()
                assert await read_frame(reader) == ACK
                assert await asyncio.wait_for(observed_cloud.get(), 2) == first
                assert observed_cloud.empty()
                prior_ack.set()
                assert await asyncio.wait_for(observed_cloud.get(), 2) == second
            finish.set()
    finally:
        prior_ack.set()
        finish.set()
        cloud.close()
        await cloud.wait_closed()


@pytest.mark.asyncio
async def test_failed_history_commit_sends_no_local_ack(make_config, tmp_path, monkeypatch):
    """The BOX can retry its original measurement when storage is unavailable."""
    monkeypatch.setattr("proxy.server.resolve_a_record", lambda *_: "127.0.0.1")
    config = make_config(proxy_mode="offline", cloud_backlog_path=str(tmp_path / "cloud.db"))
    async with running_proxy(config) as (proxy, port), box_connection(port) as (reader, writer):
        async def unavailable(*_):
            raise OSError("disk is unavailable")

        monkeypatch.setattr(proxy._cloud_backlog, "put", unavailable)
        writer.write(measurement(1))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""


@asynccontextmanager
async def acknowledging_cloud():
    frames = asyncio.Queue()

    async def upstream(reader, writer):
        try:
            while True:
                await frames.put(await read_frame(reader))
                writer.write(ACK)
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    cloud = await asyncio.start_server(upstream, "127.0.0.1", 0)
    async with cloud:
        yield cloud.sockets[0].getsockname()[1], frames


@pytest.mark.asyncio
async def test_previous_box_rows_do_not_block_direct_or_queued_current_box_history(
    make_config, tmp_path, monkeypatch,
):
    """Retain history across rebinds and replay only the currently bound BOX FIFO."""
    monkeypatch.setattr("proxy.server.resolve_a_record", lambda *_: "127.0.0.1")
    monkeypatch.setattr("proxy.server.CLOUD_REPLAY_INTERVAL_S", 0.05)
    previous = measurement(1, OTHER_DEVICE)
    first, second, live = (measurement(i) for i in range(2, 5))
    async with acknowledging_cloud() as (cloud_port, frames):
        config = make_config(
            proxy_mode="offline", cloud_port=cloud_port,
            cloud_backlog_path=str(tmp_path / "backlog.db"),
        )
        async with (
            running_proxy(config, device=OTHER_DEVICE) as (_, port),
            box_connection(port) as (reader, writer),
        ):
            writer.write(previous)
            await writer.drain()
            assert await read_frame(reader) == ACK
        config.proxy_mode = "hybrid"
        async with running_proxy(config) as (_, port), box_connection(port) as (reader, writer):
            writer.write(live)
            await writer.drain()
            assert await read_frame(reader) == ACK
            assert await asyncio.wait_for(frames.get(), 2) == live
        config.proxy_mode = "offline"
        async with running_proxy(config) as (_, port), box_connection(port) as (reader, writer):
            writer.write(first + second)
            await writer.drain()
            assert await read_frame(reader) == ACK
            assert await read_frame(reader) == ACK
        config.proxy_mode = "hybrid"
        async with running_proxy(config):
            assert await asyncio.wait_for(frames.get(), 2) == first
            assert await asyncio.wait_for(frames.get(), 2) == second
        async with running_proxy(config, device=OTHER_DEVICE):
            assert await asyncio.wait_for(frames.get(), 2) == previous


@pytest.mark.asyncio
async def test_backlog_open_failure_allows_cloud_ack_and_refuses_offline_ack(
    make_config, tmp_path, monkeypatch,
):
    """Keep the service online when SQLite cannot open and never claim offline acceptance."""
    monkeypatch.setattr("proxy.server.resolve_a_record", lambda *_: "127.0.0.1")
    frame = measurement(1)
    async with acknowledging_cloud() as (cloud_port, frames):
        config = make_config(
            proxy_mode="hybrid", cloud_port=cloud_port,
            cloud_backlog_path=str(tmp_path / "missing-directory" / "backlog.db"),
        )
        async with running_proxy(config) as (proxy, port), box_connection(port) as (reader, writer):
            writer.write(frame)
            await writer.drain()
            assert await asyncio.wait_for(frames.get(), 2) == frame
            assert await read_frame(reader) == ACK
            assert await proxy.apply_configured_mode("offline")
            async with box_connection(port) as (offline_reader, offline_writer):
                offline_writer.write(frame)
                await offline_writer.drain()
                assert await asyncio.wait_for(offline_reader.read(), 2) == b""
            assert frames.empty()
