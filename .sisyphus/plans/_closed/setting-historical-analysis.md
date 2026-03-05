# Analýza historické komunikace OIG Box — Setting problém

## TL;DR

> **Quick Summary**: Systematická analýza 924K framů historické komunikace (Dec 2025 – Feb 2026) z dvou SQLite databází, testování 4 hypotéz proč BOX ignoruje proxy Setting frame, i když jeho formát je identický s cloud-originated Settings. Výstup: SQL skripty + česká zpráva s verdikty a akčním plánem.
> 
> **Deliverables**:
> - SQL analytické skripty pro každou hypotézu (reprodukovatelné)
> - Zpráva v češtině s rankovými závěry a konkrétními čísly
> - Akční plán "co dál" — konkrétní kroky k opravě
> 
> **Estimated Effort**: Medium (4-6 hodin analýzy)
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: Task 1 → Task 2 → Tasks 3-7 (parallel) → Task 8

---

## Context

### Original Request
Uživatel: "podle toho co vypisuješ jsi v nekonečné smčce pojdem připravti plán na analýzu komunikace z dostupných dat z minulosti a na základě výsledků chování boxu. POtřebuju ale kvalitní analýzu a plán jak s tím dále naložit."

### Interview Summary
**Key Discussions**:
- Uživatel chce STOP na live debugging a přejít na analýzu historických dat
- Mock server data (dnešní) vyloučit
- Potřebuje kvalitní analýzu, ne rychlé hacky
- Komunikace v češtině

**Research Findings** (z data exploration v této session):

1. **Frame formát NENÍ problém** — porovnání pole-po-poli potvrdilo identickou strukturu
2. **Proxy-originated Settings nikdy neexistovaly** v 872K framech historie — všech 143 Settings bylo cloud_to_proxy
3. **"Ghost ACKs"** — 7 Setting ACKů na Jan 23 bez odpovídajícího cloud Setting framu
4. **IsNewSet formát se změnil** — historicky vždy SHORT (121-124B), dnes LONG (760B s tbl_actual)
5. **Connection model se změnil** — Dec: long-lived (6000+ framů), Jan: mix, dnes: krátká (~3s)
6. **Proxy injektuje GetActual** framy každých ~10 sekund do BOXu
7. **Poslední cloud Setting v DB**: Jan 4, 2026 (boiler MANUAL)

### Metis Review
**Identified Gaps (addressed)**:
- Output format unclear → Resolved: SQL skripty + česká zpráva
- Success criteria missing → Resolved: Každá hypotéza dostane verdikt (SUPPORTED/REFUTED/INCONCLUSIVE)
- Schema validation missing → Added as Task 1
- GetActual interference hypothesis highest priority → Reordered
- Timebox on IsNewSet deep-dive → 15 min max v Task 5

---

## Work Objectives

### Core Objective
Identifikovat root cause proč BOX ignoruje proxy-originated Setting frame. Testovat 4 hypotézy systematicky na historických datech a vytvořit akční plán pro opravu.

### Concrete Deliverables
- `analysis/setting_investigation/schema_check.sql` — Ověření schémat obou DB
- `analysis/setting_investigation/h1_connection_lifecycle.sql` — Test hypotézy H1
- `analysis/setting_investigation/h2_isnewset_format.sql` — Test hypotézy H2
- `analysis/setting_investigation/h3_getactual_interference.sql` — Test hypotézy H3
- `analysis/setting_investigation/h4_protocol_state.sql` — Test hypotézy H4
- `analysis/setting_investigation/ghost_acks.sql` — Analýza "ghost ACKs"
- `analysis/setting_investigation/report_cz.md` — Česká zpráva s verdikty

### Definition of Done
- [ ] Všechny 4 hypotézy mají verdikt: SUPPORTED / REFUTED / INCONCLUSIVE
- [ ] Každý verdikt je podložen konkrétními čísly (frame counts, timestamps, timing)
- [ ] SQL skripty jsou reprodukovatelné (spustitelné opakovaně)
- [ ] Zpráva v češtině obsahuje akční plán "co dál"
- [ ] Mock-server data (Feb 16, 2026) vyloučena z analýzy

### Must Have
- Systematický test všech 4 hypotéz
- Konkrétní čísla z dat (ne spekulace)
- Reprodukovatelné SQL skripty
- Akční plán v závěru

