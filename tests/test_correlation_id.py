#!/usr/bin/env python3
"""
Tests for correlation ID propagation (Task 15).

This module tests:
- Correlation ID generation
- Correlation ID propagation through all layers
- Correlation ID survival through retries/failovers
- Correlation ID logging at key decision points
"""

import asyncio
import pytest
from correlation_id import (
    generate_correlation_id,
    generate_short_correlation_id,
    get_correlation_id,
    set_correlation_id,
    clear_correlation_id,
    correlation_id_context,
    correlation_id_context_frame,
    format_log_message,
    propagate_correlation_id_to_dict,
    extract_correlation_id_from_dict,
)


class TestCorrelationIdGeneration:
    """Test correlation ID generation functions."""

    def test_generate_correlation_id_format(self):
        """Test that generated correlation ID has correct format."""
        cid = generate_correlation_id()
        assert cid.startswith("oig_")
        parts = cid.split("_")
        assert len(parts) == 3
        assert parts[0] == "oig"
        assert parts[1].isdigit()  # timestamp
        assert len(parts[2]) == 8  # hex part

    def test_generate_correlation_id_uniqueness(self):
        """Test that generated correlation IDs are unique."""
        cids = [generate_correlation_id() for _ in range(100)]
        assert len(set(cids)) == 100

    def test_generate_short_correlation_id_format(self):
        """Test that generated short correlation ID has correct format."""
        cid = generate_short_correlation_id()
        assert cid.startswith("oig_")
        parts = cid.split("_")
        assert len(parts) == 2
        assert parts[0] == "oig"
        assert len(parts[1]) == 8  # hex part


class TestCorrelationIdContext:
    """Test correlation ID context management."""

    def test_get_correlation_id_auto_generates(self):
        """Test that get_correlation_id auto-generates if not set."""
        clear_correlation_id()
        cid = get_correlation_id()
        assert cid is not None
        assert cid.startswith("oig_")

    def test_set_and_get_correlation_id(self):
        """Test setting and getting correlation ID."""
        test_cid = "oig_test_12345"
        set_correlation_id(test_cid)
        assert get_correlation_id() == test_cid

    def test_clear_correlation_id(self):
        """Test clearing correlation ID."""
        set_correlation_id("oig_test_12345")
        clear_correlation_id()
        # After clear, get_correlation_id should auto-generate a new one
        cid = get_correlation_id()
        assert cid is not None

    def test_correlation_id_context_manager(self):
        """Test correlation ID context manager."""
        with correlation_id_context() as cid:
            assert cid is not None
            assert get_correlation_id() == cid
        # After context, a new ID should be generated
        new_cid = get_correlation_id()
        assert new_cid is not None

    def test_correlation_id_context_with_custom_id(self):
        """Test correlation ID context manager with custom ID."""
        custom_cid = "oig_custom_abc123"
        with correlation_id_context(custom_cid) as cid:
            assert cid == custom_cid
            assert get_correlation_id() == custom_cid

    def test_correlation_id_context_frame(self):
        """Test correlation ID context manager for frames."""
        frame_data = b"<TestFrame>data</TestFrame>"
        with correlation_id_context_frame(frame_data) as cid:
            assert cid is not None
            assert cid.startswith("oig_")


class TestCorrelationIdHelpers:
    """Test correlation ID helper functions."""

    def test_format_log_message(self):
        """Test log message formatting with correlation ID."""
        cid = "oig_test_12345"
        message = "Test message"
        formatted = format_log_message(message, cid)
        assert cid in formatted
        assert message in formatted

    def test_propagate_correlation_id_to_dict(self):
        """Test adding correlation ID to dictionary."""
        data = {"key": "value", "number": 42}
        cid = "oig_test_12345"
        result = propagate_correlation_id_to_dict(data, cid)
        assert result["correlation_id"] == cid
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_extract_correlation_id_from_dict(self):
        """Test extracting correlation ID from dictionary."""
        data = {"key": "value", "correlation_id": "oig_test_12345"}
        cid = extract_correlation_id_from_dict(data)
        assert cid == "oig_test_12345"

    def test_extract_correlation_id_from_dict_missing(self):
        """Test extracting correlation ID from dict when missing."""
        data = {"key": "value"}
        cid = extract_correlation_id_from_dict(data)
        assert cid is None

    def test_extract_correlation_id_from_dict_with_default(self):
        """Test extracting correlation ID with default value."""
        data = {"key": "value"}
        default_cid = "oig_default_12345"
        cid = extract_correlation_id_from_dict(data, default=default_cid)
        assert cid == default_cid


class TestCorrelationIdPropagation:
    """Test correlation ID propagation through layers."""

    def test_correlation_id_survives_context_switch(self):
        """Test that correlation ID survives context switches."""
        with correlation_id_context("oig_test_abc123") as cid:
            # Simulate nested function calls
            def nested_function():
                return get_correlation_id()

            def outer_function():
                return nested_function()

            result = outer_function()
            assert result == cid

    @pytest.mark.asyncio
    async def test_correlation_id_in_async_context(self):
        """Test correlation ID in async context."""
        with correlation_id_context("oig_async_test") as cid:
            async def async_function():
                return get_correlation_id()

            result = await async_function()
            assert result == cid


class TestCorrelationIdIntegration:
    """Integration tests for correlation ID."""

    def test_correlation_id_flow_through_layers(self):
        """Test correlation ID flows through all layers."""
        # Simulate frame entry
        with correlation_id_context_frame(b"<TestFrame/>") as cid:
            # Simulate proxy layer
            proxy_cid = get_correlation_id()
            assert proxy_cid == cid

            # Simulate telemetry layer
            telemetry_data = propagate_correlation_id_to_dict({"event": "frame_received"}, cid)
            assert telemetry_data["correlation_id"] == cid

            # Simulate twin layer
            twin_cid = extract_correlation_id_from_dict(telemetry_data)
            assert twin_cid == cid

            # Simulate logging
            log_msg = format_log_message("Frame processed", cid)
            assert cid in log_msg

    def test_correlation_id_survives_retry(self):
        """Test that correlation ID survives retry scenarios."""
        with correlation_id_context("oig_retry_test") as cid:
            # Simulate retry loop
            for attempt in range(3):
                current_cid = get_correlation_id()
                assert current_cid == cid, f"Correlation ID changed on attempt {attempt}"

    def test_correlation_id_auto_generation_on_missing(self):
        """Test auto-generation when correlation ID is missing."""
        clear_correlation_id()
        cid = get_correlation_id()
        assert cid is not None
        assert cid.startswith("oig_")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
