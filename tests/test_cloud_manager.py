import asyncio
import re

import cloud_manager
from oig_frame import compute_frame_checksum


def test_cloud_queue_drops_oldest(tmp_path):
    queue = cloud_manager.CloudQueue(
        db_path=str(tmp_path / "cloud.db"),
        max_size=2,
    )

    async def run():
        await queue.add(b"<Frame>1</Frame>", "t1", "DEV")
        await queue.add(b"<Frame>2</Frame>", "t2", "DEV")
        await queue.add(b"<Frame>3</Frame>", "t3", "DEV")
        assert queue.size() == 2

        item = await queue.get_next()
        assert item is not None
        _msg_id, table, payload = item
        assert table == "t2"
        assert payload == b"<Frame>2</Frame>"

    try:
        asyncio.run(run())
    finally:
        queue.conn.close()


def test_cloud_queue_defer_and_remove(tmp_path):
    queue = cloud_manager.CloudQueue(
        db_path=str(tmp_path / "cloud.db"),
        max_size=10,
    )

    async def run():
        await queue.add(b"<Frame>1</Frame>", "t1", "DEV")
        item = await queue.get_next()
        assert item is not None
        msg_id, table, _payload = item
        assert table == "t1"

        assert await queue.defer(msg_id, delay_s=60) is True
        assert await queue.get_next() is None

        assert await queue.remove(msg_id) is True
        assert queue.size() == 0

    try:
        asyncio.run(run())
    finally:
        queue.conn.close()


def test_ack_learner_generates_crc():
    learner = cloud_manager.ACKLearner()

    ack = learner.generate_ack("tbl_actual")
    assert "<Result>ACK</Result>" in ack

    end = learner.generate_ack("IsNewSet")
    assert "<Result>END</Result>" in end

    for payload in (ack, end):
        match = re.search(r"<CRC>(\d+)</CRC>", payload)
        assert match is not None
        crc = int(match.group(1))
        assert compute_frame_checksum(payload.encode("utf-8")) == crc


def test_cloud_queue_ready_age_and_clear(tmp_path):
    queue = cloud_manager.CloudQueue(
        db_path=str(tmp_path / "cloud.db"),
        max_size=10,
    )

    async def run():
        assert await queue.next_ready_in() is None
        await queue.add(b"<Frame>1</Frame>", "t1", "DEV")
        ready_in = await queue.next_ready_in()
        assert ready_in is not None
        assert ready_in <= 0.5

        assert queue.oldest_age() is not None
        queue.clear()
        assert queue.size() == 0

    try:
        asyncio.run(run())
    finally:
        queue.conn.close()


def test_cloud_health_checker_transitions(monkeypatch):
    events = []

    async def on_event(name: str):
        events.append(name)

    class DummyWriter:
        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_open(_host, _port):
        return asyncio.StreamReader(), DummyWriter()

    checker = cloud_manager.CloudHealthChecker(
        host="example",
        port=123,
        fail_threshold=1,
        success_threshold=1,
        timeout=0.1,
    )
    checker.is_online = False
    checker.set_mode_callback(on_event)
    monkeypatch.setattr(cloud_manager, "resolve_cloud_host", lambda host: host)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    asyncio.run(checker.check_health())
    assert checker.is_online is True
    assert events == ["cloud_recovered"]

    async def fake_fail(_host, _port):
        raise RuntimeError("fail")

    checker.is_online = True
    monkeypatch.setattr(asyncio, "open_connection", fake_fail)
    asyncio.run(checker.check_health())
    assert checker.is_online is False
    assert "cloud_down" in events


def test_cloud_queue_without_frame_bytes(tmp_path):
    queue = cloud_manager.CloudQueue(
        db_path=str(tmp_path / "cloud.db"),
        max_size=10,
    )
    queue._has_frame_bytes = False

    async def run():
        await queue.add(b"<Frame>1</Frame>", "t1", "DEV")
        item = await queue.get_next()
        assert item is not None
        _msg_id, table, payload = item
        assert table == "t1"
        assert payload == b"<Frame>1</Frame>"

    try:
        asyncio.run(run())
    finally:
        queue.conn.close()


def test_cloud_health_check_loop_runs(monkeypatch):
    checker = cloud_manager.CloudHealthChecker(
        host="example",
        port=123,
        check_interval=1,
    )
    calls = {"check": 0, "sleep": 0}

    async def fake_check():
        calls["check"] += 1
        return True

    async def fake_sleep(_interval):
        calls["sleep"] += 1
        if calls["sleep"] == 1:
            return None
        raise RuntimeError("stop")

    checker.check_health = fake_check
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(checker.health_check_loop())
    except RuntimeError:
        pass

    assert calls["check"] == 1


def test_cloud_health_start_creates_task(monkeypatch):
    checker = cloud_manager.CloudHealthChecker()

    async def fake_loop():
        return None

    checker.health_check_loop = fake_loop
    asyncio.run(checker.start())
    assert checker._health_check_task is not None


def test_ack_learner_noop():
    learner = cloud_manager.ACKLearner()
    learner.learn_from_cloud("<Frame></Frame>", "tbl_actual")
