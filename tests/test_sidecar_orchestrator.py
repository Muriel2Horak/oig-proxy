# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,protected-access,too-many-public-methods

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import sidecar_orchestrator as module


class _Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _make_orchestrator(*, threshold: int = 3, hysteresis: float = 300.0, anti_flap: bool = True, now: float = 0.0):
    clock = _Clock(now=now)
    orch = module.SidecarOrchestrator(
        config=module.SidecarOrchestratorConfig(
            fail_threshold=threshold,
            hysteresis_seconds=hysteresis,
            enable_anti_flap=anti_flap,
        ),
        time_provider=clock.time,
    )
    return orch, clock


def test_config_and_state_defaults():
    cfg = module.SidecarOrchestratorConfig()
    assert cfg.fail_threshold == 3
    assert cfg.hysteresis_seconds == 300.0
    assert cfg.enable_anti_flap is True

    state = module.SidecarOrchestratorState()
    assert state.is_active is False
    assert state.fail_count == 0
    assert state.first_success_received is False
    assert state.hysteresis_timer is None
    assert state.stable_cloud_since is None
    assert state.activation_timestamp is None
    assert state.last_fail_timestamp is None


def test_threshold_based_activation_three_consecutive_failures():
    orch, _ = _make_orchestrator(threshold=3)

    assert orch.should_activate(fail_count=0) is False
    assert orch.should_activate(fail_count=2) is False
    assert orch.should_activate(fail_count=3) is True


def test_should_activate_guards_and_queue_path():
    orch, _ = _make_orchestrator()

    orch.record_activation()
    assert orch.should_activate(fail_count=100, queue_has_items=True, twin_available=True) is False
    orch.record_deactivation()

    assert orch.should_activate(fail_count=100, twin_available=False) is False
    assert orch.should_activate(fail_count=0, queue_has_items=True, twin_available=True) is True
    assert orch.should_activate(fail_count=0, queue_has_items=False, twin_available=True) is False


def test_should_deactivate_hysteresis_and_idle_conditions():
    orch, clock = _make_orchestrator(hysteresis=300.0)

    assert orch.should_deactivate(is_idle=True) is False

    orch.record_activation()
    assert orch.should_deactivate(is_idle=False) is False
    assert orch.should_deactivate(is_idle=True, has_inflight=True) is False
    assert orch.should_deactivate(is_idle=True, queue_length=1) is False
    assert orch.should_deactivate(is_idle=True, routing_via_twin=True) is False

    assert orch.should_deactivate(is_idle=True) is False

    orch.record_success()
    clock.advance(299.0)
    assert orch.should_deactivate(is_idle=True) is False
    state = orch.get_state()
    assert state.hysteresis_timer == pytest.approx(1.0)

    clock.advance(1.0)
    assert orch.should_deactivate(is_idle=True) is True


def test_anti_flap_protection_resets_timer_on_failure():
    orch, clock = _make_orchestrator(hysteresis=300.0, anti_flap=True)
    orch.record_activation()
    orch.record_success()
    clock.advance(240.0)
    assert orch.should_deactivate(is_idle=True) is False

    before = orch.get_state()
    assert before.first_success_received is True
    assert before.stable_cloud_since == 0.0

    orch.record_failure(reason="ack_timeout")
    after = orch.get_state()
    assert after.first_success_received is False
    assert after.hysteresis_timer is None
    assert after.stable_cloud_since is None

    clock.advance(60.0)
    assert orch.should_deactivate(is_idle=True) is False


def test_record_failure_without_anti_flap_keeps_stability_state():
    orch, _ = _make_orchestrator(anti_flap=False)
    orch.record_activation()
    orch.record_success()

    stable_before = orch.get_state().stable_cloud_since
    orch.record_failure(reason="cloud_eof")
    stable_after = orch.get_state().stable_cloud_since

    assert stable_before == stable_after
    assert orch.get_fail_count() == 1


def test_record_activation_and_deactivation_transitions_and_noop_when_repeated():
    orch, clock = _make_orchestrator(now=10.0)

    orch.record_activation()
    first = orch.get_state()
    assert first.is_active is True
    assert first.activation_timestamp == 10.0

    clock.advance(5.0)
    orch.record_activation()
    second = orch.get_state()
    assert second.activation_timestamp == 10.0

    orch.record_deactivation()
    off = orch.get_state()
    assert off.is_active is False
    assert off.activation_timestamp is None

    orch.record_deactivation()
    assert orch.get_state().is_active is False


