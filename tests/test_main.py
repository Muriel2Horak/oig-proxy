# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg,duplicate-code
import asyncio
import logging
import runpy

import pytest

import main as main_module


def test_check_requirements_logs(monkeypatch):
    monkeypatch.setattr(main_module, "MQTT_AVAILABLE", False)
    main_module.check_requirements()


def test_main_runs_with_device_id(monkeypatch):
    calls = []

    class DummyProxy:
        def __init__(self, device_id):
            calls.append(("init", device_id))

        async def start(self):
            calls.append(("start", None))

    async def run():
        monkeypatch.setattr(main_module, "OIGProxy", DummyProxy)
        monkeypatch.setattr(main_module, "load_sensor_map", lambda: None)
        monkeypatch.setattr(main_module.os, "getenv", lambda key: "DEVX" if key == "DEVICE_ID" else None)
        await main_module.main()

    asyncio.run(run())
    assert ("init", "DEVX") in calls
    assert ("start", None) in calls


def test_main_runs_without_device_id(monkeypatch):
    calls = []

    class DummyProxy:
        def __init__(self, device_id):
            calls.append(("init", device_id))

        async def start(self):
            calls.append(("start", None))

    async def run():
        monkeypatch.setattr(main_module, "OIGProxy", DummyProxy)
        monkeypatch.setattr(main_module, "load_sensor_map", lambda: None)
        monkeypatch.setattr(main_module.os, "getenv", lambda key: None)
        await main_module.main()

    asyncio.run(run())
    assert ("init", "AUTO") in calls


def test_main_handles_exception(monkeypatch):
    class DummyProxy:
        def __init__(self, _device_id):
            return None

        async def start(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "OIGProxy", DummyProxy)
    monkeypatch.setattr(main_module, "load_sensor_map", lambda: None)
    monkeypatch.setattr(main_module.os, "getenv", lambda key: "DEVX")

    monkeypatch.setattr(main_module.sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

    async def run():
        with pytest.raises(SystemExit) as exc:
            await main_module.main()
        return exc.value.code

    assert asyncio.run(run()) == 1


def test_main_module_interrupts(monkeypatch):
    def fake_run(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(main_module.asyncio, "run", fake_run)
    monkeypatch.setattr(main_module.sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("main", run_name="__main__")
    assert exc.value.code == 0


def test_log_sanitizer_filter():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="line1\nline2",
        args=("value\r\n", {"key": "tab\t"}),
        exc_info=None,
    )
    flt = main_module.LogSanitizerFilter()
    assert flt.filter(record) is True
    assert record.msg == "line1\\nline2"
    assert record.args[0] == "value\\r\\n"
    assert record.args[1]["key"] == "tab\\t"
