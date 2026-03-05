#!/usr/bin/env python3
"""
Protocol State Machine Analysis for OIG Proxy
Analyzes unified_timeline.json to identify:
- TCP session lifecycles
- State transitions
- Anomalies (interrupted connections, timeouts, takeovers)
"""

import json
import re
from datetime import datetime
from collections import defaultdict
from pathlib import Path


def parse_timestamp(ts_str):
    """Parse ISO timestamp to datetime."""
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except:
        return None


def extract_conn_id_from_log(log_line):
    """Extract conn_id from log_line patterns."""
    # Pattern: conn=1234
    match = re.search(r'conn=(\d+)', log_line)
    if match:
        return int(match.group(1))
    return None


def classify_log_event(log_line):
    """Classify log_line into event type and extract metadata."""
    event = {
        'type': None,
        'conn_id': None,
        'peer': None,
        'table': None,
        'frames_rx': None,
        'frames_tx': None,
        'error': None,
        'mode': None
    }
    
    # BOX connected
    if 'BOX connected' in log_line:
        event['type'] = 'BOX_CONNECT'
        match = re.search(r'conn=(\d+), peer=\([\'"]([^\'"]+)[\'"],\s*(\d+)\)', log_line)
        if match:
            event['conn_id'] = int(match.group(1))
            event['peer'] = f"{match.group(2)}:{match.group(3)}"
    
    # BOX closed
    elif 'closed the connection' in log_line:
        event['type'] = 'BOX_CLOSE'
        match = re.search(r'conn=(\d+), frames_rx=(\d+), frames_tx=(\d+)', log_line)
        if match:
            event['conn_id'] = int(match.group(1))
            event['frames_rx'] = int(match.group(2))
            event['frames_tx'] = int(match.group(3))
        # Check close reason
        if 'EOF' in log_line:
            event['error'] = 'EOF'
        elif 'timeout' in log_line.lower():
            event['error'] = 'timeout'
        elif 'reset' in log_line.lower():
            event['error'] = 'reset'
    
    # Cloud session connected
    elif 'Cloud session connected' in log_line:
        event['type'] = 'CLOUD_CONNECT'
        match = re.search(r'conn=(\d+), table=(\w+)', log_line)
        if match:
            event['conn_id'] = int(match.group(1))
            event['table'] = match.group(2)
    
    # Cloud error
    elif 'Cloud error' in log_line:
        event['type'] = 'CLOUD_ERROR'
        match = re.search(r'conn=(\d+), table=(\w+)', log_line)
        if match:
            event['conn_id'] = int(match.group(1))
            event['table'] = match.group(2)
        # Extract error type
        if 'Connection reset' in log_line:
            event['error'] = 'connection_reset'
        elif 'timeout' in log_line.lower():
            event['error'] = 'timeout'
        elif 'refused' in log_line.lower():
            event['error'] = 'connection_refused'
        else:
            err_match = re.search(r'\[Errno \d+\] ([^\(]+)', log_line)
            if err_match:
                event['error'] = err_match.group(1).strip()
    
    # HYBRID mode switch
    elif 'HYBRID' in log_line:
        if 'switching to offline mode' in log_line:
            event['type'] = 'MODE_OFFLINE'
            match = re.search(r'(\d+) failures', log_line)
            event['error'] = f"{match.group(1)} failures" if match else 'unknown'
        elif 'switching to online mode' in log_line or 'cloud recovered' in log_line:
            event['type'] = 'MODE_ONLINE'
        elif 'retry interval' in log_line:
            event['type'] = 'MODE_RETRY'
    
    # Heartbeat / status
    elif 'HB:' in log_line:
        event['type'] = 'HEARTBEAT'
        # Extract mode from HB
        mode_match = re.search(r'mode=(\w+)', log_line)
        if mode_match:
            event['mode'] = mode_match.group(1)
    
    # Event sent
    elif 'Event sent:' in log_line:
        event['type'] = 'EVENT_SENT'
        match = re.search(r'Event sent: (\w+)', log_line)
        if match:
            event['error'] = match.group(1)
    
    return event


