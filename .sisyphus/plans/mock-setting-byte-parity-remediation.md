# Mock Setting Byte-Parity Remediation Plan

## TL;DR

> **Quick Summary**: Stabilize mock Setting delivery by making outbound protocol behavior byte-parity compatible with live cloud handshake, then prove acceptance with replay and on-box verification.
>
> **Deliverables**:
> - Forensics-grade inbound/outbound captures with deterministic trace IDs
> - Deterministic Setting state-machine guardrails preventing END/Setting flow collisions
> - Replay-based parity validator and acceptance gate report
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 4 waves + Final Verification Wave
> **Critical Path**: T1 -> T6 -> T11 -> T15 -> T18 -> F1/F2/F3/F4

---

## Context

### Original Request
User reports mock Setting path is unreliable despite sending seemingly same payload as cloud, suspects communication disorder around END vs Setting delivery, and requests a concrete remediation plan.

### Interview Summary
**Key Discussions**:
- Live online data already confirms stable `MODE -> ACK(Reason=Setting) -> END` behavior across multiple same-day changes.
- Mock-side test failed with retries and fail-closed, indicating mock protocol parity gap rather than BOX business logic issue.
- User requires safe online posture: no proxy routing experiments during analysis beyond controlled mock testing.

**Research Findings**:
- Live cloud path shows fast ACK latency (~6.6-7.4s) and full handshake completion.
- Mock logs show setting delivery attempts without `ACK(Reason=Setting)` followed by timeout retries and eventual failure.
- Current mock response logic mixes poll handling and pending-setting handling in one flow and needs stricter state gating.

### Metis Review
**Identified Gaps (addressed in this plan)**:
- Missing explicit byte-level parity criteria (not just semantic parity).
- Missing outbound raw-frame forensic evidence requirements.
- Missing explicit scope guard against “feature creep” beyond protocol parity.
- Missing concurrency/race scenarios during active delivery windows.

---

## Work Objectives

### Core Objective
Make mock diagnostics server reproduce live cloud Setting handshake with byte-level fidelity where required and deterministic state behavior under retries, then verify with reproducible evidence.

### Concrete Deliverables
- Updated mock server flow in `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` with strict delivery and END-guard behavior.
- Forensics artifacts under `.sisyphus/evidence/mock-setting-parity/` capturing inbound+outbound traces.
- Replay and gate scripts under `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/` for repeatable validation.
- Protocol test coverage under `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/` for acceptance + negative/race conditions.

### Definition of Done
- [ ] Controlled mock test of MODE change reaches `ACK(Reason=Setting)` then `END` without fail-closed retries.
- [ ] Replay validator reports parity pass for designated golden sequences.
- [ ] Gate report marks PASS with no unresolved critical mismatch.

### Must Have
- Byte-level forensic visibility for outbound responses and inbound acknowledgements.
- Deterministic state transitions with explicit prohibition of END emission while waiting for valid Setting ACK.
- Strict gating for Setting delivery trigger path (default: IsNewSet-only for pending/retry delivery).

### Must NOT Have (Guardrails)
- No changes to online proxy routing behavior in `/Users/martinhorak/Projects/oig-proxy/`.
- No “new features” unrelated to setting parity (UI, telemetry redesign, protocol extensions).
- No manual-only acceptance; all verification must be command/tool executable with evidence files.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: YES (TDD for protocol guardrails, tests-after for replay and integration)
- **Framework**: pytest + bash/curl + tmux logging
- **If TDD**: RED -> GREEN -> REFACTOR for newly introduced guardrails

### QA Policy
Every task includes agent-executed QA scenarios with evidence in:
`.sisyphus/evidence/mock-setting-parity/task-{N}-{scenario-slug}.{ext}`

| Deliverable Type | Verification Tool | Method |
|------------------|-------------------|--------|
| Mock protocol logic | Bash + pytest | Run focused protocol tests, assert handshake/state transitions |
| Runtime behavior | Bash (curl) | Queue setting, poll APIs, assert state progression and completion |
| Sequence fidelity | Bash + Python scripts | Replay and compare expected/actual frame details |
| Log/state race checks | interactive_bash (tmux) | Tail logs during active flow and assert no forbidden transitions |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — evidence + scaffolding):
├── T1: Establish forensic evidence schema + paths [quick]
├── T2: Capture live golden windows and annotate handshake [quick]
├── T3: Capture current mock outbound/inbound baseline [quick]
├── T4: Build parity diff utility for frame-level comparison [quick]
├── T5: Add protocol fixture bundles for replay cases [quick]
└── T6: Define explicit state-machine transition contract doc [quick]

Wave 2 (After Wave 1 — analysis + targeted protocol guardrails):
├── T7: Implement outbound response forensic logging [unspecified-high]
├── T8: Implement END emission guard during DELIVERED/PENDING [deep]
├── T9: Enforce IsNewSet-only delivery gate for pending/retry [deep]
├── T10: Stabilize retry/backoff transitions and fail-closed evidence [unspecified-high]
├── T11: Normalize envelope composition against golden parity rules [deep]
└── T12: Add ACK matcher hardening and linkage assertions [quick]

Wave 3 (After Wave 2 — replay + protocol tests):
├── T13: Add positive acceptance replay tests (MODE->ACK->END) [deep]
├── T14: Add negative/race tests (END collision, malformed ACK) [deep]
├── T15: Add integration runner for controlled MODE test on mock [quick]
├── T16: Add parity gate evaluator and report output [quick]
└── T17: Add rollback/cleanup automation for failed runs [quick]

Wave 4 (After Wave 3 — staged validation):
├── T18: Execute staged mock run and collect full evidence pack [unspecified-high]
├── T19: Execute replay against golden sequences and validate parity [unspecified-high]
└── T20: Produce final remediation report with residual risks [writing]

Wave FINAL (After ALL tasks — independent review, 4 parallel):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality + test integrity review (unspecified-high)
├── F3: Full QA scenario replay with evidence checks (unspecified-high)
└── F4: Scope fidelity and no-creep verification (deep)

