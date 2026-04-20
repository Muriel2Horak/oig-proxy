"""Coverage tests for capture modules."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access,too-few-public-methods

from __future__ import annotations

import asyncio
import base64
import io
import queue
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from capture import frame_capture
from capture import pcap_capture


def _create_frames_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            device_id TEXT,
            table_name TEXT,
            raw TEXT,
            raw_b64 TEXT,
            parsed TEXT,
            direction TEXT,
            conn_id INTEGER,
            peer TEXT,
            length INTEGER
        )
        """
    )
    conn.commit()


def test_frame_capture_start_capture_stop_and_schema_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "frames.db"
    capture = frame_capture.FrameCapture(
        db_path=str(db_path),
        capture_raw_bytes=True,
        retention_days=0,
    )
    capture.start()
    capture.capture(
        "dev-1",
        "tbl_set",
        "<Frame>payload</Frame>",
        b"\x01\x02",
        {"value": 1},
        direction="box_to_proxy",
        conn_id=7,
        peer="1.2.3.4:5710",
        length=2,
    )
    capture.stop()

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT device_id, table_name, raw, raw_b64, parsed, direction, conn_id, peer, length FROM frames"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "dev-1"
    assert row[1] == "tbl_set"
    assert row[2] == "<Frame>payload</Frame>"
    assert row[3] == base64.b64encode(b"\x01\x02").decode("ascii")
    assert row[4] == '{"value": 1}'
    assert row[5:] == ("box_to_proxy", 7, "1.2.3.4:5710", 2)

    legacy_db = tmp_path / "legacy.db"
    legacy_conn = sqlite3.connect(legacy_db)
    legacy_conn.execute(
        """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            device_id TEXT,
            table_name TEXT,
            raw TEXT,
            parsed TEXT
        )
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    legacy_capture = frame_capture.FrameCapture(db_path=str(legacy_db))
    legacy_capture._ensure_schema()

    upgraded = sqlite3.connect(legacy_db)
    columns = {row[1] for row in upgraded.execute("PRAGMA table_info(frames)")}
    upgraded.close()
    assert {"raw_b64", "direction", "conn_id", "peer", "length"}.issubset(columns)


def test_frame_capture_capture_error_paths_and_writer_open_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    capture = frame_capture.FrameCapture(db_path=str(tmp_path / "queue.db"))
    capture._queue = queue.Queue(maxsize=1)
    capture._queue.put_nowait(("filled",))
    capture.capture("dev", "tbl", "raw", None, {})

    monkeypatch.setattr(frame_capture, "_iso_now", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    capture.capture("dev", "tbl", "raw", None, {})

    broken_capture = frame_capture.FrameCapture(db_path=str(tmp_path / "broken.db"))
    monkeypatch.setattr(frame_capture.sqlite3, "connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("db down")))
    broken_capture._writer_loop()


def test_frame_capture_helper_functions_and_timed_out_writer_loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "helpers.db"
    conn = sqlite3.connect(db_path)
    _create_frames_table(conn)
    frame_capture._configure_pragmas(conn)
    frame_capture._commit_batch(
        conn,
        [
            ("2000-01-01T00:00:00+00:00", "old", "tbl", "raw", None, "{}", None, None, None, None),
            (frame_capture._iso_now(), "new", "tbl", "raw", None, "{}", None, None, None, None),
        ],
    )
    frame_capture._prune_db(conn, retention_days=1)
    remaining = conn.execute("SELECT device_id FROM frames ORDER BY id ASC").fetchall()
    assert remaining == [("new",)]
    frame_capture._prune_db(conn, retention_days=0)
    conn.close()

    class BadConn:
        def __init__(self) -> None:
            self.rollback_called = False

        def executemany(self, *_args, **_kwargs) -> None:
            raise RuntimeError("write failed")

        def commit(self) -> None:
            raise AssertionError("commit should not be called")

        def rollback(self) -> None:
            self.rollback_called = True

    bad_conn = BadConn()
    frame_capture._commit_batch(bad_conn, [(1,)])
    assert bad_conn.rollback_called is True

    timed_db = tmp_path / "timed.db"
    timed_capture = frame_capture.FrameCapture(db_path=str(timed_db), retention_days=0)

    class FakeQueue:
        # pylint: disable=useless-return
        def __init__(self) -> None:
            self.calls = 0

        def get(self, timeout: float):
            self.calls += 1
            if self.calls == 1:
                raise queue.Empty
            return None  # noqa: R1711

    timed_capture._queue = FakeQueue()  # type: ignore[assignment]
    monotonic_values = iter([0.0, 0.6, 1.2])
    monkeypatch.setattr(frame_capture.time, "monotonic", lambda: next(monotonic_values))
    timed_capture._writer_loop()
    assert timed_db.exists()


def test_frame_capture_stop_queue_full_and_prune_failure(tmp_path: Path) -> None:
    capture = frame_capture.FrameCapture(db_path=str(tmp_path / "stop.db"))

    class FakeThread:
        def __init__(self) -> None:
            self.joined = False

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.joined = True

    class FullQueue:
        def put(self, item, timeout: float | None = None) -> None:
            raise queue.Full

    fake_thread = FakeThread()
    capture._thread = fake_thread
    capture._queue = FullQueue()  # type: ignore[assignment]
    capture.stop()
    assert fake_thread.joined is True

    class BadConn:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("prune failed")

    frame_capture._prune_db(BadConn(), retention_days=1)


def test_find_tcpdump_build_cmd_start_and_stop_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pcap_capture.os.path, "isfile", lambda path: path == "/usr/bin/tcpdump")
    monkeypatch.setattr(pcap_capture.os, "access", lambda path, mode: path == "/usr/bin/tcpdump")
    assert pcap_capture._find_tcpdump() == "/usr/bin/tcpdump"
    monkeypatch.setattr(pcap_capture.os.path, "isfile", lambda path: False)
    monkeypatch.setattr(pcap_capture.os, "access", lambda path, mode: False)
    assert pcap_capture._find_tcpdump() is None
    monkeypatch.setattr(pcap_capture.os.path, "isfile", lambda path: path == "/usr/bin/tcpdump")
    monkeypatch.setattr(pcap_capture.os, "access", lambda path, mode: path == "/usr/bin/tcpdump")

    capture = pcap_capture.PcapCapture(
        port=9000,
        pcap_path=str(tmp_path / "capture.pcap"),
        interface="eth0",
        max_size_mb=10,
        snaplen=128,
    )
    assert capture._build_cmd("/usr/bin/tcpdump") == [
        "/usr/bin/tcpdump",
        "-i",
        "eth0",
        "-s",
        "128",
        "-w",
        str(tmp_path / "capture.pcap"),
        "tcp port 9000",
        "-C",
        "10",
    ]
    assert "-C" not in pcap_capture.PcapCapture(max_size_mb=0)._build_cmd("tcpdump")

    monkeypatch.setattr(pcap_capture, "_find_tcpdump", lambda: None)
    capture.start()
    assert capture._process is None

    monkeypatch.setattr(pcap_capture, "_find_tcpdump", lambda: "/usr/bin/tcpdump")
    monkeypatch.setattr(pcap_capture.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))
    capture.start()
    assert capture._process is None

    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 123
            self.stderr = io.BytesIO(b"")
            self.signal_calls: list[int] = []
            self.kill_called = False
            self.wait_calls = 0

        def send_signal(self, sig: int) -> None:
            self.signal_calls.append(sig)

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            return 0

        def kill(self) -> None:
            self.kill_called = True

        def poll(self) -> int | None:
            return None

    fake_process = FakeProcess()
    monkeypatch.setattr(pcap_capture.subprocess, "Popen", lambda *args, **kwargs: fake_process)
    capture.start()
    assert capture.is_running is True
    capture.stop()
    assert fake_process.signal_calls == [pcap_capture.signal.SIGTERM]
    assert capture._process is None
    assert capture.is_running is False

    class BrokenProcess(FakeProcess):
        def send_signal(self, sig: int) -> None:
            raise OSError("signal failed")

    capture._process = BrokenProcess()
    capture.stop()
    assert capture._process is None


@pytest.mark.asyncio
async def test_pcap_start_async_monitor_and_stop_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 456
            self.stderr = io.BytesIO(b"stderr output")
            self.kill_called = False
            self.poll_values = [None, 1]
            self.wait_calls = 0

        def poll(self) -> int | None:
            return self.poll_values.pop(0) if self.poll_values else 1

        def send_signal(self, _sig: int) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise pcap_capture.subprocess.TimeoutExpired(cmd="tcpdump", timeout=timeout or 0)
            return 0

        def kill(self) -> None:
            self.kill_called = True

    fake_process = FakeProcess()
    capture = pcap_capture.PcapCapture(pcap_path=str(tmp_path / "async.pcap"))
    monkeypatch.setattr(pcap_capture, "_find_tcpdump", lambda: "/usr/bin/tcpdump")
    monkeypatch.setattr(pcap_capture.subprocess, "Popen", lambda *args, **kwargs: fake_process)

    monkeypatch.setattr(pcap_capture.asyncio, "sleep", AsyncMock(return_value=None))

    await capture.start_async()
    assert capture._monitor_task is not None
    await capture._monitor_task
    assert capture._process is None

    capture._process = fake_process
    capture._monitor_task = asyncio.create_task(asyncio.Event().wait())
    capture.stop()
    assert fake_process.kill_called is True
    assert capture._monitor_task is None


@pytest.mark.asyncio
async def test_pcap_monitor_process_normal_exit_and_none_process(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = pcap_capture.PcapCapture()
    await capture._monitor_process()

    class FakeProcess:
        def __init__(self) -> None:
            self.stderr = io.BytesIO(b"")

        def poll(self) -> int | None:
            return 0

    capture._process = FakeProcess()
    monkeypatch.setattr(pcap_capture.asyncio, "sleep", AsyncMock(return_value=None))
    await capture._monitor_process()
    assert capture._process is None


@pytest.mark.asyncio
async def test_pcap_monitor_process_stderr_read_error_and_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    class ErrorReadProcess:
        def __init__(self) -> None:
            self.stderr = SimpleNamespace(read=lambda _size: (_ for _ in ()).throw(OSError("read failed")))

        def poll(self) -> int | None:
            return 2

    capture = pcap_capture.PcapCapture()
    capture._process = ErrorReadProcess()
    monkeypatch.setattr(pcap_capture.asyncio, "sleep", AsyncMock(return_value=None))
    await capture._monitor_process()
    assert capture._process is None

    class RunningProcess:
        def poll(self) -> int | None:
            return None

    async def cancelled_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    capture._process = RunningProcess()
    monkeypatch.setattr(pcap_capture.asyncio, "sleep", cancelled_sleep)
    await capture._monitor_process()
    assert capture._process is not None
