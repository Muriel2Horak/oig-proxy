# Proxy Thin Pass-Through + Twin Sidecar Refactor

## TL;DR

> **Quick Summary**: Refactor proxy to strict transport-only pass-through for BOX↔cloud while moving decision logic to a sidecar twin, preserving MQTT telemetry capture for `tbl_*` and `tbl_events` with non-blocking behavior.
>
> **Deliverables**:
> - Transport-only proxy path with feature-flag migration gates
> - Non-blocking telemetry tap to existing MQTT schema/topics
> - Sidecar twin activation policy (`timeout/connect fail`, threshold=3, deactivate after 5m stable cloud)
> - TDD-backed migration with rollback gates per wave
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 5 waves + final verification
> **Critical Path**: T1 → T7 → T10 → T16 → T21 → F1-F4

---

## Context

### Original Request
User wants proxy to stop carrying business/fallback complexity and become transparent. Telemetry capture/publish to MQTT must remain. Twin should live beside proxy, activated only in defined situations.

### Interview Summary
**Key Discussions**:
- Desired architecture: thin pass-through proxy + independent twin sidecar.
- Keep telemetry extraction (`tbl_*`, `tbl_events`) in proxy as passive tap.
- Migration safety: keep HYBRID/OFFLINE temporarily behind flags, remove later.
- Test strategy selected: **TDD**.
- Cloud fail definition selected: **timeout + connect fail**.
- Twin activation threshold selected: **3 consecutive failures**.
- Twin deactivation selected: **5 minutes stable cloud**.

**Research Findings**:
- `addon/oig-proxy/proxy.py` currently mixes transport and control/twin/mode behavior.
- `addon/oig-proxy/cloud_forwarder.py` encapsulates cloud forwarding but includes mode/offline coupling.
- `addon/oig-proxy/digital_twin.py` + `twin_state.py` + `twin_transaction.py` contain core twin state logic.
- `addon/oig-proxy/mqtt_publisher.py` currently publishes without queue/replay fallback, enabling cleaner telemetry tap.
- Test infra is mature (pytest + coverage + CI workflows).

### Metis Review
**Identified Gaps (addressed)**:
- Missing explicit cloud-fail criteria → fixed with `timeout/connect fail + 3 consecutive`.
- Missing anti-flap deactivation policy → fixed with `5m stable cloud`.
- Missing guardrails against scope creep → explicitly listed under Must NOT Have.
- Missing non-blocking telemetry guarantee → codified in acceptance and QA scenarios.

---

## Work Objectives

### Core Objective
Split responsibilities so proxy is transport-only and deterministic in normal operation, while twin business decisions run out-of-band with explicit activation rules and rollback-safe migration.

### Concrete Deliverables
- Transport contract + feature-flag migration contract documented and implemented.
- Refactored proxy routing path with clear separation between pass-through and optional sidecar activation.
- Telemetry tap preserving current MQTT topic/payload contract.
- Sidecar activation state machine policy wired with selected thresholds.
- Comprehensive TDD and agent-executed QA evidence.

### Definition of Done
- [ ] All migration tasks completed with evidence in `.sisyphus/evidence/`.
- [ ] Full test suite passes (`python3 -m pytest tests/ -q`).
- [ ] Lint/type checks for touched production files pass.
- [ ] Final verification wave (F1-F4) all APPROVE.

### Must Have
- Proxy critical path must not depend on twin/telemetry availability.
- MQTT telemetry for `tbl_*` + `tbl_events` remains functionally equivalent.
- Twin activation/deactivation policy matches confirmed thresholds.
- Feature-flag rollback must be fast and deterministic.

### Must NOT Have (Guardrails)
- No new product features outside refactor scope.
- No MQTT topic schema drift (namespace/topic/payload contract unchanged).
- No reintroduction of queue/replay logic in MQTT publisher path.
- No human-only acceptance steps.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — All verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: TDD
- **Framework**: pytest (+ existing CI coverage/lint/security workflows)
- **Workflow**: Each implementation task includes RED → GREEN → REFACTOR expectations

### QA Policy
Every task includes at least one happy-path and one negative-path agent scenario.
Evidence path pattern: `.sisyphus/evidence/task-{N}-{scenario}.{ext}`.

- **Frontend/UI**: N/A unless task introduces UI
- **CLI/TUI**: `interactive_bash` for service/process flows when needed
- **API/Backend**: Bash (`curl`, script invocations, pytest)
- **Module/Library**: Bash (`python -m pytest`, focused command assertions)

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Foundation — can start immediately):
├── Task 1: Baseline contract + invariants map [quick]
├── Task 2: Feature-flag matrix + rollback switches [quick]
├── Task 3: Cloud fail detector policy spec (timeout/connect/3 fails) [quick]
├── Task 4: Twin deactivate hysteresis spec (5m stable cloud) [quick]
├── Task 5: Telemetry contract freeze (`tbl_*`, `tbl_events`) [quick]
└── Task 6: TDD harness update for refactor waves [quick]

