# Learnings: Twin ACK Forensics Verification

## Task 1: Source Inventory
**Date:** 2026-03-08
**Task:** Vymezit analytické okno a inventář zdrojů

### Successful Patterns
1. **Explicit Time Window:** Defined overnight window (00:00-06:00 Europe/Prague) with UTC reference - clear and unambiguous
2. **UTC Normalization Rule:** All downstream tasks must use `ts_epoch_ms` - mixed timezone = invalid analysis
3. **Case ID Convention:** `CASE-{YYYYMMDD}-{SEQ}` format established for cross-referencing across tasks 6-12

### Source Discovery
- **6 Twin-specific test files** identified: `test_twin_ack_correlation.py`, `test_twin_cloud_parity.py`, `test_twin_e2e_roundtrip.py`, `test_twin_integration.py`, `test_twin_poll_delivery.py`, `test_twin_replay_resilience.py`
- **4 key code files**: `proxy.py`, `digital_twin.py`, `twin_transaction.py`, `hybrid_mode.py`
- **Rich reference artifacts**: 10+ existing JSONL/CSV files from `setting-ack-parity` analysis

### Key Insights
1. **Twin vs Setting-ACK Parity:** This analysis builds upon (not duplicates) `setting-ack-parity` - Twin adds INV-1/2/3 session ownership validation
2. **ACK Taxonomy:** Valid Twin ACK must have `Reason=Setting` per `proxy.py:_maybe_handle_twin_ack` - generic ACK is OUT_OF_SCOPE
3. **Timezone Trap:** Mock container logs use CET labeled as `ts_utc` - requires 1-hour correction before canonical normalization

### Technical Notes
- Source inventory created at `analysis/twin-ack-forensics-verification/source-inventory.md`
- Evidence captured in `.sisyphus/evidence/twin-ack-forensics-verification/task-1-inventory.txt`
- QA verification: All 3 grep commands pass

## Task 2: Runtime Snapshot
**Date:** 2026-03-08
**Task:** Runtime snapshot Twin/Proxy konfigurace a módů

### Placement Fix Note (2026-03-08)
Artifacts were initially written to wrong location (main repo) and were copied to correct worktree path:
- Source: `/Users/martinhorak/Projects/oig-proxy/analysis/twin-ack-forensics-verification/`
- Destination: `/Users/martinhorak/Projects/oig-proxy-worktrees/twin-ack-forensics-verification/analysis/twin-ack-forensics-verification/`

### Successful Patterns
1. **Source Provenance Tracking:** Each config value includes source field (env/default) for auditability
2. **UTC Timestamp:** captured_at_utc uses ISO 8601 format with Z suffix per Task 1 convention
3. **Default Value Documentation:** All config defaults from config.py explicitly documented

### Key Insights
1. **Effective Twin State:** `_twin_enabled` in proxy.py is computed as `TWIN_ENABLED AND NOT TWIN_KILL_SWITCH` - both flags must be checked
2. **Config Sources:** All twin/proxy configs use env vars with sensible defaults (False for boolean, "online"/"auto" for modes)
3. **Routing Resolution:** LOCAL_CONTROL_ROUTING has 3 valid values: "auto", "force_twin", "force_cloud"

### Configuration Values Captured
- TWIN_ENABLED: default false, env var
- TWIN_KILL_SWITCH: default false, env var
- TWIN_CLOUD_ALIGNED: default false, env var
- PROXY_MODE: default "online", env var
- LOCAL_CONTROL_ROUTING: default "auto", env var with validation

### Technical Notes
- Runtime snapshot created at `analysis/twin-ack-forensics-verification/runtime-snapshot.json`
- Evidence captured in `.sisyphus/evidence/twin-ack-forensics-verification/task-2-runtime-snapshot.txt`
- QA verification: file exists + JSON schema valid (both PASS)

## Task 3 log export (2026-03-08T18:12:16Z)
- Exported forensic raw JSONL: analysis/twin-ack-forensics-verification/proxy-twin-logs-raw.jsonl
- Exported lifecycle-filtered JSONL: analysis/twin-ack-forensics-verification/proxy-twin-logs-filtered.jsonl
- Filtered marker coverage validated: queue, deliver, ack, timeout, end.
- Raw/filtered records preserve source + ts + raw_line payload fields for audit trail.

