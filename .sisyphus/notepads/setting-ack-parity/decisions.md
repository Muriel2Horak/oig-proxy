# Decisions - Setting ACK Parity Analysis

## Task 2: Export Loki Logs

### Time Window
- Chose yesterday 21:00 UTC to today 07:00 UTC (10h window)
- Rationale: Covers Czech night (22:00-08:00 CET), includes connection transitions

### Filter Patterns
- Used broad regex: Setting|DELIVERED|ACK|FAILED|QUEUED|CONTROL:|cloud_forwarder|proxy_status|IsNewSet|tbl_events|control_settings|control_api|control_pipeline
- Rationale: Cast wide net to capture full setting lifecycle context, not just the word "Setting"
- This captures pre-setting context (proxy_status, cloud_forwarder) as required

### Export Script
- Created `export_loki.py` as reusable extraction tool
- Paginated fetching handles arbitrary window sizes
- Raw export preserves original Loki structure + adds metadata envelope
- JSONL format chosen for filtered events (easy streaming/grep, one record per line)

## Task 5: Control-Plane Event Extraction

### D1: Use payloads DBs instead of JSONL files
- Plan assumed JSONL outputs from Tasks 2-4, but those files are empty/missing
- SQLite payloads DBs contain all captured frames with full raw XML — richer data source
- Decision: Extract directly from DBs, skip JSONL dependency

### D2: QUEUED events inferred from DT field
- Cloud doesn't explicitly log queue time in our captures
- `<DT>` field in Setting frame = user submission time (CET timezone)
- Convert CET→UTC, use as QUEUED timestamp if earlier than DELIVERED timestamp
- Fallback: use DELIVERED timestamp if DT missing or not earlier

### D3: ACK correlation by conn_id (temporal)
- BOX ACK frames lack id_set/setting_key fields
- Correlate by finding most recent DELIVERED on same conn_id before ACK timestamp
- 186/1157 protocol ACKs correlated; rest are non-setting ACKs
- tbl_events(Setting) provides independent ACK confirmation with full details

### D4: ACK_TIMEOUT = inferred, not observed
- No explicit timeout events in proxy captures
- Infer: DELIVERED id_set with no matching ACK_RECEIVED or FAILED = ACK_TIMEOUT
- Marked with `conflict=inferred_timeout` for transparency (21 found)

### D5: Dedup priority order
- proxy_ha_full > proxy_live > proxy_dec10 > proxy_boiler > proxy_main
- Based on capture completeness and time coverage

### D6: Include tbl_events(Setting) as ACK_RECEIVED source
- BOX reports applied settings via tbl_events with Type=Setting
- Contains change details with old→new values
- Marked with confirm=Applied to distinguish from protocol ACKs
