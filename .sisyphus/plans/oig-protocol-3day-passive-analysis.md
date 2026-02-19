# OIG Protocol 3-Day Passive Analysis Plan

## TL;DR

> **Quick Summary**: Build a strictly passive, evidence-driven 3-day analysis pipeline over existing proxy capture and telemetry to map cloud responses to standard requests and signal frames, quantify blind spots, and produce a protocol-confidence report.
>
> **Deliverables**:
> - Passive collection + validation runbook (preflight, daily checks, post-analysis)
> - Request/response and signal-reaction matrices (by mode and failure condition)
> - 3-day evidence package with confidence score and adjustment backlog
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 -> Task 4 -> Task 9 -> Task 14 -> Task 16 -> Task 19

---

## Context

### Original Request
User asked for an entry analysis of cloud<->box OIG TCP communication, especially cloud behavior on standard requests, telemetry, and signals, plus a 3-day data collection plan and validation of what is currently collected, with no active interference in live communication.

### Interview Summary
**Key Discussions**:
- Passive-only approach confirmed; no intervention in live flow.
- Data source for 3-day run confirmed: existing proxy capture + telemetry only.
- Focus signal classes confirmed: `IsNewSet`, `IsNewWeather`, `IsNewFW`, `END`, ACK/NACK, timeout/EOF/error branches.

**Research Findings**:
- Current frame capture schema and capture switches are defined in `addon/oig-proxy/utils.py` and `addon/oig-proxy/config.py`.
- Runtime behavior and signal branches are concentrated in `addon/oig-proxy/proxy.py`, `addon/oig-proxy/cloud_forwarder.py`, `addon/oig-proxy/hybrid_mode.py`, `addon/oig-proxy/control_settings.py`.
- Telemetry windows and event metrics are collected in `addon/oig-proxy/telemetry_collector.py` and sent by `addon/oig-proxy/telemetry_client.py`.
- Historical dataset exists in `analysis/ha_snapshot/payloads_ha_full.db` with high volume suitable for baseline calibration.

### Metis Review
**Identified Gaps** (addressed):
- Success criteria were not fully quantified -> plan defines measurable protocol-confidence thresholds.
- Scope creep risk (pcap/probing/dashboarding) -> explicitly excluded in guardrails.
- Blind spot handling was implicit -> plan adds dedicated blind-spot quantification and confidence scoring task.
- Daily quality control was missing -> plan adds daily validation gate task and hard pass/fail checks.

---

## Work Objectives

### Core Objective
Deliver a passive, reproducible 3-day analysis workflow that maps OIG request/response behavior and signal handling with enough evidence to safely refine future protocol handling and reduce communication divergence failures.

### Concrete Deliverables
- `docs/` or `.sisyphus/evidence/` analysis artifacts defining current collection coverage, signal matrices, and risk findings.
- Executable validation commands/scripts for daily health checks and final protocol-confidence scoring.
- Final Czech summary report with recommended data-model adjustments (without applying runtime changes).

### Definition of Done
- [ ] 3-day passive dataset validated with daily pass/fail gates and evidence files.
- [ ] Signal reaction matrix completed for all target signals and failure branches.
- [ ] Blind spots quantified and confidence score reported with explicit limitations.
- [ ] Final analysis package produced with reproducible commands.

### Must Have
- Passive-only data collection and analysis.
- Mode-aware behavior mapping (`online`, `hybrid`, `offline`).
- Quantified acceptance criteria and evidence-backed conclusions.

### Must NOT Have (Guardrails)
- No active MITM injection, replay against production, or traffic manipulation.
- No protocol-changing code in proxy runtime during collection window.
- No expansion into unrelated UI/dashboard or infrastructure redesign tasks.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — all verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (`pytest` + existing test suite)
- **Automated tests**: Tests-after
- **Framework**: `pytest`

### QA Policy
Every task includes agent-executed scenarios and evidence artifacts in `.sisyphus/evidence/task-{N}-*.{ext}`.

- **CLI/Analysis**: Use Bash (`sqlite3`, `python`, `pytest`) for deterministic checks.
- **Data validation**: Compare outputs against explicit thresholds and schema expectations.
- **Evidence**: Save command outputs, generated JSON summaries, and validation logs.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - start immediately, 6 parallel):
├── Task 1: Runtime capture/telemetry baseline snapshot [quick]
├── Task 2: Capture schema + completeness audit queries [quick]
├── Task 3: Canonical signal taxonomy definition [quick]
├── Task 4: Request-response pairing engine design [deep]
├── Task 5: Evidence manifest and naming convention [quick]
└── Task 6: Non-interference preflight guardrail runbook [writing]

Wave 2 (Collection tooling - after Wave 1, 6 parallel):
├── Task 7: Day-slice frame extractor (payload snapshots) [quick]
├── Task 8: Telemetry/event topic snapshotter [quick]
├── Task 9: Signal timeline extractor (IsNew*/END/ACK/NACK) [deep]
├── Task 10: Mode/cloud health transition extractor [deep]
├── Task 11: Edge-case detector (timeout/eof/error/disconnect) [deep]
└── Task 12: Daily validation CLI with hard thresholds [unspecified-high]

Wave 3 (Analysis synthesis - after Wave 2, 5 parallel):
├── Task 13: Standard request cloud response matrix [deep]
├── Task 14: Signal reaction matrix by mode and condition [deep]
├── Task 15: Blind-spot quantification + confidence scoring [unspecified-high]
├── Task 16: 3-day drift/anomaly comparative report [unspecified-high]
└── Task 17: Protocol data-adjustment backlog (safe changes only) [writing]

Wave 4 (Integration + packaging - after Wave 3, 3 parallel):
├── Task 18: Validate tooling on historical + live sample day [unspecified-high]
├── Task 19: Assemble final analysis package + Czech executive brief [writing]
└── Task 20: Handoff checklist and execution gates for follow-up sprint [quick]

Wave FINAL (After ALL tasks - independent review, 4 parallel):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA execution of all scenarios (unspecified-high)
└── Task F4: Scope fidelity check (deep)

