#!/usr/bin/env python3
"""
Mode manager – spravuje stav ONLINE/HYBRID/OFFLINE režimu proxy.

Zapouzdřuje hybrid state machine (fail counting, offline/online přepínání,
retry interval) a základní mode queries.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING

try:
    from ..config import Config
except ImportError:
    from config import Config  # type: ignore[no-redef]

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ConnectionMode(Enum):
    """Runtime connection mode – aktuální stav spojení."""

    ONLINE = "online"
    OFFLINE = "offline"


class ModeManager:
    """Pure-logic mode state machine pro OIG Proxy v2.

    Attributes:
        configured_mode: Nakonfigurovaný režim z config (online/hybrid/offline)
        runtime_mode: Aktuální runtime režim (ONLINE nebo OFFLINE)
        fail_count: Počet po sobě jdoucích selhání (pro HYBRID)
        last_offline_time: Čas posledního přechodu do offline
        in_offline: True pokud je aktuálně v offline stavu
    """

    def __init__(self, config: Config) -> None:
        self.config = config

        # Configured mode from options.json (online/hybrid/offline)
        self.configured_mode: str = getattr(config, "proxy_mode", "online")

        # Runtime mode (for HYBRID can flip between online/offline)
        self.runtime_mode: ConnectionMode = self._get_initial_mode()
        self._mode_lock: asyncio.Lock = asyncio.Lock()

        # HYBRID sub-state
        self.fail_count: int = 0
        self.fail_threshold: int = getattr(config, "hybrid_fail_threshold", 3)
        self.retry_interval: float = float(
            getattr(config, "hybrid_retry_interval", 60.0)
        )
        self.last_offline_time: float = 0.0
        self.in_offline: bool = False

    def _get_initial_mode(self) -> ConnectionMode:
        """Určí počáteční ConnectionMode z konfigurace."""
        if self.configured_mode == "offline":
            return ConnectionMode.OFFLINE
        # HYBRID starts as ONLINE, can flip to OFFLINE
        return ConnectionMode.ONLINE

    def is_hybrid_mode(self) -> bool:
        """Vrátí True pokud je nakonfigurovaný režim HYBRID."""
        return self.configured_mode == "hybrid"

    def force_offline_enabled(self) -> bool:
        """Vrátí True pokud je nakonfigurovaný režim OFFLINE."""
        return self.configured_mode == "offline"

    def should_try_cloud(self) -> bool:
        """Určí, zda se má pokusit o připojení k cloudu.

        ONLINE mode: vždy zkusit
        HYBRID mode: zkusit pokud není v offline stavu, nebo pokud uplynul retry interval
        OFFLINE mode: nikdy nezkoušet
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

    def record_failure(self, reason: str | None = None) -> None:
        """Zaznamená selhání cloudu pro HYBRID režim.

        Args:
            reason: Důvod selhání (pro logging)
        """
        if not self.is_hybrid_mode():
            return

        self.fail_count += 1

        if self.in_offline:
            # Restart offline window after each failed probe
            self.last_offline_time = time.time()
            if reason:
                logger.debug("HYBRID: still offline, failure reason: %s", reason)

        if self.fail_count >= self.fail_threshold:
            if not self.in_offline:
                logger.warning(
                    "☁️ HYBRID: %d failures → switching to offline mode",
                    self.fail_count,
                )
                self.in_offline = True
                self.last_offline_time = time.time()
                self.runtime_mode = ConnectionMode.OFFLINE

    def record_success(self) -> None:
        """Zaznamená úspěšné připojení k cloudu pro HYBRID režim."""
        if not self.is_hybrid_mode():
            return

        if self.in_offline:
            logger.info("☁️ HYBRID: cloud recovered → switching to online mode")
            self.in_offline = False
            self.runtime_mode = ConnectionMode.ONLINE

        self.fail_count = 0

    def is_offline(self) -> bool:
        """Vrátí True pokud je aktuálně v offline režimu."""
        if self.force_offline_enabled():
            return True
        return self.in_offline

    async def switch_mode(self, new_mode: ConnectionMode) -> ConnectionMode:
        """Atomicky přepne režim a vrátí předchozí hodnotu."""
        async with self._mode_lock:
            old_mode = self.runtime_mode
            if old_mode != new_mode:
                self.runtime_mode = new_mode
                logger.info("Mode switched: %s → %s", old_mode.value, new_mode.value)
            return old_mode

    async def get_current_mode(self) -> ConnectionMode:
        """Vrátí efektivní runtime režim."""
        if self.force_offline_enabled():
            return ConnectionMode.OFFLINE
        async with self._mode_lock:
            return self.runtime_mode

    async def apply_configured_mode(self, configured_mode: str) -> bool:
        mode = str(configured_mode).strip().lower()
        if mode not in {"online", "hybrid", "offline"}:
            return False

        async with self._mode_lock:
            self.configured_mode = mode
            if mode == "offline":
                self.in_offline = True
                self.runtime_mode = ConnectionMode.OFFLINE
            elif mode == "online":
                self.in_offline = False
                self.fail_count = 0
                self.runtime_mode = ConnectionMode.ONLINE
            else:
                self.in_offline = False
                self.fail_count = 0
                self.runtime_mode = ConnectionMode.ONLINE

        logger.info("Configured proxy mode applied: %s", mode)
        return True
