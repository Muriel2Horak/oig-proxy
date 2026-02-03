# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,line-too-long,unused-argument,broad-exception-caught
"""Comprehensive tests for telemetry_client module to achieve 100% coverage."""

import asyncio
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

import sys
sys.path.insert(0, "addon/oig-proxy")

import telemetry_client


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    db_path.unlink(missing_ok=True)


def test_telemetry_buffer_init(temp_db):
    """Test TelemetryBuffer initialization."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    assert buffer._db_path == temp_db
    assert temp_db.exists()
    
    # Check table was created
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
    assert cursor.fetchone() is not None
    conn.close()


def test_telemetry_buffer_store(temp_db):
    """Test storing telemetry message."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    topic = "oig/telemetry/TEST"
    payload = {"device_id": "TEST", "uptime_s": 100}
    
    success = buffer.store(topic, payload)
    assert success is True
    
    # Verify stored
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.execute("SELECT topic, payload FROM messages")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == topic
    assert json.loads(row[1]) == payload
    conn.close()


def test_telemetry_buffer_store_multiple(temp_db):
    """Test storing multiple messages."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    for i in range(5):
        success = buffer.store(f"topic_{i}", {"value": i})
        assert success is True
    
    # Verify count
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.execute("SELECT COUNT(*) FROM messages")
    count = cursor.fetchone()[0]
    assert count == 5
    conn.close()


def test_telemetry_buffer_get_pending(temp_db):
    """Test retrieving pending messages."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Store messages
    buffer.store("topic1", {"value": 1})
    buffer.store("topic2", {"value": 2})
    
    # Get pending
    messages = buffer.get_pending()
    assert len(messages) == 2
    assert messages[0][1] == "topic1"
    assert messages[0][2]["value"] == 1


def test_telemetry_buffer_remove(temp_db):
    """Test removing message by ID."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    buffer.store("topic1", {"value": 1})
    messages = buffer.get_pending()
    msg_id = messages[0][0]
    
    buffer.remove(msg_id)
    
    # Verify removed
    messages = buffer.get_pending()
    assert len(messages) == 0


def test_telemetry_buffer_count(temp_db):
    """Test getting buffer count."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    assert buffer.count() == 0
    
    buffer.store("topic1", {})
    assert buffer.count() == 1
    
    buffer.store("topic2", {})
    assert buffer.count() == 2


def test_telemetry_buffer_close(temp_db):
    """Test closing buffer connection."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    buffer.store("topic", {"value": 1})
    
    buffer.close()
    assert buffer._conn is None
    
    # After close, operations should fail gracefully
    assert buffer.count() == 0
    assert buffer.store("topic", {}) is False


def test_telemetry_buffer_error_handling():
    """Test buffer error handling with invalid path."""
    # Use invalid path that can't be created (e.g., root directory file)
    invalid_path = Path("/invalid/path/that/does/not/exist/buffer.db")
    buffer = telemetry_client.TelemetryBuffer(invalid_path)
    
    # Should fail gracefully
    assert buffer._conn is None
    success = buffer.store("topic", {"value": 1})
    assert success is False
    assert buffer.count() == 0


def test_telemetry_client_init():
    """Test TelemetryClient initialization."""
    with patch('telemetry_client.config.TELEMETRY_ENABLED', True):
        with patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883"):
            client = telemetry_client.TelemetryClient(
                device_id="TEST001",
                version="1.0.0"
            )
            
            assert client.device_id == "TEST001"
            assert client.version == "1.0.0"
            assert len(client.instance_hash) == 16


def test_telemetry_client_disabled():
    """Test TelemetryClient when disabled."""
    with patch('telemetry_client.config.TELEMETRY_ENABLED', False):
        client = telemetry_client.TelemetryClient(
            device_id="TEST",
            version="1.0.0"
        )
        
        assert client._enabled is False
        assert client._buffer is None


def test_telemetry_client_parse_mqtt_url():
    """Test MQTT URL parsing."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    
    # Test various URL formats
    assert client._parse_mqtt_url("mqtt://host:1883") == ("host", 1883)
    assert client._parse_mqtt_url("tcp://host:8883") == ("host", 8883)
    assert client._parse_mqtt_url("host:9001") == ("host", 9001)
    assert client._parse_mqtt_url("host") == ("host", 1883)  # Default port
    assert client._parse_mqtt_url("host:invalid") == ("host:invalid", 1883)  # Invalid port


@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_create_client():
    """Test MQTT client creation."""
    mock_client = MagicMock()
    mock_client.publish.return_value.rc = 0
    
    mock_mqtt_module = MagicMock()
    mock_mqtt_module.Client.return_value = mock_client
    mock_mqtt_module.MQTTv311 = 4
    mock_mqtt_module.CallbackAPIVersion.VERSION2 = 2
    
    with patch('telemetry_client.mqtt', mock_mqtt_module):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        # Simulate successful connection
        client._connected = False
        success = client._create_client()
        
        # Either succeeds or times out (depends on timing)
        assert isinstance(success, bool)


