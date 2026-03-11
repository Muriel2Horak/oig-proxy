# OIG Proxy: Single Runtime Artifact + Local CI Toolbox Plan

## TL;DR

> **Quick Summary**: Stabilizujeme local quality pipeline kolem `ci/ci.sh`, odstraníme duplicity a nejasnosti, a zavedeme Docker CI toolbox pro reprodukovatelné běhy bez host venv driftu.
>
> **Deliverables**:
> - Konsolidovaný local CI entrypoint bez duplicitních security běhů
> - Opravené a sjednocené dokumentační cesty
> - Docker-first CI toolbox (quality-only, žádný druhý runtime app kontejner)
> - Wrapper workflow pro bezpečný přechod z quality gate na NAS deploy
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: T2 → T4 → T8 → T12

---

## Context

### Original Request
Uživatel chce jasně nastavit development/quality workflow v OpenCode a lokálně tak, aby nebylo potřeba řešit „mraky věcí“ ve venv, lokální běh byl quality-only (test/lint/security/sonar), a runtime zůstal jediný: HA add-on nasazovaný na NAS.

### Interview Summary
**Key Discussions**:
- Lokální vývoj nemá spouštět HA server ani MQTT runtime.
- Uživatel chce quality checks lokálně a deployment přes existující skript.
- Nechce duplicitní runtime kontejner vedle HA runtime.
- Oficiální local CI entrypoint má být `ci/ci.sh`.
- Duplicitní security běhy mají být odstraněny.

**Research Findings**:
- Reálný entrypoint je `ci/ci.sh`; dokumentace místy odkazuje na neexistující `.github/scripts/ci.sh`.
- `ci/ci.sh` obsahuje inline security krok a zároveň další volání `run_security.sh`.
- `.github/scripts/run_security.sh` obsahuje interní duplicitní bloky.
- `deploy_to_haos.sh` je existující NAS/HA deployment cesta a zůstává source of truth.

### Metis Review
**Identified Gaps** (addressed in this plan):
- Přesně definované chování flagů (`--no-security`, `--sonar`) po cleanupu.
- Guardrail, že Docker CI toolbox je quality-only a nesmí být použit jako druhý runtime služby.
- Ověření konzistence report artefaktů po změnách.
- Ověření dokumentace proti skutečným cestám.

---

## Work Objectives

### Core Objective
Zajistit rychlý, reprodukovatelný a jednoznačný quality workflow pro solo operátora + OpenCode agenty, při zachování jediného runtime artifactu v HA add-on nasazení.

### Concrete Deliverables
- `ci/ci.sh` jako jediný oficiální local CI entrypoint.
- Odstranění duplicitních security běhů v local CI flow.
- Docker CI toolbox pro quality-only běhy (bez runtime duplikace aplikace).
- Sjednocená dokumentace a ověřitelné QA příkazy.

### Definition of Done
- [ ] `ci/ci.sh` proběhne bez duplicitních security sekcí a s korektními flagy.
- [ ] Dokumentace neodkazuje na neexistující `.github/scripts/ci.sh`.
- [ ] Docker CI toolbox spustí stejný quality flow a vygeneruje reporty do `reports/`.
- [ ] Deploy workflow zůstává přes `deploy_to_haos.sh` bez zavádění druhého runtime kontejneru.

### Must Have
- Single runtime artifact policy: běžící runtime pouze HA add-on.
- Local workflow quality-only (test/lint/security/mypy/sonar).
- Agent-executed QA scénáře pro každý task.

### Must NOT Have (Guardrails)
- Žádný druhý runtime kontejner OIG proxy pro lokální „produkční“ běh.
- Žádné změny, které nahradí nebo obejdou `deploy_to_haos.sh` jako deploy source of truth.
- Žádné scope creep úpravy do unrelated infra stacků (`core-platform`, `majestic-ai`).
- Žádné neověřené manuální acceptance kroky.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — všechna ověření provádí executing agent příkazy/toolingem.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: YES (Tests-after)
- **Framework**: pytest + coverage, pylint, mypy, security scanners, sonar script
- **TDD**: N/A pro shell/script refaktor (ověření skriptů přes command scénáře)