## Task 4: Frame-Level Evidence Export (2026-03-08T19:35:00Z)
**Task:** Export frame-level evidence (payload capture / forensic)

### Key Findings
1. **Frame Evidence Already Exists**: `analysis/twin-ack-forensics-verification/frame-evidence.jsonl` contains 238 records
2. **Schema Compliance**: All records have required keys (ts, conn_id, raw_xml, source)
3. **End-to-End Traceability**: 19 complete Setting->ACK->END sequences identified
4. **Source Provenance**: All frames sourced from `task-7-day0-frames.json`

### Frame Distribution
- SETTING frames: 55 (cloud-to-proxy setting commands)
- ACK_SETTING frames: 58 (box-to-proxy ACKs with Reason=Setting)
- END frames: 125 (session termination markers)

### Traceability Verification
Sample sequence (conn_id=1):
1. SETTING @ 2025-12-18T19:26:23.647960+00:00 (cloud sends setting)
2. ACK @ 2025-12-18T19:26:30.208926+00:00 (box acknowledges)
3. END @ 2025-12-18T19:26:47.374688+00:00 (session ends)

### Technical Notes
- Raw XML payloads preserved without transformation
- Chronological ordering maintained
- Connection ID grouping enables session reconstruction
- Evidence file: `.sisyphus/evidence/twin-ack-forensics-verification/task-4-frame-export.txt`

### Downstream Dependencies
Task 4 output blocks:
- Task 6: Canonical timeline normalization
- Task 8: ACK classifier + ambiguity detector  
- Task 9: Frame parity diff against cloud baseline

## Task 5 repair: MQTT timeline re-export (2026-03-08)
- Rebuilt `analysis/twin-ack-forensics-verification/mqtt-timeline.jsonl` strictly from source evidence rows containing both timestamp and `oig_local/...` topic.
- Deterministic hash rule confirmed: `payload_hash = sha256(raw source line bytes).hexdigest()` on the original evidence line bytes.
- Prefer sparse-but-auditable output over inferred lifecycle reconstruction when source-backed MQTT lines are limited.

## Task 6: Canonical timeline normalization (2026-03-08)
- Canonical timeline generated at `analysis/twin-ack-forensics-verification/twin-ack-timeline.jsonl` from Task 1–5 artifacts only (no synthetic external inputs).
- Mandatory schema persisted for every row: `case_id`, `ts`, `ts_epoch_ms`, `source`, `event_type`, `conn_id`, `tx_id`.
- Deterministic IDs pattern that remained stable across reruns:
  - `case_id`: date + connection/source bucket (`CASE-YYYYMMDD-CONN-####` or `CASE-NO_TS-SRC-*`)
  - `tx_id`: frame `ID_Set`/`ID` when available; otherwise SHA1(source:line_no); MQTT uses `payload_hash`.
- UTC normalization rule applied consistently:
  - parseable timestamps converted to ISO UTC (`...Z`) + deterministic `ts_epoch_ms`
  - unparseable/missing timestamps retained as explicit nulls (`ts=null`, `ts_epoch_ms=null`) for auditability.
- Ordering strategy for downstream tasks:
  - primary sort by `ts_epoch_ms` ascending
  - null timestamp rows grouped last, deterministic tie-break by `source` + `tx_id`.

## Task 7: Session ownership matrix (2026-03-08)
- Matrix generated at `analysis/twin-ack-forensics-verification/session-ownership-matrix.csv` with deterministic `case_id` ascending order.
- `tx_id -> conn_id` linkage was reconstructed only from rows that contain both fields; link count stored as `tx_to_conn_links` for auditability.
- `session_id` reconstruction is deterministic and explicitly tiered:
  - `session_conn_{conn_id}` for single-conn cases,
  - hash-based multi-conn session for mixed-conn cases,
  - tx-hash fallback when conn_id is absent,
  - case-hash fallback when both conn_id and tx_id are absent.
