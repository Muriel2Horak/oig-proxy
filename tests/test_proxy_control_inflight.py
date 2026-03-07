"""Tests for control pipeline inflight state management."""

import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_maybe_start_next_is_noop_without_state():
    """Test that maybe_start_next handles missing state gracefully."""
    pipe = ControlPipeline(object())
    await pipe.maybe_start_next()
    assert pipe.inflight is None


@pytest.mark.asyncio
async def test_on_box_setting_ack_keeps_inflight_unchanged():
    """Test that ACK preserves inflight state correctly."""
    pipe = ControlPipeline(object())
    pipe.inflight = {"tx_id": "1", "stage": "queued"}
    await pipe.on_box_setting_ack(tx_id="1", ack=True)
    assert pipe.inflight == {"tx_id": "1", "stage": "queued"}