Wave 2 (Extraction boundaries — after Wave 1):
├── Task 7: Extract transport-only route function(s) from proxy [deep]
├── Task 8: Isolate telemetry tap adapter (non-blocking) [unspecified-high]
├── Task 9: Isolate sidecar activation orchestrator interface [unspecified-high]
├── Task 10: Remove direct twin coupling from normal pass-through path [deep]
└── Task 11: CloudForwarder offline fallback decoupling behind flags [deep]

Wave 3 (Twin sidecar wiring — after Wave 2):
├── Task 12: Sidecar activation trigger implementation (timeout/connect fail + threshold) [deep]
├── Task 13: Sidecar deactivation hysteresis implementation [deep]
├── Task 14: Control routing arbitration (cloud vs twin) with explicit precedence [unspecified-high]
├── Task 15: Correlation ID propagation across proxy/telemetry/twin [unspecified-high]
└── Task 16: End-to-end fail-open guarantees when telemetry/twin unavailable [deep]

Wave 4 (Hard cleanup — after Wave 3):
├── Task 17: Remove dead mode-branch code from proxy critical path (flagged fallback kept) [deep]
├── Task 18: Remove obsolete helpers/routes no longer reachable [quick]
├── Task 19: Test suite simplification for removed coupling [quick]
├── Task 20: Docs/runbook updates for new operating model [writing]
└── Task 21: Canary rollout and rollback gate scripts [unspecified-high]

Wave 5 (Pre-final integration):
├── Task 22: Full regression suite + coverage gate [deep]
├── Task 23: Fault injection scenarios (cloud flap, twin down, mqtt down) [deep]
├── Task 24: Performance sanity guard (transport latency no regression) [unspecified-high]
└── Task 25: Release candidate checklist + evidence collation [quick]

Wave FINAL (After ALL tasks — 4 parallel reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real QA replay of all task scenarios (unspecified-high)
└── Task F4: Scope fidelity and no-creep check (deep)

Critical Path: T1 → T7 → T10 → T16 → T21 → F1-F4
Parallel Speedup: ~65-70% vs sequential
Max Concurrent: 6

### Dependency Matrix (full)

- T1: — → T7, T8, T9
- T2: — → T11, T17, T21
- T3: — → T12
- T4: — → T13
- T5: — → T8, T15, T19
- T6: — → T22, T23
- T7: T1 → T10, T16
- T8: T1,T5 → T15, T16
- T9: T1 → T12, T14
- T10: T7 → T17
- T11: T2 → T17
- T12: T3,T9 → T14, T16
- T13: T4 → T16
- T14: T9,T12 → T16
- T15: T5,T8 → T23
- T16: T7,T8,T12,T13,T14 → T17,T22,T23,T24
- T17: T2,T10,T11,T16 → T18,T19,T20,T21
- T18: T17 → T22
- T19: T5,T17 → T22
- T20: T17 → T25
- T21: T2,T17 → T25
- T22: T6,T16,T18,T19 → F1,F2,F3,F4
- T23: T6,T15,T16 → F3,F4
- T24: T16 → F2,F4
- T25: T20,T21 → F1,F3
- F1/F2/F3/F4: T22,T23,T24,T25 → done

### Agent Dispatch Summary
- Wave 1: 6 tasks (quick-heavy)
- Wave 2: 5 tasks (deep/unspecified-high)
- Wave 3: 5 tasks (deep core)
- Wave 4: 5 tasks (cleanup + rollout)
- Wave 5: 4 tasks (hard verification)
- Final: 4 independent reviewers

---

## TODOs

- [ ] 1. Baseline contract + invariants map

  **What to do**:
  - Zmapovat aktuální transport path a twin coupling body v `proxy.py`/`cloud_forwarder.py`.
  - Sepsat „transport-only invarianty“ (proxy nesmí měnit payload/flow mimo transport errors).

  **Must NOT do**:
  - Neměnit runtime behavior, jen mapovat/validovat baseline.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 1)
  - Blocks: T7, T8, T9
  - Blocked By: None

  **References**:
  - `addon/oig-proxy/proxy.py` - hlavní orchestrace režimů a routing
  - `addon/oig-proxy/cloud_forwarder.py` - cloud forwarding/fallback body

  **Acceptance Criteria**:
  - [ ] Vznikne baseline mapa coupling bodů v evidence markdown.

  **QA Scenarios**:
  - Scenario: Baseline mapping exists
    - Tool: Bash
    - Steps: 1) `grep` ověří přítomnost mapy 2) ověří, že obsahuje sekce transport/twin/telemetry
    - Expected Result: soubor existuje a obsahuje všechny 3 sekce
    - Evidence: `.sisyphus/evidence/task-1-baseline-map.md`
  - Scenario: Missing section detection
    - Tool: Bash
    - Steps: simulovat kontrolu povinných sekcí regexem
    - Expected Result: kontrola failne při chybějící sekci
    - Evidence: `.sisyphus/evidence/task-1-baseline-map-error.txt`

