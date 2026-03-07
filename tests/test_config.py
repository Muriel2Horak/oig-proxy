# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import importlib
import importlib.util

import pytest

config = importlib.import_module("config")


def test_get_int_env_parsing(monkeypatch):
    monkeypatch.setenv("TEST_INT", "bad")
    assert config._get_int_env("TEST_INT", 5) == 5

    monkeypatch.setenv("TEST_INT", "null")
    assert config._get_int_env("TEST_INT", 5) == 5

    monkeypatch.setenv("TEST_INT", "")
    assert config._get_int_env("TEST_INT", 5) == 5

    monkeypatch.delenv("TEST_INT", raising=False)
    assert config._get_int_env("TEST_INT", 5) == 5

    monkeypatch.setenv("TEST_INT", "7")
    assert config._get_int_env("TEST_INT", 5) == 7


def test_get_float_env_parsing(monkeypatch):
    monkeypatch.setenv("TEST_FLOAT", "bad")
    assert config._get_float_env("TEST_FLOAT", 1.5) == pytest.approx(1.5)

    monkeypatch.setenv("TEST_FLOAT", "null")
    assert config._get_float_env("TEST_FLOAT", 1.5) == pytest.approx(1.5)

    monkeypatch.setenv("TEST_FLOAT", "")
    assert config._get_float_env("TEST_FLOAT", 1.5) == pytest.approx(1.5)

    monkeypatch.delenv("TEST_FLOAT", raising=False)
    assert config._get_float_env("TEST_FLOAT", 1.5) == pytest.approx(1.5)

    monkeypatch.setenv("TEST_FLOAT", "2.5")
    assert config._get_float_env("TEST_FLOAT", 1.5) == pytest.approx(2.5)


def test_mqtt_available_handles_missing_module(monkeypatch):
    def _raise_module_not_found(_name: str):
        raise ModuleNotFoundError("missing")

    monkeypatch.setattr(importlib.util, "find_spec", _raise_module_not_found)
    reloaded = importlib.reload(config)
    assert reloaded.MQTT_AVAILABLE is False


@pytest.mark.parametrize(
    ("tbl_name", "tbl_item", "raw", "expected"),
    [
        ("tbl_box_prms", "MODE", "3", ("3", "3")),
        ("tbl_box_prms", "MODE", 2, ("2", "2")),
        ("tbl_box_prms", "MODE", "9", (None, "bad_value")),
        ("tbl_invertor_prm1", "AAC_MAX_CHRG", "50", ("50.0", "50.0")),
        ("tbl_invertor_prm1", "AAC_MAX_CHRG", 42.2, ("42.2", "42.2")),
        ("tbl_invertor_prm1", "AAC_MAX_CHRG", "x", (None, "bad_value")),
    ],
)
def test_control_parity_contract_normalization(tbl_name, tbl_item, raw, expected):
    assert config.normalize_control_value(tbl_name, tbl_item, raw) == expected


def test_control_parity_contract_whitelist_matches_contract_keys():
    assert set(config.CONTROL_WRITE_WHITELIST.keys()) == set(config.CONTROL_WRITE_PARITY_CONTRACT.keys())
    for tbl_name, items in config.CONTROL_WRITE_PARITY_CONTRACT.items():
        assert config.CONTROL_WRITE_WHITELIST[tbl_name] == set(items.keys())