### Must NOT Have (Guardrails)
- ❌ Live debugging nebo modifikace proxy kódu
- ❌ Zahrnutí dnešních dat (mock server)
- ❌ Spekulace bez datové podpory
- ❌ Deep-dive do IsNewSet formátu déle než 15 minut
- ❌ Modifikace databázových souborů
- ❌ "Hádání" frame formátů — formát je ověřen jako identický

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (Python + SQLite)
- **Automated tests**: None (this is analysis, not code)
- **Framework**: Python SQLite3 CLI or inline scripts

### QA Policy
Ověření = SQL dotazy vracejí nenulové výsledky + verdikty jsou podloženy konkrétními čísly.

| Deliverable Type | Verification Tool | Method |
|------------------|-------------------|--------|
| SQL Scripts | Bash (python3 -c) | Spustit SQL, ověřit non-empty výstup |
| Report | Bash (grep) | Ověřit přítomnost verdiktů a čísel |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — prerequisites):
├── Task 1: Schema verification + data inventory [quick]
└── Task 2: Časová mapa všech Setting events [quick]

Wave 2 (After Wave 1 — core hypotheses, MAX PARALLEL):
├── Task 3: H3 — GetActual interference (HIGHEST PRIORITY) [deep]
├── Task 4: H1 — Connection lifecycle analysis [deep]
├── Task 5: H2+H4 — IsNewSet format change + protocol state [deep]
├── Task 6: Ghost ACKs investigation [deep]
└── Task 7: Kompletní sekvence jednoho funkčního Setting delivery [deep]

Wave 3 (After Wave 2 — synthesis):
└── Task 8: Syntéza — česká zpráva + akční plán [writing]

Wave FINAL (After ALL — verification):
├── Task F1: Ověření reprodukovatelnosti SQL skriptů [quick]
└── Task F2: Scope fidelity check [quick]

