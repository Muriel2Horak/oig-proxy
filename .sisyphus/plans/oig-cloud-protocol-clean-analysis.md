# OIG Cloud Protocol Clean Analysis

## TL;DR

> **Quick Summary**: Čistá, na datech založená "black-box" analýza protokolu mezi OIG Boxem a Cloudem. Využívá výhradně včerejší provozní logy z Loki a záznamy z DB proxy. Slouží jako 100% přesný podklad pro budoucí implementaci mock/diagnostics serveru.
> 
> **Deliverables**: 
> - Detailní Markdown dokumentace protokolu
> - Mermaid sekvenční diagramy a stavové automaty
> - Přesné timing statistiky a ukázky raw komunikace (zvláště pro `setting` příkazy)
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: DB Schema & Loki Discovery → Data Extraction → Data Pairing → Analysis → Documentation Synthesis

---

## Context

### Original Request
Uživatel požaduje vytvoření analytického plánu pro důkladné sledování komunikace mezi OIG Boxem a Cloudem (přes proxy). Cílem je získat absolutně čistý podklad (model protokolu) pro pozdější vytvoření "dvojčete" cloudu (mock server). Musí se vycházet z nově nasazeného detailního logování (včerejší data) s TCP session ID, srovnat raw TCP a XML a zaměřit se na celkové round-tripy a timing včetně `setting` příkazů. Analýza NESMÍ obsahovat žádný kód ani modifikaci stávajícího řešení.

### Interview Summary
**Key Discussions**:
- **Časové okno**: Včerejší den (nasazeno nové logování).
- **Vzorek**: Komunikuje zde pouze jeden box, vezmeme jeho kompletní traffic.
- **Výstup**: Markdown, raw traffic, timingy, Mermaid sekvenční diagramy.
- **Přístupy**: Loki (10.0.0.160:3100, no auth), DB (přes `ssh ha` do kontejneru).

### Metis Review
**Identified Gaps** (addressed):
- **DB Schema & Loki Query**: Nejsou předem známy struktury tabulek a dotazovací syntaxe - přidána Discovery vlna.
- **Přesný timezone**: Musí se srovnat podle toho, v jakém formátu jsou data.
- **Edge cases protokolu**: Doplněn požadavek analyzovat nejen happy path, ale i disconnecty, incomplete sessions, timeouty a partial messages.
- **Verifikace párování**: Přidán kontrolní krok pro manuální spot-check spárovaných dat mezi logy a DB.

---

## Work Objectives

### Core Objective
Vytvořit 100% přesný dokumentační model (Digital Twin specifikaci) chování OIG protokolu na základě tvrdých produkčních dat (včerejších), bez zaujatosti předchozími implementacemi.

### Concrete Deliverables
- `protocol_behavior_specification.md` obsahující state machines, sequence diagrams a timing tabulky.
- `raw_data_samples.json` (ukázky spárovaných zpráv pro testování v mocku).

### Definition of Done
- [ ] Všechny kroky v plánu jsou kompletní.
- [ ] Finální dokumentace umožňuje vývojáři mock serveru zodpovědět jakoukoliv otázku ohledně formátu a timingu zpráv.
- [ ] Dokumentace obsahuje aspoň 1 detailní diagram pro normální flow, 1 pro reconnect flow a 1 pro `setting` command round-trip.

### Must Have
- Analýza POUZE nad jedním konkrétním boxem (tím jediným aktivním ze včerejška).
- Rozbor TCP raw vs dekódované XML.
- Detailní mapování TCP Session ID napříč spojeními.

### Must NOT Have (Guardrails)
- **ŽÁDNÁ IMPLEMENTACE KÓDU MOCKU/DIAGNOSTICS.**
- Žádné změny v produkční DB nebo logách.
- Žádné úpravy stávajících skriptů v proxy.
- Žádná historická data starší než 1 den (pouze včerejšek, od kdy funguje detailní logging).

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.
> No unit tests are strictly written because there is no code, BUT data parsing scripts (internal for analysis) and validation gates must be strictly checked.

