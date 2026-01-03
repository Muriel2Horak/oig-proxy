import asyncio

import cloud_session
from cloud_session import CloudSessionManager


def test_extract_one_xml_frame_handles_crlf():
    buf = bytearray(b"<Frame>1</Frame>\r\n<Frame>2</Frame>\n")

    first = CloudSessionManager._extract_one_xml_frame(buf)
    assert first == b"<Frame>1</Frame>\r\n"
    second = CloudSessionManager._extract_one_xml_frame(buf)
    assert second == b"<Frame>2</Frame>\n"
    assert CloudSessionManager._extract_one_xml_frame(buf) is None


def test_extract_one_xml_frame_partial_crlf():
    buf = bytearray(b"<Frame>1</Frame>\r")
    assert CloudSessionManager._extract_one_xml_frame(buf) is None
    assert buf == bytearray(b"<Frame>1</Frame>\r")


def test_ensure_connected_success(monkeypatch):
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    async def fake_open(_host, _port):
        return asyncio.StreamReader(), DummyWriter()

    monkeypatch.setattr(cloud_session, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1)
    asyncio.run(manager.ensure_connected())
    assert manager.is_connected() is True
    assert manager.stats.connects == 1


def test_ensure_connected_timeout(monkeypatch):
    async def fake_open(_host, _port):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cloud_session, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1, min_reconnect_s=0.1)
    try:
        asyncio.run(manager.ensure_connected())
    except asyncio.TimeoutError:
        # Expected timeout during connection attempt; verify stats and backoff below.
        pass
    assert manager.stats.timeouts == 1
    assert manager.stats.errors == 1
    assert manager._backoff_s >= 0.2


def test_send_and_read_ack_success(monkeypatch):
    class DummyWriter:
        def __init__(self):
            self.writes = []
            self._closing = False

        def is_closing(self):
            return self._closing

        def write(self, data):
            self.writes.append(data)

        async def drain(self):
            return None

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    manager = CloudSessionManager("example", 123)
    manager._writer = DummyWriter()

    async def fake_ensure():
        return None

    async def fake_read(**_):
        return b"ACK"

    manager.ensure_connected = fake_ensure
    manager._read_one_ack_frame = fake_read

    async def run():
        ack = await manager.send_and_read_ack(b"DATA", ack_timeout_s=0.1)
        assert ack == b"ACK"
        assert manager._writer.writes == [b"DATA"]

    asyncio.run(run())


def test_send_and_read_ack_eof(monkeypatch):
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def write(self, _data):
            return None

        async def drain(self):
            return None

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    manager = CloudSessionManager("example", 123)
    manager._writer = DummyWriter()

    async def fake_ensure():
        return None

    async def fake_read(**_):
        return b""

    manager.ensure_connected = fake_ensure
    manager._read_one_ack_frame = fake_read

    async def run():
        try:
            await manager.send_and_read_ack(b"DATA", ack_timeout_s=0.1)
        except ConnectionError:
            return "error"
        return "no-error"

    assert asyncio.run(run()) == "error"


def test_read_one_ack_frame_uses_buffer():
    manager = CloudSessionManager("example", 123)
    manager._reader = type("R", (), {"read": lambda *_: b""})()
    manager._rx_buf = bytearray(b"<Frame>1</Frame>")
    ack = asyncio.run(manager._read_one_ack_frame(ack_timeout_s=0.1, ack_max_bytes=1024))
    assert ack == b"<Frame>1</Frame>"


def test_read_one_ack_frame_fallback_on_max_bytes():
    class DummyReader:
        def __init__(self):
            self.calls = 0

        async def read(self, _size):
            self.calls += 1
            return b"x" * 10

    manager = CloudSessionManager("example", 123)
    manager._reader = DummyReader()
    manager._rx_buf = bytearray()

    ack = asyncio.run(manager._read_one_ack_frame(ack_timeout_s=0.1, ack_max_bytes=5))
    assert ack == b"x" * 10


def test_ensure_connected_skips_when_connected():
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    manager = CloudSessionManager("example", 123)
    manager._writer = DummyWriter()
    asyncio.run(manager.ensure_connected())
    assert manager.is_connected() is True


def test_ensure_connected_general_error(monkeypatch):
    async def fake_open(_host, _port):
        raise RuntimeError("boom")

    monkeypatch.setattr(cloud_session, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1, min_reconnect_s=0.1)
    try:
        asyncio.run(manager.ensure_connected())
    except RuntimeError:
        pass
    assert manager.stats.errors == 1
    assert manager._backoff_s >= 0.2


def test_send_and_read_ack_timeout(monkeypatch):
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def write(self, _data):
            return None

        async def drain(self):
            return None

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    manager = CloudSessionManager("example", 123)
    manager._writer = DummyWriter()

    async def fake_ensure():
        return None

    async def fake_read(**_):
        raise asyncio.TimeoutError

    manager.ensure_connected = fake_ensure
    manager._read_one_ack_frame = fake_read

    async def run():
        try:
            await manager.send_and_read_ack(b"DATA", ack_timeout_s=0.1)
        except asyncio.TimeoutError:
            return "timeout"
        return "no-timeout"

    assert asyncio.run(run()) == "timeout"
    assert manager.stats.timeouts == 1


def test_send_and_read_ack_exception(monkeypatch):
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def write(self, _data):
            return None

        async def drain(self):
            return None

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    manager = CloudSessionManager("example", 123)
    manager._writer = DummyWriter()

    async def fake_ensure():
        return None

    async def fake_read(**_):
        raise RuntimeError("boom")

    manager.ensure_connected = fake_ensure
    manager._read_one_ack_frame = fake_read

    async def run():
        try:
            await manager.send_and_read_ack(b"DATA", ack_timeout_s=0.1)
        except RuntimeError:
            return "error"
        return "no-error"

    assert asyncio.run(run()) == "error"
    assert manager.stats.errors == 1
