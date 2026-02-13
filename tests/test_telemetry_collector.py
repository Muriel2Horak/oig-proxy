# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring
# pylint: disable=protected-access,unused-argument
import json
import os
from unittest.mock import patch

from telemetry_collector import TelemetryCollector


class TestLoadVersionFromConfig:
    """Unit tests for _load_version_from_config()."""

    def test_loads_version_from_config_json(self, tmp_path):
        """Test that version is loaded from config.json when present."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"version": "1.2.3"}))
        with patch("os.path.dirname", return_value=str(tmp_path)):
            version = TelemetryCollector._load_version_from_config()
        assert version == "1.2.3"

    def test_returns_unknown_when_all_sources_fail(self, tmp_path):
        """Test that 'unknown' is returned when both config and package metadata fail."""
        with patch("os.path.dirname", return_value=str(tmp_path)):
            version = TelemetryCollector._load_version_from_config()
        assert version == "unknown"

    def test_handles_invalid_config_json(self, tmp_path):
        """Test fallback when config.json contains invalid JSON."""
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json")
        with patch("os.path.dirname", return_value=str(tmp_path)):
            version = TelemetryCollector._load_version_from_config()
        assert version == "unknown"

    def test_handles_config_without_version_key(self, tmp_path):
        """Test fallback when config.json exists but has no version key."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"name": "test"}))
        with patch("os.path.dirname", return_value=str(tmp_path)):
            version = TelemetryCollector._load_version_from_config()
        assert version == "unknown"
