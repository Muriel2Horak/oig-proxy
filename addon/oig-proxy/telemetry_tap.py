#!/usr/bin/env python3
"""
TelemetryTap – non-blocking telemetry adapter (fire-and-forget).

Isolates telemetry publishing from the critical transport path.
Telemetry failures are logged but never propagate to the caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from enum import Enum
from typing import Any, Callable, Coroutine, TypeVar, cast

from correlation_id import get_correlation_id

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TelemetryTap:
    """
    Adapter that wraps telemetry publish operations with fire-and-forget semantics.

    Guarantees:
    - Telemetry publish never blocks the transport critical path
    - Telemetry exceptions are logged, not raised
    - Transport result is unaffected by telemetry success/failure
    """

    def __init__(
        self,
        *,
        background_tasks: set[asyncio.Task[Any]] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """
        Initialize TelemetryTap.

        Args:
            background_tasks: Optional set to track background tasks (prevents GC)
            loop: Optional event loop to use for scheduling
        """
        self._background_tasks = background_tasks
        self._loop = loop
        self._publish_count = 0
        self._publish_success = 0
        self._publish_failed = 0

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach event loop for task scheduling."""
        self._loop = loop

    def attach_background_tasks(self, tasks: set[asyncio.Task[Any]]) -> None:
        """Attach background tasks set for task tracking."""
        self._background_tasks = tasks

    @property
    def stats(self) -> dict[str, int]:
        """Return telemetry publish statistics."""
        return {
            "total": self._publish_count,
            "success": self._publish_success,
            "failed": self._publish_failed,
        }

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get the event loop, falling back to current running loop."""
        if self._loop is not None:
            return self._loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("No event loop available for TelemetryTap") from exc

    def _track_task(self, task: asyncio.Task[Any]) -> None:
        """Track task in background set to prevent garbage collection."""
        if self._background_tasks is not None:
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    def _on_publish_complete(self, task: asyncio.Task[Any]) -> None:
        """Handle task completion, tracking success/failure."""
        self._publish_count += 1
        try:
            task.result()
            self._publish_success += 1
            logger.debug("TelemetryTap: publish completed successfully")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._publish_failed += 1
            logger.warning("TelemetryTap: publish failed (logged, not raised): %s", exc)

    def publish(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str = "telemetry_publish",
    ) -> None:
        """
        Fire-and-forget telemetry publish.

        Schedules the coroutine as a background task.
        Errors are logged but never raised to the caller.

        Args:
            coro: Coroutine to execute (e.g., telemetry_client.send_telemetry(metrics))
            name: Optional name for the task (used in logging)
        """
        try:
            loop = self._get_loop()
            task = loop.create_task(coro, name=name)
            self._track_task(task)
            task.add_done_callback(self._on_publish_complete)
            cid = get_correlation_id()
            logger.debug("TelemetryTap: scheduled %s (cid=%s)", name, cid)
        except Exception as exc: # pylint: disable=broad-exception-caught
            # Even scheduling failure shouldn't affect transport
            self._publish_failed += 1
            cid = get_correlation_id()
            logger.warning("TelemetryTap: failed to schedule %s (cid=%s): %s", name, cid, exc)

    def publish_sync_wrapper(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Wrap a synchronous telemetry function with fire-and-forget semantics.

        Runs the function in an executor to avoid blocking.

        Args:
            func: Synchronous function to call
            *args: Positional arguments
            **kwargs: Keyword arguments
        """
        async def _wrapper() -> T:
            loop = self._get_loop()
            return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

        self.publish(_wrapper(), name=f"sync_{func.__name__}")

    async def publish_with_result(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        default: T | None = None,
        name: str = "telemetry_publish",
    ) -> T | None:
        """
        Publish telemetry and return result, with error handling.

        Unlike publish(), this awaits the result but still catches exceptions.
        Returns default value on failure.

        Args:
            coro: Coroutine to execute
            default: Default value to return on failure
            name: Optional name for the operation

        Returns:
            Result of coro, or default on failure
        """
        try:
            result = await coro
            self._publish_count += 1
            self._publish_success += 1
            return result
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._publish_count += 1
            self._publish_failed += 1
            logger.warning("TelemetryTap: %s failed (returning default): %s", name, exc)
            return default


