# GPT-5.4-Only Config Correction and Validation Plan

## TL;DR

> **Quick Summary**: Correct `~/.config/opencode/oh-my-opencode.json` so only the intended GPT-5.4 replacement remains, revert unintended GPT-5.3-codex model moves, and validate against OMO schema/docs with zero scope creep.
>
> **Deliverables**:
> - Deterministic config diff (actual -> planned) for target keys only
> - Restored model mappings for `momus`, `oracle`, `ultrabrain`, `deep`
> - Compatibility cleanup for `explore.reasoningEffort` (remove for Kimi)
> - Validation evidence bundle under `.sisyphus/evidence/gpt54-only-fix/`
>
> **Estimated Effort**: Quick
> **Parallel Execution**: YES - 2 waves + Final Verification Wave
> **Critical Path**: T1 -> T6 -> T9 -> F1/F2/F3/F4

---

## Context

### Original Request
User requested replacing only GPT-5.4 usage and explicitly did **not** want broader GPT-5.3-codex remapping changes.

### Interview Summary
**Key Discussions**:
- Scope locked to GPT-5.4 intent: preserve minimal change strategy.
- Current config drift includes unintended Kimi assignments on GPT-5.3-codex targets.
- User requested explicit validation against definitions/documentation.

**Research Findings**:
- OMO docs define `reasoningEffort` for GPT-style models; non-GPT providers may ignore it.
- Current config shows `explore` on `nvidia/moonshotai/kimi-k2.5` with `reasoningEffort` present.
- Current config shows `momus`, `oracle`, `ultrabrain`, `deep` on Kimi though expected GPT-5.3-codex.

### Metis Review
**Identified Gaps (addressed in this plan)**:
- Need explicit no-scope-creep guardrail for non-target sections.
- Need model/parameter compatibility check (`reasoningEffort` on Kimi).
- Need command-verifiable acceptance criteria and evidence paths.

---

## Work Objectives

### Core Objective
Return configuration to GPT-5.4-only intended change while restoring unintended GPT-5.3-codex mappings and proving conformance via reproducible validation commands.

### Concrete Deliverables
- Updated `~/.config/opencode/oh-my-opencode.json` with target keys corrected only.
- Evidence artifacts in `.sisyphus/evidence/gpt54-only-fix/`.
- Validation output proving JSON validity + schema/doc compatibility checks.

### Definition of Done
- [ ] `agents.explore.model == nvidia/moonshotai/kimi-k2.5`
- [ ] `agents.momus.model == github-copilot/gpt-5.3-codex`
- [ ] `agents.oracle.model == github-copilot/gpt-5.3-codex`
- [ ] `categories.ultrabrain.model == github-copilot/gpt-5.3-codex`
- [ ] `categories.deep.model == github-copilot/gpt-5.3-codex`
- [ ] `agents.explore.reasoningEffort` absent/null
- [ ] No non-target sections modified

### Must Have
- Exact target-key diff before/after.
- Deterministic validation outputs (commands + evidence files).
- Schema/docs compatibility review documented in evidence.

### Must NOT Have (Guardrails)
- No changes to any other `agents.*` keys not listed above.
- No changes to categories other than `ultrabrain` and `deep`.
- No changes to `background_task`, hooks, notification, experimental, tmux, lsp, sonarqube.
- No manual-only verification.

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (CLI + jq/json tooling)
- **Automated tests**: Tests-after (config validation commands)
- **Framework**: Bash + jq + schema validator (ajv if available)

### QA Policy
Every task must emit evidence to:
`.sisyphus/evidence/gpt54-only-fix/task-{N}-{scenario-slug}.{ext}`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — baseline, scope lock, validation setup):
├── T1: Capture current target-key snapshot + full file checksum [quick]
├── T2: Generate explicit target diff specification (actual -> planned) [quick]
├── T3: Fetch/record OMO docs/schema references for compatibility checks [quick]
├── T4: Prepare evidence directory + output naming manifest [quick]
└── T5: Define immutable non-target key list for scope audit [quick]

Wave 2 (After Wave 1 — apply correction plan + validate):
├── T6: Revert model keys for momus/oracle/ultrabrain/deep to GPT-5.3-codex [quick]
├── T7: Enforce GPT-5.4-only intent on explore + remove incompatible reasoningEffort [quick]
├── T8: Run JSON + schema/doc compatibility validation suite [quick]
└── T9: Run non-target drift audit + publish final diff/evidence [quick]

