#!/usr/bin/env python3
"""
Message Timing & Round-Trip Analysis for OIG Proxy Protocol.
Focuses on setting commands and calculates latencies from unified_timeline.json.
"""

import json
import sys
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import statistics

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent / "oig-proxy"
EVIDENCE_DIR = PROJECT_DIR / ".sisyphus" / "evidence"
UNIFIED_TIMELINE = PROJECT_DIR / "unified_timeline.json"


def parse_timestamp(ts_str):
    """Parse ISO timestamp string to datetime."""
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def load_timeline():
    """Load unified_timeline.json."""
    with open(UNIFIED_TIMELINE, 'r') as f:
        data = json.load(f)
    return data.get('unified_timeline', [])


def pair_messages(timeline):
    """
    Pair box_to_proxy with cloud_to_proxy messages.
    Uses conn_id, table_name, and temporal proximity.
    """
    pairs = []
    
    # Group by source
    db_entries = [e for e in timeline if e.get('source') == 'db']
    
    # Group by conn_id
    by_conn = defaultdict(list)
    for entry in db_entries:
        by_conn[entry.get('conn_id')].append(entry)
    
    # Sort by timestamp within each connection
    for conn_id, entries in by_conn.items():
        entries.sort(key=lambda x: x.get('timestamp', ''))
        
        # Find pairs: box_to_proxy followed by cloud_to_proxy with same table_name
        pending_box = {}
        
        for entry in entries:
            direction = entry.get('direction')
            table_name = entry.get('table_name', '')
            ts = entry.get('timestamp')
            
            if direction == 'box_to_proxy':
                pending_box[table_name] = entry
            elif direction == 'cloud_to_proxy' and table_name in pending_box:
                box_entry = pending_box.pop(table_name)
                
                box_ts = parse_timestamp(box_entry.get('timestamp'))
                cloud_ts = parse_timestamp(entry.get('timestamp'))
                
                if box_ts and cloud_ts:
                    latency_ms = (cloud_ts - box_ts).total_seconds() * 1000
                    pairs.append({
                        'box_id': box_entry.get('id'),
                        'cloud_id': entry.get('id'),
                        'conn_id': conn_id,
                        'table_name': table_name,
                        'box_device_id': box_entry.get('device_id'),
                        'cloud_device_id': entry.get('device_id'),
                        'box_timestamp': box_entry.get('timestamp'),
                        'cloud_timestamp': entry.get('timestamp'),
                        'latency_ms': latency_ms,
                        'box_length': int(box_entry.get('length', 0)),
                        'cloud_length': int(entry.get('length', 0)),
                        'peer': box_entry.get('peer'),
                    })
    
    return pairs


def analyze_settings(timeline):
    """
    Analyze setting commands from timeline.
    Settings appear in:
    1. log_line content containing 'Setting' or 'Reason=Setting'
    2. Table tbl_events with Type=Setting
    """
    settings = []
    
    for entry in timeline:
        # Check log_line for setting patterns
        log_line = entry.get('log_line', '')
        if not log_line:
            continue
            
        # Look for setting-related log entries
        if any(pattern in log_line.lower() for pattern in ['setting', 'reason=setting', 'type=setting']):
            settings.append({
                'timestamp': entry.get('timestamp'),
                'source': entry.get('source'),
                'container': entry.get('container'),
                'session_id': entry.get('session_id'),
                'log_line': log_line[:200] + '...' if len(log_line) > 200 else log_line,
                'is_setting_ack': 'ACK' in log_line and 'Setting' in log_line,
                'is_setting_event': 'Type=Setting' in log_line or '<Type>Setting</Type>' in log_line,
            })
    
    return settings


def analyze_loki_sessions(timeline):
    """Analyze Loki log sessions for box/cloud interaction patterns."""
    loki_entries = [e for e in timeline if e.get('source') == 'loki']
    
    sessions = defaultdict(list)
    for entry in loki_entries:
        sid = entry.get('session_id')
        if sid:
            sessions[sid].append(entry)
    
    session_stats = []
    for sid, entries in sessions.items():
        entries.sort(key=lambda x: x.get('timestamp', ''))
        if entries:
            first_ts = entries[0].get('timestamp')
            last_ts = entries[-1].get('timestamp')
            session_stats.append({
                'session_id': sid,
                'entry_count': len(entries),
                'first_timestamp': first_ts,
                'last_timestamp': last_ts,
                'containers': list(set(e.get('container') for e in entries if e.get('container')))
            })
    
    return session_stats


