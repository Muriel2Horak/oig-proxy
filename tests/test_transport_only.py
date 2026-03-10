"""Tests for Transport Layer Extraction (Wave 2).

These tests verify the transport layer abstraction that will be extracted
from the main proxy loop. Tests are RED (failing) until implementation.

Run with: pytest tests/test_transport_only.py -v -m transport
"""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pyright: reportMissingImports=false

import pytest


class TestTransportLayerExtraction:
    """Tests for transport layer abstraction."""

    @pytest.mark.transport
    def test_transport_factory_creates_tcp_transport(self):
        """Test that transport factory can create TCP transport."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        assert transport is not None
        assert transport.protocol == "tcp"

    @pytest.mark.transport
    def test_transport_factory_creates_udp_transport(self):
        """Test that transport factory can create UDP transport."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.UDP)
        assert transport is not None
        assert transport.protocol == "udp"

    @pytest.mark.transport
    def test_tcp_transport_connect(self):
        """Test TCP transport connection."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        # This will fail until transport.connect() is implemented
        result = transport.connect("127.0.0.1", 5710)
        assert result is True

    @pytest.mark.transport
    def test_tcp_transport_send(self):
        """Test TCP transport send."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        transport.connect("127.0.0.1", 5710)
        # This will fail until transport.send() is implemented
        result = transport.send(b"<test/>")
        assert result == len(b"<test/>")

    @pytest.mark.transport
    def test_tcp_transport_receive(self):
        """Test TCP transport receive."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        transport.connect("127.0.0.1", 5710)
        # This will fail until transport.receive() is implemented
        data = transport.receive(timeout=1.0)
        assert data is not None

    @pytest.mark.transport
    def test_tcp_transport_close(self):
        """Test TCP transport close."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        transport.connect("127.0.0.1", 5710)
        # This will fail until transport.close() is implemented
        transport.close()
        assert transport.is_closed() is True


class TestTransportErrorHandling:
    """Tests for transport error handling."""

    @pytest.mark.transport
    def test_transport_connection_timeout(self):
        """Test transport handles connection timeout."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        # This will fail until timeout handling is implemented
        result = transport.connect("192.0.2.1", 5710, timeout=0.1)
        assert result is False

    @pytest.mark.transport
    def test_transport_reconnect_on_failure(self):
        """Test transport reconnects on failure."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        # This will fail until reconnect logic is implemented
        transport.connect("127.0.0.1", 5710)
        transport.simulate_failure()  # Simulate connection drop
        result = transport.reconnect()
        assert result is True


class TestTransportMetrics:
    """Tests for transport metrics collection."""

    @pytest.mark.transport
    def test_transport_tracks_bytes_sent(self):
        """Test transport tracks bytes sent."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        transport.connect("127.0.0.1", 5710)
        transport.send(b"test data")
        # This will fail until metrics are implemented
        assert transport.metrics.bytes_sent == 9

    @pytest.mark.transport
    def test_transport_tracks_bytes_received(self):
        """Test transport tracks bytes received."""
        from transport import TransportFactory, TransportType
        factory = TransportFactory()
        transport = factory.create(TransportType.TCP)
        transport.connect("127.0.0.1", 5710)
        # This will fail until metrics are implemented
        assert transport.metrics.bytes_received >= 0