"""Tests for Twin state model and queue.

Tests verify:
- TwinSetting dataclass creation
- TwinQueue enqueue/acknowledge/size operations
- Overwrite behavior for same (table, key)
- get_pending returns sorted results

Run: PYTHONPATH=addon/oig-proxy pytest tests/v2/test_twin_state.py -v
"""

# pyright: reportMissingImports=false

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

import time

import pytest

from twin.state import TwinQueue, TwinSetting


class TestTwinSetting:
    """Tests for TwinSetting dataclass."""

    def test_twin_setting_creation(self):
        """Test creating a TwinSetting instance."""
        setting = TwinSetting(
            table="tbl_set",
            key="T_Room",
            value=22,
            enqueued_at=time.time(),
        )
        assert setting.table == "tbl_set"
        assert setting.key == "T_Room"
        assert setting.value == 22
        assert isinstance(setting.enqueued_at, float)

    def test_twin_setting_with_different_types(self):
        """Test TwinSetting accepts various value types."""
        # Integer value
        s1 = TwinSetting("tbl_set", "T_Room", 22, time.time())
        assert s1.value == 22

        # String value
        s2 = TwinSetting("tbl_set", "T_Mode", "AUTO", time.time())
        assert s2.value == "AUTO"

        # Float value
        s3 = TwinSetting("tbl_set", "T_Target", 21.5, time.time())
        assert s3.value == 21.5

        # Boolean value
        s4 = TwinSetting("tbl_set", "T_Enable", True, time.time())
        assert s4.value is True


class TestTwinQueue:
    """Tests for TwinQueue class."""

    def test_empty_queue(self):
        """Test empty queue has size 0."""
        queue = TwinQueue()
        assert queue.size() == 0
        assert queue.get_pending() == []

    def test_enqueue_increases_size(self):
        """Test enqueue adds setting to queue."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)
        assert queue.size() == 1

    def test_acknowledge_decreases_size(self):
        """Test acknowledge removes setting from queue."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)
        assert queue.size() == 1

        result = queue.acknowledge("tbl_set", "T_Room")
        assert result is True
        assert queue.size() == 0

    def test_acknowledge_nonexistent_returns_false(self):
        """Test acknowledging non-existent setting returns False."""
        queue = TwinQueue()
        result = queue.acknowledge("tbl_set", "T_Room")
        assert result is False

    def test_enqueue_overwrites_same_key(self):
        """Test second enqueue of same (table, key) overwrites value."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)
        assert queue.size() == 1

        queue.enqueue("tbl_set", "T_Room", 25)
        assert queue.size() == 1  # Still 1, not 2

        setting = queue.get("tbl_set", "T_Room")
        assert setting is not None
        assert setting.value == 25  # New value

    def test_get_pending_returns_sorted_list(self):
        """Test get_pending returns settings sorted by enqueued_at."""
        queue = TwinQueue()

        # Enqueue in reverse order
        queue.enqueue("tbl_set", "T_Room", 22)
        time.sleep(0.01)  # Small delay to ensure different timestamps
        queue.enqueue("tbl_set", "T_Target", 21)
        time.sleep(0.01)
        queue.enqueue("tbl_set", "T_Mode", "AUTO")

        pending = queue.get_pending()
        assert len(pending) == 3

        # Should be sorted by enqueued_at (oldest first)
        assert pending[0].key == "T_Room"
        assert pending[1].key == "T_Target"
        assert pending[2].key == "T_Mode"

    def test_get_pending_returns_twin_setting_objects(self):
        """Test get_pending returns list of TwinSetting objects."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)

        pending = queue.get_pending()
        assert len(pending) == 1
        assert isinstance(pending[0], TwinSetting)
        assert pending[0].table == "tbl_set"
        assert pending[0].key == "T_Room"
        assert pending[0].value == 22

    def test_clear_removes_all_settings(self):
        """Test clear removes all settings from queue."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)
        queue.enqueue("tbl_set", "T_Target", 21)
        assert queue.size() == 2

        queue.clear()
        assert queue.size() == 0
        assert queue.get_pending() == []

    def test_get_returns_none_for_missing(self):
        """Test get returns None for non-existent setting."""
        queue = TwinQueue()
        result = queue.get("tbl_set", "T_Room")
        assert result is None

    def test_get_returns_setting_for_existing(self):
        """Test get returns TwinSetting for existing key."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)

        result = queue.get("tbl_set", "T_Room")
        assert result is not None
        assert isinstance(result, TwinSetting)
        assert result.value == 22

    def test_different_tables_same_key(self):
        """Test same key in different tables are separate entries."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)
        queue.enqueue("tbl_box_prms", "T_Room", 25)

        assert queue.size() == 2

        set_setting = queue.get("tbl_set", "T_Room")
        box_setting = queue.get("tbl_box_prms", "T_Room")

        assert set_setting.value == 22
        assert box_setting.value == 25

    def test_multiple_acknowledges(self):
        """Test acknowledging multiple settings."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)
        queue.enqueue("tbl_set", "T_Target", 21)
        queue.enqueue("tbl_set", "T_Mode", "AUTO")
        assert queue.size() == 3

        queue.acknowledge("tbl_set", "T_Room")
        assert queue.size() == 2

        queue.acknowledge("tbl_set", "T_Target")
        assert queue.size() == 1

        queue.acknowledge("tbl_set", "T_Mode")
        assert queue.size() == 0

    def test_overwrite_updates_timestamp(self):
        """Test overwrite updates the enqueued_at timestamp."""
        queue = TwinQueue()

        queue.enqueue("tbl_set", "T_Room", 22)
        first = queue.get("tbl_set", "T_Room")
        first_time = first.enqueued_at

        time.sleep(0.01)
        queue.enqueue("tbl_set", "T_Room", 25)
        second = queue.get("tbl_set", "T_Room")
        second_time = second.enqueued_at

        assert second_time > first_time


class TestTwinQueueEdgeCases:
    """Edge case tests for TwinQueue."""

    def test_empty_string_keys(self):
        """Test queue handles empty string keys."""
        queue = TwinQueue()
        queue.enqueue("", "", "value")
        assert queue.size() == 1
        assert queue.get("", "").value == "value"

    def test_none_value(self):
        """Test queue handles None value."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", None)
        assert queue.get("tbl_set", "T_Room").value is None

    def test_complex_value_types(self):
        """Test queue handles complex value types."""
        queue = TwinQueue()

        # List value
        queue.enqueue("tbl_set", "T_List", [1, 2, 3])
        assert queue.get("tbl_set", "T_List").value == [1, 2, 3]

        # Dict value
        queue.enqueue("tbl_set", "T_Dict", {"a": 1, "b": 2})
        assert queue.get("tbl_set", "T_Dict").value == {"a": 1, "b": 2}

    def test_acknowledge_wrong_table(self):
        """Test acknowledging with wrong table returns False."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)

        result = queue.acknowledge("tbl_box_prms", "T_Room")
        assert result is False
        assert queue.size() == 1

    def test_acknowledge_wrong_key(self):
        """Test acknowledging with wrong key returns False."""
        queue = TwinQueue()
        queue.enqueue("tbl_set", "T_Room", 22)

        result = queue.acknowledge("tbl_set", "T_Target")
        assert result is False
        assert queue.size() == 1