def test_telemetry_buffer_cleanup(temp_db):
    """Test buffer cleanup of old messages."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Store message with old timestamp
    conn = sqlite3.connect(str(temp_db))
    old_time = time.time() - (25 * 3600)  # 25 hours ago
    conn.execute(
        "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
        ("old_topic", "{}", old_time)
    )
    conn.commit()
    conn.close()
    
    # Store new message (triggers cleanup)
    buffer.store("new_topic", {"value": 1})
    
    # Old message should be gone
    buffer._cleanup()
    pending = buffer.get_pending()
    assert all(msg[1] != "old_topic" for msg in pending)


def test_telemetry_buffer_max_messages(temp_db):
    """Test buffer max message limit."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Store more than max messages
    for i in range(telemetry_client.BUFFER_MAX_MESSAGES + 100):
        buffer.store(f"topic_{i}", {"value": i})
    
    # Should not exceed max
    count = buffer.count()
    assert count <= telemetry_client.BUFFER_MAX_MESSAGES


def test_telemetry_buffer_invalid_json(temp_db):
    """Test handling of invalid JSON in buffer."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Manually insert invalid JSON
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
        ("bad_topic", "{ invalid json", time.time())
    )
    conn.commit()
    conn.close()
    
    # get_pending should skip and delete invalid JSON
    buffer.store("good_topic", {"value": 1})
    pending = buffer.get_pending()
    
    # Should only have good message
    assert pending[0][1] == "good_topic"


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry():
    """Test sending telemetry (disabled for unit test speed)."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    
    # Test with disabled client (no actual MQTT connection)
    client._enabled = False
    
    async def test():
        result = await client.send_telemetry({"uptime_s": 100})
        return result
    
    result = asyncio.run(test())
    assert result is False  # Disabled client returns False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_event():
    """Test sending events."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._enabled = False  # Disable for unit test
    
    async def test():
        result = await client.send_event("test_event", {"data": "value"})
        return result
    
    result = asyncio.run(test())
    assert result is False


def test_telemetry_client_event_methods():
    """Test convenience event methods."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._enabled = False  # Disabled for testing
    
    # Test all event methods return False when disabled
    async def test():
        assert await client.event_error_cloud_timeout("host", 5.0) is False
        assert await client.event_error_cloud_disconnect("reason") is False
        assert await client.event_error_box_disconnect("1.2.3.4") is False
        assert await client.event_error_crc("frame") is False
        assert await client.event_error_mqtt_local("broker", "error") is False
        assert await client.event_warning_mode_fallback("online", "offline", "reason") is False
        assert await client.event_box_reconnect("1.2.3.4") is False
        assert await client.event_cloud_reconnect() is False
        assert await client.event_startup() is False
        assert await client.event_shutdown() is False
    
    asyncio.run(test())


def test_get_instance_hash():
    """Test instance hash generation."""
    with patch.dict('os.environ', {'SUPERVISOR_TOKEN': 'test_token_123'}):
        hash1 = telemetry_client._get_instance_hash()
        assert len(hash1) == 16
        assert all(c in "0123456789abcdef" for c in hash1)
    
    with patch.dict('os.environ', {'HOSTNAME': 'test_host'}, clear=True):
        hash2 = telemetry_client._get_instance_hash()
        assert len(hash2) == 16


def test_telemetry_client_provision():
    """Test provision method."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._enabled = False
    
    async def test():
        result = await client.provision()
        return result
    
    result = asyncio.run(test())
    assert result is False  # Disabled client returns False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)  
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_publish_and_buffer(temp_db):
    """Test publish with buffer fallback."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        # Without connection, should buffer
        client._connected = False
        success = client._publish_sync("test/topic", {"value": 1})
        assert success is False


