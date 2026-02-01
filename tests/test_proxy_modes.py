# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg,duplicate-code,unused-variable
import asyncio

from models import ProxyMode
import proxy as proxy_module


class DummyCloudHealth:
    def __init__(self, is_online: bool = True) -> None:
        self.is_online = is_online


class DummyCloudQueue:
    def __init__(self, size: int) -> None:
        self._size = size

    def size(self) -> int:
        return self._size


def _make_proxy(mode: ProxyMode, queue_size: int, *, cloud_online: bool = True):
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode_lock = asyncio.Lock()
    proxy.mode = mode
    proxy.stats = {"mode_changes": 0}
    proxy._cloud_queue_enabled = True
    proxy._cloud_queue_disabled_warned = False
    proxy.cloud_queue = DummyCloudQueue(queue_size)
    proxy.cloud_health = DummyCloudHealth(is_online=cloud_online)
    proxy._configured_mode = "online"
    proxy._hybrid_fail_count = 0
    proxy._hybrid_fail_threshold = 3
    proxy._hybrid_retry_interval = 300.0
    proxy._hybrid_connect_timeout = 5.0
    proxy._hybrid_last_offline_time = 0.0
    proxy._hybrid_in_offline = False

    calls = {"status": 0}

    async def fake_publish_proxy_status():
        calls["status"] += 1

    proxy.publish_proxy_status = fake_publish_proxy_status
    return proxy, calls


def test_switch_mode_tracks_changes():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode_lock = asyncio.Lock()
    proxy.mode = ProxyMode.ONLINE
    proxy.stats = {"mode_changes": 0}
    proxy._configured_mode = "online"
    proxy._hybrid_fail_count = 0
    proxy._hybrid_fail_threshold = 3
    proxy._hybrid_retry_interval = 300.0
    proxy._hybrid_connect_timeout = 5.0
    proxy._hybrid_last_offline_time = 0.0
    proxy._hybrid_in_offline = False

    async def run():
        old_mode = await proxy._switch_mode(ProxyMode.ONLINE)
        assert old_mode == ProxyMode.ONLINE
        assert proxy.mode == ProxyMode.ONLINE
        assert proxy.stats["mode_changes"] == 0

        old_mode = await proxy._switch_mode(ProxyMode.OFFLINE)
        assert old_mode == ProxyMode.ONLINE
        assert proxy.mode == ProxyMode.OFFLINE
        assert proxy.stats["mode_changes"] == 1

    asyncio.run(run())


def test_hybrid_record_failure_triggers_offline():
    """Test that HYBRID mode switches to offline after threshold failures."""
    proxy, calls = _make_proxy(ProxyMode.ONLINE, queue_size=0, cloud_online=True)
    proxy._configured_mode = "hybrid"
    proxy._hybrid_fail_threshold = 2

    # First failure - should not switch yet
    proxy._hybrid_record_failure()
    assert proxy._hybrid_fail_count == 1
    assert proxy._hybrid_in_offline is False

    # Second failure - should switch to offline
    proxy._hybrid_record_failure()
    assert proxy._hybrid_fail_count == 2
    assert proxy._hybrid_in_offline is True


def test_hybrid_record_success_resets():
    """Test that HYBRID mode resets counters on success."""
    proxy, calls = _make_proxy(ProxyMode.OFFLINE, queue_size=3, cloud_online=True)
    proxy._configured_mode = "hybrid"
    proxy._hybrid_fail_count = 2
    proxy._hybrid_in_offline = True

    proxy._hybrid_record_success()
    assert proxy._hybrid_fail_count == 0
    assert proxy._hybrid_in_offline is False


def test_hybrid_no_fallback_before_threshold():
    """Test that HYBRID mode does NOT fallback to local ACK before reaching threshold."""
    proxy, calls = _make_proxy(ProxyMode.ONLINE, queue_size=0, cloud_online=True)
    proxy._configured_mode = "hybrid"
    proxy._hybrid_fail_threshold = 3

    # Simulate 2 failures (below threshold)
    proxy._hybrid_record_failure()  # fail_count = 1
    proxy._hybrid_record_failure()  # fail_count = 2
    assert proxy._hybrid_fail_count == 2
    assert proxy._hybrid_in_offline is False  # Not in offline yet

    # Condition for fallback should be False
    assert not (proxy._is_hybrid_mode() and proxy._hybrid_in_offline)


def test_hybrid_fallback_after_threshold():
    """Test that HYBRID mode DOES fallback to local ACK after reaching threshold."""
    proxy, calls = _make_proxy(ProxyMode.ONLINE, queue_size=0, cloud_online=True)
    proxy._configured_mode = "hybrid"
    proxy._hybrid_fail_threshold = 3

    # Simulate 3 failures (at threshold)
    proxy._hybrid_record_failure()  # fail_count = 1
    proxy._hybrid_record_failure()  # fail_count = 2
    proxy._hybrid_record_failure()  # fail_count = 3 → in_offline = True
    assert proxy._hybrid_fail_count == 3
    assert proxy._hybrid_in_offline is True  # Now in offline

    # Condition for fallback should be True
    assert proxy._is_hybrid_mode() and proxy._hybrid_in_offline


def test_hybrid_mode_detection():
    """Test _is_hybrid_mode detection."""
    proxy, calls = _make_proxy(ProxyMode.ONLINE, queue_size=0, cloud_online=True)

    proxy._configured_mode = "hybrid"
    assert proxy._is_hybrid_mode() is True

    proxy._configured_mode = "online"
    assert proxy._is_hybrid_mode() is False

    proxy._configured_mode = "offline"
    assert proxy._is_hybrid_mode() is False