- [ ] 2. Feature-flag matrix + rollback switches

  **What to do**:
  - Definovat feature flagy pro: thin-pass-through, sidecar activation, legacy fallback.
  - Definovat rollback pořadí a fallback priority.

  **Must NOT do**:
  - Nepřidávat unrelated konfigurační feature.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 1)
  - Blocks: T11, T17, T21
  - Blocked By: None

  **References**:
  - `addon/oig-proxy/config.py` - env guardrails a startup konfigurace
  - `addon/oig-proxy/hybrid_mode.py` - stávající mode policy body

  **Acceptance Criteria**:
  - [ ] Flag matrix obsahuje defaulty a rollback sequence.

  **QA Scenarios**:
  - Scenario: Flag matrix completeness
    - Tool: Bash
    - Steps: validace, že jsou definované všechny 3 flag skupiny
    - Expected Result: PASS
    - Evidence: `.sisyphus/evidence/task-2-flag-matrix.md`
  - Scenario: Rollback path sanity
    - Tool: Bash
    - Steps: ověřit, že pro každý flag existuje rollback target
    - Expected Result: žádný flag bez rollbacku
    - Evidence: `.sisyphus/evidence/task-2-flag-rollback.txt`

- [ ] 3. Cloud fail detector policy spec

  **What to do**:
  - Zakódovat policy: `timeout + connect fail` a aktivace po `3` po sobě jdoucích fail.
  - Definovat reset counteru při stabilním cloud recover.

  **Must NOT do**:
  - Nepoužít single-fail aktivaci.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 1)
  - Blocks: T12
  - Blocked By: None

  **References**:
  - `addon/oig-proxy/cloud_forwarder.py` - fail eventy, timeout handling
  - `addon/oig-proxy/proxy.py` - mode routing decision hooks

  **Acceptance Criteria**:
  - [ ] Policy dokumentuje timeout/connect fail signály a threshold=3.

  **QA Scenarios**:
  - Scenario: 3-fail activation threshold
    - Tool: Bash (pytest)
    - Steps: unit test nasimuluje 3 fail eventy
    - Expected Result: twin activation state = true
    - Evidence: `.sisyphus/evidence/task-3-threshold-pass.txt`
  - Scenario: 2 fails should NOT activate
    - Tool: Bash (pytest)
    - Steps: unit test nasimuluje 2 fail eventy
    - Expected Result: twin activation state = false
    - Evidence: `.sisyphus/evidence/task-3-threshold-error.txt`

- [ ] 4. Twin deactivation hysteresis spec

  **What to do**:
  - Implementovat/zakotvit deactivation po 5 minutách stabilního cloudu.
  - Definovat anti-flap chování.

  **Must NOT do**:
  - Nepřepínat okamžitě po prvním success.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 1)
  - Blocks: T13
  - Blocked By: None

  **References**:
  - `addon/oig-proxy/proxy.py` - session twin activation/deactivation funkce

  **Acceptance Criteria**:
  - [ ] Hysterese 5m je testovatelná časovým simulátorem.

  **QA Scenarios**:
  - Scenario: stable cloud for 5m deactivates twin
    - Tool: Bash (pytest)
    - Expected Result: twin deaktivován až po 300s
    - Evidence: `.sisyphus/evidence/task-4-hysteresis-pass.txt`
  - Scenario: cloud flap before 5m keeps twin active
    - Tool: Bash (pytest)
    - Expected Result: twin zůstává active
    - Evidence: `.sisyphus/evidence/task-4-hysteresis-error.txt`

