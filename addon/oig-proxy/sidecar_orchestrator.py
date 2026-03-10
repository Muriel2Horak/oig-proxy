"""SidecarOrchestrator – Activation orchestrator interface for twin sidecar.

This module defines the explicit interface between proxy transport and sidecar
orchestrator, separating activation decision logic from frame forwarding.

Key responsibilities:
- Threshold-based activation (3 consecutive failures from Task 3)
- Hysteresis-based deactivation (5-minute timer from Task 4)
- Anti-flap protection (reset timer on fail events)
- Dependency injection support for testing

Usage:
    from sidecar_orchestrator import SidecarOrchestrator

    orchestrator = SidecarOrchestrator(
        fail_threshold=3,
        hysteresis_seconds=300,
    )

    # Check if twin should activate
    if orchestrator.should_activate(fail_count=3):
        activate_twin_mode()

    # Check if twin should deactivate
    if orchestrator.should_deactivate(is_idle=True):
        deactivate_twin_mode()
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from correlation_id import get_correlation_id

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)


@dataclass
class SidecarOrchestratorConfig:
    """Configuration for SidecarOrchestrator.

    Attributes:
        fail_threshold: Number of consecutive failures before activation (Task 3)
        hysteresis_seconds: Seconds to wait before deactivation (Task 4)
        enable_anti_flap: Whether to reset timer on fail events
    """

    fail_threshold: int = 3
    hysteresis_seconds: float = 300.0  # 5 minutes
    enable_anti_flap: bool = True


class ISidecarOrchestrator(ABC):
    """Abstract interface for sidecar activation orchestrator.

    This interface defines the contract between proxy transport and sidecar
    orchestrator, enabling dependency injection and testability.
    """

    @abstractmethod
    def should_activate(
        self,
        *,
        fail_count: int,
        queue_has_items: bool = False,
        twin_available: bool = True,
    ) -> bool:
        """Determine if twin mode should be activated.

        Args:
            fail_count: Current consecutive failure count
            queue_has_items: Whether twin queue has pending items
            twin_available: Whether twin routing is available

        Returns:
            True if twin mode should activate, False otherwise
        """
        ...

    @abstractmethod
    def should_deactivate(
        self,
        *,
        is_idle: bool,
        has_inflight: bool = False,
        queue_length: int = 0,
        routing_via_twin: bool = False,
    ) -> bool:
        """Determine if twin mode should be deactivated.

        Args:
            is_idle: Whether session is idle (no activity)
            has_inflight: Whether there's an inflight transaction
            queue_length: Current queue length
            routing_via_twin: Whether settings should route via twin

        Returns:
            True if twin mode should deactivate, False otherwise
        """
        ...

    @abstractmethod
    def is_active(self) -> bool:
        """Check if twin mode is currently active.

        Returns:
            True if twin mode is active, False otherwise
        """
        ...

    @abstractmethod
    def record_activation(self) -> None:
        """Record that twin mode has been activated."""
        ...

    @abstractmethod
    def record_deactivation(self) -> None:
        """Record that twin mode has been deactivated."""
        ...

    @abstractmethod
    def record_failure(self, *, reason: str | None = None) -> None:
        """Record a failure event (resets hysteresis timer if anti-flap enabled).

        Args:
            reason: Optional failure reason for logging
        """
        ...

    @abstractmethod
    def record_success(self) -> None:
        """Record a success event (marks first success after activation)."""
        ...

    @abstractmethod
    def get_fail_count(self) -> int:
        ...

    @abstractmethod
    def resolve_route_target(
        self,
        *,
        cloud_healthy: bool,
        twin_activated: bool,
        force_twin: bool = False,
        force_cloud: bool = False,
    ) -> str:
        """Resolve routing target with explicit precedence.

        Task 14: Centralized routing arbitration with clear precedence:
        1. Cloud healthy => cloud wins (precedence rule)
        2. Sidecar only when activated/local explicit
        3. No dual-writer (cloud+twin simultaneously)

        Args:
            cloud_healthy: Whether cloud connection is healthy
            twin_activated: Whether twin mode is currently active
            force_twin: Force routing via twin (explicit override)
            force_cloud: Force routing via cloud (explicit override)

        Returns:
            'cloud' - Route via cloud forwarder
            'twin' - Route via digital twin
        """
        ...


@dataclass
class SidecarOrchestratorState:
    """Internal state for SidecarOrchestrator.

    This dataclass encapsulates all mutable state to avoid hidden coupling
    via shared mutable state.
    """

    is_active: bool = False
    fail_count: int = 0
    first_success_received: bool = False
    hysteresis_timer: float | None = None
    stable_cloud_since: float | None = None
    activation_timestamp: float | None = None
    last_fail_timestamp: float | None = None


class SidecarOrchestrator(ISidecarOrchestrator):
    """Concrete implementation of sidecar activation orchestrator.

    Implements:
    - Task 3: Threshold counting (3 consecutive failures trigger activation)
    - Task 4: Hysteresis timer (5-minute delay before deactivation)
    - Anti-flap protection (fail events reset deactivation timer)

    The orchestrator is designed for composition with OIGProxy, not inheritance.
    """

    def __init__(
        self,
        config: SidecarOrchestratorConfig | None = None,
        time_provider: Callable[[], float] | None = None,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            config: Configuration for thresholds and timers
            time_provider: Optional time provider for test injection
                          (defaults to time.time)
        """
        self._config = config or SidecarOrchestratorConfig()
        self._state = SidecarOrchestratorState()
        self._time = time_provider or time.time

    def should_activate(
        self,
        *,
        fail_count: int,
        queue_has_items: bool = False,
        twin_available: bool = True,
    ) -> bool:
        """Determine if twin mode should be activated.

        Activation triggers when:
        1. fail_count >= fail_threshold (Task 3)
        2. twin is available
        3. twin mode is not already active

        Args:
            fail_count: Current consecutive failure count
            queue_has_items: Whether twin queue has pending items
            twin_available: Whether twin routing is available

        Returns:
            True if twin mode should activate, False otherwise
        """
        if self._state.is_active:
            return False

        if not twin_available:
            return False

        # Task 3: Threshold counting - activate after N consecutive failures
        if fail_count >= self._config.fail_threshold:
            logger.info(
                "SIDE_ORCH: Activation triggered (fail_count=%d >= threshold=%d)",
                fail_count,
                self._config.fail_threshold,
            )
            return True

        # Also activate if queue has items (pending activation from Task 5)
        if queue_has_items:
            logger.info(
                "SIDE_ORCH: Activation triggered (queue_has_items=True)"
            )
            return True

        return False

    def should_deactivate(
        self,
        *,
        is_idle: bool,
        has_inflight: bool = False,
        queue_length: int = 0,
        routing_via_twin: bool = False,
    ) -> bool:
        """Determine if twin mode should be deactivated.

        Deactivation uses hysteresis (Task 4):
        1. Must be idle (no inflight, empty queue, not routing via twin)
        2. Must have received first success after activation (starts stable timer)
        3. Must keep continuous cloud stability for hysteresis_seconds

        Args:
            is_idle: Whether session is idle (no activity)
            has_inflight: Whether there's an inflight transaction
            queue_length: Current queue length
            routing_via_twin: Whether settings should route via twin

        Returns:
            True if twin mode should deactivate, False otherwise
        """
        if not self._state.is_active:
            return False

        # Check if idle conditions are met
        idle_conditions_met = (
            is_idle
            and not has_inflight
            and queue_length == 0
            and not routing_via_twin
        )

        if not idle_conditions_met:
            return False

        if (
            not self._state.first_success_received
            or self._state.stable_cloud_since is None
        ):
            return False

        elapsed = self._time() - self._state.stable_cloud_since
        remaining = max(0.0, self._config.hysteresis_seconds - elapsed)
        self._state.hysteresis_timer = remaining

        if remaining <= 0:
            logger.info(
                "SIDE_ORCH: Deactivation triggered (hysteresis timer elapsed)"
            )
            return True

        return False

    def decrement_hysteresis_timer(self, elapsed_seconds: float) -> None:
        """Decrement the hysteresis timer by elapsed time.

        This method should be called periodically (e.g., each frame processed)
        to update the hysteresis timer.

        Args:
            elapsed_seconds: Time elapsed since last check
        """
        if self._state.hysteresis_timer is not None:
            self._state.hysteresis_timer -= elapsed_seconds
            if self._state.hysteresis_timer < 0:
                self._state.hysteresis_timer = 0

    def is_active(self) -> bool:
        """Check if twin mode is currently active.

        Returns:
            True if twin mode is active, False otherwise
        """
        return self._state.is_active

    def record_activation(self) -> None:
        """Record that twin mode has been activated."""
        if not self._state.is_active:
            self._state.is_active = True
            self._state.activation_timestamp = self._time()
            self._state.first_success_received = False
            self._state.hysteresis_timer = None
            self._state.stable_cloud_since = None
            self._state.fail_count = 0
            cid = get_correlation_id()
            logger.info("SIDE_ORCH: Twin mode activated (cid=%s)", cid)

    def record_deactivation(self) -> None:
        """Record that twin mode has been deactivated."""
        if self._state.is_active:
            self._state.is_active = False
            self._state.activation_timestamp = None
            self._state.first_success_received = False
            self._state.hysteresis_timer = None
            self._state.stable_cloud_since = None
            cid = get_correlation_id()
            logger.info("SIDE_ORCH: Twin mode deactivated (cid=%s)", cid)

    def record_failure(self, *, reason: str | None = None) -> None:
        """Record a failure event.

        Task 4: Anti-flap protection - fail events reset hysteresis timer
        to ensure stable connectivity before deactivation.

        Args:
            reason: Optional failure reason for logging
        """
        self._state.fail_count += 1
        self._state.last_fail_timestamp = self._time()

        if self._config.enable_anti_flap and self._state.is_active:
            logger.info(
                "SIDE_ORCH: Anti-flap reset (reason=%s), resetting stable-cloud timer",
                reason or "unknown",
            )
            self._state.hysteresis_timer = None
            self._state.stable_cloud_since = None
            self._state.first_success_received = False

    def record_success(self) -> None:
        """Record a success event.

        Marks first success after activation, which enables hysteresis timer.
        """
        if self._state.is_active and not self._state.first_success_received:
            self._state.first_success_received = True
            self._state.stable_cloud_since = self._time()
            self._state.hysteresis_timer = self._config.hysteresis_seconds
            logger.info(
                "SIDE_ORCH: Stable-cloud timer started (%.0fs)",
                self._config.hysteresis_seconds,
            )

        # Reset fail count on success (Task 3)
        if self._state.fail_count > 0:
            logger.debug(
                "SIDE_ORCH: Resetting fail_count from %d to 0",
                self._state.fail_count,
            )
            self._state.fail_count = 0

    def get_fail_count(self) -> int:
        return self._state.fail_count

    def resolve_route_target(
        self,
        *,
        cloud_healthy: bool,
        twin_activated: bool,
        force_twin: bool = False,
        force_cloud: bool = False,
    ) -> str:
        if force_cloud:
            logger.debug("SIDE_ORCH: Routing to cloud (force_cloud override)")
            return "cloud"

        if force_twin and twin_activated:
            logger.debug("SIDE_ORCH: Routing to twin (force_twin override)")
            return "twin"

        if cloud_healthy:
            logger.debug("SIDE_ORCH: Routing to cloud (cloud_healthy=True)")
            return "cloud"

        if twin_activated:
            logger.debug("SIDE_ORCH: Routing to twin (cloud unhealthy, twin activated)")
            return "twin"

        logger.debug("SIDE_ORCH: No viable route (cloud unhealthy, twin not activated)")
        return "local"

    def get_state(self) -> SidecarOrchestratorState:
        """Get current state (for testing/debugging).

        Returns:
            Copy of current state
        """
        # Return a copy to prevent external mutation
        return SidecarOrchestratorState(
            is_active=self._state.is_active,
            fail_count=self._state.fail_count,
            first_success_received=self._state.first_success_received,
            hysteresis_timer=self._state.hysteresis_timer,
            stable_cloud_since=self._state.stable_cloud_since,
            activation_timestamp=self._state.activation_timestamp,
            last_fail_timestamp=self._state.last_fail_timestamp,
        )

    def reset(self) -> None:
        """Reset orchestrator to initial state (for testing)."""
        self._state = SidecarOrchestratorState()
        logger.debug("SIDE_ORCH: State reset")