### Test Decision
- **Infrastructure exists**: NO (Not applicable for pure documentation)
- **Automated tests**: NO (Data science / analytical verification via assertions)
- **Agent-Executed QA**: YES. Každý krok sběru a analýzy dat bude kontrolován validací přes skripty a spot-check reporty uloženými v `.sisyphus/evidence/`.

### QA Policy
Všechna extrahovaná a zpracovaná data musí být doložena. Každý `Task` vygeneruje validation report do `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`. Kde je to možné, použít `bash` s unix tooly (`jq`, `grep`, `wc`) nebo Python REPL pro ověření.

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Start Immediately — Discovery & Exploration):
├── Task 1: Zjištění a dokumentace DB schematu (ssh ha) [explore]
├── Task 2: Ověření dotazů do Loki a zjištění timezone/volumu [explore]
└── Task 3: Potvrzení přesného timestamp rozsahu [quick]

Wave 2 (After Wave 1 — Data Extraction):
├── Task 4: Extrakce logů z Loki [quick]
└── Task 5: Export relevantní části DB z ha kontejneru [quick]

Wave 3 (After Wave 2 — Normalization & Analysis):
├── Task 6: Validace a spárování dat (Session IDs, timestamps) [deep]
├── Task 7: Analýza Protocol State Machine (Lifecycles) [deep]
└── Task 8: Analýza časování a round-tripů (zejména `setting`) [deep]

Wave 4 (After Wave 3 — Synthesis):
└── Task 9: Tvorba finální dokumentace a Mermaid diagramů [writing]

Wave FINAL:
├── Task F1: Plan compliance audit
├── Task F2: Data fidelity check (odpovídá dokumentace raw logům?)
└── Task F4: Scope fidelity (nebyla překročena hranice do implementace?)

### Dependency Matrix
- **1-3**: — — 4,5
- **4**: 1-3 — 6
- **5**: 1-3 — 6
- **6**: 4,5 — 7,8
- **7**: 6 — 9
- **8**: 6 — 9
- **9**: 7,8 — F1,F2,F4

---

## TODOs

- [x] 1. **DB Schema Discovery**

  **What to do**:
  - Pomocí Bash/Explore zjistit lokaci DB souboru (SQLite) v `ha` kontejneru.
  - Provést `sqlite3 .schema` a identifikovat tabulky a sloupce důležité pro komunikaci (logy, zprávy, metadata, box IDs).
  - Vytvořit mapu schematu (seznam relevantních tabulek a sloupců).

  **Must NOT do**:
  - Nedělat žádné UPDATE/DELETE nad DB! Pouze SELECT.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [] (stačí Bash pro SSH a SQLite cli)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 5
  - **Blocked By**: None

  **References**:
  - Přístup: `ssh ha`
  - Cíl: Najít data od OIG proxy v docker/podman volumes.

  **Acceptance Criteria**:
  - [ ] Schema vypsáno a uloženo do evidence.

  **QA Scenarios**:
  ```
  Scenario: Získat SQLite schema
    Tool: Bash
    Steps:
      1. ssh ha "docker inspect oig-proxy-db-container-name" (nebo find volume)
      2. ssh ha "sqlite3 /path/to/db '.schema'"
    Expected Result: Výpis schematu v textovém formátu.
    Evidence: .sisyphus/evidence/task-1-schema.txt
  ```

