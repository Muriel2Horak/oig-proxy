"""Tests for control value normalization."""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable,broad-exception-caught

import proxy as proxy_module
from unittest.mock import MagicMock
from models import ProxyMode


def _make_proxy():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    return proxy


def test_control_normalize_mode_ok():
    proxy = _make_proxy()
    value, canon = proxy._control_normalize_value(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="2",
    )
    assert value == "2"
    assert canon == "2"


def test_control_normalize_mode_bad():
    proxy = _make_proxy()
    value, err = proxy._control_normalize_value(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="9",
    )
    assert value is None
    assert err == "bad_value"


def test_control_normalize_invertor_values():
    proxy = _make_proxy()
    value, canon = proxy._control_normalize_value(
        tbl_name="tbl_invertor_prm1",
        tbl_item="AAC_MAX_CHRG",
        new_value="50",
    )
    assert value == "50.0"
    assert canon == "50.0"


def test_control_normalize_invertor_bad():
    proxy = _make_proxy()
    value, err = proxy._control_normalize_value(
        tbl_name="tbl_invertor_prm1",
        tbl_item="AAC_MAX_CHRG",
        new_value="bad",
    )
    assert value is None
    assert err == "bad_value"


def test_control_normalize_default():
    proxy = _make_proxy()
    value, canon = proxy._control_normalize_value(
        tbl_name="tbl_box_prms",
        tbl_item="SA",
        new_value="1",
    )
    assert value == "1"
    assert canon == "1"
