# Offline Proxy + Mock Alignment Plan

## TL;DR

> **Quick Summary**: Align proxy offline/hybrid behavior and mock server state machine to match real cloud session behavior observed in online screening, with strict rollback safety.
>
> **Deliverables**:
> - Protocol contract from real captures (online truth source)
> - Proxy behavior updates for offline/hybrid setting flow
> - Mock server behavior updates for poll/state/session context fidelity
> - Replay/comparison validation harness and evidence
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 5 waves + final verification
> **Critical Path**: T1 -> T2 -> T5 -> T6 -> T8 -> F1-F4

---

## Context

### Original Request
Prepare an execution plan to adjust offline proxy and mock server behavior based on online findings, then validate in hybrid mode and decide when backup can be removed.

### Interview Summary
**Key Discussions**:
- Real cloud in online mode delivered Setting (`tbl_box_prms/MODE`) and BOX produced Setting ACK.
- Mock/offline replay often failed despite valid frame shape/CRC, indicating session-context mismatch.
- IP-path behavior differs (home direct cloud often reset; mobile direct cloud responded protocol-level).
- Hybrid can emit local END on per-frame timeout without global OFFLINE transition.

**Research Findings**:
- Poll rotation observed: `IsNewFW`, `IsNewSet`, `IsNewWeather`.
- Cloud-origin Setting observed as `IsNewFW` response (not only `IsNewSet`).
- Historical raw Setting replay alone is insufficient if surrounding session state is not aligned.

### Metis Review
**Identified Gaps** (addressed in this plan):
- Missing explicit state-machine guardrails -> added feature-flag and rollback gates.
- Missing acceptance criteria -> all criteria are command/tool executable.
- Missing edge-case coverage -> explicit protocol/state edge-case tasks added.
- Scope creep risk -> strict IN/OUT boundaries and exclusion list added.

---

## Work Objectives

### Core Objective
Deliver protocol-faithful offline/hybrid behavior and mock-server behavior so BOX-setting handshake behavior matches online truth under reproducible tests.

### Concrete Deliverables
- Updated proxy behavior in `addon/oig-proxy/proxy.py`, `addon/oig-proxy/cloud_forwarder.py`, `addon/oig-proxy/control_settings.py`, `addon/oig-proxy/hybrid_mode.py`.
- Updated mock behavior in `server.py` (oig-diagnostic-cloud).
- Comparison/replay workflow in `testing/export_ha_session.py`, `testing/replay_session_file.py`, and validation scripts.
- Evidence bundle in `.sisyphus/evidence/` for each scenario.

### Definition of Done
- [ ] Online/hybrid/offline comparison scenarios pass with expected response classes.
- [ ] At least one cloud-origin Setting scenario and one mock/offline Setting scenario produce protocol-consistent outcomes.
- [ ] Hybrid fallback behavior is deterministic and logged (per-frame rescue vs global mode transition).
- [ ] Backup removal gate checklist is green.

### Must Have
- Preserve rollback path via feature flag/config gate until full validation.
- Keep existing stable behavior available as fallback path.
- Explicitly separate per-frame local fallback from global OFFLINE transition.

### Must NOT Have (Guardrails)
- Do not remove backup route before completion of Wave 3 + Final Verification.
- Do not introduce unobserved protocol inventions beyond measured cloud behavior.
- Do not modify unrelated MQTT discovery/entity mapping behavior.
- Do not couple mock changes to production-only credentials or network policy changes.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — all verification is tool/agent executed and evidence-backed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after
- **Framework**: pytest (plus existing repo test runner scripts)
- **Agent-Executed QA**: ALWAYS

### QA Policy
Every task includes at least one happy-path and one negative-path scenario with concrete commands/evidence paths.