Critical Path: 1 -> 4 -> 9 -> 14 -> 16 -> 19
Parallel Speedup: ~65% vs sequential
Max Concurrent: 6
```

### Dependency Matrix

- **1**: Blocked By none | Blocks 7, 8, 12
- **2**: Blocked By none | Blocks 7, 9, 11, 12
- **3**: Blocked By none | Blocks 9, 13, 14
- **4**: Blocked By 1,2,3 | Blocks 9, 13, 14
- **5**: Blocked By none | Blocks all tasks (evidence naming)
- **6**: Blocked By none | Blocks all collection tasks (safety gate)
- **7**: Blocked By 1,2,5,6 | Blocks 16,18
- **8**: Blocked By 1,5,6 | Blocks 10,16,18
- **9**: Blocked By 2,3,4,5,6 | Blocks 14,16,18
- **10**: Blocked By 8,5,6 | Blocks 14,16,18
- **11**: Blocked By 2,5,6 | Blocks 15,16,18
- **12**: Blocked By 1,2,5,6 | Blocks 18
- **13**: Blocked By 3,4,7,9 | Blocks 16,19
- **14**: Blocked By 3,4,9,10 | Blocks 16,19
- **15**: Blocked By 11,13,14 | Blocks 16,17,19
- **16**: Blocked By 7,8,9,10,11,13,14,15 | Blocks 19
- **17**: Blocked By 15,16 | Blocks 19,20
- **18**: Blocked By 7,8,9,10,11,12 | Blocks 19
- **19**: Blocked By 13,14,15,16,17,18 | Blocks 20, F1-F4
- **20**: Blocked By 17,19 | Blocks F1-F4
- **F1-F4**: Blocked By 1-20 | Final approval gate

### Agent Dispatch Summary

- **Wave 1**: 6 agents — T1 `quick`, T2 `quick`, T3 `quick`, T4 `deep`, T5 `quick`, T6 `writing`
- **Wave 2**: 6 agents — T7 `quick`, T8 `quick`, T9 `deep`, T10 `deep`, T11 `deep`, T12 `unspecified-high`
- **Wave 3**: 5 agents — T13 `deep`, T14 `deep`, T15 `unspecified-high`, T16 `unspecified-high`, T17 `writing`
- **Wave 4**: 3 agents — T18 `unspecified-high`, T19 `writing`, T20 `quick`
- **FINAL**: 4 agents — F1 `oracle`, F2 `unspecified-high`, F3 `unspecified-high`, F4 `deep`

---

## TODOs

- [x] 1. Runtime capture and telemetry baseline snapshot

  **What to do**:
  - Create a read-only baseline snapshot script that records active addon/runtime settings for capture, retention, mode, and telemetry endpoints.
  - Export a single machine-readable evidence file describing "what we collect today" before 3-day collection starts.

  **Must NOT do**:
  - Do not change addon runtime settings.
  - Do not restart proxy or MQTT services.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: focused CLI inventory task over a few files.
  - **Skills**: [`git-master`]
    - `git-master`: keeps scripted/documentation changes in clean atomic commits.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser workflow.
    - `dev-browser`: no browser workflow.
    - `frontend-ui-ux`: no UI design task.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 5, 6)
  - **Blocks**: 7, 8, 12
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/config.json` - addon option defaults and schema.
  - `addon/oig-proxy/run` - runtime env export logic for capture and mode.
  - `addon/oig-proxy/config.py` - effective runtime constants and defaults.
  - `docs/telemetry_overview.md` - expected telemetry payload shape.

  **Acceptance Criteria**:
  - [ ] Baseline artifact exists at `.sisyphus/evidence/task-1-baseline.json`.
  - [ ] Artifact includes keys: `capture_payloads`, `capture_raw_bytes`, `capture_retention_days`, `proxy_mode`, `telemetry_enabled`, `telemetry_broker`.
  - [ ] Command `python scripts/protocol_analysis/baseline_snapshot.py --out .sisyphus/evidence/task-1-baseline.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Baseline snapshot happy path
    Tool: Bash (python)
    Preconditions: Repository checkout present; config files readable.
    Steps:
      1. Run `python scripts/protocol_analysis/baseline_snapshot.py --out .sisyphus/evidence/task-1-baseline.json`.
      2. Run `python scripts/protocol_analysis/validate_baseline.py --input .sisyphus/evidence/task-1-baseline.json`.
      3. Assert validator output contains `STATUS: PASS` and required keys.
    Expected Result: Baseline file generated and validated.
    Failure Indicators: Missing key, parse error, non-zero exit.
    Evidence: .sisyphus/evidence/task-1-baseline.json

  Scenario: Missing config failure path
    Tool: Bash (python)
    Preconditions: Provide a non-existent config path argument.
    Steps:
      1. Run `python scripts/protocol_analysis/baseline_snapshot.py --config /tmp/missing.json --out /tmp/should-not-exist.json`.
      2. Assert command exits non-zero and stderr contains `config not found`.
    Expected Result: Graceful error with actionable message.
    Evidence: .sisyphus/evidence/task-1-missing-config-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-1-baseline.json`
  - [ ] `.sisyphus/evidence/task-1-missing-config-error.log`

  **Commit**: YES
  - Message: `docs(analysis): snapshot passive capture baseline`
  - Files: `scripts/protocol_analysis/baseline_snapshot.py`, `scripts/protocol_analysis/validate_baseline.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_baseline_snapshot.py -q`

- [x] 2. Capture schema and completeness audit queries

  **What to do**:
  - Implement SQL query pack that audits frame schema completeness, direction distribution, null-rates, and time coverage.
  - Generate an audit report against historical DB and current sample DB snapshot.

  **Must NOT do**:
  - Do not mutate production DB schema/data.
  - Do not add destructive SQL operations.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: bounded SQL/reporting task with deterministic output.
  - **Skills**: [`git-master`]
    - `git-master`: atomic changes for SQL/reporting scripts and tests.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 5, 6)
  - **Blocks**: 7, 9, 11, 12
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/utils.py` - canonical `frames` table schema and retention behavior.
  - `analysis/ha_snapshot/payloads_ha_full.db` - baseline dataset for validation.
  - `testing/export_ha_session.py` - existing query style and DB access assumptions.

  **Acceptance Criteria**:
  - [ ] Audit report exists at `.sisyphus/evidence/task-2-capture-audit.json`.
  - [ ] Report includes `total_frames`, `direction_counts`, `table_counts`, `null_rate_by_column`, `ts_range`.
  - [ ] `python scripts/protocol_analysis/audit_capture_schema.py --db analysis/ha_snapshot/payloads_ha_full.db --out .sisyphus/evidence/task-2-capture-audit.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Capture audit happy path
    Tool: Bash (python/sqlite3)
    Preconditions: Historical DB exists at analysis/ha_snapshot/payloads_ha_full.db.
    Steps:
      1. Run audit command to generate JSON report.
      2. Run `python scripts/protocol_analysis/assert_capture_audit.py --input .sisyphus/evidence/task-2-capture-audit.json --min-rows 1000`.
      3. Assert output includes `STATUS: PASS`.
    Expected Result: Complete capture audit with valid metrics.
    Failure Indicators: Missing section, invalid JSON, zero counts.
    Evidence: .sisyphus/evidence/task-2-capture-audit.json

  Scenario: Missing frames table failure path
    Tool: Bash (python)
    Preconditions: Provide empty SQLite DB without `frames` table.
    Steps:
      1. Run audit command against empty DB path.
      2. Assert non-zero exit and error text includes `frames table not found`.
    Expected Result: Deterministic fail with explicit reason.
    Evidence: .sisyphus/evidence/task-2-missing-frames-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-2-capture-audit.json`
  - [ ] `.sisyphus/evidence/task-2-missing-frames-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add capture schema completeness audit`
  - Files: `scripts/protocol_analysis/audit_capture_schema.py`, `scripts/protocol_analysis/assert_capture_audit.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_capture_audit.py -q`

- [x] 3. Canonical OIG signal taxonomy definition

  **What to do**:
  - Define canonical request/signal classes and aliases used across analysis outputs.
  - Include mode-specific interpretation notes for `IsNewSet`, `IsNewWeather`, `IsNewFW`, `END`, ACK/NACK and weather/setting payload variants.

  **Must NOT do**:
  - Do not infer unsupported protocol behavior without evidence.
  - Do not merge distinct signal classes into a single bucket.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: constrained classification/specification task.
  - **Skills**: [`git-master`]
    - `git-master`: clean versioning for taxonomy/schema evolution.
  - **Skills Evaluated but Omitted**:
    - `playwright`: not needed.
    - `dev-browser`: not needed.
    - `frontend-ui-ux`: not needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 5, 6)
  - **Blocks**: 9, 13, 14
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/oig_frame.py` - ACK/END frame semantics.
  - `addon/oig-proxy/proxy.py` - table override from `Result` for `IsNew*`.
  - `addon/oig-proxy/cloud_forwarder.py` - signal-specific branch handling.
  - `testing/comparison_suite.py` - existing request/response class normalization patterns.

  **Acceptance Criteria**:
  - [ ] Taxonomy file exists at `.sisyphus/evidence/task-3-signal-taxonomy.json`.
  - [ ] Taxonomy includes all required classes and alias mappings.
  - [ ] Validator command exits 0: `python scripts/protocol_analysis/validate_taxonomy.py --input .sisyphus/evidence/task-3-signal-taxonomy.json`.

  **QA Scenarios**:
  ```bash
  Scenario: Taxonomy generation happy path
    Tool: Bash (python)
    Preconditions: Reference files are present.
    Steps:
      1. Run `python scripts/protocol_analysis/build_signal_taxonomy.py --out .sisyphus/evidence/task-3-signal-taxonomy.json`.
      2. Run taxonomy validator command.
      3. Assert required classes are present (`IsNewSet`, `IsNewWeather`, `IsNewFW`, `END`, `ACK`, `NACK`).
    Expected Result: Valid canonical taxonomy with aliases.
    Failure Indicators: Missing class, duplicate canonical key, invalid JSON.
    Evidence: .sisyphus/evidence/task-3-signal-taxonomy.json

  Scenario: Duplicate canonical key failure path
    Tool: Bash (python)
    Preconditions: Provide test taxonomy fixture with duplicate canonical IDs.
    Steps:
      1. Run validator against duplicate fixture.
      2. Assert validator exits non-zero with `duplicate canonical key`.
    Expected Result: Validator catches taxonomy integrity issue.
    Evidence: .sisyphus/evidence/task-3-taxonomy-duplicate-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-3-signal-taxonomy.json`
  - [ ] `.sisyphus/evidence/task-3-taxonomy-duplicate-error.log`

  **Commit**: YES
  - Message: `docs(analysis): define canonical OIG signal taxonomy`
  - Files: `scripts/protocol_analysis/build_signal_taxonomy.py`, `scripts/protocol_analysis/validate_taxonomy.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_signal_taxonomy.py -q`

