"""Tests for new helper methods in proxy.py."""

import asyncio
import time
import pytest

# pylint: disable=protected-access
import proxy as proxy_module
from tests.helpers import make_proxy

import models.ProxyMode


def test_get_current_timestamp_format(tmp_path):
    """Test _get_current_timestamp returns valid ISO format."""
    timestamp = proxy_module.OIGProxy._get_current_timestamp()

    # Should be in ISO 8601 format
    assert isinstance(timestamp, str)
    assert len(timestamp) > 0
    # Format should be YYYY-MM-DDTHH:MM:SS.ssssssZ
    assert "T" in timestamp
    assert timestamp.endswith("Z")


def test_get_current_timestamp_unique(tmp_path):
    """Test _get_current_timestamp returns different values."""
    ts1 = proxy_module.OIGProxy._get_current_timestamp()

    import time
    time.sleep(0.001)

    ts2 = proxy_module.OIGProxy._get_current_timestamp()

    # Timestamps should be slightly different (within 1ms)
    assert ts1 != ts2


def test_constants_defined(tmp_path):
    """Test all string constants are properly defined."""
    assert hasattr(proxy_module.OIGProxy, "_RESULT_ACK")
    assert hasattr(proxy_module.OIGProxy, "_RESULT_END")
    assert hasattr(proxy_module.OIGProxy, "_TIME_OFFSET")
    assert hasattr(proxy_module.OIGProxy, "_POST_DRAIN_SA_KEY")

    assert proxy_module.OIGProxy._RESULT_ACK == "<Result>ACK</Result>"
    assert proxy_module.OIGProxy._RESULT_END == "<Result>END</Result>"
    assert proxy_module.OIGProxy._TIME_OFFSET == "+00:00"
    assert proxy_module.OIGProxy._POST_DRAIN_SA_KEY == "post_drain_sa_refresh"
