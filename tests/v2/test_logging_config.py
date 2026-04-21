#!/usr/bin/env python3
"""Testy pro logging_config.py."""

import logging

import pytest

from logging_config import TRACE, configure_logging


class TestConfigureLogging:
    """Testy pro funkci configure_logging."""

    def test_trace_level_registered(self) -> None:
        """Ověří, že TRACE level je správně zaregistrován."""
        assert logging.getLevelName(TRACE) == "TRACE"
        assert logging.getLevelName(5) == "TRACE"
        assert TRACE == 5

    def test_configure_logging_info(self) -> None:
        """Ověří konfiguraci pro INFO úroveň."""
        configure_logging("INFO")
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_configure_logging_info_lowercase(self) -> None:
        """Ověří konfiguraci pro INFO úroveň (lowercase)."""
        configure_logging("info")
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_configure_logging_debug(self) -> None:
        """Ověří konfiguraci pro DEBUG úroveň."""
        configure_logging("DEBUG")
        root_logger = logging.getLogger()
        # DEBUG = 10
        assert root_logger.level <= 10

    def test_configure_logging_trace(self) -> None:
        """Ověří konfiguraci pro TRACE úroveň."""
        configure_logging("TRACE")
        root_logger = logging.getLogger()
        # TRACE = 5
        assert root_logger.level == TRACE
        assert root_logger.level == 5

    def test_configure_logging_trace_lowercase(self) -> None:
        """Ověří konfiguraci pro TRACE úroveň (lowercase)."""
        configure_logging("trace")
        root_logger = logging.getLogger()
        assert root_logger.level == TRACE

    def test_configure_logging_invalid_defaults_to_info(self) -> None:
        """Ověří, že neznámá úroveň defaultuje na INFO."""
        configure_logging("INVALID")
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_configure_logging_empty_defaults_to_info(self) -> None:
        """Ověří, že prázdná úroveň defaultuje na INFO."""
        configure_logging("")
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
