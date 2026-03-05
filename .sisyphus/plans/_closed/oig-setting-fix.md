# OIG BOX Setting Command Fix + Mock Server Alignment

## TL;DR

> **Quick Summary**: Fix proxy to deliver pending Settings on ANY poll type (IsNewFW, IsNewSet, IsNewWeather), not just IsNewSet. Update mock server to match real cloud behavior.
> 
> **Deliverables**:
> - Proxy fix in `cloud_forwarder.py` and `proxy.py` (2 lines each)
> - Mock server enhancement with pending Setting queue and delivery on all polls
> - Updated comments in `control_settings.py`
> 
> **Estimated Effort**: Short (1-2 hours)
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 1 → Task 3 → Task 5 (Proxy fix → Mock server → Integration test)

---

## Context

### Original Request
User: "ano a rovnou v tomto kontextu uprav i mock server. a srovnej chování mock server s našimi nálezy aby offline proxy se chovala stejně jako mock server."

### Interview Summary
**Key Discussions**:
- BOX je zabanovaný z cloudu, testování pouze přes mock server
- Root cause: Proxy zachytává Setting pouze pro `IsNewSet` polly, ale SKUTEČNÝ CLOUD posílá Settings jako odpověď na JAKÝKOLIV poll typ
- Evidence z databáze (18.12.2025): BOX poslal IsNewFW → Cloud odpověděl Setting CMD → BOX akceptoval

**Research Findings**:
- Databázová analýza potvrzuje, že cloud posílá Settings na IsNewFW (ne jen IsNewSet)
- ACK detekce vyžaduje `<Reason>Setting</Reason>` v odpovědi BOXu
- END frame se posílá po úspěšném ACK na `control_settings.py:374`

### Metis Review
**Identified Gaps** (addressed):
- **Mock server API pro queueování Settings**: Přidáme HTTP endpoint `/api/queue-setting`
- **Multi-Setting sequence pattern**: Single-slot design (jako proxy) - nová Setting přepíše starou
- **Komentáře v control_settings.py**: Aktualizujeme na řádcích 281-282, 295
- **Double-delivery risk v ONLINE mode**: Proxy intercept je PŘED forwardem do cloudu, takže se cloud response nepoužije

---

## Work Objectives

### Core Objective
Opravit proxy aby doručovala pending Settings jako odpověď na jakýkoliv poll typ (IsNewFW, IsNewSet, IsNewWeather), a sladit mock server s tímto chováním pro testování.

### Concrete Deliverables
- `addon/oig-proxy/cloud_forwarder.py:460` - rozšířená podmínka
- `addon/oig-proxy/proxy.py:646` - rozšířená podmínka
- `addon/oig-proxy/control_settings.py` - aktualizované komentáře
- `oig-diagnostic-cloud/server.py` - pending queue + Setting delivery na polls

### Definition of Done
- [ ] `grep -n 'in.*IsNewSet.*IsNewFW.*IsNewWeather' addon/oig-proxy/cloud_forwarder.py` → vrací řádek ~460
- [ ] `grep -n 'in.*IsNewSet.*IsNewFW.*IsNewWeather' addon/oig-proxy/proxy.py` → vrací řádek ~646
- [ ] `python3 -m py_compile addon/oig-proxy/cloud_forwarder.py` → exit 0
- [ ] `python3 -m py_compile addon/oig-proxy/proxy.py` → exit 0
- [ ] Mock server má `pending_setting` proměnnou a `/api/queue-setting` endpoint
- [ ] Mock server vrací Setting frame místo END když je pending Setting

### Must Have
- Podmínka rozšířena na všechny 3 poll typy v obou souborech
- Mock server delivery Setting na IsNewFW poll
- Mock server delivery Setting na IsNewWeather poll
- Mock server vrací END pouze když není pending Setting

