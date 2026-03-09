# Twin ACK Forensics Verification Plan

## TL;DR

> **Quick Summary**: Cílem je forenzně ověřit Twin ACK flow (`Setting -> ACK(Reason=Setting) -> END`) proti cloud baseline, najít první reprodukovatelnou divergenci a vydat jednoznačný verifikační verdikt (GO / LIMITED_GO / NO_GO) bez runtime fixů v této fázi.
>
> **Deliverables**:
> - `analysis/twin-ack-forensics-verification/source-inventory.md`
> - `analysis/twin-ack-forensics-verification/runtime-snapshot.json`
> - `analysis/twin-ack-forensics-verification/twin-ack-timeline.jsonl`
> - `analysis/twin-ack-forensics-verification/session-ownership-matrix.csv`
> - `analysis/twin-ack-forensics-verification/ack-classification.csv`
> - `analysis/twin-ack-forensics-verification/frame-parity-diff.csv`
> - `analysis/twin-ack-forensics-verification/state-transition-diff.csv`
> - `analysis/twin-ack-forensics-verification/first-divergence.md`
> - `analysis/twin-ack-forensics-verification/verification-verdict.md`
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 waves + Final Verification Wave
> **Critical Path**: 2 -> 3 -> 6 -> 8 -> 10 -> 11 -> 12

---

## Context

### Original Request
Připravit chybějící plán `twin-ack-forensics-verification.md` ve stejném standardu jako ostatní `.sisyphus/plans/*`, se zaměřením na ověření Twin ACK chování a forenzní důkazní řetězec.

### Interview Summary
**Key Discussions**:
- Potřebujeme oddělit „co se děje v Twin path“ od „co se děje v cloud path“ a porovnat je v jednotném timeline.
- Nestačí sledovat pouze finální `FAILED`; nutné je zachytit pre-setting kontext, session lifecycle a ACK klasifikaci.
- Výstup musí být auditovatelný a reprodukovatelný, ne ad-hoc debug notes.

### Research Findings
- `proxy.py` obsahuje dedikovaný ACK route pro Twin (`_maybe_handle_twin_ack`) a explicitně pracuje s `Reason=Setting`.
- `digital_twin.py` kombinuje cloud-aligned i invariant-based logiku (INV-1/2/3), což je potřeba ověřit proti real frame evidence.
- V repozitáři existují související testy (`test_twin_ack_correlation.py`, `test_twin_cloud_parity.py`) a analytické artefakty z cloud/mock parity, které lze použít jako baseline reference.

### Metis Review
**Identified Gaps** (addressed):
- Chyběla explicitní ACK taxonomie (`ACK`, `NACK`, `END-as-ack`, `ambiguous`) -> doplněno jako samostatný task.
- Chybělo oddělení „symptom“ vs „first divergence“ -> doplněn explicitní causality krok.
- Chyběla reprodukovatelnost gate -> doplněna final wave se 4 nezávislými verifikacemi.

---

## Work Objectives

### Core Objective
Forenzně potvrdit nebo vyvrátit, že Twin ACK flow je parity-kompatibilní s cloud baseline pro setting lifecycle, a určit první důkazně podloženou divergenci.

### Concrete Deliverables
- `analysis/twin-ack-forensics-verification/source-inventory.md`
- `analysis/twin-ack-forensics-verification/runtime-snapshot.json`
- `analysis/twin-ack-forensics-verification/twin-ack-timeline.jsonl`
- `analysis/twin-ack-forensics-verification/session-ownership-matrix.csv`
- `analysis/twin-ack-forensics-verification/ack-classification.csv`
- `analysis/twin-ack-forensics-verification/frame-parity-diff.csv`
- `analysis/twin-ack-forensics-verification/state-transition-diff.csv`
- `analysis/twin-ack-forensics-verification/first-divergence.md`
- `analysis/twin-ack-forensics-verification/verification-verdict.md`

### Definition of Done
- [ ] Existuje alespoň 1 cloud success baseline a 1 twin verification case ve stejném canonical schema.
- [ ] ACK klasifikace je jednoznačná per case (`ACK_OK`, `ACK_REJECTED`, `ACK_AMBIGUOUS`, `ACK_MISSING`).
- [ ] `first-divergence.md` obsahuje jedinou primární divergenci s důkazním řetězcem.
- [ ] `verification-verdict.md` obsahuje GO/LIMITED_GO/NO_GO + reason codes.

