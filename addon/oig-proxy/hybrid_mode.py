"""HybridModeManager – spravuje stav hybrid/online/offline režimu proxy.

Zapouzdřuje hybrid state machine (fail counting, offline/online přepínání,
retry interval) a základní mode queries (_is_hybrid_mode, _force_offline_enabled,
_should_try_cloud).  Vlastní runtime atributy mode, mode_lock a všechny
_hybrid_* stavy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from config import (
    HYBRID_CONNECT_TIMEOUT,
    HYBRID_FAIL_THRESHOLD,
    HYBRID_RETRY_INTERVAL,
    PROXY_MODE,
)
from models import ProxyMode

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger("oig_proxy")


class HybridModeManager:
    """Pure-logic hybrid state machine delegated from OIGProxy."""

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy

        # Configured mode from options.json (online/hybrid/offline)
        self.configured_mode: str = PROXY_MODE

        # Runtime mode (for HYBRID can flip between online/offline)
        self.mode: ProxyMode = self._get_initial_mode()
        self.mode_lock: asyncio.Lock = asyncio.Lock()

        # HYBRID sub-state
        self.fail_count: int = 0
        self.fail_threshold: int = HYBRID_FAIL_THRESHOLD
        self.retry_interval: float = float(HYBRID_RETRY_INTERVAL)
        self.connect_timeout: float = float(HYBRID_CONNECT_TIMEOUT)
        self.last_offline_time: float = 0.0
        self.in_offline: bool = False

        # Telemetry-facing hybrid state (read by TelemetryCollector)
        self.state: str | None = None
        self.state_since_epoch: float | None = None
        self.last_offline_reason: str | None = None

        if self.configured_mode == "hybrid":
            self.state = "offline" if self.in_offline else "online"
            self.state_since_epoch = time.time()

    # ------------------------------------------------------------------
    # Mode queries
    # ------------------------------------------------------------------

    def is_hybrid_mode(self) -> bool:
        """Returns True if configured mode is HYBRID."""
        return self.configured_mode == "hybrid"

    def force_offline_enabled(self) -> bool:
        """Returns True if configured mode is OFFLINE."""
        return self.configured_mode == "offline"

    def _get_initial_mode(self) -> ProxyMode:
        """Determine initial ProxyMode from config."""
        if self.configured_mode == "offline":
            return ProxyMode.OFFLINE
        if self.configured_mode == "hybrid":
            return ProxyMode.HYBRID
        return ProxyMode.ONLINE

    def should_try_cloud(self) -> bool:
        """Determine if we should try to connect to cloud.

        ONLINE mode: always try
        HYBRID mode: try if not in offline state, or if retry interval passed
        OFFLINE mode: never try
        """
        if self.configured_mode == "offline":
            return False
        if self.configured_mode == "online":
            return True
        # HYBRID mode
        if not self.in_offline:
            return True
        # Check if retry interval passed
        elapsed = time.time() - self.last_offline_time
        if elapsed >= self.retry_interval:
            logger.info(
                "☁️ HYBRID: retry interval (%.0fs) passed, trying cloud...",
                self.retry_interval,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Hybrid state transitions
    # ------------------------------------------------------------------

    def record_failure(
        self,
        *,
        reason: str | None = None,
        local_ack: bool | None = None,
    ) -> None:
        """Record a cloud failure for HYBRID mode."""
        if not self.is_hybrid_mode():
            return
        self.fail_count += 1
        if self.in_offline:
            # Restart offline window after each failed probe so we only
            # attempt once per retry interval.
            self.last_offline_time = time.time()
            self.last_offline_reason = reason or self.last_offline_reason
        if self.fail_count >= self.fail_threshold:
            if not self.in_offline:
                transition_time = time.time()
                self._proxy._tc.record_hybrid_state_end(
                    ended_at=transition_time,
                    reason=reason or "cloud_failure",
                )
                self.in_offline = True
                self.last_offline_time = time.time()
                self.state = "offline"
                self.state_since_epoch = transition_time
                self.last_offline_reason = reason or "unknown"
                self._proxy._tc.record_offline_event(
                    reason=reason, local_ack=local_ack,
                )
                logger.warning(
                    "☁️ HYBRID: %d failures → switching to offline mode",
                    self.fail_count,
                )

    def record_success(self) -> None:
        """Record a cloud success for HYBRID mode."""
        if not self.is_hybrid_mode():
            return
        if self.in_offline:
            logger.info(
                "☁️ HYBRID: cloud recovered → switching to online mode")
            transition_time = time.time()
            self._proxy._tc.record_hybrid_state_end(
                ended_at=transition_time,
                reason=self.last_offline_reason or "cloud_recovered",
            )
            self.state = "online"
            self.state_since_epoch = transition_time
            self.last_offline_reason = None
        self.fail_count = 0
        self.in_offline = False

    # ------------------------------------------------------------------
    # Async mode switching
    # ------------------------------------------------------------------

    async def switch_mode(self, new_mode: ProxyMode) -> ProxyMode:
        """Atomicky přepne režim a vrátí předchozí hodnotu."""
        async with self.mode_lock:
            old_mode = self.mode
            if old_mode != new_mode:
                self.mode = new_mode
                self._proxy.stats["mode_changes"] += 1
            return old_mode

    async def get_current_mode(self) -> ProxyMode:
        """Vrátí efektivní runtime režim."""
        if self.force_offline_enabled():
            return ProxyMode.OFFLINE
        async with self.mode_lock:
            return self.mode
