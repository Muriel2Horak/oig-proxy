"""Boundary tests for hermetic local-control test egress."""
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from typing import Callable

import pytest

from tests.v2.egress_guard import EgressGuard, EgressViolation


def test_guard_blocks_dns_resolution(tmp_path: Path) -> None:
    """Prevent a DNS resolver call from escaping a guarded test."""
    guard = EgressGuard(tmp_path / "guard.json")
    with guard.installed(probe=True), pytest.raises(EgressViolation):
        socket.getaddrinfo("bridge.oigpower.cz", 5710)


def test_guard_blocks_non_loopback_tcp(tmp_path: Path) -> None:
    """Prevent TCP connections to non-loopback numeric addresses."""
    guard = EgressGuard(tmp_path / "guard.json")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with guard.installed(probe=True), pytest.raises(EgressViolation):
        sock.connect(("192.168.1.10", 5710))
    sock.close()


def test_guard_blocks_non_loopback_udp(tmp_path: Path) -> None:
    """Prevent UDP datagrams to non-loopback numeric addresses."""
    guard = EgressGuard(tmp_path / "guard.json")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with guard.installed(probe=True), pytest.raises(EgressViolation):
        sock.sendto(b"x", ("8.8.8.8", 53))
    sock.close()


def test_guard_rejects_production_transport_config(tmp_path: Path) -> None:
    """Reject local-control configuration that names production transports."""
    guard = EgressGuard(tmp_path / "guard.json")
    config = SimpleNamespace(
        proxy_host="127.0.0.1",
        cloud_host="bridge.oigpower.cz",
        mqtt_host="core-mosquitto",
        dns_upstream="8.8.8.8",
        telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
        telemetry_enabled=True,
        twin_db_path="/data/twin_queue.db",
    )
    with pytest.raises(EgressViolation):
        guard.validate_config(config)


def test_guard_allows_numeric_loopback(tmp_path: Path) -> None:
    """Allow a real TCP connection to a numeric IPv4 loopback listener."""
    guard = EgressGuard(tmp_path / "guard.json")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with guard.installed(probe=True):
        client.connect(listener.getsockname())
    client.close()
    listener.close()


def test_guard_artifact_schema_is_deterministic(tmp_path: Path) -> None:
    """Write report fields in the stable verifier-facing order."""
    report = tmp_path / "guard.json"
    guard = EgressGuard(report)
    guard.write_report(pytest_exit_status=0)
    assert list(json.loads(report.read_text()).keys()) == [
        "status", "policies", "self_probes", "blocked_violation_count",
        "allowed_loopback_attempt_count", "pytest_exit_status",
    ]


def test_guard_blocks_every_secondary_socket_and_dns_bypass(tmp_path: Path) -> None:
    """Reject every guarded API before it resolves or sends to a remote host."""
    guard = EgressGuard(tmp_path / "guard.json")

    def assert_blocked(action: Callable[[], object]) -> None:
        with guard.installed(probe=True), pytest.raises(EgressViolation):
            action()

    assert_blocked(lambda: socket.gethostbyname("bridge.oigpower.cz"))
    assert_blocked(lambda: socket.gethostbyname_ex("bridge.oigpower.cz"))
    assert_blocked(lambda: socket.gethostbyaddr("8.8.8.8"))
    assert_blocked(lambda: socket.getnameinfo(("8.8.8.8", 53), 0))

    def getfqdn_without_name() -> object:
        return socket.getfqdn()

    assert_blocked(getfqdn_without_name)

    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        assert_blocked(lambda: tcp_sock.connect_ex(("192.168.1.10", 5710)))
        assert_blocked(lambda: udp_sock.sendmsg([b"x"], [], 0, ("8.8.8.8", 53)))
    finally:
        tcp_sock.close()
        udp_sock.close()