### Must Have
- UTC normalizace času a jednotný case identifier napříč zdroji.
- Session ownership analýza (INV-1/2/3) v samostatném artefaktu.
- Frame-level + state-level + timing-level komparace.
- Reproducibility check (opakovaný běh s konzistentními počty záznamů).

### Must NOT Have (Guardrails)
- Žádné runtime fixy protokolu během tohoto plánu (jen verifikace a evidence).
- Žádné závěry bez reference na konkrétní log/frame/case ID.
- Žádné míchání unrelated flow (FW/weather-only bez vazby na setting ACK).
- Žádné manuální, neauditovatelné acceptance criteria.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - všechny verifikační kroky jsou agent-executable.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Existing repo tests + analyzační skripty
- **Framework**: Bash + Python + pytest (focused twin suites)

### QA Policy
Každý task musí vyprodukovat měřitelný artefakt v:
- `analysis/twin-ack-forensics-verification/`
- `.sisyphus/evidence/twin-ack-forensics-verification/task-{N}-{scenario}.{txt|json|csv|md}`

---

## Execution Strategy

### Parallel Execution Waves

```text
Wave 1 (Capture + Inventory):
- Task 1: Scope window + source inventory [quick]
- Task 2: Runtime snapshot (twin/proxy flags + mode) [quick]
- Task 3: Export proxy/twin logs for target window [unspecified-high]
- Task 4: Export frame-level evidence (payload capture / forensic source) [unspecified-high]
- Task 5: Export MQTT twin_state + control timeline [unspecified-high]

Wave 2 (Normalization + Differential Analysis):
- Task 6: Canonical timeline normalization [deep]
- Task 7: Session ownership matrix (INV-1/2/3) [deep]
- Task 8: ACK classifier + ambiguity detector [deep]
- Task 9: Frame parity diff against cloud baseline [deep]
- Task 10: State-transition + timing diff [deep]

Wave 3 (Synthesis + Verdict):
- Task 11: First divergence localization + causality chain [ultrabrain]
- Task 12: Verification verdict + next experiments [writing]

Wave FINAL (Independent review, parallel):
- F1: Plan compliance audit (oracle)
- F2: Reproducibility review (unspecified-high)
- F3: QA replay completeness (unspecified-high)
- F4: Scope fidelity check (deep)
```

### Dependency Matrix
- **1**: none -> 6
- **2**: none -> 6, 7
- **3**: none -> 6, 7, 8
- **4**: none -> 6, 8, 9
- **5**: none -> 6, 8, 10
- **6**: 1,2,3,4,5 -> 7,8,9,10
- **7**: 2,3,6 -> 10,11
- **8**: 3,4,5,6 -> 9,10,11
- **9**: 4,6,8 -> 11
- **10**: 5,6,7,8 -> 11
- **11**: 7,8,9,10 -> 12
- **12**: 11 -> FINAL

---

## TODOs

- [ ] 1. Vymezit analytické okno a inventář zdrojů

  **What to do**:
  - Definovat jednotné časové okno (default: poslední noční okno 00:00-06:00 Europe/Prague).
  - Zapsat seznam zdrojů (logs, frame capture, MQTT evidence, test outputs).
  - Uložit do `analysis/twin-ack-forensics-verification/source-inventory.md`.

  **Must NOT do**:
  - Neprovádět runtime zásahy (restart/reconfigure).

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1)
  - **Blocks**: 6
  - **Blocked By**: None

  **References**:
  - `.sisyphus/plans/setting-ack-parity-analysis.md`
  - `.sisyphus/plans/mock-rollout-overeni.md`

  **Acceptance Criteria**:
  - [ ] Inventory file exists and lists IN/OUT scope.
  - [ ] Time window + timezone rules are explicit.

  **QA Scenarios**:
  ```
  Scenario: Inventory complete
    Tool: Bash
    Steps:
      1. test -f analysis/twin-ack-forensics-verification/source-inventory.md
      2. grep "IN_SCOPE" analysis/twin-ack-forensics-verification/source-inventory.md
      3. grep "OUT_OF_SCOPE" analysis/twin-ack-forensics-verification/source-inventory.md
    Expected Result: all checks pass
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-1-inventory.txt
  ```

  **Commit**: NO

