"""Tests for post-drain behavior in ControlPipeline."""

import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_publish_restart_errors_noop_even_with_pending_like_state():
    """Test publish_restart_errors is noop when queue has pending items."""
    pipe = ControlPipeline(object())
    pipe.queue = [{"tx_id": "1"}]
    await pipe.publish_restart_errors()
    assert pipe.queue == [{"tx_id": "1"}]
