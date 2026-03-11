# OIG Proxy v2 - Phase 1 (MVP)

## TL;DR
> **Cíl**: Vytvořit novou, čistou OIG proxy from scratch s jednoduchou async architekturou.
> 
> **Phase 1 MVP**: TCP proxy Box ↔ Cloud, XML parsing, MQTT publish do Home Assistant
> 
> **Počet souborů**: 7 (oproti současným 41)
> **Technologie**: Python 3.11+, asyncio, paho-mqtt
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 vlny
> **Critical Path**: Protocol → Proxy → MQTT → Integration

---

## Context

### Proč nová proxy?
Současná proxy má **41 souborů** a **1542 řádků** v proxy.py. Problémy:
- Příliš komplexní kód, špatná separace zodpovědností
- Režimy ONLINE/HYBRID/OFFLINE se motají a nefungují spolehlivě
- Chyby v ACK generování
- Těžké testování a údržba

### Cíl Phase 1
Vytvořit **funkční MVP**:
1. TCP transparentní proxy Box ↔ Cloud
2. XML parsing rámů (device_id, table_name, data)
3. MQTT publish do Home Assistant (discovery + state)

**MIMO SCOPE Phase 1** (bude Phase 2/3):
- Hybrid mode / offline fallback
- Lokální ACK generování
- SQLite queue
- Session twin

### Analýza OIG Protokolu (zjištěno z existujícího kódu)

#### Frame Format
```xml
<Frame>{inner_xml}<CRC>xxxxx</CRC></Frame>\r\n
```

- **CRC**: CRC-16/MODBUS, decimal 5-digit
- **Poly**: 0x8005, **Init**: 0xFFFF, **RefIn/RefOut**: true
- **Calc**: přes inner content bez CRC tagu

#### Důležité Tabulky
| Tabulka | Účel |
|---------|------|
| tbl_actual | Aktuální hodnoty senzorů |
| tbl_box_prms | Parametry boxu (MODE, BAT_AC, ...) |
| IsNewSet | Dotaz na nová nastavení |
| IsNewWeather | Dotaz na počasí |

#### ACK Responses
```xml
<!-- Simple ACK -->
<Frame><Result>ACK</Result><CRC>xxxxx</CRC></Frame>

<!-- END with timestamp -->
<Frame><Result>END</Result><Time>2024-01-15 14:30:00</Time><UTCTime>2024-01-15 13:30:00</UTCTime><ToDo>GetActual</ToDo><CRC>xxxxx</CRC></Frame>
```

---

## Work Objectives

### Core Objective
Vytvořit funkční TCP proxy s XML parsingem a MQTT integrací, která nahradí současnou neudržitelnou implementaci.

### Concrete Deliverables
```
new_proxy/
├── main.py              # Vstupní bod
├── config.py            # Pydantic config
├── protocol/            # OIG protokol
│   ├── __init__.py
│   ├── crc.py          # CRC-16/MODBUS
│   ├── frame.py        # Frame build/parse
│   └── parser.py       # XML parsing
├── proxy/              # Proxy logika
│   ├── __init__.py
│   └── server.py       # TCP proxy
├── mqtt/               # MQTT client
│   ├── __init__.py
│   └── client.py       # MQTT wrapper + discovery
└── tests/
    ├── test_protocol/
    ├── test_proxy/
    └── conftest.py
```

### Definition of Done
- [ ] Proxy forwarduje TCP mezi Boxem a Cloudem
- [ ] Parsuje XML rámce (device_id, table, data)
- [ ] Publikuje data do MQTT (state topics)
- [ ] Zakládá entity v Home Assistant (MQTT discovery)
- [ ] Má unit testy s >80% coverage
- [ ] Je jednodušší než současná (méňe souborů, čistší kód)

### Must Have
- TCP proxy s async/await
- OIG frame parsing (CRC, XML)
- MQTT publish s HA discovery
- Konfigurace přes YAML
- Logging a základní diagnostika

### Must NOT Have (Phase 1)
- Hybrid mode / offline fallback
- Lokální ACK generování
- SQLite queue
- Session twin
- Telemetry na externí server
- Thin pass-through mode

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest existuje)
- **Automated tests**: TDD - nejprve testy, pak implementace
- **Framework**: pytest + pytest-asyncio