- [ ] 5. Telemetry contract freeze

  **What to do**:
  - Zamknout topic/payload kontrakt pro `tbl_*` a `tbl_events`.
  - Definovat explicitně, co se nesmí změnit.

  **Must NOT do**:
  - Neměnit namespace/topic naming.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 1)
  - Blocks: T8, T15, T19
  - Blocked By: None

  **References**:
  - `addon/oig-proxy/mqtt_publisher.py` - state publish flow
  - `addon/oig-proxy/proxy.py` - event/table publish call sites

  **Acceptance Criteria**:
  - [ ] Contract snapshot exists and is referenced by tests.

  **QA Scenarios**:
  - Scenario: topic contract unchanged
    - Tool: Bash (pytest)
    - Expected Result: golden topic assertions pass
    - Evidence: `.sisyphus/evidence/task-5-topic-pass.txt`
  - Scenario: payload key drift detection
    - Tool: Bash (pytest)
    - Expected Result: test fails when key removed/renamed
    - Evidence: `.sisyphus/evidence/task-5-topic-error.txt`

- [ ] 6. TDD harness update for refactor waves

  **What to do**:
  - Připravit test skeletony pro Wave 2/3 extraction.
  - Rozdělit test suites na transport/telemetry/twin activation.

  **Must NOT do**:
  - Nepřepisovat unrelated testy.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 1)
  - Blocks: T22, T23
  - Blocked By: None

  **References**:
  - `tests/test_proxy_main_loop.py`
  - `tests/test_digital_twin.py`

  **Acceptance Criteria**:
  - [ ] RED tests exist pro planned extractions.

  **QA Scenarios**:
  - Scenario: RED baseline
    - Tool: Bash (pytest)
    - Expected Result: nové testy fail před implementací
    - Evidence: `.sisyphus/evidence/task-6-red.txt`
  - Scenario: harness partitioning
    - Tool: Bash
    - Expected Result: test markers/groups odpovídají 3 boundary modelu
    - Evidence: `.sisyphus/evidence/task-6-groups.md`

- [ ] 7. Extract transport-only route function(s)

  **What to do**:
  - Vyčlenit z `proxy.py` čistý forward path bez business větvení.
  - Udržet fail-open vlastnost vůči sidecar/telemetry.

  **Must NOT do**:
  - Nevkládat twin/mode rozhodování do nové transport funkce.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: NO (Wave 2)
  - Blocks: T10, T16
  - Blocked By: T1

  **References**:
  - `addon/oig-proxy/proxy.py:_route_box_frame_by_mode`
  - `addon/oig-proxy/cloud_forwarder.py:forward_frame`

  **Acceptance Criteria**:
  - [ ] Transport route testy pro cloud-healthy path jsou GREEN.

  **QA Scenarios**:
  - Scenario: pass-through happy path
    - Tool: Bash (pytest)
    - Expected Result: frame forwarded, ACK from cloud returned
    - Evidence: `.sisyphus/evidence/task-7-pass.txt`
  - Scenario: twin unavailable must not block transport
    - Tool: Bash (pytest)
    - Expected Result: pass-through stále funguje
    - Evidence: `.sisyphus/evidence/task-7-error.txt`

- [ ] 8. Isolate telemetry tap adapter (non-blocking)

  **What to do**:
  - Oddělit telemetry publish jako async tap mimo kritickou cestu.
  - Garantovat, že publish failure nemění transport výsledek.

  **Must NOT do**:
  - Nečekat synchronně na MQTT publish v transport path.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 2)
  - Blocks: T15, T16
  - Blocked By: T1, T5

  **References**:
  - `addon/oig-proxy/mqtt_publisher.py:publish_data`
  - `addon/oig-proxy/proxy.py` telemetry publish call sites

  **Acceptance Criteria**:
  - [ ] Telemetry exception nezpůsobí frame drop/timeout.

  **QA Scenarios**:
  - Scenario: telemetry success
    - Tool: Bash (pytest)
    - Expected Result: MQTT publish called, transport unaffected
    - Evidence: `.sisyphus/evidence/task-8-pass.txt`
  - Scenario: telemetry failure path
    - Tool: Bash (pytest)
    - Expected Result: error logged, transport response still success
    - Evidence: `.sisyphus/evidence/task-8-error.txt`

- [ ] 9. Isolate sidecar activation orchestrator interface

  **What to do**:
  - Zavést explicitní interface mezi proxy transport a sidecar orchestrátorem.
  - Oddělit activation decision od frame forwarding funkcí.

  **Must NOT do**:
  - Nedržet hidden coupling přes sdílené mutable stavy bez rozhraní.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 2)
  - Blocks: T12, T14
  - Blocked By: T1

  **References**:
  - `addon/oig-proxy/proxy.py` twin activation hooks
  - `addon/oig-proxy/digital_twin.py` twin entry points

  **Acceptance Criteria**:
  - [ ] Proxy volá orchestration interface, ne přímo business state internals.

  **QA Scenarios**:
  - Scenario: orchestration interface invoked
    - Tool: Bash (pytest)
    - Expected Result: mock interface called with expected args
    - Evidence: `.sisyphus/evidence/task-9-pass.txt`
  - Scenario: interface unavailable
    - Tool: Bash (pytest)
    - Expected Result: transport path continues without crash
    - Evidence: `.sisyphus/evidence/task-9-error.txt`

