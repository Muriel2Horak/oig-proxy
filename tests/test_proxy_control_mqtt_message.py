import importlib
import json

ControlSettings = importlib.import_module("control_settings").ControlSettings


def test_parse_setting_event_from_json_content_field():
    payload = {
        "Type": "Setting",
        "Content": "Remotely : tbl_box_prms / SA: [0]->[1]",
    }
    result = ControlSettings.parse_setting_event(payload["Content"])
    assert result == ("tbl_box_prms", "SA", "0", "1")
    assert isinstance(json.dumps(payload), str)
