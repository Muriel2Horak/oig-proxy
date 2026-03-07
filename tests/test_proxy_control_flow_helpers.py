import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline
ControlSettings = importlib.import_module("control_settings").ControlSettings


def test_parse_setting_event_extracts_values():
    content = "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]"
    assert ControlSettings.parse_setting_event(content) == (
        "tbl_invertor_prm1",
        "AAC_MAX_CHRG",
        "50.0",
        "120.0",
    )
    assert ControlSettings.parse_setting_event("invalid") is None


@pytest.mark.asyncio
async def test_handle_setting_event_appends_buffer_entry():
    proxy = SimpleNamespace(_ctrl=SimpleNamespace(publish_setting_event_state=AsyncMock()))
    cs = ControlSettings(proxy)

    await cs.handle_setting_event(
        parsed={"Type": "Setting", "Content": "Remotely : tbl_box_prms / SA: [0]->[1]"},
        table_name="tbl_events",
        device_id="DEV1",
    )

    assert cs.set_commands_buffer
    assert cs.set_commands_buffer[0]["key"] == "tbl_box_prms:SA"
    proxy._ctrl.publish_setting_event_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_noop_helpers_do_not_raise():
    pipe = ControlPipeline(object())
    await pipe.publish_restart_errors()
    await pipe.maybe_start_next()