Critical Path: Task 1 → Task 2 → Tasks 3-7 (parallel) → Task 8 → F1-F2
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 5 (Wave 2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|------------|--------|------|
| 1 | — | 2, 3, 4, 5, 6, 7 | 1 |
| 2 | 1 | 3, 4, 5, 6, 7 | 1 |
| 3 | 1, 2 | 8 | 2 |
| 4 | 1, 2 | 8 | 2 |
| 5 | 1, 2 | 8 | 2 |
| 6 | 1, 2 | 8 | 2 |
| 7 | 1, 2 | 8 | 2 |
| 8 | 3, 4, 5, 6, 7 | F1, F2 | 3 |
| F1 | 8 | — | FINAL |
| F2 | 8 | — | FINAL |

### Agent Dispatch Summary

| Wave | # Parallel | Tasks → Agent Category |
|------|------------|----------------------|
| 1 | **2** | T1 → `quick`, T2 → `quick` |
| 2 | **5** | T3 → `deep`, T4 → `deep`, T5 → `deep`, T6 → `deep`, T7 → `deep` |
| 3 | **1** | T8 → `writing` |
| FINAL | **2** | F1 → `quick`, F2 → `quick` |

---

## TODOs

- [x] 1. Ověření schémat + datový inventář

  **What to do**:
  - Ověřit, že obě DB (`payloads.db` a `payloads_ha_full.db`) mají kompatibilní schémata
  - Zdokumentovat rozdíly (raw_b64, conn_id NULL vs NOT NULL)
  - Potvrdit časové rozsahy, počty framů, a exkluzi dnešních dat
  - Vytvořit SQL script `analysis/setting_investigation/schema_check.sql`

  **Must NOT do**:
  - Modifikovat databáze
  - Zahrnout data po Feb 1, 2026

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Jednoduchý SQL introspection, žádná složitá logika
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Tasks 3, 4, 5, 6, 7
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `analysis/ha_snapshot/payloads.db` — Dec 11-12, 2025. Schema: id, ts, device_id, table_name, raw, raw_b64, parsed, direction, conn_id (NULL), peer, length. 52,107 frames.
  - `analysis/ha_snapshot/payloads_ha_full.db` — Dec 18, 2025 - Feb 1, 2026. Same schema but conn_id populated, 871,952 frames.

  **Acceptance Criteria**:
  - [ ] SQL script existuje: `analysis/setting_investigation/schema_check.sql`
  - [ ] Script reportuje schéma obou DB
  - [ ] Výstup obsahuje: počet framů, časový rozsah, directions, top tables pro obě DB
  - [ ] Potvrzeno, že žádná data z Feb 16 nejsou zahrnuta

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Schema check returns valid output
    Tool: Bash (python3)
    Preconditions: Both DB files exist at analysis/ha_snapshot/
    Steps:
      1. Run: python3 -c "import sqlite3; db=sqlite3.connect('analysis/ha_snapshot/payloads.db'); print(db.execute('PRAGMA table_info(frames)').fetchall())"
      2. Assert: Output contains 'id', 'ts', 'raw', 'direction', 'conn_id'
      3. Run: python3 -c "import sqlite3; db=sqlite3.connect('analysis/ha_snapshot/payloads_ha_full.db'); print(db.execute('SELECT COUNT(*) FROM frames').fetchone())"
      4. Assert: Output > 800000
    Expected Result: Both schemas accessible, frame counts match known values
    Evidence: .sisyphus/evidence/task-1-schema-check.txt

  Scenario: No Feb 16 data included
    Tool: Bash (python3)
    Preconditions: DB connected
    Steps:
      1. Run query: SELECT COUNT(*) FROM frames WHERE ts LIKE '2026-02-16%' on both DBs
      2. Assert: Count = 0 for payloads.db, Count = 0 for payloads_ha_full.db
    Expected Result: Zero frames from today
    Evidence: .sisyphus/evidence/task-1-no-today-data.txt
  ```

  **Commit**: NO

---

- [x] 2. Časová mapa Setting events

  **What to do**:
  - Vytvořit kompletní časovou mapu všech Setting-related událostí v obou DB:
    - Cloud Setting frames (cloud_to_proxy s NewValue)
    - BOX Setting ACKs (Result=ACK, Reason=Setting)
    - tbl_events s Type=Setting
    - IsNewSet frames (box_to_proxy)
  - Spárovat Setting → ACK → tbl_events sekvence (matching na časové okno ±30s)
  - Identifikovat "ghost ACKs" bez odpovídajícího Setting framu
  - Identifikovat "orphan Settings" bez ACK
  - Vytvořit SQL script `analysis/setting_investigation/timeline.sql`

  **Must NOT do**:
  - Interpretovat data — to je pro Task 8
  - Analyzovat obsah framů detailně — jen timeline

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: SQL timeline queries, přímočaré
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Tasks 3, 4, 5, 6, 7
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - V `payloads.db` (Dec 11-12): 20 cloud Setting párů (cloud_to_proxy + proxy_to_box), 40 ACKs
  - V `payloads_ha_full.db` (Dec 18 - Feb 1): 143 cloud Settings (cloud_to_proxy only), 1031 ACKs, 0 proxy_to_box Settings

  **Key Known Frames**:
  - id=919 v payloads.db — REAL cloud Setting (Dec 11, 18:04:56 UTC), MODE [1]→[0]
  - id=865920 v payloads_ha_full.db — "ghost ACK" (Jan 23, 04:04:29 UTC), no corresponding Setting
  - Poslední cloud Setting v full DB: id=415277 (Jan 4, 17:37:40 UTC), boiler MANUAL

  **Acceptance Criteria**:
  - [ ] SQL script existuje: `analysis/setting_investigation/timeline.sql`
  - [ ] Výstup obsahuje: počet Settings, ACKs, tbl_events per den
  - [ ] Identifikované "ghost ACKs" — kolik, kdy, jaký pattern
  - [ ] Identifikované "orphan Settings" — kolik, kdy
  - [ ] Spárované sekvence Setting→ACK s delta časy

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Timeline contains all known Setting events
    Tool: Bash (python3)
    Preconditions: Both DBs accessible
    Steps:
      1. Run timeline.sql against payloads.db
      2. Assert: Reports 20 cloud Setting frames, ~40 ACKs
      3. Run timeline.sql against payloads_ha_full.db
      4. Assert: Reports 143 cloud Setting frames, ~1031 ACKs
    Expected Result: All known Setting events accounted for
    Evidence: .sisyphus/evidence/task-2-timeline.txt

  Scenario: Ghost ACKs identified
    Tool: Bash (python3)
    Preconditions: payloads_ha_full.db
    Steps:
      1. Query: ACKs on Jan 23 with Reason=Setting
      2. Query: Cloud Settings on Jan 23
      3. Assert: ACK count (7) > Setting count (0) for Jan 23
    Expected Result: Ghost ACKs documented with exact IDs and timestamps
    Evidence: .sisyphus/evidence/task-2-ghost-acks.txt
  ```

  **Commit**: NO

---

- [x] 3. H3 — GetActual interference analýza (HIGHEST PRIORITY)

  **What to do**:
  - **Hypotéza**: Proxy injektuje `<Result>ACK</Result><ToDo>GetActual</ToDo>` každých ~10s do BOXu. Toto může interferovat s Setting processing.
  - Analyzovat timing:
    1. Najít všechna úspěšná Setting doručení v obou DB
    2. Pro každé Setting: zaznamenat čas posledního GetActual PŘED Setting
    3. Pro každé Setting: zaznamenat čas prvního GetActual PO Setting
    4. Pro každé Setting: zaznamenat čas ACK od BOXu
    5. Zjistit: Přijde GetActual MEZI Setting doručením a ACK? Jak často?
  - Porovnat s cloud chováním: Posílá cloud GetActual po Setting?
  - V December capture (payloads.db, conn_id=NULL): cloud Setting sequence NEobsahovala GetActual injection
  - V `payloads_ha_full.db`: proxy POSÍLÁ GetActual (proxy_to_box), ale cloud_to_proxy Setting framy šly přímo
  - **Klíčová otázka**: Vložil proxy GetActual MEZI cloud Setting a BOX ACK? Pokud ano — to je interference.
  - Vytvořit SQL script `analysis/setting_investigation/h3_getactual_interference.sql`

  **Must NOT do**:
  - Modifikovat proxy kód
  - Testovat na živém systému

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Složitá časová korelační analýza, window joins
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 6, 7)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - `analysis/ha_snapshot/payloads_ha_full.db` — 872K frames, obsahuje proxy_to_box direction (GetActual injection)
  - `analysis/ha_snapshot/payloads.db` — December capture, conn_id=NULL, ale má timestamps pro timing
  - Cloud Setting frame: `<Frame><ID>13591530</ID>...<Reason>Setting</Reason>...<CRC>20339</CRC></Frame>` (len=378)
  - GetActual injection: `<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>` (len=75)
  - BOX Setting ACK: `<Frame><Result>ACK</Result><Rdt>...<Reason>Setting</Reason><Tmr>100</Tmr>...<CRC>...</CRC></Frame>` (len=137)

  **API/Type References**:
  - `addon/oig-proxy/proxy.py` — GetActual injection logic (hledej `GetActual` v souboru)
  - `addon/oig-proxy/oig_frame.py:build_getactual_frame()` (line 94-97) — stavba GetActual framu

  **Acceptance Criteria**:
  - [ ] SQL script existuje: `analysis/setting_investigation/h3_getactual_interference.sql`
  - [ ] Verdikt: SUPPORTED / REFUTED / INCONCLUSIVE
  - [ ] Počet případů kde GetActual přišel mezi Setting a ACK (s přesnými čísly)
  - [ ] Timing analýza: průměrný/min/max interval GetActual vs Setting delivery
  - [ ] Porovnání: kolik Settings mělo GetActual interference vs kolik ne

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: GetActual timing analysis produces results
    Tool: Bash (python3)
    Preconditions: payloads_ha_full.db accessible
    Steps:
      1. Run h3_getactual_interference.sql
      2. Assert: Output contains timing statistics (average, min, max)
      3. Assert: Output contains interference count (how many times GetActual between Setting and ACK)
    Expected Result: Concrete numbers — e.g., "GetActual appeared between Setting and ACK in X/Y cases (Z%)"
    Evidence: .sisyphus/evidence/task-3-h3-getactual.txt

  Scenario: Comparison with non-interfered Settings
    Tool: Bash (python3)
    Preconditions: Same DB
    Steps:
      1. Query: Settings that were followed by ACK WITHOUT intermediate GetActual
      2. Query: Settings that were followed by ACK WITH intermediate GetActual
      3. Compare ACK success rates between the two groups
    Expected Result: Clear comparison showing whether GetActual presence correlates with Setting failure
    Evidence: .sisyphus/evidence/task-3-h3-comparison.txt
  ```

  **Commit**: NO

---

- [x] 4. H1 — Connection lifecycle analýza

  **What to do**:
  - **Hypotéza**: BOX potřebuje, aby spojení zůstalo otevřené dostatečně dlouho po Setting doručení (min ~10s) pro zpracování a odeslání ACK.
  - Analyzovat:
    1. Pro každé úspěšné Setting doručení: kolik framů bylo na tom conn PŘED Setting?
    2. Pro každé úspěšné Setting: kolik framů NÁSLEDOVALO po Setting na stejném conn?
    3. Kolik sekund trvalo spojení po Setting doručení?
    4. Korelace: délka spojení po Setting vs. přijetí ACK
    5. Jsou ACKy vždy na STEJNÉM conn jako Setting, nebo na JINÉM?
  - Klíčové zjištění z preliminary research: BOX ACK (id=865920 v full DB, Jan 23) přišel na conn kde NEBYL žádný cloud Setting frame. To naznačuje, že ACKy přicházejí na jiném conn než kde byl Setting doručen.
  - Vytvořit SQL script `analysis/setting_investigation/h1_connection_lifecycle.sql`

  **Must NOT do**:
  - Analyzovat dnešní data
  - Navrhovat změny connection handling

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Komplexní cross-connection korelace, window analýza
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 5, 6, 7)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - `analysis/ha_snapshot/payloads_ha_full.db` — 18,334 distinct conn_ids, connection size distribution documented
  - conn=8393 v full DB: 128 frames, Setting ACK at id=865920 ale žádný Setting frame na tomto conn
  - Connection size distribution: tiny(1-5): 1981, short(6-20): 5964, medium(21-100): 8823, large(101-500): 1503, huge(500+): 63

  **Acceptance Criteria**:
  - [ ] SQL script existuje: `analysis/setting_investigation/h1_connection_lifecycle.sql`
  - [ ] Verdikt: SUPPORTED / REFUTED / INCONCLUSIVE
  - [ ] Statistika: na kolika % conn_ids je Setting A ACK na stejném spojení
  - [ ] Statistika: průměrný počet framů po Setting doručení na stejném conn
  - [ ] Cross-conn analýza: vzor "Setting na conn X, ACK na conn Y"

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Connection lifecycle statistics computed
    Tool: Bash (python3)
    Preconditions: payloads_ha_full.db
    Steps:
      1. Run h1_connection_lifecycle.sql
      2. Assert: Output contains "same conn" vs "different conn" Setting→ACK statistics
      3. Assert: Output contains frame count post-Setting statistics
    Expected Result: Concrete percentages — "X% ACKs on same conn as Setting, Y% on different conn"
    Evidence: .sisyphus/evidence/task-4-h1-lifecycle.txt

  Scenario: Edge case — December data (single long-lived conn)
    Tool: Bash (python3)
    Preconditions: payloads.db (Dec 11-12, conn_id=NULL)
    Steps:
      1. Analyze Setting→ACK pairs using timestamp proximity (conn_id unavailable)
      2. Assert: ACK always follows Setting within 10-15 seconds
    Expected Result: December data confirms ACK timing pattern on long-lived connections
    Evidence: .sisyphus/evidence/task-4-h1-december.txt
  ```

  **Commit**: NO

