#!/usr/bin/env python3
"""
Daily collection validation CLI with hard pass/fail thresholds.

Checks:
  1. Schema integrity    - frames table and required columns present
  2. Minimum frame count - total frames >= min_frame_count
  3. Required signals    - all specified table_name values present
  4. Null-rate           - critical columns must not exceed max_null_rate

Exit codes:
  0  PASS (all checks passed)
  1  FAIL (one or more checks failed)
  2  ERROR (could not connect to DB or fatal error)
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_COLUMNS: list[str] = ["id", "ts", "device_id", "table_name", "direction"]

DEFAULT_MIN_FRAME_COUNT: int = 1000

DEFAULT_REQUIRED_SIGNALS: list[str] = [
    "IsNewSet",
    "IsNewWeather",
    "IsNewFW",
    "END",
    "ACK",
]

DEFAULT_MAX_NULL_RATE: float = 0.01  # fraction [0-1], not percent

DEFAULT_NULL_RATE_COLUMNS: list[str] = ["ts", "table_name", "direction"]


def open_db(db_path: str) -> sqlite3.Connection | None:
    """Open SQLite; tries URI read-only first, falls back to normal open."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        return conn
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        try:
            return sqlite3.connect(db_path)
        except (sqlite3.Error, OSError):
            return None


def _table_exists(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='frames'"
    )
    return cur.fetchone() is not None


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(frames)")}


def check_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    result: dict[str, Any] = {
        "check": "schema_integrity",
        "status": "FAIL",
        "details": {},
        "message": "",
    }

    if not _table_exists(conn):
        result["message"] = "frames table does not exist in database"
        return result

    existing = _existing_columns(conn)
    missing = [c for c in REQUIRED_COLUMNS if c not in existing]

    result["details"] = {
        "required_columns": REQUIRED_COLUMNS,
        "existing_columns": sorted(existing),
        "missing_columns": missing,
    }

    if missing:
        result["message"] = f"Missing required columns: {missing}"
    else:
        result["status"] = "PASS"
        result["message"] = f"All {len(REQUIRED_COLUMNS)} required columns present"

    return result


def check_frame_count(conn: sqlite3.Connection, min_count: int) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] or 0
    passed = total >= min_count
    return {
        "check": "min_frame_count",
        "status": "PASS" if passed else "FAIL",
        "details": {"total_frames": total, "threshold": min_count},
        "message": (
            f"Frame count {total} >= threshold {min_count}"
            if passed
            else f"Frame count {total} is below minimum threshold {min_count}"
        ),
    }


def check_required_signals(
    conn: sqlite3.Connection, required_signals: list[str]
) -> dict[str, Any]:
    placeholders = ",".join("?" * len(required_signals))
    found = {
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT table_name FROM frames WHERE table_name IN ({placeholders})",
            required_signals,
        )
    }
    missing = [s for s in required_signals if s not in found]

    signal_counts = {
        sig: conn.execute(
            "SELECT COUNT(*) FROM frames WHERE table_name = ?", (sig,)
        ).fetchone()[0] or 0
        for sig in required_signals
    }

    passed = len(missing) == 0
    return {
        "check": "required_signal_classes",
        "status": "PASS" if passed else "FAIL",
        "details": {
            "required_signals": required_signals,
            "found_signals": sorted(found),
            "missing_signals": missing,
            "signal_counts": signal_counts,
        },
        "message": (
            f"All {len(required_signals)} required signals present"
            if passed
            else f"Missing required signals: {missing}"
        ),
    }


