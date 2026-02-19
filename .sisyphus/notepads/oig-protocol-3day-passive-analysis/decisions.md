# Decisions - OIG Protocol 3-Day Passive Analysis

## [2026-02-19] Planning Decisions
- Selected "Proxy+telemetry only" for 3-day data collection
- Signal focus: IsNewSet, IsNewWeather, IsNewFW, END, ACK/NACK
- Target confidence: >= 0.85 overall, no dimension below 0.70
- Target signal coverage: >= 10 instances per class

## [2026-02-19] Passive Guardrail Requirements (Task 6)
- SAFETY GATE: Preflight checker must exit 0 before any data collection
- Forbidden config: `CONTROL_MQTT_ENABLED=true` (active settings injection)
- Forbidden config: `FORCE_OFFLINE=true` (may interfere with normal communication)
- Forbidden scripts: Any containing probe/scan/inject/fuzz (active probing)
- Safe scripts excluded: `replay_from_db.py` (proxy feature, not active test)
- Verification method: `check_passive_guardrails.py --out <path>.json`
- Required JSON fields: passive_mode, active_probe_detected, forbidden_actions
- Exit codes: 0 = pass (safe to collect), 1 = fail (safety gate triggered)
- User constraint: "bez zasahu do live komunikace" (no interference in live communication)
- Data source: "Proxy+telemetry only" - no pcap sniffing, no active probing
