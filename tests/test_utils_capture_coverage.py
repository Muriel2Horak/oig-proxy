"""Additional coverage tests for utils capture paths."""

import queue
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import utils


def test_load_sensor_map_exception(monkeypatch, tmp_path):
    map_path = tmp_path / "map.json"
    map_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(utils, "SENSOR_MAP_PATH", str(map_path))
    monkeypatch.setattr(utils, "MAP_RELOAD_SECONDS", 0)
    monkeypatch.setattr(utils, "_last_map_load", 0.0)

    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(utils, "_load_json_file", boom)

    with patch("utils.logger") as mock_logger:
        utils.load_sensor_map()
        assert any("Sensor map load failed" in call.args[0] for call in mock_logger.warning.call_args_list)


def test_init_capture_db_pragma_error(monkeypatch, tmp_path):
    class DummyConn:
        def __init__(self):
            self.closed = False

        def execute(self, sql, params=None):
            if sql.startswith("PRAGMA journal_mode"):
                raise sqlite3.Error("pragma fail")
            if sql.startswith("PRAGMA synchronous"):
                raise sqlite3.Error("pragma fail")
            if sql.startswith("PRAGMA temp_store"):
                raise sqlite3.Error("pragma fail")
            if sql.startswith("PRAGMA busy_timeout"):
                raise sqlite3.Error("pragma fail")
            if sql.startswith("PRAGMA table_info"):
                return [(0, "id")]
            return []

        def commit(self):
            return None

        def close(self):
            self.closed = True

    def fake_connect(*_args, **_kwargs):
        return DummyConn()

    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "CAPTURE_DB_PATH", str(tmp_path / "cap.db"))
    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with patch("utils.logger") as mock_logger:
        conn, _cols = utils.init_capture_db()
        assert conn is not None
        assert any("Capture DB pragma setup failed" in call.args[0] for call in mock_logger.debug.call_args_list)


def test_init_capture_db_column_add_commit(monkeypatch, tmp_path):
    class DummyConn:
        def __init__(self):
            self.commits = 0

        def execute(self, _sql, _params=None):
            if _sql.startswith("PRAGMA table_info"):
                return [(0, "id")]
            return []

        def commit(self):
            self.commits += 1

    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "CAPTURE_DB_PATH", str(tmp_path / "cap.db"))
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: DummyConn())

    conn, _cols = utils.init_capture_db()
    assert conn is not None
    assert conn.commits >= 1


def test_commit_capture_batch_empty():
    conn = MagicMock()
    utils._commit_capture_batch(conn, "SQL", [])
    conn.executemany.assert_not_called()


def test_commit_capture_batch_success():
    conn = MagicMock()
    utils._commit_capture_batch(conn, "SQL", [("a",)])
    conn.executemany.assert_called_once()
    conn.commit.assert_called_once()


def test_capture_loop_empty_then_stop(monkeypatch):
    class DummyQueue:
        def __init__(self):
            self.calls = 0

        def get(self, timeout=1.0):
            self.calls += 1
            if self.calls == 1:
                raise queue.Empty()
            raise StopIteration("stop")

    commit_calls = {"count": 0}

    def fake_commit(_conn, _sql, _batch):
        commit_calls["count"] += 1

    def fake_prune(_conn):
        commit_calls["count"] += 1

    monkeypatch.setattr(utils, "CAPTURE_RETENTION_DAYS", 1)
    monkeypatch.setattr(utils, "_commit_capture_batch", fake_commit)
    monkeypatch.setattr(utils, "_prune_capture_db", fake_prune)

    times = [0.0, 0.0, 601.0, 601.0]
    with patch("utils.time.time", side_effect=times):
        with pytest.raises(StopIteration):
            utils._capture_loop(MagicMock(), "SQL", DummyQueue())

    assert commit_calls["count"] >= 2


def test_capture_loop_batch_commit(monkeypatch):
    class DummyQueue:
        def __init__(self):
            self.calls = 0

        def get(self, timeout=1.0):
            self.calls += 1
            if self.calls == 1:
                return ("a",)
            raise StopIteration("stop")

    commit_calls = {"count": 0}

    def fake_commit(_conn, _sql, _batch):
        commit_calls["count"] += 1

    monkeypatch.setattr(utils, "CAPTURE_RETENTION_DAYS", 1)
    monkeypatch.setattr(utils, "_commit_capture_batch", fake_commit)
    monkeypatch.setattr(utils, "_prune_capture_db", lambda _conn: None)

    times = [0.0, 1.0, 1.0, 601.0]
    with patch("utils.time.time", side_effect=times):
        with pytest.raises(StopIteration):
            utils._capture_loop(MagicMock(), "SQL", DummyQueue())

    assert commit_calls["count"] >= 1


def test_prune_capture_db_return(monkeypatch):
    monkeypatch.setattr(utils, "CAPTURE_RETENTION_DAYS", 0)
    utils._prune_capture_db(MagicMock())


