"""Fail-closed contract for the independent Cobertura coverage gate."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from ci.check_coverage import (
    CoverageGateError,
    evaluate_coverage,
    result_document,
)


def write_cobertura(
    root: Path,
    *,
    lines: tuple[int, int],
    branches: tuple[int, int],
) -> Path:
    """Write one minimal root-level Cobertura summary."""
    path = root / "coverage.xml"
    path.write_text(
        "<coverage "
        f'lines-covered="{lines[0]}" lines-valid="{lines[1]}" '
        f'branches-covered="{branches[0]}" branches-valid="{branches[1]}"/>'
        "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("lines", "branches", "message"),
    [
        ((80, 100), (80, 100), "statement coverage"),
        ((81, 100), (80, 100), "branch coverage"),
        ((80, 100), (81, 100), "statement coverage"),
    ],
)
def test_exactly_eighty_in_either_metric_fails(
    tmp_path: Path,
    lines: tuple[int, int],
    branches: tuple[int, int],
    message: str,
) -> None:
    xml = write_cobertura(tmp_path, lines=lines, branches=branches)
    with pytest.raises(CoverageGateError, match=message):
        evaluate_coverage(xml, minimum=Decimal("80.0"))


def test_both_metrics_strictly_above_eighty_pass(tmp_path: Path) -> None:
    result = evaluate_coverage(
        write_cobertura(
            tmp_path,
            lines=(8001, 10000),
            branches=(4001, 5000),
        ),
        minimum=Decimal("80.0"),
    )
    assert result.statement_percent == Decimal("80.0100")
    assert result.branch_percent == Decimal("80.0200")
    assert result.passed is True


@pytest.mark.parametrize(
    ("lines", "branches", "message"),
    [
        ((9, 10), (0, 0), "branch denominator is zero"),
        ((0, 0), (9, 10), "statement denominator is zero"),
        ((11, 10), (9, 10), "covered count exceeds denominator"),
        ((9, 10), (-1, 10), "coverage counts must be non-negative"),
    ],
)
def test_invalid_counts_fail_closed(
    tmp_path: Path,
    lines: tuple[int, int],
    branches: tuple[int, int],
    message: str,
) -> None:
    xml = write_cobertura(tmp_path, lines=lines, branches=branches)
    with pytest.raises(CoverageGateError, match=message):
        evaluate_coverage(xml, minimum=Decimal("80.0"))


@pytest.mark.parametrize(
    "content",
    (
        "<coverage>",
        '<coverage lines-covered="9" lines-valid="10"/>',
        (
            '<coverage lines-covered="nine" lines-valid="10" '
            'branches-covered="9" branches-valid="10"/>'
        ),
    ),
)
def test_malformed_or_incomplete_xml_fails_closed(
    tmp_path: Path, content: str
) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text(content, encoding="utf-8")
    with pytest.raises(CoverageGateError, match="invalid coverage XML"):
        evaluate_coverage(xml, minimum=Decimal("80.0"))


def test_result_json_is_deterministic_and_key_ordered(tmp_path: Path) -> None:
    result = evaluate_coverage(
        write_cobertura(tmp_path, lines=(81, 100), branches=(82, 100)),
        minimum=Decimal("80.0"),
    )
    document = result_document(result)
    assert list(document) == [
        "lines_covered",
        "lines_valid",
        "branches_covered",
        "branches_valid",
        "statement_percent",
        "branch_percent",
        "minimum_exclusive",
        "passed",
    ]
    encoded = json.dumps(document, indent=2, ensure_ascii=True) + "\n"
    assert encoded == json.dumps(result_document(result), indent=2) + "\n"
    assert document["statement_percent"] == "81.0000"
    assert document["branch_percent"] == "82.0000"
