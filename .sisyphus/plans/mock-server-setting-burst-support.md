# Mock Server Setting Burst Support

## TL;DR

> **Quick Summary**: Rework the mock cloud protocol path so it reproduces the real successful Setting acceptance sequence (IsNewSet -> Setting burst -> ACK Reason=Setting -> END -> tbl_events MODE transition), then validate with executable PoC evidence.
>
> **Deliverables**:
> - Stateful setting-delivery engine in `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py`
> - Protocol validation tests and PoC runner
> - Evidence pack proving MODE=2 acceptance in safe mock zone
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: T1 -> T2 -> T6 -> T8 -> T11 -> T12 -> T13

---

## Context

### Original Request
Prepare a plan to change the mock server so BOX accepts Setting commands exactly like in proven real traffic, with primary PoC target MODE=2 (Home 2).

### Interview Summary
**Key Discussions**:
- User requested analysis-first approach based on real production captures.
- User rejected trial-and-error and asked to follow the verified communication pattern before Setting acceptance.
- User wants safe-zone operation (no production cloud impact) and evidence-backed PoC.

**Research Findings**:
- Successful acceptance window exists around 2026-02-17 13:40 UTC (cloud Setting frames + BOX ACK Reason=Setting + MODE events).
- Failed windows show repeated Setting delivery without ACK Reason=Setting and without MODE transition events.
- Static or repeated envelope profiles correlate with failure; successful windows vary frame envelope fields.

### Metis Review
**Identified Gaps (addressed)**:
- Gap: Potential confusion about pattern source and target behavior.
  - Resolution: Plan starts with canonical extraction and contract artifact generation from proven successful/failed windows.
- Gap: Risk of overfitting single frame replay instead of sequence behavior.
  - Resolution: Plan requires session-aware burst state machine and ACK-gated transitions.
- Gap: Risk of scope creep into unrelated systems.
  - Resolution: Guardrails limit changes strictly to mock setting protocol path and PoC validation.

---

## Work Objectives

### Core Objective
Implement protocol-faithful setting delivery in mock cloud so BOX reliably accepts MODE changes using the same sequence characteristics as real successful traffic.