### QA Policy
Evidence ukládat do `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **CLI/Scripts**: `Bash` / `interactive_bash` pro spuštění skriptů a aserce výstupů/exit code.
- **File assertions**: `Bash` + grep/count checky na konkrétní patterns.
- **Sonar integration**: ověřit preflight a guardrails bez nutnosti spouštět externí server v každém scénáři.

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Start Immediately — CI correctness foundations):
├── Task 1: Canonical entrypoint & path cleanup map [quick]
├── Task 2: Remove duplicate security invocation in `ci/ci.sh` [quick]
├── Task 3: Remove duplicated blocks in `run_security.sh` [quick]
├── Task 4: Normalize CI flag semantics and flow control [quick]
├── Task 5: Fix CI summary/tips command paths [quick]
└── Task 6: Report artifact contract hardening [unspecified-low]

Wave 2 (After Wave 1 — Docker-first quality toolbox):
├── Task 7: Create quality-only CI toolbox image definition [unspecified-high]
├── Task 8: Add `ci/ci-docker.sh` wrapper [quick]
├── Task 9: UID/GID-safe report output handling [quick]
├── Task 10: Sonar-in-container path mapping guardrails [unspecified-low]
└── Task 11: Docs refresh for quality-only + single-runtime policy [writing]

Wave 3 (After Wave 2 — integration with deploy discipline):
├── Task 12: Add quality-gate-to-deploy wrapper flow [unspecified-low]
├── Task 13: Add non-invasive deploy preflight checks [quick]
├── Task 14: Add evidence packaging for CI/deploy runs [unspecified-low]
└── Task 15: Add regression command matrix for solo operator [writing]

Wave FINAL (After ALL tasks — independent review, 4 parallel):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA execution by agent (unspecified-high)
└── Task F4: Scope fidelity check (deep)

Critical Path: T2 → T4 → T8 → T12
Parallel Speedup: ~60% vs sequential
Max Concurrent: 6 (Wave 1)

### Dependency Matrix (ALL tasks)

- **T1**: Blocked By — None | Blocks — T4, T5, T11
- **T2**: Blocked By — None | Blocks — T4, T6
- **T3**: Blocked By — None | Blocks — T6
- **T4**: Blocked By — T1, T2 | Blocks — T8, T12
- **T5**: Blocked By — T1 | Blocks — T11, T15
- **T6**: Blocked By — T2, T3 | Blocks — T10, T14
- **T7**: Blocked By — T4 | Blocks — T8, T9, T10
- **T8**: Blocked By — T4, T7 | Blocks — T12, T15
- **T9**: Blocked By — T7 | Blocks — T14
- **T10**: Blocked By — T6, T7 | Blocks — T11, T15
- **T11**: Blocked By — T1, T5, T10 | Blocks — T15
- **T12**: Blocked By — T4, T8 | Blocks — T13, T14
- **T13**: Blocked By — T12 | Blocks — T15
- **T14**: Blocked By — T6, T9, T12 | Blocks — T15
- **T15**: Blocked By — T5, T8, T10, T11, T13, T14 | Blocks — FINAL wave

### Agent Dispatch Summary

- **Wave 1 (6 agents)**:
  - T1 quick, T2 quick, T3 quick, T4 quick, T5 quick, T6 unspecified-low
- **Wave 2 (5 agents)**:
  - T7 unspecified-high, T8 quick, T9 quick, T10 unspecified-low, T11 writing
- **Wave 3 (4 agents)**:
  - T12 unspecified-low, T13 quick, T14 unspecified-low, T15 writing
- **FINAL (4 agents)**:
  - F1 oracle, F2 unspecified-high, F3 unspecified-high, F4 deep

---

## TODOs

- [ ] 1. Canonical CI entrypoint inventory + reference correction map

  **What to do**:
  - Audit all references to local CI entrypoint across repo docs/scripts.
  - Produce exact replacement map: `.github/scripts/ci.sh` → `ci/ci.sh` where applicable.
  - Confirm no runtime/deploy path changes are included.

  **Must NOT do**:
  - Do not alter GitHub workflow semantics.
  - Do not modify deploy scripts.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: deterministic text/path cleanup inventory.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `writing`: not needed for mechanical path audit.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T2, T3, T6)
  - **Blocks**: T4, T5, T11
  - **Blocked By**: None

  **References**:
  - `ci/ci.sh` - canonical local CI script and current tip strings.
  - `docs/CI_CD_OVERVIEW.md` - known outdated references.
  - `CHANGELOG.md` - existing notes about local CI path.

  **Acceptance Criteria**:
  - [ ] Reference map file/checklist exists in evidence.
  - [ ] All identified stale entrypoint references listed with target replacement.

  **QA Scenarios**:
  ```
  Scenario: Entry-point discovery happy path
    Tool: Bash (grep)
    Preconditions: Repo checked out
    Steps:
      1. Search for "\.github/scripts/ci\.sh" in *.md/*.sh
      2. Capture file:line list
      3. Verify map includes each occurrence
    Expected Result: Complete list with replacement target ci/ci.sh
    Failure Indicators: Missing file from grep output not in map
    Evidence: .sisyphus/evidence/task-1-entrypoint-map.txt

  Scenario: Guardrail negative
    Tool: Bash (grep)
    Preconditions: Same
    Steps:
      1. Search deploy scripts for accidental edits plan scope
      2. Ensure no replacement map item touches deploy_to_haos.sh
    Expected Result: No deploy script listed in replacement map
    Evidence: .sisyphus/evidence/task-1-guardrail-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-1-entrypoint-map.txt
  - [ ] task-1-guardrail-error.txt

  **Commit**: NO

- [ ] 2. Remove duplicate security invocation from `ci/ci.sh`

  **What to do**:
  - Remove standalone call to `.github/scripts/run_security.sh` at end of `ci/ci.sh`.
  - Keep single security execution path controlled by `RUN_SECURITY`.

  **Must NOT do**:
  - Must not change behavior of test/lint/sonar sections.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: single-file surgical script edit.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `git-master`: not needed for code change itself.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T4, T6
  - **Blocked By**: None

  **References**:
  - `ci/ci.sh` - current flow and duplicate security call.
  - `.github/scripts/run_security.sh` - separate script kept callable directly.

  **Acceptance Criteria**:
  - [ ] `ci/ci.sh` contains no unconditional call to `run_security.sh`.
  - [ ] `--no-security` no longer triggers security scanners.

  **QA Scenarios**:
  ```
  Scenario: Single security execution
    Tool: Bash
    Preconditions: Script updated
    Steps:
      1. Run: bash -n ci/ci.sh
      2. Run: grep -c "run_security.sh" ci/ci.sh
      3. Run: ./ci/ci.sh --no-security
    Expected Result: grep count 0; --no-security run has no security section
    Failure Indicators: grep count >0 or security tool output appears
    Evidence: .sisyphus/evidence/task-2-single-security.txt

  Scenario: Negative regression check
    Tool: Bash
    Preconditions: Same
    Steps:
      1. Run: ./ci/ci.sh --no-tests --no-lint
      2. Assert script exits successfully if security-only path valid
    Expected Result: No syntax/flow break in flag handling
    Evidence: .sisyphus/evidence/task-2-regression-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-2-single-security.txt
  - [ ] task-2-regression-error.txt

  **Commit**: YES (groups with 3,4,5,6)

- [ ] 3. Remove internal duplicated blocks in `.github/scripts/run_security.sh`

  **What to do**:
  - Delete repeated second half block that reruns Bandit/Safety/Gitleaks/security tests.
  - Keep one coherent 1..N sequence and single summary section.

  **Must NOT do**:
  - Do not remove optional scanners (Semgrep/Trivy/Nikto) support.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: single script dedup cleanup.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `writing`: no narrative output needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T6
  - **Blocked By**: None

  **References**:
  - `.github/scripts/run_security.sh` - duplicated sections currently present.
  - `docs/SECURITY_TESTING.md` - expected script behavior.

  **Acceptance Criteria**:
  - [ ] Script runs each scanner/test section once.
  - [ ] Summary printed once.

  **QA Scenarios**:
  ```
  Scenario: Duplicate block removal happy path
    Tool: Bash
    Preconditions: Script updated
    Steps:
      1. Run: bash -n .github/scripts/run_security.sh
      2. Run: grep -c "Running Bandit" .github/scripts/run_security.sh
      3. Run: grep -c "SECURITY SCAN COMPLETE" .github/scripts/run_security.sh
    Expected Result: One logical block and one summary marker sequence
    Failure Indicators: Multiple duplicated sequences remain
    Evidence: .sisyphus/evidence/task-3-dedup.txt

  Scenario: Scanner optional-path negative
    Tool: Bash
    Preconditions: semgrep/trivy may be absent
    Steps:
      1. Run script in env without optional binaries
      2. Assert graceful skip messages instead of hard fail
    Expected Result: Script completes with warning/skip lines
    Evidence: .sisyphus/evidence/task-3-optional-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-3-dedup.txt
  - [ ] task-3-optional-error.txt

  **Commit**: YES (groups with 2,4,5,6)

- [ ] 4. Normalize CI flag semantics and flow control in `ci/ci.sh`

  **What to do**:
  - Ensure flags (`--no-tests`, `--no-security`, `--no-lint`, `--sonar`, `--all`) map deterministically to execution blocks.
  - Ensure `RUN_SECURITY=0` skips all security actions.
  - Keep exit-code discipline (hard fail where intended, soft fail where intended).

  **Must NOT do**:
  - Must not silently downgrade failing unit tests into warnings.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `ultrabrain`: logic is straightforward branch control.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T8, T12
  - **Blocked By**: T1, T2

  **References**:
  - `ci/ci.sh` flag parser and execution phases.
  - `docs/CI_CD_OVERVIEW.md` expected CLI usage.

  **Acceptance Criteria**:
  - [ ] Each flag combination produces expected skipped/executed sections.
  - [ ] `--no-security` suppresses all security scanners/tests.

  **QA Scenarios**:
  ```
  Scenario: Flag matrix happy path
    Tool: Bash
    Preconditions: ci/ci.sh updated
    Steps:
      1. Run: ./ci/ci.sh --no-security
      2. Run: ./ci/ci.sh --no-tests --no-lint
      3. Run: ./ci/ci.sh --all
      4. Check section headers in output
    Expected Result: Executed sections exactly match flags
    Failure Indicators: Security section appears with --no-security, or missing expected section
    Evidence: .sisyphus/evidence/task-4-flag-matrix.txt

  Scenario: Error handling negative case
    Tool: Bash
    Preconditions: same
    Steps:
      1. Run: ./ci/ci.sh --unknown-flag
      2. Capture exit code
    Expected Result: Usage printed and exit code non-zero
    Evidence: .sisyphus/evidence/task-4-unknown-flag-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-4-flag-matrix.txt
  - [ ] task-4-unknown-flag-error.txt

  **Commit**: YES (groups with 2,3,5,6)

- [ ] 5. Fix in-script usage/tips paths in `ci/ci.sh`

  **What to do**:
  - Replace stale `.github/scripts/ci.sh` tips with `./ci/ci.sh`.
  - Keep option examples aligned with actual parser flags.

  **Must NOT do**:
  - Do not add new flags not implemented in parser.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `writing`: this is precise script text maintenance.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T11, T15
  - **Blocked By**: T1

  **References**:
  - `ci/ci.sh` summary tips section near end.
  - `docs/CI_CD_OVERVIEW.md` for consistent examples.

  **Acceptance Criteria**:
  - [ ] Script tips reference only existing path/flags.

  **QA Scenarios**:
  ```
  Scenario: Tips consistency happy path
    Tool: Bash
    Preconditions: Script updated
    Steps:
      1. grep -n "\.github/scripts/ci\.sh" ci/ci.sh
      2. grep -n "\./ci/ci\.sh" ci/ci.sh
    Expected Result: First grep no matches, second grep >= 1
    Failure Indicators: Stale path remains
    Evidence: .sisyphus/evidence/task-5-tips-consistency.txt

  Scenario: Usage parse negative
    Tool: Bash
    Preconditions: same
    Steps:
      1. Run ./ci/ci.sh --help-equivalent invalid flag
      2. Validate usage line mentions actual supported flags
    Expected Result: Usage text aligns with parser
    Evidence: .sisyphus/evidence/task-5-usage-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-5-tips-consistency.txt
  - [ ] task-5-usage-error.txt

  **Commit**: YES (groups with 2,3,4,6)

- [ ] 6. Harden report artifact contract (`reports/`)

  **What to do**:
  - Ensure report outputs are consistently named and produced once per run.
  - Document mandatory vs optional report files according to enabled flags/tools.

  **Must NOT do**:
  - Must not force optional scanners as hard dependencies.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: no heavy architecture needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: T10, T14
  - **Blocked By**: T2, T3

  **References**:
  - `ci/ci.sh` summary report section.
  - `.github/scripts/run_tests.sh` generated files.
  - `.github/scripts/run_sonar.sh` preconditions on coverage/bandit artifacts.

  **Acceptance Criteria**:
  - [ ] Required reports exist when corresponding phases enabled.
  - [ ] No duplicated overwrite ambiguity from redundant executions.

  **QA Scenarios**:
  ```
  Scenario: Report contract happy path
    Tool: Bash
    Preconditions: Clean reports dir
    Steps:
      1. rm -rf reports && mkdir -p reports
      2. Run ./ci/ci.sh
      3. Assert junit.xml and coverage.xml exist
      4. Assert bandit.json exists when security enabled
    Expected Result: Expected report set created once
    Failure Indicators: Missing required files or conflicting duplicates
    Evidence: .sisyphus/evidence/task-6-report-contract.txt

  Scenario: Optional-tools negative path
    Tool: Bash
    Preconditions: Missing semgrep/trivy/gitleaks binaries
    Steps:
      1. Run ./ci/ci.sh
      2. Assert script continues with warning lines
    Expected Result: Optional tools skipped gracefully
    Evidence: .sisyphus/evidence/task-6-optional-tools-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-6-report-contract.txt
  - [ ] task-6-optional-tools-error.txt

  **Commit**: YES (groups with 2,3,4,5)

- [ ] 7. Create quality-only CI toolbox image definition

  **What to do**:
  - Define Docker image/stage containing all local CI tools (python, pip deps, scanners as agreed).
  - Ensure image is explicitly marked for quality checks only (not runtime app container).
  - Keep alignment with existing script expectations (`PYTHON_BIN`, report paths).

  **Must NOT do**:
  - Do not create a runtime OIG service in compose from this image.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: container/toolchain design plus reproducibility concerns.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `visual-engineering`: non-UI work.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: T8, T9, T10
  - **Blocked By**: T4

  **References**:
  - `addon/oig-proxy/Dockerfile` - existing runtime image baseline (for awareness only).
  - `ci/ci.sh` and `.github/scripts/run_*` - tool/runtime expectations.
  - `.sisyphus/drafts/nas-autonomni-provoz-oig-proxy.md` decision: single runtime artifact.

  **Acceptance Criteria**:
  - [ ] CI toolbox image builds successfully.
  - [ ] Image can execute `ci/ci.sh` in mounted workspace.
  - [ ] No app runtime port/service is exposed by toolbox definition.

  **QA Scenarios**:
  ```
  Scenario: CI toolbox build happy path
    Tool: Bash (docker)
    Preconditions: Docker daemon running
    Steps:
      1. Build toolbox image from defined file
      2. Run image with mounted repo and execute ./ci/ci.sh --no-security
      3. Verify reports generated in host reports/
    Expected Result: Build + command success without runtime service start
    Failure Indicators: Build fails, script cannot run, reports missing
    Evidence: .sisyphus/evidence/task-7-toolbox-build.txt

  Scenario: Runtime-duplication negative check
    Tool: Bash (grep/docker inspect)
    Preconditions: toolbox definition present
    Steps:
      1. Inspect definition for exposed runtime app port patterns
      2. Ensure no oig runtime service declaration introduced
    Expected Result: Toolbox is quality-only
    Evidence: .sisyphus/evidence/task-7-runtime-duplication-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-7-toolbox-build.txt
  - [ ] task-7-runtime-duplication-error.txt

  **Commit**: YES (groups with 8,9,10,11)

- [ ] 8. Add `ci/ci-docker.sh` wrapper for containerized local CI

  **What to do**:
  - Add wrapper script that runs `ci/ci.sh` inside CI toolbox image.
  - Pass through flags and env vars (`RUN_*`, `REPORT_DIR`, `PYTHON_BIN` policy).
  - Keep host-side UX minimal for solo operator.

  **Must NOT do**:
  - Must not change semantics of `ci/ci.sh`; wrapper is transport layer only.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: limited shell wrapper complexity.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T9, T10, T11 after T7)
  - **Blocks**: T12, T15
  - **Blocked By**: T4, T7

  **References**:
  - `ci/ci.sh` CLI contract.
  - `.github/scripts/run_sonar.sh` container constraints.

  **Acceptance Criteria**:
  - [ ] `ci/ci-docker.sh --no-security` executes successfully.
  - [ ] `ci/ci-docker.sh` forwards flags identically to `ci/ci.sh`.

  **QA Scenarios**:
  ```
  Scenario: Wrapper parity happy path
    Tool: Bash (docker)
    Preconditions: T7 complete
    Steps:
      1. Run ./ci/ci.sh --no-security and capture section list
      2. Run ./ci/ci-docker.sh --no-security and capture section list
      3. Compare outputs for equivalent phase behavior
    Expected Result: Phase parity between native and docker wrapper
    Failure Indicators: Missing/extra phases in wrapper run
    Evidence: .sisyphus/evidence/task-8-wrapper-parity.txt

  Scenario: Flag passthrough negative case
    Tool: Bash
    Preconditions: same
    Steps:
      1. Run ./ci/ci-docker.sh --unknown-flag
      2. Assert usage/error from underlying script is preserved
    Expected Result: Non-zero exit and clear unknown option message
    Evidence: .sisyphus/evidence/task-8-flag-passthrough-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-8-wrapper-parity.txt
  - [ ] task-8-flag-passthrough-error.txt

  **Commit**: YES (groups with 7,9,10,11)

- [ ] 9. Enforce UID/GID-safe report ownership for docker CI runs

  **What to do**:
  - Ensure files generated in `reports/` are writable/readable by host user.
  - Add docker run/user mapping strategy for Linux hosts.

  **Must NOT do**:
  - Do not rely on manual chmod post-steps as acceptance requirement.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `ultrabrain`: straightforward runtime arg handling.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T14
  - **Blocked By**: T7

  **References**:
  - `reports/` artifact expectations in existing scripts.
  - Docker run wrapper from T8.

  **Acceptance Criteria**:
  - [ ] After docker CI run, report files are not root-locked for host user.

  **QA Scenarios**:
  ```
  Scenario: Ownership happy path
    Tool: Bash
    Preconditions: T8 wrapper implemented
    Steps:
      1. Clear reports/
      2. Run ./ci/ci-docker.sh --no-security
      3. Check file ownership/permissions for reports/*
    Expected Result: Host user can modify/delete generated reports
    Failure Indicators: Permission denied on report cleanup/update
    Evidence: .sisyphus/evidence/task-9-ownership.txt

  Scenario: Negative permission check
    Tool: Bash
    Preconditions: same
    Steps:
      1. Attempt overwrite of one generated report file
      2. Capture success/failure
    Expected Result: Overwrite succeeds without sudo
    Evidence: .sisyphus/evidence/task-9-permission-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-9-ownership.txt
  - [ ] task-9-permission-error.txt

  **Commit**: YES (groups with 7,8,10,11)

- [ ] 10. Add Sonar path-mapping guardrails for containerized runs

  **What to do**:
  - Validate/adjust path normalization so coverage/bandit paths map correctly in scanner container.
  - Keep Linux-host compatibility notes (host.docker.internal/network behavior).

  **Must NOT do**:
  - Must not hardcode environment assumptions that break non-macOS hosts.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: bounded script/path logic.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T11, T15
  - **Blocked By**: T6, T7

  **References**:
  - `.github/scripts/run_sonar.sh` normalize_report_paths function.
  - `reports/coverage.xml`, `reports/bandit.json` expected locations.

  **Acceptance Criteria**:
  - [ ] Sonar preflight passes with expected report paths.
  - [ ] Path normalization does not mutate unrelated content.

  **QA Scenarios**:
  ```
  Scenario: Sonar mapping happy path
    Tool: Bash
    Preconditions: reports generated
    Steps:
      1. Run sonar script in dry/preflight-compatible mode with env vars
      2. Verify normalized paths reference container project root
    Expected Result: scanner args and report path mapping consistent
    Failure Indicators: missing report errors despite files present
    Evidence: .sisyphus/evidence/task-10-sonar-mapping.txt

  Scenario: Host resolution negative
    Tool: Bash
    Preconditions: Linux host or mocked host config
    Steps:
      1. Run sonar with localhost URL and inspect transformed URL
      2. Validate fallback guidance on failure
    Expected Result: clear failure mode and documented override path
    Evidence: .sisyphus/evidence/task-10-host-resolution-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-10-sonar-mapping.txt
  - [ ] task-10-host-resolution-error.txt

  **Commit**: YES (groups with 7,8,9,11)

- [ ] 11. Refresh docs for quality-only workflow and single-runtime policy

  **What to do**:
  - Update docs to reflect canonical `ci/ci.sh`, optional `ci/ci-docker.sh`, and policy that HA add-on is sole runtime.
  - Clarify local quality-only nature (no HA/MQTT runtime required locally).

  **Must NOT do**:
  - Do not promise unsupported commands/paths.

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `quick`: documentation cohesion benefits from writing focus.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: T15
  - **Blocked By**: T1, T5, T10

  **References**:
  - `README.md` CI and deployment sections.
  - `docs/CI_CD_OVERVIEW.md` local CI command examples.
  - `DEPLOYMENT.md` deploy context.

  **Acceptance Criteria**:
  - [ ] Docs contain no stale `.github/scripts/ci.sh` instructions.
  - [ ] Docs explicitly state "single runtime artifact = HA add-on".

  **QA Scenarios**:
  ```
  Scenario: Documentation coherence happy path
    Tool: Bash (grep)
    Preconditions: docs updated
    Steps:
      1. grep for stale path references
      2. grep for single-runtime policy statement
      3. grep for ci/ci.sh and optional ci/ci-docker.sh mentions
    Expected Result: consistent command references and policy language
    Failure Indicators: conflicting instructions across docs
    Evidence: .sisyphus/evidence/task-11-doc-coherence.txt

  Scenario: Negative contradiction scan
    Tool: Bash (grep)
    Preconditions: same
    Steps:
      1. Search for text implying local runtime app container is required
      2. Validate none remains
    Expected Result: zero contradictions with single-runtime policy
    Evidence: .sisyphus/evidence/task-11-contradiction-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-11-doc-coherence.txt
  - [ ] task-11-contradiction-error.txt

  **Commit**: YES (groups with 7,8,9,10)

- [ ] 12. Add quality-gate-to-deploy wrapper flow

  **What to do**:
  - Add wrapper command/script that enforces sequence: quality pass → deploy invocation.
  - Wrapper must call existing `deploy_to_haos.sh` (not replace it).
  - Add clear abort behavior when quality fails.

  **Must NOT do**:
  - Must not embed deployment logic currently maintained in `deploy_to_haos.sh`.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: orchestration is linear and bounded.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: T13, T14
  - **Blocked By**: T4, T8

  **References**:
  - `deploy_to_haos.sh` existing deployment entrypoint.
  - `ci/ci.sh` and `ci/ci-docker.sh` quality entrypoints.

  **Acceptance Criteria**:
  - [ ] Deploy is never invoked if quality step exits non-zero.
  - [ ] On success, wrapper invokes `deploy_to_haos.sh` exactly once.

  **QA Scenarios**:
  ```
  Scenario: Quality-pass deploy happy path
    Tool: Bash
    Preconditions: quality command mocked/passes
    Steps:
      1. Run wrapper with passing quality mode
      2. Capture command trace/log
      3. Assert deploy script invocation exists once
    Expected Result: orderly quality→deploy flow
    Failure Indicators: deploy called multiple times or before quality completion
    Evidence: .sisyphus/evidence/task-12-pass-deploy.txt

  Scenario: Quality-fail safety stop
    Tool: Bash
    Preconditions: force quality failure
    Steps:
      1. Run wrapper with failing quality input
      2. Assert non-zero exit
      3. Assert deploy script was not invoked
    Expected Result: safe abort
    Evidence: .sisyphus/evidence/task-12-fail-stop-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-12-pass-deploy.txt
  - [ ] task-12-fail-stop-error.txt

  **Commit**: YES (groups with 13,14,15)

- [ ] 13. Add non-invasive deploy preflight checks

  **What to do**:
  - Add preflight checks before deploy invocation (SSH alias availability, script exists/executable, required env sanity).
  - Keep checks read-only and fail-fast.

  **Must NOT do**:
  - Must not alter HA host state during preflight.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `unspecified-high`: complexity is low.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: T15
  - **Blocked By**: T12

  **References**:
  - `deploy_to_haos.sh` prerequisites (`HA_HOST`, ssh alias assumptions).
  - `validate_production.sh` connectivity checks pattern.

  **Acceptance Criteria**:
  - [ ] Preflight fails with explicit message on missing SSH alias/execute bit.
  - [ ] Preflight passes cleanly in valid setup.

  **QA Scenarios**:
  ```
  Scenario: Preflight happy path
    Tool: Bash
    Preconditions: valid local setup
    Steps:
      1. Run preflight command
      2. Validate all checks PASS
    Expected Result: clear PASS summary and zero exit
    Failure Indicators: false negatives on valid setup
    Evidence: .sisyphus/evidence/task-13-preflight-pass.txt

  Scenario: Missing SSH alias negative
    Tool: Bash
    Preconditions: simulate missing alias name
    Steps:
      1. Run preflight with invalid alias env override
      2. Capture output and exit code
    Expected Result: non-zero exit with actionable message
    Evidence: .sisyphus/evidence/task-13-preflight-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-13-preflight-pass.txt
  - [ ] task-13-preflight-error.txt

  **Commit**: YES (groups with 12,14,15)

- [ ] 14. Add evidence packaging for CI + deploy runs

  **What to do**:
  - Define evidence bundle layout for quality and deploy orchestration outputs.
  - Ensure wrapper flow stores logs/artifacts under deterministic evidence paths.

  **Must NOT do**:
  - Do not require manual copy/paste as acceptance mechanism.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `writing`: this is primarily automation output structuring.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: T15
  - **Blocked By**: T6, T9, T12

  **References**:
  - `.sisyphus/evidence/` existing conventions.
  - `reports/` outputs from CI scripts.

  **Acceptance Criteria**:
  - [ ] Evidence bundle contains command logs + key report pointers.
  - [ ] Naming scheme follows task-based convention.

  **QA Scenarios**:
  ```
  Scenario: Evidence pack happy path
    Tool: Bash
    Preconditions: prior tasks completed
    Steps:
      1. Run quality flow then wrapper deploy flow (mock/no-op deploy acceptable)
      2. Verify evidence files created in expected structure
    Expected Result: deterministic artifact set
    Failure Indicators: missing logs, ambiguous naming, overwritten artifacts
    Evidence: .sisyphus/evidence/task-14-evidence-pack.txt

  Scenario: Collision negative case
    Tool: Bash
    Preconditions: run flow twice
    Steps:
      1. Execute two consecutive runs
      2. Check prior evidence not silently overwritten
    Expected Result: versioned or timestamp-safe storage
    Evidence: .sisyphus/evidence/task-14-collision-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-14-evidence-pack.txt
  - [ ] task-14-collision-error.txt

  **Commit**: YES (groups with 12,13,15)

- [ ] 15. Publish regression command matrix for solo operator + agents

  **What to do**:
  - Create compact command matrix for daily use (native CI, docker CI, quality+deploy wrapper, failure triage).
  - Include exact expected outputs/exit behavior for each command.

  **Must NOT do**:
  - Must not include commands that are not actually implemented.

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `quick`: requires precise operator-facing clarity.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (final integrator)
  - **Blocks**: FINAL wave
  - **Blocked By**: T5, T8, T10, T11, T13, T14

  **References**:
  - `ci/ci.sh`, `ci/ci-docker.sh`, wrapper from T12.
  - `README.md` / `docs/CI_CD_OVERVIEW.md` updated instructions.

  **Acceptance Criteria**:
  - [ ] Matrix covers success path and top 3 failure scenarios.
  - [ ] Commands are copy-paste runnable from repo root.

  **QA Scenarios**:
  ```
  Scenario: Command matrix happy path
    Tool: Bash
    Preconditions: matrix drafted
    Steps:
      1. Execute each listed command in order
      2. Compare observed vs documented expected outputs
    Expected Result: documented behavior matches real behavior
    Failure Indicators: any command mismatch or missing prerequisite note
    Evidence: .sisyphus/evidence/task-15-matrix-verify.txt

  Scenario: Failure-triage negative path
    Tool: Bash
    Preconditions: induce one known failure (e.g., invalid flag)
    Steps:
      1. Execute failure case command from matrix
      2. Validate troubleshooting note points to correct fix
    Expected Result: actionable and correct triage guidance
    Evidence: .sisyphus/evidence/task-15-triage-error.txt
  ```

  **Evidence to Capture:**
  - [ ] task-15-matrix-verify.txt
  - [ ] task-15-triage-error.txt

  **Commit**: YES (groups with 12,13,14)

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  - Ověří Must Have/Must NOT Have proti diffu, skriptům a evidence souborům.
  - Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  - Spustí lint/test/type/security relevantní pro změněné části a vyhodnotí anti-patterny.
  - Output: `Build/Lint/Tests/Security | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  - Agent vykoná všechny task QA scénáře, uloží důkazy do `.sisyphus/evidence/final-qa/`.
  - Output: `Scenarios [N/N] | Integration [N/N] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  - Ověří 1:1 shodu implementace s plánem, bez scope creep.
  - Output: `Tasks compliant [N/N] | Unaccounted changes [N] | VERDICT`

---

## Commit Strategy

- **Commit 1 (Wave 1)**: `fix(ci): normalize local entrypoint and remove duplicate security runs`
- **Commit 2 (Wave 2)**: `feat(ci): add docker quality toolbox workflow`
- **Commit 3 (Wave 3)**: `chore(ci): wire quality gate to deploy wrappers and evidence flow`

---

## Success Criteria

### Verification Commands
```bash
./ci/ci.sh --no-security
./ci/ci.sh
./ci/ci.sh --sonar
./ci/ci-docker.sh --no-security
./ci/ci-docker.sh
```

### Final Checklist
- [ ] `ci/ci.sh` is canonical and documented
- [ ] No duplicate security execution in local CI flow
- [ ] Docker toolbox is quality-only and does not create runtime duplication
- [ ] `deploy_to_haos.sh` remains deployment source of truth
- [ ] Evidence artifacts exist for each task scenario