- [x] 2. **Loki Capability & Scope Check**

  **What to do**:
  - Napsat a spustit testovací Loki query přes HTTP API (na `10.0.0.160:3100`).
  - Zjistit volume (počet záznamů) a formát (JSON vs plain text) za včerejšek.
  - Zkontrolovat, zda logy prokazatelně obsahují TCP session IDs a raw frames.

  **Recommended Agent Profile**:
  - **Category**: `explore`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 4
  - **Blocked By**: None

  **Acceptance Criteria**:
  - [ ] Fungující `curl` příkaz pro vytažení dat z Loki API otestován.

  **QA Scenarios**:
  ```
  Scenario: Loki health check & volume check
    Tool: Bash
    Steps:
      1. curl -s -G "http://10.0.0.160:3100/loki/api/v1/query_range" --data-urlencode 'query={job="oig-proxy"}' --data-urlencode 'limit=10'
    Expected Result: Validní JSON s loki log lines obsahujícími TCP session ID.
    Evidence: .sisyphus/evidence/task-2-loki-sample.json
  ```

- [x] 3. **Time Range & Timezone Definition**

  **What to do**:
  - Určit přesné od/do timestamps (v Unix Epoch epochách a UTC/Local date-time) pro včerejší den, tak aby sedělo párování Loki logů a záznamů v SQLite.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 4, 5
  - **Blocked By**: None

  **QA Scenarios**:
  ```
  Scenario: Definice časového rozsahu
    Tool: Bash
    Steps:
      1. Porovnat date příkazy a vygenerovat epoch časy pro začátek a konec včerejšího dne.
    Expected Result: Uložené časy pro dotazování do souboru.
    Evidence: .sisyphus/evidence/task-3-time-range.json
  ```

- [x] 4. **Data Extraction - Loki Logs**

  **What to do**:
  - Použít otestované API z Tasku 2 k plné extrakci logů z Loki za definovaný časový úsek (Task 3).
  - Uložit výstup do lokálního souboru `loki_dump.json`.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 6
  - **Blocked By**: 2, 3

  **QA Scenarios**:
  ```
  Scenario: Plný Loki export
    Tool: Bash
    Steps:
      1. curl s odpovídajícím start/end časem, loop nebo velký limit (případně python script) k dumpnutí celého logu.
      2. jq '.data.result[0].values | length' loki_dump.json
    Expected Result: Vytvořený velký JSON soubor, počet záznamů > 0.
    Evidence: .sisyphus/evidence/task-4-loki-count.txt
  ```

- [x] 5. **Data Extraction - DB Export**

  **What to do**:
  - Připojit se přes `ssh ha` a udělat query / dump relevantních tabulek za definovaný časový úsek z Tasku 3.
  - Vytáhnout přes SCP výsledek (jako CSV nebo dump) do `db_dump.csv`.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 6
  - **Blocked By**: 1, 3

  **QA Scenarios**:
  ```
  Scenario: DB dump export
    Tool: Bash
    Steps:
      1. ssh ha "sqlite3 db_path -header -csv 'SELECT * FROM ...'" > db_dump.csv
      2. wc -l db_dump.csv
    Expected Result: Validní CSV soubor, který obsahuje data pro daný den.
    Evidence: .sisyphus/evidence/task-5-db-count.txt
  ```

- [x] 6. **Data Validation & Pairing**

  **What to do**:
  - Napsat jednorázový Python skript v pracovním adresáři, který načte `loki_dump.json` a `db_dump.csv`.
  - Spáruje log entries a DB zprávy pomocí Timestampů (s povoleným delta) a pomocí TCP Session ID / Connection ID.
  - Ze spárovaných dat vytvoří sjednocenou, chronologicky seřazenou strukturu `unified_timeline.json`.
  - Reportovat procento úspěšnosti párování (spot-check).

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: 7, 8
  - **Blocked By**: 4, 5

  **QA Scenarios**:
  ```
  Scenario: Run Data Pairing
    Tool: Bash
    Steps:
      1. python3 pair_data.py
      2. jq length unified_timeline.json
    Expected Result: unified_timeline.json vygenerován bez chyb, loguje úspěšnost párování.
    Evidence: .sisyphus/evidence/task-6-pairing-report.txt
  ```

