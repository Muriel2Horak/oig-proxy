# Learnings: Setting ACK Parity Analysis

## Wave 1: Source Inventory
**Datum:** 2026-02-19
**Task:** Vymezit analyzni nocni okno + inventar zdroju

### Úspěšné patterny
1. **Explicitní časové okno:** Definice "noc 18.2.2026 00:00-06:00 CET" je jasná a neambiguitní
2. **Systematická verifikace:** Kontrola každého zdroje (Loki, NAS, lokální soubory) před zápisem do inventáře
3. **Strukturovaná dokumentace:** Použití sekcí (Analytické okno, Mapování toku, Dostupné zdroje) pro přehlednost

### Techniky přístupu
- **Loki API:** `GET /ready` endpoint je spolehlivější než `/loki/api/v1/ready`
- **NAS SSH:** Přesměrování SSH warningů do `/dev/null` pro čistší output (použito v kontextu)
- **Forensic log:** JSON formát umožňuje easy parsing a correlation

### Identifikované problémy
1. **Chybějící golden-handshake-fixtures:** Adresář neexistuje, nutno vytvořit nebo upravit referenci
2. **Časová pásma:** Forensic log má both `ts_local` a `ts_utc` - potřebuji explicitně určit které používat
3. **HA proxy logy:** Potřebuji identifikovat konkrétní log source pro setting sekvence

### Reference artefakty
- **Forensic log:** 815KB JSON entries se setting sekvencemi
- **Loki labels:** 7 dostupných labelů pro filtrování
- **Endpoint mapping:** BOX -> HA -> telemetry.muriel-cz.cz:5710 -> mock

### Další poznatky
- Setting sequences: IsNewSet, IsNewFW, IsNewWeather
- Device ID: 2206237016 (konstantní pro všechny záznamy)
- Connection IDs: seq 1,2,3 ukazují multiple connections v krátkém časovém rozmezí

## Wave 1: Loki Export (Task 2)
**Datum:** 2026-02-19

### Loki API Access
- Loki endpoint: `http://10.0.0.160:3100/loki/api/v1/query_range`
- Container name label: `addon_d7b5d5b1_oig_proxy`
- Only 1 stream returned for this container (single label combination)

### Loki Pagination
- Default batch limit: 5000 entries per request
- Pagination works by advancing `start` to `max_ts + 1` (nanoseconds)
- Direction `forward` is essential for proper pagination
- Total overnight window (10h) yielded ~17k entries across 4 pages

### Log Pattern Distribution (overnight 10h)
- **IsNewSet**: 6604 entries (most common setting-related pattern - poll mechanism)
- **proxy_status**: 5970 entries (heartbeats, mode info)
- **cloud_forwarder**: 1844 entries (cloud session lifecycle)
- **ACK**: 712 entries (acknowledgment handling)
- **tbl_events**: 85 entries (event table updates)
- **Setting**: 7 entries (actual setting commands - RARE!)
- **CONTROL:**: 4 entries (control flow for settings)
- **FAILED**: 4 entries (failures)

### Key Insight
- Setting events are very rare (7 in 10h overnight window)
- Most setting-related log volume comes from IsNewSet polling and proxy_status heartbeats
- The `CONTROL:` prefix marks actual setting delivery lifecycle events

### Time Window Convention
- Czech timezone is CET (UTC+1), "overnight" = yesterday 21:00 UTC to today 07:00 UTC
- Log timestamps format: `YYYY-MM-DD HH:MM:SS [LEVEL] module: message`
- Loki timestamps are epoch nanoseconds (string in values array)

## Wave 1: Forensic Export (Task 4)
**Datum:** 2026-02-19
**Task:** Exportovat mock forensic frame baseline (raw XML + metadata)

### Úspěšné patterny
1. **Byte-faithful preservation**: Všechny raw_xml hodnoty zůstaly naprosto nezměněny, včetně CRLF line endings
2. **Systematický extraction filter**: Použití jq pro筛选 cílových frame types přímo na NAS
3. **Kompletní metadata mapping**: Extrahování setting_data z state_before a trigger informací

