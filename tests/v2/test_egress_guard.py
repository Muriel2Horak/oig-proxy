"""Boundary tests for hermetic local-control test egress."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from typing import Callable, cast

import pytest

from tests.v2.egress_guard import EgressGuard, EgressViolation


ROOT_DIR = Path(__file__).parents[2]


def _local_config(**overrides: object) -> SimpleNamespace:
    config = SimpleNamespace(
        proxy_host="127.0.0.1",
        cloud_host="127.0.0.1",
        mqtt_host="127.0.0.1",
        dns_upstream="127.0.0.1",
        telemetry_mqtt_broker="127.0.0.1:1883",
        telemetry_enabled=False,
        twin_db_path="/tmp/oig-proxy-test.db",
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _run_isolated_pytest(
    tmp_path: Path,
    test_source: str,
    *,
    plugin_source: str = "",
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    test_path = tmp_path / "test_isolated_local_control.py"
    test_path.write_text(textwrap.dedent(test_source), encoding="utf-8")
    command = [sys.executable, "-m", "pytest", "-q"]
    if plugin_source:
        plugin_path = tmp_path / "local_sentinel_plugin.py"
        plugin_path.write_text(textwrap.dedent(plugin_source), encoding="utf-8")
        command.extend(["-p", "local_sentinel_plugin"])
    command.extend(["-p", "tests.v2.conftest", str(test_path)])
    report_path = tmp_path / "egress-guard.json"
    environment = os.environ.copy()
    environment["LOCAL_CONTROL_EGRESS_REPORT"] = str(report_path)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(ROOT_DIR), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        command,
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    return result, json.loads(report_path.read_text(encoding="utf-8"))


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


def test_guard_never_delegates_allowed_reverse_name_lookups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Return numeric loopback data without invoking resolver-capable originals."""
    guard = EgressGuard(tmp_path / "guard.json")

    def resolver_called(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"resolver original called: {args!r} {kwargs!r}")

    monkeypatch.setattr(socket, "gethostbyaddr", resolver_called)
    with guard.installed(probe=True):
        assert socket.gethostbyaddr("127.0.0.2") == (
            "127.0.0.2", [], ["127.0.0.2"]
        )

    monkeypatch.setattr(socket, "getnameinfo", resolver_called)
    with guard.installed(probe=True):
        assert socket.getnameinfo(("127.0.0.2", 5710), 0) == ("127.0.0.2", "5710")

    monkeypatch.setattr(socket, "getfqdn", resolver_called)
    with guard.installed(probe=True):
        assert socket.getfqdn("127.0.0.2") == "127.0.0.2"


def test_guard_preserves_reverse_name_api_signatures(  # pylint: disable=too-many-function-args
    tmp_path: Path,
) -> None:
    """Reject unsupported reverse-name arguments without consulting a resolver."""
    guard = EgressGuard(tmp_path / "guard.json")
    with guard.installed(probe=True):
        with pytest.raises(TypeError):
            cast(Callable[..., object], socket.gethostbyaddr)(
                "127.0.0.1", "unexpected"
            )
        with pytest.raises(TypeError):
            cast(Callable[..., object], socket.getnameinfo)(("127.0.0.1", 5710))
        with pytest.raises(TypeError):
            cast(Callable[..., object], socket.getfqdn)("127.0.0.1", "unexpected")


@pytest.mark.parametrize(
    ("sockaddr", "flags", "exception_type"),
    [
        (("127.0.0.1",), socket.NI_NUMERICHOST, TypeError),
        (("127.0.0.1", -1), socket.NI_NUMERICHOST, socket.gaierror),
        (("127.0.0.1", 70000), socket.NI_NUMERICHOST, socket.gaierror),
        (("127.0.0.1", object()), socket.NI_NUMERICHOST, TypeError),
        (("::1", 5710, -1, 0), socket.NI_NUMERICHOST, OverflowError),
        (("::1", 5710, 0, -1), socket.NI_NUMERICHOST, OverflowError),
        (("::1", 5710, 0, object()), socket.NI_NUMERICHOST, TypeError),
        (("127.0.0.1", 5710), "invalid", TypeError),
        (("127.0.0.1", 5710), 1 << 30, socket.gaierror),
    ],
)
def test_guard_rejects_malformed_numeric_nameinfo_without_policy_violation(
    tmp_path: Path,
    sockaddr: tuple[object, ...],
    flags: object,
    exception_type: type[Exception],
) -> None:
    """Keep malformed numeric sockaddr errors out of policy-violation accounting."""
    guard = EgressGuard(tmp_path / "guard.json")
    with guard.installed(probe=False), pytest.raises(exception_type):
        cast(Callable[..., object], socket.getnameinfo)(sockaddr, flags)
    assert guard.blocked_violation_count == 0
    assert not guard.has_failures()


