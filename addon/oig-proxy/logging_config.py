#!/usr/bin/env python3
"""Konfigurace logování pro OIG Proxy v2.

Úrovně:
- INFO: standard logging INFO; log parsed XML + raw hex of each frame
- DEBUG: logging DEBUG; plus full TCP dump (each chunk before assembly)
- TRACE: custom level 5; plus routing decisions, Twin queue state, cloud health
"""

from __future__ import annotations

import logging
import sys

# Custom TRACE level (lower than DEBUG which is 10)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def configure_logging(level: str) -> None:
    """Nakonfiguruje logování podle úrovně.

    Args:
        level: Úroveň logování - "INFO", "DEBUG" nebo "TRACE"
    """
    # Normalize level name
    level_upper = level.upper()

    # Determine the logging level
    if level_upper == "TRACE":
        log_level = TRACE
    elif level_upper == "DEBUG":
        log_level = logging.DEBUG
    else:
        # Default to INFO for any other value
        log_level = logging.INFO

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    # Set all loggers to the specified level
    logging.getLogger().setLevel(log_level)