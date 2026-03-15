"""
Testy pro proxy/mode.py — ModeManager a ConnectionMode.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from proxy.mode import ConnectionMode, ModeManager


class MockConfig:
    """Mock Config pro testy ModeManager."""

    def __init__(self, proxy_mode="online", hybrid_fail_threshold=3, hybrid_retry_interval=60.0):
        self.proxy_mode = proxy_mode
        self.hybrid_fail_threshold = hybrid_fail_threshold
        self.hybrid_retry_interval = hybrid_retry_interval


# -----------------------------------------------------------------------------
# ConnectionMode enum
# -----------------------------------------------------------------------------

def test_connection_mode_values():
    """ConnectionMode má správné hodnoty."""
    assert ConnectionMode.ONLINE.value == "online"
    assert ConnectionMode.OFFLINE.value == "offline"


# -----------------------------------------------------------------------------
# ModeManager initialization
# -----------------------------------------------------------------------------

def test_mode_manager_online_init():
    """ModeManager v ONLINE režimu inicializuje správně."""
    config = MockConfig(proxy_mode="online")
    mm = ModeManager(config)

    assert mm.configured_mode == "online"
    assert mm.runtime_mode == ConnectionMode.ONLINE
    assert mm.fail_count == 0
    assert mm.in_offline is False
    assert mm.fail_threshold == 3
    assert mm.retry_interval == 60.0


def test_mode_manager_offline_init():
    """ModeManager v OFFLINE režimu inicializuje správně."""
    config = MockConfig(proxy_mode="offline")
    mm = ModeManager(config)

    assert mm.configured_mode == "offline"
    assert mm.runtime_mode == ConnectionMode.OFFLINE
    assert mm.in_offline is False  # OFFLINE mode doesn't use in_offline flag


def test_mode_manager_hybrid_init():
    """ModeManager v HYBRID režimu inicializuje správně."""
    config = MockConfig(proxy_mode="hybrid")
    mm = ModeManager(config)

    assert mm.configured_mode == "hybrid"
    assert mm.runtime_mode == ConnectionMode.ONLINE  # HYBRID starts as ONLINE
    assert mm.fail_count == 0
    assert mm.in_offline is False


# -----------------------------------------------------------------------------
# Mode queries
# -----------------------------------------------------------------------------

def test_is_hybrid_mode():
    """is_hybrid_mode() vrací správnou hodnotu."""
    online_mm = ModeManager(MockConfig(proxy_mode="online"))
    hybrid_mm = ModeManager(MockConfig(proxy_mode="hybrid"))
    offline_mm = ModeManager(MockConfig(proxy_mode="offline"))

    assert online_mm.is_hybrid_mode() is False
    assert hybrid_mm.is_hybrid_mode() is True
    assert offline_mm.is_hybrid_mode() is False


def test_force_offline_enabled():
    """force_offline_enabled() vrací správnou hodnotu."""
    online_mm = ModeManager(MockConfig(proxy_mode="online"))
    hybrid_mm = ModeManager(MockConfig(proxy_mode="hybrid"))
    offline_mm = ModeManager(MockConfig(proxy_mode="offline"))

    assert online_mm.force_offline_enabled() is False
    assert hybrid_mm.force_offline_enabled() is False
    assert offline_mm.force_offline_enabled() is True


# -----------------------------------------------------------------------------
# should_try_cloud
# -----------------------------------------------------------------------------

def test_should_try_cloud_online():
    """ONLINE mode vždy zkouší cloud."""
    mm = ModeManager(MockConfig(proxy_mode="online"))
    assert mm.should_try_cloud() is True


def test_should_try_cloud_offline():
    """OFFLINE mode nikdy nezkouší cloud."""
    mm = ModeManager(MockConfig(proxy_mode="offline"))
    assert mm.should_try_cloud() is False


def test_should_try_cloud_hybrid_online():
    """HYBRID mode zkouší cloud když není v offline stavu."""
    mm = ModeManager(MockConfig(proxy_mode="hybrid"))
    assert mm.should_try_cloud() is True


def test_should_try_cloud_hybrid_offline():
    """HYBRID mode nezkouší cloud když je v offline stavu a retry interval neuplynul."""
    import time
    mm = ModeManager(MockConfig(proxy_mode="hybrid", hybrid_retry_interval=60.0))
    mm.in_offline = True
    mm.last_offline_time = time.time()  # Just switched to offline

    assert mm.should_try_cloud() is False


def test_should_try_cloud_hybrid_retry_interval_passed():
    """HYBRID mode zkouší cloud po uplynutí retry intervalu."""
    import time
    mm = ModeManager(MockConfig(proxy_mode="hybrid", hybrid_retry_interval=0.1))
    mm.in_offline = True
    mm.last_offline_time = time.time() - 1.0  # 1 second ago

    assert mm.should_try_cloud() is True


# -----------------------------------------------------------------------------
# record_failure / record_success
# -----------------------------------------------------------------------------

def test_record_failure_non_hybrid_ignored():
    """record_failure() je ignorováno v non-HYBRID režimu."""
    mm = ModeManager(MockConfig(proxy_mode="online"))
    mm.record_failure(reason="test")
    assert mm.fail_count == 0


def test_record_failure_hybrid_increments_count():
    """record_failure() zvyšuje fail_count v HYBRID režimu."""
    mm = ModeManager(MockConfig(proxy_mode="hybrid", hybrid_fail_threshold=3))

    mm.record_failure(reason="test1")
    assert mm.fail_count == 1
    assert mm.in_offline is False

    mm.record_failure(reason="test2")
    assert mm.fail_count == 2
    assert mm.in_offline is False


def test_record_failure_hybrid_switches_to_offline():
    """record_failure() přepne do offline po dosažení thresholdu."""
    mm = ModeManager(MockConfig(proxy_mode="hybrid", hybrid_fail_threshold=2))

    mm.record_failure(reason="test1")
    assert mm.fail_count == 1
    assert mm.in_offline is False
    assert mm.runtime_mode == ConnectionMode.ONLINE

    mm.record_failure(reason="test2")
    assert mm.fail_count == 2
    assert mm.in_offline is True
    assert mm.runtime_mode == ConnectionMode.OFFLINE
    assert mm.last_offline_time > 0


def test_record_failure_updates_last_offline_time():
    """record_failure() aktualizuje last_offline_time při selhání v offline stavu."""
    import time
    mm = ModeManager(MockConfig(proxy_mode="hybrid", hybrid_fail_threshold=1))
    mm.record_failure(reason="test")

    old_time = mm.last_offline_time
    time.sleep(0.01)
    mm.record_failure(reason="test2")

    assert mm.last_offline_time > old_time


def test_record_success_non_hybrid_ignored():
    """record_success() je ignorováno v non-HYBRID režimu."""
    mm = ModeManager(MockConfig(proxy_mode="online"))
    mm.fail_count = 5
    mm.record_success()
    assert mm.fail_count == 5


def test_record_success_hybrid_resets_count():
    """record_success() resetuje fail_count v HYBRID režimu."""
    mm = ModeManager(MockConfig(proxy_mode="hybrid"))
    mm.fail_count = 5
    mm.record_success()
    assert mm.fail_count == 0


def test_record_success_hybrid_switches_to_online():
    """record_success() přepne z offline zpět na online."""
    mm = ModeManager(MockConfig(proxy_mode="hybrid", hybrid_fail_threshold=1))
    mm.record_failure(reason="test")
    assert mm.in_offline is True
    assert mm.runtime_mode == ConnectionMode.OFFLINE

    mm.record_success()
    assert mm.in_offline is False
    assert mm.runtime_mode == ConnectionMode.ONLINE


# -----------------------------------------------------------------------------
# is_offline
# -----------------------------------------------------------------------------

def test_is_offline_online_mode():
    """is_offline() vrací False v ONLINE režimu."""
    mm = ModeManager(MockConfig(proxy_mode="online"))
    assert mm.is_offline() is False


def test_is_offline_offline_mode():
    """is_offline() vrací True v OFFLINE režimu."""
    mm = ModeManager(MockConfig(proxy_mode="offline"))
    assert mm.is_offline() is True


def test_is_offline_hybrid_online():
    """is_offline() vrací False když HYBRID je online."""
    mm = ModeManager(MockConfig(proxy_mode="hybrid"))
    assert mm.is_offline() is False


def test_is_offline_hybrid_offline():
    """is_offline() vrací True když HYBRID je offline."""
    mm = ModeManager(MockConfig(proxy_mode="hybrid", hybrid_fail_threshold=1))
    mm.record_failure(reason="test")
    assert mm.is_offline() is True


# -----------------------------------------------------------------------------
# Async mode switching
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_switch_mode():
    """switch_mode() atomicky přepne režim."""
    mm = ModeManager(MockConfig(proxy_mode="online"))

    old_mode = await mm.switch_mode(ConnectionMode.OFFLINE)
    assert old_mode == ConnectionMode.ONLINE
    assert mm.runtime_mode == ConnectionMode.OFFLINE

    old_mode = await mm.switch_mode(ConnectionMode.ONLINE)
    assert old_mode == ConnectionMode.OFFLINE
    assert mm.runtime_mode == ConnectionMode.ONLINE


@pytest.mark.asyncio
async def test_switch_mode_same_mode():
    """switch_mode() nezmění nic při stejném režimu."""
    mm = ModeManager(MockConfig(proxy_mode="online"))

    old_mode = await mm.switch_mode(ConnectionMode.ONLINE)
    assert old_mode == ConnectionMode.ONLINE
    assert mm.runtime_mode == ConnectionMode.ONLINE


@pytest.mark.asyncio
async def test_get_current_mode():
    """get_current_mode() vrací aktuální režim."""
    mm = ModeManager(MockConfig(proxy_mode="online"))
    assert await mm.get_current_mode() == ConnectionMode.ONLINE

    await mm.switch_mode(ConnectionMode.OFFLINE)
    assert await mm.get_current_mode() == ConnectionMode.OFFLINE


@pytest.mark.asyncio
async def test_get_current_mode_offline_forced():
    """get_current_mode() vrací OFFLINE když je force_offline_enabled."""
    mm = ModeManager(MockConfig(proxy_mode="offline"))
    assert await mm.get_current_mode() == ConnectionMode.OFFLINE