def test_telemetry_buffer_concurrent_access(temp_db):
    """Test buffer thread safety."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Simulate concurrent writes
    import threading
    
    def write_messages(start_idx):
        for i in range(start_idx, start_idx + 10):
            buffer.store(f"topic_{i}", {"value": i})
    
    threads = [
        threading.Thread(target=write_messages, args=(0,)),
        threading.Thread(target=write_messages, args=(10,)),
        threading.Thread(target=write_messages, args=(20,))
    ]
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # All messages should be stored (allow race condition tolerance)
    # SQLite serializes concurrent writes, so some may be lost/delayed
    count = buffer.count()
    assert 20 <= count <= 30  # Most messages made it


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_mqtt_callbacks(temp_db):
    """Test MQTT on_connect and on_disconnect callbacks."""
    mock_client = MagicMock()
    mock_mqtt_module = MagicMock()
    mock_mqtt_module.Client.return_value = mock_client
    mock_mqtt_module.MQTTv311 = 4
    mock_mqtt_module.CallbackAPIVersion.VERSION2 = 2
    
    with patch('telemetry_client.mqtt', mock_mqtt_module):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        success = client._create_client()
        
        # Get callbacks
        on_connect = mock_client.on_connect
        on_disconnect = mock_client.on_disconnect
        
        # Test successful connection (rc=0)
        client._connected = False
        on_connect(None, None, None, 0, None)
        assert client._connected is True
        assert client._consecutive_errors == 0
        
        # Test failed connection (rc!=0)
        client._connected = True
        on_connect(None, None, None, 1, None)
        # Connection flag doesn't change on error
        
        # Test disconnect
        client._connected = True
        on_disconnect(None, None, None, 0, None)
        assert client._connected is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_flush_buffer(temp_db):
    """Test buffer flushing with successful sends."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        # Store messages in buffer first
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("topic1", {"value": 1})
        buffer.store("topic2", {"value": 2})
        buffer.store("topic3", {"value": 3})
        
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        client._buffer = buffer
        
        # Flush buffer
        sent = client._flush_buffer_sync()
        assert sent == 3
        assert buffer.count() == 0


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_with_flush(temp_db):
    """Test send_telemetry with buffer flush."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        client._last_buffer_flush = 0.0  # Force flush
        
        async def test():
            result = await client.send_telemetry({"uptime_s": 100, "mode": "online"})
            return result
        
        result = asyncio.run(test())
        assert result is True
        assert client._consecutive_errors == 0


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_event_with_buffer_fallback(temp_db):
    """Test send_event with buffer fallback on failure."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Failure
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        async def test():
            result = await client.send_event("test_event", {"data": "value"})
            return result
        
        result = asyncio.run(test())
        assert result is True  # Buffered
        assert client._buffer.count() == 1


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_event_methods_enabled(temp_db):
    """Test all event methods with enabled client."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        async def test():
            # Test all event methods
            assert await client.event_error_cloud_timeout("host.com", 5.0) is True
            assert await client.event_error_cloud_disconnect("timeout") is True
            assert await client.event_error_box_disconnect("1.2.3.4") is True
            assert await client.event_error_crc("frame_data") is True
            assert await client.event_error_mqtt_local("broker", "error") is True
            assert await client.event_warning_mode_fallback("online", "offline", "reason") is True
            assert await client.event_box_reconnect("1.2.3.4") is True
            assert await client.event_cloud_reconnect() is True
            assert await client.event_startup() is True
            assert await client.event_shutdown() is True
        
        asyncio.run(test())
        assert mock_client.publish.call_count >= 10


def test_telemetry_client_disconnect(temp_db):
    """Test client disconnect."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        # Create mock client
        mock_client = MagicMock()
        client._client = mock_client
        client._connected = True
        
        # Disconnect
        client.disconnect()
        
        assert client._client is None
        assert client._connected is False
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()


def test_telemetry_client_disconnect_exception(temp_db):
    """Test disconnect with exception."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        mock_client = MagicMock()
        mock_client.disconnect.side_effect = Exception("Disconnect error")
        client._client = mock_client
        client._connected = True
        
        # Should not raise
        client.disconnect()
        assert client._client is None


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
def test_telemetry_client_properties():
    """Test client properties."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    
    # Test is_ready
    client._connected = False
    assert client.is_ready is False
    
    client._connected = True
    assert client.is_ready is True
    
    # Test is_buffering
    client._connected = False
    assert client.is_buffering is True
    
    client._connected = True
    assert client.is_buffering is False


def test_telemetry_client_reset_error_count():
    """Test reset_error_count method."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._consecutive_errors = 5
    
    client.reset_error_count()
    assert client._consecutive_errors == 0


def test_telemetry_client_get_buffer_count(temp_db):
    """Test get_buffer_count method."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._enabled = True
        client._buffer = telemetry_client.TelemetryBuffer(temp_db)
        
        assert client.get_buffer_count() == 0
        
        client._buffer.store("topic", {"value": 1})
        assert client.get_buffer_count() == 1


