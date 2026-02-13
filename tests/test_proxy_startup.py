# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import asyncio

import control_pipeline as ctrl_module
import mqtt_publisher
import proxy as proxy_module


class DummyServer:
    class DummySocket:
        def getsockname(self):
            return ("127.0.0.1", 1234)

    def __init__(self):
        self.sockets = [self.DummySocket()]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def serve_forever(self):
        return None


def test_proxy_init_and_start(tmp_path, monkeypatch):
    class DummyMQTTQueue:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def size(self) -> int:
            return 0

    monkeypatch.setattr(
        mqtt_publisher, "MQTT_QUEUE_DB_PATH", str(
            tmp_path / "mqtt.db"))
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyMQTTQueue)
    monkeypatch.setattr(
        ctrl_module, "CONTROL_MQTT_PENDING_PATH", str(
            tmp_path / "pending.json"))
    monkeypatch.setattr(
        ctrl_module, "CONTROL_MQTT_LOG_PATH", str(
            tmp_path / "control.log"))

    proxy = proxy_module.OIGProxy("AUTO")

    proxy.mqtt_publisher.connect = lambda: True

    async def fake_health_check():
        return None

    proxy.mqtt_publisher.start_health_check = fake_health_check
    proxy.mqtt_publisher.attach_loop = lambda *_: None

    async def fake_publish():
        return None

    async def fake_proxy_status():
        return None

    proxy.publish_proxy_status = fake_proxy_status
    proxy._ctrl.publish_restart_errors = fake_publish

    async def fake_status_loop():
        return None

    async def fake_full_refresh():
        return None

    proxy._proxy_status_loop = fake_status_loop
    proxy._full_refresh_loop = fake_full_refresh

    async def fake_start_server(*_args, **_kwargs):
        return DummyServer()

    monkeypatch.setattr(asyncio, "start_server", fake_start_server)

    asyncio.run(proxy.start())


def test_proxy_start_mqtt_failure_restores_device(tmp_path, monkeypatch):
    class DummyMQTTQueue:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def size(self) -> int:
            return 0

    class DummyControlAPI:
        def __init__(self, *args, **kwargs) -> None:
            """Mock class."""

        def start(self) -> None:
            return None

    monkeypatch.setattr(proxy_module, "CONTROL_API_PORT", 123)
    monkeypatch.setattr(proxy_module, "ControlAPIServer", DummyControlAPI)
    monkeypatch.setattr(mqtt_publisher, "MQTTQueue", DummyMQTTQueue)

    proxy = proxy_module.OIGProxy("AUTO")
    proxy._mode_device_id = "DEVX"

    proxy.mqtt_publisher.connect = lambda: False

    async def fake_health_check():
        return None

    proxy.mqtt_publisher.start_health_check = fake_health_check
    proxy.mqtt_publisher.attach_loop = lambda *_: None

    async def fake_publish():
        return None

    async def fake_proxy_status():
        return None

    proxy.publish_proxy_status = fake_proxy_status
    proxy._ctrl.publish_restart_errors = fake_publish

    calls = {"control_mqtt": 0, "cache": 0}

    def fake_setup_control():
        calls["control_mqtt"] += 1

    def fake_setup_cache():
        calls["cache"] += 1

    proxy._ctrl.mqtt_enabled = True
    proxy._ctrl.setup_mqtt = fake_setup_control
    proxy._msc.setup = fake_setup_cache

    async def fake_status_loop():
        return None

    async def fake_full_refresh():
        return None

    proxy._proxy_status_loop = fake_status_loop
    proxy._full_refresh_loop = fake_full_refresh

    async def fake_start_server(*_args, **_kwargs):
        return DummyServer()

    monkeypatch.setattr(asyncio, "start_server", fake_start_server)

    asyncio.run(proxy.start())
    assert proxy.device_id == "DEVX"
    assert proxy.mqtt_publisher.device_id == "DEVX"
    assert calls["control_mqtt"] == 1
    assert calls["cache"] >= 1
