# pyright: reportMissingImports=false
# pylint: disable=protected-access

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import proxy as proxy_module
import sidecar_orchestrator as sidecar_module


class _Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def time(self) -> float:
        return self.now


def _make_proxy_for_deactivation(clock: _Clock):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._twin_mode_active = True
    proxy._pending_twin_activation = False
    proxy._hm = MagicMock()
    proxy._hm.should_route_settings_via_twin = MagicMock(return_value=False)
    proxy._twin = SimpleNamespace(
        get_inflight=AsyncMock(return_value=None),
        get_queue_length=AsyncMock(return_value=0),
    )
    proxy._sidecar_adapter = sidecar_module.ProxySidecarAdapter(
        proxy=proxy,
        orchestrator=sidecar_module.SidecarOrchestrator(
            config=sidecar_module.SidecarOrchestratorConfig(hysteresis_seconds=300.0),
            time_provider=clock.time,
        ),
    )
    return proxy


def test_deactivate_after_300s_stable_cloud_window():
    clock = _Clock()
    proxy = _make_proxy_for_deactivation(clock)
    proxy._sidecar_adapter.record_activation()
    proxy._sidecar_adapter.record_success()

    asyncio.run(proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=13))
    assert proxy._twin_mode_active is True

    clock.now = 299.0
    asyncio.run(proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=13))
    assert proxy._twin_mode_active is True

    clock.now = 300.0
    asyncio.run(proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=13))
    assert proxy._twin_mode_active is False


def test_fail_at_240s_resets_timer_and_keeps_twin_active():
    clock = _Clock()
    proxy = _make_proxy_for_deactivation(clock)
    proxy._sidecar_adapter.record_activation()
    proxy._sidecar_adapter.record_success()

    clock.now = 240.0
    asyncio.run(proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=13))
    assert proxy._twin_mode_active is True

    proxy._sidecar_adapter.record_failure(reason="ack_timeout")
    clock.now = 300.0
    asyncio.run(proxy._maybe_deactivate_session_twin_mode_if_idle(conn_id=13))
    assert proxy._twin_mode_active is True