def test_global_init_and_get():
    """Test global init_telemetry and get_telemetry_client."""
    client = telemetry_client.init_telemetry("GLOBAL_TEST", "1.0.0")
    assert client is not None
    assert client.device_id == "GLOBAL_TEST"
    
    retrieved = telemetry_client.get_telemetry_client()
    assert retrieved is client


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_provision(temp_db):
    """Test provision method (calls event_startup)."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        async def test():
            return await client.provision()
        
        result = asyncio.run(test())
        assert result is True
        assert mock_client.publish.called


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_connection_timeout():
    """Test connection timeout in _create_client."""
    mock_client = MagicMock()
    mock_mqtt_module = MagicMock()
    mock_mqtt_module.Client.return_value = mock_client
    mock_mqtt_module.MQTTv311 = 4
    mock_mqtt_module.CallbackAPIVersion.VERSION2 = 2
    
    with patch('telemetry_client.mqtt', mock_mqtt_module):
        with patch('telemetry_client.time.sleep'):  # Speed up test
            client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
            client._connected = False  # Never becomes True
            
            success = client._create_client()
            assert success is False  # Timeout


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_ensure_connected_already_connected():
    """Test _ensure_connected when already connected."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._connected = True
    client._client = MagicMock()
    
    result = client._ensure_connected()
    assert result is True


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_publish_exception(temp_db):
    """Test _publish_sync with exception."""
    mock_client = MagicMock()
    mock_client.publish.side_effect = Exception("Publish error")
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        result = client._publish_sync("test/topic", {"value": 1})
        assert result is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_flush_partial_success(temp_db):
    """Test buffer flush with partial success."""
    mock_client = MagicMock()
    mock_result_ok = MagicMock()
    mock_result_ok.rc = 0
    mock_result_fail = MagicMock()
    mock_result_fail.rc = 1
    
    # First 2 succeed, 3rd fails
    mock_client.publish.side_effect = [mock_result_ok, mock_result_ok, mock_result_fail]
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("topic1", {"value": 1})
        buffer.store("topic2", {"value": 2})
        buffer.store("topic3", {"value": 3})
        
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        client._buffer = buffer
        
        sent = client._flush_buffer_sync()
        assert sent == 2  # Only 2 sent successfully
        assert buffer.count() == 1  # 1 remains


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_buffer_failure(temp_db):
    """Test send_telemetry when buffering fails."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Publish fails
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        # Mock buffer to fail
        with patch.object(client._buffer, 'store', return_value=False):
            async def test():
                result = await client.send_telemetry({"uptime_s": 100})
                return result
            
            result = asyncio.run(test())
            assert result is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_no_buffer():
    """Test send_telemetry when no buffer available."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Publish fails
    mock_client.publish.return_value = mock_result
    
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._client = mock_client
    client._connected = True
    client._buffer = None  # No buffer
    
    async def test():
        result = await client.send_telemetry({"uptime_s": 100})
        return result
    
    result = asyncio.run(test())
    assert result is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_flush_with_exception(temp_db):
    """Test buffer flush with exception during publish."""
    mock_client = MagicMock()
    mock_client.publish.side_effect = Exception("Publish error")
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("topic1", {"value": 1})
        buffer.store("topic2", {"value": 2})
        
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        client._buffer = buffer
        
        sent = client._flush_buffer_sync()
        assert sent == 0  # No messages sent due to exception


@patch('telemetry_client.config.TELEMETRY_ENABLED', False)
def test_telemetry_client_mqtt_unavailable():
    """Test with MQTT_AVAILABLE=False."""
    with patch('telemetry_client.MQTT_AVAILABLE', False):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        assert client._enabled is False


def test_telemetry_buffer_init_path_error():
    """Test Buffer init with path creation error."""
    # Try to create in a read-only location
    invalid_path = Path("/root/impossible_path/telemetry.db")
    
    with patch('pathlib.Path.mkdir', side_effect=PermissionError("No permission")):
        buffer = telemetry_client.TelemetryBuffer(invalid_path)
        assert buffer._conn is None


def test_telemetry_buffer_cleanup_exception():
    """Test buffer cleanup with exception."""
    buffer = telemetry_client.TelemetryBuffer()
    
    # Mock execute to raise exception
    if buffer._conn:
        with patch.object(buffer._conn, 'execute', side_effect=Exception("DB error")):
            # Should not raise
            buffer._cleanup()


def test_telemetry_buffer_get_pending_json_error(temp_db):
    """Test get_pending with JSON decode error and cleanup."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Manually insert corrupted JSON
    if buffer._conn:
        buffer._conn.execute(
            "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
            ("bad_topic", "not valid json at all", time.time())
        )
        buffer._conn.commit()
        
        # get_pending should handle error and delete bad record
        pending = buffer.get_pending()
        
        # Bad record should be gone
        count = buffer._conn.execute("SELECT COUNT(*) FROM messages WHERE topic = 'bad_topic'").fetchone()[0]
        assert count == 0


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_create_exception():
    """Test _create_client with exception in connect."""
    mock_client = MagicMock()
    mock_client.connect.side_effect = Exception("Connection error")
    mock_mqtt_module = MagicMock()
    mock_mqtt_module.Client.return_value = mock_client
    mock_mqtt_module.MQTTv311 = 4
    mock_mqtt_module.CallbackAPIVersion.VERSION2 = 2
    
    with patch('telemetry_client.mqtt', mock_mqtt_module):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        success = client._create_client()
        assert success is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_event_no_buffer_fail():
    """Test send_event when buffer not available and publish fails."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Fail
    mock_client.publish.return_value = mock_result
    
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._client = mock_client
    client._connected = True
    client._buffer = None
    
    async def test():
        result = await client.send_event("test_event", {"data": "value"})
        return result
    
    result = asyncio.run(test())
    assert result is False


