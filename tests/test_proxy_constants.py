"""Tests for constants in oig_frame.py."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import oig_frame


def test_constants_defined():
    """Test all string constants are properly defined in oig_frame."""
    assert oig_frame.RESULT_ACK == "<Result>ACK</Result>"
    assert oig_frame.RESULT_END == "<Result>END</Result>"