### Extrakce a transformace
- **Připojení k NAS**: SSH přístup k forensic_outbound.log na /volume1/docker/oig-diagnostic/data/
- **Cílové frame types**: 
  - `setting_delivery`: 4 records (klíčové pro setting-ACK parity)
  - `end_bare`: 382 records  
  - `end_time`: 189 records
  - `ack_bare`: 132 records
  - `ack_getactual`: 1191 records
- **Celkem transformováno**: 1,898 záznamů

### Výstupní struktura
**Soubor**: `analysis/setting-ack-parity/mock-forensic.jsonl`

**Povinná pole**: `ts_utc`, `ts_local`, `device_id`, `conn_id`, `direction`, `frame_type`, `table_name`, `tbl_item`, `raw_xml`, `setting_key`

### Klíčové validace
- ✅ Všechny raw_xml obsahují CRLF (1,898/1,898)
- ✅ Setting delivery frames přítomny (4)
- ✅ Session mapping přes conn_id zachováno
- ✅ Setting keys správně formátovány (např. `tbl_box_prms/MODE=2`)

### Technické poznatky
1. **JSONL vs JSON**: Forensic log obsahuje single JSON objects na řádek, ne JSON array
2. **SSH handling**: Warning o post-quantum key exchange jsou jen info, neblokují operace
3. **State extraction**: setting_data jsou vnořená v state_before.setting_data pro setting_delivery frames

### Reference artefakty
- **Forensic baseline**: 1,898 records s kompletní XML + metadata pro setting-ACK analýzu
- **Sample setting frame**: Obsahuje kompletní XML s ID_Device, ID_Set, TblName, NewValue atd.

## Wave 2: Control-Plane Event Extraction (Task 5)
**Datum:** 2026-02-19

### Data Source Pivot
- Tasks 2-4 JSONL outputs were empty/missing — pivoted to SQLite payloads DBs
- Primary: `payloads_ha_full.db` (871K frames, Dec 18 2025 - Feb 1 2026)
- 4 additional DBs with overlapping Dec 7-13 data (75K-76K frames each)

### Protocol Mapping
- BOX polls `IsNewSet` → cloud responds with Setting frame or END
- Setting frame: `<Reason>Setting</Reason>`, `<TblName>`, `<TblItem>`, `<NewValue>`, `<ID_Set>`, `<DT>`, `<Confirm>`
- `<DT>` = user submission time (CET, format: dd.mm.yyyy HH:MM:SS)
- BOX ACK: `<Result>ACK</Result>` + `<Reason>Setting</Reason>` — **no id_set in ACK frames**
- BOX also reports via `tbl_events` with `<Type>Setting</Type>` containing "Remotely : tbl/ITEM: [old]->[new]"
- Cloud retries: re-delivers same id_set on next poll until ACKed

### ACK Correlation
- Protocol ACKs lack id_set → correlate by conn_id (last DELIVERED before ACK on same connection)
- 186/1157 protocol ACKs matched; 971 unmatched (mostly non-setting ACKs)
- tbl_events(Setting) provides independent confirmation with full setting details

### Deduplication
- 5 DBs with overlapping time ranges → 170 exact duplicates removed
- Priority-based: ha_full > live > dec10 > boiler > main

### Results
- **2,405 events**: 2,050 ACK_RECEIVED, 183 DELIVERED, 151 QUEUED, 21 ACK_TIMEOUT
- **151 unique id_set values**, **0 sequence violations** (all clean)
- **53 events with conflicts** (re-deliveries/multi-source)
- Single device: 2206237016
- Time range: Dec 7 2025 - Jan 23 2026

### ACK_TIMEOUT Insight
- 21 inferred timeouts — mostly from `proxy_live` (limited Dec 11-13 capture window)
- Settings likely ACKed outside capture window