Critical Path: T1 -> T6 -> T11 -> T15 -> T18 -> F1/F2/F3/F4
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 6
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|------------|--------|------|
| T1 | — | T4, T7, T18 | 1 |
| T2 | — | T4, T11, T13 | 1 |
| T3 | — | T4, T8, T9 | 1 |
| T4 | T1, T2, T3 | T11, T13, T16, T19 | 1 |
| T5 | — | T13, T14 | 1 |
| T6 | — | T8, T9, T10, T12 | 1 |
| T7 | T1 | T18, T19 | 2 |
| T8 | T3, T6 | T13, T14, T18 | 2 |
| T9 | T3, T6 | T13, T14, T18 | 2 |
| T10 | T6 | T14, T18 | 2 |
| T11 | T2, T4 | T13, T15, T19 | 2 |
| T12 | T6 | T13, T14 | 2 |
| T13 | T4, T5, T8, T9, T11, T12 | T18, T19 | 3 |
| T14 | T5, T8, T9, T10, T12 | T18 | 3 |
| T15 | T11 | T18 | 3 |
| T16 | T4, T13, T14 | T19, T20 | 3 |
| T17 | T10, T14 | T18 | 3 |
| T18 | T1, T7, T8, T9, T10, T13, T14, T15, T17 | F1, F3, F4 | 4 |
| T19 | T4, T7, T11, T13, T16 | F1, F3 | 4 |
| T20 | T16, T18, T19 | F1, F4 | 4 |
| F1 | T18, T19, T20 | — | FINAL |
| F2 | T13, T14, T18 | — | FINAL |
| F3 | T18, T19 | — | FINAL |
| F4 | T18, T20 | — | FINAL |

### Agent Dispatch Summary

| Wave | # Parallel | Tasks → Agent Category |
|------|------------|------------------------|
| 1 | **6** | T1-T6 → `quick` |
| 2 | **6** | T7 → `unspecified-high`, T8/T9/T11 → `deep`, T10 → `unspecified-high`, T12 → `quick` |
| 3 | **5** | T13/T14 → `deep`, T15/T16/T17 → `quick` |
| 4 | **3** | T18/T19 → `unspecified-high`, T20 → `writing` |
| FINAL | **4** | F1 → `oracle`, F2/F3 → `unspecified-high`, F4 → `deep` |

---

## TODOs

- [ ] 1. Establish forensic evidence schema and storage layout

  **What to do**:
  - Create canonical evidence schema (request_id, conn_id, direction, raw_xml, crc, ts_local, ts_utc, state_before, state_after).
  - Define directory contract under `/Users/martinhorak/Projects/oig-diagnostic-cloud/.sisyphus/evidence/mock-setting-parity/`.
  - Define run manifest format linking all artifacts for one test run.

  **Must NOT do**:
  - Do not change runtime behavior yet.
  - Do not infer values from transformed logs; store raw first.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Documentation and schema scaffolding, low algorithmic complexity.
  - **Skills**: [`git-master`]
    - `git-master`: Enforces precise artifact naming, reproducible updates, and auditable diffs.
  - **Skills Evaluated but Omitted**:
    - `playwright`: No browser work.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T2, T3, T4, T5, T6)
  - **Blocks**: T4, T7, T18
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/.sisyphus/evidence/mock-setting-poc/` - Existing evidence naming and artifact style to stay consistent.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1495` - Current frame persistence shape to extend without breaking existing logs.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/evaluate_setting_gate.py` - Existing gate input/output structure.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/docs/SETTING_POC_ROLLBACK.md` - Existing operational evidence expectations.

  **Acceptance Criteria**:
  - [ ] Evidence schema doc exists and covers all mandatory fields.
  - [ ] Run manifest template exists and references all artifact types.
  - [ ] Naming convention validated with one generated dry-run manifest.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Schema contract generation (happy path)
    Tool: Bash
    Preconditions: Repo available at /Users/martinhorak/Projects/oig-diagnostic-cloud
    Steps:
      1. Render schema markdown/json examples to evidence directory.
      2. Validate JSON examples with python -m json.tool.
      3. Assert required keys exist: request_id, conn_id, direction, raw_xml, crc.
    Expected Result: Example schema files parse and contain all required keys.
    Failure Indicators: Missing keys or invalid JSON.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-1-schema-happy.json

  Scenario: Missing field validation (negative)
    Tool: Bash
    Preconditions: Schema validator script available.
    Steps:
      1. Validate malformed sample without crc and raw_xml.
      2. Assert validator returns non-zero and error list includes missing keys.
    Expected Result: Validation fails with explicit missing-field report.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-1-schema-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-1-schema-happy.json`
  - [ ] `task-1-schema-negative.txt`

  **Commit**: YES
  - Message: `chore(mock): define forensic evidence schema`
  - Files: `docs/`, `.sisyphus/evidence/mock-setting-parity/`
  - Pre-commit: `python3 -m json.tool`

- [ ] 2. Capture and annotate live golden handshake windows

  **What to do**:
  - Extract today’s live MODE windows from HA DB with full sequence context.
  - Annotate golden sequence boundaries: trigger, setting frame, ACK(Reason=Setting), END.
  - Store curated golden fixtures for replay.

  **Must NOT do**:
  - Do not mutate live services.
  - Do not include unrelated telemetry frames outside sequence windows.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Data extraction and annotation from known source.
  - **Skills**: [`git-master`]
    - `git-master`: Repeatable command logging and evidence tracking discipline.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not UI/browser oriented.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T3, T4, T5, T6)
  - **Blocks**: T4, T11, T13
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-proxy/testing/export_ha_session.py` - Existing HA export pattern and credentials usage.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/analysis/setting-acceptance-contract.yaml` - Invariants to preserve in curated windows.
  - `/tmp/setting-contract/success-window.json` - Prior successful window format for compatibility.

  **Acceptance Criteria**:
  - [ ] At least 3 same-day golden windows exported with full frame ordering.
  - [ ] Each golden window includes MODE frame, ACK(Reason=Setting), END linkage.
  - [ ] Fixture metadata includes source timestamp range and extraction query.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Golden extraction complete (happy path)
    Tool: Bash
    Preconditions: HA DB reachable read-only.
    Steps:
      1. Run extraction script for UTC date window.
      2. Verify output contains >=3 windows.
      3. Assert each window has mode_id, ack_id, end_id fields populated.
    Expected Result: Curated golden fixture passes structural checks.
    Failure Indicators: Missing linkage fields or empty window list.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-2-golden-happy.json

  Scenario: Corrupt window rejection (negative)
    Tool: Bash
    Preconditions: Validator available.
    Steps:
      1. Remove ack_id from one sample window.
      2. Run validator and assert failure with exact error location.
    Expected Result: Invalid window rejected with deterministic message.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-2-golden-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-2-golden-happy.json`
  - [ ] `task-2-golden-negative.txt`

  **Commit**: YES
  - Message: `chore(mock): add live golden setting fixtures`
  - Files: `analysis/`, `.sisyphus/evidence/mock-setting-parity/`
  - Pre-commit: `python3 -m json.tool`

