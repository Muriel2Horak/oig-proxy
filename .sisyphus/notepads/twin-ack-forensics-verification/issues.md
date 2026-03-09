# Issues: Twin ACK Forensics Verification

## Task 1: Source Inventory
**Date:** 2026-03-08
**Task:** Vymezit analytické okno a inventář zdrojů

### Identified Issues / Open Questions

1. **Runtime Source Availability Unknown**
   - Loki logs (HA): Not verified if accessible from analysis context
   - payloads.db: Not verified if exists in current deployment
   - MQTT twin_state: Not verified if available
   - **Impact:** May need to pivot to pure test-based analysis if runtime sources unavailable

2. **Timezone Ambiguity in Mock Logs**
   - Mock container logs use CET labeled as `ts_utc` 
   - Requires explicit correction: `ts_epoch_ms - 3600000`
   - **Mitigation:** Documented in TIMEZONE_RULES section

3. **Reference Artifact Time Window Mismatch**
   - Setting-ACK parity artifacts from Feb 2026
   - Current analysis window is March 2026
   - **Impact:** Reference artifacts are historical baselines, not current state

4. **LSP Import Errors in Test Files**
   - Multiple import resolution errors in test files (digital_twin, twin_state, twin_transaction)
   - **Status:** Pre-existing in worktree, not caused by this task
   - **Impact:** None for inventory task - tests are in-scope as evidence sources

### Risk Assessment
| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Runtime sources unavailable | Medium | Low | Use test-based analysis |
| Timezone parsing errors | Low | High | Explicit correction rule documented |
| Test import failures | Low | Low | Code files still referenceable |

### Next Steps (Blockers for Later Tasks)
- Task 6 (canonical normalization) depends on this inventory
- No blockers identified for proceeding to Wave 1 tasks 2-5

## Task 2: Runtime Snapshot
**Date:** 2026-03-08
**Task:** Runtime snapshot Twin/Proxy konfigurace a módů

### Placement Fix Note (2026-03-08)
Artifacts were copied from wrong location (main repo) to correct worktree:
- `runtime-snapshot.json`: copied from main repo analysis dir
- `task-2-runtime-snapshot.txt`: copied from main repo evidence dir

### Identified Issues / Open Questions

1. **Runtime Value vs Default Value**
   - Snapshot captures config.py defaults and env var names
   - Actual runtime values depend on running container's env - not captured here
   - **Impact:** For true runtime snapshot, must capture from live container env

2. **TWIN_CLOUD_ALIGNED Semantic**
   - Flag documented as "uses cloud endpoint directly instead of local MQTT-based sync"
   - Not fully traced how this affects twin routing in proxy.py
   - **Impact:** May need code analysis in later task to understand effect

3. **Effective Routing Mode Unknown at Snapshot Time**
   - LOCAL_CONTROL_ROUTING is "auto" by default
   - Actual routing depends on proxy mode (ONLINE/HYBRID/OFFLINE) at runtime
   - **Impact:** Must correlate with proxy mode state during actual setting flow

### Risk Assessment
| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Runtime values differ from defaults | High | Medium | Document default vs env distinction clearly |
| Cloud alignment effect unclear | Low | Medium | Add proxy.py code trace in Task 3 |
| Routing mode ambiguity | Medium | Low | Snapshot includes proxy_mode; correlation needed later |

## Task 3 log export (2026-03-08T18:12:16Z)
- Active worktree did not contain live runtime *.log files; Task 3 used deterministic forensic evidence sources under .sisyphus/evidence to avoid synthetic data fabrication.
- Timestamp fidelity is mixed by source (ISO/HMS/no ts); ts field is preserved when present and left null otherwise for downstream canonical normalization (Task 6).
- Potential noise remains from historical evidence lines; downstream normalization should apply strict case-id/time-window slicing.

## Task 4: Frame-Level Evidence Export (2026-03-08T19:35:00Z)
**Task:** Export frame-level evidence (payload capture / forensic)

### Issues / Observations
1. **Pre-existing Artifact**: Frame evidence file was already created by prior work
   - Status: No action needed, verified existing content
   - Validation: All 238 records meet schema requirements

2. **ACK vs Setting ACK Distinction**
   - 58 ACK_SETTING frames have `<Reason>Setting</Reason>`
   - Generic ACK frames (without Reason=Setting) are OUT_OF_SCOPE per plan
   - This aligns with Task 1 finding: "generic ACK is OUT_OF_SCOPE"

3. **Sequence Completeness**
   - 24 Setting->ACK sequences found
   - 19 complete Setting->ACK->END sequences
   - 5 sequences missing END frame (may indicate incomplete sessions)

### Risk Assessment
| Risk | Probability | Impact | Status |
|------|-------------|--------|--------|
| Missing END frames | Low | Low | Documented, 79% complete |
| Frame ordering issues | None | High | Verified: chronological order preserved |
| Payload transformation | None | High | Verified: raw XML preserved |