- [ ] 10. Remove direct twin coupling from normal pass-through path

  **What to do**:
  - Odstranit twin branching z default cloud-healthy flow.
  - Twin branching ponechat jen v explicit activation path.

  **Must NOT do**:
  - Nezměnit cloud-healthy behavior.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: NO (Wave 2)
  - Blocks: T17
  - Blocked By: T7

  **References**:
  - `addon/oig-proxy/proxy.py:_handle_box_frame_iteration`
  - `addon/oig-proxy/proxy.py:_activate_session_twin_mode_if_needed`

  **Acceptance Criteria**:
  - [ ] Cloud-healthy path netriggeruje twin decision logiku.

  **QA Scenarios**:
  - Scenario: cloud healthy no twin path
    - Tool: Bash (pytest)
    - Expected Result: twin call count = 0 for normal frames
    - Evidence: `.sisyphus/evidence/task-10-pass.txt`
  - Scenario: explicit activation still routes to twin
    - Tool: Bash (pytest)
    - Expected Result: twin call occurs only under activation condition
    - Evidence: `.sisyphus/evidence/task-10-error.txt`

- [ ] 11. CloudForwarder offline fallback decoupling behind flags

  **What to do**:
  - Přesunout offline fallback rozhodování mimo hardcoded forward flow.
  - Nechat legacy fallback pouze za migracním flagem.

  **Must NOT do**:
  - Neodstranit fallback naráz bez rollback cesty.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 2)
  - Blocks: T17
  - Blocked By: T2

  **References**:
  - `addon/oig-proxy/cloud_forwarder.py:fallback_offline`
  - `addon/oig-proxy/proxy.py:_handle_frame_local_offline`

  **Acceptance Criteria**:
  - [ ] Legacy fallback reachable only when flag enabled.

  **QA Scenarios**:
  - Scenario: fallback flag OFF
    - Tool: Bash (pytest)
    - Expected Result: local offline ACK path not entered
    - Evidence: `.sisyphus/evidence/task-11-pass.txt`
  - Scenario: fallback flag ON
    - Tool: Bash (pytest)
    - Expected Result: legacy fallback path remains functional
    - Evidence: `.sisyphus/evidence/task-11-error.txt`

- [ ] 12. Sidecar activation trigger implementation

  **What to do**:
  - Implementovat trigger `timeout/connect fail` + threshold `3` consecutive.
  - Napojit trigger na sidecar activation orchestrator.

  **Must NOT do**:
  - Neaktivovat twin na non-failure signálech.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 3)
  - Blocks: T14, T16
  - Blocked By: T3, T9

  **References**:
  - `addon/oig-proxy/cloud_forwarder.py` failure paths
  - `addon/oig-proxy/proxy.py` session activation hooks

  **Acceptance Criteria**:
  - [ ] Aktivace proběhne přesně na 3. fail eventu.

  **QA Scenarios**:
  - Scenario: 3 failures activate sidecar
    - Tool: Bash (pytest)
    - Expected Result: activation state true at failure #3
    - Evidence: `.sisyphus/evidence/task-12-pass.txt`
  - Scenario: success resets failure counter
    - Tool: Bash (pytest)
    - Expected Result: counter reset, activation not triggered incorrectly
    - Evidence: `.sisyphus/evidence/task-12-error.txt`

- [ ] 13. Sidecar deactivation hysteresis implementation

  **What to do**:
  - Implementovat deactivation po 5 minutách stabilního cloudu.
  - Přidat anti-flap guard (při fail eventu timer reset).

  **Must NOT do**:
  - Nepřepínat sidecar on/off při krátkých flapech.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 3)
  - Blocks: T16
  - Blocked By: T4

  **References**:
  - `addon/oig-proxy/proxy.py:_maybe_deactivate_session_twin_mode_if_idle`

  **Acceptance Criteria**:
  - [ ] Deactivation pouze po 300s stable window.

  **QA Scenarios**:
  - Scenario: stable 300s deactivates
    - Tool: Bash (pytest)
    - Expected Result: sidecar off after 300s stable
    - Evidence: `.sisyphus/evidence/task-13-pass.txt`
  - Scenario: flap at 240s keeps sidecar on
    - Tool: Bash (pytest)
    - Expected Result: no deactivation
    - Evidence: `.sisyphus/evidence/task-13-error.txt`