def test_prune_capture_db_checkpoint(monkeypatch):
    class DummyCursor:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class DummyConn:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append(sql)
            if sql.startswith("DELETE FROM frames"):
                return DummyCursor(rowcount=1)
            return DummyCursor(rowcount=0)

        def commit(self):
            return None

    monkeypatch.setattr(utils, "CAPTURE_RETENTION_DAYS", 1)
    conn = DummyConn()

    utils._prune_capture_db(conn)
    assert any("wal_checkpoint" in sql for sql in conn.executed)


def test_prune_capture_db_error(monkeypatch):
    class DummyConn:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def commit(self):
            return None

    monkeypatch.setattr(utils, "CAPTURE_RETENTION_DAYS", 1)
    with patch("utils.logger") as mock_logger:
        utils._prune_capture_db(DummyConn())
        assert any("Capture DB prune failed" in call.args[0] for call in mock_logger.debug.call_args_list)


def test_capture_payload_disabled(monkeypatch):
    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", False)
    utils.capture_payload(
        device_id="DEV1",
        table="tbl_box_prms",
        raw="<Frame>1</Frame>",
        raw_bytes=None,
        parsed={},
    )


def test_capture_payload_close_error(monkeypatch, tmp_path):
    class DummyConn:
        def close(self):
            raise sqlite3.Error("close fail")

    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "CAPTURE_DB_PATH", str(tmp_path / "cap.db"))
    monkeypatch.setattr(utils, "_capture_queue", None)
    monkeypatch.setattr(utils, "_capture_thread", None)
    monkeypatch.setattr(utils, "_capture_cols", set())
    monkeypatch.setattr(utils, "init_capture_db", lambda: (DummyConn(), set()))

    with patch("utils.logger") as mock_logger:
        utils.capture_payload(
            device_id="DEV1",
            table="tbl_box_prms",
            raw="<Frame>1</Frame>",
            raw_bytes=None,
            parsed={},
        )
        assert any("Capture DB close failed" in call.args[0] for call in mock_logger.debug.call_args_list)


def test_capture_payload_queue_missing(monkeypatch):
    class DummyThread:
        def is_alive(self):
            return True

        def start(self):
            return None

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "_capture_queue", None)
    monkeypatch.setattr(utils, "_capture_thread", None)
    monkeypatch.setattr(utils, "_capture_cols", set())
    monkeypatch.setattr(utils, "init_capture_db", lambda: (DummyConn(), set()))
    monkeypatch.setattr(utils.queue, "Queue", lambda maxsize=0: None)
    monkeypatch.setattr(utils.threading, "Thread", lambda **_kwargs: DummyThread())

    with patch("utils.logger") as mock_logger:
        utils.capture_payload(
            device_id="DEV1",
            table="tbl_box_prms",
            raw="<Frame>1</Frame>",
            raw_bytes=None,
            parsed={},
        )
        assert any("Capture queue missing" in call.args[0] for call in mock_logger.debug.call_args_list)


def test_capture_payload_queue_full_logs(monkeypatch):
    q = MagicMock()
    q.put_nowait.side_effect = queue.Full()

    class DummyThread:
        def is_alive(self):
            return True

    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "_capture_queue", q)
    monkeypatch.setattr(utils, "_capture_thread", DummyThread())
    monkeypatch.setattr(utils, "_capture_cols", set())

    with patch("utils.logger") as mock_logger:
        utils.capture_payload(
            device_id="DEV1",
            table="tbl_box_prms",
            raw="<Frame>1</Frame>",
            raw_bytes=None,
            parsed={},
        )
        assert any("Capture queue full" in call.args[0] for call in mock_logger.debug.call_args_list)


def test_capture_payload_exception(monkeypatch):
    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "_capture_queue", queue.Queue(maxsize=1))
    monkeypatch.setattr(utils, "_capture_thread", MagicMock(is_alive=lambda: True))
    monkeypatch.setattr(utils, "_capture_cols", set())
    monkeypatch.setattr(utils.json, "dumps", MagicMock(side_effect=RuntimeError("boom")))

    with patch("utils.logger") as mock_logger:
        utils.capture_payload(
            device_id="DEV1",
            table="tbl_box_prms",
            raw="<Frame>1</Frame>",
            raw_bytes=None,
            parsed={},
        )
        assert any("Capture payload failed" in call.args[0] for call in mock_logger.debug.call_args_list)


def test_module_init_close_error(monkeypatch):
    source = Path(utils.__file__).read_text()

    class DummyConn:
        def execute(self, *_args, **_kwargs):
            return []

        def commit(self):
            return None

        def close(self):
            raise sqlite3.Error("close fail")

    import config

    monkeypatch.setattr(config, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(config, "CAPTURE_DB_PATH", ":memory:")
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: DummyConn())

    globs: dict = {
        "__name__": "utils_close_error",
        "__file__": utils.__file__,
    }
    exec(compile(source, utils.__file__, "exec"), globs)
