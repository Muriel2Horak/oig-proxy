{
  "metadata": {
    "task": "task-17-data-adjustment-backlog",
    "generated_at": "2026-02-19T23:48:00Z",
    "based_on_evidence": [
      "task-11-edge-cases.json",
      "task-10-mode-cloud-transitions.json",
      "task-9-signal-timeline.json",
      "task-2-contract-matrix.json",
      "task-2-contract-validation.json"
    ],
    "safe_only": true,
    "no_breaking_changes": true
  },
  "summary": {
    "total_items": 10,
    "by_priority": {
      "high": 3,
      "medium": 4,
      "low": 3
    },
    "by_category": {
      "telemetry_logging": 4,
      "documentation": 3,
      "schema_refinement": 2,
      "configuration": 1
    }
  },
  "items": [
    {
      "id": "DA-001",
      "title": "Add NACK reason telemetry tracking",
      "description": "Capture and publish NACK reason codes (e.g., 'OneMore') to telemetry for monitoring. Currently NACK events are detected but reason tracking is minimal.",
      "evidence_ref": "task-11-edge-cases.json - 27 NACK events all with 'OneMore' reason, but no telemetry tracking of reasons",
      "risk_level": "low",
      "effort_estimate": "2 hours",
      "category": "telemetry_logging",
      "safe_justification": "Additive telemetry only - no changes to protocol handling logic. NACK events already detected, just not logged with reasons.",
      "acceptance_criteria": [
        "NACK frames capture 'Reason' field value",
        "Telemetry metric 'nack_reasons' published with breakdown by reason type",
        "Historical NACK analysis possible via telemetry history"
      ]
    },
    {
      "id": "DA-002",
      "title": "Add cloud gap duration histogram metric",
      "description": "Add telemetry histogram for cloud gap durations to identify threshold tuning opportunities. Current gaps range 301-381s but no visibility into distribution.",
      "evidence_ref": "task-10-mode-cloud-transitions.json - 66 cloud gaps detected ranging from 301-381s, no duration distribution tracking",
      "risk_level": "low",
      "effort_estimate": "2 hours",
      "category": "telemetry_logging",
      "safe_justification": "Additive telemetry metric only - no changes to gap detection logic or thresholds.",
      "acceptance_criteria": [
        "Telemetry publishes 'cloud_gap_duration_ms' histogram",
        "Buckets: <60s, 60-120s, 120-300s, 300-600s, >600s",
        "Visible in telemetry snapshot output"
      ]
    },
    {
      "id": "DA-003",
      "title": "Document cloud response ratio variability",
      "description": "Add documentation explaining observed cloud response ratio variability across connections (0.63-0.982) and monitoring recommendations.",
      "evidence_ref": "task-10-mode-cloud-transitions.json - cloud_response_ratio varies widely by connection, from 0.63 to 0.982",
      "risk_level": "low",
      "effort_estimate": "1 hour",
      "category": "documentation",
      "safe_justification": "Documentation-only change - no code modifications.",
      "acceptance_criteria": [
        "New section in docs/protocol_analysis/cloud_reliability.md",
        "Explains observed response ratio range",
        "Provides monitoring guidance and alert thresholds"
      ]
    },
    {
      "id": "DA-004",
      "title": "Add pairing confidence telemetry",
      "description": "Publish pairing confidence distribution to telemetry to monitor signal pairing quality. Current low confidence rate is ~8.5% (31/367 paired events).",
      "evidence_ref": "task-9-signal-timeline.json - 31 low confidence pairs out of 367 paired events (8.5%), no telemetry tracking",
      "risk_level": "low",
      "effort_estimate": "3 hours",
      "category": "telemetry_logging",
      "safe_justification": "Additive telemetry - pairing logic unchanged. Confidence already calculated, just not published.",
      "acceptance_criteria": [
        "Telemetry publishes 'pairing_confidence' gauge",
        "Counters for high/medium/low confidence pairs",
        "Percentages updated in proxy_status telemetry"
      ]
    },
    {
      "id": "DA-005",
      "title": "Add frame direction telemetry counters",
      "description": "Add counters for frame directions (box_to_proxy, cloud_to_proxy, proxy_to_box) to telemetry. Current data shows imbalance but no monitoring.",
      "evidence_ref": "task-9-signal-timeline.json - distribution shows 1750 cloud_to_proxy, 367 box_to_proxy, 1328 proxy_to_box",
      "risk_level": "low",
      "effort_estimate": "1 hour",
      "category": "telemetry_logging",
      "safe_justification": "Additive telemetry counters only - no protocol changes.",
      "acceptance_criteria": [
        "Three telemetry counters: frames_box_to_proxy, frames_cloud_to_proxy, frames_proxy_to_box",
        "Counters included in daily telemetry snapshot",
        "Ratios calculated and published"
      ]
    },
    {
      "id": "DA-006",
      "title": "Document timing tolerances for signal classes",
      "description": "Document observed timing windows for different signal classes (ACK avg 9.7ms, IsNewSet avg 16.9ms, IsNewWeather avg 19.6ms, settings avg 27.4ms).",
      "evidence_ref": "task-2-contract-matrix.json - detailed timing data for all signal classes with CI_95 and tolerances",
      "risk_level": "low",
      "effort_estimate": "2 hours",
      "category": "documentation",
      "safe_justification": "Documentation-only change - consolidates existing evidence into reference doc.",
      "acceptance_criteria": [
        "New docs/protocol_analysis/signal_timing.md",
        "Tables for each signal class with min/max/avg/std_dev",
        "Observed vs configured tolerance comparison"
      ]
    },
    {
      "id": "DA-007",
      "title": "Add signal class distribution telemetry",
      "description": "Add telemetry counter for each signal class (ACK, IsNewSet, IsNewWeather, IsNewFW, END, NACK) to monitor protocol usage patterns.",
      "evidence_ref": "task-9-signal-timeline.json distributions - 2914 ACK, 238 IsNewSet, 262 END, 17 IsNewWeather, 14 IsNewFW, 0 NACK",
      "risk_level": "low",
      "effort_estimate": "2 hours",
      "category": "telemetry_logging",
      "safe_justification": "Additive telemetry - signal classification already exists, just add counters.",
      "acceptance_criteria": [
        "Separate counters for each signal class",
        "Updated in real-time as frames processed",
        "Included in proxy_status telemetry window"
      ]
    },
    {
      "id": "DA-008",
      "title": "Add optional cloud response ratio threshold config",
      "description": "Add configuration option for cloud response ratio monitoring threshold. Current variability (0.63-0.982) suggests users may want custom alerts.",
      "evidence_ref": "task-10-mode-cloud-transitions.json - wide variability in cloud_response_ratio across connections",
      "risk_level": "low",
      "effort_estimate": "2 hours",
      "category": "configuration",
      "safe_justification": "Optional config addition - default behavior unchanged. Only used if explicitly set.",
      "acceptance_criteria": [
        "New config option 'cloud_response_ratio_min_threshold' (default: 0.7)",
        "Warning logged if ratio falls below threshold",
        "Config documented in README.md"
      ]
    },
    {
      "id": "DA-009",
      "title": "Add END frame frequency telemetry",
      "description": "Track END frame frequency as it's the most common signal (26,932 of 183,331 frames in dataset) and indicates disconnection patterns.",
      "evidence_ref": "task-11-edge-cases.json - 18,334 box disconnect (END frame) events, task-2-contract-matrix.json - 26,932 END frames",
      "risk_level": "low",
      "effort_estimate": "1 hour",
      "category": "telemetry_logging",
      "safe_justification": "Additive telemetry counter - END detection already implemented.",
      "acceptance_criteria": [
        "Counter 'end_frames_received'",
        "Counter 'end_frames_sent'",
        "Time-since-last-END metric published"
      ]
    },
    {
      "id": "DA-010",
      "title": "Document connection lifecycle patterns",
      "description": "Document observed connection lifecycle patterns based on 18598 transitions, including mode transitions and cloud gap recovery behavior.",
      "evidence_ref": "task-10-mode-cloud-transitions.json - 18598 transitions across 9831 online, 166 offline, 7065 hybrid, 1531 hybrid_offline",
      "risk_level": "low",
      "effort_estimate": "3 hours",
      "category": "documentation",
      "safe_justification": "Documentation-only change - summarizes existing evidence.",
      "acceptance_criteria": [
        "New docs/protocol_analysis/connection_lifecycle.md",
        "State diagram of transitions",
        "Statistics on transition frequencies",
        "Recovery pattern documentation"
      ]
    }
  ]
}