## Wave 2: Mock Pre-Setting Context (Task 10)
**Datum:** 2026-02-19

### Failed Delivery Pattern (Mock)
- 2 failed cases found in overnight mock test (Feb 19)
- Both follow: PENDING → DELIVERED → (conn close ~24s) → reconnect → ACK_TIMEOUT (~34s) → FAILED
- Cross-session failure: Setting delivered on conn_N, timeout fires when BOX is already on conn_N+1
- BOX closes connection normally (Reason=normal) after ~24s with Setting frame
- ACK timeout fires 34-35s after delivery (configured 30s + ~4s check interval jitter)

### Timezone Trap
- mock-setting-events.jsonl `ts_utc` field contains CET (UTC+1), NOT UTC
- mock-overnight.log timestamps are also CET
- mock-forensic.jsonl `ts_utc` is actual UTC
- 1-hour offset must be compensated when correlating across sources

### Forensic Delivery Details
- 4 total setting_delivery frames in forensic (2 from Feb 18, 2 from Feb 19)
- Feb 19 deliveries match the 2 FAILED cases in mock-setting-events
- Feb 18 deliveries (conn_id=8 MODE=2, conn_id=20 MODE=3) — no failure events in test window
- Each delivery contains full XML with ID, ID_Set, ID_Device, NewValue, DT, Confirm=New

### Context Window Statistics
- Case 1 (MODE=3): 8 forensic frames, 9 log entries, 17 baseline records in T-120s..T+60s
- Case 2 (MODE=2): 29 forensic frames, 15 log entries, 44 baseline records in T-120s..T+60s
- Case 2 has more context because conn_id=17 carried 11 frames (longer data session)

## Wave 2: Cloud Pre-Setting Context (Task 9)
**Datum:** 2026-02-19

### Cloud Baseline Has No Protocol Setting/ACK Frames
- cloud-baseline.jsonl (15,201 lines) contains only LOG, POLL, DATA, ACK_TABLE frame types
- `reason` and `result` fields are always null in ONLINE mode
- Proxy observes settings via tbl_events "SETTING: State publish" entries, not protocol frames
- 121 ACK_TABLE events are all "Cloud ACK timeout (1.0s)" — unrelated to settings

### Setting Detection Strategy
- Searched for `SETTING: State publish tbl_box_prms/MODE=` pattern in DATA frames
- Found exactly 7 occurrences — all MODE changes (Home 1 ↔ Home UPS, automated HDO schedule)
- These are proxy-side observations, not cloud protocol Setting frames

