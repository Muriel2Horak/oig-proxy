"""Validate the committed local-setting traceability evidence."""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
TRACE = (
    ROOT
    / "docs/superpowers/reports/"
    "2026-08-06-local-setting-transaction-hardening-traceability.md"
)
ROW = re.compile(r"^\| SI-(\d+) \| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \|$")


def _trace_rows() -> dict[int, tuple[str, str, str]]:
    rows: dict[int, tuple[str, str, str]] = {}
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            rows[int(match.group(1))] = (
                match.group(2), match.group(3), match.group(4)
            )
    return rows


def test_traceability_covers_si_1_through_si_15() -> None:
    """Require one matrix row for every approved safety invariant."""
    assert set(_trace_rows()) == set(range(1, 16))


def test_traceability_uses_unit_integration_and_e2e_nodes() -> None:
    """Require each row to name the required test-layer nodes."""
    for unit, integration, e2e in _trace_rows().values():
        assert unit.startswith("tests/v2/")
        assert integration.startswith("tests/v2/")
        assert e2e.startswith("tests/v2/e2e/")
        assert "::test_" in unit
        assert "::test_" in integration
        assert "::test_e2e_" in e2e


def test_every_traceability_node_exists_as_a_python_test_function() -> None:
    """Reject stale evidence paths and renamed or deleted test nodes."""
    for row in _trace_rows().values():
        for reference in row:
            relative_path, node = reference.split("::", maxsplit=1)
            path = ROOT / relative_path
            assert path.is_file(), reference
            functions = {
                item.name
                for item in ast.walk(
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                )
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert node.split("[", maxsplit=1)[0] in functions, reference