| Deliverable Type | Verification Tool | Method |
|------------------|-------------------|--------|
| Proxy behavior | Bash + pytest | run targeted tests and replay assertions |
| Mock server behavior | Bash + curl + container logs | queue setting, replay frames, assert transitions |
| Replay/Comparison | Bash + python scripts | replay captured session, classify responses |
| Hybrid transitions | Bash + log assertions | verify no unintended global offline switch |

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Foundations):
- T1 Protocol baseline capture pack [quick]
- T3 Feature flag + rollback gate spec [quick]

Wave 2 (Contract):
- T2 Protocol contract matrix from captures [deep]

Wave 3 (Alignment):
- T4 Mock poll/session state machine alignment [deep]
- T5 Proxy offline/hybrid response alignment [deep]

Wave 4 (Validation):
- T6 Comparison suite and replay validation [deep]
- T7 Hybrid reliability and cloud disconnect study [unspecified-high]

Wave 5 (Decision):
- T8 Backup removal gate and release decision [quick]

Wave FINAL (After all tasks):
- F1 Plan compliance audit (oracle)
- F2 Code quality review (unspecified-high)
- F3 Real QA replay audit (unspecified-high)
- F4 Scope fidelity check (deep)

Critical Path: T1 -> T2 -> T5 -> T6 -> T8 -> F1-F4

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|------------|--------|------|
| T1 | — | T2, T6 | 1 |
| T3 | — | T5, T7, T8 | 1 |
| T2 | T1 | T4, T5, T6 | 2 |
| T4 | T2 | T6 | 3 |
| T5 | T2, T3 | T6, T7 | 3 |
| T6 | T1, T2, T4, T5 | T8 | 4 |
| T7 | T3, T5 | T8 | 4 |
| T8 | T3, T6, T7 | FINAL | 5 |

---

## TODOs

- [x] 1. Protocol baseline capture pack

  **What to do**:
  - Collect representative online sessions including `IsNewFW/IsNewSet/IsNewWeather`, cloud-origin Setting, BOX Setting ACK.
  - Store normalized artifacts for replay/comparison.

  **Must NOT do**:
  - Do not sanitize away ordering or timing metadata.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: deterministic extraction/packaging.
  - **Skills**: `git-master`
    - `git-master`: preserve clean traceability of fixture artifacts/scripts.
  - **Skills Evaluated but Omitted**:
    - `playwright`: not relevant for TCP protocol capture.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T3)
  - **Blocks**: T2, T6
  - **Blocked By**: None

  **References**:
  - `analysis/ha_snapshot/payloads_ha_full.db` - historical truth source for cloud/box frames.
  - `addon/oig-proxy/utils.py` - capture schema usage.
  - `addon/oig-proxy/config.json` - capture toggles and runtime options.

   **Acceptance Criteria**:
   - [x] Bash query produces capture set with at least one cloud-origin Setting + matching BOX ACK candidate.
   - [x] Evidence files stored under `.sisyphus/evidence/task-1-*.json`.

  **QA Scenarios**:
  ```
  Scenario: baseline extraction succeeds
    Tool: Bash (sqlite3/python)
    Preconditions: payload DB available
    Steps:
      1. Extract session slices containing IsNew* and Setting frames.
      2. Validate ordering and conn_id continuity.
      3. Save normalized fixtures.
    Expected Result: Fixtures include IsNew* + Setting + ACK sequence candidates.
    Evidence: .sisyphus/evidence/task-1-baseline.json

  Scenario: negative - missing DB
    Tool: Bash
    Preconditions: DB path intentionally invalid
    Steps:
      1. Run extraction command with wrong path.
      2. Assert graceful error and no partial fixture output.
    Expected Result: Clear error, non-zero exit, no corrupt artifacts.
    Evidence: .sisyphus/evidence/task-1-missing-db-error.txt
  ```