### No Blockers Identified
Task 4 output is ready for downstream consumption by Tasks 6, 8, and 9.

## Task 5 repair: MQTT timeline re-export (2026-03-08)
- Available source-backed MQTT topic evidence in this worktree is sparse; only one row met all required fields (`ts`, `source`, `topic`, `payload_hash`) without inference.
- Prior timeline content mixed non-source-backed lifecycle synthesis; repaired export now contains only directly extractable rows.

## Task 6: Canonical timeline normalization (2026-03-08)
1. **Mixed timestamp fidelity across sources**
   - Frame/MQTT artifacts provide full ISO timestamps, but many Task 3 log-export rows contain `ts=null` or `HH:MM:SS` without date context.
   - **Impact:** not all rows can be mapped to epoch deterministically from source alone.
   - **Mitigation:** preserve explicit nulls (`ts=null`, `ts_epoch_ms=null`) and keep rows in canonical dataset with deterministic tail ordering.

2. **Task 3 logs contain analytical prose and SQL report excerpts, not only runtime events**
   - Event semantics are sometimes inferred from marker tags/keywords rather than strict protocol fields.
   - **Impact:** `event_type` for some rows is coarse-grained (`log_ack_related`, `log_observation`) by necessity.
   - **Mitigation:** retain raw provenance per record (`raw.source`, `raw.line_no`, `raw_line`) for later classifier filtering in Tasks 7/8.

3. **Connection and transaction identity sparsity**
   - `conn_id` and protocol tx identifiers are missing in parts of Task 1/2/3/5 artifacts.
   - **Impact:** complete lifecycle stitching is not possible for all rows at Task 6 stage.
   - **Mitigation:** deterministic fallback IDs used, and nulls preserved where source has no value; no fabricated lifecycle claims introduced.

## Task 7: Session ownership matrix (2026-03-08)
1. **High null density limits definitive invariant evaluation**
   - 115/144 cases have no `conn_id`, and 133/144 cases have insufficient setting-vs-ack conn evidence for INV-1.
   - **Impact:** INV-1 and INV-2 are predominantly `UNKNOWN` by evidence quality, not by computation error.
   - **Mitigation:** explicit UNKNOWN verdict policy with rationale strings per row.

2. **Timeout ownership (INV-3) is the only strongly testable invariant in current dataset**
   - 44 cases include timeout markers with missing/unlinkable tx ownership, producing deterministic FAILs.
   - **Impact:** downstream Task 10/11 should treat these as concrete ownership anomalies with direct pointers.
   - **Mitigation:** every FAIL row includes `fail_evidence_pointer` with source + line + tx reference.

3. **No multi-conn cases in canonical timeline**
   - Cross-connection/session mismatch scenario for INV-2 FAIL did not occur in Task 6 data.
   - **Impact:** INV-2 coverage is PASS/UNKNOWN only; no direct FAIL exemplars this run.
   - **Mitigation:** keep deterministic rule in place and preserve fallback session_id derivation for future datasets.


## Task 8 (Worktree Repair): ACK classifier a ambiguity detector
**Date:** 2026-03-08
**Scope:** Active worktree-only regeneration of Task 8 outputs.

### Identified Issues / Constraints
1. `frame-evidence.jsonl` contains explicit `ACK_SETTING` and `END` markers but no explicit `NACK` frames in sampled/exported frame corpus.
2. Canonical timeline mixes transport-level frame cases with historical evidence-derived ACK-like logs; many legacy cases have null `conn_id` and remain ambiguity-prone.
3. Class domain supports `NACK`, but observed input distribution can legitimately yield zero `NACK` rows when no explicit NACK evidence is present.


## Task 9 (Worktree Path Repair): Frame parity diff vs cloud baseline
**Date:** 2026-03-08
**Task:** Recreate Task 9 artifacts in active worktree absolute paths

### Identified Issues / Constraints
1. Prior blocker was path mismatch between main repo and active worktree output locations.
2. `analysis/golden-handshake-fixtures/` is absent in active worktree, limiting cloud frame baseline parity.
3. Conservative fallback was required to avoid synthetic diffs while still satisfying failed-case coverage gate.

### Risk Mitigation
- Force absolute-path outputs under `/Users/martinhorak/Projects/oig-proxy-worktrees/twin-ack-forensics-verification/...`
- Keep diff taxonomy strictly within allowed domain including explicit `no_frame_diff` fallback.


## Task 10 (Worktree): State-transition + timing diff
**Date:** 2026-03-08
**Task:** Deterministic transition/timing comparison with explicit unknowns