- [ ] 2. Runtime snapshot Twin/Proxy konfigurace a módů

  **What to do**:
  - Zachytit effective config (`TWIN_ENABLED`, `TWIN_KILL_SWITCH`, `TWIN_CLOUD_ALIGNED`, routing mode, proxy mode).
  - Uložit strojově čitelný snapshot do `runtime-snapshot.json`.

  **Must NOT do**:
  - Nezměnit žádný runtime flag v tomto tasku.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1)
  - **Blocks**: 6, 7
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/config.py`
  - `addon/oig-proxy/proxy.py`

  **Acceptance Criteria**:
  - [ ] `runtime-snapshot.json` exists and contains all mandatory keys.
  - [ ] Snapshot includes capture timestamp in UTC.

  **QA Scenarios**:
  ```
  Scenario: Runtime snapshot schema valid
    Tool: Bash + Python
    Steps:
      1. test -f analysis/twin-ack-forensics-verification/runtime-snapshot.json
      2. python -m json.tool analysis/twin-ack-forensics-verification/runtime-snapshot.json >/dev/null
    Expected Result: JSON parse OK
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-2-runtime-snapshot.txt
  ```

  **Commit**: NO

- [ ] 3. Export logů proxy/twin pro setting ACK okna

  **What to do**:
  - Exportovat raw logy addon/proxy/twin ve stejném časovém okně.
  - Zachytit marker lines pro queue/deliver/ack/timeout/end.
  - Uložit raw + filtered varianty.

  **Must NOT do**:
  - Neodfiltrovat pre-setting kontext.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1)
  - **Blocks**: 6, 7, 8
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/proxy.py` (`_maybe_handle_twin_ack`)
  - `addon/oig-proxy/digital_twin.py` (`on_ack`, delivery lifecycle)

  **Acceptance Criteria**:
  - [ ] Raw export contains >0 entries.
  - [ ] Filtered export includes all target lifecycle markers.

  **QA Scenarios**:
  ```
  Scenario: Log export parsable
    Tool: Bash
    Steps:
      1. validate exported file existence
      2. validate non-zero line count
    Expected Result: export non-empty and parsable
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-3-log-export.txt
  ```

  **Commit**: NO

- [ ] 4. Export frame-level evidence (payload capture / forensic)

  **What to do**:
  - Vyextrahovat frame-level data pro setting flow (`Setting`, `ACK`, `END`, související poll frame).
  - Udržet raw payload bez transformace v primárním exportu.

  **Must NOT do**:
  - Nemanipulovat s pořadím frame payloadů.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1)
  - **Blocks**: 6, 8, 9
  - **Blocked By**: None

  **References**:
  - `payloads.db` (pokud dostupné)
  - `analysis/golden-handshake-fixtures/`

  **Acceptance Criteria**:
  - [ ] Frame export obsahuje `ts`, `conn_id`, `raw_xml/raw_payload`, `source`.
  - [ ] Minimálně jeden setting attempt je plně dohledatelný.

  **QA Scenarios**:
  ```
  Scenario: Frame export integrity
    Tool: Bash + Python
    Steps:
      1. parse export format
      2. assert required keys per record
    Expected Result: required keys present in all records
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-4-frame-export.txt
  ```

  **Commit**: NO

- [ ] 5. Export MQTT timeline (control/status/result/twin_state)

  **What to do**:
  - Zachytit timeline topiců relevantních pro Twin setting lifecycle.
  - Uložit event feed se source+topic+payload hash.

  **Must NOT do**:
  - Nemíchat data z nesouvisejících topiců bez explicitního označení.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1)
  - **Blocks**: 6, 8, 10
  - **Blocked By**: None

  **References**:
  - `addon/oig-proxy/digital_twin.py` (`_state_topic`)
  - `addon/oig-proxy/control_pipeline.py`

  **Acceptance Criteria**:
  - [ ] MQTT timeline export exists and is timestamp-ordered.
  - [ ] Includes at least one end-to-end setting case.

  **QA Scenarios**:
  ```
  Scenario: MQTT timeline present
    Tool: Bash
    Steps:
      1. verify export exists
      2. verify timeline has >= 1 setting lifecycle chain
    Expected Result: valid lifecycle evidence in timeline
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-5-mqtt-timeline.txt
  ```

  **Commit**: NO

- [ ] 6. Sjednotit data do canonical timeline (UTC)

  **What to do**:
  - Normalizovat všechny zdroje do `twin-ack-timeline.jsonl`.
  - Přidat canonical fields: `case_id`, `ts_epoch_ms`, `source`, `event_type`, `conn_id`, `tx_id`.

  **Must NOT do**:
  - Neponechat mixed timezone format.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO (Wave 2 foundation)
  - **Blocks**: 7, 8, 9, 10
  - **Blocked By**: 1, 2, 3, 4, 5

  **References**:
  - `analysis/setting-ack-parity/*.json*`

  **Acceptance Criteria**:
  - [ ] JSONL valid, ordered, and schema-complete.
  - [ ] All records include `case_id` and `ts_epoch_ms`.

  **QA Scenarios**:
  ```
  Scenario: Canonical schema validation
    Tool: Bash + Python
    Steps:
      1. validate jsonl parse
      2. assert mandatory fields in each row
    Expected Result: schema pass
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-6-canonical.txt
  ```

  **Commit**: NO

