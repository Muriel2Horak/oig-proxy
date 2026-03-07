import importlib
from types import SimpleNamespace

ControlSettings = importlib.import_module("control_settings").ControlSettings


def test_get_health_includes_basic_proxy_flags():
    proxy = SimpleNamespace(
        device_id="DEV1",
        box_connected=True,
        _active_box_peer="127.0.0.1:12345",
        _last_data_epoch=None,
    )
    cs = ControlSettings(proxy)
    health = cs.get_health()
    assert health["ok"] is True
    assert health["device_id"] == "DEV1"
    assert health["box_connected"] is True
