# tbl_events - Kompletní Analýza

## Přehled

`tbl_events` je event log BatteryBoxu obsahující **3 typy událostí**:

| Typ | Počet | Zdroj | Účel |
|-----|-------|-------|------|
| **Factory** | 319 | Box → Cloud | Interní diagnostika boxu (HG režimy, FMT, invertor příkazy, modem, čas) |
| **Setting** | 31 | Box → Cloud | Log vzdálených změn konfigurace (MODE přepínání) |
| **Change** | 2 | Box → Cloud | Log lokálních změn hodnot (ERR_PV změny) |

**Klíčové zjištění**: Všechny eventy jdou **Z BOXU na cloud** jako telemetrie.

---

## 1. Factory Events (319 záznamů)

### 1.1 Kategorie Factory Events

| Kategorie | Příklad | Význam |
|-----------|---------|--------|
| **HG režimy** | `HG I (Load:793W, PV:0W, BC:47%, ...)` | Aktivace Grid režimu (HG I / HG IV-UPS) |
| **FMT režimy** | `FMT off [0-Standard (OFF)] (Load:...)` | Aktivace FMT režimu (Standard/No Limit) |
| **Invertor ACK** | `Invertor ACK : ^S006SEP01` | Potvrzení Voltronic příkazů do střídače |
| **Modem Reset** | `Modem Reset : 11,61,5,5,61,...` | Reset GSM modemu |
| **Time sync** | `Time set remotely : 2025-12-08 07:21 -> 07:22` | Synchronizace času z cloudu |
| **Forecast** | `Forecast Load OK: 3072325.xml, Geobaseid:3072325` | Stažení předpovědi spotřeby |
| **Day counter** | `New Day energy counter reset [7] -> [8]` | Reset denních čítačů energie |

### 1.2 HG Režimy (Grid Modes)

```
HG I           - MODE 0 (Home 1)   - Standard grid mode
HG IV-UPS      - MODE 3 (Home UPS) - UPS mode with battery priority
```

**Formát**: `HG {mode} (Load:XXXw, PV:XXXw, BC:XX%, BV:XX.XXXv, BA:±XX.XXXa, DAC:XX%, GMin:XX%)`

**System Snapshot obsahuje**:
- `Load` - Aktuální spotřeba [W]
- `PV` - Aktuální výkon z FVE [W]
- `BC` - Battery Capacity [%]
- `BV` - Battery Voltage [V]
- `BA` - Battery Amperage [A] (záporné = nabíjení)
- `DAC` - Discharge to AC [%]
- `GMin` - Grid Minimum [%]

### 1.3 FMT Režimy (Feed-in to Main Terminal)

```
FMT off [0-Standard (OFF)]  - MODE 0 - Bez dodávky do sítě
FMT on [3-No Limit]         - MODE 3 - Dodávka do sítě bez limitu
```

**Formát**: Stejný jako HG eventy, obsahuje system snapshot.

### 1.4 Voltronic Protocol - Příkazy do Střídače

#### Příkazový Set

| Příkaz | Frekvence | Význam (odhad) |
|--------|-----------|----------------|
| `^S015MCHGV0565,0565` | 31× | **Max CHarGe Voltage** - Maximální nabíjecí napětí (56.5V, 56.5V?) |
| `^S006SEP01` | 25× | **SEt Parameter** - Neznámý parametr 01 |
| `^S011MUCHGC0500` | 24× | **Max Utility CHarGe Current** - Max nabíjecí proud ze sítě (5.00A) |
| `^S005EDG0` | 24× | **Enable/Disable G** - Neznámý parametr G=0 |
| `^S005EDE1` | 24× | **Enable/Disable E** - Neznámý parametr E=1 |
| `^S005EDD1` | 24× | **Enable/Disable D** - Neznámý parametr D=1 |
| `^S005EDC1` | 24× | **Enable/Disable C** - Neznámý parametr C=1 |
| `^S005EDA1` | 24× | **Enable/Disable A** - Neznámý parametr A=1 |
| `^S005EDB1` | 12× | **Enable/Disable B** - Neznámý parametr B=1 |
| `^S005EDB0` | 12× | **Enable/Disable B** - Neznámý parametr B=0 |

#### Struktura Příkazu

```
^S[length][command][parameters]

Příklady:
^S015MCHGV0565,0565
  │││ │││││└─────────── Parametry: 0565 (56.5V), 0565 (56.5V)
  │││ └────────────────── Příkaz: MCHGV (Max Charge Voltage)
  ││└──────────────────── Délka celého řetězce: 015 (15 znaků)
  │└───────────────────── Typ: S (Set)
  └────────────────────── Prefix: ^
```

#### Kontext Volání

Příkazy se objevují vždy **po HG/FMT změně**:
1. HG režim aktivován (HG I nebo HG IV-UPS)
2. FMT režim aktivován (FMT off nebo FMT on)
3. **Sekvence invertor příkazů** (~8 příkazů během 50s)