def analyze_timeline(input_file, output_file):
    """Main analysis function."""
    
    print(f"Loading timeline from {input_file}...")
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    timeline = data.get('unified_timeline', [])
    print(f"Loaded {len(timeline)} records")
    
    # Data structures
    sessions = defaultdict(lambda: {
        'conn_id': None,
        'start_time': None,
        'end_time': None,
        'peer': None,
        'events': [],
        'tables': set(),
        'db_records': 0,
        'frames_rx': 0,
        'frames_tx': 0,
        'cloud_connects': 0,
        'cloud_errors': [],
        'state_transitions': [],
        'close_reason': None
    })
    
    # Global state tracking
    global_events = {
        'mode_changes': [],
        'cloud_errors': [],
        'heartbeats': []
    }
    
    # Track current mode
    current_mode = 'unknown'
    
    # Process each record
    print("Processing records...")
    for i, record in enumerate(timeline):
        source = record.get('source')
        timestamp = record.get('timestamp')
        ts_dt = parse_timestamp(timestamp)
        
        # Process DB records
        if source == 'db':
            conn_id = record.get('conn_id')
            if conn_id:
                sessions[conn_id]['conn_id'] = conn_id
                sessions[conn_id]['db_records'] += 1
                
                table_name = record.get('table_name')
                if table_name:
                    sessions[conn_id]['tables'].add(table_name)
                
                direction = record.get('direction')
                sessions[conn_id]['events'].append({
                    'time': timestamp,
                    'type': 'DB_RECORD',
                    'table': table_name,
                    'direction': direction
                })
        
        # Process Loki logs
        elif source == 'loki':
            log_line = record.get('log_line', '')
            session_id = record.get('session_id')
            
            event = classify_log_event(log_line)
            conn_id = event.get('conn_id') or session_id
            
            if event['type']:
                # Handle session-level events
                if event['type'] in ['BOX_CONNECT', 'BOX_CLOSE', 'CLOUD_CONNECT', 'CLOUD_ERROR']:
                    if conn_id:
                        sessions[conn_id]['conn_id'] = conn_id
                        sessions[conn_id]['events'].append({
                            'time': timestamp,
                            'type': event['type'],
                            **{k: v for k, v in event.items() if k != 'type' and v is not None}
                        })
                        
                        if event['type'] == 'BOX_CONNECT':
                            sessions[conn_id]['start_time'] = timestamp
                            sessions[conn_id]['peer'] = event.get('peer')
                            sessions[conn_id]['state_transitions'].append({
                                'time': timestamp,
                                'from': None,
                                'to': 'CONNECTED'
                            })
                        
                        elif event['type'] == 'BOX_CLOSE':
                            sessions[conn_id]['end_time'] = timestamp
                            sessions[conn_id]['close_reason'] = event.get('error', 'unknown')
                            sessions[conn_id]['frames_rx'] = event.get('frames_rx', 0)
                            sessions[conn_id]['frames_tx'] = event.get('frames_tx', 0)
                            sessions[conn_id]['state_transitions'].append({
                                'time': timestamp,
                                'from': 'CONNECTED',
                                'to': 'CLOSED'
                            })
                        
                        elif event['type'] == 'CLOUD_CONNECT':
                            sessions[conn_id]['cloud_connects'] += 1
                            sessions[conn_id]['state_transitions'].append({
                                'time': timestamp,
                                'from': 'CONNECTED',
                                'to': 'CLOUD_SESSION'
                            })
                        
                        elif event['type'] == 'CLOUD_ERROR':
                            sessions[conn_id]['cloud_errors'].append({
                                'time': timestamp,
                                'error': event.get('error'),
                                'table': event.get('table')
                            })
                
                # Handle global events
                elif event['type'] in ['MODE_OFFLINE', 'MODE_ONLINE', 'MODE_RETRY']:
                    old_mode = current_mode
                    if event['type'] == 'MODE_OFFLINE':
                        current_mode = 'offline'
                    elif event['type'] == 'MODE_ONLINE':
                        current_mode = 'online'
                    elif event['type'] == 'MODE_RETRY':
                        current_mode = 'retry'
                    
                    global_events['mode_changes'].append({
                        'time': timestamp,
                        'from': old_mode,
                        'to': current_mode,
                        'reason': event.get('error')
                    })
                
                elif event['type'] == 'HEARTBEAT':
                    if event.get('mode'):
                        global_events['heartbeats'].append({
                            'time': timestamp,
                            'mode': event['mode']
                        })
        
        if i % 10000 == 0:
            print(f"  Processed {i}/{len(timeline)} records...")
    
    print(f"  Processed {len(timeline)}/{len(timeline)} records")
    
    # Analyze sessions
    print("\nAnalyzing sessions...")
    
    session_list = []
    state_statistics = defaultdict(int)
    transition_statistics = defaultdict(int)
    anomaly_statistics = defaultdict(int)
    
    for conn_id, sess in sessions.items():
        if sess['start_time'] is None:
            continue  # Skip sessions without BOX_CONNECT
        
        # Calculate duration
        duration_sec = None
        if sess['start_time'] and sess['end_time']:
            start_dt = parse_timestamp(sess['start_time'])
            end_dt = parse_timestamp(sess['end_time'])
            if start_dt and end_dt:
                duration_sec = (end_dt - start_dt).total_seconds()
        
        # Identify session type
        session_type = 'short'
        if duration_sec:
            if duration_sec < 5:
                session_type = 'short'
            elif duration_sec < 30:
                session_type = 'medium'
            else:
                session_type = 'long'
        
        # Detect anomalies
        anomalies = []
        
        # No close event
        if sess['end_time'] is None:
            anomalies.append('no_close_event')
            anomaly_statistics['no_close_event'] += 1
        
        # No cloud connect
        if sess['cloud_connects'] == 0 and sess['db_records'] > 0:
            anomalies.append('no_cloud_connect')
            anomaly_statistics['no_cloud_connect'] += 1
        
        # Cloud errors
        if sess['cloud_errors']:
            anomalies.append('cloud_error')
            anomaly_statistics['cloud_error'] += len(sess['cloud_errors'])
        
        # Very short session with data
        if duration_sec and duration_sec < 1 and sess['db_records'] > 0:
            anomalies.append('quick_disconnect')
            anomaly_statistics['quick_disconnect'] += 1
        
        # Count states
        for trans in sess['state_transitions']:
            if trans['to']:
                state_statistics[trans['to']] += 1
            if trans['from'] and trans['to']:
                transition_statistics[f"{trans['from']}->{trans['to']}"] += 1
        
        session_list.append({
            'conn_id': conn_id,
            'start_time': sess['start_time'],
            'end_time': sess['end_time'],
            'duration_sec': duration_sec,
            'peer': sess['peer'],
            'session_type': session_type,
            'tables': list(sess['tables']),
            'db_records': sess['db_records'],
            'frames_rx': sess['frames_rx'],
            'frames_tx': sess['frames_tx'],
            'cloud_connects': sess['cloud_connects'],
            'cloud_errors': len(sess['cloud_errors']),
            'close_reason': sess['close_reason'],
            'anomalies': anomalies,
            'state_transitions': sess['state_transitions']
        })
    
    # Sort by start time
    session_list.sort(key=lambda x: x['start_time'] or '')
    
    # Calculate statistics
    durations = [s['duration_sec'] for s in session_list if s['duration_sec']]
    
    # Mode change analysis
    mode_stats = defaultdict(int)
    for mc in global_events['mode_changes']:
        mode_stats[f"{mc['from']}->{mc['to']}"] += 1
    
    # Build report
    report = {
        'summary': {
            'total_sessions': len(session_list),
            'total_records': len(timeline),
            'sessions_with_anomalies': sum(1 for s in session_list if s['anomalies']),
            'sessions_with_cloud_errors': sum(1 for s in session_list if s['cloud_errors'] > 0)
        },
        'state_statistics': dict(state_statistics),
        'transition_statistics': dict(transition_statistics),
        'anomaly_statistics': dict(anomaly_statistics),
        'mode_changes': {
            'count': len(global_events['mode_changes']),
            'transitions': dict(mode_stats),
            'timeline': global_events['mode_changes'][:50]  # First 50
        },
        'session_duration': {
            'min_sec': min(durations) if durations else None,
            'max_sec': max(durations) if durations else None,
            'avg_sec': sum(durations) / len(durations) if durations else None,
            'distribution': {
                'short_lt5s': sum(1 for d in durations if d < 5),
                'medium_5to30s': sum(1 for d in durations if 5 <= d < 30),
                'long_gt30s': sum(1 for d in durations if d >= 30)
            }
        },
        'identified_states': [
            {'state': 'CONNECTED', 'description': 'BOX TCP connection established'},
            {'state': 'CLOUD_SESSION', 'description': 'Cloud session active for a table'},
            {'state': 'CLOSED', 'description': 'Connection terminated'},
            {'state': 'OFFLINE', 'description': 'Proxy in offline/hybrid mode (no cloud)'},
            {'state': 'ONLINE', 'description': 'Proxy in online mode (cloud connected)'},
            {'state': 'RETRY', 'description': 'Proxy retrying cloud connection'}
        ],
        'sessions_sample': session_list[:100],  # First 100 sessions
        'anomaly_sessions': [s for s in session_list if s['anomalies']][:50]  # First 50 with anomalies
    }
    
    # Save report
    print(f"\nSaving report to {output_file}...")
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n=== ANALYSIS SUMMARY ===")
    print(f"Total sessions: {report['summary']['total_sessions']}")
    print(f"Sessions with anomalies: {report['summary']['sessions_with_anomalies']}")
    print(f"Sessions with cloud errors: {report['summary']['sessions_with_cloud_errors']}")
    print(f"\nState distribution:")
    for state, count in sorted(report['state_statistics'].items()):
        print(f"  {state}: {count}")
    print(f"\nTransition distribution:")
    for trans, count in sorted(report['transition_statistics'].items()):
        print(f"  {trans}: {count}")
    print(f"\nAnomaly distribution:")
    for anomaly, count in sorted(report['anomaly_statistics'].items()):
        print(f"  {anomaly}: {count}")
    print(f"\nMode changes: {report['mode_changes']['count']}")
    for trans, count in sorted(report['mode_changes']['transitions'].items()):
        print(f"  {trans}: {count}")
    print(f"\nDuration distribution:")
    print(f"  Short (<5s): {report['session_duration']['distribution']['short_lt5s']}")
    print(f"  Medium (5-30s): {report['session_duration']['distribution']['medium_5to30s']}")
    print(f"  Long (>30s): {report['session_duration']['distribution']['long_gt30s']}")
    
    return report


if __name__ == '__main__':
    INPUT_FILE = '/Users/martinhorak/Projects/oig-proxy-analysis/unified_timeline.json'
    OUTPUT_FILE = '/Users/martinhorak/Projects/oig-proxy-analysis/.sisyphus/evidence/task-7-states.json'
    
    analyze_timeline(INPUT_FILE, OUTPUT_FILE)