- [x] 4. Request-response pairing engine design and implementation

  **What to do**:
  - Implement pairing engine that maps box requests to subsequent cloud/local responses using conn_id, sequence order, and bounded fallback windows.
  - Output confidence indicators for matched/unmatched pairs and ambiguous cases.

  **Must NOT do**:
  - Do not assume strict 1:1 ordering without fallback logic.
  - Do not drop unmatched rows silently.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: correlation logic with ambiguity handling and confidence scoring.
  - **Skills**: [`git-master`]
    - `git-master`: safe iterative commits for core analysis logic.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no UI/browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential anchor inside Wave 1
  - **Blocks**: 9, 13, 14
  - **Blocked By**: 1, 2, 3

  **References**:
  - `testing/comparison_suite.py` - existing pairing and class normalization baseline.
  - `addon/oig-proxy/telemetry_collector.py` - request/response queue model (`record_request`, `record_response`).
  - `analysis/ha_snapshot/payloads_ha_full.db` - large dataset for pairing stress tests.

  **Acceptance Criteria**:
  - [ ] Pairing output exists at `.sisyphus/evidence/task-4-pairing-sample.json`.
  - [ ] Output includes fields `request_id`, `request_class`, `response_id`, `response_class`, `match_confidence`, `match_reason`.
  - [ ] `python scripts/protocol_analysis/pair_frames.py --db analysis/ha_snapshot/payloads_ha_full.db --limit 5000 --out .sisyphus/evidence/task-4-pairing-sample.json` exits 0.
  - [ ] Ambiguous pair rate is reported explicitly.

  **QA Scenarios**:
  ```bash
  Scenario: Pairing engine happy path
    Tool: Bash (python)
    Preconditions: Historical DB available; taxonomy file from Task 3 exists.
    Steps:
      1. Run pairing engine command on sample window.
      2. Run `python scripts/protocol_analysis/assert_pairing_quality.py --input .sisyphus/evidence/task-4-pairing-sample.json --min-confidence 0.7`.
      3. Assert validator prints `STATUS: PASS` and includes matched/unmatched counts.
    Expected Result: Pairing output with confidence and traceability.
    Failure Indicators: Missing confidence fields, zero matches, parse failures.
    Evidence: .sisyphus/evidence/task-4-pairing-sample.json

  Scenario: Corrupted ordering failure path
    Tool: Bash (python)
    Preconditions: Synthetic fixture with shuffled IDs.
    Steps:
      1. Run pairing engine on shuffled fixture.
      2. Assert engine exits non-zero or emits `ambiguous_ordering` warnings in output.
    Expected Result: Engine flags ordering ambiguity without silent data loss.
    Evidence: .sisyphus/evidence/task-4-pairing-ordering-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-4-pairing-sample.json`
  - [ ] `.sisyphus/evidence/task-4-pairing-ordering-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add frame pairing engine with confidence scoring`
  - Files: `scripts/protocol_analysis/pair_frames.py`, `scripts/protocol_analysis/assert_pairing_quality.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_pair_frames.py -q`

- [x] 5. Evidence manifest and naming convention

  **What to do**:
  - Define canonical evidence naming (`task-{N}-{scenario-slug}.{ext}`) and implement manifest generator.
  - Enforce uniqueness and completeness checks for all planned evidence artifacts.

  **Must NOT do**:
  - Do not allow ad-hoc filenames outside manifest rules.
  - Do not overwrite existing evidence silently.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: lightweight consistency tooling and documentation.
  - **Skills**: [`git-master`]
    - `git-master`: stable commit boundaries for manifest and validators.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 6)
  - **Blocks**: all downstream tasks (evidence contract)
  - **Blocked By**: None

  **References**:
  - `.sisyphus/evidence/` - target evidence root.
  - `testing/comparison_suite.py` - existing evidence write pattern.
  - `.sisyphus/plans/oig-protocol-3day-passive-analysis.md` - task IDs and scenario naming contract.

  **Acceptance Criteria**:
  - [ ] Manifest template exists at `.sisyphus/evidence/task-5-evidence-manifest.json`.
  - [ ] Validator rejects duplicate filenames and missing required artifacts.
  - [ ] `python scripts/protocol_analysis/validate_evidence_manifest.py --plan .sisyphus/plans/oig-protocol-3day-passive-analysis.md --manifest .sisyphus/evidence/task-5-evidence-manifest.json` exits 0 for valid manifest.

  **QA Scenarios**:
  ```bash
  Scenario: Manifest generation happy path
    Tool: Bash (python)
    Preconditions: Plan file exists with tasks and scenarios.
    Steps:
      1. Run `python scripts/protocol_analysis/generate_evidence_manifest.py --plan .sisyphus/plans/oig-protocol-3day-passive-analysis.md --out .sisyphus/evidence/task-5-evidence-manifest.json`.
      2. Run validator command.
      3. Assert output contains `STATUS: PASS`.
    Expected Result: Complete, unique evidence manifest.
    Failure Indicators: Missing tasks, duplicate names, invalid JSON.
    Evidence: .sisyphus/evidence/task-5-evidence-manifest.json

  Scenario: Duplicate evidence name failure path
    Tool: Bash (python)
    Preconditions: Corrupt manifest fixture with duplicate names.
    Steps:
      1. Run validator on duplicate fixture.
      2. Assert non-zero exit and message `duplicate evidence filename`.
    Expected Result: Duplicate detection enforced.
    Evidence: .sisyphus/evidence/task-5-duplicate-manifest-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-5-evidence-manifest.json`
  - [ ] `.sisyphus/evidence/task-5-duplicate-manifest-error.log`

  **Commit**: YES
  - Message: `chore(analysis): enforce evidence naming and manifest rules`
  - Files: `scripts/protocol_analysis/generate_evidence_manifest.py`, `scripts/protocol_analysis/validate_evidence_manifest.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_evidence_manifest.py -q`