def test_record_success_starts_timer_only_once_and_resets_fail_counter():
    orch, clock = _make_orchestrator(hysteresis=30.0)
    orch.record_failure(reason="e1")
    orch.record_failure(reason="e2")
    assert orch.get_fail_count() == 2

    orch.record_success()
    assert orch.get_fail_count() == 0
    assert orch.get_state().first_success_received is False

    orch.record_activation()
    orch.record_failure(reason="e3")
    assert orch.get_fail_count() == 1

    orch.record_success()
    s1 = orch.get_state()
    assert s1.first_success_received is True
    assert s1.hysteresis_timer == 30.0
    assert s1.stable_cloud_since == clock.time()
    assert orch.get_fail_count() == 0

    clock.advance(7.0)
    orch.record_success()
    s2 = orch.get_state()
    assert s2.stable_cloud_since == s1.stable_cloud_since


def test_decrement_hysteresis_timer_behavior():
    orch, _ = _make_orchestrator()

    orch.decrement_hysteresis_timer(10.0)
    assert orch.get_state().hysteresis_timer is None

    orch._state.hysteresis_timer = 5.0
    orch.decrement_hysteresis_timer(2.0)
    assert orch.get_state().hysteresis_timer == 3.0

    orch.decrement_hysteresis_timer(10.0)
    assert orch.get_state().hysteresis_timer == 0.0


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"cloud_healthy": True, "twin_activated": True, "force_cloud": True}, "cloud"),
        ({"cloud_healthy": False, "twin_activated": True, "force_twin": True}, "twin"),
        ({"cloud_healthy": True, "twin_activated": False}, "cloud"),
        ({"cloud_healthy": False, "twin_activated": True}, "twin"),
        ({"cloud_healthy": False, "twin_activated": False}, "local"),
        ({"cloud_healthy": False, "twin_activated": False, "force_twin": True}, "local"),
    ],
)
def test_resolve_route_target_precedence(kwargs, expected):
    orch, _ = _make_orchestrator()
    assert orch.resolve_route_target(**kwargs) == expected


def test_get_state_returns_copy_and_reset_restores_initial_state():
    orch, _ = _make_orchestrator()
    orch.record_activation()
    snap = orch.get_state()
    snap.is_active = False

    assert orch.is_active() is True

    orch.reset()
    assert orch.is_active() is False
    assert orch.get_fail_count() == 0
    assert orch.get_state().stable_cloud_since is None


def test_noop_sidecar_orchestrator_all_public_methods():
    noop = module.NoOpSidecarOrchestrator()

    assert noop.should_activate(fail_count=99, queue_has_items=True, twin_available=True) is False
    assert noop.should_deactivate(
        is_idle=True,
        has_inflight=False,
        queue_length=0,
        routing_via_twin=False,
    ) is False
    assert noop.is_active() is False
    noop.record_activation()
    noop.record_deactivation()
    noop.record_failure(reason="x")
    noop.record_success()
    assert noop.get_fail_count() == 0
    assert noop.resolve_route_target(cloud_healthy=False, twin_activated=True, force_twin=True) == "cloud"


class _StubOrchestrator(module.ISidecarOrchestrator):
    def __init__(self):
        self._active = False
        self._fail_count = 0
        self.activate_result = False
        self.deactivate_result = False
        self.activate_args = None
        self.deactivate_args = None
        self.fail_reason = None
        self.success_calls = 0
        self.activation_calls = 0
        self.deactivation_calls = 0
        self.route_args = None

    def should_activate(self, *, fail_count: int, queue_has_items: bool = False, twin_available: bool = True) -> bool:
        self.activate_args = {
            "fail_count": fail_count,
            "queue_has_items": queue_has_items,
            "twin_available": twin_available,
        }
        return self.activate_result

    def should_deactivate(
        self,
        *,
        is_idle: bool,
        has_inflight: bool = False,
        queue_length: int = 0,
        routing_via_twin: bool = False,
    ) -> bool:
        self.deactivate_args = {
            "is_idle": is_idle,
            "has_inflight": has_inflight,
            "queue_length": queue_length,
            "routing_via_twin": routing_via_twin,
        }
        return self.deactivate_result

    def is_active(self) -> bool:
        return self._active

    def record_activation(self) -> None:
        self.activation_calls += 1
        self._active = True

    def record_deactivation(self) -> None:
        self.deactivation_calls += 1
        self._active = False

    def record_failure(self, *, reason: str | None = None) -> None:
        self.fail_reason = reason
        self._fail_count += 1

    def record_success(self) -> None:
        self.success_calls += 1
        self._fail_count = 0

    def get_fail_count(self) -> int:
        return self._fail_count

    def resolve_route_target(
        self,
        *,
        cloud_healthy: bool,
        twin_activated: bool,
        force_twin: bool = False,
        force_cloud: bool = False,
    ) -> str:
        self.route_args = {
            "cloud_healthy": cloud_healthy,
            "twin_activated": twin_activated,
            "force_twin": force_twin,
            "force_cloud": force_cloud,
        }
        return "twin" if twin_activated else "cloud"


