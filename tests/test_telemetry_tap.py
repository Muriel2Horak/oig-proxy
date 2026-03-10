# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from __future__ import annotations

import asyncio
import gc
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import telemetry_tap
from telemetry_tap import TapType, TelemetryTap, TelemetryTapAdapter, TelemetryTapFactory


async def _wait_until_empty(tasks: set[asyncio.Task[object]]) -> None:
    for _ in range(10):
        if not tasks:
            return
        await asyncio.sleep(0)


class FakeLoop:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.calls: list[tuple[object, str | None]] = []
        self.last_task: asyncio.Task[object] | None = None

    def create_task(self, coro, name=None):  # noqa: ANN001,ANN201
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append((coro, name))
        task = asyncio.create_task(coro, name=name)
        self.last_task = task
        return task


@pytest.mark.asyncio
async def test_attach_and_stats_property_tracks_success_and_failure(caplog):
    background: set[asyncio.Task[object]] = set()
    tap = TelemetryTap(loop=asyncio.get_running_loop(), background_tasks=background)

    async def ok() -> str:
        return "ok"

    async def fail() -> None:
        raise RuntimeError("boom")

    tap.publish(ok(), name="ok_publish")
    tap.publish(fail(), name="failing_publish")
    await _wait_until_empty(background)

    assert tap.stats == {"total": 2, "success": 1, "failed": 1}
    assert background == set()
    assert any("publish failed" in rec.message for rec in caplog.records)


def test_get_loop_uses_attached_loop():
    tap = TelemetryTap()
    fake_loop = MagicMock()
    tap.attach_loop(fake_loop)
    assert tap._get_loop() is fake_loop


def test_get_loop_without_running_loop_raises_descriptive_error():
    tap = TelemetryTap()
    with pytest.raises(RuntimeError, match="No event loop available for TelemetryTap"):
        tap._get_loop()


@pytest.mark.asyncio
async def test_publish_is_fire_and_forget_non_blocking():
    background: set[asyncio.Task[object]] = set()
    tap = TelemetryTap(loop=asyncio.get_running_loop(), background_tasks=background)
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed() -> str:
        started.set()
        await release.wait()
        return "done"

    tap.publish(delayed(), name="delayed_publish")
    await started.wait()
    assert tap.stats == {"total": 0, "success": 0, "failed": 0}

    release.set()
    await _wait_until_empty(background)
    assert tap.stats == {"total": 1, "success": 1, "failed": 0}


