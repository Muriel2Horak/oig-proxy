"""Public proxy runtime interfaces."""

# pylint: disable=undefined-all-variable

from __future__ import annotations

from typing import Any

__all__ = [
    "ProxyConnectionContext",
    "ProxyServer",
    "StreamClosedEvent",
    "StreamFrameEvent",
    "StreamTimeoutEvent",
    "StreamTimeoutKind",
]


def __getattr__(name: str) -> Any:
    """Load server interfaces lazily to keep package imports acyclic."""
    if name not in __all__:
        raise AttributeError(name)
    from . import server

    return getattr(server, name)