**Typická sekvence při MODE 0→3**:
```
20:02:26  HG IV-UPS Start
20:02:34  FMT on [3-No Limit] Start
20:02:53  Invertor ACK : ^S006SEP01
20:02:59  HG IV-UPS (potvrzení)
20:03:03  FMT on [3-No Limit] (potvrzení)
20:03:11  Invertor ACK : ^S006SEP01
20:03:14  Invertor ACK : ^S011MUCHGC0500
20:03:18  Invertor ACK : ^S015MCHGV0565,0565
20:03:21  Invertor ACK : ^S005EDA1
20:03:25  Invertor ACK : ^S005EDB1
20:03:28  Invertor ACK : ^S005EDC1
20:03:32  Invertor ACK : ^S005EDD1
20:03:36  Invertor ACK : ^S005EDE1
20:03:44  Invertor ACK : ^S005EDG0
```

---

## 2. Setting Events (31 záznamů)

**Typ**: Log vzdálených změn konfigurace  
**Formát**: `Remotely : {table} / {field}: [{old}]->[{new}]`

### Příklad
```
Remotely : tbl_box_prms / MODE: [0]->[3]
```

### Pozorování
- Všechny Setting eventy v našem datasetu jsou **pouze MODE změny**
- Název "Remotely" je klamný - používá se i pro změny z lokálního displeje
- Obsahuje i redundantní záznamy (MODE [0]->[0]) - pravděpodobně ACK potvrzení

---

## 3. Change Events (2 záznamy)

**Typ**: Log lokálních změn hodnot  
**Formát**: `Input : {table} / {field}: [{old}]->[{new}]`

### Příklady
```
2025-12-08 06:53:15  Input : tbl_invertor_prms / ERR_PV: [12]->[4]
2025-12-08 15:42:53  Input : tbl_invertor_prms / ERR_PV: [4]->[12]
```

**ERR_PV**: Chybový kód FVE (Photovoltaic Error)
- `4` = ?
- `12` = ?

---

## 4. CRC Analýza (tbl_events)

Analýza `logs/payloads_boiler.db` (934 eventů) ukazuje, že `CRC` je **funkce pouze pole `<ver>`** – žádná jiná část frame ji neovlivňuje.

- 923 unikátních `ver` hodnot → 0 kolizí `ver → CRC` (u `tbl_events`)
- Opakované `ver` mají vždy stejný `CRC`, i když se liší `Content`, `DT` i `ID_Set`
  - např. `ver=52299` → `CRC=46201` pro dva různé eventy: `Invertor ACK : ^S015MCHGV0565,0565` (2025-12-07 20:48:44) i `Invertor ACK : ^S005EDG0` (2025-12-08 00:10:26)
- V celé DB je jediná výjimka (`ver=12345` má CRC `00000` i `12345`), ostatní páry jsou jednoznačné
- Důsledek: CRC **nezávisí na obsahu eventu**; pro generování nových eventů stačí vzít existující dvojici `(ver, CRC)` nebo zjistit mapování `CRC = f(ver)` z datasetu

---

## 5. Návrh Architektury: Events Device

### 5.1 Koncept

Vytvořit **samostatné HA zařízení** `BatteryBox Events` místo cpání sensorů do stávajících tabulek.

**Výhody**:
- ✅ Logické oddělení: Telemetrie vs. Event Log
- ✅ Čistota: tbl_box_prms zůstane jen pro skutečné parametry boxu
- ✅ Rozšiřitelnost: Snadné přidávání nových event sensorů
- ✅ Diagnostika: Centrální místo pro všechny diagnostické události

### 5.2 Navrhované Sensory

#### Tier 1: Okamžitě implementovatelné

| Sensor ID | Typ | Zdroj | Význam |
|-----------|-----|-------|--------|
| `event_hg_mode` | sensor (text) | Factory: HG | Aktuální Grid Mode (HG I / HG IV-UPS) |
| `event_fmt_mode` | sensor (text) | Factory: FMT | Aktuální FMT režim (Standard OFF / No Limit) |
| `event_mode_snapshot` | sensor (JSON attrs) | Factory: HG/FMT | System snapshot při MODE změně |
| `event_last_mode_change` | sensor (datetime) | Setting: MODE | Čas poslední MODE změny |
| `event_last_time_sync` | sensor (datetime) | Factory: Time | Čas poslední synchronizace času |

#### Tier 2: Vyžaduje analýzu Voltronic protokolu

| Sensor ID | Typ | Zdroj | Význam |
|-----------|-----|-------|--------|
| `event_invertor_max_charge_voltage` | sensor (V) | Factory: ^S015MCHGV | Maximální nabíjecí napětí |
| `event_invertor_max_charge_current` | sensor (A) | Factory: ^S011MUCHGC | Maximální nabíjecí proud |
| `event_invertor_parameters` | sensor (JSON) | Factory: ^S005ED* | Aktivní invertor parametry A-G |
| `event_invertor_last_command` | sensor (text) | Factory: Invertor ACK | Poslední odeslaný příkaz |