def check_null_rates(
    conn: sqlite3.Connection,
    columns: list[str],
    max_null_rate: float,
    per_column_max: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Null-rate check per column. per_column_max overrides max_null_rate for
    specific columns. Rates are fractions [0, 1], not percentages.
    """
    result: dict[str, Any] = {
        "check": "null_rate",
        "status": "FAIL",
        "details": {},
        "message": "",
    }

    total = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] or 0
    if total == 0:
        result["message"] = "No frames in table; cannot compute null rates"
        return result

    existing = _existing_columns(conn)
    overrides = per_column_max or {}
    column_results: dict[str, Any] = {}
    all_pass = True

    for col in columns:
        if col not in existing:
            column_results[col] = {
                "status": "SKIP",
                "null_count": None,
                "null_rate": None,
                "threshold": None,
                "message": "Column not present in schema",
            }
            continue

        null_count = (
            conn.execute(f"SELECT COUNT(*) FROM frames WHERE {col} IS NULL")
            .fetchone()[0] or 0
        )
        null_rate = null_count / total
        threshold = overrides.get(col, max_null_rate)
        passed = null_rate <= threshold

        if not passed:
            all_pass = False

        column_results[col] = {
            "status": "PASS" if passed else "FAIL",
            "null_count": null_count,
            "null_rate": round(null_rate, 6),
            "null_rate_pct": round(null_rate * 100, 4),
            "threshold": threshold,
            "threshold_pct": round(threshold * 100, 4),
            "message": (
                f"null rate {null_rate*100:.4f}% <= {threshold*100:.4f}%"
                if passed
                else f"null rate {null_rate*100:.4f}% EXCEEDS threshold {threshold*100:.4f}%"
            ),
        }

    failing = [col for col, r in column_results.items() if r["status"] == "FAIL"]

    result["details"] = {
        "total_frames": total,
        "global_max_null_rate": max_null_rate,
        "columns": column_results,
    }
    if all_pass:
        result["status"] = "PASS"
        result["message"] = (
            f"All {len(columns)} columns within null-rate threshold "
            f"(<= {max_null_rate*100:.2f}%)"
        )
    else:
        result["message"] = f"Null-rate threshold exceeded for columns: {failing}"

    return result


def run_validation(
    db_path: str,
    min_frame_count: int = DEFAULT_MIN_FRAME_COUNT,
    required_signals: list[str] | None = None,
    max_null_rate: float = DEFAULT_MAX_NULL_RATE,
    null_rate_columns: list[str] | None = None,
    per_column_max: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Run all four validation checks and return a structured report.

    Any single FAIL makes overall FAIL; there is no silent downgrade to warning.
    Returns report dict with overall, checks list, summary counts, and error field.
    """
    if required_signals is None:
        required_signals = list(DEFAULT_REQUIRED_SIGNALS)
    if null_rate_columns is None:
        null_rate_columns = list(DEFAULT_NULL_RATE_COLUMNS)

    report: dict[str, Any] = {
        "validation_timestamp": datetime.now(timezone.utc).isoformat(),
        "db_path": db_path,
        "thresholds": {
            "min_frame_count": min_frame_count,
            "required_signals": required_signals,
            "max_null_rate": max_null_rate,
            "null_rate_columns": null_rate_columns,
            "per_column_overrides": per_column_max or {},
        },
        "overall": "FAIL",
        "checks": [],
        "summary": {"total_checks": 0, "passed": 0, "failed": 0, "skipped": 0},
        "error": None,
    }

    conn = open_db(db_path)
    if conn is None:
        report["error"] = f"Could not open database: {db_path}"
        return report

    try:
        checks = [
            check_schema(conn),
            check_frame_count(conn, min_frame_count),
            check_required_signals(conn, required_signals),
            check_null_rates(conn, null_rate_columns, max_null_rate, per_column_max),
        ]
    except sqlite3.Error as exc:
        report["error"] = f"Database query failed: {exc}"
        return report
    finally:
        conn.close()

    report["checks"] = checks
    passed = sum(1 for c in checks if c["status"] == "PASS")
    failed = sum(1 for c in checks if c["status"] == "FAIL")
    skipped = sum(1 for c in checks if c["status"] == "SKIP")
    report["summary"] = {
        "total_checks": len(checks),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }
    report["overall"] = "PASS" if failed == 0 else "FAIL"

    return report


def print_human_summary(report: dict[str, Any]) -> None:
    ts = report.get("validation_timestamp", "?")
    db = report.get("db_path", "?")
    overall = report.get("overall", "UNKNOWN")

    print()
    print("=" * 70)
    print("  OIG PROXY - Daily Collection Validation")
    print(f"  {ts}")
    print(f"  DB: {db}")
    print("=" * 70)

    if report.get("error"):
        print(f"\n  ERROR: {report['error']}")
        print("\n  OVERALL: ERROR")
        print("=" * 70)
        return

    for check in report.get("checks", []):
        status = check["status"]
        name = check["check"]
        message = check.get("message", "")
        icon = "+" if status == "PASS" else ("-" if status == "FAIL" else "~")

        print(f"\n  [{icon}] {name}")
        print(f"      {message}")

        details = check.get("details", {})

        if name == "schema_integrity" and details.get("missing_columns"):
            print(f"      Missing: {details['missing_columns']}")

        if name == "min_frame_count":
            print(
                f"      total={details.get('total_frames')}  "
                f"threshold={details.get('threshold')}"
            )

        if name == "required_signal_classes":
            missing_sigs = set(details.get("missing_signals", []))
            for sig, cnt in details.get("signal_counts", {}).items():
                marker = "-" if sig in missing_sigs else "+"
                print(f"        [{marker}] {sig}: {cnt}")

        if name == "null_rate":
            for col, cr in details.get("columns", {}).items():
                col_status = cr.get("status", "?")
                col_icon = "+" if col_status == "PASS" else ("-" if col_status == "FAIL" else "~")
                pct = cr.get("null_rate_pct")
                thr = cr.get("threshold_pct")
                if pct is not None:
                    print(f"        [{col_icon}] {col}: {pct:.4f}% (max {thr:.4f}%)")
                else:
                    print(f"        [~] {col}: {cr.get('message', 'N/A')}")

    summary = report.get("summary", {})
    print()
    print("-" * 70)
    print(
        f"  Checks: {summary.get('passed', 0)} passed / "
        f"{summary.get('failed', 0)} failed / "
        f"{summary.get('skipped', 0)} skipped  "
        f"(total: {summary.get('total_checks', 0)})"
    )
    print()
    print(f"  OVERALL: {overall}")
    print("=" * 70)
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_daily_collection",
        description=(
            "Validate daily OIG frame capture quality. "
            "Exits 0 on PASS, 1 on FAIL, 2 on fatal error."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", required=True, metavar="PATH",
                        help="Path to SQLite database with frames table")
    parser.add_argument("--out", metavar="PATH",
                        help="Write machine-readable JSON result to this path")
    parser.add_argument("--min-frame-count", type=int,
                        default=DEFAULT_MIN_FRAME_COUNT, metavar="N",
                        help="Minimum total frame count threshold")
    parser.add_argument("--required-signals", nargs="+",
                        default=list(DEFAULT_REQUIRED_SIGNALS), metavar="SIGNAL",
                        help="Required table_name values (signal classes) that must be present")
    parser.add_argument("--max-null-rate", type=float,
                        default=DEFAULT_MAX_NULL_RATE, metavar="RATE",
                        help="Maximum allowed null rate [0.0-1.0, e.g. 0.01 = 1%%]")
    parser.add_argument("--null-rate-columns", nargs="+",
                        default=list(DEFAULT_NULL_RATE_COLUMNS), metavar="COL",
                        help="Columns to check for null rates")
    parser.add_argument("--max-null-rate-ts", type=float, default=None, metavar="RATE",
                        help="Override max null rate for 'ts' column")
    parser.add_argument("--max-null-rate-table-name", type=float, default=None,
                        metavar="RATE", help="Override max null rate for 'table_name' column")
    parser.add_argument("--max-null-rate-direction", type=float, default=None,
                        metavar="RATE", help="Override max null rate for 'direction' column")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress human-readable output; only write JSON (requires --out)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    per_column: dict[str, float] = {}
    if args.max_null_rate_ts is not None:
        per_column["ts"] = args.max_null_rate_ts
    if args.max_null_rate_table_name is not None:
        per_column["table_name"] = args.max_null_rate_table_name
    if args.max_null_rate_direction is not None:
        per_column["direction"] = args.max_null_rate_direction

    report = run_validation(
        db_path=args.db,
        min_frame_count=args.min_frame_count,
        required_signals=args.required_signals,
        max_null_rate=args.max_null_rate,
        null_rate_columns=args.null_rate_columns,
        per_column_max=per_column if per_column else None,
    )

    if not args.quiet:
        print_human_summary(report)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if not args.quiet:
            print(f"  JSON report saved to: {out_path}")

    if report.get("error"):
        return 2
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
