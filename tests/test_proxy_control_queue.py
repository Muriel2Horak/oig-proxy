"""Tests for queue management in ControlPipeline."""

import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_maybe_start_next_keeps_queue_intact_for_stub_pipeline():
    """Test maybe_start_next does not modify queue for stub pipeline."""
    pipe = ControlPipeline(object())
    pipe.queue = [{"tx_id": "a"}, {"tx_id": "b"}]
    await pipe.maybe_start_next()
    assert [item["tx_id"] for item in pipe.queue] == ["a", "b"]