- [x] 6. Non-interference preflight guardrail runbook

  **What to do**:
  - Create a preflight checklist and checker script that verifies passive-only conditions before and during collection.
  - Validate no active probing/replay/injection flags are enabled in planned workflow.

  **Must NOT do**:
  - Do not add active network tests to the preflight pipeline.
  - Do not auto-change runtime settings from the checker.

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: policy/runbook-centric task with light CLI validation.
  - **Skills**: [`git-master`]
    - `git-master`: keeps policy and checker updates traceable.
  - **Skills Evaluated but Omitted**:
    - `playwright`: not applicable.
    - `dev-browser`: not applicable.
    - `frontend-ui-ux`: not applicable.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 5)
  - **Blocks**: 7, 8, 9, 10, 11, 12
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/run` - runtime config toggles that can alter traffic handling.
  - `addon/oig-proxy/cloud_forwarder.py` - sensitive fallback/forward paths.
  - `README.md` - deployment and mode expectations.

  **Acceptance Criteria**:
  - [ ] Runbook exists at `.sisyphus/evidence/task-6-non-interference-runbook.md`.
  - [ ] Checker output exists at `.sisyphus/evidence/task-6-preflight-check.json`.
  - [ ] `python scripts/protocol_analysis/check_passive_guardrails.py --out .sisyphus/evidence/task-6-preflight-check.json` exits 0 when passive requirements are met.

  **QA Scenarios**:
  ```bash
  Scenario: Passive preflight happy path
    Tool: Bash (python)
    Preconditions: Current workflow uses proxy+telemetry only, no probe scripts enabled.
    Steps:
      1. Run guardrail checker command.
      2. Assert JSON contains `passive_mode=true` and `active_probe_detected=false`.
      3. Assert checker exits 0.
    Expected Result: Preflight passes and documents passive guarantees.
    Failure Indicators: Any forbidden action flag true.
    Evidence: .sisyphus/evidence/task-6-preflight-check.json

  Scenario: Forbidden probe flag failure path
    Tool: Bash (python)
    Preconditions: Provide fixture config with active probing enabled.
    Steps:
      1. Run checker against forbidden fixture.
      2. Assert non-zero exit and output includes `forbidden_active_probe`.
    Expected Result: Checker blocks non-passive setup.
    Evidence: .sisyphus/evidence/task-6-preflight-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-6-non-interference-runbook.md`
  - [ ] `.sisyphus/evidence/task-6-preflight-check.json`
  - [ ] `.sisyphus/evidence/task-6-preflight-error.log`

  **Commit**: YES
  - Message: `docs(analysis): add passive guardrail preflight runbook`
  - Files: `scripts/protocol_analysis/check_passive_guardrails.py`, `docs/protocol_analysis/passive_guardrails.md`
  - Pre-commit: `pytest tests/protocol_analysis/test_passive_guardrails.py -q`

- [ ] 7. Day-slice frame extractor for 3-day windows

  **What to do**:
  - Implement extractor that produces per-day frame snapshots (read-only) from payload capture DB with stable schema.
  - Support date-range filtering and conn_id grouping metadata.

  **Must NOT do**:
  - Do not delete or rewrite source DB records.
  - Do not perform full-table export when date filter is provided.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: bounded extraction utility with straightforward IO.
  - **Skills**: [`git-master`]
    - `git-master`: commit hygiene for extraction tooling.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 8, 9, 10, 11, 12)
  - **Blocks**: 16, 18
  - **Blocked By**: 1, 2, 5, 6

  **References**:
  - `testing/export_ha_session.py` - existing extraction patterns and output structure.
  - `addon/oig-proxy/utils.py` - frame fields available in `frames` table.
  - `analysis/ha_snapshot/payloads_ha_full.db` - baseline DB for test runs.

  **Acceptance Criteria**:
  - [ ] Extractor generates `.sisyphus/evidence/task-7-day1-frames.json`, `task-7-day2-frames.json`, `task-7-day3-frames.json`.
  - [ ] Each output includes metadata keys: `source_db`, `start_ts`, `end_ts`, `frame_count`, `conn_id_count`.
  - [ ] `python scripts/protocol_analysis/extract_day_slice.py --db <db> --date 2026-02-01 --out .sisyphus/evidence/task-7-day1-frames.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Day-slice extraction happy path
    Tool: Bash (python)
    Preconditions: Input DB contains frames for selected date.
    Steps:
      1. Run extractor command for one date.
      2. Run `python scripts/protocol_analysis/validate_day_slice.py --input .sisyphus/evidence/task-7-day1-frames.json --min-frames 100`.
      3. Assert validator returns `STATUS: PASS`.
    Expected Result: Valid day-slice artifact with non-zero frames.
    Failure Indicators: Empty extraction, malformed JSON, missing metadata.
    Evidence: .sisyphus/evidence/task-7-day1-frames.json

  Scenario: Invalid date range failure path
    Tool: Bash (python)
    Preconditions: Provide end date earlier than start date.
    Steps:
      1. Run extractor with invalid date ordering.
      2. Assert non-zero exit and message includes `invalid date range`.
    Expected Result: Deterministic validation failure.
    Evidence: .sisyphus/evidence/task-7-invalid-range-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-7-day1-frames.json`
  - [ ] `.sisyphus/evidence/task-7-invalid-range-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add day-slice frame extraction utility`
  - Files: `scripts/protocol_analysis/extract_day_slice.py`, `scripts/protocol_analysis/validate_day_slice.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_extract_day_slice.py -q`

- [ ] 8. Telemetry and event topic snapshotter

  **What to do**:
  - Implement read-only snapshotter for `oig/telemetry/<device_id>` and `oig/events/<device_id>` payloads over bounded collection windows.
  - Persist normalized snapshots aligned with frame day-slices.

  **Must NOT do**:
  - Do not publish control messages to broker.
  - Do not modify retained topics.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: subscription + normalization utility with limited scope.
  - **Skills**: [`git-master`]
    - `git-master`: controlled commits around broker snapshot tooling.
  - **Skills Evaluated but Omitted**:
    - `playwright`: not relevant.
    - `dev-browser`: not relevant.
    - `frontend-ui-ux`: not relevant.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 9, 10, 11, 12)
  - **Blocks**: 10, 16, 18
  - **Blocked By**: 1, 5, 6

  **References**:
  - `addon/oig-proxy/telemetry_client.py` - telemetry/event topic naming and payload shape.
  - `addon/oig-proxy/telemetry_collector.py` - expected metric keys and window structures.
  - `docs/telemetry_overview.md` - top-level vs window metrics semantics.

  **Acceptance Criteria**:
  - [ ] Snapshot output exists at `.sisyphus/evidence/task-8-telemetry-snapshot.json`.
  - [ ] Snapshot contains arrays `telemetry_messages` and `event_messages` with timestamps.
  - [ ] `python scripts/protocol_analysis/snapshot_telemetry_topics.py --duration-s 300 --out .sisyphus/evidence/task-8-telemetry-snapshot.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Telemetry snapshot happy path
    Tool: Bash (python)
    Preconditions: Broker reachable; telemetry messages present in interval.
    Steps:
      1. Run telemetry snapshotter for bounded interval.
      2. Run `python scripts/protocol_analysis/validate_telemetry_snapshot.py --input .sisyphus/evidence/task-8-telemetry-snapshot.json`.
      3. Assert validator returns `STATUS: PASS` and required keys exist.
    Expected Result: Normalized telemetry/event snapshot saved.
    Failure Indicators: Broker auth failure, empty malformed payload, missing arrays.
    Evidence: .sisyphus/evidence/task-8-telemetry-snapshot.json

  Scenario: Broker unavailable failure path
    Tool: Bash (python)
    Preconditions: Use unreachable broker host fixture.
    Steps:
      1. Run snapshotter with unreachable broker.
      2. Assert non-zero exit and error includes `broker unreachable`.
    Expected Result: Graceful failure with retry/context info.
    Evidence: .sisyphus/evidence/task-8-broker-unavailable-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-8-telemetry-snapshot.json`
  - [ ] `.sisyphus/evidence/task-8-broker-unavailable-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add passive telemetry topic snapshotter`
  - Files: `scripts/protocol_analysis/snapshot_telemetry_topics.py`, `scripts/protocol_analysis/validate_telemetry_snapshot.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_telemetry_snapshot.py -q`

- [ ] 9. Signal timeline extractor (IsNew*/END/ACK/NACK)

  **What to do**:
  - Build extractor that produces chronological signal timelines combining frame direction, table/result class, and pairing confidence.
  - Include explicit segments for `IsNewSet`, `IsNewWeather`, `IsNewFW`, `END`, ACK, NACK per connection and per day.

  **Must NOT do**:
  - Do not collapse signal classes into generic "other" unless explicitly documented.
  - Do not discard low-confidence pairings; mark them.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: chronology stitching and class-aware correlation logic.
  - **Skills**: [`git-master`]
    - `git-master`: clean iterative changes for core analysis logic.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no UI/browser.
    - `dev-browser`: no UI/browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 10, 11, 12)
  - **Blocks**: 14, 16, 18
  - **Blocked By**: 2, 3, 4, 5, 6

  **References**:
  - `addon/oig-proxy/proxy.py` - signal table normalization (`Result` -> `_table` for IsNew*).
  - `addon/oig-proxy/cloud_forwarder.py` - ACK forwarding and signal branch behavior.
  - `addon/oig-proxy/oig_frame.py` - END/ACK frame variants.
  - `testing/comparison_suite.py` - existing classification helpers.

  **Acceptance Criteria**:
  - [ ] Timeline artifact exists at `.sisyphus/evidence/task-9-signal-timeline.json`.
  - [ ] Artifact includes `conn_id`, `signal_class`, `direction`, `ts`, `response_class`, `pair_confidence`.
  - [ ] `python scripts/protocol_analysis/build_signal_timeline.py --frames .sisyphus/evidence/task-7-day1-frames.json --out .sisyphus/evidence/task-9-signal-timeline.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Signal timeline happy path
    Tool: Bash (python)
    Preconditions: Day-slice frames and taxonomy are available.
    Steps:
      1. Run timeline builder command.
      2. Run `python scripts/protocol_analysis/assert_signal_timeline.py --input .sisyphus/evidence/task-9-signal-timeline.json --required-signals IsNewSet IsNewWeather IsNewFW END`.
      3. Assert validator output `STATUS: PASS`.
    Expected Result: Chronological timeline with required signal classes.
    Failure Indicators: Missing required signal class, invalid timestamps, empty output.
    Evidence: .sisyphus/evidence/task-9-signal-timeline.json

  Scenario: Unknown signal class failure path
    Tool: Bash (python)
    Preconditions: Provide fixture with unclassified signal token.
    Steps:
      1. Run timeline builder on fixture.
      2. Assert tool marks rows `signal_class=UNKNOWN` and exits non-zero when strict mode enabled.
    Expected Result: Unknown classes are surfaced, not silently dropped.
    Evidence: .sisyphus/evidence/task-9-unknown-signal-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-9-signal-timeline.json`
  - [ ] `.sisyphus/evidence/task-9-unknown-signal-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add signal timeline extraction`
  - Files: `scripts/protocol_analysis/build_signal_timeline.py`, `scripts/protocol_analysis/assert_signal_timeline.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_signal_timeline.py -q`

- [ ] 10. Mode and cloud health transition extractor

  **What to do**:
  - Extract mode and cloud-session transitions from telemetry snapshots and proxy status metrics.
  - Emit normalized transition events with start/end, reason, and duration.

  **Must NOT do**:
  - Do not infer mode transitions without timestamp evidence.
  - Do not merge cloud health transitions into a single undifferentiated status.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: event-state modeling across telemetry windows.
  - **Skills**: [`git-master`]
    - `git-master`: controlled commit flow for state-extraction logic.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 11, 12)
  - **Blocks**: 14, 16, 18
  - **Blocked By**: 8, 5, 6

  **References**:
  - `addon/oig-proxy/hybrid_mode.py` - mode and fail/offline transition semantics.
  - `addon/oig-proxy/telemetry_collector.py` - `hybrid_sessions`, `cloud_sessions`, `offline_events`.
  - `addon/oig-proxy/proxy_status.py` - mode/cloud status counters and heartbeat fields.

  **Acceptance Criteria**:
  - [ ] Output exists at `.sisyphus/evidence/task-10-mode-cloud-transitions.json`.
  - [ ] Output includes `from_state`, `to_state`, `reason`, `started_at`, `ended_at`, `duration_s`.
  - [ ] `python scripts/protocol_analysis/extract_mode_transitions.py --telemetry .sisyphus/evidence/task-8-telemetry-snapshot.json --out .sisyphus/evidence/task-10-mode-cloud-transitions.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Transition extraction happy path
    Tool: Bash (python)
    Preconditions: Telemetry snapshot with session and offline events exists.
    Steps:
      1. Run transition extractor command.
      2. Run `python scripts/protocol_analysis/assert_transition_integrity.py --input .sisyphus/evidence/task-10-mode-cloud-transitions.json`.
      3. Assert validator reports no negative durations and valid state pairs.
    Expected Result: Valid transition graph with durations.
    Failure Indicators: Invalid state enum, negative duration, missing reason.
    Evidence: .sisyphus/evidence/task-10-mode-cloud-transitions.json

  Scenario: Missing timestamp failure path
    Tool: Bash (python)
    Preconditions: Fixture with events missing timestamp.
    Steps:
      1. Run extractor in strict mode on broken fixture.
      2. Assert non-zero exit and message `missing timestamp`.
    Expected Result: Strict validation blocks invalid transition data.
    Evidence: .sisyphus/evidence/task-10-missing-timestamp-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-10-mode-cloud-transitions.json`
  - [ ] `.sisyphus/evidence/task-10-missing-timestamp-error.log`

  **Commit**: YES
  - Message: `feat(analysis): extract mode and cloud transition events`
  - Files: `scripts/protocol_analysis/extract_mode_transitions.py`, `scripts/protocol_analysis/assert_transition_integrity.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_mode_transitions.py -q`

- [ ] 11. Edge-case detector for timeout/eof/error/disconnect/NACK

  **What to do**:
  - Implement detector that flags protocol edge cases and creates per-class counts and samples.
  - Map each edge case to probable branch source (`cloud_forwarder`, `proxy`, `control_settings`).

  **Must NOT do**:
  - Do not treat absence of an edge case as failure without context.
  - Do not conflate timeout and EOF categories.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: multi-source event correlation and classification.
  - **Skills**: [`git-master`]
    - `git-master`: safe evolution of detection rules and fixtures.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no UI/browser.
    - `dev-browser`: no UI/browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 10, 12)
  - **Blocks**: 15, 16, 18
  - **Blocked By**: 2, 5, 6

  **References**:
  - `addon/oig-proxy/cloud_forwarder.py` - timeout/eof/error handlers.
  - `addon/oig-proxy/proxy.py` - box disconnect and read timeout behavior.
  - `addon/oig-proxy/control_settings.py` - ACK/NACK handling path.
  - `addon/oig-proxy/telemetry_client.py` - error event names.

  **Acceptance Criteria**:
  - [ ] Detector report exists at `.sisyphus/evidence/task-11-edge-cases.json`.
  - [ ] Report includes categories: `cloud_timeout`, `cloud_eof`, `cloud_error`, `box_disconnect`, `nack`.
  - [ ] `python scripts/protocol_analysis/detect_edge_cases.py --frames .sisyphus/evidence/task-7-day1-frames.json --telemetry .sisyphus/evidence/task-8-telemetry-snapshot.json --out .sisyphus/evidence/task-11-edge-cases.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Edge-case detection happy path
    Tool: Bash (python)
    Preconditions: Frame and telemetry snapshots available.
    Steps:
      1. Run edge-case detector command.
      2. Run `python scripts/protocol_analysis/assert_edge_case_report.py --input .sisyphus/evidence/task-11-edge-cases.json`.
      3. Assert report schema and category keys are valid.
    Expected Result: Edge-case report generated with counts and samples.
    Failure Indicators: Missing categories, invalid JSON, zero schema fields.
    Evidence: .sisyphus/evidence/task-11-edge-cases.json

  Scenario: Corrupted telemetry input failure path
    Tool: Bash (python)
    Preconditions: Invalid JSON telemetry fixture.
    Steps:
      1. Run detector with corrupted telemetry input.
      2. Assert non-zero exit and message `invalid telemetry json`.
    Expected Result: Tool fails fast and reports parse issue.
    Evidence: .sisyphus/evidence/task-11-invalid-telemetry-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-11-edge-cases.json`
  - [ ] `.sisyphus/evidence/task-11-invalid-telemetry-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add protocol edge-case detection`
  - Files: `scripts/protocol_analysis/detect_edge_cases.py`, `scripts/protocol_analysis/assert_edge_case_report.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_edge_case_detection.py -q`

- [ ] 12. Daily validation CLI with hard pass/fail thresholds

  **What to do**:
  - Implement a single validation CLI that checks daily capture health, schema integrity, signal presence, and minimum sample thresholds.
  - Output both human-readable summary and machine-readable pass/fail JSON.

  **Must NOT do**:
  - Do not use vague pass criteria.
  - Do not silently downgrade failures to warnings.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: orchestrates multiple validation checks with strict gating.
  - **Skills**: [`git-master`]
    - `git-master`: helps manage multi-file CLI/test changes cleanly.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no UI/browser.
    - `dev-browser`: no UI/browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 10, 11)
  - **Blocks**: 18
  - **Blocked By**: 1, 2, 5, 6

  **References**:
  - `docs/telemetry_overview.md` - expected telemetry dimensions and failure modes.
  - `analysis/ha_snapshot/payloads_ha_full.db` - baseline for threshold calibration.
  - `testing/README.md` - existing metric/validation expectations.

  **Acceptance Criteria**:
  - [ ] Validation output exists at `.sisyphus/evidence/task-12-daily-validation.json`.
  - [ ] CLI exit code is 0 on pass, non-zero on fail.
  - [ ] Thresholds include at least: min frame count, required signal classes, max null-rate for critical fields.
  - [ ] Command `python scripts/protocol_analysis/validate_daily_collection.py --frames .sisyphus/evidence/task-7-day1-frames.json --telemetry .sisyphus/evidence/task-8-telemetry-snapshot.json --out .sisyphus/evidence/task-12-daily-validation.json` exits deterministically.

  **QA Scenarios**:
  ```bash
  Scenario: Daily validation happy path
    Tool: Bash (python)
    Preconditions: Day-slice and telemetry snapshot files are valid.
    Steps:
      1. Run daily validation CLI.
      2. Assert exit code 0.
      3. Assert JSON output contains `status=PASS` and threshold details.
    Expected Result: Deterministic pass with explicit threshold report.
    Failure Indicators: Missing thresholds, inconsistent status vs exit code.
    Evidence: .sisyphus/evidence/task-12-daily-validation.json

  Scenario: Threshold breach failure path
    Tool: Bash (python)
    Preconditions: Fixture with removed signal class and low frame count.
    Steps:
      1. Run validation CLI on failing fixture.
      2. Assert non-zero exit and `status=FAIL` with failed checks list.
    Expected Result: Hard fail with actionable failure reasons.
    Evidence: .sisyphus/evidence/task-12-threshold-fail.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-12-daily-validation.json`
  - [ ] `.sisyphus/evidence/task-12-threshold-fail.log`

  **Commit**: YES
  - Message: `feat(analysis): add strict daily collection validator`
  - Files: `scripts/protocol_analysis/validate_daily_collection.py`, `tests/protocol_analysis/test_daily_validation.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_daily_validation.py -q`

- [ ] 13. Standard request cloud response matrix (tbl_* families)

  **What to do**:
  - Build matrix for standard table requests (`tbl_actual`, `tbl_*`, `tbl_*_prms`) showing observed cloud response classes and rates.
  - Include confidence and mismatch counters based on pairing quality.

  **Must NOT do**:
  - Do not merge `tbl_*` and `tbl_*_prms` classes without explicit rationale.
  - Do not hide low-frequency mismatches.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: statistical aggregation with protocol-class boundaries.
  - **Skills**: [`git-master`]
    - `git-master`: controlled updates for analysis logic and expected fixtures.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 14, 15, 16, 17)
  - **Blocks**: 16, 19
  - **Blocked By**: 3, 4, 7, 9

  **References**:
  - `testing/comparison_suite.py` - existing contract and request class patterns.
  - `addon/oig-proxy/cloud_forwarder.py` - cloud response forwarding semantics.
  - `.sisyphus/evidence/task-4-pairing-sample.json` - pairing substrate.

  **Acceptance Criteria**:
  - [ ] Matrix exists at `.sisyphus/evidence/task-13-standard-request-matrix.json`.
  - [ ] Matrix includes columns: `request_class`, `response_class`, `count`, `rate_pct`, `confidence_bucket`.
  - [ ] `python scripts/protocol_analysis/build_standard_matrix.py --pairing .sisyphus/evidence/task-4-pairing-sample.json --out .sisyphus/evidence/task-13-standard-request-matrix.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Standard matrix happy path
    Tool: Bash (python)
    Preconditions: Pairing sample exists and passes quality checks.
    Steps:
      1. Run matrix builder command.
      2. Run `python scripts/protocol_analysis/assert_matrix_schema.py --input .sisyphus/evidence/task-13-standard-request-matrix.json --require-class tbl_actual`.
      3. Assert `STATUS: PASS`.
    Expected Result: Valid standard request matrix with rates.
    Failure Indicators: Missing required request classes, invalid percentages.
    Evidence: .sisyphus/evidence/task-13-standard-request-matrix.json

  Scenario: Empty pairing input failure path
    Tool: Bash (python)
    Preconditions: Provide empty pairing fixture.
    Steps:
      1. Run matrix builder with empty input.
      2. Assert non-zero exit and message `no pairings available`.
    Expected Result: Explicit failure for empty analytical basis.
    Evidence: .sisyphus/evidence/task-13-empty-input-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-13-standard-request-matrix.json`
  - [ ] `.sisyphus/evidence/task-13-empty-input-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add standard request response matrix`
  - Files: `scripts/protocol_analysis/build_standard_matrix.py`, `scripts/protocol_analysis/assert_matrix_schema.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_standard_matrix.py -q`

- [ ] 14. Signal reaction matrix by mode and condition

  **What to do**:
  - Build matrix that maps each target signal to cloud/local reaction branches under `online`, `hybrid-online`, `hybrid-offline`, and `offline` states.
  - Include explicit handling rows for timeout, EOF, cloud_error, and pending-setting injection.

  **Must NOT do**:
  - Do not report signal behavior without mode context.
  - Do not collapse timeout/EOF/cloud_error into one error class.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: multi-dimensional matrix from timeline + transition + branch evidence.
  - **Skills**: [`git-master`]
    - `git-master`: version control discipline for high-impact analysis outputs.
  - **Skills Evaluated but Omitted**:
    - `playwright`: not needed.
    - `dev-browser`: not needed.
    - `frontend-ui-ux`: not needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 15, 16, 17)
  - **Blocks**: 16, 19
  - **Blocked By**: 3, 4, 9, 10

  **References**:
  - `addon/oig-proxy/cloud_forwarder.py` - branch-level signal handling rules.
  - `addon/oig-proxy/proxy.py` - mode routing and offline processing.
  - `addon/oig-proxy/hybrid_mode.py` - state transition semantics.
  - `.sisyphus/evidence/task-9-signal-timeline.json` and `.sisyphus/evidence/task-10-mode-cloud-transitions.json`.

  **Acceptance Criteria**:
  - [ ] Matrix exists at `.sisyphus/evidence/task-14-signal-reaction-matrix.json`.
  - [ ] Each target signal has mode-specific rows with `observed_response`, `fallback_behavior`, `evidence_refs`.
  - [ ] `python scripts/protocol_analysis/build_signal_reaction_matrix.py --timeline .sisyphus/evidence/task-9-signal-timeline.json --transitions .sisyphus/evidence/task-10-mode-cloud-transitions.json --out .sisyphus/evidence/task-14-signal-reaction-matrix.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Signal reaction matrix happy path
    Tool: Bash (python)
    Preconditions: Signal timeline and mode transitions artifacts exist.
    Steps:
      1. Run signal reaction matrix builder.
      2. Validate output with `python scripts/protocol_analysis/assert_signal_reaction_matrix.py --input .sisyphus/evidence/task-14-signal-reaction-matrix.json`.
      3. Assert all target signals and all mode buckets are present.
    Expected Result: Complete signal/mode matrix with branch evidence.
    Failure Indicators: Missing mode rows, missing signal classes, invalid branch labels.
    Evidence: .sisyphus/evidence/task-14-signal-reaction-matrix.json

  Scenario: Missing transition data failure path
    Tool: Bash (python)
    Preconditions: Provide timeline without transition file.
    Steps:
      1. Run builder with missing transitions input.
      2. Assert non-zero exit and `transition input required`.
    Expected Result: Tool enforces mode-context dependency.
    Evidence: .sisyphus/evidence/task-14-missing-transitions-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-14-signal-reaction-matrix.json`
  - [ ] `.sisyphus/evidence/task-14-missing-transitions-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add signal reaction matrix by mode`
  - Files: `scripts/protocol_analysis/build_signal_reaction_matrix.py`, `scripts/protocol_analysis/assert_signal_reaction_matrix.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_signal_reaction_matrix.py -q`

- [ ] 15. Blind-spot quantification and reconstruction confidence scoring

  **What to do**:
  - Quantify known blind spots (missing parser meta fields, proxy_to_cloud visibility gap, unmatched pairs) and estimate impact on protocol reconstruction.
  - Produce confidence score with explicit weighting and limitations.

  **Must NOT do**:
  - Do not present confidence score without listing assumptions.
  - Do not hide low-confidence dimensions.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: risk-weighted scoring model with explicit uncertainty.
  - **Skills**: [`git-master`]
    - `git-master`: helps maintain traceability for scoring logic revisions.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 14, 16, 17)
  - **Blocks**: 16, 17, 19
  - **Blocked By**: 11, 13, 14

  **References**:
  - `addon/oig-proxy/parser.py` - skipped fields and parsing limitations.
  - `addon/oig-proxy/utils.py` - captured columns and direction model.
  - `.sisyphus/evidence/task-2-capture-audit.json` - completeness baseline.
  - `.sisyphus/evidence/task-11-edge-cases.json` - edge-case coverage evidence.

  **Acceptance Criteria**:
  - [ ] Score file exists at `.sisyphus/evidence/task-15-confidence-score.json`.
  - [ ] Score file includes `overall_score`, `dimension_scores`, `assumptions`, `blind_spot_impacts`.
  - [ ] `python scripts/protocol_analysis/score_reconstruction_confidence.py --inputs .sisyphus/evidence --out .sisyphus/evidence/task-15-confidence-score.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Confidence scoring happy path
    Tool: Bash (python)
    Preconditions: Required evidence files from Tasks 2, 11, 13, 14 exist.
    Steps:
      1. Run confidence scoring command.
      2. Validate with `python scripts/protocol_analysis/assert_confidence_score.py --input .sisyphus/evidence/task-15-confidence-score.json --min-dimensions 4`.
      3. Assert validator reports `STATUS: PASS`.
    Expected Result: Transparent confidence score with dimension breakdown.
    Failure Indicators: Missing assumptions, missing blind-spot impacts, invalid score range.
    Evidence: .sisyphus/evidence/task-15-confidence-score.json

  Scenario: Missing prerequisite evidence failure path
    Tool: Bash (python)
    Preconditions: Remove one required input path via fixture.
    Steps:
      1. Run scorer with incomplete evidence set.
      2. Assert non-zero exit and message `missing prerequisite evidence`.
    Expected Result: Scorer blocks partial evidence runs.
    Evidence: .sisyphus/evidence/task-15-missing-prereq-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-15-confidence-score.json`
  - [ ] `.sisyphus/evidence/task-15-missing-prereq-error.log`

  **Commit**: YES
  - Message: `feat(analysis): quantify blind spots and confidence score`
  - Files: `scripts/protocol_analysis/score_reconstruction_confidence.py`, `scripts/protocol_analysis/assert_confidence_score.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_confidence_score.py -q`

- [ ] 16. 3-day drift and anomaly comparative report

  **What to do**:
  - Compare day1/day2/day3 distributions (request classes, response classes, edge-case frequencies, mode transitions) and detect drift.
  - Produce anomaly report with severity levels and potential protocol-risk interpretations.

  **Must NOT do**:
  - Do not classify anomalies without baseline context.
  - Do not escalate low-sample outliers as critical without confidence tags.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: multi-day statistical comparison and risk annotation.
  - **Skills**: [`git-master`]
    - `git-master`: maintain clean diff for report generation logic.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 14, 15, 17)
  - **Blocks**: 19
  - **Blocked By**: 7, 8, 9, 10, 11, 13, 14, 15

  **References**:
  - `.sisyphus/evidence/task-7-day*-frames.json` - daily frame slices.
  - `.sisyphus/evidence/task-8-telemetry-snapshot.json` - telemetry windows.
  - `.sisyphus/evidence/task-13-standard-request-matrix.json`
  - `.sisyphus/evidence/task-14-signal-reaction-matrix.json`
  - `.sisyphus/evidence/task-15-confidence-score.json`

  **Acceptance Criteria**:
  - [ ] Report exists at `.sisyphus/evidence/task-16-drift-anomaly-report.json`.
  - [ ] Report includes `metric_deltas`, `anomaly_list`, `severity`, `confidence`, `recommendation`.
  - [ ] `python scripts/protocol_analysis/build_drift_report.py --day1 .sisyphus/evidence/task-7-day1-frames.json --day2 .sisyphus/evidence/task-7-day2-frames.json --day3 .sisyphus/evidence/task-7-day3-frames.json --out .sisyphus/evidence/task-16-drift-anomaly-report.json` exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Drift report happy path
    Tool: Bash (python)
    Preconditions: Three day slices and prerequisite matrices exist.
    Steps:
      1. Run drift report builder.
      2. Validate with `python scripts/protocol_analysis/assert_drift_report.py --input .sisyphus/evidence/task-16-drift-anomaly-report.json`.
      3. Assert output includes at least one delta section and valid severity enum.
    Expected Result: Multi-day drift report with machine-parseable anomalies.
    Failure Indicators: Missing day comparison blocks, invalid severity labels.
    Evidence: .sisyphus/evidence/task-16-drift-anomaly-report.json

  Scenario: Non-overlapping day windows failure path
    Tool: Bash (python)
    Preconditions: Provide invalid/overlapping date fixtures.
    Steps:
      1. Run report builder with malformed day windows.
      2. Assert non-zero exit and message `invalid day window alignment`.
    Expected Result: Tool rejects invalid day comparisons.
    Evidence: .sisyphus/evidence/task-16-window-alignment-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-16-drift-anomaly-report.json`
  - [ ] `.sisyphus/evidence/task-16-window-alignment-error.log`

  **Commit**: YES
  - Message: `feat(analysis): add 3-day drift and anomaly reporting`
  - Files: `scripts/protocol_analysis/build_drift_report.py`, `scripts/protocol_analysis/assert_drift_report.py`
  - Pre-commit: `pytest tests/protocol_analysis/test_drift_report.py -q`