Wave FINAL (After all implementation tasks — independent review in parallel):
├── F1: Plan compliance audit (oracle)
├── F2: Config quality + syntax audit (unspecified-high)
├── F3: End-to-end verification replay of all QA scenarios (unspecified-high)
└── F4: Scope fidelity diff audit (deep)
```

### Dependency Matrix
- **T1**: — -> T6, T9
- **T2**: — -> T6, T7
- **T3**: — -> T8
- **T4**: — -> T8, T9
- **T5**: — -> T9
- **T6**: T1, T2 -> T8, T9
- **T7**: T2 -> T8, T9
- **T8**: T3, T4, T6, T7 -> F1-F4
- **T9**: T1, T4, T5, T6, T7 -> F1-F4

### Agent Dispatch Summary
- **Wave 1**: T1-T5 -> `quick`
- **Wave 2**: T6-T9 -> `quick`
- **FINAL**: F1 -> `oracle`, F2/F3 -> `unspecified-high`, F4 -> `deep`

---

## TODOs

---

- [ ] 1. Capture baseline target-key snapshot and checksum

  **What to do**:
  - Record current values of:
    - `agents.explore.model`
    - `agents.explore.reasoningEffort`
    - `agents.momus.model`
    - `agents.oracle.model`
    - `categories.ultrabrain.model`
    - `categories.deep.model`
  - Record full-file checksum and save baseline evidence.

  **Must NOT do**:
  - Do not edit config in this task.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: deterministic read-only extraction and evidence capture.
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `git-master`: not needed (non-git user config operation).

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2-5)
  - **Blocks**: 6, 9
  - **Blocked By**: None

  **References**:
  - `~/.config/opencode/oh-my-opencode.json` - primary target config.
  - `https://ohmyopencode.org/docs` - schema/options reference.

  **Acceptance Criteria**:
  - [ ] Evidence file exists: `.sisyphus/evidence/gpt54-only-fix/task-1-baseline.json`
  - [ ] Evidence includes all six target keys + checksum.

  **QA Scenarios**:
  ```
  Scenario: Happy path baseline capture
    Tool: Bash (jq)
    Preconditions: Config file exists at ~/.config/opencode/oh-my-opencode.json
    Steps:
      1. Run jq query extracting six target keys.
      2. Run checksum command on config file.
      3. Save output to task-1-baseline.json.
    Expected Result: JSON file with keys + checksum is created.
    Failure Indicators: Missing key, jq parse error, missing evidence file.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-1-baseline.json

  Scenario: Error path missing file
    Tool: Bash
    Preconditions: Simulate wrong path in command input.
    Steps:
      1. Run jq against nonexistent file path.
      2. Assert non-zero exit and captured error text.
    Expected Result: Graceful failure with "No such file" message.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-1-missing-file-error.txt
  ```

- [ ] 2. Create explicit actual-vs-planned diff spec (target keys only)

  **What to do**:
  - Build a compact mapping table for only target keys.
  - Include exact planned values and explicit null/removal for `explore.reasoningEffort`.

  **Must NOT do**:
  - Do not include any non-target keys.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: structured formatting task.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1,3,4,5)
  - **Blocks**: 6, 7
  - **Blocked By**: None

  **References**:
  - `.sisyphus/drafts/omo_analysis.md` - user-agreed scope context.
  - `~/.config/opencode/oh-my-opencode.json` - actual state.

  **Acceptance Criteria**:
  - [ ] Evidence file exists: `.sisyphus/evidence/gpt54-only-fix/task-2-diff-spec.md`
  - [ ] Contains exactly six rows for six target keys.

  **QA Scenarios**:
  ```
  Scenario: Happy path diff-spec creation
    Tool: Bash
    Preconditions: Baseline values available from Task 1.
    Steps:
      1. Produce markdown table with target keys only.
      2. Validate row count = 6.
    Expected Result: Diff spec file with no extra keys.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-2-diff-spec.md

  Scenario: Negative path scope creep detection
    Tool: Bash
    Preconditions: Introduce an extra key row intentionally.
    Steps:
      1. Run checker that compares allowed key list.
      2. Assert checker flags extra key.
    Expected Result: Validation fails with explicit extra-key message.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-2-scope-creep-error.txt
  ```