---

- [x] 5. H2+H4 — IsNewSet format change + protocol state

  **What to do**:
  - **H2**: Firmware update změnil IsNewSet formát. Historicky vždy SHORT (121-124B), dnes LONG (760B).
    1. Ověřit, že v OBOU DB je IsNewSet vždy SHORT
    2. Zjistit, zda existuje transition period kde se formát měnil
    3. Porovnat firmware verzi v BOX framech (pole `<Fw>` v IsNewFW)
  - **H4**: Setting musí přijít v konkrétním bodě protokolu.
    1. Jaký frame předchází IsNewSet, na který cloud odpovídá Setting?
    2. Je IsNewSet vždy na konci data burst? Nebo uprostřed?
    3. Jak rychle po IsNewSet přijde cloud Setting response? (timing)
    4. Je BOX v jiném "stavu" když posílá SHORT vs LONG IsNewSet?
  - **TIMEBOX**: Max 15 minut na pure IsNewSet format deep-dive. Pokud nic, přejít dál.
  - Vytvořit SQL script `analysis/setting_investigation/h2_h4_isnewset_protocol.sql`

  **Must NOT do**:
  - Trávit víc než 15 min na IsNewSet format spekulacích
  - Hádat co firmware dělá interně

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Protocol sequence analysis, firmware correlation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4, 6, 7)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - December SHORT IsNewSet (122B): `<Frame><Result>IsNewSet</Result><ID_Device>2206237016</ID_Device><Lat>3926</Lat><ver>27053</ver><CRC>07330</CRC></Frame>`
  - Current LONG IsNewSet (760B): `<Frame><Result>IsNewSet</Result><ID_Device>2206237016</ID_Device><Lat>1616</Lat><TblName>tbl_actual</TblName><ID_Set>...<ENBL>1</ENBL>...[full tbl_actual]...<ver>19133</ver><CRC>...</CRC></Frame>`
  - IsNewSet lengths in full DB: 122 (22,533x), 121 (4,098x), 123 (157x), 124 (69x) — all SHORT
  - BOX firmware: `v.4.25.43.1219` (from current IsNewFW frame)

  **Acceptance Criteria**:
  - [ ] SQL script existuje
  - [ ] H2 verdikt: SUPPORTED / REFUTED / INCONCLUSIVE
  - [ ] H4 verdikt: SUPPORTED / REFUTED / INCONCLUSIVE
  - [ ] IsNewSet length distribution zdokumentována
  - [ ] Firmware verze z IsNewFW framů zdokumentována
  - [ ] Timing: cloud Setting response time po IsNewSet (průměr, min, max)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: IsNewSet format analysis
    Tool: Bash (python3)
    Preconditions: Both DBs
    Steps:
      1. Query: IsNewSet length distribution in both DBs
      2. Assert: All historical IsNewSet frames are SHORT (< 200 bytes)
      3. Query: Firmware version from IsNewFW frames
      4. Assert: Firmware version documented
    Expected Result: Confirmed that IsNewSet was always SHORT in historical data
    Evidence: .sisyphus/evidence/task-5-h2-isnewset.txt

  Scenario: Protocol state before Setting
    Tool: Bash (python3)
    Steps:
      1. For 10 successful Settings: extract the 5 frames before IsNewSet
      2. Document the pattern (what frame types precede IsNewSet)
      3. Extract cloud response time to IsNewSet
    Expected Result: Pattern of frames before IsNewSet documented
    Evidence: .sisyphus/evidence/task-5-h4-protocol-state.txt
  ```

  **Commit**: NO

---

- [x] 6. Ghost ACKs investigace

  **What to do**:
  - Vyšetřit fenomén "ghost ACKs" — Setting ACKs bez odpovídajícího cloud Setting framu v DB
  - Statistika:
    1. Kolik Setting ACKs má odpovídající cloud Setting (na ±60s okno)?
    2. Kolik je "ghost ACKs" (ACK bez předchozího Setting)?
    3. Jsou ghost ACKs seskupeny časově? (batch pattern?)
    4. Jaký je DT pattern v ghost ACKs? (Rdt v ACK framu vs actual timestamp)
  - Tři možné vysvětlení:
    A. Cloud posílal Settings, ale proxy je nezachytil (capture bug)
    B. BOX opakuje ACKs pro staré Settings (replay pattern)
    C. Settings přišly přes jiný kanál (ne přes proxy)
  - Vytvořit SQL script `analysis/setting_investigation/ghost_acks.sql`

  **Must NOT do**:
  - Spekulovat bez dat
  - Analyzovat dnešní data

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Cross-referencing Setting frames s ACKs přes časové okno
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4, 5, 7)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - payloads_ha_full.db: 143 cloud Settings, 1031 Setting ACKs — ratio 1:7.2 (mnohem víc ACKs než Settings!)
  - Jan 23: 7 ACKs, 0 Settings
  - Last cloud Setting in DB: Jan 4 (id=415277)
  - ACKs continuing through Jan 23 (and possibly beyond) without new Settings

  **Acceptance Criteria**:
  - [ ] SQL script existuje
  - [ ] Počet matched vs unmatched ACKs (přesná čísla)
  - [ ] Časový pattern ghost ACKs (kdy se objevují, s jakou frekvencí)
  - [ ] Alespoň jedna z tří hypotéz (A/B/C) podpořena nebo vyvrácena

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Ghost ACK quantification
    Tool: Bash (python3)
    Steps:
      1. Count total Setting ACKs in payloads_ha_full.db
      2. Count Setting ACKs with matching cloud Setting (±60s window)
      3. Count ghost ACKs (no matching Setting)
      4. Assert: matched + ghost = total
    Expected Result: "X matched ACKs, Y ghost ACKs out of 1031 total"
    Evidence: .sisyphus/evidence/task-6-ghost-acks.txt

  Scenario: Ghost ACK temporal pattern
    Tool: Bash (python3)
    Steps:
      1. Group ghost ACKs by date
      2. Check if they correlate with last known Settings (replay pattern?)
      3. Check ACK Rdt values vs actual timestamp
    Expected Result: Pattern documented (e.g., "ghost ACKs appear every X minutes")
    Evidence: .sisyphus/evidence/task-6-ghost-temporal.txt
  ```

  **Commit**: NO