def test_guard_matches_numeric_original_ipv6_nameinfo_forms(tmp_path: Path) -> None:
    """Match numeric-only platform parsing for valid IPv6 sockaddr forms."""
    numeric_flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
    original = cast(Callable[..., tuple[str, str]], socket.getnameinfo)
    guard = EgressGuard(tmp_path / "guard.json")
    for sockaddr in (("::1", 5710), ("::1", 5710, 0), ("::1", 5710, 0, 0)):
        expected = original(sockaddr, numeric_flags)
        with guard.installed(probe=True):
            assert cast(Callable[..., tuple[str, str]], socket.getnameinfo)(
                sockaddr, numeric_flags
            ) == expected


def test_guard_matches_numeric_ipv6_flow_boundary_and_name_required(
    tmp_path: Path,
) -> None:
    """Enforce platform flow limit and numeric/name-required incompatibility."""
    numeric_flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
    guard = EgressGuard(tmp_path / "guard.json")
    with guard.installed(probe=True):
        assert socket.getnameinfo(("::1", 5710, 1048575, 0), numeric_flags) == (
            "::1", "5710"
        )
        with pytest.raises(OverflowError):
            socket.getnameinfo(("::1", 5710, 1048576, 0), numeric_flags)
        with pytest.raises(socket.gaierror) as error:
            socket.getnameinfo(("::1", 5710), numeric_flags | socket.NI_NAMEREQD)
    assert error.value.errno == socket.EAI_NONAME


def test_guard_allows_ipv6_and_af_unix_local_transports(tmp_path: Path) -> None:
    """Permit direct IPv6 loopback and AF_UNIX datagrams under the guard."""
    guard = EgressGuard(tmp_path / "guard.json")
    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    unix_path = Path("/tmp") / f"egress-guard-{os.getpid()}-{id(guard)}.sock"
    try:
        listener.bind(("::1", 0))
        listener.listen(1)
        receiver.bind(str(unix_path))
        with guard.installed(probe=True):
            client.connect(listener.getsockname())
            sender.sendto(b"x", str(unix_path))
        assert receiver.recv(1) == b"x"
    finally:
        client.close()
        listener.close()
        receiver.close()
        sender.close()
        unix_path.unlink(missing_ok=True)


def test_guard_restores_nested_contexts_after_exceptions(tmp_path: Path) -> None:
    """Restore all original functions after nested contexts and an exception."""
    guard = EgressGuard(tmp_path / "guard.json")
    original = socket.getaddrinfo
    with pytest.raises(RuntimeError):
        with guard.installed(probe=True):
            with guard.installed(probe=True), pytest.raises(EgressViolation):
                socket.getaddrinfo("bridge.oigpower.cz", 5710)
            raise RuntimeError("test exception")
    assert socket.getaddrinfo is original
    assert socket.getaddrinfo("127.0.0.1", 5710)


def test_guard_accepts_safe_config_and_rejects_each_later_rule(tmp_path: Path) -> None:
    """Validate safe settings and independently reject telemetry and data paths."""
    guard = EgressGuard(tmp_path / "guard.json")
    guard.validate_config(_local_config())
    with pytest.raises(EgressViolation, match="telemetry"):
        guard.validate_config(_local_config(telemetry_enabled=True))
    with pytest.raises(EgressViolation, match="temporary twin database"):
        guard.validate_config(_local_config(twin_db_path="/data/twin_queue.db"))


def test_guard_counts_caught_config_policy_violation(tmp_path: Path) -> None:
    """Count a caught invalid transport configuration as a failed guard policy."""
    guard = EgressGuard(tmp_path / "guard.json")
    with pytest.raises(EgressViolation):
        guard.validate_config(_local_config(cloud_host="bridge.oigpower.cz"))
    assert guard.blocked_violation_count == 1
    assert guard.has_failures()