class NoOpSidecarOrchestrator(ISidecarOrchestrator):
    """No-op implementation for when sidecar orchestrator is unavailable.

    This implementation allows the proxy to continue operating without
    crashing when the sidecar orchestrator interface is not available.
    """

    def should_activate(
        self,
        *,
        fail_count: int,
        queue_has_items: bool = False,
        twin_available: bool = True,
    ) -> bool:
        """Always returns False - no activation without orchestrator."""
        return False

    def should_deactivate(
        self,
        *,
        is_idle: bool,
        has_inflight: bool = False,
        queue_length: int = 0,
        routing_via_twin: bool = False,
    ) -> bool:
        """Always returns False - no deactivation without orchestrator."""
        return False

    def is_active(self) -> bool:
        """Always returns False."""
        return False

    def record_activation(self) -> None:
        """No-op."""
        pass

    def record_deactivation(self) -> None:
        """No-op."""
        pass

    def record_failure(self, *, reason: str | None = None) -> None:
        """No-op."""
        pass

    def record_success(self) -> None:
        """No-op."""
        pass

    def get_fail_count(self) -> int:
        return 0

    def resolve_route_target(
        self,
        *,
        cloud_healthy: bool,
        twin_activated: bool,
        force_twin: bool = False,
        force_cloud: bool = False,
    ) -> str:
        return "cloud"


