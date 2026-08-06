"""Konfigurace testů pro OIG Proxy v2."""
import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.v2.egress_guard import EgressGuard

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
V2_ADDON_DIR = os.path.join(ROOT_DIR, "addon", "oig-proxy")
V1_ADDON_DIR = os.path.join(ROOT_DIR, "addon", "oig-proxy")

# Odstraníme v1 z path, pokud tam je (pytest ho přidává automaticky)
while V1_ADDON_DIR in sys.path:
    sys.path.remove(V1_ADDON_DIR)

if V2_ADDON_DIR not in sys.path:
    sys.path.insert(0, V2_ADDON_DIR)

# v1 pro cross-referenční testy — přidáme na konec, aby v2 mělo přednost
if V1_ADDON_DIR not in sys.path:
    sys.path.append(V1_ADDON_DIR)

EGRESS_GUARD_KEY: pytest.StashKey[EgressGuard] = pytest.StashKey()
EGRESS_CONTEXT_KEY: pytest.StashKey[ExitStack] = pytest.StashKey()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Run the hermetic boundary probes before any local-control tests."""
    report_path = os.environ.get(
        "LOCAL_CONTROL_EGRESS_REPORT", "reports/egress-guard.json"
    )
    guard = EgressGuard(Path(report_path))
    guard.run_self_probes()
    session.config.stash[EGRESS_GUARD_KEY] = guard
    if guard.has_failures():
        pytest.exit("local-control egress guard self-probe failed", returncode=1)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Install the egress guard only around marked local-control tests."""
    if item.get_closest_marker("local_control") or item.get_closest_marker("e2e"):
        guard = item.config.stash[EGRESS_GUARD_KEY]
        context = ExitStack()
        context.enter_context(guard.installed(probe=False))
        item.stash[EGRESS_CONTEXT_KEY] = context


def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Restore socket and DNS functions after a marked test completes."""
    context = item.stash.get(EGRESS_CONTEXT_KEY, None)
    if context is not None:
        context.close()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Persist the local-control report and fail caught egress attempts."""
    guard = session.config.stash.get(EGRESS_GUARD_KEY, None)
    if guard is None:
        return
    if guard.has_failures() and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    guard.write_report(pytest_exit_status=int(session.exitstatus))


@pytest.fixture
def make_config():
    """Factory for test Config with sensible defaults."""
    def _factory(**overrides):
        cfg = cast(Any, SimpleNamespace())
        cfg.proxy_host = "127.0.0.1"
        cfg.proxy_port = 0
        cfg.cloud_host = "127.0.0.1"
        cfg.cloud_port = 5710
        cfg.cloud_connect_timeout = 0.1
        cfg.cloud_ack_timeout = 1.0
        cfg.proxy_mode = "online"
        cfg.hybrid_fail_threshold = 1
        cfg.hybrid_retry_interval = 0.0

        cfg.mqtt_host = "127.0.0.1"
        cfg.mqtt_port = 1883
        cfg.mqtt_username = ""
        cfg.mqtt_password = ""
        cfg.mqtt_namespace = "oig_local"
        cfg.mqtt_qos = 1
        cfg.mqtt_state_retain = True

        cfg.log_level = "DEBUG"
        cfg.telemetry_enabled = False
        cfg.telemetry_mqtt_broker = "telemetry.muriel-cz.cz:1883"
        cfg.telemetry_interval_s = 300
        cfg.proxy_status_interval = 60
        cfg.proxy_device_id = "oig_proxy"
        cfg.sensor_map_path = "/data/sensor_map.json"
        cfg.max_concurrent_connections = 100
        cfg.dns_upstream = "8.8.8.8"

        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    return _factory


@pytest.fixture
def stream_reader_from_chunks():
    """Build StreamReader preloaded with chunks and EOF."""

    def _factory(*chunks: bytes) -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        for chunk in chunks:
            if chunk:
                reader.feed_data(chunk)
        reader.feed_eof()
        return reader

    return _factory


class DummyWriter:
    """Minimal async StreamWriter-like test double."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        """Store data bytes to written list."""
        self.written.append(bytes(data))

    async def drain(self) -> None:
        """No-op drain method."""
        return None

    def close(self) -> None:
        """Mark writer as closed."""
        self.closed = True

    async def wait_closed(self) -> None:
        """No-op wait_closed method."""
        return None

    def is_closing(self) -> bool:
        """Return closed status."""
        return self.closed

    def get_extra_info(self, name: str, default=None):
        """Return extra info for peername or default."""
        if name == "peername":
            return ("127.0.0.1", 12345)
        return default


@pytest.fixture
def dummy_writer_factory():
    """Factory returning DummyWriter instances."""

    def _factory() -> DummyWriter:
        return DummyWriter()

    return _factory