- [ ] 17. Protocol data-adjustment backlog (safe changes only)

  **What to do**:
  - Produce prioritized backlog of data-collection/protocol-observability adjustments based on evidence (no runtime implementation in this phase).
  - For each proposed adjustment, include expected benefit, risk, and required validation gate.

  **Must NOT do**:
  - Do not implement runtime protocol changes in this task.
  - Do not include out-of-scope infrastructure redesign items.

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: synthesis and prioritization output from completed evidence.
  - **Skills**: [`git-master`]
    - `git-master`: keeps backlog document updates clean and auditable.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 14, 15, 16)
  - **Blocks**: 19, 20
  - **Blocked By**: 15, 16

  **References**:
  - `.sisyphus/evidence/task-14-signal-reaction-matrix.json` - behavior gaps.
  - `.sisyphus/evidence/task-15-confidence-score.json` - confidence and blind spots.
  - `.sisyphus/evidence/task-16-drift-anomaly-report.json` - operational drift risks.
  - `addon/oig-proxy/utils.py` and `addon/oig-proxy/parser.py` - likely adjustment loci.

  **Acceptance Criteria**:
  - [ ] Backlog exists at `.sisyphus/evidence/task-17-adjustment-backlog.md`.
  - [ ] Each item has fields: `priority`, `benefit`, `risk`, `required_evidence`, `rollout_gate`.
  - [ ] Backlog contains at least one low-risk quick win and one high-impact medium-risk item.

  **QA Scenarios**:
  ```bash
  Scenario: Backlog synthesis happy path
    Tool: Bash (python)
    Preconditions: Tasks 14-16 evidence files are present.
    Steps:
      1. Run `python scripts/protocol_analysis/build_adjustment_backlog.py --evidence .sisyphus/evidence --out .sisyphus/evidence/task-17-adjustment-backlog.md`.
      2. Run `python scripts/protocol_analysis/assert_backlog_structure.py --input .sisyphus/evidence/task-17-adjustment-backlog.md`.
      3. Assert `STATUS: PASS`.
    Expected Result: Structured, prioritized backlog with rollout gates.
    Failure Indicators: Missing risk/gate fields, no prioritization, no evidence links.
    Evidence: .sisyphus/evidence/task-17-adjustment-backlog.md

  Scenario: Missing evidence dependency failure path
    Tool: Bash (python)
    Preconditions: Remove one required evidence input.
    Steps:
      1. Run backlog builder with incomplete evidence directory.
      2. Assert non-zero exit and message `missing required evidence`.
    Expected Result: Backlog generation blocked until evidence is complete.
    Evidence: .sisyphus/evidence/task-17-missing-evidence-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-17-adjustment-backlog.md`
  - [ ] `.sisyphus/evidence/task-17-missing-evidence-error.log`

  **Commit**: YES
  - Message: `docs(analysis): produce protocol data-adjustment backlog`
  - Files: `scripts/protocol_analysis/build_adjustment_backlog.py`, `.sisyphus/evidence/task-17-adjustment-backlog.md`
  - Pre-commit: `pytest tests/protocol_analysis/test_adjustment_backlog.py -q`

