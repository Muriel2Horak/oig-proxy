"""Hermetic egress boundary for local-control pytest tests."""
from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import json
from pathlib import Path
import socket
from typing import Any, Callable, Iterator, Protocol


def _nameinfo_flag_mask() -> int:
    mask = 0
    for flag_name in (
        "NI_NOFQDN",
        "NI_NUMERICHOST",
        "NI_NAMEREQD",
        "NI_NUMERICSERV",
        "NI_DGRAM",
        "NI_IDN",
        "NI_IDN_ALLOW_UNASSIGNED",
        "NI_IDN_USE_STD3_ASCII_RULES",
    ):
        mask |= getattr(socket, flag_name, 0)
    return mask


_NAMEINFO_FLAG_MASK = _nameinfo_flag_mask()


class EgressViolation(AssertionError):
    """Raised when a local-control test attempts non-loopback egress."""


class LocalControlConfig(Protocol):  # pylint: disable=too-few-public-methods
    """Configuration fields that can select an external transport."""

    proxy_host: object
    cloud_host: object
    mqtt_host: object
    dns_upstream: object
    telemetry_mqtt_broker: object
    telemetry_enabled: bool
    twin_db_path: object


def _loopback_address(host: object) -> bool:
    """Return whether host is a numeric IPv4 or IPv6 loopback address."""
    if not isinstance(host, str):
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class EgressGuard:
    """Patch socket APIs temporarily so local-control tests fail closed."""

    def __init__(self, report_path: Path) -> None:
        self.report_path = report_path
        self.blocked_violation_count = 0
        self.probe_violation_count = 0
        self.allowed_loopback_attempt_count = 0
        self.self_probes: dict[str, str] = {}

    def validate_config(self, config: LocalControlConfig) -> None:
        """Reject settings that could direct a local-control test outward."""
        hosts = (
            config.proxy_host,
            config.cloud_host,
            config.mqtt_host,
            config.dns_upstream,
            str(config.telemetry_mqtt_broker).rsplit(":", 1)[0],
        )
        if not all(_loopback_address(host) for host in hosts):
            self._block(
                "validate_config",
                hosts,
                probe=False,
                message="local-control E2E config contains non-loopback host",
            )
        if config.telemetry_enabled:
            self._block(
                "validate_config",
                config.telemetry_enabled,
                probe=False,
                message="telemetry must be disabled in local-control E2E",
            )
        if str(config.twin_db_path).startswith("/data/"):
            self._block(
                "validate_config",
                config.twin_db_path,
                probe=False,
                message="E2E requires a temporary twin database",
            )

    @contextmanager
    def installed(self, *, probe: bool) -> Iterator[None]:
        """Install reversible guards for DNS and socket egress operations."""
        patches: list[tuple[Any, str, Any]] = []

        def patch(target: Any, name: str, replacement: Any) -> None:
            patches.append((target, name, getattr(target, name)))
            setattr(target, name, replacement)

        def guarded_dns(method: str, original: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(host: object, *args: Any, **kwargs: Any) -> Any:
                self._require_loopback(host, method, probe)
                return original(host, *args, **kwargs)

            return wrapper

        def guarded_getnameinfo(_original: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(sockaddr: object, flags: int) -> tuple[str, str]:
                host, port = self._validated_nameinfo(sockaddr, flags, probe)
                self._require_loopback(host, "getnameinfo", probe)
                return host, str(port)

            return wrapper

        def guarded_gethostbyaddr(_original: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(host: object) -> tuple[str, list[str], list[str]]:
                self._require_loopback(host, "gethostbyaddr", probe)
                numeric_host = str(host)
                return numeric_host, [], [numeric_host]

            return wrapper

        def guarded_getfqdn(_original: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(name: object = "") -> str:
                self._require_loopback(name, "getfqdn", probe)
                return str(name)

            return wrapper

        def guarded_connect(method: str, original: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(sock_obj: socket.socket, address: object) -> Any:
                self._require_socket_address(address, method, probe, sock_obj)
                return original(sock_obj, address)

            return wrapper

        def guarded_sendto(original: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(sock_obj: socket.socket, *args: Any, **kwargs: Any) -> Any:
                address = args[-1] if args else kwargs.get("address")
                if not self._is_socket_address(address, sock_obj):
                    address = self._peer_address(sock_obj)
                self._require_socket_address(address, "sendto", probe, sock_obj)
                return original(sock_obj, *args, **kwargs)

            return wrapper

        def guarded_sendmsg(original: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(sock_obj: socket.socket, *args: Any, **kwargs: Any) -> Any:
                address = args[3] if len(args) > 3 else kwargs.get("address")
                if address is None:
                    address = self._peer_address(sock_obj)
                self._require_socket_address(address, "sendmsg", probe, sock_obj)
                return original(sock_obj, *args, **kwargs)

            return wrapper

        patch(socket, "getaddrinfo", guarded_dns("getaddrinfo", socket.getaddrinfo))
        patch(socket, "gethostbyname", guarded_dns("gethostbyname", socket.gethostbyname))
        patch(
            socket,
            "gethostbyname_ex",
            guarded_dns("gethostbyname_ex", socket.gethostbyname_ex),
        )
        patch(
            socket,
            "gethostbyaddr",
            guarded_gethostbyaddr(socket.gethostbyaddr),
        )
        patch(socket, "getnameinfo", guarded_getnameinfo(socket.getnameinfo))
        patch(socket, "getfqdn", guarded_getfqdn(socket.getfqdn))
        patch(socket.socket, "connect", guarded_connect("connect", socket.socket.connect))
        patch(
            socket.socket,
            "connect_ex",
            guarded_connect("connect_ex", socket.socket.connect_ex),
        )
        patch(socket.socket, "sendto", guarded_sendto(socket.socket.sendto))
        patch(socket.socket, "sendmsg", guarded_sendmsg(socket.socket.sendmsg))
        try:
            yield
        finally:
            for target, name, original in reversed(patches):
                setattr(target, name, original)

    def run_self_probes(self) -> None:
        """Exercise each boundary without permitting any remote resolution or packet."""
        self._expect_block("blocked_dns", self._probe_dns)
        self._expect_block("blocked_tcp", self._probe_tcp)
        self._expect_block("blocked_udp", self._probe_udp)
        self._expect_allow("allowed_ipv4_loopback", self._probe_ipv4_loopback)
        self._expect_allow("allowed_ipv6_loopback", self._probe_ipv6_loopback)

    def write_report(self, *, pytest_exit_status: int) -> None:
        """Write the stable local-control egress report, creating its parent safely."""
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": self._report_status(pytest_exit_status),
            "policies": {
                "dns": "numeric loopback addresses only",
                "socket": "numeric loopback addresses and AF_UNIX only",
                "telemetry": "disabled for local-control E2E",
                "twin_database": "temporary path required for local-control E2E",
            },
            "self_probes": self.self_probes,
            "blocked_violation_count": self.blocked_violation_count,
            "allowed_loopback_attempt_count": self.allowed_loopback_attempt_count,
            "pytest_exit_status": pytest_exit_status,
        }
        self.report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def has_failures(self) -> bool:
        """Return whether a probe failed or a test caught an unexpected violation."""
        return self.blocked_violation_count > 0 or any(
            result != "pass" for result in self.self_probes.values()
        )

    def record_startup_failure(self, error: BaseException) -> None:
        """Record a startup exception before the session exits."""
        self.self_probes["startup"] = f"fail: {type(error).__name__}"

    def _report_status(self, pytest_exit_status: int) -> str:
        if pytest_exit_status == 0 and not self.has_failures():
            return "pass"
        return "fail"

    def _expect_block(self, name: str, probe: Callable[[], None]) -> None:
        try:
            with self.installed(probe=True):
                probe()
        except EgressViolation:
            self.self_probes[name] = "pass"
        except OSError:
            self.self_probes[name] = "fail"
        else:
            self.self_probes[name] = "fail"

    def _expect_allow(self, name: str, probe: Callable[[], None]) -> None:
        try:
            with self.installed(probe=True):
                probe()
        except (EgressViolation, OSError):
            self.self_probes[name] = "fail"
        else:
            self.self_probes[name] = "pass"

    @staticmethod
    def _probe_dns() -> None:
        socket.getaddrinfo("bridge.oigpower.cz", 5710)

    @staticmethod
    def _probe_tcp() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(("192.168.1.10", 5710))
        finally:
            sock.close()

    @staticmethod
    def _probe_udp() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(b"x", ("8.8.8.8", 53))
        finally:
            sock.close()

    @staticmethod
    def _probe_ipv4_loopback() -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client.connect(listener.getsockname())
        finally:
            client.close()
            listener.close()

    @staticmethod
    def _probe_ipv6_loopback() -> None:
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            listener.bind(("::1", 0))
            listener.listen(1)
            client.connect(listener.getsockname())
        finally:
            client.close()
            listener.close()

    def _require_loopback(self, host: object, method: str, probe: bool) -> None:
        if _loopback_address(host):
            self.allowed_loopback_attempt_count += 1
            return
        self._block(method, host, probe)

    def _require_socket_address(
        self,
        address: object,
        method: str,
        probe: bool,
        sock_obj: socket.socket | None,
    ) -> None:
        if self._is_socket_address(address, sock_obj):
            self.allowed_loopback_attempt_count += 1
            return
        self._block(method, address, probe)

    @staticmethod
    def _is_socket_address(address: object, sock_obj: socket.socket | None) -> bool:
        if sock_obj is not None and sock_obj.family == socket.AF_UNIX:
            return True
        if isinstance(address, tuple) and address:
            return _loopback_address(address[0])
        return False

    def _validated_nameinfo(
        self, sockaddr: object, flags: object, probe: bool
    ) -> tuple[str, int]:
        if not isinstance(flags, int):
            raise TypeError("getnameinfo flags must be an integer")
        if flags < 0 or flags & ~_NAMEINFO_FLAG_MASK:
            raise socket.gaierror(socket.EAI_BADFLAGS, "invalid getnameinfo flags")
        if flags & socket.NI_NAMEREQD:
            raise socket.gaierror(socket.EAI_NONAME, "numeric host name is required")
        if not isinstance(sockaddr, tuple) or not sockaddr:
            raise TypeError("getnameinfo sockaddr must be a non-empty tuple")
        host = sockaddr[0]
        if not isinstance(host, str):
            raise TypeError("getnameinfo host must be a string")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            self._block("getnameinfo", sockaddr, probe)
        if address.version == 4 and len(sockaddr) != 2:
            raise TypeError("getnameinfo sockaddr has an invalid address family shape")
        if address.version == 6 and len(sockaddr) not in (2, 3, 4):
            raise TypeError("getnameinfo sockaddr has an invalid address family shape")
        port = sockaddr[1]
        if not isinstance(port, int):
            raise TypeError("getnameinfo port must be an integer")
        if port < 0 or port > 65535:
            raise socket.gaierror(socket.EAI_SERVICE, "invalid numeric service")
        if address.version == 6:
            flowinfo = sockaddr[2] if len(sockaddr) >= 3 else 0
            scope_id = sockaddr[3] if len(sockaddr) == 4 else 0
            self._validated_unsigned(flowinfo, "flowinfo", maximum=0xFFFFF)
            self._validated_unsigned(scope_id, "scope_id", maximum=0xFFFFFFFF)
        return host, port

    @staticmethod
    def _validated_unsigned(value: object, name: str, *, maximum: int) -> int:
        if not isinstance(value, int):
            raise TypeError(f"getnameinfo {name} must be an integer")
        if value < 0 or value > maximum:
            raise OverflowError(f"getnameinfo {name} is outside unsigned integer range")
        return value

    @staticmethod
    def _peer_address(sock_obj: socket.socket) -> object:
        try:
            return sock_obj.getpeername()
        except OSError:
            return None

    def _block(
        self, method: str, target: object, probe: bool, message: str | None = None
    ) -> None:
        if probe:
            self.probe_violation_count += 1
        else:
            self.blocked_violation_count += 1
        raise EgressViolation(message or f"blocked non-loopback egress via {method}: {target!r}")