def test_plugin_guards_fixture_finalizers_and_restores_sentinel(
    tmp_path: Path,
) -> None:
    """Keep the guard active until marked-test finalizers and monkeypatch cleanup end."""
    result, report = _run_isolated_pytest(
        tmp_path,
        """
        import socket
        import pytest
        from tests.v2.egress_guard import EgressViolation

        @pytest.fixture
        def guarded_finalizer(monkeypatch):
            with pytest.raises(EgressViolation):
                socket.getaddrinfo("bridge.oigpower.cz", 5710)
            monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
            yield
            with pytest.raises(EgressViolation):
                socket.getaddrinfo("bridge.oigpower.cz", 5710)

        @pytest.mark.local_control
        def test_finalizer(guarded_finalizer):
            with pytest.raises(EgressViolation):
                socket.getaddrinfo("bridge.oigpower.cz", 5710)
        """,
        plugin_source="""
        import socket
        import pytest

        def resolver_sentinel(*args, **kwargs):
            raise AssertionError("resolver original called")

        @pytest.hookimpl(tryfirst=True)
        def pytest_sessionstart(session):
            socket.getaddrinfo = resolver_sentinel

        @pytest.hookimpl(trylast=True)
        def pytest_sessionfinish(session, exitstatus):
            if socket.getaddrinfo is not resolver_sentinel:
                raise AssertionError("stale guard wrapper restored")
        """,
    )
    assert result.returncode == 1
    assert "resolver original called" not in result.stdout + result.stderr
    assert "stale guard wrapper restored" not in result.stdout + result.stderr
    assert report["blocked_violation_count"] == 3
    assert report["status"] == "fail"


def test_plugin_guards_unmarked_tests_session_wide(tmp_path: Path) -> None:
    """Guard unmarked implementation tests before the original resolver runs."""
    result, report = _run_isolated_pytest(
        tmp_path,
        """
        import socket
        import pytest
        from tests.v2.egress_guard import EgressViolation

        def test_unmarked_implementation_boundary():
            with pytest.raises(EgressViolation):
                socket.getaddrinfo("bridge.oigpower.cz", 5710)
        """,
        plugin_source="""
        import socket
        import pytest

        def resolver_sentinel(*args, **kwargs):
            raise AssertionError("unguarded resolver called")

        @pytest.hookimpl(tryfirst=True)
        def pytest_sessionstart(session):
            socket.getaddrinfo = resolver_sentinel
        """,
    )
    assert result.returncode == 1
    assert "unguarded resolver called" not in result.stdout + result.stderr
    assert report["blocked_violation_count"] == 1
    assert report["status"] == "fail"


def test_plugin_writes_failure_report_before_startup_exit(tmp_path: Path) -> None:
    """Persist deterministic evidence when a startup self-probe raises unexpectedly."""
    result, report = _run_isolated_pytest(
        tmp_path,
        "def test_never_runs():\n    assert False\n",
        plugin_source="""
        from tests.v2.egress_guard import EgressGuard

        def broken_self_probes(self):
            raise RuntimeError("startup probe failure")

        EgressGuard.run_self_probes = broken_self_probes
        """,
    )
    assert result.returncode == 1
    assert report["status"] == "fail"
    assert report["pytest_exit_status"] == 1
    self_probes = cast(dict[str, str], report["self_probes"])
    assert self_probes["startup"] == "fail: RuntimeError"


def test_plugin_reports_failed_and_caught_egress_tests(tmp_path: Path) -> None:
    """Write failure artifacts for a failed test and a caught egress violation."""
    failed_result, failed_report = _run_isolated_pytest(
        tmp_path / "failed",
        """
        import pytest

        @pytest.mark.local_control
        def test_failure():
            assert False
        """,
    )
    assert failed_result.returncode == 1
    assert failed_report["status"] == "fail"
    assert failed_report["pytest_exit_status"] == 1

    caught_result, caught_report = _run_isolated_pytest(
        tmp_path / "caught",
        """
        import socket
        import pytest
        from tests.v2.egress_guard import EgressViolation

        @pytest.mark.local_control
        def test_caught_violation():
            with pytest.raises(EgressViolation):
                socket.getaddrinfo("bridge.oigpower.cz", 5710)
            with pytest.raises(EgressViolation):
                socket.getnameinfo(("8.8.8.8", 53), 0)
            with pytest.raises(EgressViolation):
                socket.getfqdn()
        """,
    )
    assert caught_result.returncode == 1
    assert caught_report["status"] == "fail"
    assert caught_report["blocked_violation_count"] == 3