---

- [x] 7. Kompletní sekvence funkčního Setting delivery

  **What to do**:
  - Extrahovat a zdokumentovat 3 KOMPLETNÍ sekvence úspěšného Setting doručení:
    1. Z payloads.db (Dec 11, id=917-925) — čistý případ, long-lived conn
    2. Z payloads_ha_full.db — January případ, medium-length conn
    3. Z payloads_ha_full.db — Nejpozdější dostupný případ
  - Pro každou sekvenci zaznamenat:
    - Všechny framy v ±60s okně kolem Setting
    - Přesné timestamps (ms rozlišení)
    - Directions (box_to_proxy, cloud_to_proxy, proxy_to_box)
    - Frame sizes a obsah klíčových framů
    - Timing: IsNewSet → Setting → ACK → tbl_events
    - Přítomnost/absence GetActual injection v okně
  - Toto bude "reference manual" pro porovnání s naším broken behavior
  - Vytvořit SQL script + výstupní soubor

  **Must NOT do**:
  - Interpretovat — jen dokumentovat sekvence

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Přesná extrakce s timing analýzou
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4, 5, 6)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - payloads.db, id=917-925 — kompletní cloud Setting sekvence, Dec 11, 18:04:56 UTC
  - payloads_ha_full.db — Setting framy na conn=1 (long-lived, Dec 18-Jan 19)
  - Known working sequence: IsNewSet (SHORT) → Cloud Setting (378B) → [~10s] → BOX ACK (137B) → Cloud END+Time → tbl_events

  **Acceptance Criteria**:
  - [ ] 3 kompletní sekvence zdokumentovány s přesnými timestamps
  - [ ] Pro každou: frame-by-frame listing s direction, table, length, timing delta
  - [ ] Identifikace přítomnosti/absence GetActual v každé sekvenci
  - [ ] Timing tabulka: IsNewSet→Setting (ms), Setting→ACK (ms)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Three complete sequences extracted
    Tool: Bash (python3)
    Steps:
      1. Extract Dec 11 sequence (payloads.db, around id=917-925)
      2. Extract Jan sequence from payloads_ha_full.db
      3. Extract latest available sequence from payloads_ha_full.db
      4. Assert: Each sequence contains IsNewSet, Setting, ACK
    Expected Result: Three complete, documented Setting delivery sequences
    Evidence: .sisyphus/evidence/task-7-sequences.txt
  ```

  **Commit**: NO

---

- [x] 8. Syntéza — Česká zpráva + akční plán

  **What to do**:
  - Zkompilovat výsledky Tasks 3-7 do strukturované zprávy v češtině
  - Struktura zprávy:
    1. **Shrnutí** — co jsme zjistili (1 odstavec)
    2. **Verdikty hypotéz** — pro každou H1-H4: SUPPORTED/REFUTED/INCONCLUSIVE s důkazy
    3. **Ghost ACKs vysvětlení** — nejpravděpodobnější vysvětlení
    4. **Reference sekvence** — jak vypadá funkční Setting delivery
    5. **Root cause** — ranking nejpravděpodobnějších příčin
    6. **Akční plán** — konkrétní kroky k opravě (seřazené podle priority a pravděpodobnosti úspěchu):
       - Krok 1: [co udělat]
       - Krok 2: [co udělat]
       - Fallback: [co dělat pokud kroky 1-2 nezaberou]
  - Uložit do `analysis/setting_investigation/report_cz.md`

  **Must NOT do**:
  - Vymýšlet data — jen sumarizovat výsledky analýzy
  - Navrhovat implementaci (jen směr/strategii)

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Kompilace analytických výsledků do strukturované zprávy
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (sequential)
  - **Blocks**: F1, F2
  - **Blocked By**: Tasks 3, 4, 5, 6, 7

  **References**:

  **Pattern References**:
  - Outputs from Tasks 3-7 (SQL results + evidence files)
  - `.sisyphus/evidence/task-3-*.txt` through `task-7-*.txt`

  **Acceptance Criteria**:
  - [ ] Report existuje: `analysis/setting_investigation/report_cz.md`
  - [ ] Obsahuje verdikty pro všechny 4 hypotézy
  - [ ] Každý verdikt má konkrétní čísla z dat
  - [ ] Akční plán má ≥3 kroky seřazené podle priority
  - [ ] Psáno v češtině

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Report completeness check
    Tool: Bash (grep)
    Steps:
      1. grep -c "SUPPORTED\|REFUTED\|INCONCLUSIVE" analysis/setting_investigation/report_cz.md
      2. Assert: Count >= 4 (one verdict per hypothesis)
      3. grep -c "Krok" analysis/setting_investigation/report_cz.md
      4. Assert: Count >= 3 (at least 3 action steps)
    Expected Result: Report contains all required sections
    Evidence: .sisyphus/evidence/task-8-report-check.txt

  Scenario: Czech language verification
    Tool: Bash (python3)
    Steps:
      1. Read first 500 chars of report_cz.md
      2. Assert: Contains Czech characters (ě, š, č, ř, ž, ů, ú, á, í, é)
    Expected Result: Report is in Czech
    Evidence: .sisyphus/evidence/task-8-lang-check.txt
  ```

  **Commit**: YES
  - Message: `analysis: add OIG Box Setting communication historical analysis`
  - Files: `analysis/setting_investigation/*.sql`, `analysis/setting_investigation/report_cz.md`
  - Pre-commit: None (analysis files, no code)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