### Identified Issues / Constraints
1. No explicit cloud baseline fixture dataset exists in active worktree for per-case twin-vs-cloud pairing; baseline had to be estimated from source-backed conn-bound `ACK_OK` cases only.
2. Many cases have sparse or null timestamp evidence (especially non-frame `SRC`/`NO_TS` cases), so timing fields legitimately remain `unknown`.
3. Retry-gap metric is only computable for cases with >=2 source-backed setting timestamps; otherwise kept explicit `unknown` to avoid inference.

## Task 11 (Worktree): First divergence localization + causality chain
**Date:** 2026-03-08
**Task:** Single primary divergence selection from Tasks 7/8/9/10 artifacts

### Identified Issues / Constraints
1. `frame-parity-diff.csv` provides only `no_frame_diff` because `analysis/golden-handshake-fixtures` is missing, so frame-level confidence contribution is limited.
2. The winning case has `conn_id` null in canonical timeline at first divergence event (`twin-ack-timeline.jsonl:392`), requiring explicit `conn_id: unknown` in coordinates.
3. Much of the corpus is `NO_TS`/sparse; deterministic ranking must prioritize known timestamps first to avoid selecting non-temporal cases as “first” divergence.


## Task 12 (Final Verdict): Issues
**Date:** 2026-03-08

### Identified Issues / Constraints
1. **Frame baseline structurally absent throughout entire analysis**
   - No `analysis/golden-handshake-fixtures/` directory exists in the worktree.
   - This forces `no_frame_diff` for all 135 frame-parity rows (RC-03).
   - **Impact:** The highest-information forensic dimension (frame-level cloud vs twin comparison) cannot be evaluated. This is the primary unresolved blocker for a `GO` verdict.
   - **Next step:** F1 experiment (capture golden handshake fixtures).

2. **Primary divergence has conn_id=null**
   - The winner case (`CASE-20260219-SRC-sisyphus-evidence-setting-ack-parity-task-10-mock-context-txt`) originates from a historical mock context evidence file, not a live capture.
   - `conn_id` is null throughout all 8 timeline rows for this case.
   - **Impact:** Cannot link divergence to a specific physical session. U-1 remains open.
   - **Next step:** F2 experiment (live log capture with conn_id).

3. **Corpus is dominated by historical evidence sources, not live proxy events**
   - The timeline mixes Dec 2025, Feb 2026, and March 2026 evidence from different analysis waves.
   - ACK_AMBIGUOUS rate (80%) may be inflated by source mixing rather than reflecting real Twin failure rate.
   - **Impact:** Verdict confidence for the general corpus is lower than for the primary divergence case specifically.
   - **Mitigation stated:** U-5 documented in verdict. F4 experiment addresses it.

## F4: Scope fidelity check - observed constraints
**Date:** 2026-03-08

1. Historical wrong-path writes are visible in prior evidence notes (Task 1/2 placement-fix entries), which is a process risk but not current scope contamination.
2. Plan file appears as untracked in this isolated worktree context; treated as read-only input and left unmodified.
3. No out-of-scope runtime/code/test changes detected during F4 execution.


## F1 Plan Compliance Audit (2026-03-08)
- Known limitation remains active: `analysis/golden-handshake-fixtures` missing, so frame parity is constrained to `no_frame_diff` gate outcomes.
- Final verification wave is not fully complete yet (F2/F3/F4 pending), so overall wave closure is deferred beyond this F1 artifact.

## F3: Real QA Replay - Issues (2026-03-08)
- No issues identified during QA replay.
- All 12 scenarios have corresponding evidence files with PASS status.
- Zero failures, zero missing evidence.

## Task F2: Data Quality + Reproducibility Review (2026-03-08)
**Task:** Assess reproducibility and data-quality integrity

### Identified Issues

1. **Task 11 first-divergence.md drift detected**
   - Evidence recorded hash: 2ead051bf26f8967abb76a37f9561f5b3fffc26e87d807a8cb34a77815d8476e
   - Current file hash: 9c84e6ce7ce65ab24ef641a1fc1d48f3b22aa56f6f843c342a33ba2699397396
   - Impact: Medium - file was modified after Task 11 evidence capture
   - Likely cause: File regeneration or manual edit post-evidence
   - Mitigation: Re-run Task 11 to update evidence hash if changes are intentional

2. **No checksum stored for verification-verdict.md (Task 12)**
   - Task 12 evidence does not include SHA256 for the verdict markdown file
   - Impact: Low - cannot detect drift for this specific file
   - Mitigation: Future tasks should store checksums for all generated artifacts

3. **frame-parity-diff.csv has fewer rows (135) than case count (144)**
   - This is expected behavior (only non-ACK_OK cases have frame parity entries)
   - Impact: None - by design
   - Note: Documented in report for audit trail completeness

### Risk Assessment
| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Undetected file drift | Low | Medium | Store checksums for all artifacts |
| Schema drift | Low | High | Validate headers/domains on each task |
| Count inconsistency | Low | Medium | Cross-validate dependent counts |