- [ ] 3. Capture current mock baseline with outbound visibility

  **What to do**:
  - Run one controlled MODE=2 test against mock and capture inbound/outbound frames.
  - Record state-machine transitions and retry timeline from start to terminal state.
  - Produce baseline artifact bundle for diffing.

  **Must NOT do**:
  - Do not apply protocol fixes yet.
  - Do not run concurrent tests that contaminate baseline window.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Controlled data collection run.
  - **Skills**: [`git-master`]
    - `git-master`: Ensures reproducible command chronology and evidence traceability.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not applicable to TCP/API flow.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T2, T4, T5, T6)
  - **Blocks**: T4, T8, T9
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1530` - Response generation entry point.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1971` - Timeout/retry checker behavior.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/run_setting_poc.sh` - Existing run flow.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/export_setting_evidence.py` - Baseline evidence schema extension point.

  **Acceptance Criteria**:
  - [ ] Baseline run bundle contains request, all poll responses, retries, terminal state.
  - [ ] Transition timeline includes exact elapsed seconds between deliver and timeout.
  - [ ] Baseline manifest references all generated artifacts.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Baseline capture run (happy path)
    Tool: Bash
    Preconditions: Mock server running on NAS with API reachable.
    Steps:
      1. Reset setting state via /api/setting-reset.
      2. Queue MODE=2 via /api/queue-setting-burst.
      3. Poll /api/setting-state until terminal state and export artifacts.
    Expected Result: Complete baseline bundle produced with transition timeline.
    Failure Indicators: Missing outbound frame capture or missing terminal state.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-3-baseline-happy.json

  Scenario: Mixed-window contamination detection (negative)
    Tool: Bash
    Preconditions: Baseline validator available.
    Steps:
      1. Inject unrelated run_id entries into baseline manifest.
      2. Validate manifest isolation rules.
    Expected Result: Validator flags contamination and rejects manifest.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-3-baseline-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-3-baseline-happy.json`
  - [ ] `task-3-baseline-negative.txt`

  **Commit**: YES
  - Message: `chore(mock): capture pre-fix setting baseline`
  - Files: `.sisyphus/evidence/mock-setting-parity/`
  - Pre-commit: `python3 -m json.tool`

- [ ] 4. Build frame-level parity diff utility

  **What to do**:
  - Implement comparator producing semantic+byte-level diff between golden and mock runs.
  - Normalize allowed-drift fields (timestamps/IDs) while preserving structure/ordering checks.
  - Output machine-readable mismatch report consumed by gate evaluator.

  **Must NOT do**:
  - Do not over-normalize fields that hide real protocol mismatches.
  - Do not depend on manual inspection.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Utility scripting with bounded scope.
  - **Skills**: [`git-master`]
    - `git-master`: Reliable change management and traceable CLI checks.
  - **Skills Evaluated but Omitted**:
    - `frontend-ui-ux`: No UI work.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T2, T3, T5, T6)
  - **Blocks**: T11, T13, T16, T19
  - **Blocked By**: T1, T2, T3

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/validate_setting_contract.py` - Existing invariant validation style.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/analysis/setting-acceptance-contract.yaml` - Contract definitions for must-match fields.
  - `/tmp/setting-contract/success-window.json` - Golden input shape.

  **Acceptance Criteria**:
  - [ ] Comparator returns zero mismatches on validated golden-vs-golden self-check.
  - [ ] Comparator flags deliberate tag-order mismatch.
  - [ ] Comparator exports JSON report with mismatch severity and frame references.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Comparator self-check (happy path)
    Tool: Bash
    Preconditions: Comparator script available.
    Steps:
      1. Compare golden fixture against itself.
      2. Assert mismatch_count == 0.
      3. Assert report status == PASS.
    Expected Result: Self-comparison passes with zero mismatches.
    Failure Indicators: Any mismatch on identical input.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-4-compare-happy.json

  Scenario: Tag-order mismatch detection (negative)
    Tool: Bash
    Preconditions: Mutated fixture with reordered tags.
    Steps:
      1. Compare golden fixture to mutated fixture.
      2. Assert mismatch report includes tag_order violation.
    Expected Result: Comparator fails and pinpoints offending frame/tag path.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-4-compare-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-4-compare-happy.json`
  - [ ] `task-4-compare-negative.json`

  **Commit**: YES
  - Message: `feat(mock): add setting frame parity comparator`
  - Files: `scripts/`
  - Pre-commit: `python3 scripts/* --help`

- [ ] 5. Add protocol replay fixture bundles for acceptance and race windows

  **What to do**:
  - Build reusable fixture packs: `happy_single`, `happy_multi`, `missing_ack`, `end_collision`, `wrong_reason_ack`.
  - Add metadata tags (`expected_state`, `expected_mismatch`, `window_source`).
  - Ensure fixtures are consumable by pytest and replay utility.

  **Must NOT do**:
  - Do not embed environment-specific secrets or hostnames.
  - Do not mix synthetic and live-derived windows without labeling.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Fixture packaging and metadata alignment.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps fixture provenance and reproducibility clean.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not applicable.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T2, T3, T4, T6)
  - **Blocks**: T13, T14
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/conftest.py` - Existing fixture loading patterns.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_acceptance.py` - Positive assertions to preserve.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_negative_cases.py` - Negative-case baseline.

  **Acceptance Criteria**:
  - [ ] Fixture index file lists all scenarios and expected verdicts.
  - [ ] Pytest fixture loader can load each scenario without manual path edits.
  - [ ] Replay utility can iterate all fixtures from one root.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Fixture loader smoke (happy path)
    Tool: Bash
    Preconditions: Fixture bundles generated.
    Steps:
      1. Run pytest collect-only on protocol suite.
      2. Assert each new fixture ID appears in collected parameterized tests.
      3. Assert no fixture parsing errors.
    Expected Result: Fixture bundles are discoverable and parse cleanly.
    Failure Indicators: Collection failure or missing fixture IDs.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-5-fixtures-happy.txt

  Scenario: Missing metadata rejection (negative)
    Tool: Bash
    Preconditions: One fixture intentionally missing expected_state.
    Steps:
      1. Run fixture validation script.
      2. Assert validation fails with fixture path + missing key.
    Expected Result: Invalid fixture rejected before test execution.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-5-fixtures-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-5-fixtures-happy.txt`
  - [ ] `task-5-fixtures-negative.txt`

  **Commit**: YES
  - Message: `test(mock): add setting replay fixture bundles`
  - Files: `tests/protocol/fixtures/`
  - Pre-commit: `python3 -m pytest --collect-only`