- [ ] 14. Control routing arbitration with explicit precedence

  **What to do**:
  - Upevnit precedence: cloud healthy => cloud wins; sidecar only when activated/local explicit.
  - Konsolidovat routing rozhodnutí do jednoho policy bodu.

  **Must NOT do**:
  - Nedovolit dual-writer chování cloud+twin současně.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 3)
  - Blocks: T16
  - Blocked By: T9, T12

  **References**:
  - `addon/oig-proxy/proxy.py:_resolve_local_control_routing`
  - `addon/oig-proxy/control_pipeline.py`

  **Acceptance Criteria**:
  - [ ] Arbitration tests pokrývají cloud-healthy/cloud-fail/local-explicit.

  **QA Scenarios**:
  - Scenario: cloud healthy precedence
    - Tool: Bash (pytest)
    - Expected Result: route target = cloud
    - Evidence: `.sisyphus/evidence/task-14-pass.txt`
  - Scenario: cloud fail + activation
    - Tool: Bash (pytest)
    - Expected Result: route target = twin, no cloud write
    - Evidence: `.sisyphus/evidence/task-14-error.txt`

- [ ] 15. Correlation ID propagation across proxy/telemetry/twin

  **What to do**:
  - Zavést nebo sjednotit correlation ID threading přes transport + telemetry + twin.
  - Zajistit audit traceability v log/event výstupech.

  **Must NOT do**:
  - Neztratit correlation ID při retry/failover.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 3)
  - Blocks: T23
  - Blocked By: T5, T8

  **References**:
  - `addon/oig-proxy/telemetry_collector.py`
  - `addon/oig-proxy/proxy.py` frame record helpers

  **Acceptance Criteria**:
  - [ ] Correlation ID visible ve všech 3 vrstvách pro stejnou transakci.

  **QA Scenarios**:
  - Scenario: end-to-end correlation
    - Tool: Bash (pytest)
    - Expected Result: same correlation ID in proxy log + telemetry + twin record
    - Evidence: `.sisyphus/evidence/task-15-pass.txt`
  - Scenario: missing id auto-generation
    - Tool: Bash (pytest)
    - Expected Result: generated ID propagated consistently
    - Evidence: `.sisyphus/evidence/task-15-error.txt`

- [ ] 16. End-to-end fail-open guarantees when telemetry/twin unavailable

  **What to do**:
  - Zajistit, že výpadek telemetry nebo twin neshodí transport path.
  - Přidat guard testy pro dependency failure.

  **Must NOT do**:
  - Nepřidat hard dependency transportu na telemetry/twin.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: NO (Wave 3)
  - Blocks: T17, T22, T23, T24
  - Blocked By: T7, T8, T12, T13, T14

  **References**:
  - `addon/oig-proxy/proxy.py`
  - `addon/oig-proxy/mqtt_publisher.py`
  - `addon/oig-proxy/digital_twin.py`

  **Acceptance Criteria**:
  - [ ] Dependency failure scenarios keep pass-through alive.

  **QA Scenarios**:
  - Scenario: telemetry down
    - Tool: Bash (pytest)
    - Expected Result: transport still returns cloud ACK
    - Evidence: `.sisyphus/evidence/task-16-pass.txt`
  - Scenario: twin down while cloud healthy
    - Tool: Bash (pytest)
    - Expected Result: no transport impact
    - Evidence: `.sisyphus/evidence/task-16-error.txt`

- [ ] 17. Remove dead mode-branch code from proxy critical path (flagged fallback kept)

  **What to do**:
  - Odstranit nepotřebné větve z hlavní path, ale ponechat migration fallback za flagem.
  - Zjednodušit execution flow na transport-first model.

  **Must NOT do**:
  - Neodstranit fallback branch bez flag guardu.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: NO (Wave 4)
  - Blocks: T18, T19, T20, T21
  - Blocked By: T2, T10, T11, T16

  **References**:
  - `addon/oig-proxy/proxy.py`
  - `addon/oig-proxy/hybrid_mode.py`

  **Acceptance Criteria**:
  - [ ] Default path je transport-only; fallback dostupný jen přes flag.

  **QA Scenarios**:
  - Scenario: default route minimality
    - Tool: Bash (pytest)
    - Expected Result: tests potvrzují reduced branching
    - Evidence: `.sisyphus/evidence/task-17-pass.txt`
  - Scenario: fallback branch guarded
    - Tool: Bash (pytest)
    - Expected Result: branch reachable only with flag=true
    - Evidence: `.sisyphus/evidence/task-17-error.txt`

