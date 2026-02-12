# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code,unused-variable
import asyncio
import time

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

        def wait_closed(self):
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

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1)
    manager.backoff.initial_backoff_s = 0.1
    try:
        asyncio.run(manager.ensure_connected())
    except asyncio.TimeoutError:
        pass
    assert manager.stats.timeouts == 1
    assert manager.stats.errors == 1
    assert manager.backoff.get_backoff_delay() >= 0.2


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
    ack = asyncio.run(
        manager._read_one_ack_frame(
            ack_timeout_s=0.1,
            ack_max_bytes=1024))
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

    ack = asyncio.run(
        manager._read_one_ack_frame(
            ack_timeout_s=0.1,
            ack_max_bytes=5))
    assert ack == b"x" * 10


def test_ensure_connected_skips_when_connected():
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        def wait_closed(self):
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

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1)
    manager.backoff.initial_backoff_s = 0.1
    try:
        asyncio.run(manager.ensure_connected())
    except RuntimeError:
        pass
    assert manager.stats.errors == 1
    assert manager.backoff.get_backoff_delay() >= 0.2


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


def test_close_counts_disconnect(monkeypatch):
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

    async def run():
        await manager.close(count_disconnect=True)
        return manager.stats.disconnects

    assert asyncio.run(run()) == 1


def test_extract_one_xml_frame_handles_cr():
    buf = bytearray(b"<Frame>1</Frame>\r\n")
    first = CloudSessionManager._extract_one_xml_frame(buf)
    assert first == b"<Frame>1</Frame>\r\n"
    assert CloudSessionManager._extract_one_xml_frame(buf) is None


def test_extract_one_xml_frame_single_cr():
    buf = bytearray(b"<Frame>1</Frame>\r")
    assert CloudSessionManager._extract_one_xml_frame(buf) is None
    assert buf == bytearray(b"<Frame>1</Frame>\r")

    buf = bytearray(b"<Frame>1</Frame>\rX")
    result = CloudSessionManager._extract_one_xml_frame(buf)
    assert result == b"<Frame>1</Frame>\r"
    assert buf == bytearray(b"X")


def test_read_one_ack_frame_eof():
    class DummyReader:
        async def read(self, _size):
            return b""

    manager = CloudSessionManager("example", 123)
    manager._reader = DummyReader()
    manager._rx_buf = bytearray()

    async def run():
        ack = await manager._read_one_ack_frame(ack_timeout_s=0.1, ack_max_bytes=1024)
        return ack

    assert asyncio.run(run()) == b""


def test_read_one_ack_frame_after_extension():
    class DummyReader:
        async def read(self, _size):
            return b"</Frame>"

    manager = CloudSessionManager("example", 123)
    manager._reader = DummyReader()
    manager._rx_buf = bytearray(b"<Frame>1")

    async def run():
        ack = await manager._read_one_ack_frame(ack_timeout_s=0.1, ack_max_bytes=1024)
        return ack

    assert asyncio.run(run()) == b"<Frame>1</Frame>"


def test_ensure_connected_backoff_sleep(monkeypatch):
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    sleep_called = []

    async def fake_sleep(delay):
        sleep_called.append(delay)

    async def fake_open(_host, _port):
        return asyncio.StreamReader(), DummyWriter()

    monkeypatch.setattr(cloud_session, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1)
    manager.backoff.initial_backoff_s = 0.05
    manager._last_connect_attempt = time.monotonic()

    asyncio.run(manager.ensure_connected())
    assert len(sleep_called) > 0


def test_ensure_connected_double_check_in_lock(monkeypatch):
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        async def wait_closed(self):
            return None

    connect_count = {"value": 0}
    lock_acquired = {"value": False}

    async def fake_open(_host, _port):
        connect_count["value"] += 1
        return asyncio.StreamReader(), DummyWriter()

    class FakeLock:
        async def __aenter__(self):
            lock_acquired["value"] = True
            manager._writer = DummyWriter()
            return self

        async def __aexit__(self, *_args):
            lock_acquired["value"] = False
            return None

    monkeypatch.setattr(cloud_session, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1)
    manager._conn_lock = FakeLock()

    asyncio.run(manager.ensure_connected())
    assert manager.is_connected() is True
    assert connect_count["value"] == 0