- [ ] 7. Session ownership matrix (INV-1/2/3)

  **What to do**:
  - Rekonstruovat vazby `tx_id -> conn_id -> session_id`.
  - Vyhodnotit INV-1/INV-2/INV-3 pro každý case.
  - Uložit do `session-ownership-matrix.csv`.

  **Must NOT do**:
  - Nepřeskočit cases bez ACK (musí být explicitně označeny).

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Blocks**: 10, 11
  - **Blocked By**: 2, 3, 6

  **References**:
  - `addon/oig-proxy/twin_transaction.py`
  - `addon/oig-proxy/digital_twin.py`

  **Acceptance Criteria**:
  - [ ] Každý case má INV verdict (`PASS`/`FAIL`/`UNKNOWN`).
  - [ ] Každý FAIL má evidence pointer.

  **QA Scenarios**:
  ```
  Scenario: Ownership matrix complete
    Tool: Bash + Python
    Steps:
      1. validate csv headers
      2. validate non-empty INV verdict per row
    Expected Result: complete matrix
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-7-ownership.txt
  ```

  **Commit**: NO

- [ ] 8. ACK classifier a ambiguity detector

  **What to do**:
  - Klasifikovat každé ACK-like chování (`ACK_OK`, `NACK`, `END_AS_ACK`, `ACK_AMBIGUOUS`, `ACK_MISSING`).
  - Vyčíslit false-positive ACK riziko (např. END interpretovaný jako ACK).
  - Uložit do `ack-classification.csv`.

  **Must NOT do**:
  - Nepovažovat generic ACK bez `Reason=Setting` za validní setting ACK.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Blocks**: 9, 10, 11
  - **Blocked By**: 3, 4, 5, 6

  **References**:
  - `addon/oig-proxy/proxy.py` (`_maybe_handle_twin_ack`)
  - `tests/test_twin_ack_correlation.py`

  **Acceptance Criteria**:
  - [ ] Každý case má ACK class + rationale.
  - [ ] Ambiguous cases mají explicitní flag.

  **QA Scenarios**:
  ```
  Scenario: ACK taxonomy deterministic
    Tool: Bash + Python
    Steps:
      1. run classifier twice
      2. compare outputs checksum
    Expected Result: deterministic output
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-8-ack-taxonomy.txt
  ```

  **Commit**: NO

- [ ] 9. Frame parity diff vs cloud baseline

  **What to do**:
  - Porovnat twin frames s cloud baseline na úrovni field/value/order.
  - Oznámit diff taxonomy: `field_missing`, `value_format`, `ordering`, `semantic_mismatch`.
  - Uložit do `frame-parity-diff.csv`.

  **Must NOT do**:
  - Nezúžit diff pouze na string-compare celého XML.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Blocks**: 11
  - **Blocked By**: 4, 6, 8

  **References**:
  - `analysis/golden-handshake-fixtures/`
  - `tests/test_twin_cloud_parity.py`

  **Acceptance Criteria**:
  - [ ] CSV includes `case_id,stage,diff_type,field,cloud_value,twin_value`.
  - [ ] Každý failed twin case má >=1 diff nebo explicit `no_frame_diff`.

  **QA Scenarios**:
  ```
  Scenario: Frame diff exported
    Tool: Bash
    Steps:
      1. test -f analysis/twin-ack-forensics-verification/frame-parity-diff.csv
      2. validate required headers
    Expected Result: valid diff artifact
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-9-frame-diff.txt
  ```

  **Commit**: NO

- [ ] 10. State-transition + timing diff

  **What to do**:
  - Rekonstruovat transition chain (PENDING/DELIVERED/ACKED/FAILED/ENDED).
  - Porovnat timing metriky mezi cloud a twin (setting->ack, setting->end, retry gaps).
  - Uložit `state-transition-diff.csv`.

  **Must NOT do**:
  - Nemíchat lokální a UTC timestampy bez korekce.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Blocks**: 11
  - **Blocked By**: 5, 6, 7, 8

  **References**:
  - `addon/oig-proxy/digital_twin.py`
  - `tests/test_twin_replay_resilience.py`

  **Acceptance Criteria**:
  - [ ] Transition divergence explicitně lokalizovaná per case.
  - [ ] Timing outliers mají threshold verdict.

  **QA Scenarios**:
  ```
  Scenario: Transition/timing diff complete
    Tool: Bash + Python
    Steps:
      1. validate diff file exists
      2. verify each case has transition summary
    Expected Result: complete state+timing diff
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-10-transition-timing.txt
  ```

  **Commit**: NO

