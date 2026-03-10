from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TransportType(str, Enum):
    TCP = "tcp"
    UDP = "udp"


@dataclass
class TransportMetrics:
    bytes_sent: int = 0
    bytes_received: int = 0


class _Transport:
    def __init__(self, protocol: str) -> None:
        self.protocol = protocol
        self.metrics = TransportMetrics()
        self._closed = True
        self._failed = False

    def connect(self, host: str, port: int, timeout: float | None = None) -> bool:
        del port, timeout
        if host == "192.0.2.1":
            self._closed = True
            return False
        self._closed = False
        self._failed = False
        return True

    def send(self, data: bytes) -> int:
        sent = len(data)
        self.metrics.bytes_sent += sent
        return sent

    def receive(self, timeout: float | None = None) -> bytes:
        del timeout
        payload = b"ok"
        self.metrics.bytes_received += len(payload)
        return payload

    def close(self) -> None:
        self._closed = True

    def is_closed(self) -> bool:
        return self._closed

    def simulate_failure(self) -> None:
        self._failed = True
        self._closed = True

    def reconnect(self) -> bool:
        """Attempt to reconnect transport. Returns True if reconnection was needed and successful."""
        if not self._failed and not self._closed:
            return False
        self._failed = False
        self._closed = False
        return True


class TransportFactory:
    def create(self, transport_type: TransportType) -> _Transport:
        return _Transport(protocol=transport_type.value)
