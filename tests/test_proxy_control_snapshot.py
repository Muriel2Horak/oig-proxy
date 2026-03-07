"""Tests for snapshot functionality in ControlPipeline."""

import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_publish_setting_event_state_is_noop_and_safe():
    """Test publish_setting_event_state is safe and does nothing."""
    pipe = ControlPipeline(object())
    await pipe.publish_setting_event_state(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="1",
        device_id="DEV1",
        source="tbl_events",
    )