- [ ] 18. Tooling validation on historical and live sample datasets

  **What to do**:
  - Run all implemented analysis tools on historical DB and at least one live sample day export to confirm reproducibility.
  - Capture pass/fail matrix for each tool and each dataset.

  **Must NOT do**:
  - Do not validate only on one dataset type.
  - Do not skip failure evidence when a tool fails.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: broad integration verification across many artifacts.
  - **Skills**: [`git-master`]
    - `git-master`: supports clean fixes if validation reveals defects.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 19, 20)
  - **Blocks**: 19
  - **Blocked By**: 7, 8, 9, 10, 11, 12

  **References**:
  - `analysis/ha_snapshot/payloads_ha_full.db` - historical validation dataset.
  - `testing/export_ha_session.py` - live sample export pattern.
  - `.sisyphus/evidence/task-12-daily-validation.json` - gate logic baseline.

  **Acceptance Criteria**:
  - [ ] Validation matrix exists at `.sisyphus/evidence/task-18-tool-validation-matrix.json`.
  - [ ] Matrix includes per-tool status for both `historical` and `live_sample` datasets.
  - [ ] `python scripts/protocol_analysis/run_full_validation_matrix.py --out .sisyphus/evidence/task-18-tool-validation-matrix.json` exits 0 only when all required checks pass.

  **QA Scenarios**:
  ```bash
  Scenario: Full tooling validation happy path
    Tool: Bash (python)
    Preconditions: All task artifacts from Waves 1-3 exist.
    Steps:
      1. Run full validation matrix command.
      2. Assert output JSON includes both datasets and all tool rows.
      3. Assert summary has `required_pass=true`.
    Expected Result: End-to-end tooling reproducibility proven.
    Failure Indicators: Missing dataset section, skipped tool, ambiguous status.
    Evidence: .sisyphus/evidence/task-18-tool-validation-matrix.json

  Scenario: Intentional broken tool failure path
    Tool: Bash (python)
    Preconditions: Use test mode with one intentionally invalid input.
    Steps:
      1. Run validation matrix in fault-injection mode.
      2. Assert non-zero exit and failed tool explicitly listed.
    Expected Result: Harness correctly reports failing component.
    Evidence: .sisyphus/evidence/task-18-fault-injection-fail.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-18-tool-validation-matrix.json`
  - [ ] `.sisyphus/evidence/task-18-fault-injection-fail.log`

  **Commit**: YES
  - Message: `test(analysis): validate tooling across historical and live samples`
  - Files: `scripts/protocol_analysis/run_full_validation_matrix.py`, `.sisyphus/evidence/task-18-tool-validation-matrix.json`
  - Pre-commit: `pytest tests/protocol_analysis -q`

