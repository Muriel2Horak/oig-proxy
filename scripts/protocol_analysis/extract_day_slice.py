#!/usr/bin/env python3
"""
Day-slice frame extractor for OIG protocol analysis.

Extracts frame snapshots for a specific date from the payload capture DB.
Supports date-range filtering and conn_id grouping metadata.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_date(date_str: str) -> datetime:
    """
    Parse date string in YYYY-MM-DD format.

    Args:
        date_str: Date string in YYYY-MM-DD format

    Returns:
        datetime object representing the start of the day (00:00:00 UTC)

    Raises:
        ValueError: If date format is invalid
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"Invalid date format: {date_str}. Expected YYYY-MM-DD format."
        ) from exc


def extract_day_slice(
    db_path: str,
    date: datetime,
) -> dict:
    """
    Extract frames for a specific day from the SQLite DB.

    Args:
        db_path: Path to the SQLite database
        date: datetime object representing the target date (at 00:00:00 UTC)

    Returns:
        Dictionary with metadata and frames array
    """
    start_ts = date.isoformat()
    end_ts = (date + timedelta(days=1)).isoformat()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT
            id,
            ts,
            device_id,
            table_name,
            raw,
            raw_b64,
            parsed,
            direction,
            conn_id,
            peer,
            length
        FROM frames
        WHERE ts >= ? AND ts < ?
        ORDER BY ts ASC
    """

    cursor.execute(query, (start_ts, end_ts))
    rows = cursor.fetchall()

    frames = []
    for row in rows:
        frame = {
            "id": row["id"],
            "ts": row["ts"],
            "device_id": row["device_id"],
            "table_name": row["table_name"],
            "raw": row["raw"],
            "raw_b64": row["raw_b64"],
            "parsed": json.loads(row["parsed"]) if row["parsed"] else None,
            "direction": row["direction"],
            "conn_id": row["conn_id"],
            "peer": row["peer"],
            "length": row["length"],
        }
        frames.append(frame)

    conn_ids = set(f["conn_id"] for f in frames if f["conn_id"] is not None)

    conn.close()

    payload = {
        "source_db": str(db_path),
        "date": date.strftime("%Y-%m-%d"),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "frame_count": len(frames),
        "conn_id_count": len(conn_ids),
        "conn_ids": sorted(list(conn_ids)) if conn_ids else [],
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "frames": frames,
    }

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract per-day frame snapshots from payload capture DB"
    )
    parser.add_argument(
        "--db",
        default="analysis/ha_snapshot/payloads_ha_full.db",
        help="Path to SQLite database (default: analysis/ha_snapshot/payloads_ha_full.db)",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Date in YYYY-MM-DD format to extract",
    )
    parser.add_argument(
        "--out",
        default=".sisyphus/evidence/task-7-day-slice.json",
        help="Output JSON file path (default: .sisyphus/evidence/task-7-day-slice.json)",
    )
    args = parser.parse_args()

    try:
        target_date = parse_date(args.date)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: Database not found: {db_path}", file=sys.stderr)
        return 1

    try:
        payload = extract_day_slice(str(db_path), target_date)
    except sqlite3.Error as exc:
        print(f"Error: Database query failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Error: Extraction failed: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"Extracted {payload['frame_count']} frames for {args.date} -> {out_path}"
        )
        print(f"Connection IDs: {payload['conn_ids']}")
        return 0
    except (OSError, IOError) as exc:
        print(f"Error: Failed to write output file: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