- [ ] 6. Define explicit state-machine transition contract

  **What to do**:
  - Document allowed transitions and forbidden transitions for setting lifecycle.
  - Add explicit invariant: no END emission while state is `DELIVERED` waiting for valid ACK.
  - Add retry/backoff transition table including fail-closed terminal behavior.

  **Must NOT do**:
  - Do not leave ambiguous transition semantics.
  - Do not allow implicit side effects between burst scheduler and state machine.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Contract/spec work used by later implementation and tests.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps transition-contract edits scoped and traceable across revisions.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not browser related.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T2, T3, T4, T5)
  - **Blocks**: T8, T9, T10, T12
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:41` - Current enum states and transition map.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1200` - ACK handling and END emission path.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1971` - Timeout/retry checker semantics.

  **Acceptance Criteria**:
  - [ ] Transition contract lists all states and allowed predecessors/successors.
  - [ ] Forbidden transitions explicitly listed with rationale.
  - [ ] Contract maps each transition to expected log evidence signature.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Transition map completeness (happy path)
    Tool: Bash
    Preconditions: Contract file generated.
    Steps:
      1. Run contract validator to ensure every state has outgoing definition.
      2. Validate every transition references an observable event.
      3. Assert no undefined state labels.
    Expected Result: Contract is complete and internally consistent.
    Failure Indicators: Missing transition entries or unresolved state names.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-6-contract-happy.txt

  Scenario: Forbidden transition detection (negative)
    Tool: Bash
    Preconditions: Synthetic transition log containing DELIVERED->ENDED without ACK.
    Steps:
      1. Run validator against synthetic log.
      2. Assert violation flagged as critical.
    Expected Result: Contract enforcement catches forbidden transition.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-6-contract-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-6-contract-happy.txt`
  - [ ] `task-6-contract-negative.txt`

  **Commit**: YES
  - Message: `docs(mock): define setting transition contract`
  - Files: `docs/`, `analysis/`
  - Pre-commit: `python3 -m pytest /Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol --collect-only -q`

- [ ] 7. Implement outbound forensic logging with trace linkage

  **What to do**:
  - Add outbound frame logger capturing exact XML payload, CRC, conn_id, request_id, and transition context.
  - Ensure each outbound frame can be correlated to incoming trigger and state transition.
  - Persist forensic lines to dedicated file separate from operational logs.

  **Must NOT do**:
  - Do not redact protocol fields needed for diffing.
  - Do not block hot path with expensive synchronous writes.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Runtime instrumentation in critical protocol path.
  - **Skills**: [`git-master`]
    - `git-master`: Helps isolate instrumentation changes and verify minimal blast radius.
  - **Skills Evaluated but Omitted**:
    - `frontend-ui-ux`: Not relevant.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T8, T9, T10, T11, T12)
  - **Blocks**: T18, T19
  - **Blocked By**: T1

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1495` - Inbound `_save_frame` implementation to mirror for outbound.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:2151` - Response write boundary where outbound capture should occur.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/data/frames/` - Existing storage structure conventions.

  **Acceptance Criteria**:
  - [ ] Every outbound frame written once to forensic log with request linkage.
  - [ ] Log writes do not break normal response timing under test load.
  - [ ] Forensic parser reconstructs full sequence from logs only.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Outbound logging coverage (happy path)
    Tool: Bash
    Preconditions: Mock running with forensic logging enabled.
    Steps:
      1. Trigger one MODE delivery cycle.
      2. Parse forensic log and count outbound frames.
      3. Assert each outbound frame includes request_id, conn_id, crc, raw_xml.
    Expected Result: 100% outbound coverage with required fields.
    Failure Indicators: Missing entries or missing required fields.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-7-forensic-happy.json

  Scenario: Duplicate frame write detection (negative)
    Tool: Bash
    Preconditions: Dedup check script available.
    Steps:
      1. Run cycle and build hash per outbound frame.
      2. Assert no duplicate write for same frame hash+timestamp bucket.
    Expected Result: No duplicate forensic writes.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-7-forensic-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-7-forensic-happy.json`
  - [ ] `task-7-forensic-negative.txt`

  **Commit**: YES
  - Message: `feat(mock): add outbound forensic frame logging`
  - Files: `server.py`
  - Pre-commit: `python3 -m pytest tests/protocol -q`

- [ ] 8. Add END emission guard during active delivery wait

  **What to do**:
  - Implement hard guard so poll handlers never emit END while device state is `DELIVERED` awaiting valid ACK.
  - Route poll responses to deterministic wait behavior consistent with cloud pattern.
  - Add explicit log marker when END is suppressed by guard.

  **Must NOT do**:
  - Do not clear pending state on generic ACK.
  - Do not bypass guard for IsNewFW/IsNewWeather retries.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Concurrency/state correctness in protocol finite-state flow.
  - **Skills**: [`git-master`]
    - `git-master`: Controlled diff and traceability around risky state logic.
  - **Skills Evaluated but Omitted**:
    - `playwright`: No browser surface.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T9, T10, T11, T12)
  - **Blocks**: T13, T14, T18
  - **Blocked By**: T3, T6

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1668` - Current IsNewSet END response branch.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1680` - Current IsNewFW/IsNewWeather END response branch.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1200` - ACK-only state clear policy.

  **Acceptance Criteria**:
  - [ ] No END frames emitted for device while state is DELIVERED waiting on ACK.
  - [ ] Guard behavior visible in logs with deterministic marker.
  - [ ] Existing non-setting poll behavior remains unchanged when no pending delivery.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: END suppression during DELIVERED (happy path)
    Tool: Bash
    Preconditions: Device state in DELIVERED via queued setting.
    Steps:
      1. Send IsNewFW and IsNewWeather poll frames while waiting for ACK.
      2. Inspect outbound forensic log.
      3. Assert no END response emitted for guarded device state.
    Expected Result: END suppressed until valid ACK(Reason=Setting) arrives.
    Failure Indicators: Any END emitted before ACK for the active delivery.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-8-end-guard-happy.json

  Scenario: Guard bypass attempt (negative)
    Tool: Bash
    Preconditions: Inject generic ACK without Reason=Setting.
    Steps:
      1. Send generic ACK frame.
      2. Send IsNewSet poll.
      3. Assert state remains pending/delivered and END still guarded.
    Expected Result: Generic ACK does not unlock END emission.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-8-end-guard-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-8-end-guard-happy.json`
  - [ ] `task-8-end-guard-negative.json`

  **Commit**: YES
  - Message: `fix(mock): guard END emission during setting delivery`
  - Files: `server.py`, `tests/protocol/`
  - Pre-commit: `python3 -m pytest tests/protocol -k end_guard -q`