- [ ] F1. **Reprodukovatelnost SQL skriptů** — `quick`
  Spustit všechny SQL skripty z `analysis/setting_investigation/` proti oběma DB. Ověřit, že produkují non-empty výstup. Žádný script nesmí failnout.
  Output: `Scripts [N/N pass] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Scope Fidelity Check** — `quick`
  Ověřit: 1) Žádná data z Feb 16 v analýze. 2) Žádné code changes v proxy. 3) Všechny verdikty podloženy čísly. 4) Report v češtině. 5) Akční plán přítomen.
  Output: `Checks [N/N pass] | VERDICT: APPROVE/REJECT`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 8 | `analysis: add OIG Box Setting communication historical analysis` | `analysis/setting_investigation/*.sql`, `analysis/setting_investigation/report_cz.md` | SQL scripts produce output |

---

## Success Criteria

### Verification Commands
```bash
# All SQL scripts exist
ls analysis/setting_investigation/*.sql  # Expected: 6+ files

# Report exists in Czech
head -5 analysis/setting_investigation/report_cz.md  # Expected: Czech text

# All hypotheses have verdicts
grep -c "SUPPORTED\|REFUTED\|INCONCLUSIVE" analysis/setting_investigation/report_cz.md  # Expected: >= 4

# Action plan present
grep -c "Krok" analysis/setting_investigation/report_cz.md  # Expected: >= 3
```

### Final Checklist
- [ ] Všechny 4 hypotézy mají verdikt
- [ ] Ghost ACKs vysvětleny
- [ ] 3 reference sekvence zdokumentovány
- [ ] SQL skripty reprodukovatelné
- [ ] Zpráva v češtině s akčním plánem
- [ ] Mock-server data vyloučena
- [ ] Žádné code changes v proxy
