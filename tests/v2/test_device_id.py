"""Testy pro device_id.py — DeviceIdManager."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

# sys.path nastaven v conftest.py
from device_id import DeviceIdManager


class TestDeviceIdManager:
    """Testy pro DeviceIdManager."""

    def test_load_returns_none_when_file_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            manager = DeviceIdManager(path)
            result = manager.load()
            assert result is None
            assert manager.device_id is None

    def test_load_returns_none_when_file_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            with open(path, "w") as f:
                f.write("not valid json")
            manager = DeviceIdManager(path)
            result = manager.load()
            assert result is None
            assert manager.device_id is None

    def test_load_returns_none_when_device_id_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            with open(path, "w") as f:
                json.dump({"first_seen": "2024-01-01T00:00:00+00:00"}, f)
            manager = DeviceIdManager(path)
            result = manager.load()
            assert result is None
            assert manager.device_id is None

    def test_load_returns_device_id_and_sets_property(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            with open(path, "w") as f:
                json.dump({"device_id": "12345", "first_seen": "2024-01-01T00:00:00+00:00"}, f)
            manager = DeviceIdManager(path)
            result = manager.load()
            assert result == "12345"
            assert manager.device_id == "12345"

    def test_save_creates_file_with_device_id_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            manager = DeviceIdManager(path)
            manager.save("12345")
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert data["device_id"] == "12345"
            assert "first_seen" in data
            assert manager.device_id == "12345"

    def test_save_creates_directory_if_not_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "subdir", "device_id.json")
            manager = DeviceIdManager(path)
            manager.save("12345")
            assert os.path.exists(path)

    def test_validate_returns_false_when_no_device_id_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            manager = DeviceIdManager(path)
            assert manager.validate("12345") is False

    def test_validate_returns_true_when_matching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            with open(path, "w") as f:
                json.dump({"device_id": "12345", "first_seen": "2024-01-01T00:00:00+00:00"}, f)
            manager = DeviceIdManager(path)
            manager.load()
            assert manager.validate("12345") is True

    def test_validate_returns_false_when_not_matching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            with open(path, "w") as f:
                json.dump({"device_id": "12345", "first_seen": "2024-01-01T00:00:00+00:00"}, f)
            manager = DeviceIdManager(path)
            manager.load()
            assert manager.validate("67890") is False

    def test_device_id_property_returns_none_when_not_set(self):
        manager = DeviceIdManager()
        assert manager.device_id is None

    def test_device_id_property_returns_loaded_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            with open(path, "w") as f:
                json.dump({"device_id": "12345", "first_seen": "2024-01-01T00:00:00+00:00"}, f)
            manager = DeviceIdManager(path)
            manager.load()
            assert manager.device_id == "12345"

    def test_device_id_property_returns_saved_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            manager = DeviceIdManager(path)
            manager.save("12345")
            assert manager.device_id == "12345"

    def test_any_string_is_valid_device_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "device_id.json")
            manager = DeviceIdManager(path)
            manager.save("any-string-is-valid")
            manager.load()
            assert manager.validate("any-string-is-valid") is True
            assert manager.validate("different-string") is False