- [x] 2. Protocol contract matrix from captures

  **What to do**:
  - Derive explicit transition table: request frame class -> expected cloud response class -> expected BOX follow-up.
  - Include timing windows and tolerated variance.

  **Must NOT do**:
  - Do not infer unobserved transitions.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: protocol-state reasoning.
  - **Skills**: `git-master`
    - `git-master`: maintain artifact versioning and traceability.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential
  - **Blocks**: T4, T5, T6
  - **Blocked By**: T1

  **References**:
  - `analysis/ha_snapshot/payloads_ha_full.db` - source for empirical transition matrix.
  - `addon/oig-proxy/cloud_forwarder.py` - current response forwarding behavior.
  - `addon/oig-proxy/proxy.py` - connection/session lifecycle behavior.

  **Acceptance Criteria**:
  - [ ] Contract document enumerates each observed IsNew*/Setting/ACK/END sequence.
  - [ ] Every row cites at least one captured session reference.

  **QA Scenarios**:
  ```
  Scenario: contract consistency check
    Tool: Bash (python validator)
    Preconditions: capture fixtures from Task 1
    Steps:
      1. Validate each contract row against at least one real capture.
      2. Flag unmatched rows.
    Expected Result: 0 unmatched rows.
    Evidence: .sisyphus/evidence/task-2-contract-validation.json

  Scenario: negative - invented transition
    Tool: Bash
    Preconditions: inject fake transition row
    Steps:
      1. Run validator on modified contract.
      2. Assert failure with explicit mismatch.
    Expected Result: Validator rejects unobserved transition.
    Evidence: .sisyphus/evidence/task-2-fake-transition-error.txt
  ```