- [x] 7. **Protocol State Machine Analysis**

  **What to do**:
  - Analyzovat `unified_timeline.json`.
  - Vyhledat: začátky a konce TCP sessions, frekvenci keep-alive/pingů.
  - Vyhledat anomálie: přerušená spojení (a reakci obou stran), timeouty, jak vypadá session převzetí ("takeover").
  - Identifikovat všechny stavy (např. INIT, AUTH_PENDING, IDLE, SETTING_IN_PROGRESS).

  **Recommended Agent Profile**:
  - **Category**: `deep`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: 9
  - **Blocked By**: 6

  **QA Scenarios**:
  ```
  Scenario: Identifikace stavů protokolu
    Tool: Bash
    Steps:
      1. python3 analyze_states.py
    Expected Result: Vygeneruje tabulku stavů a seznam zpozorovaných disconnect/reconnect patternů.
    Evidence: .sisyphus/evidence/task-7-states.json
  ```

- [x] 8. **Message Timing & Round-Trip Analysis (Focus on `setting`)**

  **What to do**:
  - Analyzovat latence: Doba od Box -> Proxy (TCP přijetí) -> Cloud. Doba od Cloud (odpověď) -> Proxy -> Box.
  - Zvláštní zaměření na `setting` příkazy: Projít každý zaznamenaný pokus o `setting`, popsat kolečko (Kdy cloud pošle setting, kdy box odpoví, posílá box ack?, jaký je timing z obou stran?).
  - Pozorovat vztah mezi raw TCP rámem a dekódovaným XML.
  - Identifikovat, co se stane, když se `setting` command pošle na nečinnou (idle) session.

  **Recommended Agent Profile**:
  - **Category**: `deep`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: 9
  - **Blocked By**: 6

  **QA Scenarios**:
  ```
  Scenario: Timing statistiky a round-tripy
    Tool: Bash
    Steps:
      1. python3 analyze_timing.py
    Expected Result: Vypsané průměrné, minimální a maximální latence a izolované "setting" requesty.
    Evidence: .sisyphus/evidence/task-8-timing-stats.txt
  ```

- [x] 9. **Documentation Synthesis**

  **What to do**:
  - Vygenerovat soubor `protocol_behavior_specification.md` v repozitáři (do docs/ složky pro uložení).
  - Vložit Mermaid sekvenční diagramy zachycující celý Flow spojení.
  - Vložit tabulky z Tasku 8 popisující timings.
  - Přidat reálné ukázky payloadů (série XML zpráv napříč jednou konkrétní session).

  **Recommended Agent Profile**:
  - **Category**: `writing`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: F1, F2, F4
  - **Blocked By**: 7, 8

  **QA Scenarios**:
  ```
  Scenario: Kontrola formátu dokumentace
    Tool: Bash
    Steps:
      1. cat protocol_behavior_specification.md | grep "\`\`\`mermaid"
    Expected Result: Mermaid sekce přítomna, dokument uložen.
    Evidence: .sisyphus/evidence/task-9-doc-check.txt
  ```

---

## Final Verification Wave (MANDATORY)

- [x] F1. **Plan Compliance Audit** — `oracle`
  Ověřit, že byly úspěšně splněny všechny fáze a `protocol_behavior_specification.md` je vygenerován a obsahuje vše, co plán zadal. Žádný implementační kód mocku nebyl vytvořen.

- [x] F2. **Data Fidelity Check** — `unspecified-high`
  Namátkově otevřít `protocol_behavior_specification.md` a zkontrolovat ukázky zpráv s `unified_timeline.json`. Zprávy nesmí být "vymyšlené modelem", musí přesně odpovídat raw datům z Loki/DB.

- [x] F4. **Scope Fidelity Check** — `deep`
  Ověřit modifikace filesystému (git diff). Všechny změny musí být pouze analytické dokumenty nebo sisyphus evidence JSON/TXT. Žádné .py modifikace stávající proxy.

---

## Commit Strategy
Analýza se nahraje do dedikovaného analytického adresáře (nebo jako volná .md zpráva). 
Příklad commitu: `docs(analysis): add raw protocol behavior documentation from loki/db logs`