- [ ] 3. Collect authoritative docs/schema references for compatibility checks

  **What to do**:
  - Capture references proving model config fields and variant/effort semantics.
  - Record rationale for removing `explore.reasoningEffort` on non-GPT target.

  **Must NOT do**:
  - Do not infer unsupported claims without citation.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: lightweight docs extraction.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 8
  - **Blocked By**: None

  **References**:
  - `https://ohmyopencode.org/docs`
  - `https://opencode.ai/docs/models/`

  **Acceptance Criteria**:
  - [ ] Evidence file exists: `.sisyphus/evidence/gpt54-only-fix/task-3-doc-citations.md`
  - [ ] Each claim in Task 8 links to at least one citation.

  **QA Scenarios**:
  ```
  Scenario: Happy path citation capture
    Tool: webfetch
    Preconditions: URLs reachable.
    Steps:
      1. Fetch docs pages.
      2. Extract relevant snippets and URLs.
      3. Save citation file.
    Expected Result: Citation file with source URLs and quoted snippets.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-3-doc-citations.md

  Scenario: Failure path unavailable source
    Tool: webfetch
    Preconditions: Use one invalid URL.
    Steps:
      1. Attempt fetch.
      2. Record fetch failure and fallback source used.
    Expected Result: Error captured, fallback documented.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-3-source-error.txt
  ```

- [ ] 4. Prepare evidence directory and manifest

  **What to do**:
  - Create `.sisyphus/evidence/gpt54-only-fix/` and manifest listing all expected evidence files.

  **Must NOT do**:
  - Do not write evidence outside this directory.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: filesystem scaffolding only.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 8, 9
  - **Blocked By**: None

  **References**:
  - Plan evidence policy section.

  **Acceptance Criteria**:
  - [ ] Manifest exists and lists Task 1-9 + Final wave evidence targets.

  **QA Scenarios**:
  ```
  Scenario: Happy path evidence scaffold
    Tool: Bash
    Preconditions: .sisyphus/evidence exists.
    Steps:
      1. Create target subdirectory.
      2. Write manifest file.
      3. Verify paths are writable.
    Expected Result: Directory and manifest present.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-4-manifest.md

  Scenario: Failure path wrong directory
    Tool: Bash
    Preconditions: Attempt write to forbidden path.
    Steps:
      1. Attempt write outside .sisyphus/evidence.
      2. Capture refusal/error.
    Expected Result: Operation blocked/failed, error logged.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-4-path-error.txt
  ```

- [ ] 5. Build immutable non-target key audit list

  **What to do**:
  - Enumerate top-level and nested non-target keys that must remain unchanged.
  - Produce machine-checkable allowlist for diff audit.

  **Must NOT do**:
  - Do not include target keys in immutable set.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: small static rule definition.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 9
  - **Blocked By**: None

  **References**:
  - `~/.config/opencode/oh-my-opencode.json` structure.

  **Acceptance Criteria**:
  - [ ] Allowlist file exists and excludes all six target keys.

  **QA Scenarios**:
  ```
  Scenario: Happy path allowlist generation
    Tool: Bash
    Preconditions: Config structure readable.
    Steps:
      1. Enumerate keys.
      2. Remove target keys.
      3. Save allowlist.
    Expected Result: allowlist generated and non-empty.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-5-immutable-allowlist.txt

  Scenario: Failure path target accidentally included
    Tool: Bash
    Preconditions: Intentionally include target key in allowlist.
    Steps:
      1. Run allowlist validator.
      2. Assert validator fails with key name.
    Expected Result: Failure flags forbidden inclusion.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-5-allowlist-error.txt
  ```