class ProxySidecarAdapter:
    """Adapter to integrate SidecarOrchestrator with OIGProxy.

    This adapter provides a clean composition-based integration between
    the proxy and the sidecar orchestrator, avoiding hidden coupling
    via shared mutable state.
    """

    def __init__(
        self,
        proxy: OIGProxy,
        orchestrator: ISidecarOrchestrator | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            proxy: The OIGProxy instance
            orchestrator: Optional orchestrator (creates default if None)
        """
        self._proxy = proxy
        self._orchestrator = orchestrator or SidecarOrchestrator()

    @property
    def orchestrator(self) -> ISidecarOrchestrator:
        """Get the underlying orchestrator."""
        return self._orchestrator

    async def check_and_activate(self) -> bool:
        """Check if twin should activate and activate if needed.

        Returns:
            True if activation occurred, False otherwise
        """
        fail_count = self._orchestrator.get_fail_count()

        # Check if twin is available
        twin_available = self._proxy._is_twin_routing_available()

        # Check if queue has items
        queue_has_items = False
        if self._proxy._twin is not None:
            queue_has_items = await self._proxy._twin.get_queue_length() > 0

        if self._orchestrator.should_activate(
            fail_count=fail_count,
            queue_has_items=queue_has_items,
            twin_available=twin_available,
        ):
            self._orchestrator.record_activation()
            return True

        return False

    async def check_and_deactivate(self) -> bool:
        """Check if twin should deactivate and deactivate if needed.

        Returns:
            True if deactivation occurred, False otherwise
        """
        if not self._orchestrator.is_active():
            return False

        # Check idle conditions
        has_inflight = False
        queue_length = 0
        if self._proxy._twin is not None:
            inflight = await self._proxy._twin.get_inflight()
            has_inflight = inflight is not None
            queue_length = await self._proxy._twin.get_queue_length()

        routing_via_twin = self._proxy._hm.should_route_settings_via_twin()

        # For idle detection, we consider idle if no recent activity
        # This is simplified - real implementation would track last activity time
        is_idle = not has_inflight and queue_length == 0

        if self._orchestrator.should_deactivate(
            is_idle=is_idle,
            has_inflight=has_inflight,
            queue_length=queue_length,
            routing_via_twin=routing_via_twin,
        ):
            self._orchestrator.record_deactivation()
            return True

        return False

    def record_failure(self, *, reason: str | None = None) -> None:
        """Record a failure event."""
        self._orchestrator.record_failure(reason=reason)

    def record_success(self) -> None:
        """Record a success event."""
        self._orchestrator.record_success()

    def record_activation(self) -> None:
        self._orchestrator.record_activation()

    def record_deactivation(self) -> None:
        self._orchestrator.record_deactivation()

    def resolve_route_target(
        self,
        *,
        cloud_healthy: bool,
        force_twin: bool = False,
        force_cloud: bool = False,
    ) -> str:
        twin_activated = self._orchestrator.is_active()
        return self._orchestrator.resolve_route_target(
            cloud_healthy=cloud_healthy,
            twin_activated=twin_activated,
            force_twin=force_twin,
            force_cloud=force_cloud,
        )