### Concrete Deliverables
- Updated mock server setting state machine and API controls:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py`
- Protocol utilities:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/extract_setting_windows.py`
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/run_setting_poc.sh`
- Protocol tests:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_acceptance.py`
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_negative_cases.py`
- Evidence outputs:
  - `.sisyphus/evidence/mock-setting-poc/*.json`
  - `.sisyphus/evidence/mock-setting-poc/*.txt`

### Definition of Done
- [ ] `python3 /Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/extract_setting_windows.py --db /tmp/payloads_live_local.db --out /tmp/setting-contract` exits 0 and writes success+failure artifacts.
- [ ] `python3 -m pytest /Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_acceptance.py -q` exits 0.
- [ ] `python3 -m pytest /Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_negative_cases.py -q` exits 0.
- [ ] `bash /Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/run_setting_poc.sh --mode 2` produces evidence with ACK Reason=Setting and MODE transition event.

### Must Have
- Mock delivery follows sequence behavior, not static frame replay.
- Pending setting clears only after BOX ACK Reason=Setting.
- PoC evidence includes both protocol ACK and business-level MODE event transition.

### Must NOT Have (Guardrails)
- No production cloud endpoint changes.
- No edits to unrelated telemetry/SSO components.
- No "pass" decision based only on manual eyeballing.
- No static repeated envelope profile for setting bursts.
- No file modifications outside `/Users/martinhorak/Projects/oig-diagnostic-cloud/` for implementation tasks.
- No task execution from `oig-proxy` workdir when implementing mock server changes.
- No cross-repo drift: `oig-proxy` paths are read-only pattern references only.

### Timeout RCA (from previous run)
- Root cause 1: subagents executed in wrong repository context (`oig-proxy`) causing broad irrelevant churn and timeouts.
- Root cause 2: ambiguous references across two repositories without explicit write-boundary guardrails.
- Root cause 3: missing preflight gate to fail fast when workdir/path target is incorrect.
- Mitigation: Wave 0 preflight + mandatory repository fidelity checks before each implementation wave.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - all validation must be executable by agent commands and evidence files.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: YES (Tests-after)
- **Framework**: Python `pytest` for protocol tests, existing CLI scripts for replay/evidence
- **Agent-Executed QA**: Mandatory for every task

### QA Policy

| Deliverable Type | Verification Tool | Method |
|------------------|-------------------|--------|
| Mock protocol server | Bash + Python | Run queue/replay scripts, inspect sequence transitions |
| Protocol tests | Bash | Execute pytest and assert pass/fail |
| API behavior | Bash (curl) | Call endpoints, assert JSON fields and state transitions |
| Evidence artifacts | Bash + Python | Verify files, parse and assert required fields |

Evidence saved to `.sisyphus/evidence/mock-setting-poc/task-{N}-{scenario}.json|txt`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Preflight - mandatory before all tasks):
`- T0: Repository context validation and drift guard setup

Wave 1 (Start immediately - analysis foundation, 5 parallel):
|- T1: Extract success/failure setting windows from live captures
|- T2: Build machine-readable protocol contract + invariants
|- T3: Create envelope profile synthesizer (ID/ID_Set/ver/time/CRC)
|- T4: Add runtime config schema for delivery/timeout/retry knobs
`- T5: Build protocol test harness scaffolding

Wave 2 (After Wave 1 - core behavior, 5 parallel):
|- T6: Implement session-aware setting state machine in server
|- T7: Implement burst scheduler triggered by IsNewSet flow
|- T8: Implement ACK Reason=Setting matcher + pending clear rules
|- T9: Implement END/termination and retry/backoff policy
`- T10: Implement API endpoints for burst queue/status/reset

Wave 3 (After Wave 2 - verification and rollout proof, 3 parallel):
|- T11: Implement protocol acceptance/negative tests
|- T12: Implement staging PoC runner + evidence exporter
`- T13: Implement gate evaluator + rollback rehearsal report

Wave FINAL (After all tasks - independent review, 4 parallel):
|- F1: Plan compliance audit (oracle)
|- F2: Code quality review (unspecified-high)
|- F3: Real QA replay of all scenarios (unspecified-high)
`- F4: Scope fidelity check (deep)

Critical Path: T0 -> T1 -> T2 -> T6 -> T8 -> T11 -> T12 -> T13 -> F1-F4
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 5
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|------------|--------|------|
| T0 | - | T1-T13 | 0 |
| T1 | - | T2, T3, T11 | 1 |
| T2 | T0, T1 | T6, T7, T8, T11 | 1 |
| T3 | T0, T1, T2 | T7, T10 | 1 |
| T4 | T0 | T6, T9, T10 | 1 |
| T5 | T0 | T11 | 1 |
| T6 | T2, T4 | T7, T8, T9 | 2 |
| T7 | T2, T3, T6 | T8, T9, T12 | 2 |
| T8 | T2, T6, T7 | T9, T11, T12 | 2 |
| T9 | T4, T7, T8 | T11, T12 | 2 |
| T10 | T3, T4, T6 | T12 | 2 |
| T11 | T1, T2, T5, T8, T9 | T12, T13 | 3 |
| T12 | T7, T8, T9, T10, T11 | T13 | 3 |
| T13 | T11, T12 | F1-F4 | 3 |

### Agent Dispatch Summary

| Wave | # Parallel | Tasks -> Agent Category |
|------|------------|-------------------------|
| 0 | **1** | T0 -> `quick` |
| 1 | **5** | T1 -> `deep`, T2 -> `deep`, T3 -> `unspecified-high`, T4 -> `quick`, T5 -> `quick` |
| 2 | **5** | T6 -> `deep`, T7 -> `unspecified-high`, T8 -> `deep`, T9 -> `unspecified-high`, T10 -> `quick` |
| 3 | **3** | T11 -> `deep`, T12 -> `unspecified-high`, T13 -> `quick` |
| FINAL | **4** | F1 -> `oracle`, F2 -> `unspecified-high`, F3 -> `unspecified-high`, F4 -> `deep` |

---

## TODOs

- [x] 0. Preflight repository fidelity and timeout guard

  **What to do**:
  - Verify execution repository is `/Users/martinhorak/Projects/oig-diagnostic-cloud/`.
  - Verify target file exists: `server.py`.
  - Create evidence baseline for preflight checks.
  - Abort immediately if workdir is wrong.

  **Must NOT do**:
  - Do not proceed to implementation if `pwd` is not `oig-diagnostic-cloud`.
  - Do not modify any file in `/Users/martinhorak/Projects/oig-proxy/`.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: fast deterministic validation and setup
  - **Skills**: [`git-master`]
    - `git-master`: safe workspace checks before coding

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential gate before Wave 1
  - **Blocks**: T1-T13
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - target implementation file must be present.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud` - required execution root.

  **Acceptance Criteria**:
  - [ ] Evidence file exists: `.sisyphus/evidence/mock-setting-poc/task-0-preflight.json`.
  - [ ] Evidence contains `workdir=/Users/martinhorak/Projects/oig-diagnostic-cloud`.
  - [ ] Evidence confirms target file existence and write scope lock.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: preflight pass in correct repo
    Tool: Bash
    Preconditions: target repository available
    Steps:
      1. Run preflight script/commands from oig-diagnostic-cloud root
      2. Assert output includes exact path and server.py presence
      3. Write structured evidence JSON
    Expected Result: pass and unblock Wave 1
    Evidence: .sisyphus/evidence/mock-setting-poc/task-0-preflight.json

  Scenario: negative wrong-repo execution
    Tool: Bash
    Preconditions: run preflight from oig-proxy path
    Steps:
      1. Execute preflight intentionally from wrong root
      2. Assert non-zero exit with explicit wrong-repo message
    Expected Result: blocked before any file modifications
    Evidence: .sisyphus/evidence/mock-setting-poc/task-0-preflight-error.txt
  ```

  **Commit**: NO
  - Message: `chore(mock-protocol): add preflight repo fidelity gate`
  - Files: evidence only for gate check

- [x] 1. Extract canonical success/failure windows

  **What to do**:
  - Create extraction script to export successful and failed setting windows from payload DB.
  - Emit normalized JSON artifacts with ordered frames and parsed tags.

  **Must NOT do**:
  - Do not mutate source DB.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: protocol-forensic extraction and data normalization
  - **Skills**: [`git-master`]
    - `git-master`: clean scripted changes and disciplined commit boundaries
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser interaction required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T2, T3, T11
  - **Blocked By**: None

  **References**:
  - `/tmp/payloads_live_local.db` - canonical live capture source used for acceptance/failure diff.
  - `/Users/martinhorak/Projects/oig-proxy/testing/export_ha_session.py` - existing extraction pattern from HA DB.
  - `/Users/martinhorak/Projects/oig-proxy/.sisyphus/evidence/task-6-comparison-report.json` - prior evidence structure to align artifact formatting.

  **Acceptance Criteria**:
  - [ ] `/tmp/setting-contract/success-window.json` generated.
  - [ ] `/tmp/setting-contract/failed-window.json` generated.
  - [ ] Both files include parsed `Result`, `Reason`, `TblName`, `TblItem`, `NewValue`, `ID_Set`, `ver` per frame.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: extraction happy path
    Tool: Bash
    Preconditions: /tmp/payloads_live_local.db exists
    Steps:
      1. Run python3 scripts/extract_setting_windows.py --db /tmp/payloads_live_local.db --out /tmp/setting-contract
      2. Assert files /tmp/setting-contract/success-window.json and failed-window.json exist
      3. Assert each file has at least 1 frame with "Reason":"Setting"
    Expected Result: two normalized artifacts with non-zero frame count
    Evidence: .sisyphus/evidence/mock-setting-poc/task-1-window-extract.json

  Scenario: negative missing DB
    Tool: Bash
    Preconditions: invalid DB path
    Steps:
      1. Run script with --db /tmp/does-not-exist.db
      2. Assert non-zero exit and explicit "db not found" error
    Expected Result: deterministic failure, no partial output files
    Evidence: .sisyphus/evidence/mock-setting-poc/task-1-window-extract-error.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): extract canonical setting acceptance windows`
  - Files: `scripts/extract_setting_windows.py`
  - Pre-commit: `python3 scripts/extract_setting_windows.py --help`

- [x] 2. Build protocol contract and invariants

  **What to do**:
  - Create machine-readable contract defining successful sequence constraints.
  - Add validator script checking sequence ordering and required events.

  **Must NOT do**:
  - Do not encode assumptions unsupported by extracted windows.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser work

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T6, T7, T8, T11
  - **Blocked By**: T1

  **References**:
  - `/tmp/setting-contract/success-window.json` - source truth for invariant derivation.
  - `/tmp/setting-contract/failed-window.json` - negative pattern set.
  - `/Users/martinhorak/Projects/oig-proxy/addon/oig-proxy/control_settings.py` - ACK(Reason=Setting) detection semantics to mirror.

  **Acceptance Criteria**:
  - [ ] Contract file created: `/tmp/setting-contract/setting-acceptance-contract.yaml`.
  - [ ] Validator exits 0 on success-window and exits non-zero on failed-window.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: contract validates success window
    Tool: Bash
    Preconditions: task 1 artifacts present
    Steps:
      1. Run python3 scripts/validate_setting_contract.py --contract /tmp/setting-contract/setting-acceptance-contract.yaml --input /tmp/setting-contract/success-window.json
      2. Assert exit code 0 and output contains "ACK_REASON_SETTING_REQUIRED: PASS"
    Expected Result: success-window passes all required invariants
    Evidence: .sisyphus/evidence/mock-setting-poc/task-2-contract-pass.json

  Scenario: contract rejects failed window
    Tool: Bash
    Preconditions: failed-window artifact present
    Steps:
      1. Run validator on failed-window.json
      2. Assert non-zero exit and output contains "NO_MODE_EVENT_AFTER_SETTING"
    Expected Result: failed-window correctly rejected
    Evidence: .sisyphus/evidence/mock-setting-poc/task-2-contract-fail.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): add setting acceptance contract validator`
  - Files: `scripts/validate_setting_contract.py`, `analysis/setting-acceptance-contract.yaml`
  - Pre-commit: `python3 scripts/validate_setting_contract.py --help`

- [x] 3. Implement envelope profile synthesizer

  **What to do**:
  - Build utility that generates varying `ID`, `ID_Set`, `ver`, `DT`, `TSec`, and valid CRC.
  - Ensure output matches contract constraints and real-frame shape.

  **Must NOT do**:
  - Do not replay static envelope fields across burst frames.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: no UI context

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T7, T10
  - **Blocked By**: T1, T2

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/local_oig_crc.py` - required CRC frame builder.
  - `/tmp/setting-contract/success-window.json` - canonical successful MODE sequence source extracted in T1.
  - `/tmp/setting_accept_analysis.json` - successful vs failed envelope variability evidence.

  **Acceptance Criteria**:
  - [ ] Generator emits 10 sequential setting frames with unique `ID` and `ID_Set`.
  - [ ] Every frame has valid CRC as verified by parser/check function.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: generate variable envelope burst
    Tool: Bash
    Preconditions: profile config available
    Steps:
      1. Run python3 scripts/generate_setting_burst.py --tbl-name tbl_box_prms --tbl-item MODE --new-value 2 --count 10
      2. Parse output and assert all ID values are unique
      3. Parse output and assert all ID_Set values are unique
    Expected Result: no repeated IDs/ID_Set values
    Evidence: .sisyphus/evidence/mock-setting-poc/task-3-envelope-uniqueness.json

  Scenario: negative static profile detection
    Tool: Bash
    Preconditions: static test profile with fixed ID/ID_Set
    Steps:
      1. Run generator with --static-profile
      2. Assert validator flags STATIC_ENVELOPE_PROFILE
    Expected Result: static profile rejected
    Evidence: .sisyphus/evidence/mock-setting-poc/task-3-envelope-static-error.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): add dynamic setting envelope synthesizer`
  - Files: `scripts/generate_setting_burst.py`
  - Pre-commit: `python3 scripts/generate_setting_burst.py --self-check`

- [x] 4. Add runtime config schema for delivery controls

  **What to do**:
  - Introduce explicit runtime knobs for delivery mode, retry policy, and timeout windows.
  - Ensure defaults are safe and backward-compatible.

  **Must NOT do**:
  - Do not change defaults in a way that affects non-setting traffic.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: not relevant

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T6, T9, T10
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - current env var pattern (`SETTING_DELIVERY_MODE`).
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/docker/diagnostics/docker-compose.yml` - runtime deployment config entry point.

  **Acceptance Criteria**:
  - [ ] Config schema documented and parsed at startup.
  - [ ] Default mode remains legacy-safe when new knobs not provided.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: startup with default config
    Tool: Bash
    Preconditions: no new env vars set
    Steps:
      1. Start server
      2. Query /api/status
      3. Assert mode reports legacy-safe default
    Expected Result: server starts and reports expected default mode
    Evidence: .sisyphus/evidence/mock-setting-poc/task-4-default-config.json

  Scenario: invalid config rejected
    Tool: Bash
    Preconditions: invalid delivery mode value
    Steps:
      1. Start server with SETTING_DELIVERY_MODE=invalid-mode
      2. Assert explicit fallback warning logged
    Expected Result: deterministic fallback with warning
    Evidence: .sisyphus/evidence/mock-setting-poc/task-4-invalid-config.txt
  ```

  **Commit**: YES
  - Message: `chore(mock-protocol): add runtime delivery configuration schema`
  - Files: `server.py`, `docker/diagnostics/docker-compose.yml`
  - Pre-commit: `python3 server.py --help`

- [x] 5. Build protocol test harness scaffolding

  **What to do**:
  - Create pytest scaffolding for protocol sequence tests with reusable fixtures.
  - Add fixture loaders for success/failure windows and expected invariants.

  **Must NOT do**:
  - Do not rely on manual log inspection as test oracle.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: protocol tests are non-UI

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T11
  - **Blocked By**: None

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/package.json` - existing test runner conventions.
  - `/Users/martinhorak/Projects/oig-proxy/testing/test_mode_sequence.py` - existing sequence test semantics.

  **Acceptance Criteria**:
  - [ ] Test scaffold runs with placeholder test passing.
  - [ ] Fixtures load normalized window artifacts.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: scaffold smoke test
    Tool: Bash
    Preconditions: pytest installed in environment
    Steps:
      1. Run python3 -m pytest tests/protocol/test_scaffold_smoke.py -q
      2. Assert output "1 passed"
    Expected Result: harness is runnable
    Evidence: .sisyphus/evidence/mock-setting-poc/task-5-harness-smoke.txt

  Scenario: fixture loading failure
    Tool: Bash
    Preconditions: broken fixture path
    Steps:
      1. Run test with missing fixture file
      2. Assert explicit fixture-not-found error
    Expected Result: deterministic fail with clear message
    Evidence: .sisyphus/evidence/mock-setting-poc/task-5-fixture-error.txt
  ```

  **Commit**: YES
  - Message: `test(mock-protocol): add protocol harness scaffolding`
  - Files: `tests/protocol/conftest.py`, `tests/protocol/test_scaffold_smoke.py`
  - Pre-commit: `python3 -m pytest tests/protocol/test_scaffold_smoke.py -q`

- [x] 6. Implement session-aware setting state machine

  **What to do**:
  - Add explicit per-device state machine for setting lifecycle.
  - Track transitions: idle -> pending -> delivered -> acked -> ended.

  **Must NOT do**:
  - Do not couple state to a single connection id only.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser path

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T7, T8, T9
  - **Blocked By**: T2, T4

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - current pending-setting single-slot behavior.
  - `/tmp/setting-contract/setting-acceptance-contract.yaml` - target transitions and invariants.

  **Acceptance Criteria**:
  - [ ] State transitions logged for every setting lifecycle step.
  - [ ] Multi-connection poll cycles maintain same device-level pending state.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: state machine happy lifecycle
    Tool: Bash
    Preconditions: queued MODE=2 setting
    Steps:
      1. Trigger IsNewSet poll
      2. Verify state progresses pending -> delivered
      3. Send ACK Reason=Setting and verify delivered -> acked -> ended
    Expected Result: no invalid transition detected
    Evidence: .sisyphus/evidence/mock-setting-poc/task-6-state-lifecycle.json

  Scenario: negative wrong-state ACK
    Tool: Bash
    Preconditions: no pending setting
    Steps:
      1. Send ACK Reason=Setting frame
      2. Assert state machine rejects transition and logs warning
    Expected Result: state remains idle
    Evidence: .sisyphus/evidence/mock-setting-poc/task-6-state-invalid-ack.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): add per-device setting state machine`
  - Files: `server.py`
  - Pre-commit: `python3 -m pytest tests/protocol/test_state_machine.py -q`

- [ ] 7. Implement burst scheduler on IsNewSet flow

  **What to do**:
  - Deliver setting burst frames according to contract timing and ordering.
  - Gate burst start on poll trigger sequence and current state.

  **Must NOT do**:
  - Do not emit setting frame outside allowed trigger context.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: not needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T8, T9, T12
  - **Blocked By**: T2, T3, T6

  **References**:
  - `/tmp/setting_accept_analysis.json` - successful burst cadence and ordering.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - existing `_generate_ack` poll handling entrypoint.

  **Acceptance Criteria**:
  - [ ] Burst starts only after allowed trigger.
  - [ ] Burst emits at least the proven MODE sequence shape (1/2 or 2-only profile by config).

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: trigger-gated burst delivery
    Tool: Bash
    Preconditions: MODE=2 burst queued
    Steps:
      1. Send IsNewFW and IsNewWeather only
      2. Assert no delivery if mode configured is IsNewSet-only
      3. Send IsNewSet and assert immediate setting delivery
    Expected Result: delivery begins only on configured trigger
    Evidence: .sisyphus/evidence/mock-setting-poc/task-7-trigger-gate.json

  Scenario: negative trigger bypass attempt
    Tool: Bash
    Preconditions: pending setting exists
    Steps:
      1. Send unrelated table frame (tbl_actual)
      2. Assert no setting frame delivered
    Expected Result: bypass blocked
    Evidence: .sisyphus/evidence/mock-setting-poc/task-7-trigger-bypass-error.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): schedule setting bursts from poll triggers`
  - Files: `server.py`
  - Pre-commit: `python3 -m pytest tests/protocol/test_burst_scheduler.py -q`

- [ ] 8. Implement ACK Reason=Setting matcher and pending clear

  **What to do**:
  - Detect BOX ACK Setting frames and clear pending only on valid ack.
  - Record ack evidence with linkage to delivered setting id.

  **Must NOT do**:
  - Do not clear pending on generic ACK without `Reason=Setting`.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: no UI testing path

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T9, T11, T12
  - **Blocked By**: T2, T6, T7

  **References**:
  - `/Users/martinhorak/Projects/oig-proxy/addon/oig-proxy/control_settings.py` - established ACK detection pattern.
  - `/tmp/setting-contract/setting-acceptance-contract.yaml` - required ack semantics.

  **Acceptance Criteria**:
  - [ ] Valid ACK Setting clears pending.
  - [ ] Generic ACK does not clear pending.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: valid ACK clears pending
    Tool: Bash
    Preconditions: one pending setting in delivered state
    Steps:
      1. Send frame containing <Result>ACK</Result><Reason>Setting</Reason>
      2. Query /api/pending-detail
      3. Assert state == cleared and pending == null
    Expected Result: pending cleared only after valid ACK
    Evidence: .sisyphus/evidence/mock-setting-poc/task-8-ack-clears.json

  Scenario: negative generic ACK ignored
    Tool: Bash
    Preconditions: pending setting exists
    Steps:
      1. Send <Result>ACK</Result> without Reason=Setting
      2. Query /api/pending-detail
      3. Assert pending still present
    Expected Result: pending not cleared
    Evidence: .sisyphus/evidence/mock-setting-poc/task-8-generic-ack-ignored.txt
  ```

  **Commit**: YES
  - Message: `fix(mock-protocol): clear pending only on ACK Reason=Setting`
  - Files: `server.py`
  - Pre-commit: `python3 -m pytest tests/protocol/test_ack_matching.py -q`

- [ ] 9. Implement END/termination and retry policy

  **What to do**:
  - Emit END after successful burst completion.
  - Add bounded retry policy when ACK missing, then fail closed.

  **Must NOT do**:
  - Do not retry indefinitely.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: not applicable

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T11, T12
  - **Blocked By**: T4, T7, T8

  **References**:
  - `/tmp/setting_accept_analysis.json` - successful END closure behavior after ACK burst.
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - current END responses on IsNew* frames.

  **Acceptance Criteria**:
  - [ ] END emitted after ack-complete state.
  - [ ] Missing ACK path hits retry limit and marks delivery failed.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: END emitted after ack completion
    Tool: Bash
    Preconditions: burst delivery and valid ACK sequence
    Steps:
      1. Execute successful sequence
      2. Assert final cloud frame is <Result>END</Result>
    Expected Result: deterministic closure frame
    Evidence: .sisyphus/evidence/mock-setting-poc/task-9-end-after-ack.json

  Scenario: negative missing ACK retry path
    Tool: Bash
    Preconditions: simulate no ACK from BOX
    Steps:
      1. Deliver burst without ACK responses
      2. Assert retries stop at configured max
      3. Assert state is failed and pending cleared by policy
    Expected Result: bounded retry, no infinite loop
    Evidence: .sisyphus/evidence/mock-setting-poc/task-9-retry-timeout.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): add end-of-burst termination and retry bounds`
  - Files: `server.py`
  - Pre-commit: `python3 -m pytest tests/protocol/test_end_retry_policy.py -q`

- [ ] 10. Implement burst control API endpoints

  **What to do**:
  - Add endpoints for queuing burst profiles, inspecting state, and resetting stuck state.
  - Keep existing `/api/queue-setting` backwards compatible.

  **Must NOT do**:
  - Do not break current API consumers.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser layer

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T12
  - **Blocked By**: T3, T4, T6

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - existing `/api/queue-setting` and `/api/pending` handlers.
  - `/Users/martinhorak/Projects/oig-proxy/testing/mock_cloud_server.py` - API shape conventions for test clients.

  **Acceptance Criteria**:
  - [ ] New endpoints respond with valid JSON and deterministic status fields.
  - [ ] Legacy endpoint behavior unchanged for simple mode.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: queue burst via API
    Tool: Bash (curl)
    Preconditions: server running
    Steps:
      1. POST /api/queue-setting-burst with tbl_name=tbl_box_prms,tbl_item=MODE,new_value=2,profile=success-burst
      2. GET /api/pending-detail
      3. Assert profile, state, and pending IDs are present
    Expected Result: burst queued and visible in state API
    Evidence: .sisyphus/evidence/mock-setting-poc/task-10-api-burst-queue.json

  Scenario: negative invalid profile
    Tool: Bash (curl)
    Preconditions: server running
    Steps:
      1. POST /api/queue-setting-burst with profile=unknown
      2. Assert HTTP error payload includes INVALID_PROFILE
    Expected Result: invalid profile rejected
    Evidence: .sisyphus/evidence/mock-setting-poc/task-10-api-invalid-profile.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): add burst queue and state control APIs`
  - Files: `server.py`
  - Pre-commit: `python3 -m pytest tests/protocol/test_api_burst_controls.py -q`

- [ ] 11. Implement protocol acceptance and negative tests

  **What to do**:
  - Implement tests that assert successful sequence and mode transition evidence.
  - Implement negative tests for static envelope and missing trigger/ACK.

  **Must NOT do**:
  - Do not assert only on raw frame count; assert semantic sequence conditions.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: protocol tests are headless

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: T12, T13
  - **Blocked By**: T1, T2, T5, T8, T9

  **References**:
  - `/tmp/setting-contract/setting-acceptance-contract.yaml` - authoritative invariants.
  - `/tmp/setting_accept_analysis.json` - observed sequence signatures.
  - `/Users/martinhorak/Projects/oig-proxy/testing/test_mode_sequence.py` - sequence validation style.

  **Acceptance Criteria**:
  - [ ] Success test proves ACK Reason=Setting + MODE transition event.
  - [ ] Negative tests fail for static envelope and missing IsNewSet trigger.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: success path test suite
    Tool: Bash
    Preconditions: tasks 6-10 complete
    Steps:
      1. Run python3 -m pytest tests/protocol/test_setting_acceptance.py -q
      2. Assert output contains "passed" and zero failures
    Expected Result: success-path protocol acceptance validated
    Evidence: .sisyphus/evidence/mock-setting-poc/task-11-tests-success.txt

  Scenario: negative suite catches regressions
    Tool: Bash
    Preconditions: negative fixtures available
    Steps:
      1. Run python3 -m pytest tests/protocol/test_setting_negative_cases.py -q
      2. Assert expected fail-cases are caught and reported as pass (negative assertions)
    Expected Result: invalid behaviors are explicitly rejected
    Evidence: .sisyphus/evidence/mock-setting-poc/task-11-tests-negative.txt
  ```

  **Commit**: YES
  - Message: `test(mock-protocol): add acceptance and negative protocol suites`
  - Files: `tests/protocol/test_setting_acceptance.py`, `tests/protocol/test_setting_negative_cases.py`
  - Pre-commit: `python3 -m pytest tests/protocol -q`

- [ ] 12. Implement staging PoC runner and evidence exporter

  **What to do**:
  - Build one-command runner for safe-zone NAS mock PoC.
  - Capture structured evidence: queued setting, delivered frames, ACK events, tbl_events mode transition.

  **Must NOT do**:
  - Do not target production cloud endpoints.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: API/log verification only

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: T13
  - **Blocked By**: T7, T8, T9, T10, T11

  **References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/deploy.sh` - NAS deployment entrypoint.
  - `/Users/martinhorak/Projects/oig-proxy/deploy_to_ha.sh` - HA-side deploy pattern and rollback context.
  - `/Users/martinhorak/Projects/oig-proxy/testing/replay_session_file.py` - controlled replay utility.

  **Acceptance Criteria**:
  - [ ] Runner executes without manual edits.
  - [ ] Evidence bundle contains ACK and MODE transition confirmation.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: safe-zone MODE=2 PoC
    Tool: Bash
    Preconditions: NAS mock running, target points to mock
    Steps:
      1. Run bash scripts/run_setting_poc.sh --mode 2 --target safe
      2. Assert evidence file includes "ack_reason_setting_seen": true
      3. Assert evidence includes tbl_events content with "MODE:" and "->[2]"
    Expected Result: PoC reaches accepted MODE=2 transition
    Evidence: .sisyphus/evidence/mock-setting-poc/task-12-poc-pass.json

  Scenario: negative target safety guard
    Tool: Bash
    Preconditions: accidental production-like host provided
    Steps:
      1. Run runner with forbidden host argument
      2. Assert script aborts before sending queue command
    Expected Result: production target blocked
    Evidence: .sisyphus/evidence/mock-setting-poc/task-12-safety-block.txt
  ```

  **Commit**: YES
  - Message: `feat(mock-protocol): add safe-zone setting PoC runner and evidence export`
  - Files: `scripts/run_setting_poc.sh`, `scripts/export_setting_evidence.py`
  - Pre-commit: `bash scripts/run_setting_poc.sh --help`

- [ ] 13. Gate evaluation and rollback rehearsal

  **What to do**:
  - Evaluate PoC evidence against contract gates.
  - Rehearse rollback path and produce explicit decision report.

  **Must NOT do**:
  - Do not mark PASS if ACK or MODE event evidence is missing.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser checks

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4
  - **Blocked By**: T11, T12

  **References**:
  - `.sisyphus/evidence/mock-setting-poc/task-12-poc-pass.json` - primary gate input.
  - `/tmp/setting-contract/setting-acceptance-contract.yaml` - pass/fail criteria.

  **Acceptance Criteria**:
  - [ ] Gate report generated with PASS/FAIL per criterion.
  - [ ] Rollback rehearsal log generated and validated.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: gate pass report
    Tool: Bash
    Preconditions: complete PoC evidence present
    Steps:
      1. Run python3 scripts/evaluate_setting_gate.py --evidence .sisyphus/evidence/mock-setting-poc/task-12-poc-pass.json --contract /tmp/setting-contract/setting-acceptance-contract.yaml
      2. Assert report includes ACK_SEQUENCE=PASS and MODE_TRANSITION=PASS
    Expected Result: clear reproducible gate decision
    Evidence: .sisyphus/evidence/mock-setting-poc/task-13-gate-pass.json

  Scenario: negative gate fail
    Tool: Bash
    Preconditions: remove ACK evidence field from copied file
    Steps:
      1. Run evaluator on modified evidence
      2. Assert decision is FAIL with explicit missing criterion
    Expected Result: gate blocks incomplete evidence
    Evidence: .sisyphus/evidence/mock-setting-poc/task-13-gate-fail.txt
  ```

  **Commit**: YES
  - Message: `chore(mock-protocol): add gate evaluator and rollback rehearsal`
  - Files: `scripts/evaluate_setting_gate.py`, `docs/SETTING_POC_ROLLBACK.md`
  - Pre-commit: `python3 scripts/evaluate_setting_gate.py --help`

---

## Final Verification Wave (MANDATORY)

- [ ] F1. **Plan Compliance Audit** - `oracle`
  Verify every must-have and must-not-have from this plan using files, commands, and evidence artifacts.

- [ ] F2. **Code Quality Review** - `unspecified-high`
  Run lint/type/test checks relevant to changed files; reject unresolved warnings in protocol path.

- [ ] F3. **Real QA Replay** - `unspecified-high`
  Re-run all task QA scenarios end-to-end in safe zone; verify evidence files exist and are consistent.

- [ ] F4. **Scope Fidelity Check** - `deep`
  Confirm changed files are limited to mock setting protocol path and planned scripts/tests.

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| T1-T3 | `feat(mock-protocol): establish setting contract baseline` | extract/contract/envelope scripts | script self-check + contract validation |
| T4-T6 | `feat(mock-protocol): add configurable stateful setting engine` | server + config files | pytest state machine |
| T7-T10 | `feat(mock-protocol): implement burst delivery and ack semantics` | server API + protocol logic | protocol integration tests |
| T11-T13 | `test(mock-protocol): validate PoC and gate release readiness` | tests + runner + gate scripts | full PoC run + gate evaluator |

---

## Success Criteria

### Verification Commands
```bash
python3 -m pytest /Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_acceptance.py -q
# Expected: all tests pass

python3 -m pytest /Users/martinhorak/Projects/oig-diagnostic-cloud/tests/protocol/test_setting_negative_cases.py -q
# Expected: all tests pass

bash /Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/run_setting_poc.sh --mode 2
# Expected: evidence states ACK Reason=Setting seen and MODE transition to 2 observed

python3 /Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/evaluate_setting_gate.py --evidence .sisyphus/evidence/mock-setting-poc/task-12-poc-pass.json --contract /tmp/setting-contract/setting-acceptance-contract.yaml
# Expected: overall PASS
```

### Final Checklist
- [ ] Successful sequence reproduced from mock: IsNewSet -> Setting burst -> ACK Setting -> END
- [ ] MODE transition evidence present in `tbl_events` (`->[2]`)
- [ ] Failed-pattern safeguards active (static envelope / missing ACK blocked)
- [ ] Rollback rehearsal completed and documented
- [ ] No production cloud impact introduced