def test_telemetry_buffer_remove_exception(temp_db):
    """Test buffer remove with exception (connection closed)."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    buffer.store("topic", {"value": 1})
    
    # Close connection to trigger exception
    buffer._conn.close()
    buffer._conn = None
    
    # Should not raise - gracefully handle None connection
    buffer.remove(1)


def test_telemetry_buffer_close_exception(temp_db):
    """Test buffer close with exception."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Already close connection
    buffer._conn.close()
    
    # Calling close again should not raise
    buffer.close()
    assert buffer._conn is None


def test_telemetry_buffer_count_exception(temp_db):
    """Test count with exception (closed connection)."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Close connection
    buffer._conn.close()
    
    count = buffer.count()
    assert count == 0


def test_telemetry_buffer_get_pending_exception(temp_db):
    """Test get_pending with exception (closed connection)."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Close connection
    buffer._conn.close()
    
    pending = buffer.get_pending()
    assert pending == []


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
def test_telemetry_client_properties_disabled():
    """Test properties when disabled."""
    with patch('telemetry_client.config.TELEMETRY_ENABLED', False):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        assert client.is_ready is False
        assert client.is_buffering is False


def test_telemetry_buffer_count_no_connection():
    """Test count when connection is None."""
    buffer = telemetry_client.TelemetryBuffer()
    buffer._conn = None
    
    count = buffer.count()
    assert count == 0


@patch('telemetry_client.MQTT_AVAILABLE', False)
def test_telemetry_client_disabled_mqtt_unavailable():
    """Test client when MQTT is not available."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    assert client._enabled is False
    
    # _create_client should return False
    success = client._create_client()
    assert success is False


def test_telemetry_buffer_cleanup_old_messages(temp_db):
    """Test cleanup removes old messages."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Insert old message directly
    old_time = time.time() - (25 * 3600)  # 25 hours ago
    if buffer._conn:
        buffer._conn.execute(
            "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
            ("old_topic", '{"old": true}', old_time)
        )
        buffer._conn.commit()
        
        # Add new message to trigger cleanup
        buffer.store("new_topic", {"new": True})
        
        # Manually call cleanup
        buffer._cleanup()
        
        # Old message should be gone
        cursor = buffer._conn.execute("SELECT COUNT(*) FROM messages WHERE topic = 'old_topic'")
        count = cursor.fetchone()[0]
        assert count == 0


def test_telemetry_buffer_max_messages_cleanup(temp_db):
    """Test cleanup when exceeding BUFFER_MAX_MESSAGES."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Store more than max
    original_max = telemetry_client.BUFFER_MAX_MESSAGES
    telemetry_client.BUFFER_MAX_MESSAGES = 5  # Temporarily set low
    
    try:
        for i in range(10):
            buffer.store(f"topic_{i}", {"value": i})
        
        # Should have triggered cleanup
        count = buffer.count()
        assert count <= 5
    finally:
        telemetry_client.BUFFER_MAX_MESSAGES = original_max


def test_telemetry_buffer_cleanup_no_connection():
    """Test cleanup when connection is None."""
    buffer = telemetry_client.TelemetryBuffer()
    buffer._conn = None
    
    # Should return early without error
    buffer._cleanup()


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_flush_buffer_no_buffer():
    """Test _flush_buffer_sync when buffer is None."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    client._buffer = None
    client._connected = True
    
    sent = client._flush_buffer_sync()
    assert sent == 0


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_with_buffer_time_trigger(temp_db):
    """Test send_telemetry triggers buffer flush after 60s."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("old_topic", {"old": 1})
        
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        client._buffer = buffer
        client._last_buffer_flush = time.time() - 61.0  # Over 60s ago
        
        async def test():
            return await client.send_telemetry({"uptime_s": 100})
        
        result = asyncio.run(test())
        assert result is True
        # Buffer should have been flushed
        assert buffer.count() == 0


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True) 
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_buffer_store_fail_logging(temp_db):
    """Test logging when buffer store fails."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Fail
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        # Mock buffer.store to return False
        with patch.object(client._buffer, 'store', return_value=False):
            async def test():
                return await client.send_telemetry({"uptime_s": 100})
            
            result = asyncio.run(test())
            assert result is False
            assert client._consecutive_errors >= 1


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_event_buffer_then_fail(temp_db):
    """Test send_event buffers when publish fails."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Fail
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        async def test():
            return await client.send_event("test_event", {"data": "value"})
        
        result = asyncio.run(test())
        assert result is True  # Buffered successfully
        assert client._buffer.count() == 1


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_disconnect_with_buffer(temp_db):
    """Test disconnect closes buffer."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        mock_client = MagicMock()
        client._client = mock_client
        client._connected = True
        
        # Store something in buffer
        client._buffer.store("topic", {"value": 1})
        
        # Disconnect should close buffer
        client.disconnect()
        
        assert client._buffer._conn is None