- [ ] 19. Final analysis package and Czech executive brief

  **What to do**:
  - Assemble final package combining matrices, confidence score, drift report, and backlog into a single structured handoff directory.
  - Write executive summary in Czech focused on protocol understanding level, key risks, and next recommended safe actions.

  **Must NOT do**:
  - Do not omit explicit limitations from the summary.
  - Do not present confidence as absolute certainty.

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: final synthesis and stakeholder-oriented summary.
  - **Skills**: [`git-master`]
    - `git-master`: clean packaging commit for release-ready artifact.
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser.
    - `dev-browser`: no browser.
    - `frontend-ui-ux`: no UI.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential integrator in Wave 4
  - **Blocks**: 20, F1-F4
  - **Blocked By**: 13, 14, 15, 16, 17, 18

  **References**:
  - `.sisyphus/evidence/task-13-standard-request-matrix.json`
  - `.sisyphus/evidence/task-14-signal-reaction-matrix.json`
  - `.sisyphus/evidence/task-15-confidence-score.json`
  - `.sisyphus/evidence/task-16-drift-anomaly-report.json`
  - `.sisyphus/evidence/task-17-adjustment-backlog.md`
  - `.sisyphus/evidence/task-18-tool-validation-matrix.json`

  **Acceptance Criteria**:
  - [ ] Final package index exists at `.sisyphus/evidence/task-19-analysis-package-index.json`.
  - [ ] Czech executive brief exists at `.sisyphus/evidence/task-19-executive-brief-cs.md`.
  - [ ] Package index references all required upstream artifacts and checksum fields.

  **QA Scenarios**:
  ```bash
  Scenario: Final packaging happy path
    Tool: Bash (python)
    Preconditions: Tasks 13-18 outputs exist.
    Steps:
      1. Run `python scripts/protocol_analysis/assemble_final_package.py --evidence .sisyphus/evidence --out .sisyphus/evidence/task-19-analysis-package-index.json`.
      2. Run `python scripts/protocol_analysis/assert_final_package.py --index .sisyphus/evidence/task-19-analysis-package-index.json --brief .sisyphus/evidence/task-19-executive-brief-cs.md`.
      3. Assert `STATUS: PASS` and all required artifact refs resolved.
    Expected Result: Complete package with Czech summary and traceable references.
    Failure Indicators: Missing required artifact, missing brief, broken references.
    Evidence: .sisyphus/evidence/task-19-analysis-package-index.json

  Scenario: Missing upstream artifact failure path
    Tool: Bash (python)
    Preconditions: Remove one required upstream file in test fixture.
    Steps:
      1. Run package assembler.
      2. Assert non-zero exit and missing artifact listed.
    Expected Result: Packaging fails fast on incomplete inputs.
    Evidence: .sisyphus/evidence/task-19-missing-artifact-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-19-analysis-package-index.json`
  - [ ] `.sisyphus/evidence/task-19-executive-brief-cs.md`
  - [ ] `.sisyphus/evidence/task-19-missing-artifact-error.log`

  **Commit**: YES
  - Message: `docs(analysis): publish final passive protocol analysis package`
  - Files: `scripts/protocol_analysis/assemble_final_package.py`, `.sisyphus/evidence/task-19-executive-brief-cs.md`
  - Pre-commit: `pytest tests/protocol_analysis/test_final_package.py -q`