def test_ensure_connected_connects_within_lock(monkeypatch):
    call_count = {"value": 0}

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
        call_count["value"] += 1
        return asyncio.StreamReader(), DummyWriter()

    monkeypatch.setattr(cloud_session, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    manager = CloudSessionManager("example", 123, connect_timeout_s=0.1)

    asyncio.run(manager.ensure_connected())
    assert call_count["value"] == 1
    assert manager.is_connected() is True


def test_read_until_frame_sync():
    class FakeReader:
        def __init__(self, chunks):
            self.chunks = chunks
            self.idx = 0

        def read(self, _size):
            if self.idx < len(self.chunks):
                chunk = self.chunks[self.idx]
                self.idx += 1
                return chunk
            return b""

    manager = CloudSessionManager("example", 123)
    buf = bytearray()

    reader = FakeReader([b"<Frame>1</Frame>", b""])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 1024)
    assert frame == b"<Frame>1</Frame>"

    buf = bytearray()
    reader = FakeReader([b"<Frame>1</Frame>\r\n", b""])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 1024)
    assert frame == b"<Frame>1</Frame>\r\n"

    buf = bytearray()
    reader = FakeReader([b"<Frame>1</Frame>\n", b""])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 1024)
    assert frame == b"<Frame>1</Frame>\n"

    buf = bytearray()
    reader = FakeReader([b"<Frame>1</Frame>\r", b"X"])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 1024)
    assert frame == b"<Frame>1</Frame>\r"

    buf = bytearray()
    reader = FakeReader([b"<Frame>1</Frame>\r"])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 1024)
    assert frame == b""
    assert new_buf == bytearray(b"<Frame>1</Frame>\r")

    buf = bytearray(b"X" * 4096)
    reader = FakeReader([b"X" * 100, b""])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 4096)
    assert len(frame) > 4096
    assert new_buf == bytearray()

    buf = bytearray(b"X" * 4096)
    reader = FakeReader([b"X"])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 4096)
    assert len(frame) == 4097
    assert new_buf == bytearray()

    buf = bytearray(b"<Frame>1</Frame>\r")
    reader = FakeReader([b"\n", b""])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 4096)
    assert frame == b"<Frame>1</Frame>\r\n"
    assert new_buf == bytearray()

    buf = bytearray(b"<Frame>1</Frame>\r")
    reader = FakeReader([b"\n", b""])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 4096)
    assert frame == b"<Frame>1</Frame>\r\n"
    assert new_buf == bytearray()

    buf = bytearray(b"<Frame>1</Frame>\r")
    reader = FakeReader([b""])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 4096)
    assert frame == b""
    assert new_buf == bytearray(b"<Frame>1</Frame>\r")

    buf = bytearray(b"<Frame>1</Frame>")
    reader = FakeReader([b"\r"])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 4096)
    assert frame == b""
    assert new_buf == bytearray(b"<Frame>1</Frame>\r")

    buf = bytearray(b"<Frame>")
    reader = FakeReader([b"1", b"</Frame>"])
    frame, new_buf = manager._read_until_frame_sync(reader, buf, 4096)
    assert frame == b"<Frame>1</Frame>"
    assert new_buf == bytearray()


def test_read_one_ack_frame_no_reader():
    manager = CloudSessionManager("example", 123)
    manager._reader = None

    async def run():
        try:
            await manager._read_one_ack_frame(ack_timeout_s=0.1, ack_max_bytes=1024)
            return "no-error"
        except ConnectionError:
            return "error"

    assert asyncio.run(run()) == "error"


def test_send_and_read_ack_no_writer(monkeypatch):
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

    async def fake_ensure():
        manager._writer = None

    manager.ensure_connected = fake_ensure

    async def run():
        try:
            await manager.send_and_read_ack(b"DATA", ack_timeout_s=0.1)
            return "no-error"
        except ConnectionError:
            return "error"

    assert asyncio.run(run()) == "error"


def test_sync_methods():
    manager = CloudSessionManager("example", 123)
    manager._writer = None

    assert manager._is_connected_sync() is False
    stats = manager._get_stats_sync()
    assert stats.connects == 0
    assert stats.disconnects == 0
    assert stats.errors == 0
    assert stats.timeouts == 0

    delay = manager._get_backoff_delay_sync()
    assert delay > 0

    manager._reset_backoff_sync()
    assert manager.backoff._attempt == 0

    manager._record_failure_sync()
    assert manager.backoff._attempt == 1

    manager._set_last_connect_attempt_sync(123.456)
    assert manager._last_connect_attempt == 123.456