- INV verdicts are robust under sparse evidence by preferring `UNKNOWN` over inferred PASS/FAIL when required evidence dimensions are missing.
- FAIL evidence pointer format (`source:line=<n>;tx_id=<id|null>`) proved sufficient for machine and human traceability.


## Task 8 (Worktree Repair): ACK classifier a ambiguity detector
**Date:** 2026-03-08
**Scope:** Active worktree-only regeneration of Task 8 outputs.

### Successful Patterns
1. Deterministic one-row-per-case generation from canonical timeline (`case_id` ascending) produced stable checksum output.
2. Frame evidence integration (`ACK_SETTING`, `END`) improved class coverage over telemetry-only classification.
3. Evidence pointers combining source path + line + tx_id remained audit-friendly at scale.

### Technical Notes
- Output CSV generated at `analysis/twin-ack-forensics-verification/ack-classification.csv` (144 rows).
- Evidence transcript generated at `.sisyphus/evidence/twin-ack-forensics-verification/task-8-ack-taxonomy.txt`.
- Determinism check (two checksums): PASS.


## Task 9 (Worktree Path Repair): Frame parity diff vs cloud baseline
**Date:** 2026-03-08
**Task:** Recreate Task 9 artifacts in active worktree absolute paths

### Successful Patterns
1. Enforcing absolute-path writes in the active worktree eliminated path-mismatch regressions.
2. Deterministic coverage generation from `ack-classification.csv` ensured every non-ACK_OK case has explicit representation.
3. Conservative `no_frame_diff` rows remained audit-safe where baseline/frame parity evidence is not available.

### Technical Notes
- Generated: `/Users/martinhorak/Projects/oig-proxy-worktrees/twin-ack-forensics-verification/analysis/twin-ack-forensics-verification/frame-parity-diff.csv`
- Generated: `/Users/martinhorak/Projects/oig-proxy-worktrees/twin-ack-forensics-verification/.sisyphus/evidence/twin-ack-forensics-verification/task-9-frame-diff.txt`
- Evidence includes `pwd`, schema/domain/coverage checks, and required final command outputs.


## Task 10 (Worktree): State-transition + timing diff
**Date:** 2026-03-08
**Task:** Deterministic per-case transition/timing divergence artifact

### Successful Patterns
1. Using `ack-classification.csv` as canonical case universe guaranteed exact one-row-per-case coverage (144/144) and deterministic `case_id` ascending order.
2. Transition reconstruction remained auditable by deriving chain states only from existing event evidence (`frame_setting`, `frame_ack_setting`, `frame_end`, timeout markers) plus existing Task 7/8 verdicts.
3. Source-backed baseline from conn-bound `ACK_OK` cohort enabled threshold verdicts without fabricated cloud timings.

### Technical Notes
- Generated: `analysis/twin-ack-forensics-verification/state-transition-diff.csv`
- Evidence: `.sisyphus/evidence/twin-ack-forensics-verification/task-10-transition-timing.txt`
- Determinism check: two generation runs + file checksum stability PASS.


## Task 11 (Worktree): First divergence localization + causality chain
**Date:** 2026-03-08
**Task:** Deterministic single-candidate divergence synthesis

### Successful Patterns
1. Deterministic tuple-based tie-break (`has_ts`, `inv_3_verdict`, timeout/setting evidence, earliest ts, ack severity, density, case_id) produced stable run1/run2 winner and identical ranking hash.
2. Using Task 10 + Task 7 as primary signal (transition divergence + INV-3 ownership failure) prevented selecting earlier-but-weaker symptom-only cases.
3. Explicit `conn_id: unknown` handling for null timeline conn preserved auditability and avoided inferred coordinates.

### Technical Notes
- Generated: `analysis/twin-ack-forensics-verification/first-divergence.md`
- Evidence: `.sisyphus/evidence/twin-ack-forensics-verification/task-11-first-divergence.txt`
- Winner (deterministic): `CASE-20260219-SRC-sisyphus-evidence-setting-ack-parity-task-10-mock-context-txt`


## Task 12 (Final Verdict): Verification verdict synthesis
**Date:** 2026-03-08
**Task:** Final verification verdict + next experiments