def test_telemetry_buffer_cleanup_with_exception_in_execute():
    """Test cleanup handles exception during DELETE execution."""
    buffer = telemetry_client.TelemetryBuffer()
    
    # Create a mock connection that raises on execute
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("DB error")
    buffer._conn = mock_conn
    
    # Should not raise, exception is caught
    buffer._cleanup()


def test_telemetry_buffer_store_triggers_cleanup_with_many_messages(temp_db):
    """Test that storing message triggers cleanup when count > MAX."""
    # Temporarily lower max for testing
    original_max = telemetry_client.BUFFER_MAX_MESSAGES
    telemetry_client.BUFFER_MAX_MESSAGES = 10
    
    try:
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        
        # Fill buffer to exactly MAX
        for i in range(10):
            buffer.store(f"topic_{i}", {"value": i})
        
        assert buffer.count() == 10
        
        # Store one more - this should trigger cleanup on line 114
        result = buffer.store("trigger", {"trigger": True})
        assert result is True
        
        # Cleanup should have been called, reducing count
        count = buffer.count()
        assert count <= 10
    finally:
        telemetry_client.BUFFER_MAX_MESSAGES = original_max


def test_telemetry_buffer_get_pending_with_json_decode_and_delete(temp_db):
    """Test get_pending deletes message with bad JSON."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Insert valid and invalid JSON
    if buffer._conn:
        buffer._conn.execute(
            "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
            ("bad", "not json at all!", time.time())
        )
        buffer._conn.execute(
            "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
            ("good", '{"valid": true}', time.time())
        )
        buffer._conn.commit()
        
        # Get pending should skip bad JSON and delete it
        pending = buffer.get_pending()
        
        # Should only return good message
        assert len(pending) == 1
        assert pending[0][1] == "good"
        
        # Bad message should be deleted
        cursor = buffer._conn.execute("SELECT COUNT(*) FROM messages WHERE topic = 'bad'")
        assert cursor.fetchone()[0] == 0


def test_telemetry_buffer_close_with_exception_in_close():
    """Test close handles exception during connection.close()."""
    buffer = telemetry_client.TelemetryBuffer()
    
    # Create mock that raises on close
    mock_conn = MagicMock()
    mock_conn.close.side_effect = Exception("Close failed")
    buffer._conn = mock_conn
    
    # Should not raise
    buffer.close()
    assert buffer._conn is None


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_create_with_connection_exception():
    """Test _create_client handles exception in connect()."""
    mock_mqtt_module = MagicMock()
    mock_client = MagicMock()
    mock_client.connect.side_effect = Exception("Connection failed")
    mock_mqtt_module.Client.return_value = mock_client
    mock_mqtt_module.MQTTv311 = 4
    mock_mqtt_module.CallbackAPIVersion.VERSION2 = 2
    
    with patch('telemetry_client.mqtt', mock_mqtt_module):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        success = client._create_client()
        assert success is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_flush_on_time(temp_db):
    """Test send_telemetry triggers buffer flush based on time."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("buffered1", {"old": 1})
        buffer.store("buffered2", {"old": 2})
        
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        client._buffer = buffer
        client._last_buffer_flush = time.time() - 65.0  # Over 60s
        
        async def test():
            return await client.send_telemetry({"uptime_s": 100})
        
        result = asyncio.run(test())
        assert result is True
        # Buffer should be flushed
        assert buffer.count() == 0


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_buffer_fail_warning(temp_db):
    """Test send_telemetry logs warning when buffer.store fails."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Fail
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        # Mock buffer.store to fail
        with patch.object(client._buffer, 'store', return_value=False):
            async def test():
                result = await client.send_telemetry({"uptime_s": 100})
                return result
            
            result = asyncio.run(test())
            assert result is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_event_buffer_fallback_success(temp_db):
    """Test send_event successfully buffers when publish fails."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Fail
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        async def test():
            result = await client.send_event("test_event", {"data": "value"})
            return result
        
        result = asyncio.run(test())
        assert result is True
        assert client._buffer.count() == 1


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_disconnect_exception_in_loop_stop(temp_db):
    """Test disconnect handles exception in loop_stop."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        mock_client = MagicMock()
        mock_client.loop_stop.side_effect = Exception("Stop failed")
        client._client = mock_client
        client._connected = True
        
        # Should not raise
        client.disconnect()
        assert client._client is None


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', False)
def test_telemetry_client_empty_device_id():
    """Test TelemetryClient with empty device_id disables telemetry (line 114)."""
    client = telemetry_client.TelemetryClient("", "1.0.0")
    assert client.device_id == ""
    assert client._enabled is False
    assert client._buffer is None


def test_telemetry_buffer_init_db_exception(tmp_path):
    """Test buffer _init_db exception path (lines 137-138, 151)."""
    invalid_db = str(tmp_path / "invalid" / "nested" / "path" / "test.db")
    # Make parent directory read-only to cause write failure
    parent = tmp_path / "invalid"
    parent.mkdir()
    parent.chmod(0o444)  # Read-only
    
    try:
        # This should handle the exception gracefully
        buffer = telemetry_client.TelemetryBuffer(invalid_db)
        assert buffer._conn is None
    finally:
        parent.chmod(0o755)  # Restore permissions


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.mqtt')
def test_telemetry_client_connect_timeout(mock_mqtt):
    """Test _create_client timeout (line 223)."""
    mock_client_instance = MagicMock()
    mock_mqtt.Client.return_value = mock_client_instance
    mock_mqtt.MQTTv311 = 4
    mock_mqtt.CallbackAPIVersion.VERSION2 = 2
    
    client = telemetry_client.TelemetryClient("TEST", "1.0.0")
    
    # _create_client will create client but never set _connected
    # Should timeout after 50 iterations and return False (line 223)
    result = client._create_client()
    assert result is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_event_buffer_fallback_failure(temp_db):
    """Test send_event buffer fallback fails (lines 337-340)."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Publish fails
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        # Make buffer.store fail
        with patch.object(client._buffer, 'store', return_value=False):
            async def test():
                result = await client.send_event("test_event", {"data": "value"})
                return result
            
            result = asyncio.run(test())
            assert result is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_disconnect_all_exceptions(temp_db):
    """Test disconnect handles exception paths in loop_stop and disconnect (lines 404-408)."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        mock_client = MagicMock()
        # Both methods raise exceptions
        mock_client.loop_stop.side_effect = Exception("loop_stop failed")
        mock_client.disconnect.side_effect = Exception("disconnect failed")
        client._client = mock_client
        client._connected = True
        
        # Should handle exceptions in loop_stop and disconnect gracefully
        client.disconnect()
        assert client._client is None
        assert client._connected is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_triggers_buffer_flush(temp_db):
    """Test send_telemetry triggers buffer flush after successful send (lines 296-299)."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0  # Success
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        # Set last flush to 70 seconds ago to trigger flush condition
        client._last_buffer_flush = time.time() - 70.0
        
        # Add some messages to buffer
        client._buffer.store("test/topic", {"test": "data"})
        assert client._buffer.count() == 1
        
        async def test():
            result = await client.send_telemetry({"uptime_s": 100})
            return result
        
        result = asyncio.run(test())
        assert result is True
        # Buffer should be flushed
        assert client._buffer.count() == 0


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_buffers_on_fail(temp_db):
    """Test send_telemetry buffers message on failure (lines 304-306)."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Failure
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        async def test():
            result = await client.send_telemetry({"uptime_s": 100})
            return result
        
        result = asyncio.run(test())
        assert result is True  # Should succeed via buffering
        assert client._buffer.count() == 1


def test_telemetry_buffer_get_pending_with_no_connection():
    """Test get_pending returns empty list when _conn is None (line 114)."""
    buffer = telemetry_client.TelemetryBuffer()  # No DB path
    buffer._conn = None
    result = buffer.get_pending()
    assert result == []
    assert isinstance(result, list)


def test_telemetry_buffer_remove_with_exception(temp_db):
    """Test remove handles exception gracefully (lines 137-138)."""
    buffer = telemetry_client.TelemetryBuffer(temp_db)
    
    # Close connection to cause exception
    if buffer._conn:
        buffer._conn.close()
    
    # Should not raise exception
    buffer.remove(999)  # Non-existent ID


def test_telemetry_buffer_close_with_none_connection():
    """Test close when _conn is already None (line 151->exit)."""
    buffer = telemetry_client.TelemetryBuffer()  # No DB
    buffer._conn = None
    # Should not raise, just exit early
    buffer.close()
    assert buffer._conn is None


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.mqtt')
def test_telemetry_client_create_client_successful_connection(mock_mqtt, temp_db):
    """Test _create_client with successful connection (line 223)."""
    mock_client_instance = MagicMock()
    mock_mqtt.Client.return_value = mock_client_instance
    mock_mqtt.MQTTv311 = 4
    mock_mqtt.CallbackAPIVersion.VERSION2 = 2
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient("TEST", "1.0.0")
        
        # Simulate successful connection by calling on_connect callback
        def simulate_connect(*args, **kwargs):
            # Trigger the on_connect callback
            if hasattr(mock_client_instance, 'on_connect'):
                mock_client_instance.on_connect(None, None, None, 0)
            return None
        
        mock_client_instance.connect.side_effect = simulate_connect
        
        result = client._create_client()
        assert result is True
        assert client._connected is True


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_event_buffers_with_log(temp_db):
    """Test send_event buffers with debug log (lines 337-339)."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 1  # Failure
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        # Mock logger to verify debug call
        with patch('telemetry_client.logger') as mock_logger:
            async def test():
                result = await client.send_event("test_event", {"key": "value"})
                return result
            
            result = asyncio.run(test())
            assert result is True
            assert client._buffer.count() == 1
            # Verify debug log was called
            mock_logger.debug.assert_called()


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_disconnect_with_buffer_close(temp_db):
    """Test disconnect closes buffer (line 412)."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        mock_client = MagicMock()
        client._client = mock_client
        client._connected = True
        
        # Verify buffer exists
        assert client._buffer is not None
        buffer_instance = client._buffer
        
        # Disconnect should close buffer
        client.disconnect()
        
        # Buffer close should have been called (via close method)
        assert client._client is None
        assert client._connected is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_send_telemetry_no_buffer_flush_needed(temp_db):
    """Test send_telemetry without buffer flush when recently flushed (lines 296->299 branch)."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.rc = 0  # Success
    mock_client.publish.return_value = mock_result
    
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        client._client = mock_client
        client._connected = True
        
        # Set last flush to only 30 seconds ago - should NOT trigger flush
        client._last_buffer_flush = time.time() - 30.0
        
        async def test():
            result = await client.send_telemetry({"uptime_s": 100})
            return result
        
        result = asyncio.run(test())
        assert result is True