def _make_proxy_stub(*, twin=None, routing_via_twin=False, twin_available=True):
    proxy = SimpleNamespace()
    proxy._twin = twin
    proxy._hm = MagicMock()
    proxy._hm.should_route_settings_via_twin = MagicMock(return_value=routing_via_twin)
    proxy._is_twin_routing_available = MagicMock(return_value=twin_available)
    return proxy


def test_proxy_adapter_init_default_orchestrator_and_property():
    proxy = _make_proxy_stub(twin=None)
    adapter = module.ProxySidecarAdapter(proxy=proxy, orchestrator=None)
    assert isinstance(adapter.orchestrator, module.SidecarOrchestrator)


def test_proxy_adapter_check_and_activate_with_and_without_queue():
    orch = _StubOrchestrator()
    orch._fail_count = 2
    twin = SimpleNamespace(get_queue_length=AsyncMock(return_value=3), get_inflight=AsyncMock(return_value=None))
    proxy = _make_proxy_stub(twin=twin, twin_available=True)
    adapter = module.ProxySidecarAdapter(proxy=proxy, orchestrator=orch)

    orch.activate_result = True
    activated = asyncio.run(adapter.check_and_activate())
    assert activated is True
    assert orch.activation_calls == 1
    assert orch.activate_args == {"fail_count": 2, "queue_has_items": True, "twin_available": True}

    orch.activate_result = False
    proxy._twin = None
    activated = asyncio.run(adapter.check_and_activate())
    assert activated is False
    assert orch.activate_args is not None
    assert orch.activate_args["queue_has_items"] is False


def test_proxy_adapter_check_and_deactivate_paths():
    orch = _StubOrchestrator()
    twin = SimpleNamespace(get_inflight=AsyncMock(return_value=None), get_queue_length=AsyncMock(return_value=0))
    proxy = _make_proxy_stub(twin=twin, routing_via_twin=False)
    adapter = module.ProxySidecarAdapter(proxy=proxy, orchestrator=orch)

    assert asyncio.run(adapter.check_and_deactivate()) is False

    orch._active = True
    orch.deactivate_result = True
    assert asyncio.run(adapter.check_and_deactivate()) is True
    assert orch.deactivation_calls == 1
    assert orch.deactivate_args == {
        "is_idle": True,
        "has_inflight": False,
        "queue_length": 0,
        "routing_via_twin": False,
    }

    orch._active = True
    orch.deactivate_result = False
    proxy._twin = None
    assert asyncio.run(adapter.check_and_deactivate()) is False
    assert orch.deactivate_args is not None
    assert orch.deactivate_args["is_idle"] is True


def test_proxy_adapter_check_and_deactivate_non_idle_arg_mapping():
    orch = _StubOrchestrator()
    orch._active = True
    orch.deactivate_result = False

    twin = SimpleNamespace(get_inflight=AsyncMock(return_value=object()), get_queue_length=AsyncMock(return_value=4))
    proxy = _make_proxy_stub(twin=twin, routing_via_twin=True)
    adapter = module.ProxySidecarAdapter(proxy=proxy, orchestrator=orch)

    assert asyncio.run(adapter.check_and_deactivate()) is False
    assert orch.deactivate_args == {
        "is_idle": False,
        "has_inflight": True,
        "queue_length": 4,
        "routing_via_twin": True,
    }


def test_proxy_adapter_passthrough_methods_and_route_resolution():
    orch = _StubOrchestrator()
    proxy = _make_proxy_stub(twin=None)
    adapter = module.ProxySidecarAdapter(proxy=proxy, orchestrator=orch)

    adapter.record_failure(reason="connect_failed")
    assert orch.fail_reason == "connect_failed"
    adapter.record_success()
    assert orch.success_calls == 1
    adapter.record_activation()
    assert orch.activation_calls == 1
    adapter.record_deactivation()
    assert orch.deactivation_calls == 1

    orch._active = True
    target = adapter.resolve_route_target(cloud_healthy=False, force_twin=True, force_cloud=False)
    assert target == "twin"
    assert orch.route_args == {
        "cloud_healthy": False,
        "twin_activated": True,
        "force_twin": True,
        "force_cloud": False,
    }