- [ ] 9. Enforce IsNewSet-only delivery gate for pending and retry paths

  **What to do**:
  - Ensure pending/retry Setting delivery is triggered only by `IsNewSet` events.
  - Prevent delivery on `IsNewFW`/`IsNewWeather` during active setting cycle.
  - Add explicit metrics counter for suppressed non-IsNewSet triggers.

  **Must NOT do**:
  - Do not regress idle/no-pending poll response behavior.
  - Do not silently fallback to broad poll mode.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: High-impact protocol gate behavior with state/race implications.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps logic change tightly scoped and auditable.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T8, T10, T11, T12)
  - **Blocks**: T13, T14, T18
  - **Blocked By**: T3, T6

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1558` - `is_isnewset` decision point.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1573` - Existing mode branch (`immediate`/`isnewset`/poll).
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:658` - Burst scheduler IsNewSet callback semantics.

  **Acceptance Criteria**:
  - [ ] Active delivery attempts occur only on IsNewSet trigger.
  - [ ] IsNewFW/IsNewWeather during pending/retry do not deliver setting payload.
  - [ ] Suppression events are visible in diagnostics logs/metrics.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: IsNewSet-only gate enforcement (happy path)
    Tool: Bash
    Preconditions: Setting queued and state=PENDING.
    Steps:
      1. Send IsNewFW poll and inspect outbound frame.
      2. Send IsNewWeather poll and inspect outbound frame.
      3. Send IsNewSet poll and inspect outbound frame.
    Expected Result: Setting payload emitted only on IsNewSet.
    Failure Indicators: Setting payload emitted on IsNewFW/IsNewWeather.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-9-gate-happy.json

  Scenario: Retry gate regression check (negative)
    Tool: Bash
    Preconditions: State in retry>0 after timeout.
    Steps:
      1. Trigger retry window.
      2. Send non-IsNewSet polls repeatedly.
      3. Assert no delivery until IsNewSet arrives.
    Expected Result: Retry delivery remains IsNewSet-gated.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-9-gate-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-9-gate-happy.json`
  - [ ] `task-9-gate-negative.json`

  **Commit**: YES
  - Message: `fix(mock): enforce isnewset-only setting delivery`
  - Files: `server.py`, `tests/protocol/`
  - Pre-commit: `python3 -m pytest tests/protocol -k isnewset -q`

- [ ] 10. Stabilize retry/backoff and fail-closed transition evidence

  **What to do**:
  - Confirm retry deadlines, backoff math, and timeout windows are deterministic per device.
  - Ensure terminal FAILED state is emitted once with complete failure metadata.
  - Ensure burst state and device state remain synchronized after failure.

  **Must NOT do**:
  - Do not allow infinite retry loops.
  - Do not lose original setting metadata on retry/failure transitions.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Robustness hardening in asynchronous state loop.
  - **Skills**: [`git-master`]
    - `git-master`: Trace retries and prevent accidental broad edits.
  - **Skills Evaluated but Omitted**:
    - `frontend-ui-ux`: Not relevant.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T8, T9, T11, T12)
  - **Blocks**: T14, T18
  - **Blocked By**: T6

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:295` - Retry transition logic.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1971` - Timeout checker loop.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:2038` - Failure reason emission.

  **Acceptance Criteria**:
  - [ ] Retry intervals follow documented exponential backoff.
  - [ ] FAILED state contains reason, retry count, and setting identity.
  - [ ] No active burst left orphaned after fail-closed transition.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Deterministic retry progression (happy path)
    Tool: Bash
    Preconditions: Queue setting with ACK intentionally withheld.
    Steps:
      1. Capture timestamps of each retry attempt.
      2. Compute observed intervals.
      3. Assert intervals match expected backoff policy tolerance.
    Expected Result: Retry timeline matches policy and ends at configured max.
    Failure Indicators: Non-deterministic retry count or interval drift.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-10-retry-happy.json

  Scenario: Orphaned burst detection (negative)
    Tool: Bash
    Preconditions: Force fail-closed completion.
    Steps:
      1. Let setting fail after max retries.
      2. Query /api/setting-state and /api/burst-status.
      3. Assert no inconsistent active burst with FAILED device state.
    Expected Result: Burst/device state consistency maintained.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-10-retry-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-10-retry-happy.json`
  - [ ] `task-10-retry-negative.json`

  **Commit**: YES
  - Message: `fix(mock): harden retry and fail-closed transitions`
  - Files: `server.py`, `tests/protocol/`
  - Pre-commit: `python3 -m pytest tests/protocol -k retry -q`