- [ ] 18. Remove obsolete helpers/routes no longer reachable

  **What to do**:
  - Smazat dead helpers vzniklé po oddělení sidecaru.
  - Ujistit se, že nejsou reference v runtime ani testech.

  **Must NOT do**:
  - Nesmazat API surface, které je stále využívané.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 4)
  - Blocks: T22
  - Blocked By: T17

  **References**:
  - `addon/oig-proxy/proxy.py`
  - `addon/oig-proxy/cloud_forwarder.py`

  **Acceptance Criteria**:
  - [ ] LSP references pro odstraněné symboly = 0.

  **QA Scenarios**:
  - Scenario: no stale references
    - Tool: LSP + Bash
    - Expected Result: žádné unresolved imports/symbols
    - Evidence: `.sisyphus/evidence/task-18-pass.txt`
  - Scenario: runtime smoke
    - Tool: Bash (pytest subset)
    - Expected Result: smoke test bez regressí
    - Evidence: `.sisyphus/evidence/task-18-error.txt`

- [ ] 19. Test suite simplification for removed coupling

  **What to do**:
  - Aktualizovat/smazat testy navázané na odstraněnou coupling logiku.
  - Zachovat coverage kritických behavior cílů.

  **Must NOT do**:
  - Nezmenšit coverage pod dohodnutý gate.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 4)
  - Blocks: T22
  - Blocked By: T5, T17

  **References**:
  - `tests/test_proxy_main_loop.py`
  - `tests/test_digital_twin.py`

  **Acceptance Criteria**:
  - [ ] Test suite běží bez obsolete assumptions.

  **QA Scenarios**:
  - Scenario: refactor-aligned suite
    - Tool: Bash (pytest)
    - Expected Result: refactor-related suites pass
    - Evidence: `.sisyphus/evidence/task-19-pass.txt`
  - Scenario: removed behavior assertion
    - Tool: Bash (pytest)
    - Expected Result: obsolete behavior assertion removed/replaced
    - Evidence: `.sisyphus/evidence/task-19-error.txt`

- [ ] 20. Docs/runbook updates for new operating model

  **What to do**:
  - Aktualizovat operational docs: default transport mode, sidecar activation policy, rollback postup.
  - Přidat canary/incident runbook část.

  **Must NOT do**:
  - Nepopsat behavior odlišně od implementace.

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 4)
  - Blocks: T25
  - Blocked By: T17

  **References**:
  - `README.md`
  - `DEPLOYMENT.md`

  **Acceptance Criteria**:
  - [ ] Runbook obsahuje activation/deactivation thresholdy + rollback steps.

  **QA Scenarios**:
  - Scenario: runbook completeness check
    - Tool: Bash
    - Expected Result: všechny policy body dohledatelné v docs
    - Evidence: `.sisyphus/evidence/task-20-pass.md`
  - Scenario: contradiction check
    - Tool: Bash
    - Expected Result: žádný rozpor mezi docs a config defaults
    - Evidence: `.sisyphus/evidence/task-20-error.md`

- [ ] 21. Canary rollout and rollback gate scripts

  **What to do**:
  - Připravit skripty/checklist pro canary rollout a okamžitý rollback.
  - Navázat na feature-flag matrix.

  **Must NOT do**:
  - Nespouštět automaticky produkční změny bez explicitního potvrzení.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 4)
  - Blocks: T25
  - Blocked By: T2, T17

  **References**:
  - `.sisyphus/plans/proxy-thin-pass-through-twin-sidecar-refactor.md` (this plan)
  - existing deployment scripts in repo root

  **Acceptance Criteria**:
  - [ ] Canary gate checklist executable by agent commands.

  **QA Scenarios**:
  - Scenario: canary gate pass path
    - Tool: Bash
    - Expected Result: all gates PASS produce go/no-go output
    - Evidence: `.sisyphus/evidence/task-21-pass.txt`
  - Scenario: rollback trigger path
    - Tool: Bash
    - Expected Result: threshold breach results in rollback steps output
    - Evidence: `.sisyphus/evidence/task-21-error.txt`

- [ ] 22. Full regression suite + coverage gate

  **What to do**:
  - Spustit kompletní regresi a coverage gates po refaktoru.
  - Opravit regresní pády bez scope creep.

  **Must NOT do**:
  - Neschovat fail testy skipy bez zdůvodnění.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 5)
  - Blocks: Final wave
  - Blocked By: T6, T16, T18, T19

  **References**:
  - `.github/workflows/ci.yml`
  - `.coveragerc`

  **Acceptance Criteria**:
  - [ ] `python3 -m pytest tests/ -q` PASS
  - [ ] coverage gate PASS

  **QA Scenarios**:
  - Scenario: full suite pass
    - Tool: Bash
    - Expected Result: zero failed tests
    - Evidence: `.sisyphus/evidence/task-22-pass.txt`
  - Scenario: coverage fail detection
    - Tool: Bash
    - Expected Result: gate fails when threshold not met
    - Evidence: `.sisyphus/evidence/task-22-error.txt`

