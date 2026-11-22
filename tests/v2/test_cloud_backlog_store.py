"""Exercise the durable measurement FIFO against a real SQLite file."""

import asyncio
from contextlib import closing
import sqlite3

import pytest

from proxy.cloud_backlog import CloudBacklog


@pytest.mark.asyncio
async def test_committed_frames_keep_fifo_order_and_exact_bytes_after_reopen(tmp_path):
    """Persist identity, sequence, and original wire bytes until acknowledged."""
    path = str(tmp_path / "backlog.db")
    store = CloudBacklog(path)
    assert not store.pending
    await store.open()
    assert not store.pending
    assert await store.oldest("box-a") is None

    await store.put("box-a", b"<Frame>first</Frame>\r\n")
    await store.put("box-b", b"<Frame>second</Frame>\n")
    await store.put("box-b", b"<Frame>third</Frame>\n")
    first = await store.oldest("box-a")
    assert first is not None
    assert store.pending
    await store.close()

    store = CloudBacklog(path)
    await store.open()
    assert store.pending
    assert store.pending_for("box-a") and store.pending_for("box-b")
    assert not store.pending_for("another-box")
    assert await store.oldest("box-a") == first
    assert first.device_id == "box-a"
    assert first.frame == b"<Frame>first</Frame>\r\n"
    assert not await store.acknowledge("box-a", first.sequence + 1)
    assert not await store.acknowledge("box-a", first.sequence - 1)

    second = await store.oldest("box-b")
    assert second is not None
    assert second.sequence > first.sequence
    assert second.device_id == "box-b"
    assert second.frame == b"<Frame>second</Frame>\n"
    assert not await store.acknowledge("box-b", second.sequence + 1)
    assert await store.acknowledge("box-b", second.sequence)
    assert await store.oldest("box-a") == first
    assert await store.acknowledge("box-a", first.sequence)
    assert not store.pending_for("box-a")
    third = await store.oldest("box-b")
    assert third is not None
    assert third.frame == b"<Frame>third</Frame>\n"
    assert await store.acknowledge("box-b", third.sequence)
    assert not store.pending
    assert await store.oldest("box-a") is None
    await store.close()


@pytest.mark.asyncio
async def test_deduplication_only_removes_delimiters_within_same_device_and_pending_rows(tmp_path):
    """Keep the original bytes and allow identical samples after delivery."""
    store = CloudBacklog(str(tmp_path / "backlog.db"))
    await store.open()
    await asyncio.gather(
        store.put("box-a", b"wire data\r\n"),
        store.put("box-a", b"wire data\n"),
        store.put("box-b", b"wire data"),
        store.put("box-a", b"wire data \n"),
    )
    rows = []
    for device in ("box-a", "box-b"):
        while (entry := await store.oldest(device)) is not None:
            rows.append(entry)
            assert await store.acknowledge(device, entry.sequence)
    assert [(row.device_id, row.frame) for row in rows] == [
        ("box-a", b"wire data\r\n"),
        ("box-a", b"wire data \n"),
        ("box-b", b"wire data"),
    ]

    await store.put("box-a", b"wire data")
    later = await store.oldest("box-a")
    assert later is not None
    assert later.sequence > max(entry.sequence for entry in rows)
    assert later.frame == b"wire data"
    await store.close()


@pytest.mark.asyncio
async def test_cancelled_put_can_commit_without_leaving_pending_false(tmp_path):
    """Cancel the caller while SQLite is waiting on a different writer."""
    path = str(tmp_path / "backlog.db")
    store = CloudBacklog(path)
    await store.open()
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        put = asyncio.create_task(store.put("box-a", b"test\r\n"))
        for _ in range(10):
            if store.pending_for("box-a"):
                break
            await asyncio.sleep(0)
        assert store.pending_for("box-a")
        assert not store.pending_for("box-b")
        put.cancel()
        with pytest.raises(asyncio.CancelledError):
            await put
        assert store.pending_for("box-a")
        writer.rollback()

    entry = await store.oldest("box-a")
    assert entry is not None
    assert entry.frame == b"test\r\n"
    assert store.pending
    await store.close()


@pytest.mark.asyncio
async def test_close_waits_for_cancelled_acknowledgment_to_finish(tmp_path):
    """A cancellation during deletion preserves the rest of the durable FIFO."""
    path = str(tmp_path / "backlog.db")
    store = CloudBacklog(path)
    await store.open()
    await store.put("box-a", b"first")
    await store.put("box-b", b"second")
    entry = await store.oldest("box-a")
    assert entry is not None
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        acknowledge = asyncio.create_task(store.acknowledge("box-a", entry.sequence))
        await asyncio.sleep(0)
        acknowledge.cancel()
        with pytest.raises(asyncio.CancelledError):
            await acknowledge
        assert store.pending
        writer.rollback()
    await store.close()

    await store.open()
    next_entry = await store.oldest("box-b")
    assert next_entry is not None
    assert next_entry.sequence > entry.sequence
    assert next_entry.frame == b"second"
    assert store.pending
    await store.close()


@pytest.mark.asyncio
async def test_failed_insert_or_delete_does_not_report_success(tmp_path):
    """Surface failed commits to callers without clearing a durable entry."""
    path = str(tmp_path / "backlog.db")
    store = CloudBacklog(path)
    await store.open()
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("""
            CREATE TRIGGER reject_insert BEFORE INSERT ON cloud_backlog
            BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END
        """)
        with pytest.raises(sqlite3.IntegrityError, match="injected insert failure"):
            await store.put("box-a", b"wire data")
        assert not store.pending

        writer.execute("DROP TRIGGER reject_insert")
        await store.put("box-a", b"wire data")
        entry = await store.oldest("box-a")
        assert entry is not None
        writer.execute("""
            CREATE TRIGGER reject_delete BEFORE DELETE ON cloud_backlog
            BEGIN SELECT RAISE(ABORT, 'injected delete failure'); END
        """)
        with pytest.raises(sqlite3.IntegrityError, match="injected delete failure"):
            await store.acknowledge("box-a", entry.sequence)
        assert store.pending
        assert await store.oldest("box-a") == entry
    await store.close()