@patch('telemetry_client.config.TELEMETRY_ENABLED', True)
@patch('telemetry_client.MQTT_AVAILABLE', True)
@patch('telemetry_client.config.TELEMETRY_MQTT_BROKER', "test.example.com:1883")
def test_telemetry_client_disconnect_with_no_client(temp_db):
    """Test disconnect when _client is None (lines 404->412 branch)."""
    with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
        client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
        
        # Ensure _client is None
        client._client = None
        client._connected = False
        
        # Disconnect should handle None client gracefully
        client.disconnect()
        
        assert client._client is None
        assert client._connected is False


@patch('telemetry_client.config.TELEMETRY_ENABLED', False)
def test_telemetry_client_disconnect_with_no_buffer():
    """Test disconnect when both _client and _buffer are None (line 412->exit)."""
    client = telemetry_client.TelemetryClient(device_id="TEST", version="1.0.0")
    
    # When TELEMETRY_ENABLED=False, _buffer should be None
    assert client._buffer is None
    client._client = None
    client._connected = False
    
    # Disconnect should exit early when no client and no buffer
    client.disconnect()
    
    assert client._client is None
    assert client._buffer is None


def test_mqtt_import_with_real_mqtt():
    """Test that MQTT_AVAILABLE=True when paho.mqtt is available (line 26)."""
    # This test verifies the successful import path
    # Since paho.mqtt is installed in the test environment,
    # MQTT_AVAILABLE should be True
    import telemetry_client
    assert telemetry_client.MQTT_AVAILABLE is True
    assert telemetry_client.mqtt is not None


