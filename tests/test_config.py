import pytest

import config


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
