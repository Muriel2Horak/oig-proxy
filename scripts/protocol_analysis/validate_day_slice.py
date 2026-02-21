#!/usr/bin/env python3
"""
Validate day-slice frame extractor output.

Validates the JSON output from extract_day_slice.py to ensure:
- Required metadata fields are present
- Date format is valid
- Frame count matches actual array length
- Connection IDs are consistent with frames
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def validate_day_slice(json_path: str) -> tuple[bool, list[str]]:
    """
    Validate a day-slice JSON file.

    Args:
        json_path: Path to the JSON file to validate

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    try:
        file_path = Path(json_path)
        if not file_path.exists():
            errors.append(f"File not found: {json_path}")
            return False, errors

        content = file_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (OSError, IOError) as exc:
        errors.append(f"Failed to read file: {exc}")
        return False, errors
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON: {exc}")
        return False, errors

    required_metadata = [
        "source_db",
        "date",
        "start_ts",
        "end_ts",
        "frame_count",
        "conn_id_count",
        "conn_ids",
        "extracted_at",
        "frames",
    ]

    for field in required_metadata:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if "date" in data:
        try:
            datetime.strptime(data["date"], "%Y-%m-%d")
        except ValueError as exc:
            errors.append(f"Invalid date format: {data['date']}: {exc}")

    if "start_ts" in data:
        try:
            datetime.fromisoformat(data["start_ts"].replace("Z", "+00:00"))
        except ValueError as exc:
            errors.append(f"Invalid start_ts format: {data['start_ts']}: {exc}")

    if "end_ts" in data:
        try:
            datetime.fromisoformat(data["end_ts"].replace("Z", "+00:00"))
        except ValueError as exc:
            errors.append(f"Invalid end_ts format: {data['end_ts']}: {exc}")

    if "extracted_at" in data:
        try:
            datetime.fromisoformat(data["extracted_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            errors.append(f"Invalid extracted_at format: {data['extracted_at']}: {exc}")

    if "frames" in data:
        frames = data["frames"]
        if not isinstance(frames, list):
            errors.append("frames must be a list")
        else:
            if "frame_count" in data and data["frame_count"] != len(frames):
                errors.append(
                    f"frame_count mismatch: metadata says {data['frame_count']}, "
                    f"but frames array has {len(frames)} elements"
                )

            required_frame_fields = [
                "id",
                "ts",
                "direction",
                "conn_id",
            ]

            for idx, frame in enumerate(frames):
                if not isinstance(frame, dict):
                    errors.append(f"Frame {idx}: must be a dictionary")
                    continue

                for field in required_frame_fields:
                    if field not in frame:
                        errors.append(f"Frame {idx}: missing required field: {field}")

                if "ts" in frame:
                    try:
                        datetime.fromisoformat(frame["ts"].replace("Z", "+00:00"))
                    except ValueError:
                        errors.append(f"Frame {idx}: invalid ts format: {frame['ts']}")

                if "date" in data and "ts" in frame:
                    if not frame["ts"].startswith(data["date"]):
                        errors.append(
                            f"Frame {idx}: timestamp {frame['ts']} does not match date {data['date']}"
                        )

    if "conn_id_count" in data and "frames" in data:
        frames = data["frames"]
        actual_conn_ids = set(
            f.get("conn_id") for f in frames if isinstance(f, dict) and f.get("conn_id") is not None
        )
        if "conn_ids" in data:
            expected_conn_ids = set(data["conn_ids"])
            if actual_conn_ids != expected_conn_ids:
                errors.append(
                    f"conn_ids mismatch: expected {expected_conn_ids}, "
                    f"found {actual_conn_ids} in frames"
                )

        if data["conn_id_count"] != len(actual_conn_ids):
            errors.append(
                f"conn_id_count mismatch: metadata says {data['conn_id_count']}, "
                f"but found {len(actual_conn_ids)} unique conn_ids in frames"
            )

    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate day-slice frame extractor output"
    )
    parser.add_argument(
        "json_file",
        help="Path to JSON file to validate",
    )
    args = parser.parse_args()

    is_valid, errors = validate_day_slice(args.json_file)

    if is_valid:
        print(f"✓ Validation passed: {args.json_file}")
        return 0

    print(f"✗ Validation failed: {args.json_file}")
    for error in errors:
        print(f"  - {error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