### Must NOT Have (Guardrails)
- **NEMĚNIT** ACK detection logiku v `control_settings.py` (funguje správně)
- **NEMĚNIT** retry logiku v `control_pipeline.py` (nesouvisí)
- **NEPŘIDÁVAT** nové poll typy nebo konstanty
- **NEPŘIDÁVAT** MQTT do mock serveru
- **NEREFAKTOROVAT** okolní kód - pouze změna podmínky
- **NEVYTVÁŘET** queue pro více Settings - single-slot design jako proxy

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest, mock server)
- **Automated tests**: Tests-after (priorita je fix, testy až po ověření)
- **Framework**: pytest

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

| Deliverable Type | Verification Tool | Method |
|------------------|-------------------|--------|
| Proxy code change | Bash (grep, python -m py_compile) | Pattern search, syntax check |
| Mock server | Bash (curl, netcat) | HTTP API test, TCP frame test |
| Integration | Bash (curl + mock server logs) | Send poll, verify Setting response |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — proxy fix + mock server core):
├── Task 1: Proxy condition fix (cloud_forwarder.py + proxy.py) [quick]
├── Task 2: Update comments in control_settings.py [quick]
└── Task 3: Mock server pending queue + poll handling [unspecified-high]

Wave 2 (After Wave 1 — API + integration):
├── Task 4: Mock server HTTP API endpoint [quick]
└── Task 5: Integration test - verify Setting delivery [deep]

Wave FINAL (After ALL tasks — verification):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (syntax, lint)
└── Task F3: Real QA with mock server (curl tests)

Critical Path: Task 1 → Task 3 → Task 5 → F1-F3
Parallel Speedup: ~50% faster than sequential
Max Concurrent: 3 (Wave 1)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|------------|--------|------|
| 1 | — | 5 | 1 |
| 2 | — | — | 1 |
| 3 | — | 4, 5 | 1 |
| 4 | 3 | 5 | 2 |
| 5 | 1, 3, 4 | F1-F3 | 2 |
| F1-F3 | 5 | — | FINAL |

### Agent Dispatch Summary