- [ ] 23. Fault injection scenarios

  **What to do**:
  - Otestovat cloud flap, twin down, mqtt down.
  - Ověřit fail-open transport a správnou sidecar policy.

  **Must NOT do**:
  - Netestovat jen happy path.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 5)
  - Blocks: Final wave
  - Blocked By: T6, T15, T16

  **References**:
  - `tests/test_twin_replay_resilience.py`
  - `tests/test_proxy_main_loop.py`

  **Acceptance Criteria**:
  - [ ] Fault scenarios mají deterministické pass/fail výsledky.

  **QA Scenarios**:
  - Scenario: cloud flap
    - Tool: Bash (pytest)
    - Expected Result: no oscillation beyond policy thresholds
    - Evidence: `.sisyphus/evidence/task-23-pass.txt`
  - Scenario: twin unavailable + cloud healthy
    - Tool: Bash (pytest)
    - Expected Result: transport uninterrupted
    - Evidence: `.sisyphus/evidence/task-23-error.txt`

- [ ] 24. Performance sanity guard

  **What to do**:
  - Ověřit, že thin transport nezhoršil latenci proti baseline.
  - Reportnout P50/P95 delta.

  **Must NOT do**:
  - Nedeklarovat improvement bez měření.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 5)
  - Blocks: Final wave
  - Blocked By: T16

  **References**:
  - baseline evidence from T1

  **Acceptance Criteria**:
  - [ ] P95 regress <= agreed tolerance.

  **QA Scenarios**:
  - Scenario: baseline vs refactor comparison
    - Tool: Bash
    - Expected Result: report with non-regression verdict
    - Evidence: `.sisyphus/evidence/task-24-pass.txt`
  - Scenario: regression breach detection
    - Tool: Bash
    - Expected Result: explicit FAIL when threshold exceeded
    - Evidence: `.sisyphus/evidence/task-24-error.txt`

- [ ] 25. Release candidate checklist + evidence collation

  **What to do**:
  - Sjednotit evidence, vyplnit RC checklist, připravit handoff.
  - Zkontrolovat, že každý task má evidence artefakty.

  **Must NOT do**:
  - Nepřeskočit chybějící evidence.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - Can Run In Parallel: YES (Wave 5)
  - Blocks: Final wave
  - Blocked By: T20, T21

  **References**:
  - `.sisyphus/evidence/`

  **Acceptance Criteria**:
  - [ ] RC checklist complete, no missing task evidence.

  **QA Scenarios**:
  - Scenario: evidence completeness
    - Tool: Bash
    - Expected Result: every task evidence path exists
    - Evidence: `.sisyphus/evidence/task-25-pass.txt`
  - Scenario: missing evidence detection
    - Tool: Bash
    - Expected Result: check fails when one artifact removed
    - Evidence: `.sisyphus/evidence/task-25-error.txt`

---

## Final Verification Wave (MANDATORY)

- [ ] F1. **Plan Compliance Audit** — `oracle`
  - Verify each Must Have and Must NOT Have directly against implemented files and evidence.
  - Output: `Must Have [N/N] | Must NOT Have [N/N] | VERDICT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  - Run lint/type/tests and scan for slop patterns (`as any`, ignored exceptions, dead code).
  - Output: `Build/Lint/Tests summary | VERDICT`

- [ ] F3. **Real Scenario QA Replay** — `unspecified-high`
  - Execute every scenario from every task and store evidence in `.sisyphus/evidence/final-qa/`.
  - Output: `Scenarios pass ratio | Integration verdict`

- [ ] F4. **Scope Fidelity Check** — `deep`
  - Ensure no scope creep and all removed coupling aligns with plan boundaries.
  - Output: `Compliant tasks / contamination / unaccounted changes | VERDICT`

---

## Commit Strategy

- Group commits per wave, semantic style (`refactor(proxy): ...`, `test(proxy): ...`, `chore(runbook): ...`)
- Each commit gated by wave-specific tests and targeted lint.

---

## Success Criteria

### Verification Commands
```bash
python3 -m pytest tests/ -q
python3 -m pylint addon/oig-proxy/proxy.py addon/oig-proxy/cloud_forwarder.py addon/oig-proxy/digital_twin.py
```

### Final Checklist
- [ ] Proxy critical path no longer carries business/fallback logic by default
- [ ] Telemetry publish contract unchanged
- [ ] Twin activation policy exactly matches selected thresholds
- [ ] Feature-flag rollback proven
- [ ] Final verification wave approved by all 4 reviewers
