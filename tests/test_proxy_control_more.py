import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline
ControlSettings = importlib.import_module("control_settings").ControlSettings


def _proxy_stub() -> SimpleNamespace:
    return SimpleNamespace(
        device_id="DEV1",
        box_connected=True,
        _last_data_epoch=None,
        _active_box_peer=None,
        _ctrl=SimpleNamespace(publish_setting_event_state=AsyncMock()),
    )


def test_formatters_are_stable():
    tx = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
        "stage": "queued",
        "tx_id": "tx-1",
        "_attempts": 2,
    }
    result = {
        "status": "applied",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": "1",
        "tx_id": "tx-1",
    }

    assert "tbl_box_prms/MODE=1" in ControlPipeline.format_tx(tx)
    assert "applied tbl_box_prms/MODE=1" in ControlPipeline.format_result(result)


def test_append_to_log_writes_when_path_set(tmp_path):
    pipe = ControlPipeline(_proxy_stub())
    target = tmp_path / "control.log"
    pipe.log_path = str(target)
    pipe.append_to_log("line\n")
    assert target.read_text(encoding="utf-8") == "line\n"


def test_control_settings_parse_setting_event_roundtrip():
    parsed = ControlSettings.parse_setting_event(
        "Remotely : tbl_box_prms / MODE: [0]->[1]"
    )
    assert parsed == ("tbl_box_prms", "MODE", "0", "1")


@pytest.mark.asyncio
async def test_control_settings_handle_setting_event_tracks_buffer():
    cs = ControlSettings(_proxy_stub())
    await cs.handle_setting_event(
        parsed={"Type": "Setting", "Content": "Remotely : tbl_box_prms / MODE: [0]->[1]"},
        table_name="tbl_events",
        device_id="DEV1",
    )
    assert cs.set_commands_buffer == [
        {
            "key": "tbl_box_prms:MODE",
            "value": "1",
            "result": "applied",
            "source": "tbl_events",
        }
    ]