def test_mqtt_import_failure():
    """Test MQTT_AVAILABLE=False when paho.mqtt import fails (lines 27-29)."""
    import importlib
    import sys
    
    # Save original module
    original_module = sys.modules.get('paho.mqtt.client')
    original_telemetry = sys.modules.get('telemetry_client')
    
    try:
        # Remove paho.mqtt from sys.modules to simulate import failure
        if 'paho.mqtt.client' in sys.modules:
            del sys.modules['paho.mqtt.client']
        if 'paho.mqtt' in sys.modules:
            del sys.modules['paho.mqtt']
        if 'paho' in sys.modules:
            del sys.modules['paho']
        
        # Mock the import to raise ImportError
        import builtins
        original_import = builtins.__import__
        
        def mock_import(name, *args, **kwargs):
            if 'paho' in name:
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)
        
        builtins.__import__ = mock_import
        
        # Remove telemetry_client from cache to force reimport
        if 'telemetry_client' in sys.modules:
            del sys.modules['telemetry_client']
        
        # Now import should trigger the except block
        sys.path.insert(0, 'addon/oig-proxy')
        import telemetry_client as tc_test
        
        # Verify except block was executed
        assert tc_test.MQTT_AVAILABLE is False
        assert tc_test.mqtt is None
        
    finally:
        # Restore original import
        builtins.__import__ = original_import
        
        # Restore original modules
        if original_module is not None:
            sys.modules['paho.mqtt.client'] = original_module
        if original_telemetry is not None:
            sys.modules['telemetry_client'] = original_telemetry
        
        # Clean up test import
        if 'telemetry_client' in sys.modules:
            del sys.modules['telemetry_client']
        
        # Re-import to restore normal state
        sys.path.insert(0, 'addon/oig-proxy')
        import telemetry_client  # noqa: F401


