# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import pytest

from control_settings import ControlSettings


class TestParseSettingEvent:
    """Tests for ControlSettings.parse_setting_event()"""

    @pytest.mark.parametrize(
        "content,expected",
        [
            # MODE parameter (tbl_box_prms)
            (
                "Remotely : tbl_box_prms / MODE: [3]->[0]",
                ("tbl_box_prms", "MODE", "3", "0"),
            ),
            # MANUAL parameter (tbl_boiler_prms)
            (
                "Remotely : tbl_boiler_prms / MANUAL: [0]->[1]",
                ("tbl_boiler_prms", "MANUAL", "0", "1"),
            ),
            # Same value change
            (
                "Remotely : tbl_box_prms / MODE: [0]->[0]",
                ("tbl_box_prms", "MODE", "0", "0"),
            ),
            # Different table and parameter
            (
                "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]",
                ("tbl_invertor_prm1", "AAC_MAX_CHRG", "50.0", "120.0"),
            ),
            # Spaces around table name
            (
                "Remotely :  tbl_box_prms  /  MODE: [1]->[2]",
                ("tbl_box_prms", "MODE", "1", "2"),
            ),
            # Numeric values
            (
                "Remotely : tbl_box_prms / MODE: [100]->[0]",
                ("tbl_box_prms", "MODE", "100", "0"),
            ),
            # Float values
            (
                "Remotely : tbl_temp_prms / TEMP_SET: [25.5]->[26.0]",
                ("tbl_temp_prms", "TEMP_SET", "25.5", "26.0"),
            ),
        ],
    )
    def test_valid_setting_events(self, content, expected):
        """Test valid setting event strings are parsed correctly."""
        result = ControlSettings.parse_setting_event(content)
        assert result == expected

    @pytest.mark.parametrize(
        "content",
        [
            # Not a setting event
            "Forecast Load OK",
            "BOX connected",
            "Some random message",
            "",
            # Missing parts
            "Remotely : tbl_box_prms",
            "tbl_box_prms / MODE: [1]",
            "MODE: [1]->[2]",
            # Invalid format
            "tbl_box_prms MODE: [1]->[2]",
            "Remotely : / MODE: [1]->[2]",
            "Remotely : tbl_box_prms / : [1]->[2]",
            # Missing arrow
            "Remotely : tbl_box_prms / MODE: [3]",
            "Remotely : tbl_box_prms / MODE: [3] to [0]",
            # Missing brackets
            "Remotely : tbl_box_prms / MODE: 3->[0]",
            "Remotely : tbl_box_prms / MODE: [3]->0",
        ],
    )
    def test_invalid_setting_events(self, content):
        """Test invalid setting event strings return None."""
        result = ControlSettings.parse_setting_event(content)
        assert result is None

    @pytest.mark.parametrize(
        "content,expected",
        [
            # Empty brackets are valid (regex accepts them)
            (
                "Remotely : tbl_box_prms / MODE: []->[]",
                ("tbl_box_prms", "MODE", "", ""),
            ),
            (
                "Remotely : tbl_box_prms / MODE: [1]->[]",
                ("tbl_box_prms", "MODE", "1", ""),
            ),
            (
                "Remotely : tbl_box_prms / MODE: []->[2]",
                ("tbl_box_prms", "MODE", "", "2"),
            ),
        ],
    )
    def test_empty_brackets_accepted(self, content, expected):
        """Test that empty brackets are accepted (return tuple with empty strings)."""
        result = ControlSettings.parse_setting_event(content)
        assert result == expected
