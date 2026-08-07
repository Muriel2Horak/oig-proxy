"""Konfigurace testů pro OIG Proxy v2."""
# pylint: disable=import-error,redefined-outer-name,wrong-import-order,wrong-import-position
import asyncio
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Callable, Iterator, cast

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

from twin.state import (  # noqa: E402
    AttemptRenderContext,
    CommandState,
    ControlPolicy,
    RenderedAttempt,
    TwinCommand,
)
from twin.store import TwinCommandStore  # noqa: E402

EGRESS_GUARD_KEY: pytest.StashKey[EgressGuard] = pytest.StashKey()


@pytest.fixture
def control_policy() -> ControlPolicy:
    """Return deterministic lifecycle limits in milliseconds."""
    return ControlPolicy(
        ack_timeout_ms=30_000,
        event_timeout_ms=300_000,
        pending_ttl_ms=900_000,
        max_attempts=8,
    )


@pytest.fixture
def command() -> TwinCommand:
    """Return a complete immutable command snapshot."""
    return TwinCommand(
        command_id="cmd-1",
        audit_id="audit-1",
        device_id="device-1",
        table_name="tbl_set",
        item_name="T_Room",
        value_text="22",
        raw_ingress_text='{"tbl_set":{"T_Room":22}}',
        state=CommandState.PENDING,
        created_at_ms=1,
        updated_at_ms=1,
        pending_expires_at_ms=901,
        wire_id=None,
        wire_id_set=None,
        wire_dt=None,
        attempt_count=0,
        active_session_id=None,
        ack_deadline_ms=None,
        event_deadline_ms=None,
        acked_at_ms=None,
        ack_device_rdt=None,
        completed_at_ms=None,
        predecessor_command_id=None,
        last_wire_frame=None,
        last_error=None,
    )


@pytest.fixture
def store_factory(
    tmp_path: Path,
) -> Iterator[Callable[[int], TwinCommandStore]]:
    """Open isolated durable stores with a caller-selected attempt limit."""
    stores: list[TwinCommandStore] = []

    def _factory(max_attempts: int = 8) -> TwinCommandStore:
        store = TwinCommandStore(
            tmp_path / f"twin-{len(stores)}.db",
            policy=ControlPolicy(
                ack_timeout_ms=30_000,
                event_timeout_ms=300_000,
                pending_ttl_ms=900_000,
                max_attempts=max_attempts,
            ),
        )
        store.open(now_ms=1)
        stores.append(store)
        return store

    yield _factory

    for store in reversed(stores):
        store.close()


@pytest.fixture
def store(
    store_factory: Callable[[int], TwinCommandStore],
) -> TwinCommandStore:
    """Return one opened lifecycle store using the standard policy."""
    return store_factory(8)


@pytest.fixture
def deterministic_renderer():
    """Render deterministic, attempt-distinct bytes and protocol fields."""
    call_count = 0

    def _render(context: AttemptRenderContext) -> RenderedAttempt:
        nonlocal call_count
        call_count += 1
        ver_text = f"{call_count:05d}"
        crc_text = f"{(call_count * 17) % 65536:05d}"
        frame = (
            f"{context.command.command_id}|{context.attempt_number}|"
            f"{context.wire_id}|{context.wire_id_set}|{context.wire_dt}|"
            f"{context.prepared_at_ms}|{ver_text}|{crc_text}"
        ).encode("ascii")
        return RenderedAttempt(
            tsec_text=str(context.prepared_at_ms),
            ver_text=ver_text,
            crc_text=crc_text,
            wire_frame=frame,
        )

    return _render


def pytest_sessionstart(session: pytest.Session) -> None:
    """Run the hermetic boundary probes before any local-control tests."""
    report_path = os.environ.get(
        "LOCAL_CONTROL_EGRESS_REPORT", "reports/egress-guard.json"
    )
    guard = EgressGuard(Path(report_path))
    session.config.stash[EGRESS_GUARD_KEY] = guard
    try:
        guard.run_self_probes()
    except Exception as error:  # pylint: disable=broad-exception-caught
        guard.record_startup_failure(error)
    if guard.has_failures():
        guard.write_report(pytest_exit_status=int(pytest.ExitCode.TESTS_FAILED))
        pytest.exit("local-control egress guard self-probe failed", returncode=1)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Iterator[None]:
    """Keep the guard installed through setup, call, finalizers, and teardown."""
    del nextitem
    if item.get_closest_marker("local_control") or item.get_closest_marker("e2e"):
        guard = item.config.stash[EGRESS_GUARD_KEY]
        with guard.installed(probe=False):
            yield
        return
    yield


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
        cfg.control_mqtt_enabled = False
        cfg.control_ack_timeout_s = 1.0
        cfg.control_event_timeout_s = 1.0
        cfg.control_command_ttl_s = 1.0
        cfg.control_max_attempts = 1
        cfg.twin_db_path = "/tmp/oig-proxy-test.db"
        cfg.cloud_dialog_timeout_s = 1.0
        cfg.startup_warnings = ()
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
