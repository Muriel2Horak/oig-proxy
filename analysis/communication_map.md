# OIG BatteryBox – komunikační mapa a vzory (2 dny dat)

Analýza 20 814 záznamů zachycených v SQLite payloads.db (7.12.2025 19:38 – 8.12.2025 17:45 UTC).

## 1. Shrnutí komunikace

### Sčítání

| Směr | Počet | Obsah |
|------|-------|-------|
| **box_to_proxy** | 10 409 | Senzory, tabulky, dotazy, ACK |
| **proxy_to_box** | 10 405 | ACK, ToDo, END, Cloud framy |
| **Celkem** | 20 814 | ~1 327 minut = 22 hodin |

### Nejčastější tabulky

| Tabulka | Počet | Span (min) | Frekvence (ks/h) |
|---------|-------|-----------|------------------|
| `tbl_actual` | 7 483 | 1 327 | 5.6 |
| `unknown` (IsNewSet) | 722 | ~ | ~ |
| `tbl_events` | 352 | 1 204 | 0.3 |
| `tbl_dc_in` | 268 | 1 327 | 0.2 |
| `tbl_ac_out` | 268 | 1 327 | 0.2 |
| `tbl_ac_in` | 268 | 1 327 | 0.2 |
| `tbl_box` | 267 | 1 322 | 0.2 |
| `tbl_boiler` | 267 | 1 322 | 0.2 |
| `tbl_batt` | 267 | 1 322 | 0.2 |
| `tbl_batt_prms` | 187 | 1 312 | 0.1 |
| `tbl_invertor_prms` | 34 | 1 270 | 0.03 |
| `tbl_box_prms` | 26 | 1 118 | 0.02 |

## 2. Komunikační flow (aktivit-driven)

### A) Pravidelná telemetrie (tbl_actual) – ~5.6 ks/h

**Iniciátor:** Box

**Frekvence:** ~10 s intervaly

**Content:**

```xml
Box → Proxy/Cloud:
  <Frame>
    <TblName>tbl_actual</TblName>
    <ID_Set>836419502</ID_Set>
    <DT>2025-12-08 18:45:02</DT>
    <Reason>Table</Reason>
    <ID_Device>2206237016</ID_Device>
    <ENBL>1</ENBL>
    <VIZ>8</VIZ>
    <Temp>26.80</Temp>
    <Humid>24.8</Humid>
    [... více senzorů]
  </Frame>

Proxy → Box:
  <Frame>
    <Result>ACK</Result>
    <ToDo>GetActual</ToDo>
    <CRC>00167</CRC>
  </Frame>
```

**Poznámka:**

- `ID_Set` inkrement (836419493 → 836419502) ~9 za 9 s.
- `DT` v rámci je přibližně lokální čas boxu (UTC+1).
- ACK z proxy je **vždy** `<ToDo>GetActual</ToDo>` – signál pro box pokračovat v telemetrii.
- CRC ACK je konstantní (`00167`) → pravděpodobně fixní, nezávisí na obsahu zprávy.

**Typ komunikace:** Request/Response (synchronní).

### B) Dotaz na nové nastavení (IsNewSet) – ireg. ~0.1–0.2 ks/min

**Iniciátor:** Box

**Frekvence:** Ireg. (pozorován interval 3–8 minut)

**Content:**

```xml
Box → Cloud:
  <Frame>
    <Result>IsNewSet</Result>
    <ID_Device>2206237016</ID_Device>
    <Lat>2963</Lat>
    <ver>08085</ver>
    <CRC>18507</CRC>
  </Frame>

Cloud → Proxy → Box:
  <Frame>
    <ID>13584179</ID>
    <ID_Device>2206237016</ID_Device>
    <ID_Set>1765136481</ID_Set>
    <ID_SubD>0</ID_SubD>
    <DT>07.12.2025 20:41:21</DT>
    <NewValue>3</NewValue>
    <Confirm>New</Confirm>
    <TblName>tbl_box_prms</TblName>
    <TblItem>MODE</TblItem>
    <ID_Server>5</ID_Server>
    <Reason>Setting</Reason>
    <TSec>2025-12-07 19:47:07</TSec>
    <ver>10712</ver>
    <CRC>16664</CRC>
  </Frame>

Box → Cloud:
  <Frame>
    <Result>ACK</Result>
    <Reason>Setting</Reason>
  </Frame>
```

