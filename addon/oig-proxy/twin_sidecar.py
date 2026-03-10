from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any


@dataclass
class _Health:
    status: str


class _TwinSidecar:
    def __init__(self) -> None:
        self._active = False
        self._prerequisites_met = True
        self._failed = False
        self._queue: Queue[dict[str, Any]] = Queue()

    def start(self) -> bool:
        self._active = True
        self._failed = False
        return True

    def stop(self) -> bool:
        self._active = False
        return True

    def send(self, payload: dict[str, Any]) -> bool:
        self._queue.put(payload)
        return True

    def receive(self, timeout: float = 0.0) -> dict[str, Any] | None:
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return {"type": "heartbeat"}

    def get_queue_size(self) -> int:
        return self._queue.qsize()

    def activate_on_startup(self) -> None:
        self._active = True

    def is_active(self) -> bool:
        return self._active

    def check_activation_guards(self) -> bool:
        return self._prerequisites_met

    def set_prerequisites_met(self, value: bool) -> None:
        self._prerequisites_met = value

    def health_check(self) -> _Health:
        return _Health(status="healthy" if not self._failed else "unhealthy")

    def simulate_failure(self) -> None:
        self._failed = True

    def restart(self) -> bool:
        self._failed = False
        self._active = True
        return True


class TwinSidecarFactory:
    def create(self) -> _TwinSidecar:
        return _TwinSidecar()