| Wave | # Parallel | Tasks → Agent Category |
|------|------------|----------------------|
| 1 | **3** | T1 → `quick`, T2 → `quick`, T3 → `unspecified-high` |
| 2 | **2** | T4 → `quick`, T5 → `deep` |
| FINAL | **3** | F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high` |

---

## TODOs

- [x] 1. Proxy Condition Fix - Rozšířit zachytávání na všechny poll typy

  **What to do**:
  - V `cloud_forwarder.py:460` změnit `table_name == "IsNewSet"` na `table_name in ("IsNewSet", "IsNewFW", "IsNewWeather")`
  - V `proxy.py:646` změnit `table_name == "IsNewSet"` na `table_name in ("IsNewSet", "IsNewFW", "IsNewWeather")`
  - Aktualizovat log message na řádku 485-486 a 661-662 aby reflektovalo "any poll type" místo "IsNewSet"

  **Must NOT do**:
  - Neměnit žádný jiný kód v těchto funkcích
  - Nepřidávat nové proměnné nebo konstanty
  - Nerefaktorovat surrounding code

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Jednoduchá 2-řádková změna, jasně definovaná
  - **Skills**: []
    - Žádné speciální dovednosti nepotřeba
  - **Skills Evaluated but Omitted**:
    - `git-master`: Commit bude až po všech změnách

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Task 5
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `addon/oig-proxy/cloud_forwarder.py:460-492` - Aktuální IsNewSet interception (ONLINE/HYBRID mode)
  - `addon/oig-proxy/proxy.py:646-667` - Aktuální IsNewSet interception (OFFLINE mode)

  **API/Type References**:
  - `table_name` je string z parsovaného XML frame

  **External References**:
  - Databázová analýza: Cloud posílá Settings na IsNewFW poll (Dec 18, 2025)

  **WHY Each Reference Matters**:
  - `cloud_forwarder.py:460-492` - Toto je ONLINE/HYBRID mode handler, tady se intercept děje před forwardem do cloudu
  - `proxy.py:646-667` - Toto je OFFLINE mode handler, tady se generuje lokální odpověď

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Verify condition updated in cloud_forwarder.py
    Tool: Bash (grep)
    Preconditions: File exists at addon/oig-proxy/cloud_forwarder.py
    Steps:
      1. Run: grep -n 'table_name in.*IsNewSet.*IsNewFW.*IsNewWeather' addon/oig-proxy/cloud_forwarder.py
      2. Assert output contains line number around 460
      3. Run: python3 -m py_compile addon/oig-proxy/cloud_forwarder.py
      4. Assert exit code is 0
    Expected Result: Grep returns matching line, compile succeeds
    Failure Indicators: No grep match OR compile error
    Evidence: .sisyphus/evidence/task-1-cloud-forwarder-grep.txt

  Scenario: Verify condition updated in proxy.py
    Tool: Bash (grep)
    Preconditions: File exists at addon/oig-proxy/proxy.py
    Steps:
      1. Run: grep -n 'table_name in.*IsNewSet.*IsNewFW.*IsNewWeather' addon/oig-proxy/proxy.py
      2. Assert output contains line number around 646
      3. Run: python3 -m py_compile addon/oig-proxy/proxy.py
      4. Assert exit code is 0
    Expected Result: Grep returns matching line, compile succeeds
    Failure Indicators: No grep match OR compile error
    Evidence: .sisyphus/evidence/task-1-proxy-grep.txt

  Scenario: Verify old condition no longer exists (negative test)
    Tool: Bash (grep)
    Preconditions: Changes applied
    Steps:
      1. Run: grep -n 'table_name == "IsNewSet"' addon/oig-proxy/cloud_forwarder.py addon/oig-proxy/proxy.py
      2. Assert output is EMPTY (no matches)
    Expected Result: No matches found - old condition removed
    Failure Indicators: Any match found means old condition still exists
    Evidence: .sisyphus/evidence/task-1-old-condition-check.txt
  ```

  **Commit**: YES (group with Task 2)
  - Message: `fix(proxy): deliver Settings on any poll type (IsNewFW/IsNewSet/IsNewWeather)`
  - Files: `addon/oig-proxy/cloud_forwarder.py`, `addon/oig-proxy/proxy.py`
  - Pre-commit: `python3 -m py_compile addon/oig-proxy/cloud_forwarder.py addon/oig-proxy/proxy.py`

---