- [ ] 6. Revert unintended model mappings (momus/oracle/ultrabrain/deep)

  **What to do**:
  - Set:
    - `agents.momus.model = github-copilot/gpt-5.3-codex`
    - `agents.oracle.model = github-copilot/gpt-5.3-codex`
    - `categories.ultrabrain.model = github-copilot/gpt-5.3-codex`
    - `categories.deep.model = github-copilot/gpt-5.3-codex`

  **Must NOT do**:
  - Do not change any other model key.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: deterministic config key edits.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (with dependency on Wave 1)
  - **Blocks**: 8, 9
  - **Blocked By**: 1, 2

  **References**:
  - Task 2 diff spec.
  - `~/.config/opencode/oh-my-opencode.json` target paths.

  **Acceptance Criteria**:
  - [ ] All four target model paths match planned values exactly.

  **QA Scenarios**:
  ```
  Scenario: Happy path model reversion
    Tool: Bash (jq)
    Preconditions: Task 2 planned values approved.
    Steps:
      1. Apply four model path updates.
      2. Query each path with jq.
      3. Compare outputs to expected literals.
    Expected Result: All four values exactly match GPT-5.3-codex.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-6-model-revert.json

  Scenario: Failure path typo in model id
    Tool: Bash (jq)
    Preconditions: Introduce typo in one model value in test run.
    Steps:
      1. Run literal-value assertion checker.
      2. Capture failing key/value pair.
    Expected Result: Checker fails and reports exact mismatch.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-6-model-revert-error.txt
  ```

- [ ] 7. Enforce GPT-5.4-only intent on explore and remove incompatible reasoningEffort

  **What to do**:
  - Ensure `agents.explore.model = nvidia/moonshotai/kimi-k2.5`.
  - Remove `agents.explore.reasoningEffort` key.

  **Must NOT do**:
  - Do not modify any other explore keys.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: surgical edit on single object.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: 8, 9
  - **Blocked By**: 2

  **References**:
  - OMO docs for model options and compatibility notes.

  **Acceptance Criteria**:
  - [ ] `agents.explore.model` equals Kimi target.
  - [ ] `agents.explore.reasoningEffort` is absent/null.

  **QA Scenarios**:
  ```
  Scenario: Happy path explore normalization
    Tool: Bash (jq)
    Preconditions: Config editable.
    Steps:
      1. Set explore.model to Kimi value.
      2. Remove explore.reasoningEffort key.
      3. Query both paths for assertion.
    Expected Result: Model matches; reasoningEffort returns null.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-7-explore-normalized.json

  Scenario: Failure path stale reasoningEffort persists
    Tool: Bash (jq)
    Preconditions: Keep reasoningEffort intentionally.
    Steps:
      1. Run null assertion on reasoningEffort.
      2. Capture failed assertion.
    Expected Result: Assertion fails with non-null value.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-7-explore-error.txt
  ```

- [ ] 8. Run JSON + schema/docs compatibility validation suite

  **What to do**:
  - Validate JSON parse.
  - Validate against schema (if available toolchain).
  - Validate compatibility assertions documented from Task 3 references.

  **Must NOT do**:
  - Do not treat undocumented assumptions as pass.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: command-run verification.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: Final wave
  - **Blocked By**: 3, 4, 6, 7

  **References**:
  - Task 3 citation file.
  - OMO schema URL.

  **Acceptance Criteria**:
  - [ ] JSON parse succeeds.
  - [ ] Schema validation result captured (pass or tool-unavailable with fallback check).
  - [ ] Compatibility checklist completed.

  **QA Scenarios**:
  ```
  Scenario: Happy path validation
    Tool: Bash
    Preconditions: Config edits from Tasks 6-7 complete.
    Steps:
      1. Run jq empty for JSON validity.
      2. Run ajv/schema check if available.
      3. Save outputs.
    Expected Result: JSON valid; schema/docs checks documented.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-8-validation.txt

  Scenario: Failure path malformed JSON
    Tool: Bash
    Preconditions: Introduce malformed JSON in controlled temp copy.
    Steps:
      1. Run jq empty on malformed copy.
      2. Confirm non-zero exit and parse error text.
    Expected Result: Parse error detected and captured.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-8-json-error.txt
  ```

