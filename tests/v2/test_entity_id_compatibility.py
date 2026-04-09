"""Tests for MQTTClient._build_object_id() V1-compatible behavior."""
# pylint: disable=protected-access

import pytest

from mqtt.client import MQTTClient


class TestBuildObjectId:
    """Test _build_object_id produces V1-compatible entity IDs."""

    def test_namespace_prefix_included(self):
        """Namespace should be included in object_id for V1 compatibility."""
        result = MQTTClient._build_object_id(
            device_id="2206237016",
            table="tbl_actual",
            safe_key="aci_wr",
        )
        # V1 format: oig_local_2206237016_tbl_actual_aci_wr
        assert result == "oig_local_2206237016_tbl_actual_aci_wr"

    def test_special_characters_normalized(self):
        """Special characters should be normalized to underscore."""
        result = MQTTClient._build_object_id(
            device_id="device-123",
            table="tbl.sensor",
            safe_key="key@value",
        )
        # All special chars should become underscores
        assert result == "oig_local_device_123_tbl_sensor_key_value"

    def test_multiple_underscores_collapsed(self):
        """Multiple consecutive underscores should be collapsed to one."""
        result = MQTTClient._build_object_id(
            device_id="dev__id",
            table="tbl___test",
            safe_key="key___value",
        )
        # Should not have consecutive underscores
        assert "__" not in result

    def test_leading_trailing_underscores_stripped(self):
        """Leading and trailing underscores should be stripped."""
        result = MQTTClient._build_object_id(
            device_id="_device_id_",
            table="_table_",
            safe_key="_key_",
        )
        # Should not start or end with underscore
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_control_object_id_ends_with_cfg(self):
        """Control entities should end with _cfg suffix."""
        result = MQTTClient._build_object_id(
            device_id="2206237016",
            table="tbl_box_prms",
            safe_key="mode",
            is_control=True,
        )
        assert result == "oig_local_2206237016_tbl_box_prms_mode_cfg"

    def test_non_control_no_cfg_suffix(self):
        """Non-control entities should not have _cfg suffix."""
        result = MQTTClient._build_object_id(
            device_id="2206237016",
            table="tbl_actual",
            safe_key="temp",
            is_control=False,
        )
        assert result == "oig_local_2206237016_tbl_actual_temp"
        assert not result.endswith("_cfg")

    def test_case_normalized_to_lowercase(self):
        """All characters should be lowercase."""
        result = MQTTClient._build_object_id(
            device_id="DeviceID",
            table="TBL_Table",
            safe_key="KeyName",
        )
        assert result == "oig_local_deviceid_tbl_table_keyname"

    def test_v1_example_format(self):
        """Test V1-compatible format example: oig_local_2206237016_tbl_actual_aci_wr."""
        result = MQTTClient._build_object_id(
            device_id="2206237016",
            table="tbl_actual",
            safe_key="aci_wr",
        )
        assert result == "oig_local_2206237016_tbl_actual_aci_wr"