- [x] 2. Update Comments in control_settings.py

  **What to do**:
  - Na řádku 281-282 aktualizovat komentář z "BOX only accepts Settings as responses to IsNewSet polls" na "BOX accepts Settings as responses to any poll type (IsNewSet, IsNewFW, IsNewWeather)"
  - Na řádku 295 aktualizovat log message pokud zmiňuje pouze IsNewSet

  **Must NOT do**:
  - Neměnit žádnou logiku v tomto souboru
  - Neměnit ACK detection nebo retry handling

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Pouze změna komentářů, žádná logika
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `addon/oig-proxy/control_settings.py:281-295` - Komentáře k opravě

  **WHY Each Reference Matters**:
  - Komentáře musí reflektovat novou realitu že Settings se posílají na jakýkoliv poll typ

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Verify comments updated
    Tool: Bash (grep)
    Preconditions: File exists
    Steps:
      1. Run: grep -n 'any poll type\|IsNewSet.*IsNewFW.*IsNewWeather' addon/oig-proxy/control_settings.py
      2. Assert output shows updated comment text
      3. Run: grep -n 'only accepts.*IsNewSet' addon/oig-proxy/control_settings.py
      4. Assert output is EMPTY (old comment removed)
    Expected Result: New comment present, old comment absent
    Failure Indicators: Old comment still exists OR new comment missing
    Evidence: .sisyphus/evidence/task-2-comments-check.txt
  ```

  **Commit**: YES (group with Task 1)
  - Message: `docs(proxy): update comments to reflect all poll types accept Settings`
  - Files: `addon/oig-proxy/control_settings.py`

---

- [x] 3. Mock Server - Pending Queue + Poll Handling

  **What to do**:
  - Přidat `pending_setting: dict | None = None` class variable do MockServer
  - Upravit `_generate_ack()` metodu (server.py:565-609):
    - Pokud `pending_setting is not None` A `table_name in ("IsNewSet", "IsNewFW", "IsNewWeather")`:
      - Sestavit Setting frame z `pending_setting`
      - Vrátit Setting frame místo END
      - NEMAZAT pending_setting zde (to až po ACK)
    - Jinak: stávající chování (END pro polls)
  - Přidat metodu `queue_setting(tbl_name: str, tbl_item: str, new_value: str)` pro nastavení `pending_setting`
  - Přidat ACK handler: když BOX pošle `<Reason>Setting</Reason>`, vymazat `pending_setting` a poslat END

  **Must NOT do**:
  - Nepřidávat MQTT
  - Nepřidávat queue pro více Settings (single-slot design)
  - Neměnit data frame handling (ACK+GetActual pattern)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Komplexnější změna vyžadující pochopení protokolu
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py:565-609` - `_generate_ack()` metoda k úpravě
  - `addon/oig-proxy/control_settings.py:35-36` - `pending` a `pending_frame` struktura jako vzor
  - `addon/oig-proxy/oig_frame.py:62` - `build_frame()` pro sestavení Setting frame

  **API/Type References**:
  - Setting frame format: `<Frame><Rdt>...</Rdt><Reason>Setting</Reason><TblName>X</TblName><TblItem>Y</TblItem><NewValue>Z</NewValue></Frame>`
  - ACK from BOX: `<Frame><Result>ACK</Result><Reason>Setting</Reason>...</Frame>`

  **External References**:
  - Databázová analýza: Cloud sekvence Setting → ACK → END

  **WHY Each Reference Matters**:
  - `server.py:565-609` - Tady je `_generate_ack()` která vrací odpovědi na polls, musíme přidat Setting logiku
  - `control_settings.py:35-36` - Vzorová struktura pro pending Setting
  - `oig_frame.py:62` - Budeme potřebovat CRC wrapper pro Setting frame

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Verify pending_setting variable exists
    Tool: Bash (grep)
    Preconditions: server.py modified
    Steps:
      1. Run: grep -n 'pending_setting' /Users/martinhorak/Projects/oig-diagnostic-cloud/server.py
      2. Assert output shows class variable declaration
    Expected Result: pending_setting variable found
    Failure Indicators: No match
    Evidence: .sisyphus/evidence/task-3-pending-var.txt

  Scenario: Verify poll handling checks pending_setting
    Tool: Bash (grep)
    Preconditions: server.py modified
    Steps:
      1. Run: grep -A5 'IsNewFW\|IsNewWeather\|IsNewSet' /Users/martinhorak/Projects/oig-diagnostic-cloud/server.py | grep -i 'pending'
      2. Assert output shows pending check in poll handling
    Expected Result: Pending check integrated with poll handling
    Failure Indicators: No pending check near poll handling
    Evidence: .sisyphus/evidence/task-3-poll-pending.txt

  Scenario: Verify queue_setting method exists
    Tool: Bash (grep)
    Preconditions: server.py modified
    Steps:
      1. Run: grep -n 'def queue_setting\|def set_pending' /Users/martinhorak/Projects/oig-diagnostic-cloud/server.py
      2. Assert output shows method definition
    Expected Result: Method for queuing Settings exists
    Failure Indicators: No method found
    Evidence: .sisyphus/evidence/task-3-queue-method.txt
  ```

  **Commit**: NO (wait for Task 4)

---

- [x] 4. Mock Server - HTTP API Endpoint

  **What to do**:
  - Přidat HTTP endpoint `POST /api/queue-setting` s JSON body: `{"tbl_name": "X", "tbl_item": "Y", "new_value": "Z"}`
  - Endpoint volá `queue_setting()` metodu z Task 3
  - Vrací `{"status": "queued", "pending": {...}}` nebo `{"status": "error", "message": "..."}`
  - Přidat `GET /api/pending` pro kontrolu aktuálního pending stavu

  **Must NOT do**:
  - Nepřidávat autentizaci (testovací server)
  - Nepřidávat queue pro více Settings

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Jednoduchý HTTP endpoint, jasná struktura
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 5)
  - **Blocks**: Task 5
  - **Blocked By**: Task 3

  **References**:

  **Pattern References**:
  - `/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py` - Existující HTTP handlers (pokud jsou)

  **WHY Each Reference Matters**:
  - Potřebujeme zjistit jak server handluje HTTP requesty a přidat endpoint

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Queue Setting via HTTP API
    Tool: Bash (curl)
    Preconditions: Mock server running on localhost:8080
    Steps:
      1. Run: curl -X POST http://localhost:8080/api/queue-setting -H "Content-Type: application/json" -d '{"tbl_name":"tbl_prms","tbl_item":"MODE","new_value":"0"}'
      2. Assert response contains "queued"
      3. Run: curl http://localhost:8080/api/pending
      4. Assert response shows pending Setting with MODE=0
    Expected Result: Setting queued successfully, visible in pending endpoint
    Failure Indicators: Error response OR empty pending
    Evidence: .sisyphus/evidence/task-4-api-queue.txt

  Scenario: API rejects invalid request
    Tool: Bash (curl)
    Preconditions: Mock server running
    Steps:
      1. Run: curl -X POST http://localhost:8080/api/queue-setting -H "Content-Type: application/json" -d '{}'
      2. Assert response contains "error" and describes missing fields
    Expected Result: Error response with helpful message
    Failure Indicators: 200 OK with empty/invalid queued Setting
    Evidence: .sisyphus/evidence/task-4-api-validation.txt
  ```

  **Commit**: YES (group with Task 3)
  - Message: `feat(mock-server): add pending Setting queue and HTTP API`
  - Files: `server.py`
  - Pre-commit: `python3 -m py_compile /Users/martinhorak/Projects/oig-diagnostic-cloud/server.py`

