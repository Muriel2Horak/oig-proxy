#!/usr/bin/env python3
"""
Frame builders for OIG protocol – offline ACK and response frames.

Extends protocol.frame with higher-level frame builders for local/offline mode.
"""

from __future__ import annotations

from datetime import datetime, timezone

try:
    from .frame import build_frame, RESULT_ACK, RESULT_END
except ImportError:
    from frame import build_frame, RESULT_ACK, RESULT_END  # type: ignore[no-redef]


def build_ack_only_frame() -> bytes:
    """Sestaví prostý ACK frame.

    Returns:
        ACK frame bytes with CRC and CRLF
    """
    return build_frame(RESULT_ACK).encode("utf-8")


def build_getactual_frame() -> bytes:
    """Sestaví ACK frame s příkazem GetActual.

    Returns:
        ACK frame with ToDo=GetActual command
    """
    inner = f"{RESULT_ACK}<ToDo>GetActual</ToDo>"
    return build_frame(inner).encode("utf-8")


def build_end_time_frame() -> bytes:
    """Sestaví END frame s aktuálním časem (local + UTC) a příkazem GetActual.

    Returns:
        END frame with Time, UTCTime, and ToDo=GetActual
    """
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    inner = (
        f"{RESULT_END}"
        f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
        f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
        "<ToDo>GetActual</ToDo>"
    )
    return build_frame(inner).encode("utf-8")


def build_end_frame_with_timestamp() -> bytes:
    """Sestaví END frame s aktuálním časem (pro IsNewSet odpověď).

    Returns:
        END frame with DT timestamp
    """
    now = datetime.now()
    inner = (
        f"{RESULT_END}"
        f"<DT>{now.strftime('%Y-%m-%d %H:%M:%S')}</DT>"
    )
    return build_frame(inner).encode("utf-8")


def build_end_only_frame() -> bytes:
    """Sestaví prostý END frame bez timestampu.

    Returns:
        Simple END frame (for IsNewWeather, IsNewFW)
    """
    return build_frame(RESULT_END).encode("utf-8")