def calculate_statistics(pairs):
    """Calculate timing statistics from pairs."""
    if not pairs:
        return {}
    
    latencies = [p['latency_ms'] for p in pairs]
    by_table = defaultdict(list)
    by_conn = defaultdict(list)
    
    for p in pairs:
        by_table[p['table_name']].append(p['latency_ms'])
        by_conn[p['conn_id']].append(p['latency_ms'])
    
    return {
        'total_pairs': len(pairs),
        'latency': {
            'min_ms': min(latencies),
            'max_ms': max(latencies),
            'mean_ms': statistics.mean(latencies),
            'median_ms': statistics.median(latencies),
            'stdev_ms': statistics.stdev(latencies) if len(latencies) > 1 else 0,
        },
        'by_table': {
            table: {
                'count': len(lats),
                'mean_ms': statistics.mean(lats),
                'min_ms': min(lats),
                'max_ms': max(lats),
            }
            for table, lats in by_table.items()
        },
        'by_conn_id': {
            str(cid): {
                'count': len(lats),
                'mean_ms': statistics.mean(lats),
            }
            for cid, lats in by_conn.items()
        },
        'packet_sizes': {
            'box_min': min(p['box_length'] for p in pairs),
            'box_max': max(p['box_length'] for p in pairs),
            'cloud_min': min(p['cloud_length'] for p in pairs),
            'cloud_max': max(p['cloud_length'] for p in pairs),
        }
    }


