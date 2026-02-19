# Learnings - OIG Protocol 3-Day Passive Analysis

## [2026-02-19] Session Initialized
- Plan created and Momus-reviewed
- 20 main tasks + 4 final verification tasks
- Critical path: 1 -> 4 -> 9 -> 14 -> 16 -> 19
- Data source: proxy capture + telemetry only (no pcap)
- Historical baseline: analysis/ha_snapshot/payloads_ha_full.db (871K frames)

## Key Architecture References
- Frame capture: `addon/oig-proxy/utils.py` (frames table schema)
- Signal handling: `addon/oig-proxy/proxy.py`, `cloud_forwarder.py`, `hybrid_mode.py`
- Telemetry: `addon/oig-proxy/telemetry_collector.py`, `telemetry_client.py`

## Constraints
- NO active interference in live communication
- Passive-only data collection

## [2026-02-19] Task 1: Runtime Capture and Telemetry Baseline
**Created baseline snapshot scripts for capturing current runtime settings:**

- `baseline_snapshot.py`: Reads config.json and config.py to extract runtime settings
- `validate_baseline.py`: Validates JSON output for required keys and schema

**Current Baseline Settings:**
- capture_payloads: false (disabled by default)
- capture_raw_bytes: false (disabled by default)
- capture_retention_days: 7 (days)
- proxy_mode: hybrid
- telemetry_enabled: false (disabled in config.py default)
- telemetry_broker: telemetry.muriel-cz.cz:1883

**Config Structure Learnings:**
- Config files are in `addon/oig-proxy/` directory
- `config.json` contains addon option defaults and schema
- `config.py` defines runtime constants with env var defaults
- Capture settings are boolean flags in config.json options
- Proxy mode is a string (online/hybrid/offline)
- Telemetry settings are constants defined in config.py

**Evidence Artifact:**
- `.sisyphus/evidence/task-1-baseline.json` - machine-readable baseline snapshot

## [2026-02-19] Task 3: Canonical OIG Signal Taxonomy

### Signal Classes Identified

**Polling Signals (box_to_proxy):**
- `IsNewSet`: Setting update polling signal. Carries <TblName>tbl_actual</TblName> but logical table is Result value. Used to check for new settings from cloud.
- `IsNewWeather`: Weather data polling signal. Same structure as IsNewSet, checks for weather updates.
- `IsNewFW`: Firmware update polling signal. Same structure as IsNewSet, checks for firmware updates.

**Control Frames:**
- `END`: End-of-transmission frame. BOX sends END with local/UTC time and GetActual. Cloud responds with ACK (not "no response" as historical data suggested).
- `ACK`: Acknowledgment from cloud or generated locally. Standard response to data table frames (tbl_*) and END frames.
- `NACK`: Negative acknowledgment (conceptual, not observed in codebase). Included for taxonomy completeness.

### Key Protocol Behaviors

**IsNew* Signal Handling:**
- Lines 459-465 in proxy.py: Table override - when Result is IsNewSet/IsNewWeather/IsNewFW, override table_name from tbl_actual to the Result value
- Lines 460-492 in cloud_forwarder.py: If pending Setting frame exists, deliver it as response to any IsNew* poll (protocol requirement)
- Lines 433-439 in cloud_forwarder.py: Track telemetry (last_response, last_rtt_ms) for IsNew* signals

**Mode-Specific Responses (from oig_frame.py:118-126):**
- `END`: Returns END with time + UTC + GetActual (build_end_time_frame)
- `IsNewSet`: Returns END with time + UTC + GetActual
- `IsNewWeather`: Returns plain END without time/GetActual
- `IsNewFW`: Returns plain END without time/GetActual
- Others: Returns plain ACK

### Directionality

| Signal | Direction | Description |
|---------|------------|-------------|
| IsNewSet/IsNewWeather/IsNewFW | box_to_proxy | Polling queries from BOX |
| END | bidirectional | BOX sends END, cloud responds with ACK |
| ACK | cloud_to_proxy_or_local | Acknowledgments from cloud or generated locally |
| NACK | cloud_to_proxy_or_local | Error responses (not observed) |

### Files Created

- `scripts/protocol_analysis/build_signal_taxonomy.py`: Generates taxonomy from codebase analysis
- `scripts/protocol_analysis/validate_taxonomy.py`: Validates taxonomy structure and required fields
- `.sisyphus/evidence/task-3-signal-taxonomy.json`: Canonical taxonomy JSON

### Validation Results

- ✅ All required signal classes present (IsNewSet, IsNewWeather, IsNewFW, END, ACK, NACK)
- ✅ No duplicate canonical keys
- ✅ All required fields present for each signal
- ✅ Valid direction values
- ✅ Valid payload variants
- ✅ All mode_notes entries present (online, hybrid, offline)

### Dependencies on This Task

This task BLOCKS: Tasks 9, 13, 14

## [2026-02-19] Task 2: Capture Schema Audit

### Schema Structure
- Frames table columns: id, ts, device_id, table_name, raw, raw_b64, parsed, direction, conn_id, peer, length
- Direction values observed: box_to_proxy (315K), cloud_to_proxy (281K), proxy_to_box (275K)
- 29 unique tables: tbl_actual (276K), GetActual (275K), ACK (51K), END (27K), etc.

### Data Quality Observations
- device_id null rate: 70.37% - Expected behavior (ACK/END control frames lack device_id)
- ts, table_name, direction: 0% null rate (excellent completeness)
- Two device_ids in historical data: "2206237016" and "0000000000"
- 18,334 connections, average 47.56 frames per connection
- Time coverage: 2025-12-18 to 2026-02-01 (45 days)

### Audit Script Features
- Graceful handling of missing frames table (returns FAIL status with error message)
- Calculates null rates for critical columns
- Provides direction and table distribution summaries
- Timestamp range extraction for time coverage analysis
- Handles empty database edge case

### Implementation Notes
- Used SQLite PRAGMA table_info for schema introspection
- Aggregate queries with GROUP BY for distribution analysis
- JSON output format for easy integration with other tools
- Assertion script configurable for different thresholds

## Task 5: Evidence Manifest and Naming Convention - 2026-02-19

### Evidence Naming Convention
Standard format: `task-{N}-{scenario-slug}.{ext}`
- `N`: Task number (1-20, F1-F4)
- `scenario-slug`: Lowercase-hyphenated description
- `ext`: File extension (json, log, md, etc.)

### Implementation
- `scripts/protocol_analysis/generate_evidence_manifest.py`: Parses plan file to extract all expected evidence artifacts
- `scripts/protocol_analysis/validate_evidence_manifest.py`: Validates manifest for duplicates, naming violations, completeness
- Manifest location: `.sisyphus/evidence/task-5-evidence-manifest.json`

### Validation Rules
1. Duplicate detection: Rejects manifests with duplicate filenames
2. Naming convention: Enforces `task-{N}-{slug}.{ext}` pattern
3. Completeness: Reports which artifacts exist vs pending
4. Informational vs errors: Missing files are info, duplicates/naming violations are errors

### Usage
```bash
# Generate manifest from plan
python scripts/protocol_analysis/generate_evidence_manifest.py \
  --plan .sisyphus/plans/oig-protocol-3day-passive-analysis.md \
  --out .sisyphus/evidence/task-5-evidence-manifest.json

# Validate manifest (exits 0 if valid)
python scripts/protocol_analysis/validate_evidence_manifest.py \
  --manifest .sisyphus/evidence/task-5-evidence-manifest.json \
  --plan .sisyphus/plans/oig-protocol-3day-passive-analysis.md
```