### QA Policy
Každý task má agent-exekvované QA scénáře. Evidence v `.sisyphus/evidence/`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Protocol - základ, 3 parallel):
├── Task 1: CRC-16/MODBUS implementace [quick]
├── Task 2: Frame builder/parser [quick]
└── Task 3: XML data parser [quick]

Wave 2 (Core Proxy, 3 parallel):
├── Task 4: TCP proxy server (Box ↔ Cloud) [deep]
├── Task 5: Config management (Pydantic) [quick]
└── Task 6: Logging a základní diagnostika [quick]

Wave 3 (MQTT + Integrace, 3 parallel):
├── Task 7: MQTT client wrapper [unspecified-high]
├── Task 8: HA MQTT discovery [deep]
└── Task 9: End-to-end integrace [unspecified-high]

Wave 4 (Testy + Dokumentace, 2 parallel):
├── Task 10: Unit testy (protocol, proxy, mqtt) [unspecified-high]
└── Task 11: Dokumentace a README [writing]

Wave FINAL (Review, 3 parallel):
├── Task F1: Code quality review [unspecified-high]
├── Task F2: Integration test [unspecified-high]
└── Task F3: Scope fidelity check [deep]

Critical Path: 1 → 2 → 3 → 4 → 8 → 9 → F1-F3
Parallel Speedup: ~60% vs sequential
Max Concurrent: 3
```

### Dependency Matrix
- **1**: — — 2
- **2**: 1 — 3, 4
- **3**: 2 — 4, 8
- **4**: 2, 3 — 9
- **5**: — — 4, 6
- **6**: — — 9
- **7**: — — 8
- **8**: 3, 7 — 9
- **9**: 4, 6, 8 — 10, 11
- **10**: 9 — F1-F3
- **11**: 9 — F1-F3
- **F1-F3**: 10, 11 — (end)

---

## TODOs

- [ ] **Task 1: CRC-16/MODBUS implementace**

**What to do**:
- Implementovat CRC-16/MODBUS podle existujícího local_oig_crc.py
- Polynomial: 0x8005, Init: 0xFFFF, RefIn/RefOut: true
- Předpočítaná tabulka pro rychlost
- Funkce: `crc16_modbus(data: bytes) -> int`

**Must NOT do**:
- Nepoužívat jiný algoritmus CRC
- Neměnit parametry (poly, init)

**Recommended Agent Profile**:
- **Category**: `quick`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 1
- **Blocks**: 2
- **Blocked By**: None

**References**:
- `addon/oig-proxy/local_oig_crc.py:28-52` - existující implementace

**Acceptance Criteria**:
- [ ] `crc16_modbus(b"test")` vrací správný výsledek
- [ ] Předpočítaná tabulka funguje
- [ ] Testy pro známé hodnoty z existujícího kódu

**QA Scenarios**:
```python
Scenario: CRC výpočet správně
Tool: Python REPL
Preconditions: crc.py existuje
Steps:
1. from protocol.crc import crc16_modbus
2. result = crc16_modbus(b"<Result>ACK</Result>")
3. Assert result == expected_value (z existujícího kódu)
Expected Result: CRC se shoduje s existující implementací
Evidence: .sisyphus/evidence/task-1-crc-test.txt
```

---

- [ ] **Task 2: Frame builder/parser**

**What to do**:
- Build frame: `<Frame>{inner}<CRC>xxxxx</CRC></Frame>\r\n`
- Parse frame: extrahovat inner content
- Validate CRC
- Handle CRLF terminátor

**Must NOT do**:
- Nepoužívat XML parser pro frame extrakci (pouze regex)
- Nepřidávat další vrstvy abstrakce

**Recommended Agent Profile**:
- **Category**: `quick`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 1
- **Blocks**: 3, 4
- **Blocked By**: 1

**References**:
- `addon/oig-proxy/oig_frame.py:49-77` - frame_inner_bytes, build_frame

**Acceptance Criteria**:
- [ ] `build_frame("<Result>ACK</Result>")` vrací validní frame
- [ ] `parse_frame(frame_bytes)` extrahuje inner XML
- [ ] CRC validation funguje

**QA Scenarios**:
```python
Scenario: Build a parse frame
Tool: Python REPL
Steps:
1. from protocol.frame import build_frame, parse_frame
2. frame = build_frame("<Result>ACK</Result>")
3. inner = parse_frame(frame)
4. Assert inner == "<Result>ACK</Result>"
Expected Result: Round-trip build+parse funguje
Evidence: .sisyphus/evidence/task-2-frame-test.txt
```

---

- [ ] **Task 3: XML data parser**

**What to do**:
- Extrahovat: `<TblName>`, `<ID_Device>`, `<DT>`, `<ID_SubD>`
- Parsovat všechna data pole (auto-convert int/float)
- Skip fields: TblName, ID_Device, ID_Set, Reason, ver, CRC, DT, ID_SubD
- Ignorovat SubD > 0 (neaktivní bateriové banky)

**Must NOT do**:
- Nepoužívat xml.etree (příliš pomalé) - použij regex
- Neměnit logiku SubD filtrování

**Recommended Agent Profile**:
- **Category**: `quick`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 1
- **Blocks**: 8
- **Blocked By**: 2

**References**:
- `addon/oig-proxy/oig_parser.py:17-76` - parse_xml_frame

**Acceptance Criteria**:
- [ ] `parse_xml_frame(xml)` vrací dict s _table, _device_id
- [ ] Číselná pole se auto-convertují
- [ ] SubD > 0 vrací prázdný dict

**QA Scenarios**:
```python
Scenario: Parse tbl_actual frame
Tool: Python REPL
Steps:
1. from protocol.parser import parse_xml_frame
2. result = parse_xml_frame("<TblName>tbl_actual</TblName><ID_Device>123</ID_Device><ENBL>1</ENBL>")
3. Assert result["_table"] == "tbl_actual"
4. Assert result["_device_id"] == "123"
5. Assert result["ENBL"] == 1 (int)
Expected Result: Správně parsovaná data
Evidence: .sisyphus/evidence/task-3-parser-test.txt
```

---

- [ ] **Task 4: TCP proxy server**

**What to do**:
- Asyncio TCP server na `PROXY_LISTEN_PORT`
- Forward: Box → Cloud (transparent)
- Parse rámce z Boxu, publikovat do MQTT
- Neblokující handling obou směrů

**Must NOT do**:
- Neimplementovat hybrid/offline logiku (jen forward)
- Nepřidávat queueing v Phase 1

**Recommended Agent Profile**:
- **Category**: `deep`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 2
- **Blocks**: 9
- **Blocked By**: 2, 3

**References**:
- `addon/oig-proxy/proxy.py` - reference (ale zjednodušit!)

**Acceptance Criteria**:
- [ ] Proxy naslouchá na portu 5710
- [ ] Forwarduje data Box ↔ Cloud
- [ ] Parsuje rámce a posílá data do handleru

**QA Scenarios**:
```python
Scenario: TCP proxy forwards data
Tool: Python test
Steps:
1. Start proxy server
2. Connect mock box
3. Send frame
4. Assert frame received by mock cloud
Expected Result: Transparent forward funguje
Evidence: .sisyphus/evidence/task-4-proxy-test.txt
```

---

- [ ] **Task 5: Config management**

**What to do**:
- Pydantic model pro konfiguraci
- YAML/JSON loading
- Environment variables override
- Validace (host, port, mqtt credentials)

**Must NOT do**:
- Nepřidávat feature flagy (LEGACY_FALLBACK, atd.)
- Jen základní config

**Recommended Agent Profile**:
- **Category**: `quick`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 2
- **Blocks**: 4, 6
- **Blocked By**: None

**Acceptance Criteria**:
- [ ] Config se načítá z YAML
- [ ] Env vars override fungují
- [ ] Validace vyhazuje při chybách

---

- [ ] **Task 6: Logging a diagnostika**

**What to do**:
- Structured logging (JSON format)
- Log levels: DEBUG, INFO, WARNING, ERROR
- Log connection events, frame processing
- Sanitizace logů (bez hesel)

**Must NOT do**:
- Neposílat telemetry na externí server (Phase 1)

**Recommended Agent Profile**:
- **Category**: `quick`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 2
- **Blocks**: 9
- **Blocked By**: None

---

- [ ] **Task 7: MQTT client wrapper**

**What to do**:
- paho-mqtt async wrapper
- Connect s auto-reconnect
- Publish s QoS 1
- Last will (LWT) pro availability

**Must NOT do**:
- Nepřidávat complex retry logic

**Recommended Agent Profile**:
- **Category**: `unspecified-high`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 3
- **Blocks**: 8
- **Blocked By**: None

---

- [ ] **Task 8: HA MQTT discovery**

**What to do**:
- Generovat discovery config pro senzory
- Topic: `homeassistant/sensor/{device_id}/{name}/config`
- Payload: JSON s name, state_topic, unit_of_measurement, atd.
- Retain flag pro discovery
- Device info (identifiers, name, model)

**Must NOT do**:
- Nezapomenout na retain flag
- Neměnit topic strukturu oproti současné

**Recommended Agent Profile**:
- **Category**: `deep`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 3
- **Blocks**: 9
- **Blocked By**: 3, 7

**References**:
- `addon/oig-proxy/mqtt_publisher.py` - reference

**Acceptance Criteria**:
- [ ] Discovery config se publishne při startu
- [ ] Entity se objeví v HA
- [ ] State updates fungují

---

- [ ] **Task 9: End-to-end integrace**

**What to do**:
- Spojit všechny komponenty
- main.py entry point
- Graceful shutdown (SIGTERM handling)
- Error handling a recovery

**Must NOT do**:
- Neimplementovat hybrid/offline logiku

**Recommended Agent Profile**:
- **Category**: `unspecified-high`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: NO
- **Parallel Group**: Wave 3 (synchronizační)
- **Blocks**: 10, 11
- **Blocked By**: 4, 6, 8

---

- [ ] **Task 10: Unit testy**

**What to do**:
- Testy pro protocol (CRC, frame, parser)
- Testy pro proxy (mock connections)
- Testy pro MQTT (mock broker)
- Coverage >80%

**Recommended Agent Profile**:
- **Category**: `unspecified-high`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 4
- **Blocks**: F1-F3
- **Blocked By**: 9

---

- [ ] **Task 11: Dokumentace**

**What to do**:
- README.md s instalací
- Architektura (diagram)
- Config reference
- Troubleshooting guide

**Recommended Agent Profile**:
- **Category**: `writing`
- **Skills**: []

**Parallelization**:
- **Can Run In Parallel**: YES
- **Parallel Group**: Wave 4
- **Blocks**: F1-F3
- **Blocked By**: 9

---

## Final Verification Wave

- [ ] **F1: Code quality review**
- pylint/mypy bez chyb
- Žádné `as any`, `type: ignore` bez komentáře
- Jednoduchá struktura (<10 souborů core)

- [ ] **F2: Integration test**
- End-to-end test s mock Box a Cloud
- MQTT broker test
- Ověření discovery v HA

- [ ] **F3: Scope fidelity check**
- Není implementován hybrid mode
- Není implementován local ACK
- Není SQLite queue
- Jen Phase 1 scope

---

## Success Criteria

### Verification Commands
```bash
# Testy
pytest new_proxy/tests/ -v --cov=new_proxy --cov-report=term-missing

# Spuštění
python new_proxy/main.py --config config.yaml

# MQTT check
mosquitto_sub -h localhost -t "oig_local/+/+/state" -v
```

### Final Checklist
- [ ] Všechny testy passují
- [ ] Coverage >80%
- [ ] Méně než 10 souborů v core
- [ ] Proxy běží a forwarduje data
- [ ] Entity se objeví v Home Assistant

---

## Poznámky pro Phase 2

Hybrid mode bude Phase 2:
- Detekce výpadku cloudu (timeout)
- Přepnutí do offline režimu
- Lokální ACK generování
- SQLite queue pro replay

Toto je plán pro Phase 1 MVP.