- [ ] 20. Handoff checklist and follow-up execution gates

  **What to do**:
  - Produce handoff checklist for next execution sprint including gates for safe rollout of any backlog item.
  - Include explicit stop conditions and rollback criteria if future changes impact communication stability.

  **Must NOT do**:
  - Do not include implementation tasks beyond analysis handoff scope.
  - Do not leave gate criteria qualitative-only.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: concise operational handoff artifact.
  - **Skills**: [`git-master`]
    - `git-master`: ensures handoff docs and gating checklist are versioned cleanly.
  - **Skills Evaluated but Omitted**:
    - `playwright`: not needed.
    - `dev-browser`: not needed.
    - `frontend-ui-ux`: not needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 18)
  - **Blocks**: F1-F4
  - **Blocked By**: 17, 19

  **References**:
  - `.sisyphus/evidence/task-17-adjustment-backlog.md`
  - `.sisyphus/evidence/task-19-executive-brief-cs.md`
  - `.sisyphus/plans/oig-protocol-3day-passive-analysis.md` - guardrails and success criteria.

  **Acceptance Criteria**:
  - [ ] Handoff checklist exists at `.sisyphus/evidence/task-20-handoff-checklist.md`.
  - [ ] Checklist includes `entry_gates`, `exit_gates`, `rollback_triggers`, `owner`, `evidence_required`.
  - [ ] Checklist validator exits 0.

  **QA Scenarios**:
  ```bash
  Scenario: Handoff checklist happy path
    Tool: Bash (python)
    Preconditions: Final package and backlog outputs are available.
    Steps:
      1. Run `python scripts/protocol_analysis/build_handoff_checklist.py --backlog .sisyphus/evidence/task-17-adjustment-backlog.md --brief .sisyphus/evidence/task-19-executive-brief-cs.md --out .sisyphus/evidence/task-20-handoff-checklist.md`.
      2. Run `python scripts/protocol_analysis/assert_handoff_checklist.py --input .sisyphus/evidence/task-20-handoff-checklist.md`.
      3. Assert `STATUS: PASS`.
    Expected Result: Actionable gate-driven handoff checklist.
    Failure Indicators: Missing rollback triggers, missing evidence requirements, missing owner.
    Evidence: .sisyphus/evidence/task-20-handoff-checklist.md

  Scenario: Missing rollback section failure path
    Tool: Bash (python)
    Preconditions: Fixture checklist missing rollback section.
    Steps:
      1. Run checklist validator on broken fixture.
      2. Assert non-zero exit and message `rollback section required`.
    Expected Result: Validator enforces mandatory safety content.
    Evidence: .sisyphus/evidence/task-20-missing-rollback-error.log
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-20-handoff-checklist.md`
  - [ ] `.sisyphus/evidence/task-20-missing-rollback-error.log`

  **Commit**: YES
  - Message: `docs(analysis): add handoff gates for follow-up execution`
  - Files: `scripts/protocol_analysis/build_handoff_checklist.py`, `.sisyphus/evidence/task-20-handoff-checklist.md`
  - Pre-commit: `pytest tests/protocol_analysis/test_handoff_checklist.py -q`

---

## Final Verification Wave (MANDATORY - after ALL implementation tasks)

- [ ] F1. **Plan Compliance Audit** - `oracle`
  Validate all Must Have/Must NOT Have requirements against produced artifacts and evidence files.

- [ ] F2. **Code Quality Review** - `unspecified-high`
  Run `pytest`, lint/type checks for any new analysis scripts, and validate no unsafe runtime mutations were introduced.

- [ ] F3. **Real Manual QA** - `unspecified-high`
  Execute every QA scenario from Tasks 1-20 and verify evidence files exist in `.sisyphus/evidence/`.

- [ ] F4. **Scope Fidelity Check** - `deep`
  Confirm all outputs match passive-analysis scope and no out-of-scope implementation slipped in.

---

## Commit Strategy

- **Commit 1 (Wave 1)**: `docs(analysis): define passive protocol baseline and safety guardrails`
- **Commit 2 (Wave 2)**: `feat(analysis): add 3-day collection and validation tooling`
- **Commit 3 (Wave 3)**: `feat(analysis): generate signal matrices and confidence scoring`
- **Commit 4 (Wave 4)**: `docs(analysis): publish final package and handoff checklist`

---

## Success Criteria

### Verification Commands
```bash
python scripts/protocol_analysis/validate_daily_collection.py --frames <day-frames.json> --telemetry <day-telemetry.json> --out .sisyphus/evidence/daily-validation.json
# Expected: PASS with threshold summary

python scripts/protocol_analysis/build_signal_reaction_matrix.py --timeline .sisyphus/evidence/task-9-signal-timeline.json --transitions .sisyphus/evidence/task-10-mode-cloud-transitions.json --out .sisyphus/evidence/task-14-signal-reaction-matrix.json
# Expected: matrix file generated, all target signals present

python scripts/protocol_analysis/score_reconstruction_confidence.py --inputs .sisyphus/evidence --out .sisyphus/evidence/task-15-confidence-score.json
# Expected: confidence score + explicit blind spots

pytest tests/protocol_analysis -q
# Expected: all tests pass
```

### Final Checklist
- [ ] All Must Have items satisfied
- [ ] All Must NOT Have items satisfied
- [ ] Daily validation gates passed for all 3 days
- [ ] Signal reaction matrix complete for target signal set
- [ ] Target signal coverage: 100% for `IsNewSet`, `IsNewWeather`, `IsNewFW`, `END`, ACK/NACK with at least 10 observed instances per class across 3 days
- [ ] Reconstruction confidence target met: `overall_score >= 0.85` with no dimension below `0.70`
- [ ] Blind spots quantified with explicit impact statement
- [ ] Final analysis package and Czech executive brief delivered