def generate_report(stats, settings, session_stats, pairs):
    """Generate human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("OIG PROXY - MESSAGE TIMING & ROUND-TRIP ANALYSIS")
    lines.append("=" * 70)
    lines.append("")
    
    # General statistics
    lines.append("## TIMING STATISTICS")
    lines.append("-" * 50)
    if stats:
        lines.append(f"Total message pairs analyzed: {stats.get('total_pairs', 0)}")
        latency = stats.get('latency', {})
        lines.append(f"")
        lines.append(f"Latency (Box -> Proxy -> Cloud):")
        lines.append(f"  Min:    {latency.get('min_ms', 0):.2f} ms")
        lines.append(f"  Max:    {latency.get('max_ms', 0):.2f} ms")
        lines.append(f"  Mean:   {latency.get('mean_ms', 0):.2f} ms")
        lines.append(f"  Median: {latency.get('median_ms', 0):.2f} ms")
        lines.append(f"  StdDev: {latency.get('stdev_ms', 0):.2f} ms")
        lines.append(f"")
        
        sizes = stats.get('packet_sizes', {})
        lines.append(f"Packet sizes:")
        lines.append(f"  Box (upstream):   {sizes.get('box_min', 0)} - {sizes.get('box_max', 0)} bytes")
        lines.append(f"  Cloud (response): {sizes.get('cloud_min', 0)} - {sizes.get('cloud_max', 0)} bytes")
        lines.append(f"")
        
        lines.append(f"## LATENCY BY TABLE")
        lines.append("-" * 50)
        by_table = stats.get('by_table', {})
        for table, tstats in sorted(by_table.items(), key=lambda x: -x[1]['count']):
            lines.append(f"  {table}:")
            lines.append(f"    Count: {tstats['count']}, Mean: {tstats['mean_ms']:.2f} ms, "
                        f"Range: {tstats['min_ms']:.2f} - {tstats['max_ms']:.2f} ms")
    else:
        lines.append("No timing statistics available (no pairs found)")
    
    lines.append("")
    lines.append("## SETTING COMMANDS ANALYSIS")
    lines.append("-" * 50)
    if settings:
        lines.append(f"Total setting-related log entries: {len(settings)}")
        ack_count = sum(1 for s in settings if s.get('is_setting_ack'))
        event_count = sum(1 for s in settings if s.get('is_setting_event'))
        lines.append(f"  Setting ACKs:  {ack_count}")
        lines.append(f"  Setting events: {event_count}")
        lines.append("")
        
        if settings:
            lines.append("Sample setting entries:")
            for s in settings[:5]:
                lines.append(f"  [{s.get('timestamp', 'N/A')}]")
                lines.append(f"    Container: {s.get('container', 'N/A')}")
                lines.append(f"    Session: {s.get('session_id', 'N/A')}")
                lines.append(f"    Log: {s.get('log_line', '')[:100]}")
    else:
        lines.append("No setting commands found in timeline")
        lines.append("")
        lines.append("Note: The unified_timeline.json contains DB entries (table data)")
        lines.append("and Loki logs. Setting commands typically appear in:")
        lines.append("  1. tbl_events with Type=Setting in XML payload")
        lines.append("  2. ACK frames with Reason=Setting")
        lines.append("These require parsing the actual frame content (not just metadata)")
    
    lines.append("")
    lines.append("## SESSION STATISTICS (from Loki)")
    lines.append("-" * 50)
    if session_stats:
        lines.append(f"Total sessions: {len(session_stats)}")
        for sess in session_stats[:10]:
            lines.append(f"  Session {sess['session_id']}: {sess['entry_count']} entries")
            lines.append(f"    First: {sess['first_timestamp']}")
            lines.append(f"    Last:  {sess['last_timestamp']}")
            lines.append(f"    Containers: {', '.join(sess['containers'])}")
    else:
        lines.append("No session data available")
    
    lines.append("")
    lines.append("## SAMPLE MESSAGE PAIRS (first 10)")
    lines.append("-" * 50)
    for p in pairs[:10]:
        lines.append(f"  Pair: box_id={p['box_id']} -> cloud_id={p['cloud_id']}")
        lines.append(f"    Table: {p['table_name']}, Conn: {p['conn_id']}")
        lines.append(f"    Latency: {p['latency_ms']:.2f} ms")
        lines.append(f"    Sizes: {p['box_length']} -> {p['cloud_length']} bytes")
        lines.append(f"    Box TS: {p['box_timestamp']}")
        lines.append(f"    Cloud TS: {p['cloud_timestamp']}")
        lines.append("")
    
    lines.append("")
    lines.append("=" * 70)
    lines.append("END OF REPORT")
    lines.append("=" * 70)
    
    return "\n".join(lines)


def main():
    print("Loading unified_timeline.json...")
    timeline = load_timeline()
    print(f"  Loaded {len(timeline)} entries")
    
    # Categorize entries
    db_count = sum(1 for e in timeline if e.get('source') == 'db')
    loki_count = sum(1 for e in timeline if e.get('source') == 'loki')
    print(f"  DB entries: {db_count}")
    print(f"  Loki entries: {loki_count}")
    
    print("\nPairing messages...")
    pairs = pair_messages(timeline)
    print(f"  Found {len(pairs)} message pairs")
    
    print("\nAnalyzing settings...")
    settings = analyze_settings(timeline)
    print(f"  Found {len(settings)} setting-related entries")
    
    print("\nAnalyzing sessions...")
    session_stats = analyze_loki_sessions(timeline)
    print(f"  Found {len(session_stats)} sessions")
    
    print("\nCalculating statistics...")
    stats = calculate_statistics(pairs)
    
    # Generate report
    report = generate_report(stats, settings, session_stats, pairs)
    
    # Ensure evidence directory exists
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Save report
    report_path = EVIDENCE_DIR / "task-8-timing-stats.txt"
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nReport saved to: {report_path}")
    
    # Save detailed JSON
    settings_json = {
        'analysis_timestamp': datetime.now().isoformat(),
        'statistics': stats,
        'settings': settings,
        'session_stats': session_stats,
        'sample_pairs': pairs[:20],
    }
    settings_path = EVIDENCE_DIR / "task-8-settings.json"
    with open(settings_path, 'w') as f:
        json.dump(settings_json, f, indent=2)
    print(f"JSON saved to: {settings_path}")
    
    # Print report to stdout
    print("\n" + report)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