---

- [x] 5. Integration Test - Verify Setting Delivery on All Poll Types

  **What to do**:
  - Spustit mock server
  - Přes API zařadit Setting (MODE=0)
  - Poslat IsNewFW poll frame na TCP port 5710
  - Ověřit že odpověď obsahuje Setting frame s `<Reason>Setting</Reason>`
  - Opakovat pro IsNewWeather poll
  - Poslat ACK s `<Reason>Setting</Reason>`
  - Ověřit že další poll dostane END (pending cleared)

  **Must NOT do**:
  - Netestovat s reálným BOXem (zabanovaný)
  - Neměnit žádný kód (pouze testování)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Komplexní integrace TCP + HTTP + frame parsing
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (závisí na všech předchozích)
  - **Parallel Group**: Wave 2 (sequential after Tasks 1, 3, 4)
  - **Blocks**: Final verification
  - **Blocked By**: Tasks 1, 3, 4

  **References**:

  **Pattern References**:
  - `analysis/payloads_analysis.py` - Příklady parsování frames
  - `addon/oig-proxy/oig_frame.py` - Frame building utilities

  **API/Type References**:
  - IsNewFW poll: `<Frame><Result>IsNewFW</Result><Rdt>...</Rdt></Frame>`
  - Setting response: `<Frame><Reason>Setting</Reason><TblName>X</TblName>...</Frame>`
  - ACK: `<Frame><Result>ACK</Result><Reason>Setting</Reason>...</Frame>`

  **WHY Each Reference Matters**:
  - Potřebujeme sestavit validní poll frames pro testování
  - Response parsing pro ověření Setting delivery

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Setting delivered on IsNewFW poll
    Tool: Bash (curl + netcat/python)
    Preconditions: Mock server running, Setting queued via API
    Steps:
      1. Run: curl -X POST http://localhost:8080/api/queue-setting -d '{"tbl_name":"tbl_prms","tbl_item":"MODE","new_value":"0"}'
      2. Assert: Response shows "queued"
      3. Send IsNewFW poll frame to TCP 5710: echo '<Frame>...<Result>IsNewFW</Result>...</Frame>' | nc localhost 5710
      4. Capture response
      5. Assert: Response contains '<Reason>Setting</Reason>'
      6. Assert: Response contains '<TblName>tbl_prms</TblName>'
      7. Assert: Response contains '<NewValue>0</NewValue>'
    Expected Result: Setting frame returned instead of END
    Failure Indicators: Response contains '<Result>END</Result>' without Setting data
    Evidence: .sisyphus/evidence/task-5-isnewfw-setting.txt

  Scenario: Setting delivered on IsNewWeather poll
    Tool: Bash (same as above)
    Preconditions: Mock server running, Setting queued
    Steps:
      1. Queue Setting via API
      2. Send IsNewWeather poll frame to TCP 5710
      3. Assert: Response contains '<Reason>Setting</Reason>'
    Expected Result: Setting delivered on IsNewWeather (not just IsNewFW/IsNewSet)
    Failure Indicators: END without Setting
    Evidence: .sisyphus/evidence/task-5-isnewweather-setting.txt

  Scenario: Pending cleared after ACK
    Tool: Bash
    Preconditions: Setting was delivered
    Steps:
      1. Send ACK frame with '<Reason>Setting</Reason>' to TCP 5710
      2. Send another IsNewFW poll
      3. Assert: Response is '<Result>END</Result>' (no more pending)
      4. Run: curl http://localhost:8080/api/pending
      5. Assert: Response shows null/empty pending
    Expected Result: After ACK, pending is cleared, subsequent polls get END
    Failure Indicators: Second poll still returns Setting OR pending still set
    Evidence: .sisyphus/evidence/task-5-ack-clears-pending.txt
  ```

  **Commit**: NO (testing only)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 3 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist in .sisyphus/evidence/.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `python3 -m py_compile` on all modified files. Check for syntax errors. Verify no debug code left behind.
  Output: `Compile [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real QA with Mock Server** — `unspecified-high`
  Start mock server, execute ALL QA scenarios from Tasks 4 and 5. Verify Setting delivery works on all poll types.
  Output: `Scenarios [N/N pass] | VERDICT`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 + 2 | `fix(proxy): deliver Settings on any poll type` | cloud_forwarder.py, proxy.py, control_settings.py | python -m py_compile |
| 3 + 4 | `feat(mock-server): add pending Setting queue and HTTP API` | server.py | python -m py_compile |

---

## Success Criteria

### Verification Commands
```bash
# Proxy fix verification
grep -n 'in.*IsNewSet.*IsNewFW.*IsNewWeather' addon/oig-proxy/cloud_forwarder.py  # Expected: line ~460
grep -n 'in.*IsNewSet.*IsNewFW.*IsNewWeather' addon/oig-proxy/proxy.py  # Expected: line ~646

# Syntax check
python3 -m py_compile addon/oig-proxy/cloud_forwarder.py addon/oig-proxy/proxy.py  # Expected: exit 0

# Mock server API test (requires running server)
curl -X POST http://localhost:8080/api/queue-setting -d '{"tbl_name":"tbl_prms","tbl_item":"MODE","new_value":"0"}'  # Expected: {"status":"queued"...}
```

### Final Checklist
- [ ] Proxy delivers Settings on IsNewFW poll
- [ ] Proxy delivers Settings on IsNewWeather poll
- [ ] Proxy delivers Settings on IsNewSet poll (unchanged)
- [ ] Mock server queues Settings via HTTP API
- [ ] Mock server delivers Settings on any poll type
- [ ] Mock server clears pending after ACK
- [ ] All files compile without errors
- [ ] Comments updated to reflect new behavior
