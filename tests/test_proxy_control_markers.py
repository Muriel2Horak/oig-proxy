"""Tests for control pipeline frame markers handling."""

import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_observe_box_frame_accepts_end_marker():
    """Test that END marker frames are handled correctly."""
    pipe = ControlPipeline(object())
    await pipe.observe_box_frame({"Content": "x"}, "END", "frame")


@pytest.mark.asyncio
async def test_observe_box_frame_accepts_tbl_events():
    """Test that tbl_events frames are handled correctly."""
    pipe = ControlPipeline(object())
    await pipe.observe_box_frame(
        {"Type": "Setting", "Content": "Remotely : tbl_box_prms / SA: [0]->[1]"},
        "tbl_events",
        "frame",
    )