- [x] 3. Feature flag + rollback gate spec

  **What to do**:
  - Define switchable behavior gates for old/new offline+mock logic.
  - Define hard conditions for backup removal.

  **Must NOT do**:
  - Do not remove legacy path during this phase.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `git-master`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1)
  - **Blocks**: T5, T7, T8
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/config.json` - mode and threshold options.
  - `addon/oig-proxy/hybrid_mode.py` - runtime mode semantics.

  **Acceptance Criteria**:
  - [ ] Written gate spec includes enable/disable procedure and rollback command path.
  - [ ] Backup removal checklist is binary and auditable.

  **QA Scenarios**:
  ```
  Scenario: rollback gate dry-run
    Tool: Bash
    Preconditions: gate spec available
    Steps:
      1. Simulate fail condition against gate checklist.
      2. Verify rollback path is selected automatically.
    Expected Result: Gate blocks removal and points to rollback steps.
    Evidence: .sisyphus/evidence/task-3-gate-dry-run.txt

  Scenario: negative - missing criterion
    Tool: Bash
    Preconditions: remove one mandatory criterion in test copy
    Steps:
      1. Run checklist validator.
      2. Assert failure with missing criterion report.
    Expected Result: Checklist rejected.
    Evidence: .sisyphus/evidence/task-3-checklist-failure.txt
  ```

- [x] 4. Mock poll/session state machine alignment

  **What to do**:
  - Align mock response decisions to contract matrix: poll rotation, Setting gate, ACK follow-up behavior.
  - Ensure no protocol shortcuts that bypass required context.

  **Must NOT do**:
  - Do not emit synthetic responses not in contract.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `git-master`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T5)
  - **Blocks**: T6
  - **Blocked By**: T2

  **References**:
  - `../oig-diagnostic-cloud/server.py` - mock state and response generation.
  - `analysis/ha_snapshot/payloads_ha_full.db` - observed truth for state transitions.

  **Acceptance Criteria**:
  - [ ] Mock emits Setting only under contract-valid context.
  - [ ] ACK/END follow-up order matches contract.

  **QA Scenarios**:
  ```
  Scenario: happy path setting handshake in mock
    Tool: Bash (replay script + mock logs)
    Preconditions: contract fixture loaded
    Steps:
      1. Replay contract-valid sequence into mock.
      2. Assert Setting emitted once.
      3. Assert ACK follow-up response class is correct.
    Expected Result: Sequence class matches online contract.
    Evidence: .sisyphus/evidence/task-4-mock-setting-handshake.json

  Scenario: negative - out-of-context setting trigger
    Tool: Bash
    Preconditions: sequence missing required context
    Steps:
      1. Replay invalid sequence.
      2. Assert mock does not emit Setting.
    Expected Result: No invalid Setting emission.
    Evidence: .sisyphus/evidence/task-4-out-of-context-blocked.json
  ```

- [x] 5. Proxy offline/hybrid response alignment

  **What to do**:
  - Align offline/hybrid local response builder to contract classes (END vs ACK vs local rescue).
  - Keep global mode transition criteria separated from per-frame rescue.

  **Must NOT do**:
  - Do not conflate local frame rescue with full OFFLINE switch.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `git-master`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: T6, T7
  - **Blocked By**: T2, T3

  **References**:
  - `addon/oig-proxy/proxy.py` - online/offline processing entry points.
  - `addon/oig-proxy/cloud_forwarder.py` - cloud error/timeout handling and local fallback emits.
  - `addon/oig-proxy/hybrid_mode.py` - mode transition state machine.

  **Acceptance Criteria**:
  - [ ] Per-frame timeout causes only local frame rescue, not implicit global mode switch.
  - [ ] Global offline transition only via explicit threshold logic.

  **QA Scenarios**:
  ```
  Scenario: per-frame timeout rescue in hybrid
    Tool: Bash (fault injection + logs)
    Preconditions: hybrid mode, induced cloud timeout
    Steps:
      1. Trigger timeout on one frame.
      2. Verify local END/ACK emitted for that frame.
      3. Verify mode remains hybrid online-state.
    Expected Result: Rescue happened, no global offline transition.
    Evidence: .sisyphus/evidence/task-5-hybrid-rescue.txt

  Scenario: threshold-driven global fallback
    Tool: Bash
    Preconditions: repeated cloud failures beyond threshold
    Steps:
      1. Trigger consecutive failures.
      2. Verify global fallback transition occurs once threshold reached.
    Expected Result: Explicit transition event appears.
    Evidence: .sisyphus/evidence/task-5-global-fallback.txt
  ```

- [x] 6. Comparison suite and replay validation

  **What to do**:
  - Run online vs mock vs offline comparison using exported sessions.
  - Compare response classes and timing tolerances.
  - Resolve any contradiction between session-proven cloud-origin Setting behavior and generated contract artifacts.

  **Must NOT do**:
  - Do not mark pass using manual eyeballing only.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `git-master`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential
  - **Blocks**: T8
  - **Blocked By**: T1, T2, T4, T5

  **References**:
  - `testing/export_ha_session.py` - session fixture export.
  - `testing/replay_session_file.py` - controlled replay.
  - `analysis/ha_snapshot/payloads_ha_full.db` - truth baseline.

  **Acceptance Criteria**:
  - [ ] Comparison report generated with per-sequence pass/fail.
  - [ ] No critical mismatches in Setting handshake class transitions.
  - [ ] Any contract contradiction is resolved and documented with capture references.

  **QA Scenarios**:
  ```
  Scenario: full comparison run
    Tool: Bash
    Preconditions: fixtures + aligned implementations available
    Steps:
      1. Run replay across online/mock/offline targets.
      2. Classify responses and compare against contract.
    Expected Result: Critical sequences pass.
    Evidence: .sisyphus/evidence/task-6-comparison-report.json

  Scenario: negative - contract mismatch injected
    Tool: Bash
    Preconditions: intentionally altered expected class for one sequence
    Steps:
      1. Run comparison.
      2. Assert mismatch detected and reported.
    Expected Result: Suite fails with explicit mismatch line.
    Evidence: .sisyphus/evidence/task-6-mismatch-detected.txt
  ```

- [x] 7. Hybrid reliability and cloud disconnect study

  **What to do**:
  - Run controlled hybrid soak with cloud resets/timeouts.
  - Verify whether cloud disconnects correlate with session transitions and not with local fallback side-effects.

  **Must NOT do**:
  - Do not disable safety logging during soak.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `git-master`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with T6)
  - **Blocks**: T8
  - **Blocked By**: T3, T5

  **References**:
  - `addon/oig-proxy/hybrid_mode.py` - fail counters and transitions.
  - `addon/oig-proxy/proxy_status.py` - runtime status metrics.
  - `addon/oig-proxy/telemetry_collector.py` - telemetry windows and counters.

  **Acceptance Criteria**:
  - [ ] Hybrid soak report includes cloud_reset/cloud_timeout frequencies and transition map.
  - [ ] No unintended oscillation between states.

  **QA Scenarios**:
  ```
  Scenario: hybrid soak baseline
    Tool: Bash
    Preconditions: hybrid enabled, cloud reachable
    Steps:
      1. Run 10+ minute soak.
      2. Collect reconnect/timeouts and state transitions.
    Expected Result: No unexpected OFFLINE oscillation.
    Evidence: .sisyphus/evidence/task-7-hybrid-soak.json

  Scenario: negative - forced intermittent cloud loss
    Tool: Bash
    Preconditions: induced intermittent upstream failures
    Steps:
      1. Inject controlled failures.
      2. Verify hybrid policy behavior matches threshold rules.
    Expected Result: Predictable transitions, no state corruption.
    Evidence: .sisyphus/evidence/task-7-failure-injection.json
  ```

- [x] 8. Backup removal gate and release decision

  **What to do**:
  - Evaluate all gate criteria; decide KEEP or REMOVE backup route.
  - Rehearse rollback once before any removal.

  **Must NOT do**:
  - Do not remove backup if any critical criterion fails.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `git-master`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential
  - **Blocks**: F1, F2, F3, F4
  - **Blocked By**: T3, T6, T7

  **References**:
  - `.sisyphus/evidence/` - all previous task evidence.
  - `addon/oig-proxy/config.json` - mode and fallback controls.

  **Acceptance Criteria**:
  - [ ] Gate checklist produced with PASS/FAIL for each criterion.
  - [ ] Rollback rehearsal executed and evidence captured.

  **QA Scenarios**:
  ```
  Scenario: gate pass evaluation
    Tool: Bash
    Preconditions: all prior evidence available
    Steps:
      1. Run gate evaluator.
      2. Emit final decision KEEP/REMOVE backup.
    Expected Result: Decision is evidence-backed and reproducible.
    Evidence: .sisyphus/evidence/task-8-gate-decision.json

  Scenario: negative - forced gate failure
    Tool: Bash
    Preconditions: simulate missing critical evidence
    Steps:
      1. Run gate evaluator with missing artifact.
      2. Assert automatic KEEP + rollback recommendation.
    Expected Result: Backup not removed.
    Evidence: .sisyphus/evidence/task-8-gate-failure.txt
  ```

---

## Final Verification Wave (MANDATORY)

- [x] F1. **Plan Compliance Audit** — `oracle`
- [x] F2. **Code Quality Review** — `unspecified-high`
- [x] F3. **Real QA Replay Audit** — `unspecified-high`
- [x] F4. **Scope Fidelity Check** — `deep`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| Wave 1 | `chore(protocol): add baseline contract artifacts` | fixtures/scripts | replay smoke |
| Wave 3 | `fix(proxy): align offline-hybrid response state machine` | proxy files | targeted pytest + replay |
| Wave 3 | `fix(mock): align poll-setting session behavior` | mock server files | mock integration replay |
| Wave 3 | `test(protocol): add comparison and hybrid validation` | tests/scripts | full comparison suite |

---

## Success Criteria

### Verification Commands
```bash
python3 testing/export_ha_session.py --ssh-host ha --output testing/replay_session_latest.json
python3 testing/replay_session_file.py --session-file testing/replay_session_latest.json --host 185.25.185.30 --timeout 4 --hold-after 20
pytest -q
```

### Final Checklist
- [ ] Cloud-origin Setting and BOX ACK behavior is reproducible in aligned test path.
- [ ] Hybrid per-frame fallback and global transition semantics are explicit and validated.
- [ ] Mock and offline paths match contract response classes for critical sequences.
- [ ] Backup removal gate is evidence-backed and approved.
