# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg,duplicate-code
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
    proxy.cloud_queue = DummyCloudQueue(queue_size)
    proxy.cloud_health = DummyCloudHealth(is_online=cloud_online)

    calls = {"replay": 0, "status": 0}

    def mark_replay():
        calls["replay"] += 1

    async def fake_publish_proxy_status():
        calls["status"] += 1

    proxy._ensure_replay_task_running = mark_replay
    proxy.publish_proxy_status = fake_publish_proxy_status
    return proxy, calls


def test_switch_mode_tracks_changes():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.mode_lock = asyncio.Lock()
    proxy.mode = ProxyMode.ONLINE
    proxy.stats = {"mode_changes": 0}

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


def test_maybe_switch_online_to_replay_transitions_and_calls():
    proxy, calls = _make_proxy(ProxyMode.ONLINE, queue_size=2, cloud_online=True)

    async def run():
        await proxy._maybe_switch_online_to_replay(reason="test")
        assert proxy.mode == ProxyMode.REPLAY
        assert proxy.stats["mode_changes"] == 1
        assert calls["replay"] == 1
        assert calls["status"] == 1

    asyncio.run(run())


def test_maybe_switch_online_to_replay_skips_when_empty():
    proxy, calls = _make_proxy(ProxyMode.ONLINE, queue_size=0, cloud_online=True)

    async def run():
        await proxy._maybe_switch_online_to_replay(reason="empty")
        assert proxy.mode == ProxyMode.ONLINE
        assert proxy.stats["mode_changes"] == 0
        assert calls["replay"] == 0
        assert calls["status"] == 0

    asyncio.run(run())


def test_on_cloud_state_change_cloud_down():
    proxy, calls = _make_proxy(ProxyMode.ONLINE, queue_size=0, cloud_online=True)

    async def run():
        await proxy._on_cloud_state_change("cloud_down")
        assert proxy.mode == ProxyMode.OFFLINE
        assert proxy.stats["mode_changes"] == 1
        assert calls["replay"] == 0
        assert calls["status"] == 1

    asyncio.run(run())


def test_on_cloud_state_change_recovered_with_queue():
    proxy, calls = _make_proxy(ProxyMode.OFFLINE, queue_size=3, cloud_online=True)

    async def run():
        await proxy._on_cloud_state_change("cloud_recovered")
        assert proxy.mode == ProxyMode.REPLAY
        assert proxy.stats["mode_changes"] == 1
        assert calls["replay"] == 1
        assert calls["status"] == 1

    asyncio.run(run())


def test_on_cloud_state_change_recovered_empty_queue():
    proxy, calls = _make_proxy(ProxyMode.OFFLINE, queue_size=0, cloud_online=True)

    async def run():
        await proxy._on_cloud_state_change("cloud_recovered")
        assert proxy.mode == ProxyMode.ONLINE
        assert proxy.stats["mode_changes"] == 1
        assert calls["replay"] == 0
        assert calls["status"] == 1

    asyncio.run(run())