@pytest.mark.asyncio
async def test_publish_handles_scheduling_failure_fail_open(caplog, monkeypatch):
    tap = TelemetryTap(loop=FakeLoop(fail_with=RuntimeError("schedule failed")))

    monkeypatch.setattr(telemetry_tap, "get_correlation_id", lambda: "cid-test")

    async def never_runs() -> None:
        return None

    coro = never_runs()
    try:
        tap.publish(coro, name="telemetry_send")
    finally:
        coro.close()

    assert tap.stats == {"total": 0, "success": 0, "failed": 1}
    assert any("failed to schedule telemetry_send" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_publish_uses_running_loop_when_not_attached():
    background: set[asyncio.Task[object]] = set()
    tap = TelemetryTap(background_tasks=background)

    async def nop() -> None:
        return None

    tap.publish(nop(), name="auto_loop")
    await _wait_until_empty(background)
    assert tap.stats == {"total": 1, "success": 1, "failed": 0}


@pytest.mark.asyncio
async def test_publish_sync_wrapper_runs_in_executor_and_is_non_blocking():
    background: set[asyncio.Task[object]] = set()
    tap = TelemetryTap(loop=asyncio.get_running_loop(), background_tasks=background)
    seen: list[int] = []

    def sync_fn(value: int) -> int:
        seen.append(value)
        return value * 2

    tap.publish_sync_wrapper(sync_fn, 21)
    await _wait_until_empty(background)
    assert seen == [21]
    assert tap.stats == {"total": 1, "success": 1, "failed": 0}


@pytest.mark.asyncio
async def test_publish_with_result_success_and_failure(caplog):
    tap = TelemetryTap()

    async def ok() -> int:
        return 7

    async def fail() -> int:
        raise ValueError("bad")

    assert await tap.publish_with_result(ok(), name="ok", default=99) == 7
    assert await tap.publish_with_result(fail(), name="fail", default=99) == 99
    assert tap.stats == {"total": 2, "success": 1, "failed": 1}
    assert any("failed (returning default)" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_adapter_send_methods_delegate_to_tap_publish():
    class Client:
        async def send_telemetry(self, metrics):  # noqa: ANN001
            return metrics

        async def send_event(self, event_type, details):  # noqa: ANN001
            return {"event_type": event_type, "details": details}

    background: set[asyncio.Task[object]] = set()
    tap = TelemetryTap(loop=asyncio.get_running_loop(), background_tasks=background)
    adapter = TelemetryTapAdapter(Client(), tap)

    adapter.send_telemetry({"power": 1})
    adapter.send_event("boot", {"source": "test"})
    await _wait_until_empty(background)

    assert tap.stats == {"total": 2, "success": 2, "failed": 0}


def test_adapter_no_client_short_circuit_and_tap_property_and_attach():
    adapter = TelemetryTapAdapter(None)
    assert isinstance(adapter.tap, TelemetryTap)

    loop = MagicMock()
    tasks: set[asyncio.Task[object]] = set()
    adapter.attach_loop(loop)
    adapter.attach_background_tasks(tasks)

    adapter.send_telemetry({"x": 1})
    adapter.send_event("evt", {})
    assert adapter.tap.stats == {"total": 0, "success": 0, "failed": 0}


@pytest.mark.asyncio
async def test_adapter_getattr_event_wrapper_handles_async_and_sync_returns():
    class Client:
        def __init__(self) -> None:
            self.sync_called = 0

        async def event_async(self, value: int) -> int:
            return value

        def event_sync(self, value: int) -> int:
            self.sync_called += 1
            return value

        def plain_attr(self) -> str:
            return "plain"

    background: set[asyncio.Task[object]] = set()
    tap = TelemetryTap(loop=asyncio.get_running_loop(), background_tasks=background)
    client = Client()
    adapter = TelemetryTapAdapter(client, tap)

    wrapped_async = adapter.event_async
    wrapped_sync = adapter.event_sync
    plain = adapter.plain_attr

    wrapped_async(5)
    wrapped_sync(6)
    await _wait_until_empty(background)

    assert tap.stats == {"total": 1, "success": 1, "failed": 0}
    assert client.sync_called == 1
    assert plain() == "plain"


def test_adapter_getattr_missing_attribute_raises_attribute_error():
    adapter = TelemetryTapAdapter(SimpleNamespace())
    with pytest.raises(AttributeError, match="has no attribute 'missing'"):
        _ = adapter.missing


def test_metrics_tap_record_get_filter_export_attach():
    factory = TelemetryTapFactory()
    metrics = factory.create(TapType.METRICS)

    metrics.record("temp", 1.5, {"env": "prod", "site": "a"})
    metrics.record("temp", 2.5, {"env": "dev", "site": "a"})
    metrics.record("power_total", 3.0, {"env": "prod"})

    assert metrics.get("temp") == 2.5
    assert metrics.get("missing") is None

    assert len(metrics.filter(name_pattern="temp*")) == 2
    assert len(metrics.filter(name_pattern="power_total")) == 1
    assert len(metrics.filter(labels={"env": "prod"})) == 2
    assert len(metrics.filter(name_pattern="temp", labels={"env": "prod", "site": "a"})) == 1

    prom = metrics.export(format="prometheus")
    assert "temp 1.5" in prom and "power_total 3.0" in prom

    exported_json = json.loads(metrics.export(format="json"))
    assert len(exported_json) == 3

    assert metrics.is_attached() is False
    metrics.attach_to_proxy()
    assert metrics.is_attached() is True


def test_events_tap_record_get_attach_and_default_payload():
    factory = TelemetryTapFactory()
    events = factory.create(TapType.EVENTS)

    events.record("connect", {"host": "127.0.0.1"})
    events.record("connect", None)
    assert events.get_events("connect") == [{"host": "127.0.0.1"}, {}]
    assert events.get_events("missing") == []

    assert events.is_attached() is False
    events.attach_to_proxy()
    assert events.is_attached() is True
    assert events.get_events("proxy_start") == [{"source": "tap"}]


def test_traces_tap_context_manager_records_trace():
    factory = TelemetryTapFactory()
    traces = factory.create(TapType.TRACES)

    with traces.trace("op1"):
        pass
    assert traces.get_traces() == [{"operation": "op1"}]


def test_factory_create_all_types_and_invalid_type():
    factory = TelemetryTapFactory()
    assert factory.create(TapType.METRICS).__class__.__name__ == "_MetricsTap"
    assert factory.create(TapType.EVENTS).__class__.__name__ == "_EventsTap"
    assert factory.create(TapType.TRACES).__class__.__name__ == "_TracesTap"

    with pytest.raises(ValueError, match="Unsupported tap type"):
        factory.create("unsupported")


@pytest.mark.asyncio
async def test_task_tracking_prevents_gc_until_done(monkeypatch):
    task_store: set[asyncio.Task[object]] = set()
    tap = TelemetryTap(loop=asyncio.get_running_loop(), background_tasks=task_store)

    done = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0)
        done.set()

    tap.publish(work(), name="gc_track")
    gc.collect()
    assert len(task_store) == 1
    await done.wait()
    await asyncio.sleep(0)
    assert len(task_store) == 0