- [ ] 11. Normalize outbound envelope composition to golden parity rules

  **What to do**:
  - Align setting frame envelope fields (ordering/format/presence) with golden cloud captures.
  - Validate CRC generation path and line endings against observed live conventions.
  - Maintain allowed variable fields list explicitly (timestamp/ID drift only).

  **Must NOT do**:
  - Do not mutate payload semantics to force match.
  - Do not skip CRC verification when emitting parity report.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Byte-level protocol fidelity and serialization correctness.
  - **Skills**: [`git-master`]
    - `git-master`: Ensures precise diffs around serialization.
  - **Skills Evaluated but Omitted**:
    - `playwright`: No browser context.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T8, T9, T10, T12)
  - **Blocks**: T13, T15, T19
  - **Blocked By**: T2, T4

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/local_oig_crc.py` - Frame/CRC builder used by mock.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/generate_setting_burst.py` - Burst frame synthesis source.
  - `/tmp/setting-contract/success-window.json` - Golden framing examples.

  **Acceptance Criteria**:
  - [ ] Parity comparator reports no critical envelope mismatches for happy windows.
  - [ ] CRC validation passes for emitted setting frames.
  - [ ] Allowed-drift fields are explicitly documented and enforced.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Envelope parity pass (happy path)
    Tool: Bash
    Preconditions: Updated frame builder and comparator available.
    Steps:
      1. Generate mock setting run output.
      2. Compare against golden fixture with allowed drift list.
      3. Assert critical_mismatch_count == 0.
    Expected Result: Envelope and ordering parity pass.
    Failure Indicators: Any critical field/order mismatch.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-11-envelope-happy.json

  Scenario: CRC mismatch detection (negative)
    Tool: Bash
    Preconditions: Mutated frame with invalid CRC.
    Steps:
      1. Run CRC validator.
      2. Assert mismatch is detected and frame id reported.
    Expected Result: CRC failure flagged deterministically.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-11-envelope-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-11-envelope-happy.json`
  - [ ] `task-11-envelope-negative.txt`

  **Commit**: YES
  - Message: `fix(mock): align setting envelope with live parity`
  - Files: `server.py`, `scripts/generate_setting_burst.py`, `local_oig_crc.py`
  - Pre-commit: `python3 -m pytest tests/protocol -k envelope -q`

- [ ] 12. Harden ACK matcher and delivery linkage assertions

  **What to do**:
  - Ensure only `Result=ACK` + `Reason=Setting` clears delivery state.
  - Verify ACK is linked to currently delivered setting identity/context.
  - Emit explicit evidence record for accepted/rejected ACK frames.

  **Must NOT do**:
  - Do not accept generic ACK or wrong-reason ACK.
  - Do not clear pending on malformed ACK parse.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Focused matcher and assertions update.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps exact matcher changes reviewable.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not applicable.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T8, T9, T10, T11)
  - **Blocks**: T13, T14
  - **Blocked By**: T6

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1382` - Existing ACK matcher.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1408` - ACK evidence writer.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_negative_cases.py` - Wrong ACK negatives.

  **Acceptance Criteria**:
  - [ ] Generic ACK without `Reason=Setting` never transitions DELIVERED->ACKED.
  - [ ] Rejected ACK evidence includes rejection reason and frame snapshot.
  - [ ] Accepted ACK links to active setting id and device id.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Valid ACK acceptance (happy path)
    Tool: Bash
    Preconditions: Device in DELIVERED state.
    Steps:
      1. Send ACK frame with Reason=Setting.
      2. Assert state transitions to ACKED/ENDED/IDLE sequence.
      3. Assert acceptance evidence includes delivered_setting_id.
    Expected Result: Valid ACK clears pending through expected transitions.
    Failure Indicators: State remains DELIVERED or missing linkage evidence.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-12-ack-happy.json

  Scenario: Wrong-reason ACK rejection (negative)
    Tool: Bash
    Preconditions: Device in DELIVERED state.
    Steps:
      1. Send ACK with Reason=Data.
      2. Assert state does not clear and retry path continues.
      3. Assert rejection evidence recorded.
    Expected Result: Wrong-reason ACK rejected.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-12-ack-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-12-ack-happy.json`
  - [ ] `task-12-ack-negative.json`

  **Commit**: YES
  - Message: `fix(mock): harden ack matcher and linkage checks`
  - Files: `server.py`, `tests/protocol/`
  - Pre-commit: `python3 -m pytest tests/protocol -k ack -q`

- [ ] 13. Add positive replay acceptance tests for MODE -> ACK -> END

  **What to do**:
  - Add protocol tests that replay golden setting windows against updated mock logic.
  - Assert exact transition ordering and final state conditions.
  - Include latency threshold assertions derived from live window baseline.

  **Must NOT do**:
  - Do not assert only on frame count.
  - Do not hardcode environment-specific IDs unless fixture-scoped.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Multi-signal acceptance assertions with replay semantics.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps test updates aligned with implementation commits.
  - **Skills Evaluated but Omitted**:
    - `playwright`: No UI surface.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T14, T15, T16, T17)
  - **Blocks**: T18, T19
  - **Blocked By**: T4, T5, T8, T9, T11, T12

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_acceptance.py` - Current acceptance coverage baseline.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/conftest.py` - Fixture injection points.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/analysis/setting-acceptance-contract.yaml` - Contract invariants.

  **Acceptance Criteria**:
  - [ ] Replay acceptance tests pass for all golden windows.
  - [ ] Tests assert ordering: delivered setting before ACK, ACK before END.
  - [ ] Tests fail deterministically on swapped or missing events.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Golden replay acceptance (happy path)
    Tool: Bash
    Preconditions: Updated mock logic and fixtures available.
    Steps:
      1. Run pytest -k replay_acceptance.
      2. Assert all golden windows pass.
      3. Verify test output includes ordered event assertions.
    Expected Result: MODE->ACK->END acceptance passes across fixtures.
    Failure Indicators: Any ordering/assertion failure.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-13-replay-happy.txt

  Scenario: Missing END failure (negative)
    Tool: Bash
    Preconditions: Fixture mutated to remove END event.
    Steps:
      1. Run replay acceptance tests.
      2. Assert failure references missing END event.
    Expected Result: Test suite rejects incomplete handshake.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-13-replay-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-13-replay-happy.txt`
  - [ ] `task-13-replay-negative.txt`

  **Commit**: YES
  - Message: `test(mock): add positive replay acceptance suite`
  - Files: `tests/protocol/`
  - Pre-commit: `python3 -m pytest tests/protocol -k replay_acceptance -q`

- [ ] 14. Add negative and race-condition protocol tests

  **What to do**:
  - Add negative tests for END collision, generic ACK, wrong-reason ACK, malformed ACK, retry race.
  - Add concurrent poll tests while state is DELIVERED.
  - Assert no forbidden transitions and no premature END.

  **Must NOT do**:
  - Do not rely on sleeps alone; use deterministic state checks.
  - Do not ignore flaky race tests; stabilize with deterministic fixtures.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Race-condition and negative behavior verification.
  - **Skills**: [`git-master`]
    - `git-master`: Helps maintain stable test boundaries and isolate failures.
  - **Skills Evaluated but Omitted**:
    - `playwright`: No browser requirement.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T13, T15, T16, T17)
  - **Blocks**: T18
  - **Blocked By**: T5, T8, T9, T10, T12

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_negative_cases.py` - Existing negatives.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1668` - END emission branch to protect.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:1971` - Retry race conditions.

  **Acceptance Criteria**:
  - [ ] All new negative tests fail pre-fix and pass post-fix.
  - [ ] Race tests prove no END/Setting collision during active delivery.
  - [ ] Wrong ACK classes remain rejected under load.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Race-condition resilience (happy path)
    Tool: Bash
    Preconditions: Mock running with deterministic test hooks.
    Steps:
      1. Queue setting and force DELIVERED state.
      2. Send IsNewFW/IsNewWeather/IsNewSet in rapid sequence.
      3. Assert no forbidden transitions and no premature END.
    Expected Result: State machine remains valid and handshake completes or retries deterministically.
    Failure Indicators: Forbidden transition or END before valid ACK.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-14-race-happy.json

  Scenario: Malformed ACK rejection (negative)
    Tool: Bash
    Preconditions: DELIVERED state active.
    Steps:
      1. Inject malformed ACK XML.
      2. Assert parser rejects and state remains active.
      3. Confirm rejection evidence entry exists.
    Expected Result: Malformed ACK does not clear delivery.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-14-race-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-14-race-happy.json`
  - [ ] `task-14-race-negative.json`

  **Commit**: YES
  - Message: `test(mock): add setting race and negative coverage`
  - Files: `tests/protocol/`
  - Pre-commit: `python3 -m pytest tests/protocol -k "negative or race" -q`

- [ ] 15. Add controlled integration runner for one-shot MODE change test

  **What to do**:
  - Extend runner to execute one deterministic MODE change cycle with run_id.
  - Auto-collect state snapshots and forensic logs into run bundle.
  - Return structured pass/fail result with failure classification.

  **Must NOT do**:
  - Do not run against cloud endpoints.
  - Do not leave queued pending settings after test completion.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Script integration and orchestration.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps script changes atomic and verifiable.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T13, T14, T16, T17)
  - **Blocks**: T18
  - **Blocked By**: T11

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/run_setting_poc.sh` - Existing PoC flow.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/export_setting_evidence.py` - Evidence export structure.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/evaluate_setting_gate.py` - Gate result contract.

  **Acceptance Criteria**:
  - [ ] Runner executes reset -> queue -> monitor -> export sequence in one command.
  - [ ] Runner exits non-zero on handshake failure classes.
  - [ ] Runner always writes evidence bundle even on failure.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: One-shot MODE integration run (happy path)
    Tool: Bash
    Preconditions: Mock running and reachable API.
    Steps:
      1. Execute runner for MODE=2.
      2. Assert exit code 0.
      3. Assert evidence bundle contains MODE, ACK(Setting), END linkage.
    Expected Result: Controlled run passes and produces complete bundle.
    Failure Indicators: Missing linkage or non-zero exit.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-15-runner-happy.json

  Scenario: Forced timeout classification (negative)
    Tool: Bash
    Preconditions: ACK suppressed test mode.
    Steps:
      1. Execute runner with ACK suppression.
      2. Assert non-zero exit and failure_class=ack_timeout.
      3. Assert evidence bundle still written.
    Expected Result: Failure classified correctly with artifacts preserved.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-15-runner-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-15-runner-happy.json`
  - [ ] `task-15-runner-negative.json`

  **Commit**: YES
  - Message: `feat(mock): add controlled setting integration runner`
  - Files: `scripts/`
  - Pre-commit: `bash scripts/run_setting_poc.sh --help`

- [ ] 16. Build parity gate evaluator with actionable mismatch report

  **What to do**:
  - Extend gate evaluator to consume parity diff report + state evidence.
  - Produce severity-based verdict (PASS/FAIL) with exact mismatch locations.
  - Emit machine-readable summary for CI/ops consumption.

  **Must NOT do**:
  - Do not silently downgrade critical mismatches.
  - Do not emit pass on partial evidence.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Deterministic reporting tool extension.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps gate semantics versioned and auditable.
  - **Skills Evaluated but Omitted**:
    - `frontend-ui-ux`: Not relevant.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T13, T14, T15, T17)
  - **Blocks**: T19, T20
  - **Blocked By**: T4, T13, T14

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/evaluate_setting_gate.py` - Existing gate baseline.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/validate_setting_contract.py` - Invariant-check style.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/analysis/setting-acceptance-contract.yaml` - Contract source.

  **Acceptance Criteria**:
  - [ ] Gate fails if critical parity mismatch exists.
  - [ ] Gate output includes mismatch path, expected, actual, severity.
  - [ ] Gate can be run non-interactively with stable exit codes.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Gate pass report (happy path)
    Tool: Bash
    Preconditions: Passing replay evidence set.
    Steps:
      1. Run gate evaluator on passing artifacts.
      2. Assert decision == PASS and exit code 0.
      3. Assert summary totals match parsed mismatch counts.
    Expected Result: Deterministic PASS report.
    Failure Indicators: Non-zero exit or inconsistent summary counts.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-16-gate-happy.json

  Scenario: Critical mismatch fail (negative)
    Tool: Bash
    Preconditions: Inject critical order mismatch in report input.
    Steps:
      1. Run gate evaluator.
      2. Assert decision == FAIL and exit code 1.
      3. Assert report pinpoints offending frame path.
    Expected Result: Critical mismatch blocks pass.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-16-gate-negative.json
  ```

  **Evidence to Capture**:
  - [ ] `task-16-gate-happy.json`
  - [ ] `task-16-gate-negative.json`

  **Commit**: YES
  - Message: `feat(mock): add parity gate evaluator`
  - Files: `scripts/evaluate_setting_gate.py`, `analysis/`
  - Pre-commit: `python3 scripts/evaluate_setting_gate.py --help`

- [ ] 17. Add rollback and cleanup automation for failed setting runs

  **What to do**:
  - Add script path for cancel/reset/archive on failed test cycles.
  - Ensure cleanup returns device state to IDLE and clears active burst.
  - Archive failed run evidence with reason classification.

  **Must NOT do**:
  - Do not drop failure artifacts.
  - Do not reset unrelated devices.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Operational scripting around existing endpoints.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps rollback procedure safe and explicit.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T13, T14, T15, T16)
  - **Blocks**: T18
  - **Blocked By**: T10, T14

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/docs/SETTING_POC_ROLLBACK.md` - Existing rollback intent.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:2139` - `/api/setting-reset` endpoint.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:2145` - `/api/burst-cancel` endpoint.

  **Acceptance Criteria**:
  - [ ] One command performs cancel/reset/archive flow.
  - [ ] Post-cleanup verification confirms IDLE + no active burst.
  - [ ] Cleanup is idempotent (safe to run twice).

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Failed-run cleanup (happy path)
    Tool: Bash
    Preconditions: Device in FAILED or DELIVERED timeout state.
    Steps:
      1. Run cleanup script for target device.
      2. Query /api/setting-state and /api/burst-status.
      3. Assert device IDLE and no active burst remains.
    Expected Result: Cleanup returns deterministic clean state.
    Failure Indicators: Residual pending/active burst after cleanup.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-17-cleanup-happy.json

  Scenario: Idempotent rerun (negative)
    Tool: Bash
    Preconditions: State already clean.
    Steps:
      1. Run cleanup script twice.
      2. Assert second run exits 0 with no destructive side effects.
    Expected Result: Repeated cleanup is safe.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-17-cleanup-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-17-cleanup-happy.json`
  - [ ] `task-17-cleanup-negative.txt`

  **Commit**: YES
  - Message: `chore(mock): automate failed-run rollback cleanup`
  - Files: `scripts/`, `docs/SETTING_POC_ROLLBACK.md`
  - Pre-commit: `bash scripts/*cleanup* --help`

- [ ] 18. Execute staged mock run and collect full evidence pack

  **What to do**:
  - Run controlled MODE change on mock under updated logic.
  - Capture full evidence pack (inbound/outbound/state transitions/gate report).
  - Verify handshake completion and absence of forbidden transitions.

  **Must NOT do**:
  - Do not run overlapping test runs.
  - Do not accept run without complete evidence set.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: End-to-end staged validation with strict evidence requirements.
  - **Skills**: [`git-master`]
    - `git-master`: Maintains disciplined execution log and reproducibility.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not browser-based.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: F1, F3, F4
  - **Blocked By**: T1, T7, T8, T9, T10, T13, T14, T15, T17

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/run_setting_poc.sh` - Primary staged runner.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/export_setting_evidence.py` - Evidence exporter.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - Updated protocol flow under test.

  **Acceptance Criteria**:
  - [ ] Staged run reaches MODE -> ACK(Reason=Setting) -> END.
  - [ ] No DELIVERED->FAILED transition in happy run.
  - [ ] Evidence pack complete and linked by run_id.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Staged happy run completion
    Tool: Bash
    Preconditions: Updated mock deployed and clean state.
    Steps:
      1. Execute runner with MODE=2.
      2. Monitor /api/setting-state to terminal completion.
      3. Assert evidence pack contains handshake linkage.
    Expected Result: Run completes with handshake success and full evidence.
    Failure Indicators: Missing ACK linkage or fail-closed state.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-18-stage-happy.json

  Scenario: Incomplete evidence rejection
    Tool: Bash
    Preconditions: Remove one expected artifact from pack.
    Steps:
      1. Run pack validator.
      2. Assert validator fails and lists missing artifact.
    Expected Result: Incomplete pack cannot pass staging gate.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-18-stage-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-18-stage-happy.json`
  - [ ] `task-18-stage-negative.txt`

  **Commit**: NO

- [ ] 19. Execute golden replay parity validation on updated mock

  **What to do**:
  - Run replay validator against selected golden windows and staged outputs.
  - Produce consolidated parity mismatch report with severity counts.
  - Confirm all critical mismatches resolved.

  **Must NOT do**:
  - Do not ignore unresolved critical mismatches.
  - Do not mark pass with partial fixture coverage.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Validation-intensive, evidence-heavy quality gate.
  - **Skills**: [`git-master`]
    - `git-master`: Reproducible execution and report integrity.
  - **Skills Evaluated but Omitted**:
    - `playwright`: No UI validation.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: F1, F3
  - **Blocked By**: T4, T7, T11, T13, T16

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/replay_validation.py` - Replay engine target.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/evaluate_setting_gate.py` - Gate verdict logic.
  - `/tmp/setting-contract/success-window.json` - Golden baseline input.

  **Acceptance Criteria**:
  - [ ] Replay runs all selected fixtures.
  - [ ] Critical mismatch count is zero.
  - [ ] Final parity report written and timestamped.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Full replay parity pass
    Tool: Bash
    Preconditions: Updated mock outputs and golden fixtures available.
    Steps:
      1. Run replay validator for all fixture bundles.
      2. Assert critical_mismatch_count == 0.
      3. Persist consolidated report.
    Expected Result: Parity validation passes across fixture set.
    Failure Indicators: Any unresolved critical mismatch.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-19-replay-happy.json

  Scenario: Fixture coverage gap detection
    Tool: Bash
    Preconditions: Deliberately omit one fixture from run list.
    Steps:
      1. Execute replay with incomplete fixture list.
      2. Assert validator fails on coverage requirement.
    Expected Result: Coverage gap blocks parity pass.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-19-replay-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-19-replay-happy.json`
  - [ ] `task-19-replay-negative.txt`

  **Commit**: NO

- [ ] 20. Publish remediation report with residual risk register

  **What to do**:
  - Summarize root cause, implemented controls, proof artifacts, and remaining risks.
  - Include operator runbook notes for future regression triage.
  - Attach final pass/fail matrix for all tasks and gates.

  **Must NOT do**:
  - Do not claim closure for unresolved critical mismatches.
  - Do not omit rollback guidance.

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Final technical communication and operational handoff.
  - **Skills**: [`git-master`]
    - `git-master`: Keeps final report references aligned with actual committed evidence paths.
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not relevant.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (parallel with T18/T19 report drafting only)
  - **Blocks**: F1, F4
  - **Blocked By**: T16, T18, T19

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/docs/SETTING_POC_ROLLBACK.md` - Operational rollback section.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/.sisyphus/evidence/mock-setting-parity/` - Evidence source of truth.
  - `/Users/martinhorak/Projects/oig-proxy/.sisyphus/plans/mock-setting-byte-parity-remediation.md` - Plan compliance cross-check.

  **Acceptance Criteria**:
  - [ ] Report includes root cause, fixes, verification results, and residual risks.
  - [ ] Report references exact evidence file paths.
  - [ ] Report includes explicit “safe to switch back” or “hold” decision criteria.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Report completeness review
    Tool: Bash
    Preconditions: Evidence and gate outputs available.
    Steps:
      1. Run report completeness linter/checklist.
      2. Assert all mandatory sections present.
      3. Validate all referenced evidence paths exist.
    Expected Result: Report passes completeness and path checks.
    Failure Indicators: Missing sections or broken evidence links.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-20-report-happy.txt

  Scenario: Broken reference detection
    Tool: Bash
    Preconditions: Intentionally remove one referenced evidence file.
    Steps:
      1. Re-run report checker.
      2. Assert failure with missing path listed.
    Expected Result: Broken references are blocked.
    Evidence: .sisyphus/evidence/mock-setting-parity/task-20-report-negative.txt
  ```

  **Evidence to Capture**:
  - [ ] `task-20-report-happy.txt`
  - [ ] `task-20-report-negative.txt`

  **Commit**: YES
  - Message: `docs(mock): publish setting parity remediation report`
  - Files: `docs/`, `.sisyphus/evidence/mock-setting-parity/`
  - Pre-commit: `python3 -m pytest /Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol --collect-only -q`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Validate every must-have/must-not-have against files, logs, API responses, and evidence artifacts.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run lint/type/test checks relevant to changed scope and scan for high-risk anti-patterns.

- [ ] F3. **Real QA Replay** — `unspecified-high`
  Execute all QA scenarios from all tasks and store evidence in `.sisyphus/evidence/mock-setting-parity/final-qa/`.

- [ ] F4. **Scope Fidelity Check** — `deep`
  Ensure no out-of-scope files/behaviors were introduced and protocol changes stay mock-only.

---

## Commit Strategy

| After Task Group | Message | Files | Verification |
|------------------|---------|-------|--------------|
| Wave 1 | `chore(mock): add setting parity evidence scaffolding` | scripts/tests/docs | pytest + script smoke |
| Wave 2 | `fix(mock): harden setting handshake state machine` | server.py + tests | protocol tests |
| Wave 3 | `test(mock): add replay and race-condition coverage` | tests/scripts | pytest replay suite |
| Wave 4 | `docs(mock): publish parity remediation report` | evidence/docs | gate evaluator |

---

## Success Criteria

### Verification Commands
```bash
python3 -m pytest /Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol -q
python3 /Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/replay_validation.py --golden /tmp/setting-contract/success-window.json --target mock
python3 /Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/evaluate_setting_gate.py --evidence /Users/martinhorak/Projects/oig-diagnostic-cloud/.sisyphus/evidence/mock-setting-parity/latest.json
```

### Final Checklist
- [ ] Live-aligned handshake observed on mock: `MODE -> ACK(Reason=Setting) -> END`
- [ ] No END/Setting collision while awaiting valid ACK
- [ ] Retry path behaves deterministically and fail-closed with evidence
- [ ] Replay parity gate passes for all selected golden sequences
- [ ] No proxy online routing or cloud-side logic changed