class TelemetryTapAdapter:
    """
    Adapter that wraps a TelemetryClient with TelemetryTap semantics.

    Provides the same interface as TelemetryClient but with fire-and-forget
    semantics for all publish operations.
    """

    def __init__(
        self,
        telemetry_client: Any,
        tap: TelemetryTap | None = None,
    ) -> None:
        """
        Initialize adapter.

        Args:
            telemetry_client: The underlying telemetry client to wrap
            tap: Optional TelemetryTap instance (creates one if not provided)
        """
        self._client = telemetry_client
        self._tap = tap or TelemetryTap()

    @property
    def tap(self) -> TelemetryTap:
        """Access the underlying TelemetryTap."""
        return self._tap

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach event loop to the tap."""
        self._tap.attach_loop(loop)

    def attach_background_tasks(self, tasks: set[asyncio.Task[Any]]) -> None:
        """Attach background tasks set."""
        self._tap.attach_background_tasks(tasks)

    def send_telemetry(self, metrics: dict[str, Any]) -> None:
        """
        Fire-and-forget telemetry send.

        Non-blocking, errors logged but not raised.
        """
        if self._client is None:
            return
        self._tap.publish(
            self._client.send_telemetry(metrics),
            name="send_telemetry",
        )

    def send_event(
        self,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Fire-and-forget event send.

        Non-blocking, errors logged but not raised.
        """
        if self._client is None:
            return
        self._tap.publish(
            self._client.send_event(event_type, details),
            name=f"event_{event_type}",
        )

    def __getattr__(self, name: str) -> Callable[..., None]:
        """
        Delegate unknown attributes to the wrapped client with tap semantics.

        For event_* methods, wraps them with fire-and-forget.
        """
        attr = getattr(self._client, name, None)
        if attr is None:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

        if name.startswith("event_") and callable(attr):
            def _wrapped_event(*args: Any, **kwargs: Any) -> None:
                result = attr(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    self._tap.publish(
                        cast(Coroutine[Any, Any, Any], result),
                        name=name,
                    )
            return _wrapped_event

        # For other attributes, return as-is (but this shouldn't be called for methods)
        return attr  # type: ignore[return-value]


class TapType(str, Enum):
    METRICS = "metrics"
    EVENTS = "events"
    TRACES = "traces"


class _MetricsTap:
    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._attached = False

    def record(self, name: str, value: float, labels: dict[str, Any] | None = None) -> None:
        self._entries.append({"name": name, "value": value, "labels": labels or {}})

    def get(self, name: str) -> float | None:
        for entry in reversed(self._entries):
            if entry["name"] == name:
                return float(entry["value"])
        return None

    def filter(
        self,
        *,
        name_pattern: str | None = None,
        labels: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        items = list(self._entries)
        if name_pattern is not None:
            if name_pattern.endswith("*"):
                prefix = name_pattern[:-1]
                items = [item for item in items if str(item["name"]).startswith(prefix)]
            else:
                items = [item for item in items if item["name"] == name_pattern]
        if labels is not None:
            items = [
                item
                for item in items
                if all(item["labels"].get(key) == value for key, value in labels.items())
            ]
        return items

    def export(self, *, format: str = "json") -> str:
        if format == "prometheus":
            lines = [f"{entry['name']} {entry['value']}" for entry in self._entries]
            return "\n".join(lines)
        return json.dumps(self._entries)

    def attach_to_proxy(self) -> None:
        self._attached = True

    def is_attached(self) -> bool:
        return self._attached


class _EventsTap:
    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._attached = False

    def record(self, name: str, payload: dict[str, Any] | None = None) -> None:
        self._events.setdefault(name, []).append(payload or {})

    def get_events(self, name: str) -> list[dict[str, Any]]:
        return self._events.get(name, [])

    def attach_to_proxy(self) -> None:
        self._attached = True
        self.record("proxy_start", {"source": "tap"})

    def is_attached(self) -> bool:
        return self._attached


class _TracesTap:
    def __init__(self) -> None:
        self._traces: list[dict[str, Any]] = []

    @contextmanager
    def trace(self, operation: str):
        self._traces.append({"operation": operation})
        yield

    def get_traces(self) -> list[dict[str, Any]]:
        return list(self._traces)


class TelemetryTapFactory:
    def create(self, tap_type: TapType):
        if tap_type == TapType.METRICS:
            return _MetricsTap()
        if tap_type == TapType.EVENTS:
            return _EventsTap()
        if tap_type == TapType.TRACES:
            return _TracesTap()
        raise ValueError(f"Unsupported tap type: {tap_type}")
