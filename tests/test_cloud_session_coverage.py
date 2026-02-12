"""Tests for cloud_session.py coverage."""

import cloud_session
from cloud_session import CloudSessionManager, CloudStats

def test_cloud_session_manager_init():
    """Test CloudSessionManager initialization."""
    stats = CloudStats()
    manager = CloudSessionManager(
        host="example.com",
        port=8080,
        stats=stats,
        connect_timeout_s=5.0,
    )
    
    assert manager.host == "example.com"
    assert manager.port == 8080
    assert manager.stats is stats
    assert manager.connect_timeout_s == 5.0
    assert manager._reader is None
    assert manager._writer is None
    assert manager._rx_buf == bytearray()
    assert manager._conn_lock is not None
    assert manager._io_lock is not None
    assert manager._last_connect_attempt == 0.0
    assert manager._last_warn_ts == 0.0
    assert manager.backoff is not None
    assert manager.backoff.get_backoff_delay() > 0

def test_cloud_session_manager_get_stats_sync_initial():
    """Test CloudSessionManager._get_stats_sync."""
    manager = CloudSessionManager("example.com", 8080)
    
    assert manager._get_stats_sync() == manager.stats
    assert isinstance(manager._get_stats_sync(), CloudStats)
    assert manager._get_stats_sync().connects == 0
    assert manager._get_stats_sync().disconnects == 0


def test_cloud_session_manager_get_stats_sync_connects():
    """Test CloudSessionManager._get_stats_sync after updates."""
    manager = CloudSessionManager("example.com", 8080)
    manager.stats.connects = 2
    assert manager._get_stats_sync().connects == 2

def test_cloud_session_manager_is_connected_sync():
    """Test CloudSessionManager._is_connected_sync."""
    manager = CloudSessionManager("example.com", 8080)
    
    assert manager._is_connected_sync() is False
    
    class DummyWriter:
        def __init__(self):
            self._closing = False

        def is_closing(self):
            return self._closing

    manager._writer = DummyWriter()
    assert manager._is_connected_sync() is True
    
    manager._writer._closing = True
    assert manager._is_connected_sync() is False

def test_cloud_session_manager_get_backoff_s_sync():
    """Test CloudSessionManager._get_backoff_delay_sync."""
    manager = CloudSessionManager("example.com", 8080)
    assert manager._get_backoff_delay_sync() > 0

def test_cloud_session_manager_set_last_connect_attempt_sync():
    """Test CloudSessionManager._set_last_connect_attempt_sync."""
    manager = CloudSessionManager("example.com", 8080)
    
    timestamp = 123.456
    manager._set_last_connect_attempt_sync(timestamp)
    assert manager._last_connect_attempt == timestamp


def test_cloud_session_manager_reset_backoff_sync():
    """Test CloudSessionManager._reset_backoff_sync."""
    manager = CloudSessionManager("example.com", 8080)
    
    assert manager._get_backoff_delay_sync() > 0

    manager._reset_backoff_sync()
    assert manager._get_backoff_delay_sync() > 0


def test_cloud_session_manager_get_stats_sync():
    """Test CloudSessionManager._get_stats_sync."""
    manager = CloudSessionManager("example.com", 8080)
    
    assert manager._get_stats_sync() == manager.stats
    assert isinstance(manager._get_stats_sync(), CloudStats)
    assert manager._get_stats_sync().connects == 0

def test_cloud_session_manager_read_until_frame_sync_basic():
    """Test CloudSessionManager._read_until_frame_sync basic behavior."""
    manager = CloudSessionManager("example.com", 8080)
    class FakeReader:
        def __init__(self):
            self.data = b""
        
        def read(self, size):
            return self.data
    
    buf = bytearray()
    reader_inst = FakeReader()
    
    result, buf_after = manager._read_until_frame_sync(
        reader_inst,
        buf,
        ack_max_bytes=100,
    )
    
    assert result == b""
    assert buf_after == bytearray()
    assert len(buf_after) == 0

def test_cloud_session_manager_read_until_frame_sync_with_frame():
    """Test CloudSessionManager._read_until_frame_sync finds frame."""
    manager = CloudSessionManager("example.com", 8080)
    
    class FakeReader:
        def __init__(self):
            self.call_count = 0
            self.data = b"<Frame>test</Frame>\n"
        
        def read(self, size):
            self.call_count += 1
            return self.data
    
    buf = bytearray()
    reader_inst = FakeReader()
    
    result, buf_after = manager._read_until_frame_sync(
        reader_inst,
        buf,
        ack_max_bytes=100,
    )
    
    assert result == b"<Frame>test</Frame>\n"
    assert buf_after == bytearray()
    assert reader_inst.call_count == 1


def test_cloud_session_manager_read_until_frame_sync_max_bytes():
    """Test CloudSessionManager._read_until_frame_sync max_bytes handling."""
    manager = CloudSessionManager("example.com", 8080)
    
    class FakeReader:
        def __init__(self):
            self.call_count = 0
            self.data = b"<Frame>test" + (b"X" * 200)
        
        def read(self, size):
            self.call_count += 1
            return self.data
    
    buf = bytearray()
    reader_inst = FakeReader()
    
    result, buf_after = manager._read_until_frame_sync(
        reader_inst,
        buf,
        ack_max_bytes=50,
    )
    
    assert result == b"<Frame>test" + (b"X" * 200)
    assert buf_after == bytearray()
    assert reader_inst.call_count == 1
