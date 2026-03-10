#!/usr/bin/env python3

import logging
import sys
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon/oig-proxy"))

import correlation_id as cid_mod  # type: ignore[import-not-found]


@pytest.fixture(autouse=True)
def clear_context() -> Iterator[None]:
    """Ensure correlation-id context is clean between tests."""
    cid_mod.clear_correlation_id()
    yield
    cid_mod.clear_correlation_id()


def test_generate_correlation_id_format_and_uniqueness() -> None:
    first = cid_mod.generate_correlation_id()
    second = cid_mod.generate_correlation_id()

    assert first.startswith("oig_")
    p = first.split("_")
    assert len(p) == 3
    assert p[0] == "oig"
    assert p[1].isdigit()
    assert len(p[2]) == 8
    assert first != second


def test_generate_short_correlation_id_format() -> None:
    corr_id = cid_mod.generate_short_correlation_id()
    p = corr_id.split("_")
    assert corr_id.startswith("oig_")
    assert len(p) == 2
    assert p[0] == "oig"
    assert len(p[1]) == 8


def test_get_optional_set_clear_and_get_auto_generate(caplog: pytest.LogCaptureFixture) -> None:
    assert cid_mod.get_correlation_id_optional() is None

    cid_mod.set_correlation_id("oig_manual")
    assert cid_mod.get_correlation_id_optional() == "oig_manual"
    assert cid_mod.get_correlation_id() == "oig_manual"

    cid_mod.set_correlation_id(None)
    assert cid_mod.get_correlation_id_optional() is None

    with caplog.at_level(logging.DEBUG):
        generated = cid_mod.get_correlation_id()

    assert generated.startswith("oig_")
    assert cid_mod.get_correlation_id_optional() == generated
    assert "Auto-generated correlation ID" in caplog.text

    cid_mod.clear_correlation_id()
    assert cid_mod.get_correlation_id_optional() is None


def test_correlation_id_context_restores_previous_value_even_on_exception() -> None:
    cid_mod.set_correlation_id("oig_outer")

    with pytest.raises(RuntimeError, match="boom"):
        with cid_mod.correlation_id_context("oig_inner") as active:
            assert active == "oig_inner"
            assert cid_mod.get_correlation_id() == "oig_inner"
            raise RuntimeError("boom")

    assert cid_mod.get_correlation_id_optional() == "oig_outer"


def test_correlation_id_context_auto_generates_when_none_provided() -> None:
    with cid_mod.correlation_id_context() as active:
        assert active.startswith("oig_")
        assert cid_mod.get_correlation_id() == active


def test_correlation_id_context_frame_bytes_str_and_none_paths() -> None:
    with cid_mod.correlation_id_context_frame(b"abc") as corr_id_bytes:
        parts = corr_id_bytes.split("_")
        assert parts[0] == "oig"
        assert parts[1].isdigit()
        assert len(parts[2]) == 8

    with cid_mod.correlation_id_context_frame("xyz") as corr_id_str:
        parts = corr_id_str.split("_")
        assert parts[0] == "oig"
        assert parts[1].isdigit()
        assert len(parts[2]) == 8

    with cid_mod.correlation_id_context_frame(None) as corr_id_none:
        assert corr_id_none.startswith("oig_")


def test_correlation_id_context_frame_falls_back_on_frame_processing_error() -> None:
    class BadFrame:
        def __str__(self) -> str:
            raise ValueError("cannot stringify")

    with cid_mod.correlation_id_context_frame(BadFrame()) as active:
        assert active.startswith("oig_")


def test_with_correlation_id_decorator_generates_resets_and_passthrough() -> None:
    observed: list[str] = []

    @cid_mod.with_correlation_id
    def wrapped(value: int) -> int:
        observed.append(cid_mod.get_correlation_id())
        return value + 1

    result = wrapped(5)
    assert result == 6
    assert observed[0].startswith("oig_")
    assert cid_mod.get_correlation_id_optional() is None

    cid_mod.set_correlation_id("oig_existing")
    assert wrapped(1) == 2
    assert observed[1] == "oig_existing"
    assert cid_mod.get_correlation_id_optional() == "oig_existing"


def test_with_correlation_id_decorator_resets_context_after_exception() -> None:
    @cid_mod.with_correlation_id
    def wrapped() -> None:
        raise ValueError("fail")

    with pytest.raises(ValueError, match="fail"):
        wrapped()

    assert cid_mod.get_correlation_id_optional() is None


def test_log_with_correlation_id_and_format_log_message(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        extra = {"scope": "test"}
        cid_mod.log_with_correlation_id("hello", level=logging.INFO, extra=extra)

    assert extra["scope"] == "test"
    assert "correlation_id" in extra
    assert any(r.message == "hello" for r in caplog.records)
    assert all(hasattr(r, "correlation_id") for r in caplog.records)

    formatted_explicit = cid_mod.format_log_message("msg", "oig_explicit")
    assert formatted_explicit == "[oig_explicit] msg"

    cid_mod.set_correlation_id("oig_from_context")
    formatted_implicit = cid_mod.format_log_message("msg")
    assert formatted_implicit == "[oig_from_context] msg"


def test_correlation_id_mixin_property_setter_and_logging(caplog: pytest.LogCaptureFixture) -> None:
    class Component(cid_mod.CorrelationIdMixin):
        pass

    instance = Component()

    cid_mod.set_correlation_id("oig_global")
    assert instance.correlation_id == "oig_global"

    instance.correlation_id = "oig_instance"
    assert instance.correlation_id == "oig_instance"

    with caplog.at_level(logging.WARNING):
        instance.log_with_cid("warn", level=logging.WARNING)

    assert any(r.message == "[oig_instance] warn" for r in caplog.records)


def test_propagate_and_extract_correlation_id_dict_helpers() -> None:
    payload = {"k": "v"}
    augmented = cid_mod.propagate_correlation_id_to_dict(payload, "oig_given")

    assert augmented == {"k": "v", "correlation_id": "oig_given"}
    assert payload == {"k": "v"}  # input dict unchanged

    cid_mod.set_correlation_id("oig_context")
    augmented_from_context = cid_mod.propagate_correlation_id_to_dict(payload)
    assert augmented_from_context["correlation_id"] == "oig_context"

    assert cid_mod.extract_correlation_id_from_dict(augmented) == "oig_given"
    assert cid_mod.extract_correlation_id_from_dict({}, default="fallback") == "fallback"
