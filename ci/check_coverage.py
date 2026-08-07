#!/usr/bin/env python3
"""Independent exclusive statement and branch coverage gate."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys
from typing import Any, Sequence
from xml.etree import ElementTree


_FOUR_PLACES = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class CoverageGateResult:
    """Exact Cobertura totals and separately evaluated percentages."""

    lines_covered: int
    lines_valid: int
    branches_covered: int
    branches_valid: int
    statement_percent: Decimal
    branch_percent: Decimal
    minimum_exclusive: Decimal
    passed: bool


class CoverageGateError(RuntimeError):
    """Raised whenever coverage evidence is absent, invalid, or insufficient."""

    def __init__(
        self,
        message: str,
        *,
        result: CoverageGateResult | None = None,
    ) -> None:
        super().__init__(message)
        self.result = result


def _percent(covered: int, valid: int) -> Decimal:
    return (Decimal(covered) * Decimal(100) / Decimal(valid)).quantize(
        _FOUR_PLACES
    )


def _validate_minimum(minimum: Decimal) -> Decimal:
    if not isinstance(minimum, Decimal) or not minimum.is_finite():
        raise CoverageGateError("minimum must be a finite decimal")
    if minimum < 0 or minimum >= 100:
        raise CoverageGateError("minimum must be at least zero and below 100")
    return minimum


def evaluate_coverage(
    path: Path,
    *,
    minimum: Decimal,
) -> CoverageGateResult:
    """Evaluate root Cobertura totals against one exclusive threshold."""
    threshold = _validate_minimum(minimum)
    try:
        root = ElementTree.parse(path).getroot()
        lines_covered = int(root.attrib["lines-covered"])
        lines_valid = int(root.attrib["lines-valid"])
        branches_covered = int(root.attrib["branches-covered"])
        branches_valid = int(root.attrib["branches-valid"])
    except (OSError, ElementTree.ParseError, KeyError, ValueError) as error:
        raise CoverageGateError(f"invalid coverage XML: {error}") from error

    counts = (lines_covered, lines_valid, branches_covered, branches_valid)
    if any(count < 0 for count in counts):
        raise CoverageGateError("coverage counts must be non-negative")
    if lines_valid == 0:
        raise CoverageGateError("statement denominator is zero")
    if branches_valid == 0:
        raise CoverageGateError("branch denominator is zero")
    if lines_covered > lines_valid or branches_covered > branches_valid:
        raise CoverageGateError("covered count exceeds denominator")

    statement = _percent(lines_covered, lines_valid)
    branch = _percent(branches_covered, branches_valid)
    passed = statement > threshold and branch > threshold
    result = CoverageGateResult(
        lines_covered,
        lines_valid,
        branches_covered,
        branches_valid,
        statement,
        branch,
        threshold,
        passed,
    )
    if statement <= threshold:
        raise CoverageGateError(
            f"statement coverage {statement} is not greater than {threshold}",
            result=result,
        )
    if branch <= threshold:
        raise CoverageGateError(
            f"branch coverage {branch} is not greater than {threshold}",
            result=result,
        )
    return result


def result_document(result: CoverageGateResult) -> dict[str, Any]:
    """Return deterministic JSON-ready evidence in stable review order."""
    return {
        "lines_covered": result.lines_covered,
        "lines_valid": result.lines_valid,
        "branches_covered": result.branches_covered,
        "branches_valid": result.branches_valid,
        "statement_percent": f"{result.statement_percent:.4f}",
        "branch_percent": f"{result.branch_percent:.4f}",
        "minimum_exclusive": str(result.minimum_exclusive),
        "passed": result.passed,
    }


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _decimal_argument(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_xml", type=Path)
    parser.add_argument("--minimum", type=_decimal_argument, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Write evidence and return nonzero for invalid or insufficient coverage."""
    args = _parser().parse_args(argv)
    try:
        result = evaluate_coverage(args.coverage_xml, minimum=args.minimum)
    except CoverageGateError as error:
        document = (
            result_document(error.result)
            if error.result is not None
            else {
                "minimum_exclusive": str(args.minimum),
                "passed": False,
            }
        )
        document["error"] = str(error)
        try:
            _write_document(args.output, document)
        except OSError as write_error:
            print(f"coverage gate evidence write failed: {write_error}", file=sys.stderr)
            return 2
        print(f"coverage gate failed: {error}", file=sys.stderr)
        return 1
    try:
        _write_document(args.output, result_document(result))
    except OSError as error:
        print(f"coverage gate evidence write failed: {error}", file=sys.stderr)
        return 2
    print(
        "coverage gate passed: "
        f"statements={result.statement_percent:.4f}% "
        f"branches={result.branch_percent:.4f}% "
        f"minimum>{result.minimum_exclusive}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