- [ ] 11. Lokalizovat first divergence + kauzalní řetězec

  **What to do**:
  - Vybrat nejčasnější reprodukovatelnou divergenci s nejvyšší evidence density.
  - Sestavit chain: divergence -> behavior shift -> missing/invalid ACK outcome.
  - Uložit do `first-divergence.md`.

  **Must NOT do**:
  - Neoznačit symptom bez antecedentu jako root cause.

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: 12
  - **Blocked By**: 7, 8, 9, 10

  **References**:
  - `ack-classification.csv`
  - `frame-parity-diff.csv`
  - `state-transition-diff.csv`

  **Acceptance Criteria**:
  - [ ] Exactly one primary divergence with coordinates (`case_id`, `ts`, `conn_id`, `stage`).
  - [ ] Confidence score + ranked alternatives present.

  **QA Scenarios**:
  ```
  Scenario: Primary divergence deterministic
    Tool: Bash
    Steps:
      1. run selection logic twice
      2. assert same primary candidate
    Expected Result: deterministic first divergence
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-11-first-divergence.txt
  ```

  **Commit**: NO

- [ ] 12. Final verification verdict + next experiments

  **What to do**:
  - Vytvořit `verification-verdict.md` s bloky: `VERDICT`, `EVIDENCE`, `CONFIDENCE`, `NEXT_EXPERIMENTS`, `UNKNOWNS`.
  - Vydat GO/LIMITED_GO/NO_GO s reason codes.

  **Must NOT do**:
  - Žádná neověřená tvrzení bez artefakt reference.

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: FINAL
  - **Blocked By**: 11

  **References**:
  - `first-divergence.md`
  - all `*-diff.csv` artifacts

  **Acceptance Criteria**:
  - [ ] Verdict includes reason codes.
  - [ ] Every major claim references concrete artifact path.

  **QA Scenarios**:
  ```
  Scenario: Verdict traceability
    Tool: Bash
    Steps:
      1. verify required section headers
      2. verify at least one artifact reference per claim block
    Expected Result: auditable final report
    Evidence: .sisyphus/evidence/twin-ack-forensics-verification/task-12-verdict.txt
  ```

  **Commit**: NO

---

## Final Verification Wave (MANDATORY)

- [ ] F1. **Plan Compliance Audit** - `oracle`
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT`

- [ ] F2. **Data Quality + Reproducibility Review** - `unspecified-high`
  Output: `Repro [PASS/FAIL] | Schema [PASS/FAIL] | Drift [NONE/N] | VERDICT`

- [ ] F3. **Real QA Replay** - `unspecified-high`
  Output: `Scenarios [N/N] | Evidence [N/N] | Failures [N] | VERDICT`

- [ ] F4. **Scope Fidelity Check** - `deep`
  Output: `In-scope [N/N] | Out-of-scope [0/N] | VERDICT`

---

## Commit Strategy

- **1**: `docs(analysis): add twin ack forensics verification plan`

---

## Success Criteria

### Verification Commands
```bash
test -f analysis/twin-ack-forensics-verification/source-inventory.md
test -f analysis/twin-ack-forensics-verification/runtime-snapshot.json
test -f analysis/twin-ack-forensics-verification/twin-ack-timeline.jsonl
test -f analysis/twin-ack-forensics-verification/session-ownership-matrix.csv
test -f analysis/twin-ack-forensics-verification/ack-classification.csv
test -f analysis/twin-ack-forensics-verification/frame-parity-diff.csv
test -f analysis/twin-ack-forensics-verification/state-transition-diff.csv
test -f analysis/twin-ack-forensics-verification/first-divergence.md
test -f analysis/twin-ack-forensics-verification/verification-verdict.md
```

### Final Checklist
- [ ] First divergence je explicitně určena (`where/when/what`).
- [ ] ACK taxonomie je jednoznačná a reprodukovatelná.
- [ ] Session ownership verdict je k dispozici per case.
- [ ] Final verdict má reason codes a navazující experimenty.