### Successful Patterns
1. Deterministic reason code catalog (RC-01 through RC-06) gave the verdict clear machine-readable grounding and prevented vague narrative-only claims.
2. Cross-artifact consistency check for the winner case (6 independent attribute checks across 4 CSVs + JSONL) confirmed the primary divergence is internally coherent before committing to the verdict.
3. Conservative `LIMITED_GO` choice was justified by the evidence: real failure patterns exist and are source-backed, but frame-level parity (RC-03) and connection lifecycle (RC-04) are structurally incomplete. Choosing `GO` would have overstated confidence.
4. Confidence scoring by dimension (not a single scalar) produced a more useful output for downstream experiments; it flags exactly which dimensions are weak and why.

### Key Insights
1. The winning primary divergence case is the only case in the corpus with all four of: known timestamp + INV-3 FAIL + setting evidence + timeout evidence. This combination is rare and meaningful.
2. ACK_AMBIGUOUS dominance (80%) is partly an artifact of source mixing (historical f1-ghost-acks and f1-reference-sequences evidence files with no conn_id). This is U-5 and should be re-evaluated when live capture data is available.
3. Frame parity limitation (RC-03) is the single highest-leverage gap for next experiments. Without golden fixtures, frame-level Twin vs Cloud comparison is structurally impossible.

### Technical Notes
- Verdict file: `analysis/twin-ack-forensics-verification/verification-verdict.md`
- Evidence transcript: `.sisyphus/evidence/twin-ack-forensics-verification/task-12-verdict.txt`
- All 5 check categories: PASS (sections, verdict value, reason codes, claim traces, winner consistency)

## F4: Scope fidelity check (2026-03-08)
- Full scope review over plan guardrails + 13 analysis artifacts + 12 task evidence artifacts completed.
- Deterministic scope classification produced `In-scope [26/26] | Out-of-scope [0/26] | PASS`.
- Strong anti-contamination signal: worktree status contains only `.sisyphus/*` paths and no runtime/source/test code modifications.


## F1 Plan Compliance Audit (2026-03-08)
- Compliance snapshot: Must Have 4/4, Must NOT Have 4/4, Tasks 12/12.
- Determinism/reproducibility evidence is strong across Task 8/10/11 reruns; this supports auditability of synthesized conclusions.
- Final-wave context captured explicitly: F1 complete, F2–F4 pending outside F1 scope.

## F3: Real QA Replay (2026-03-08)
- Deterministic scenario-to-evidence mapping completed for all 12 tasks.
- All evidence files present and report PASS status.
- Final output line format verified: `Scenarios [12/12] | Evidence [12/12] | Failures [0] | PASS`
- No failures detected across the entire QA scenario suite.

## Task F2: Data Quality + Reproducibility Review (2026-03-08)
**Task:** Assess reproducibility and data-quality integrity of produced artifacts

### Successful Patterns
1. **Deterministic checksum comparison**: Comparing evidence-recorded SHA256 hashes with current file hashes reliably detects drift
2. **Cross-task count validation**: Verifying dependent counts (e.g., case counts, ACK class distributions) across tasks ensures consistency
3. **Domain validation**: Checking that categorical values are within expected sets catches schema violations early

### Key Findings
1. **Reproducibility is strong**: 2 of 3 tasks with stored checksums (Task 8, Task 10) show perfect reproducibility with identical SHA256 hashes across runs
2. **Drift detection works**: Task 11's first-divergence.md shows drift (evidence hash != current hash), indicating file modification post-evidence capture
3. **Schema compliance is complete**: All CSV headers present, all markdown sections accounted for, all domain values valid

### Technical Notes
- Evidence file: `.sisyphus/evidence/twin-ack-forensics-verification/f2-data-quality-reproducibility.txt`
- Checksums captured for 6 key artifacts
- Row counts validated: 144 cases across ack-classification, state-transition-diff, session-ownership-matrix
- Domain values validated: ack_class ∈ {ACK_OK, ACK_MISSING, ACK_AMBIGUOUS, END_AS_ACK}, inv_verdict ∈ {PASS, FAIL, UNKNOWN}