**Poznámka:**

- `Lat` hodnota (2963, 494, 2937) – zatím neznámo (latence? náhodné?).
- `ver` v IsNewSet je jiný než `ver` v nastavovacím frame – nejde o přímou synchronizaci.
- Cloud frame má **proprietární CRC**, který neznáme.
- Box vždy ACK potvrdí s `Reason=Setting`.

**Typ komunikace:** Polling (box se ptá, cloud pošle až pak).

### C) Periodické tabulky (tbl_dc_in, tbl_ac_in, atd.) – ~0.2 ks/h

**Iniciátor:** Box

**Frekvence:** ~5–10 minut

**Content:**

```xml
Box → Proxy:
  <Frame>
    <TblName>tbl_dc_in</TblName>
    <ID_Set>836407801</ID_Set>
    <DT>2025-12-08 15:30:00</DT>
    <Reason>Table</Reason>
    <ID_Device>2206237016</ID_Device>
    [Pole DC_IN, DC_IN_Status, ...]
  </Frame>

Proxy → Box:
  <Frame>
    <Result>ACK</Result>
    <ToDo>GetActual</ToDo>
    <CRC>00167</CRC>
  </Frame>
```

**Poznámka:**

- Všechny tyto tabulky dostávají stejný ACK s `GetActual` (implicitní instrukce pokračovat).
- Časy jsou zakulaceny na desítky sekund → box je zasílá synchronně s `tbl_actual`.

**Typ komunikace:** Request/Response.

### D) Eventy (tbl_events) – ~0.3 ks/h

**Iniciátor:** Box

**Frekvence:** Ireg. (když se něco stane)

**Content:**

```xml
Box → Proxy:
  <Frame>
    <TblName>tbl_events</TblName>
    <Reason>Table</Reason>
    <ID_Device>2206237016</ID_Device>
    <ID_Set>836409994</ID_Set>
    <DT>2025-12-08 16:06:34</DT>
    <Type>Change</Type>
    <Confirm>NoNeed</Confirm>
    <Content>Invertor: Mode changed from 1 to 3</Content>
  </Frame>
```

**Poznámka:**

- `Confirm=NoNeed` → box nečeká potvrzení.
- `Type` může být `Change`, `Factory`, `Info` atd.
- Proxy stejně posílá ACK.

**Typ komunikace:** Event push (jednosměrný, no-wait).

### E) Parametry (tbl_batt_prms, tbl_invertor_prms, atd.) – velmi řídké

**Iniciátor:** Box

**Frekvence:** Velmi řídká (0.02–0.1 ks/h)

**Content:**

```xml
Box → Proxy:
  <Frame>
    <TblName>tbl_box_prms</TblName>
    <ID_Set>836407203</ID_Set>
    <DT>2025-12-08 15:20:00</DT>
    <Reason>Table</Reason>
    <ID_Device>2206237016</ID_Device>
    <MODE>0</MODE>
    <FMT>1</FMT>
    [... status parametry]
  </Frame>
```

**Poznámka:**

- Vysílají se jen periodicky (nejspíš jen při prvním startu nebo změně).
- `tbl_invertor_prms` viděna jen 34× za 1 270 minut → velmi řídká.

**Typ komunikace:** Request/Response.

### F) Cloud příkazy (proxy_to_box) – různé typy

**Iniciátor:** Cloud (proxy)

**Obsah:**

```xml
1. ACK + ToDo (nejčastěji):
   <Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>
   Dél: 75 B, interval ~10 s

2. END (session close):
   <Frame><Result>END</Result><CRC>34500</CRC></Frame>
   Pozorováno: 2025-12-08 17:32:41, 14:52:30, 14:52:26, 14:52:16

3. Weather (forecast):
   <Frame><Reason>Weather</Reason><Result>Weather</Result><Ix>36</Ix>...
   Dél: ~400+ B, obsah XML

4. Setting (cloud inicializace):
   [Full <Frame> s NewValue, ver, CRC - viz mode_commands.py]
   Dél: ~500+ B
```

**Poznámka:**

