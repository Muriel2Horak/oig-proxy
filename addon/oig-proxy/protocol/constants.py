"""Shared OIG protocol constants."""

from __future__ import annotations

# Keys that are transport/metadata only (never publishable telemetry payload).
# Shared by the frame processor and the proxy server so the two filters cannot
# silently diverge.
TRANSPORT_METADATA_KEYS = frozenset(
    {
        "Confirm",
        "ID",
        "ID_Server",
        "NewValue",
        "Rdt",
        "Result",
        "TSec",
        "TblItem",
        "Tmr",
        "ToDo",
        "mytimediff",
    }
)