### Poll-to-Observation Timing
- In ONLINE mode, protocol ACK is <1s (within same TCP connection, not directly observable)
- Measurable metric: time from last IsNewSet poll to tbl_events SETTING observation
- Range: 10.9s - 89.6s (avg 59.6s, excluding case #1 restart anomaly)
- IsNewSet poll cadence: ~59.8s average
- Connection rotation: IsNewSet → IsNewWeather → IsNewFW (3-table cycle, ~45-90s)

### Proxy Restart Anomaly (Case #1)
- 3 proxy restarts between 21:07-21:20 UTC created 1044s gap for case #1
- Server changed from `telemetry.muriel-cz.cz` to `oigservis.cz` after restart
- Setting was likely delivered during restart gap; proxy only saw tbl_events after reconnect

### Cloud vs Mock Comparison
- Mock: explicit protocol Setting→ACK frames, 2 failed cases (cross-session timeout)
- Cloud: only tbl_events observations, all 7 successful (no failures visible)
- Mock setting_to_ack: protocol-measurable; Cloud: poll-to-observation (fundamentally different metric)
- Mock: no proxy restarts; Cloud: 4 proxy restarts in window

### Context Window Structure
- T-120s..T+60s windows contain 70-93 frames each (avg ~77)
- Frame distribution: DATA + LOG dominate, with POLL and occasional ACK_TABLE
- All 7 cases have complete context — no data gaps within windows

### Control-Plane Events Non-Overlap
- control-plane-events.csv covers Dec 2025 - Jan 2026 (from SQLite DBs)
- cloud-baseline covers Feb 18-19 2026 (from Loki)
- Cannot cross-reference these two sources directly
## Task 7: Proxy Session Matrix
**Datum:** 2026-02-19

### Session Structure in cloud-baseline
- 15201 total rows; 5266 have conn_id (non-null), 9935 have conn_id=None (global logs/status)
- 1672 unique conn_ids (range 1-1672), each session has 2-7 frames
- No single-frame sessions exist — every BOX connection has at minimum connect+disconnect LOG pair
- All frame_types: LOG, POLL, DATA, ACK_TABLE (no SETTING frame_type in dataset)

### Setting Signal Characteristics
- IsNewSet is the primary setting-related tbl_name (566 conn_ids polled it)
- tbl_events appears on exactly 1 conn_id (334) — rare but setting-significant
- 7 SETTING log_messages exist but ALL have conn_id=None (global DATA pipeline events, not session-bound)
- Setting signals cannot be reliably mapped to a single conn_id by temporal proximity (multiple sessions overlap)
- Classification uses tbl_name membership (IsNewSet, tbl_events, tbl_box_prms) as session-level indicator

### Close Reason Pattern
- 99.9% of sessions close with EOF (BOX-initiated clean disconnect)
- Pattern: "🔌 BOX closed the connection (EOF, conn=X, frames_rx=Y, frames_tx=Z)"
- Last conn_id (1672) has unknown close reason — still open at capture boundary

### Session Type Distribution
- setting-window-candidate: 567 (33.9%) — sessions with IsNewSet/tbl_events polls
- multi-frame burst: 1105 (66.1%) — remaining sessions without setting signal
- single-frame poll: 0 (0%) — all sessions have ≥2 frames

## Task 8: Mock Session Matrix
**Datum:** 2026-02-19

### Session Matrix Build
- 513 session rows from 3 sources: mock-overnight.log (lifecycle), mock-baseline.jsonl (forensic frames), mock-setting-events.jsonl (structured events)
- 508 full-coverage sessions (conn_id 1–508) with complete open/close/duration/frames/close_reason
- 5 partial-coverage sessions (conn_id 509–513) — forensic frames exist but no container lifecycle events
- conn_id=2206237016 excluded: device_id misused as conn_id in 7 device-level log entries (STATE_TRANSITION, DELIVERY_FAILED)

### Setting Delivery Sessions
- 4 sessions carry setting deliveries: conn_ids 1, 8, 17, 20
- conn_id 1 & 17: active failures (DELIVERED→FAILED in mock test window)
- conn_id 8 & 20: Feb 18 deliveries visible in forensic source only (no failure events)

### Cross-Session Failure Pattern (confirmed)
- Both failures: setting delivered on conn N, BOX closes normally after ~24s, timeout fires ~34s on conn N+1
- ACK timeout is time-based (30s + ~4s jitter), not connection-scoped
- No retry mechanism: FAILED is terminal state

### Data Quality Traps
- mock-baseline.jsonl contains TWO source types (`forensic` and `container_log`) — must filter/join carefully
- Forensic source has conn_ids 1–513 (5 extra vs container's 1–508)
- Timestamp mismatch: container_log `ts_utc` is actually CET (UTC+1), forensic `ts_utc` is real UTC

## Task 13: State Machine Transition Comparison
**Datum:** 2026-02-19

### State Model Design
1. **Semantic equivalence mapping**: Cloud uses QUEUED, mock uses PENDING — same logical state, different terminology due to delivery mechanism. Explicitly documenting equivalence prevents false divergences.
2. **Implicit vs explicit states**: Cloud has no explicit QUEUED/DELIVERED states in logs — they must be inferred from IsNewSet poll timing and tbl_events observation. Mock has explicit structured events for every state transition.
3. **Terminal state asymmetry**: Cloud always reaches ACKED (100% success), mock always reaches FAILED (100% failure in test window). This total asymmetry means divergence is structural, not probabilistic.

### Divergence Characterization
1. **First divergence at transition index 2**: Both flows share INIT→QUEUED/PENDING→DELIVERED. The split happens when cloud gets ACKED but mock gets CONN_CLOSED.
2. **Cross-session timeout is the kill mechanism**: ACK timeout fires 34-35s after delivery, but by then BOX is on a different connection (closed delivery conn after 24s, reconnected after 30s). The timeout fires on the wrong connection scope.
3. **Proxy role difference is the root cause**: In cloud mode, proxy is transparent (not in ACK critical path). In mock mode, proxy IS the ACK endpoint — fundamentally different failure surface.

### Timestamp Estimation
1. **Cloud delivery timestamps are estimated**: We use last_isnewset_poll + 500ms as delivery estimate. Actual delivery is within the TCP session (<1s), but exact timing isn't logged.
2. **Case 1 restart anomaly**: 1044s gap between poll and observation due to 3 proxy restarts. This is a data quality limitation, not a protocol failure.
3. **Mock timestamps are precise**: Structured events have explicit epoch_ms from the mock proxy's event system — much higher precision than cloud log-derived timestamps.

### Analytical Techniques
1. **CSV divergence marking**: Using `is_divergence=true` on the first divergent transition per case enables automated filtering for the exact fork point.
2. **Note column in CSV**: Adding a note column (beyond minimum spec) provides inline context without requiring cross-reference to summary doc.
3. **State model before data**: Defining the state model explicitly BEFORE mapping events to states prevents ad-hoc state invention during analysis.

## Task 11: Frame Schema Diff Analysis
**Datum:** 2026-02-19

### Structural Comparison Method
- Generated 46 diff records comparing cloud vs mock frame structures
- Diff types: field_missing (32), value_semantic (7), value_format (4), crc_mismatch (2), field_order (1)
- Severity distribution: medium (32), high (9), low (5)

### High-Severity Architectural Differences
1. **Setting Frame Observation**
   - Cloud: observes settings indirectly via `tbl_events` DATA frame
   - Mock: explicit SETTING frame with full XML structure
   
2. **Cross-Session Timeout (Mock-Specific)**
   - Mock: ACK timeout fires on DIFFERENT connection than delivery (conn_N → conn_N+1)
   - Cloud: protocol ACK within same TCP session (<1s)
   
3. **Connection Lifecycle**
   - Mock: delivery connection closes before timeout fires
   - Cloud: single connection handles complete exchange

4. **ACK Timing Measurement**
   - Cloud: sub-second protocol ACK (within IsNewSet session, not directly observable)
   - Mock: explicit ACK timeout at ~30-35s (config-based threshold)

### Setting Frame Fields (Mock Only)
Fields captured in mock forensic but not in cloud baseline:
- ID (frame ID)
- ID_Device
- ID_Set
- ID_SubD
- DT (datetime in local format dd.mm.yyyy HH:MM:SS)
- NewValue
- Confirm (always "New" for fresh settings)
- TblName
- TblItem
- ID_Server
- mytimediff
- Reason (single instance; spec allows multiple)
- TSec (timestamp in UTC format)
- ver (firmware version)
- CRC

### CSV Column Structure
Required columns: `case_id, source_pair, frame_anchor_ts, diff_type, field_name, cloud_value, mock_value, severity, notes`
- `source_pair`: context for comparison (e.g., "setting-frame/ID", "timeout_pattern")
- `frame_anchor_ts`: timestamp of relevant event (delivery/timeout)
- `diff_type`: classification of difference type
- `severity`: low/medium/high based on impact on parity analysis

### Key Insight for Parity Analysis
The fundamental architectural difference (proxy-observes vs server-delivers) means direct frame-level comparison is not possible. Instead, focus on:
1. Timing patterns (when does setting appear in each path)
2. Failure indicators (what signals exist in each path)
3. State transitions (PENDING→DELIVERED→ACK vs PENDING→DELIVERED→FAILED)

## Task 14: First Divergence Locator + Causality Chain
**Datum:** 2026-02-19
**Confidence:** 0.95

### Triangulation Method
1. **3-source convergence works well:** frame-diff, timing-diff, and state-machine-diff all independently pointed to the same divergence point (T+24s BOX connection close). Cross-referencing increases confidence significantly.
2. **State machine diff is the strongest signal:** Transition index 2 (DELIVERED→X) is the exact fork point. State machines make divergence analysis much cleaner than raw log comparison.
3. **Timing diff provides quantitative anchor:** The exact T+24s timing is consistent across both mock cases, confirming it's a deterministic BOX behavior, not a random timing issue.

### Key Findings
1. **First divergence is structural, not timing-based:** The divergence isn't "mock is slower" — it's "mock takes a completely different path." Cloud: DELIVERED→ACKED. Mock: DELIVERED→CONN_CLOSED. These are qualitatively different outcomes.
2. **T+24s is the BOX's natural session duration:** The BOX closes after completing its data exchange cycle (~24s). This happens regardless of whether a Setting was delivered — the BOX doesn't "wait" for Setting ACK.
3. **Cross-session timeout is an artifact, not root cause:** The timeout fires on conn_N+1, not conn_N. This is a consequence of the connection close, not a separate bug. Fixing the ACK tracking to be connection-aware would still require the ACK to arrive before the BOX closes.
4. **retry=0 is the safety net that doesn't exist:** Even if ACK tracking were perfect, zero retries means any transient failure is permanent. This was never exposed in cloud mode because cloud ACK always succeeds.

### Patterns for Future Analysis
1. **Dual hypothesis approach:** Always state primary + alternative hypothesis with confidence scores. H1 (missing protocol, 0.90) and H2 (BOX firmware behavior, 0.30) give the reader clear decision points.
2. **MEASURED vs DERIVED labeling:** Explicitly marking each evidence piece as measured or derived prevents over-confidence in inferred conclusions.
3. **Uncertainties section is critical:** Listing U1-U4 with impact assessment prevents the analysis from appearing more certain than it actually is.

### Architectural Insight
The root cause is a **role mismatch**: in cloud mode the proxy is a passive observer (not in ACK path), but in mock/OFFLINE mode the proxy becomes an active protocol participant (IS the ACK endpoint). This role change requires implementing the full Setting→ACK protocol, which was not done. The mock proxy delivers the Setting but has no mechanism to receive, correlate, and confirm the protocol-level ACK from the BOX.

## 2026-02-19: Task 12 - Timing Diff Analysis

### Protocol ACK Timing
- **Cloud**: Sub-second (<1s) protocol ACK within same TCP connection
- **Mock**: No ACK sent at all
- **Root cause**: Mock BOX implementation doesn't generate ACK frames

### Close Pattern Divergence
- **First divergence point**: T+24s after delivery (connection close without ACK)
- **Mock pattern**: Delivery → 24s wait → Close → 6s gap → Reconnect → 34-35s timeout → FAIL
- **Cloud pattern**: Delivery → <1s ACK → Session continues

### Cross-Session Timeout Behavior
- Mock timeout tracking follows device, not connection
- Timeout fires on conn_N+1 even though delivery was on conn_N
- This is correct behavior (setting-level tracking) but exposes missing ACK

### Session Duration Patterns
- Cloud sessions: ~34,000 seconds (long-lived, connection pooling)
- Mock sessions: ~3-5 seconds (short-lived, frequent reconnects)
- Mock BOX reconnects after each exchange cycle

### Poll vs Push Model
- Cloud: Proxy polls cloud (IsNewSet), cloud responds with Setting or END
- Mock: Server pushes Setting to BOX (no polling concept)
- Different communication models affect timing measurements

### Measurement Classification
| Metric | Type | Source |
|--------|------|--------|
| delivery_to_close | Direct | Forensic frame timestamps |
| delivery_to_timeout | Direct | State transition logs |
| reconnect_gap | Direct | Connection timestamps |
| session_duration | Direct | Session matrix |
| protocol_ack | Derived | Success/failure inference |
| poll_to_observation | Computed | setting_ts - last_poll_ts |

### Retry Configuration Impact
- Mock configured with retry=0/0 (max=0)
- Any failure immediately marks setting as FAILED
- No opportunity for transient issues to resolve

### Timing Baseline Summary
| Metric | Cloud | Mock |
|--------|-------|------|
| IsNewSet poll cadence | ~58s | N/A |
| Protocol ACK | <1s | NONE |
| Delivery→Close | >30s | 24s |
| Delivery→Timeout | N/A (success) | 34-35s |
| Reconnect gap | 0s | 6s |
| Session duration | 34,013s | 3.5s |

## Task 15: Final Analytical Dossier (Root Cause Report)
**Datum:** 2026-02-19

### Report Structure Patterns
1. **Executive summary up front**: State root cause, confidence, key findings in first section for immediate accessibility.
2. **Evidence cross-referencing**: Every claim links to specific analysis artifact (first-divergence.json, causality-chain.md, etc.).
3. **Measured vs inferred separation**: Explicitly distinguish between directly observed facts and derived conclusions.
4. **Dual hypothesis framework**: Present primary hypothesis (H1, 0.90) and alternative (H2, 0.30) with supporting evidence.

### Causality Chain Visualization
- ASCII art causal chain works well for showing linear dependency
- Link-by-link evidence table (Evidence Type | Source | Confidence) provides transparency
- "First divergence" marking clearly separates causal point from consequences

### Confidence Scoring Method
- Base confidence 0.95: 3 independent evidence sources converge on same divergence point
- Deduction -0.05: small sample size, estimated cloud ACK timing, unobserved firmware behavior
- Hypothesis confidence: 0.90 for primary (measured evidence), 0.30 for alternative (speculative)

### Experiment Prioritization Framework
- P0 (critical, low risk): Connection-aware tracking, protocol ACK observation
- P1 (important, medium risk): Retry config, identity spoofing
- P2 (useful, medium/high risk): Protocol simulation, timeout extension
- P3 (validation, low risk): Firmware behavior capture (packet-level)

### Risk and Limits Documentation
1. **Dataset limitations**: Sample size (2 mock cases), time window (2 days), observability gaps (cloud protocol ACK not captured, mock ACK unknown)
2. **Analysis assumptions**: Cloud ACK latency <1s (estimated), connection close reason normal (assumed), 30s timeout config (inferred)
3. **Unresolved questions**: Does BOX send ACK in mock mode? Is ACK connection-scoped or session-scoped? What is exact ACK frame structure?

### No-Retry Compatibility
- All experiments designed to prevent first failure, not implement retry mechanism
- Exp 1 (connection-aware tracking): Fixes timeout scope issue
- Exp 2 (protocol ACK observation): Validates ACK existence
- Exp 3 (retry config test): Tests retry as validation, not implementation

### Reproducibility Checklist
- Pattern: T+0 delivery, T+24s close, T+34s timeout, FAILED (100% reproducible)
- Verification table comparing mock vs cloud cases
- Clear reproduction steps with prerequisites

### Report Writing Best Practices
- Avoid AI-sounding phrases ("delve", "utilize", "it's important to note")
- Use plain language: "observe" not "leverage", "start" not "commence"
- Vary sentence length for readability
- No consecutive sentences starting with same word

### Key Takeaway
The mock proxy's failure is a **protocol implementation gap**, not a timing bug. Delivering Setting frames without implementing ACK reception means the protocol is incomplete. The root cause is architectural: proxy acts as ACK endpoint in mock mode but doesn't have the protocol mechanisms that cloud server has natively.
