#!/usr/bin/env python3
"""
Capture and annotate live golden handshake windows from HA DB.

Extracts complete MODE → ACK(Reason=Setting) → END sequences from
today's live data for use as golden fixtures in testing.

Based on existing HA export pattern and setting acceptance contract.
"""
import argparse
import json
import shlex
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple


def _run_ssh(ssh_host: str, inner_cmd: str) -> str:
    """Execute command on HA via SSH."""
    proc = subprocess.run(
        ["ssh", ssh_host, inner_cmd],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _find_mode_sequences(ssh_host: str, start_date: str) -> List[Dict[str, Any]]:
    """
    Find Setting frame sequences that include ACK and END frames.
    
    Returns list of complete sequences with metadata.
    """
    # Find Setting frames (Reason=Setting) for today
    py = (
        "import sqlite3, json;"
        "c=sqlite3.connect('/data/payloads.db');"
        "cur=c.cursor();"
        
        # Find Setting frames with their conn_id and timestamp
        f"setting_query = \"\"\""
        "SELECT id, conn_id, ts, table_name, raw "
        "FROM frames "
        "WHERE direction = 'cloud_to_proxy' "
        "AND raw LIKE '%Reason>Setting%' "
        "AND ts >= '{start_date} 00:00:00' "
        "AND ts < '{start_date} 23:59:59' "
        "ORDER BY ts"
        f"\"\"\";"
        
        "setting_frames = cur.execute(setting_query.format(start_date=start_date)).fetchall();"
        
        # For each Setting frame, find corresponding ACK and END frames
        "sequences = [];"
        "for setting_id, conn_id, setting_ts, table_name, raw in setting_frames:"
        "    # Find ACK frame (Reason=Setting, box_to_proxy)"
        "    ack_query = \"\"\""
        "    SELECT id, ts, table_name, raw "
        "    FROM frames "
        "    WHERE conn_id = ? "
        "    AND direction = 'box_to_proxy' "
        "    AND ts > ? "
        "    AND raw LIKE '%Reason>Setting%' "
        "    AND raw LIKE '%Result>ACK%' "
        "    ORDER BY ts "
        "    LIMIT 1"
        "    \"\"\";"
        
        "    ack_frame = cur.execute(ack_query, (conn_id, setting_ts)).fetchone();"
        
        "    # Find END frame after ACK"
        "    if ack_frame:"
        "        end_query = \"\"\""
        "        SELECT id, ts, table_name, raw "
        "        FROM frames "
        "        WHERE conn_id = ? "
        "        AND direction = 'box_to_proxy' "
        "        AND ts > ? "
        "        AND table_name = 'END' "
        "        ORDER BY ts "
        "        LIMIT 1"
        "        \"\"\";"
        "        end_frame = cur.execute(end_query, (conn_id, ack_frame[1])).fetchone();"
        "    else:"
        "        end_frame = None;"
        
        "    if ack_frame and end_frame:"
        "        sequences.append({"
        "            'setting_id': setting_id,"
        "            'setting_ts': setting_ts,"
        "            'conn_id': conn_id,"
        "            'setting_table': table_name,"
        "            'setting_raw': raw,"
        "            'ack_id': ack_frame[0],"
        "            'ack_ts': ack_frame[1],"
        "            'ack_table': ack_frame[2],"
        "            'ack_raw': ack_frame[3],"
        "            'end_id': end_frame[0],"
        "            'end_ts': end_frame[1],"
        "            'end_table': end_frame[2],"
        "            'end_raw': end_frame[3]"
        "        });"
        
        "print(json.dumps(sequences, indent=2));"
        "c.close()"
    )
    
    cmd = (
        "sudo docker exec addon_d7b5d5b1_oig_proxy "
        f"python3 -c {shlex.quote(py)}"
    )
    out = _run_ssh(ssh_host, cmd)
    return json.loads(out)


def _fetch_full_sequence_frames(ssh_host: str, conn_id: int, setting_id: int, ack_id: int, end_id: int) -> List[Dict[str, Any]]:
    """Fetch all frames in a sequence for context."""
    py = (
        "import sqlite3, json;"
        "c=sqlite3.connect('/data/payloads.db');"
        "cur=c.cursor();"
        
        # Get frames around the sequence for context
        f"query = \"\"\""
        "SELECT id, ts, direction, table_name, raw, length "
        "FROM frames "
        "WHERE conn_id = ? "
        "AND id BETWEEN ? AND ? "
        "ORDER BY id"
        f"\"\"\";"
        
        "min_id = min({mode_id}, {ack_id}, {end_id}) - 5;"  # Include some context before
        "max_id = max({mode_id}, {ack_id}, {end_id}) + 5;"  # Include some context after
        
        "rows = cur.execute(query, (conn_id, min_id, max_id)).fetchall();"
        
        "out = [];"
        "[out.append({{'id':r[0],'ts':r[1],'direction':r[2],'table_name':r[3],'raw':r[4],'length':r[5]}}) for r in rows];"
        
        "print(json.dumps(out, indent=2));"
        "c.close()"
    )
    
    cmd = (
        "sudo docker exec addon_d7b5d5b1_oig_proxy "
        f"python3 -c {shlex.quote(py)}"
    )
    out = _run_ssh(ssh_host, cmd)
    return json.loads(out)


def _create_golden_fixture(sequence: Dict[str, Any], context_frames: List[Dict[str, Any]], ssh_host: str) -> Dict[str, Any]:
    """Create a golden fixture from sequence data."""
    # Calculate timing metrics
    setting_dt = datetime.fromisoformat(sequence['setting_ts'].replace(' ', 'T'))
    ack_dt = datetime.fromisoformat(sequence['ack_ts'].replace(' ', 'T'))
    end_dt = datetime.fromisoformat(sequence['end_ts'].replace(' ', 'T'))
    
    setting_to_ack_s = (ack_dt - setting_dt).total_seconds()
    ack_to_end_s = (end_dt - ack_dt).total_seconds()
    total_duration_s = (end_dt - setting_dt).total_seconds()
    
    # Extract key values from frames
    setting_raw = sequence['setting_raw']
    ack_raw = sequence['ack_raw']
    end_raw = sequence['end_raw']
    
    fixture = {
        "metadata": {
            "source": f"ha:/data/payloads.db (conn_id={sequence['conn_id']})",
            "extraction_date": datetime.now(timezone.utc).isoformat(),
            "date_range": {
                "start": sequence['setting_ts'],
                "end": sequence['end_ts']
            },
            "extraction_query": f"Setting frames for {sequence['setting_ts'][:10]} with ACK(Reason=Setting) and END",
            "sequence_type": "Setting → ACK(Reason=Setting) → END",
            "timing_metrics": {
                "setting_to_ack_seconds": round(setting_to_ack_s, 3),
                "ack_to_end_seconds": round(ack_to_end_s, 3),
                "total_duration_seconds": round(total_duration_s, 3)
            },
            "validation_fields": {
                "setting_id": sequence['setting_id'],
                "ack_id": sequence['ack_id'],
                "end_id": sequence['end_id']
            }
        },
        "sequence": {
            "setting_frame": {
                "id": sequence['setting_id'],
                "ts": sequence['setting_ts'],
                "table_name": sequence['setting_table'],
                "raw": setting_raw
            },
            "ack_frame": {
                "id": sequence['ack_id'],
                "ts": sequence['ack_ts'],
                "table_name": sequence['ack_table'],
                "raw": ack_raw
            },
            "end_frame": {
                "id": sequence['end_id'],
                "ts": sequence['end_ts'],
                "table_name": sequence['end_table'],
                "raw": end_raw
            }
        },
        "context_frames": context_frames
    }
    
    return fixture


def _validate_fixture(fixture: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate fixture meets acceptance contract requirements."""
    issues = []
    
    # Check required validation fields
    validation_fields = fixture['metadata']['validation_fields']
    for field_name in ['mode_id', 'ack_id', 'end_id']:
        if validation_fields[field_name] is None:
            issues.append(f"Missing {field_name}")
    
    # Check sequence completeness
    sequence = fixture['sequence']
    if not sequence['setting_frame']['raw']:
        issues.append("Setting frame raw data missing")
    if not sequence['ack_frame']['raw']:
        issues.append("ACK frame raw data missing")
    if not sequence['end_frame']['raw']:
        issues.append("END frame raw data missing")
    
    # Check for Reason=Setting in ACK
    ack_raw = sequence['ack_frame']['raw']
    if 'Reason>Setting<' not in ack_raw:
        issues.append("ACK frame missing Reason=Setting")
    
    # Check for Result=ACK in ACK
    if 'Result>ACK<' not in ack_raw:
        issues.append("ACK frame missing Result=ACK")
    
    # Check timing (should be 6-7 seconds based on contract)
    timing = fixture['metadata']['timing_metrics']
    if timing['setting_to_ack_seconds'] > 60:
        issues.append(f"Setting to ACK delay too long: {timing['setting_to_ack_seconds']}s")
    
    return len(issues) == 0, issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture live golden handshake windows from HA DB"
    )
    parser.add_argument("--ssh-host", default="ha")
    parser.add_argument("--date", default="2026-02-18", help="Date to extract (YYYY-MM-DD)")
    parser.add_argument("--output-dir", default="/Users/martinhorak/Projects/oig-diagnostic-cloud/analysis/golden-handshake-fixtures")
    parser.add_argument("--min-sequences", type=int, default=3, help="Minimum sequences to extract")
    args = parser.parse_args()
    
    print(f"🔍 Extracting golden handshake windows for {args.date}...")
    
    # Find MODE sequences
    sequences = _find_mode_sequences(args.ssh_host, args.date)
    print(f"Found {len(sequences)} complete MODE sequences")
    
    if len(sequences) < args.min_sequences:
        print(f"❌ Only found {len(sequences)} sequences, need at least {args.min_sequences}")
        return 1
    
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract and validate golden fixtures
    valid_fixtures = []
    for i, sequence in enumerate(sequences[:args.min_sequences]):
        print(f"📦 Processing sequence {i+1}/{min(args.min_sequences, len(sequences))}")
        
        # Fetch context frames
        context_frames = _fetch_full_sequence_frames(
            args.ssh_host, 
            sequence['conn_id'],
            sequence['setting_id'],
            sequence['ack_id'],
            sequence['end_id']
        )
        
        # Create golden fixture
        fixture = _create_golden_fixture(sequence, context_frames, args.ssh_host)
        
        # Validate fixture
        is_valid, issues = _validate_fixture(fixture)
        if not is_valid:
            print(f"⚠️  Sequence {i+1} validation issues: {', '.join(issues)}")
            continue
        
        valid_fixtures.append(fixture)
        
        # Save individual fixture
        fixture_file = output_dir / f"golden-setting-sequence-{i+1}-{args.date}.json"
        with open(fixture_file, 'w', encoding='utf-8') as f:
            json.dump(fixture, f, indent=2, ensure_ascii=False)
        print(f"✅ Saved: {fixture_file}")
    
    # Save combined fixtures file
    combined_file = output_dir / f"golden-handshake-fixtures-{args.date}.json"
    with open(combined_file, 'w', encoding='utf-8') as f:
        json.dump({
            "metadata": {
                "extraction_date": datetime.now(timezone.utc).isoformat(),
                "source_date": args.date,
                "total_fixtures": len(valid_fixtures),
                "description": "Live golden handshake windows for testing"
            },
            "fixtures": valid_fixtures
        }, f, indent=2, ensure_ascii=False)
    print(f"✅ Combined fixtures saved: {combined_file}")
    
    # Summary
    print(f"\n📊 Summary:")
    print(f"  - Date: {args.date}")
    print(f"  - Sequences found: {len(sequences)}")
    print(f"  - Valid fixtures: {len(valid_fixtures)}")
    print(f"  - Output directory: {output_dir}")
    
    if len(valid_fixtures) >= args.min_sequences:
        print(f"✅ Successfully extracted {len(valid_fixtures)} golden handshake windows")
        return 0
    else:
        print(f"❌ Only {len(valid_fixtures)} valid fixtures, needed {args.min_sequences}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())