- `ACK + GetActual` je 99% zpráv – steady state.
- `END` signalizuje zavření session nebo obnovení.
- `Weather` a `Setting` jsou zřídka.

## 3. Simulační architektura

### Komponenty

1. **Box emulator** – produkuje `tbl_actual`, `tbl_events`, dotazy `IsNewSet`.
2. **Cloud proxy** – zpracuje telemetrii, odpoví `ACK+GetActual`, přijaté settings pošle boxu.
3. **CRC engine** – přesný replay (zatím) nebo reverse-engineered algoritmus.

### Klíčové prvky

#### a) Periodicita tbl_actual

- Interval: ~10 s
- ID_Set: inkrementální (~1 za sekundu)
- DT: lokální čas boxu (UTC+1 pozorován v datech)
- Přesný obsah: kopie senzorů ze sensor_map.json

#### b) Keepalive

- Proxy → Box: `<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>`
- Interval: odpověď v 0–100 ms po příchodu tbl_actual
- CRC fixní (00167) – není závislý na sadě

#### c) IsNewSet polling

- Box → Cloud: ireg. ~3–8 min
- Obsah: Result, ID_Device, Lat, ver, CRC (proprietární)
- Cloud odpověď: buď Setting (Mode, FMT atd.) s komplexním CRC, nebo nic

#### d) Weather/Info zprávy

- Cloud → Proxy → Box: ireg.
- XML obsah v payloadu
- Možná trigger na určité akce (forecast, alerts)

## 4. Simulační plán

### Fáze 1: Základní telemetrie loop

1. **Emulovat box:**
   - Vygeneruj `tbl_actual` s realističným intervalem (10 s) a inkrementálními ID_Set.
   - Kopíruj hodnoty ze vzorů v DB (Temp, Humid, ENBL, VIZ atd.).
2. **Proxy respons:**
   - Na každý frame odpoví `<Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC>`.
3. **Validace:**
   - Ověř, že box přijímá ACK a pokračuje.

### Fáze 2: IsNewSet polling

1. **Emulovat dotaz:**
   - Ireg. (0–10 min) pošli IsNewSet s náhodným `Lat` a `ver`.
   - Obsah: `<Result>IsNewSet</Result><ID_Device>2206237016</ID_Device><Lat>XXXX</Lat><ver>XXXXX</ver><CRC>XXXXX</CRC>`.
2. **Cloud odpověď:**
   - Zatím jen ACK nebo nic (bez Setting, protože CRC je proprietární).
   - Nebo replay zachycený Setting frame z mode_commands.py.

### Fáze 3: Ostatní tabulky

1. Emuluj `tbl_dc_in`, `tbl_ac_in` atd. (stejný ACK).
2. Ireg. push `tbl_events` s realističnými zprávami.

### Fáze 4: CRC reverse

1. Pokus o bruteforce CRC pro jednu změnu (např. MODE 0 → 3 s fixním ID_Set).
2. Pokud úspěšné, přidej do algoritmu.

## 5. Datový zdroj

- **SQLite:** `analysis/payloads.db` (10 MB, 20 814 záznamů).
- **Trvání:** 7.12 19:38 – 8.12 17:45 UTC.
- **Vzorky k dispozici:**
  - Přesné frame XMLy v DB.
  - Kompletní tbl_actual payload s Temp, Humid atd.
  - Přesné timestampy (UTC+milisekundy).
  - Zachycené Mode command framy (CRC validované).

## 6. Otevřené otázky k vyjasnění

1. **Lat v IsNewSet:** Jakou roli hraje? Latence? Identifikátor? Náhodné číslo?
2. **CRC algoritmus:** Patří k reverse-engineeringu (viz mode_commands.md).
3. **Weather obsah:** Kdy se posílá a jak se generuje?
4. **Session handling:** Co triggeruje `<Result>END</Result>`? Reconnect?

## 7. Příští kroky

- [ ] Repro script telemetrie loop (Python/Node emulator).
- [ ] Replay testování: naposílej zachycené tbl_actual frame na box a sbírání feedback.
- [ ] CRC solver: bruteforce nebo RE firmware.
- [ ] Cloud command knihovna: katalog všech možných nastavení (MODE, FMT, ...).