#### Tier 3: Diagnostické

| Sensor ID | Typ | Zdroj | Význam |
|-----------|-----|-------|--------|
| `event_modem_resets` | counter | Factory: Modem Reset | Počet modem resetů |
| `event_forecast_status` | binary_sensor | Factory: Forecast | Stav stahování předpovědí |
| `event_pv_error` | sensor (text) | Change: ERR_PV | Aktuální chybový kód FVE |

### 5.3 System Snapshot Attributy

Z HG/FMT eventů extrahovat:
```json
{
  "load_w": 793,
  "pv_w": 0,
  "battery_capacity_pct": 47,
  "battery_voltage_v": 52.19,
  "battery_amperage_a": -19.1,
  "discharge_to_ac_pct": 80,
  "grid_minimum_pct": 20
}
```

---

## 6. Reverse Engineering Voltronic Protocol

### 6.1 Známé Příkazy (z Voltronic dokumentace)

Voltronic má veřejně dostupný protokol pro některé modely:
- `QPIGS` - Query Protocol ID Get Status
- `QMOD` - Query Mode
- `POP02` - Set Output Priority

### 6.2 Naše Příkazy vs. Standard

Naše příkazy (`^S015MCHGV...`) se **NELIŠÍ** od standardu:
- Standard používá `P` prefix (e.g. `PCP03`)
- Naše používají `^S` prefix s délkou

**Hypotéza**: OIG BatteryBox používá **custom firmware** s proprietárním protokolem.

### 6.3 Další Kroky Pro Dekódování

1. **Získat Voltronic dokumentaci** pro konkrétní model střídače v BatteryBoxu
2. **Korelovat příkazy s akcemi**:
   - Zachytit ^S005EDB1 → změřit co se stalo
   - Srovnat s ^S005EDB0 → identifikovat význam
3. **Pattern matching**:
   - `^S005ED[A-G][0-1]` = Enable/Disable flags?
   - `^S011MUCHGC0500` = 0500 = 5.00A?
   - `^S015MCHGV0565,0565` = 2× 56.5V (bulk/float voltage?)

---

## 7. Implementační Plán

### Fáze 1: Events Device + Basic Sensors (Tier 1)
- [ ] Vytvořit nové HA zařízení `BatteryBox Events`
- [ ] Implementovat parsery pro HG/FMT/Time sync/MODE change
- [ ] Přidat sensory: HG mode, FMT mode, snapshot, last changes
- [ ] Otestovat na live komunikaci

### Fáze 2: Voltronic Analysis (Tier 2)
- [ ] Hledat Voltronic dokumentaci pro BatteryBox invertor
- [ ] Experimentálně určit význam ^S005ED[A-G] flags
- [ ] Implementovat parsery pro MCHGV/MUCHGC
- [ ] Přidat invertor sensory

### Fáze 3: Diagnostics (Tier 3)
- [ ] Implementovat modem reset counter
- [ ] Parsovat ERR_PV kódy
- [ ] Forecast status monitoring

### Fáze 4: Advanced
- [ ] Reverse engineer CRC algoritmus
- [ ] Umožnit generování vlastních Voltronic příkazů
- [ ] Local control bez cloud závislosti

---

## 8. Rizika & Omezení

### ❌ Nelze Bez Dalšího Výzkumu
- **CRC generování**: Proprietární algoritmus, nelze vytvořit nové příkazy
- **Voltronic sémantika**: Význam většiny příkazů je neznámý
- **Přímá kontrola**: Nelze posílat příkazy do střídače bez pochopení protokolu

### ⚠️ Pozor
- **Parse errors**: Factory eventy nemají striktní formát (free-text Content)
- **Voltronic změny**: Protokol se může lišit mezi verzemi firmware
- **Safety**: Experimentování s invertor příkazy může poškodit hardware

### ✅ Bezpečně Implementovatelné
- **HG/FMT detection**: Robustní regex parsing
- **System snapshots**: Parsování known patterns
- **Event counters**: Jednoduchý counter bez rizika
- **MODE tracking**: Již implementováno a otestováno

---

## 9. Závěr

`tbl_events` je **zlatý důl diagnostických dat** s obrovským potenciálem:

1. **Okamžitě použitelné**: HG/FMT režimy, system snapshots, MODE tracking
2. **Střednědobě**: Voltronic příkazy po reverse engineeringu
3. **Dlouhodobě**: Úplná lokální kontrola po cracku CRC

**Doporučení**: Začít implementací **Events Device s Tier 1 sensory** a paralelně analyzovat Voltronic protokol pro Tier 2.
