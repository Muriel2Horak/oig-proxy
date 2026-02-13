# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code,unused-variable
import asyncio
from unittest.mock import MagicMock, patch

from models import ProxyMode
from hybrid_mode import HybridModeManager
import proxy as proxy_module


def _make_proxy(
        mode: ProxyMode,
        queue_size: int,
        *,
        cloud_online: bool = True):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.stats = {"mode_changes": 0}
    proxy._tc = MagicMock()
    proxy._box_connected_since_epoch = None
    proxy._last_box_disconnect_reason = None

    # Create a real HybridModeManager, patching config defaults
    with patch("hybrid_mode.PROXY_MODE", "online"), \
         patch("hybrid_mode.HYBRID_FAIL_THRESHOLD", 3), \
         patch("hybrid_mode.HYBRID_RETRY_INTERVAL", 300), \
         patch("hybrid_mode.HYBRID_CONNECT_TIMEOUT", 5):
        proxy._hm = HybridModeManager(proxy)

    # Override the mode after construction to match what the test wants
    proxy._hm.mode = mode
    proxy._hm.configured_mode = "online"

    _ = {"status": 0}

    def fake_publish_proxy_status():
        _["status"] += 1

    proxy.publish_proxy_status = fake_publish_proxy_status
    return proxy, _


def test_switch_mode_tracks_changes():
    proxy, _ = _make_proxy(ProxyMode.ONLINE, queue_size=0)

    async def run():
        old_mode = await proxy._hm.switch_mode(ProxyMode.ONLINE)
        assert old_mode == ProxyMode.ONLINE
        assert proxy._hm.mode == ProxyMode.ONLINE
        assert proxy.stats["mode_changes"] == 0

        old_mode = await proxy._hm.switch_mode(ProxyMode.OFFLINE)
        assert old_mode == ProxyMode.ONLINE
        assert proxy._hm.mode == ProxyMode.OFFLINE
        assert proxy.stats["mode_changes"] == 1

    asyncio.run(run())


def test_hybrid_record_failure_triggers_offline():
    """Test that HYBRID mode switches to offline after threshold failures."""
    proxy, _ = _make_proxy(
        ProxyMode.ONLINE, queue_size=0, cloud_online=True)
    proxy._hm.configured_mode = "hybrid"
    proxy._hm.fail_threshold = 2

    # First failure - should not switch yet
    proxy._hm.record_failure(reason="test", local_ack=False)
    assert proxy._hm.fail_count == 1
    assert proxy._hm.in_offline is False

    # Second failure - should switch to offline
    proxy._hm.record_failure(reason="test", local_ack=False)
    assert proxy._hm.fail_count == 2
    assert proxy._hm.in_offline is True


def test_hybrid_record_success_resets():
    """Test that HYBRID mode resets counters on success."""
    proxy, _ = _make_proxy(
        ProxyMode.OFFLINE, queue_size=3, cloud_online=True)
    proxy._hm.configured_mode = "hybrid"
    proxy._hm.fail_count = 2
    proxy._hm.in_offline = True

    proxy._hm.record_success()
    assert proxy._hm.fail_count == 0
    assert proxy._hm.in_offline is False


def test_hybrid_no_fallback_before_threshold():
    """Test that HYBRID mode does NOT fallback to local ACK before reaching threshold."""
    proxy, _ = _make_proxy(
        ProxyMode.ONLINE, queue_size=0, cloud_online=True)
    proxy._hm.configured_mode = "hybrid"
    proxy._hm.fail_threshold = 3

    # Simulate 2 failures (below threshold)
    proxy._hm.record_failure(
        reason="test",
        local_ack=False)
    proxy._hm.record_failure(
        reason="test",
        local_ack=False)
    assert proxy._hm.fail_count == 2
    assert proxy._hm.in_offline is False  # Not in offline yet

    # Condition for fallback should be False
    assert not (proxy._hm.is_hybrid_mode() and proxy._hm.in_offline)


def test_hybrid_fallback_after_threshold():
    """Test that HYBRID mode DOES fallback to local ACK after reaching threshold."""
    proxy, _ = _make_proxy(
        ProxyMode.ONLINE, queue_size=0, cloud_online=True)
    proxy._hm.configured_mode = "hybrid"
    proxy._hm.fail_threshold = 3

    # Simulate 3 failures (at threshold)
    proxy._hm.record_failure(
        reason="test",
        local_ack=False)
    proxy._hm.record_failure(
        reason="test",
        local_ack=False)
    # fail_count = 3 → in_offline = True
    proxy._hm.record_failure(reason="test", local_ack=False)
    assert proxy._hm.fail_count == 3
    assert proxy._hm.in_offline is True  # Now in offline

    # Condition for fallback should be True
    assert proxy._hm.is_hybrid_mode() and proxy._hm.in_offline


def test_hybrid_mode_detection():
    """Test is_hybrid_mode detection."""
    proxy, _ = _make_proxy(
        ProxyMode.ONLINE, queue_size=0, cloud_online=True)

    proxy._hm.configured_mode = "hybrid"
    assert proxy._hm.is_hybrid_mode() is True

    proxy._hm.configured_mode = "online"
    assert proxy._hm.is_hybrid_mode() is False

    proxy._hm.configured_mode = "offline"
    assert proxy._hm.is_hybrid_mode() is False