- [ ] 9. Run scope-drift audit and publish final target diff

  **What to do**:
  - Compare final file against baseline.
  - Assert only six target paths changed.
  - Publish compact final diff table and evidence index.

  **Must NOT do**:
  - Do not accept any non-target changes.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: deterministic diff checks.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: Final wave
  - **Blocked By**: 1, 4, 5, 6, 7

  **References**:
  - Task 1 baseline file.
  - Task 5 immutable allowlist.

  **Acceptance Criteria**:
  - [ ] Non-target drift report is CLEAN.
  - [ ] Final diff table contains only approved keys.

  **QA Scenarios**:
  ```
  Scenario: Happy path scope-fidelity pass
    Tool: Bash
    Preconditions: Baseline and final snapshots exist.
    Steps:
      1. Run key-level diff script.
      2. Compare changed keys against allowlist.
      3. Export final table.
    Expected Result: Only approved keys changed.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-9-scope-audit.json

  Scenario: Failure path non-target change detected
    Tool: Bash
    Preconditions: Introduce one extra key change in test copy.
    Steps:
      1. Run scope audit.
      2. Capture flagged key path.
    Expected Result: Audit fails with explicit non-target key path.
    Evidence: .sisyphus/evidence/gpt54-only-fix/task-9-scope-audit-error.txt
  ```


## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Verify all Must Have / Must NOT Have with evidence file cross-check.

  **QA Scenario**:
  ```
  Scenario: Final compliance pass
    Tool: Bash + Read
    Preconditions: Tasks 1-9 evidence files exist.
    Steps:
      1. Read this plan and list all Must Have / Must NOT Have items.
      2. Verify each item against produced evidence files and final config values.
      3. Emit pass/fail matrix with one row per requirement.
    Expected Result: 100% Must Have pass, 0 Must NOT violations.
    Evidence: .sisyphus/evidence/gpt54-only-fix/f1-plan-compliance-audit.md
  ```

- [ ] F2. **Config Quality Review** — `unspecified-high`
  Verify JSON syntax, schema compatibility checks, and no malformed keys.

  **QA Scenario**:
  ```
  Scenario: Syntax + schema quality audit
    Tool: Bash
    Preconditions: Corrected config file is saved.
    Steps:
      1. Run: jq empty ~/.config/opencode/oh-my-opencode.json
      2. Run schema validation command (ajv or documented fallback).
      3. Run key-presence sanity checks for target paths.
    Expected Result: JSON parse passes, schema/docs checks pass, no malformed key names.
    Evidence: .sisyphus/evidence/gpt54-only-fix/f2-config-quality-audit.txt
  ```

- [ ] F3. **QA Scenario Replay** — `unspecified-high`
  Re-run all task QA scenarios and confirm evidence files exist.

  **QA Scenario**:
  ```
  Scenario: End-to-end replay of task validations
    Tool: Bash
    Preconditions: Task evidence manifest exists.
    Steps:
      1. Re-run core verification commands from Tasks 6-9.
      2. Check every expected evidence path from manifest exists and is non-empty.
      3. Summarize pass/fail per task scenario.
    Expected Result: All required scenario checks pass and all evidence files are present.
    Evidence: .sisyphus/evidence/gpt54-only-fix/f3-qa-replay-report.md
  ```

- [ ] F4. **Scope Fidelity Check** — `deep`
  Confirm only target keys changed; no unrelated drift.

  **QA Scenario**:
  ```
  Scenario: Non-target drift detection
    Tool: Bash
    Preconditions: Baseline snapshot from Task 1 and immutable allowlist from Task 5 exist.
    Steps:
      1. Compute key-level diff between baseline and final config.
      2. Compare changed keys against approved target list.
      3. Fail if any changed key is outside approved set.
    Expected Result: Only approved keys changed (target list), non-target drift = 0.
    Evidence: .sisyphus/evidence/gpt54-only-fix/f4-scope-fidelity-audit.json
  ```

---

## Commit Strategy

- **1**: `chore(omo-config): fix gpt-5.4-only scope and validate`

---

## Success Criteria

### Verification Commands
```bash
jq '.agents.explore.model' ~/.config/opencode/oh-my-opencode.json
jq '.agents.momus.model,.agents.oracle.model,.categories.ultrabrain.model,.categories.deep.model' ~/.config/opencode/oh-my-opencode.json
jq '.agents.explore.reasoningEffort' ~/.config/opencode/oh-my-opencode.json
jq empty ~/.config/opencode/oh-my-opencode.json
```

### Final Checklist
- [ ] All target mappings corrected
- [ ] `explore.reasoningEffort` removed
- [ ] JSON validates
- [ ] Schema/docs compatibility evidence captured
- [ ] No non-target keys changed
