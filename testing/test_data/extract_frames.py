#!/usr/bin/env python3
"""
Extrahuje reálné frames z payloads.db pro testování.

Vytvoří JSON soubory s frames které můžeme přehrát v testech.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "analysis" / "payloads.db"


def extract_frames(db_path: str, limit: int = 100, direction: str = "box_to_proxy"):
    """Extrahuje frames z databáze."""

    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get frames
    query = """
        SELECT ts, device_id, table_name, raw, parsed, length
        FROM frames
        WHERE direction = ?
        ORDER BY ts
        LIMIT ?
    """

    cursor.execute(query, (direction, limit))
    rows = cursor.fetchall()

    frames = []
    for row in rows:
        ts, device_id, table_name, raw, parsed, length = row
        frames.append({
            "timestamp": ts,
            "device_id": device_id,
            "table_name": table_name,
            "frame": raw,
            "parsed": json.loads(parsed) if parsed else {},
            "length": length
        })

    conn.close()

    print(f"✅ Extracted {len(frames)} frames from {db_path}")
    return frames


def extract_by_table(db_path: str, table_name: str, limit: int = 50):
    """Extrahuje frames pro konkrétní tabulku."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = """
        SELECT ts, device_id, table_name, raw, parsed, length
        FROM frames
        WHERE table_name = ? AND direction = 'box_to_proxy'
        ORDER BY ts
        LIMIT ?
    """

    cursor.execute(query, (table_name, limit))
    rows = cursor.fetchall()

    frames = []
    for row in rows:
        ts, device_id, table_name, raw, parsed, length = row
        frames.append({
            "timestamp": ts,
            "device_id": device_id,
            "table_name": table_name,
            "frame": raw,
            "parsed": json.loads(parsed) if parsed else {},
            "length": length
        })

    conn.close()

    print(f"✅ Extracted {len(frames)} frames for {table_name}")
    return frames


def main():
    """Extrahuje testovací data."""

    db_path = str(DB_PATH)
    output_dir = Path(__file__).parent / "test_data"
    output_dir.mkdir(exist_ok=True)

    print(f"📦 Extracting frames from: {db_path}")
    print(f"📁 Output directory: {output_dir}")
    print()

    # 1. Mix 100 frames (různé tabulky)
    frames_100 = extract_frames(db_path, limit=100)
    if frames_100:
        output_file = output_dir / "box_frames_100.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(frames_100, f, indent=2, ensure_ascii=False)
        print(f"   → {output_file}")

    # 2. Jen tbl_actual (high frequency)
    frames_actual = extract_by_table(db_path, "tbl_actual", limit=50)
    if frames_actual:
        output_file = output_dir / "box_frames_actual.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(frames_actual, f, indent=2, ensure_ascii=False)
        print(f"   → {output_file}")

    # 3. 5min tabulky (pro replay test)
    tables_5min = ["tbl_dc_in", "tbl_ac_in", "tbl_ac_out", "tbl_batt",
                   "tbl_boiler", "tbl_box", "tbl_events"]
    frames_5min = []
    for table in tables_5min:
        frames = extract_by_table(db_path, table, limit=10)
        frames_5min.extend(frames)

    if frames_5min:
        output_file = output_dir / "box_frames_5min.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(frames_5min, f, indent=2, ensure_ascii=False)
        print(f"   → {output_file} ({len(frames_5min)} frames)")

    print()
    print("✅ Test data ready!")
    print()
    print("Usage in tests:")
    print("  python mock_box_client.py --data test_data/box_frames_100.json")


if __name__ == "__main__":
    main()
