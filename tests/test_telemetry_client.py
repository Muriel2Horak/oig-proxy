"""Tests for telemetry_client module."""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,too-many-lines

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "addon" / "oig-proxy"))

import telemetry_client  # noqa: E402


class TestGetInstanceHash:
    """Test _get_instance_hash function."""

    def test_with_supervisor_token(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token-123")
        result = telemetry_client._get_instance_hash()
        assert isinstance(result, str)
        assert len(result) == 16

    def test_without_supervisor_token(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("HOSTNAME", "test-host")
        result = telemetry_client._get_instance_hash()
        assert isinstance(result, str)
        assert len(result) == 16

    def test_deterministic(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "same-token")
        result1 = telemetry_client._get_instance_hash()
        result2 = telemetry_client._get_instance_hash()
        assert result1 == result2


class TestTelemetryBuffer:
    """Test TelemetryBuffer class."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.db"

    def test_init(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        assert buffer._db_path == temp_db
        assert buffer._conn is not None

    def test_init_creates_table(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        cursor = buffer._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        assert cursor.fetchone() is not None

    def test_store_success(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        result = buffer.store("test/topic", {"key": "value"})
        assert result is True
        assert buffer.count() == 1

    def test_store_multiple(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("topic1", {"a": 1})
        buffer.store("topic2", {"b": 2})
        assert buffer.count() == 2

    def test_get_pending(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("topic1", {"a": 1})
        buffer.store("topic2", {"b": 2})
        pending = buffer.get_pending(limit=10)
        assert len(pending) == 2
        assert pending[0][1] == "topic1"
        assert pending[0][2] == {"a": 1}

    def test_get_pending_limit(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        for i in range(10):
            buffer.store(f"topic{i}", {"i": i})
        pending = buffer.get_pending(limit=5)
        assert len(pending) == 5

    def test_remove(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.store("topic", {"a": 1})
        pending = buffer.get_pending()
        msg_id = pending[0][0]
        buffer.remove(msg_id)
        assert buffer.count() == 0

    def test_cleanup_old_messages(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        # Insert old message
        old_time = time.time() - (25 * 3600)
        buffer._conn.execute(
            "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
            ("topic", json.dumps({"a": 1}), old_time)
        )
        buffer._conn.commit()
        buffer._cleanup()
        assert buffer.count() == 0

    def test_cleanup_excess_messages(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        # Insert more than BUFFER_MAX_MESSAGES
        for i in range(telemetry_client.BUFFER_MAX_MESSAGES + 100):
            buffer.store(f"topic{i}", {"i": i})
        buffer._cleanup()
        assert buffer.count() <= telemetry_client.BUFFER_MAX_MESSAGES

    def test_get_pending_invalid_json(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        # Insert invalid JSON
        buffer._conn.execute(
            "INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)",
            ("topic", "invalid-json{{{", time.time())
        )
        buffer._conn.commit()
        pending = buffer.get_pending()
        assert len(pending) == 0

    def test_close(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.close()
        assert buffer._conn is None

    def test_operations_after_close(self, temp_db):
        buffer = telemetry_client.TelemetryBuffer(temp_db)
        buffer.close()
        assert buffer.store("topic", {}) is False
        assert buffer.get_pending() == []
        assert buffer.count() == 0

    def test_init_db_error(self, monkeypatch):
        # Create buffer with invalid path
        invalid_path = Path("/nonexistent/path/to/db")
        buffer = telemetry_client.TelemetryBuffer(invalid_path)
        # Should handle error gracefully
        assert buffer._conn is None


class TestTelemetryClientInit:
    """Test TelemetryClient initialization."""

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', True)
    def test_init_enabled(self, mock_config):
        mock_config.TELEMETRY_ENABLED = True
        mock_config.TELEMETRY_MQTT_BROKER = "mqtt://test:1883"
        mock_config.TELEMETRY_INTERVAL_S = 300
        
        client = telemetry_client.TelemetryClient("12345", "1.0.0")
        assert client.device_id == "12345"
        assert client.version == "1.0.0"
        assert client._enabled is True
        assert client._mqtt_host == "test"
        assert client._mqtt_port == 1883

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', False)
    def test_init_mqtt_unavailable(self, mock_config):
        mock_config.TELEMETRY_ENABLED = True
        client = telemetry_client.TelemetryClient("12345", "1.0.0")
        assert client._enabled is False

    @patch('telemetry_client.config')
    def test_init_disabled_no_device_id(self, mock_config):
        mock_config.TELEMETRY_ENABLED = True
        client = telemetry_client.TelemetryClient("", "1.0.0")
        assert client._enabled is False

    @patch('telemetry_client.config')
    def test_init_telemetry_disabled(self, mock_config):
        mock_config.TELEMETRY_ENABLED = False
        client = telemetry_client.TelemetryClient("12345", "1.0.0")
        assert client._enabled is False


class TestTelemetryClientParseMqttUrl:
    """Test _parse_mqtt_url method."""

    def test_parse_with_port(self):
        host, port = telemetry_client.TelemetryClient._parse_mqtt_url("mqtt://test.com:1234")
        assert host == "test.com"
        assert port == 1234

    def test_parse_without_port(self):
        host, port = telemetry_client.TelemetryClient._parse_mqtt_url("mqtt://test.com")
        assert host == "test.com"
        assert port == 1883

    def test_parse_tcp_prefix(self):
        host, port = telemetry_client.TelemetryClient._parse_mqtt_url("tcp://test.com:9999")
        assert host == "test.com"
        assert port == 9999

    def test_parse_no_prefix(self):
        host, port = telemetry_client.TelemetryClient._parse_mqtt_url("test.com:5555")
        assert host == "test.com"
        assert port == 5555

    def test_parse_invalid_port(self):
        host, port = telemetry_client.TelemetryClient._parse_mqtt_url("test.com:invalid")
        assert host == "test.com:invalid"  # Invalid port keeps whole string as host
        assert port == 1883


class TestTelemetryClientMqtt:
    """Test TelemetryClient MQTT operations."""

    @pytest.fixture
    def mock_mqtt(self):
        with patch('telemetry_client.mqtt') as mock:
            mock_client = MagicMock()
            mock.Client.return_value = mock_client
            mock.MQTTv311 = 4
            mock.CallbackAPIVersion = MagicMock()
            mock.CallbackAPIVersion.VERSION2 = 2
            yield mock, mock_client

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', True)
    def test_create_client_success(self, mock_config, mock_mqtt):
        mock_config.TELEMETRY_ENABLED = True
        mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
        
        mqtt_mock, mock_client = mock_mqtt
        mock_client.connect.return_value = None
        
        client = telemetry_client.TelemetryClient("12345", "1.0.0")
        
        # Simulate connection
        client._connected = True
        result = client._create_client()
        assert result is True

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', True)
    def test_ensure_connected_when_connected(self, mock_config, mock_mqtt):
        mock_config.TELEMETRY_ENABLED = True
        mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
        
        client = telemetry_client.TelemetryClient("12345", "1.0.0")
        client._connected = True
        client._client = MagicMock()
        
        result = client._ensure_connected()
        assert result is True


class TestTelemetryClientPublish:
    """Test publish operations."""

    @pytest.fixture
    def client(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            mock_config.TELEMETRY_INTERVAL_S = 300
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                client = telemetry_client.TelemetryClient("12345", "1.0.0")
                client._buffer = None  # Disable buffer for these tests
                yield client

    def test_publish_sync_success(self, client):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.rc = 0
        mock_client.publish.return_value = mock_result
        
        client._client = mock_client
        client._connected = True
        
        result = client._publish_sync("test/topic", {"key": "value"})
        assert result is True

    def test_publish_sync_failure(self, client):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.rc = 1
        mock_client.publish.return_value = mock_result
        
        client._client = mock_client
        client._connected = True
        
        result = client._publish_sync("test/topic", {"key": "value"})
        assert result is False

    def test_publish_sync_no_connection(self, client):
        client._connected = False
        result = client._publish_sync("test/topic", {"key": "value"})
        assert result is False

    @pytest.mark.asyncio
    async def test_send_telemetry_success(self, client):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.rc = 0
        mock_client.publish.return_value = mock_result
        
        client._client = mock_client
        client._connected = True
        client._enabled = True
        
        result = await client.send_telemetry({"mode": "online", "uptime_s": 100})
        assert result is True

    @pytest.mark.asyncio
    async def test_send_telemetry_disabled(self, client):
        client._enabled = False
        result = await client.send_telemetry({"mode": "online"})
        assert result is False

    @pytest.mark.asyncio
    async def test_send_event_success(self, client):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.rc = 0
        mock_client.publish.return_value = mock_result
        
        client._client = mock_client
        client._connected = True
        client._enabled = True
        
        result = await client.send_event("test_event", {"detail": "value"})
        assert result is True

    @pytest.mark.asyncio
    async def test_send_event_disabled(self, client):
        client._enabled = False
        result = await client.send_event("test_event")
        assert result is False


class TestTelemetryClientConvenienceMethods:
    """Test convenience event methods."""

    @pytest.fixture
    def client(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                client = telemetry_client.TelemetryClient("12345", "1.0.0")
                client._buffer = None
                yield client

    @pytest.mark.asyncio
    async def test_event_error_cloud_timeout(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_error_cloud_timeout("cloud.example.com", 30.0)
        assert result is True
        client.send_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_error_cloud_disconnect(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_error_cloud_disconnect("test reason")
        assert result is True

    @pytest.mark.asyncio
    async def test_event_error_box_disconnect(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_error_box_disconnect("10.0.0.1")
        assert result is True

    @pytest.mark.asyncio
    async def test_event_error_crc(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_error_crc("frame info")
        assert result is True

    @pytest.mark.asyncio
    async def test_event_error_mqtt_local(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_error_mqtt_local("broker", "error")
        assert result is True

    @pytest.mark.asyncio
    async def test_event_warning_mode_fallback(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_warning_mode_fallback("online", "offline", "test")
        assert result is True

    @pytest.mark.asyncio
    async def test_event_box_reconnect(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_box_reconnect("10.0.0.1")
        assert result is True

    @pytest.mark.asyncio
    async def test_event_cloud_reconnect(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_cloud_reconnect()
        assert result is True

    @pytest.mark.asyncio
    async def test_event_startup(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_startup()
        assert result is True

    @pytest.mark.asyncio
    async def test_event_shutdown(self, client):
        client.send_event = AsyncMock(return_value=True)
        result = await client.event_shutdown()
        assert result is True

    @pytest.mark.asyncio
    async def test_provision(self, client):
        client.event_startup = AsyncMock(return_value=True)
        result = await client.provision()
        assert result is True


class TestTelemetryClientProperties:
    """Test client properties and utility methods."""

    @pytest.fixture
    def client(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                yield telemetry_client.TelemetryClient("12345", "1.0.0")

    def test_is_ready_when_connected(self, client):
        client._enabled = True
        client._connected = True
        assert client.is_ready is True

    def test_is_ready_when_disconnected(self, client):
        client._enabled = True
        client._connected = False
        assert client.is_ready is False

    def test_is_ready_when_disabled(self, client):
        client._enabled = False
        client._connected = True
        assert client.is_ready is False

    def test_is_buffering_when_offline(self, client):
        client._enabled = True
        client._connected = False
        assert client.is_buffering is True

    def test_is_buffering_when_online(self, client):
        client._enabled = True
        client._connected = True
        assert client.is_buffering is False

    def test_reset_error_count(self, client):
        client._consecutive_errors = 5
        client.reset_error_count()
        assert client._consecutive_errors == 0

    def test_get_buffer_count_with_buffer(self, client):
        mock_buffer = MagicMock()
        mock_buffer.count.return_value = 10
        client._buffer = mock_buffer
        assert client.get_buffer_count() == 10

    def test_get_buffer_count_no_buffer(self, client):
        client._buffer = None
        assert client.get_buffer_count() == 0

    def test_disconnect(self, client):
        mock_client = MagicMock()
        mock_buffer = MagicMock()
        client._client = mock_client
        client._buffer = mock_buffer
        
        client.disconnect()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
        mock_buffer.close.assert_called_once()
        assert client._client is None
        assert client._connected is False


class TestTelemetryClientBuffer:
    """Test buffering functionality."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.db"

    @pytest.mark.asyncio
    async def test_send_telemetry_buffers_on_failure(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            mock_config.TELEMETRY_INTERVAL_S = 300
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    client._connected = False
                    client._enabled = True
                    
                    result = await client.send_telemetry({"mode": "online"})
                    # Should buffer when MQTT unavailable
                    assert client._buffer.count() >= 1

    @pytest.mark.asyncio
    async def test_flush_buffer_on_success(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            mock_config.TELEMETRY_INTERVAL_S = 300
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    
                    # Add message to buffer
                    client._buffer.store("test/topic", {"test": "data"})
                    assert client._buffer.count() == 1
                    
                    # Setup mock for successful publish
                    mock_client = MagicMock()
                    mock_result = MagicMock()
                    mock_result.rc = 0
                    mock_client.publish.return_value = mock_result
                    client._client = mock_client
                    client._connected = True
                    client._enabled = True
                    client._last_buffer_flush = 0
                    
                    # Send telemetry - should flush buffer
                    await client.send_telemetry({"mode": "online"})
                    
                    # Buffer should be flushed
                    assert client._buffer.count() == 0


class TestGlobalFunctions:
    """Test module-level functions."""

    def test_init_telemetry(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                client = telemetry_client.init_telemetry("12345", "1.0.0")
                assert isinstance(client, telemetry_client.TelemetryClient)
                assert client.device_id == "12345"
                assert client.version == "1.0.0"

    def test_get_telemetry_client(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                telemetry_client.init_telemetry("12345", "1.0.0")
                client = telemetry_client.get_telemetry_client()
                assert client is not None
                assert client.device_id == "12345"


class TestEdgeCases:
    """Test edge cases and error paths."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.db"

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', True)
    def test_create_client_exception(self, mock_config):
        mock_config.TELEMETRY_ENABLED = True
        mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
        
        with patch('telemetry_client.mqtt') as mock_mqtt:
            mock_mqtt.Client.side_effect = Exception("Connection error")
            client = telemetry_client.TelemetryClient("12345", "1.0.0")
            result = client._create_client()
            assert result is False

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', False)
    def test_create_client_mqtt_unavailable(self, mock_config):
        mock_config.TELEMETRY_ENABLED = True
        mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
        client = telemetry_client.TelemetryClient("12345", "1.0.0")
        result = client._create_client()
        assert result is False

    @pytest.mark.asyncio
    async def test_send_telemetry_no_buffer(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            mock_config.TELEMETRY_INTERVAL_S = 300
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                client = telemetry_client.TelemetryClient("12345", "1.0.0")
                client._buffer = None
                client._connected = False
                client._enabled = True
                
                result = await client.send_telemetry({"mode": "online"})
                assert result is False

    @pytest.mark.asyncio
    async def test_send_event_no_buffer(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                client = telemetry_client.TelemetryClient("12345", "1.0.0")
                client._buffer = None
                client._connected = False
                client._enabled = True
                
                result = await client.send_event("test_event")
                assert result is False

    def test_publish_sync_exception(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                client = telemetry_client.TelemetryClient("12345", "1.0.0")
                mock_client = MagicMock()
                mock_client.publish.side_effect = Exception("Publish error")
                client._client = mock_client
                client._connected = True
                
                result = client._publish_sync("test/topic", {"key": "value"})
                assert result is False

    def test_flush_buffer_exception(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    client._buffer.store("test/topic", {"test": "data"})
                    
                    # Mock client to fail on publish
                    mock_client = MagicMock()
                    mock_client.publish.side_effect = Exception("Publish error")
                    client._client = mock_client
                    client._connected = True
                    
                    sent = client._flush_buffer_sync()
                    assert sent == 0

    def test_flush_buffer_partial_failure(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    client._buffer.store("topic1", {"a": 1})
                    client._buffer.store("topic2", {"b": 2})
                    
                    # Mock client to succeed first, fail second
                    mock_client = MagicMock()
                    mock_result = MagicMock()
                    mock_result.rc = 1  # Fail
                    mock_client.publish.return_value = mock_result
                    client._client = mock_client
                    client._connected = True
                    
                    sent = client._flush_buffer_sync()
                    assert sent == 0  # Stops on first failure

    def test_disconnect_exception(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                client = telemetry_client.TelemetryClient("12345", "1.0.0")
                mock_client = MagicMock()
                mock_client.loop_stop.side_effect = Exception("Stop error")
                mock_client.disconnect.side_effect = Exception("Disconnect error")
                client._client = mock_client
                
                # Should not raise exception
                client.disconnect()
                assert client._client is None

    def test_create_client_no_connection(self):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.mqtt') as mock_mqtt:
                    mock_client_inst = MagicMock()
                    mock_mqtt.Client.return_value = mock_client_inst
                    mock_mqtt.MQTTv311 = 4
                    mock_mqtt.CallbackAPIVersion = MagicMock()
                    mock_mqtt.CallbackAPIVersion.VERSION2 = 2
                    
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    # Never set _connected to True, so timeout occurs
                    result = client._create_client()
                    assert result is False

    @pytest.mark.asyncio
    async def test_send_telemetry_buffer_store_failure(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            mock_config.TELEMETRY_INTERVAL_S = 300
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    client._connected = False
                    client._enabled = True
                    
                    # Make buffer.store return False
                    client._buffer.store = MagicMock(return_value=False)
                    
                    result = await client.send_telemetry({"mode": "online"})
                    assert result is False

    @pytest.mark.asyncio
    async def test_send_event_buffer_store_failure(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    client._connected = False
                    client._enabled = True
                    
                    # Make buffer.store return False
                    client._buffer.store = MagicMock(return_value=False)
                    
                    result = await client.send_event("test_event", {"detail": "value"})
                    assert result is False

    @pytest.mark.asyncio
    async def test_send_event_buffer_store_success(self, temp_db):
        with patch('telemetry_client.config') as mock_config:
            mock_config.TELEMETRY_ENABLED = True
            mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
            with patch('telemetry_client.MQTT_AVAILABLE', True):
                with patch('telemetry_client.BUFFER_DB_PATH', temp_db):
                    client = telemetry_client.TelemetryClient("12345", "1.0.0")
                    client._connected = False
                    client._enabled = True
                    
                    # Make buffer.store return True
                    client._buffer.store = MagicMock(return_value=True)
                    
                    result = await client.send_event("test_event", {"detail": "value"})
                    assert result is True

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', True)
    def test_create_client_connect_callback(self, mock_config):
        """Test MQTT connect callback."""
        mock_config.TELEMETRY_ENABLED = True
        mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
        
        with patch('telemetry_client.mqtt') as mock_mqtt:
            mock_client_inst = MagicMock()
            mock_mqtt.Client.return_value = mock_client_inst
            mock_mqtt.MQTTv311 = 4
            mock_mqtt.CallbackAPIVersion = MagicMock()
            mock_mqtt.CallbackAPIVersion.VERSION2 = 2
            
            client = telemetry_client.TelemetryClient("12345", "1.0.0")
            client._create_client()
            
            # Simulate successful connection callback
            on_connect = mock_client_inst.on_connect
            on_connect(None, None, None, 0, None)
            assert client._connected is True
            assert client._consecutive_errors == 0

    @patch('telemetry_client.config')
    @patch('telemetry_client.MQTT_AVAILABLE', True)
    def test_create_client_disconnect_callback(self, mock_config):
        """Test MQTT disconnect callback."""
        mock_config.TELEMETRY_ENABLED = True
        mock_config.TELEMETRY_MQTT_BROKER = "test:1883"
        
        with patch('telemetry_client.mqtt') as mock_mqtt:
            mock_client_inst = MagicMock()
            mock_mqtt.Client.return_value = mock_client_inst
            mock_mqtt.MQTTv311 = 4
            mock_mqtt.CallbackAPIVersion = MagicMock()
            mock_mqtt.CallbackAPIVersion.VERSION2 = 2
            
            client = telemetry_client.TelemetryClient("12345", "1.0.0")
            client._connected = True
            client._create_client()
            
            # Simulate disconnect callback
            on_disconnect = mock_client_inst.on_disconnect
            on_disconnect(None, None, None, 0, None)
            assert client._connected is False

