# Parameter Change Protocol Analysis

## Měnitelné Parametry

BOX obsahuje několik *_PRMS tabulek s parametry které lze měnit vzdáleně přes Setting frames:

### TBL_BOX_PRMS (hlavní parametry systému)
- **MODE** - režim práce: 0=HOME I, 1=HOME II, 2=HOME III, 3=HOME UPS ⭐
- **ISON** - provoz/mimo provoz
- **BYPASS** / **BYPASS_M** - automatický/ruční bypass
- **LCD_BRIGH** / **LED_BRIGH** - jas LCD/LED (%)
- **FAN1** / **FAN2** - teplota ventilátorů
- **BAT_AC** - dobití AC (%)
- **P_FVE** / **P_BAT** / **P_GRID** / **P_LOAD** - výkonové limity
- **S_STOP_ISO** - centrální stop
- **RQRESET** / **RQRESEE** - reset LCD / EEPROM

### TBL_BOILER_PRMS (parametry bojleru)
- **MANUAL** - ruční ohřev vody (0/1) ⭐
- **ISON** - zap/vyp bojleru
- **PRRTY** - priorita
- **P_SET** - nastavený výkon (W)
- **ZONE1_S/E až ZONE4_S/E** - časová pásma ohřevu
- **HDO** - nízký tarif
- **TERMOSTAT** - signalizace termostatu
- **WD** - energie při HDO (Wh)
- **SSR0/1/2** - zapnutí/vypnutí SSR relé
- **OFFSET** - offset max. energie při HDO

### Další *_PRMS tabulky
- **TBL_BATT_PRMS** - parametry baterie
- **TBL_H_PUMP_PRMS** - tepelné čerpadlo
- **TBL_AIRCON_PRMS** - klimatizace
- **TBL_WL_CHARGE_PRMS** - bezdrátový ohřev
- **TBL_RECUPER_PRMS** - rekuperace
- **TBL_INVERTOR_PRMS** - invertor
- **TBL_CAR_CHARGE_PRMS** - EV nabíjení

⭐ = Nejčastěji měněné v databázi (MODE: 84×, MANUAL: 2×)

## Setting Frame Pattern

**Struktura je IDENTICKÁ pro všechny parametry** - liší se pouze TblName, TblItem a hodnoty:

```xml
<Frame>
  <ID>13589896</ID>                    <!-- DB record ID -->
  <ID_Device>2206237016</ID_Device>    <!-- BOX ID -->
  <ID_Set>1765399890</ID_Set>          <!-- Setting version -->
  <ID_SubD>0</ID_SubD>
  <DT>10.12.2025 21:51:30</DT>        <!-- User request timestamp -->
  <NewValue>1</NewValue>               <!-- Nová hodnota -->
  <Confirm>New</Confirm>
  <TblName>tbl_boiler_prms</TblName>  <!-- Cílová tabulka -->
  <TblItem>MANUAL</TblItem>            <!-- Parametr -->
  <ID_Server>5</ID_Server>
  <mytimediff>0</mytimediff>
  <Reason>Setting</Reason>
  <TSec>2025-12-10 20:51:30</TSec>    <!-- Server response time (UTC) -->
  <ver>11264</ver>
  <CRC>56812</CRC>
</Frame>
```

**Po aplikaci Setting BOX pošle update příslušné *_PRMS tabulky s novou hodnotou:**

Příklad: Setting MANUAL→1 (ID 75935) → tbl_boiler_prms s MANUAL=1 (ID 75940, ~5 frames později)

---

## IsNewSet Polling Pattern

**BOX → CLOUD každých ~90-120 sekund:**
```xml
<Frame>
  <Result>IsNewSet</Result>
  <ID_Device>2206237016</ID_Device>
  <Lat>1177</Lat>              <!-- Latence BOXu (ms) - neznámý význam -->
  <ver>41548</ver>             <!-- Verze / timestamp -->
  <CRC>65187</CRC>             <!-- CRC -->
</Frame>
```

**CLOUD odpovědi:**

### A) Jsou nové Settingy → posílá JE VŠECHNY najednou:
```
BOX → IsNewSet
CLOUD → Setting #1 (MODE→0)
BOX → ACK (Tmr=100)
CLOUD → Setting #2 (MODE→3)
BOX → ACK (Tmr=100)
CLOUD → Setting #3 (MANUAL→1)
BOX → ACK (Tmr=100)
CLOUD → END
```

### B) Nejsou nové Settingy → END frame:
```xml
<Frame>
  <Result>END</Result>
  <Time>2025-12-07 21:14:21</Time>        <!-- CET -->
  <UTCTime>2025-12-07 20:14:21</UTCTime>  <!-- UTC -->
  <ToDo>GetActual</ToDo>                  <!-- Hint pro další akci -->
  <CRC>28606</CRC>
</Frame>
```

**Klíčové poznatky:**
- Cloud má **frontu Settings** a při IsNewSet pollingu je pošle **všechny najednou**
- BOX potvrzuje **každý Setting zvlášť** ACK frame
- Po všech ACK cloud pošle **END frame** (konec Setting session)
- Pokud není žádný Setting → rovnou END frame
- `<Tmr>100</Tmr>` v ACK = BOX potvrzuje aplikaci za ~100ms (invertor komunikace trvá 8-9s, ale ACK je okamžitý)

---

## Implementace vlastních změn (mimo cloud)

Pro implementaci lokálních změn parametrů musíme **napodobit chování cloudu**:

### Co potřebujeme znát:

1. **Setting Frame struktura** (✅ ZNÁME):
   - Formát je IDENTICKÝ pro všechny parametry
   - Dynamická pole: `<ID>`, `<ID_Set>`, `<DT>`, `<NewValue>`, `<TSec>`, `<ver>`, `<CRC>`
   - Statická pole: `<TblName>`, `<TblItem>`, `<ID_Device>`, `<Confirm>New</Confirm>`

2. **CRC výpočet** (❌ NEZNÁME):
   - CRC je funkce času a obsahu frame
   - Možnosti:
     - A) **Learning mode**: Proxy zachytí Setting z cloudu a naučí se pattern
     - B) **Reverse engineering**: Analyzovat vztah CRC = f(Time, TblName, TblItem, NewValue)
     - C) **Cloud proxy**: Nechat cloud vygenerovat Setting, PROXY ho změní a přepošle

3. **ID_Set generování** (❓ NEJISTÉ):
   - Vypadá jako Unix timestamp (1765135114 ≈ 2025-12-07 20:18)
   - Možná: `ID_Set = int(time.time())`

4. **ver field** (❓ NEJISTÉ):
   - Hodnoty 11264, 20245, 23912... (random?)
   - Možná verzionování Setting frames v cloud DB

### Strategie implementace:

**Varianta A: Learning Mode** (DOPORUČENÁ)
```python
# 1. Zachytit Setting frame z cloudu
cloud_setting = capture_setting_frame()

# 2. Naučit se pattern pro konkrétní TblName/TblItem
learned_patterns['tbl_box_prms']['MODE'] = {
    'structure': parse_xml(cloud_setting),
    'crc_function': learn_crc_pattern(cloud_setting)
}

# 3. Generovat vlastní Setting s naučeným CRC
def create_local_setting(table, item, value):
    frame = learned_patterns[table][item]['structure'].copy()
    frame['NewValue'] = value
    frame['DT'] = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    frame['CRC'] = learned_patterns[table][item]['crc_function'](frame)
    return frame
```

**Varianta B: Cloud Proxy** (FALLBACK)
```python
# 1. Poslat request na cloud (přes fake user request)
# 2. Zachytit Setting frame od cloudu
# 3. Nechat cloud vygenerovat CRC
# 4. Forward na BOX
```

**Varianta C: Reverse Engineering CRC** (LONG-TERM)
```python
# Analyzovat vztah CRC vs. čas/obsah
# Najít hash funkci nebo lookup table
```

### Minimální požadavky pro změnu MODE:

```xml
<Frame>
  <ID>???</ID>                         <!-- Random? Auto-increment? -->
  <ID_Device>2206237016</ID_Device>    <!-- ✅ ZNÁME (z BOX frames) -->
  <ID_Set>???</ID_Set>                 <!-- Timestamp? -->
  <ID_SubD>0</ID_SubD>                 <!-- ✅ ZNÁME (vždy 0) -->
  <DT>10.12.2025 22:00:00</DT>        <!-- ✅ ZNÁME (local time) -->
  <NewValue>3</NewValue>               <!-- ✅ ZNÁME (user input) -->
  <Confirm>New</Confirm>               <!-- ✅ ZNÁME (vždy New) -->
  <TblName>tbl_box_prms</TblName>     <!-- ✅ ZNÁME -->
  <TblItem>MODE</TblItem>              <!-- ✅ ZNÁME -->
  <ID_Server>5</ID_Server>             <!-- ✅ ZNÁME (vždy 5) -->
  <mytimediff>0</mytimediff>           <!-- ✅ ZNÁME (vždy 0) -->
  <Reason>Setting</Reason>             <!-- ✅ ZNÁME (vždy Setting) -->
  <TSec>2025-12-10 21:00:00</TSec>    <!-- ✅ ZNÁME (UTC time) -->
  <ver>???</ver>                       <!-- ❌ NEZNÁME -->
  <CRC>???</CRC>                       <!-- ❌ NEZNÁME -->
</Frame>
```

**ANALÝZA CRC Z DATABÁZE:**

Z 93 Setting frames:
- Pouze **3.2% má CRC == ver** (pravděpodobně debug/test hodnoty)
- Objevují se **CRC=00000, CRC=12345** (dummy hodnoty v databázi)
- **Duplicitní Settingy** (stejné ID) mají **STEJNÉ CRC** → CRC je uložené v cloud DB, ne kalkulované real-time
- Nepodařilo se najít vztah: CRC ≠ f(TSec), CRC ≠ f(ver XOR ID), CRC ≠ hash(...)

**❌ BOX VALIDUJE CRC - TESTOVÁNO!**

Z `mode_commands.md` (prosinec 2025):
```
Box validuje CRC přes celý <Frame> (včetně ver, NewValue, timestampů). 
Jakákoliv změna → NACK s Reason=WC (Wrong Checksum).

Přesný replay zachyceného příkazu funguje (ACK); 
změna libovolné hodnoty → NACK s Reason=WC.
```

**Poznatky z testování:**
1. ✅ **Přesný replay** funguje (BOX pošle ACK)
2. ❌ **Změna CRC** → NACK s `<Reason>WC</Reason>`
3. ❌ **Změna NewValue** → NACK (CRC už neplatí)
4. ❌ **Změna ver/ID_Set/timestamps** → NACK

**ZÁVĚR**: 
- CRC je **proprietární algoritmus** (není to standardní CRC16/CRC32)
- BOX **striktně validuje** CRC proti celému frame
- Cloud má CRC **uložené v databázi** (předpočítané při vytvoření Setting záznamu)
- **Dummy hodnoty** (00000, 12345) jsou **POUZE v databázi** (cloud je nikdy nepošle BOXu!)

**💡 PŘELOMOVÉ ZJIŠTĚNÍ: CRC NEZÁVISÍ na NewValue!**

Z analýzy 93 Setting frames:
```
CRC=47999: 3 Settingy se STEJNÝM ver, ID, ID_Set, TSec ale RŮZNÝM NewValue (0 vs 3)
→ CRC je vypočítané JEN z metadata, NE z hodnoty!
```

**Důsledek**: Můžeme **vzít existující Setting frame** a **změnit POUZE `<NewValue>`** bez změny CRC!

**ŘEŠENÍ pro offline mode:**

**✅ VARIANTA A: Modifikace existujícího frame (DOPORUČENO)**
```python
# 1. Zachytit Setting z cloudu (např. MODE→3)
captured_frame = '''<Frame>
  <ID>13584179</ID>
  <ID_Device>2206237016</ID_Device>
  <ID_Set>1765136481</ID_Set>
  <ID_SubD>0</ID_SubD>
  <DT>07.12.2025 20:41:21</DT>
  <NewValue>3</NewValue>  <!-- Původní hodnota -->
  <Confirm>New</Confirm>
  <TblName>tbl_box_prms</TblName>
  <TblItem>MODE</TblItem>
  <ID_Server>5</ID_Server>
  <mytimediff>0</mytimediff>
  <Reason>Setting</Reason>
  <TSec>2025-12-07 19:47:07</TSec>
  <ver>10712</ver>
  <CRC>16664</CRC>  <!-- CRC zůstává STEJNÉ! -->
</Frame>'''

# 2. Změnit POUZE NewValue
modified_frame = captured_frame.replace('<NewValue>3</NewValue>', '<NewValue>0</NewValue>')

# 3. Poslat na BOX → mělo by fungovat! (CRC je stále validní)
```

**❓ K OTESTOVÁNÍ:**
1. Zachytit Setting frame pro MODE→3
2. Změnit `<NewValue>3</NewValue>` → `<NewValue>0</NewValue>`
3. Poslat na BOX a sledovat odpověď:
   - ✅ ACK → **CRC je NEZÁVISLÉ na NewValue!** → Můžeme měnit hodnoty!
   - ❌ NACK (WC) → CRC závisí i na NewValue → Potřebujeme přesný replay

**✅ VARIANTA B: Přesný replay (FALLBACK)**
- Zachytit celý frame, uložit, replayovat beze změny
- Funguje 100%, ale omezené na zachycené hodnoty

**❌ NEFUNGUJE:**
- Generování CRC (proprietární algoritmus, průměrná chyba ~17727)
- Reverse engineering (pokus o 100+ formulí selhal)

---

## Timeline of MODE Change (2025-12-10)

**User Action:** MODE change request sent to cloud at **21:40:08 CET**  
**BOX Confirmation:** MODE applied and confirmed at **21:43:04 CET**  
**Total Duration:** 2 minutes 56 seconds

## Detailed Communication Flow

### 1. User Initiates Change (21:40:08 CET)
- User sets MODE=3 on cloud interface
- Cloud stores the setting with timestamp `<DT>10.12.2025 21:40:08</DT>`
- **Cloud waits passively** - does NOT push to BOX immediately

### 2. BOX Polls for New Settings (21:42:51 CET)
**BOX → CLOUD** (Frame ID 75764, 20:42:51 UTC):
```xml
<Frame>
  <Result>IsNewSet</Result>
  <ID_Device>2206237016</ID_Device>
  <Lat>2845</Lat>
  <ver>09691</ver>
  <CRC>14579</CRC>
</Frame>
```

**Notes:**
- BOX sends IsNewSet poll every ~3-5 minutes (confirmed from earlier analysis)
- `<Lat>2845</Lat>` = latency 2.845 seconds (typical network delay)
- This is **table_name='unknown'** in database (not recognized as table)

### 3. Cloud Responds with Setting (21:42:51 CET)
**CLOUD → BOX** (Frame ID 75765, 20:42:51 UTC):
```xml
<Frame>
  <ID>13589888</ID>
  <ID_Device>2206237016</ID_Device>
  <ID_Set>1765399208</ID_Set>
  <ID_SubD>0</ID_SubD>
  <DT>10.12.2025 21:40:08</DT>         ← Original request timestamp!
  <NewValue>3</NewValue>                ← New MODE value
  <Confirm>New</Confirm>
  <TblName>tbl_box_prms</TblName>
  <TblItem>MODE</TblItem>
  <ID_Server>5</ID_Server>
  <mytimediff>0</mytimediff>
  <Reason>Setting</Reason>
  <TSec>2025-12-10 20:42:51</TSec>     ← Response generation time (UTC)
  <ver>61728</ver>
  <CRC>11021</CRC>
</Frame>
```

**Key Fields:**
- `<DT>` = Original user request time (2min 43s ago)
- `<NewValue>` = New parameter value
- `<TblName>` + `<TblItem>` = Which parameter to change (tbl_box_prms.MODE)
- `<Confirm>New</Confirm>` = Indicates new setting available
- `<Reason>Setting</Reason>` = Frame type identifier
- `<TSec>` = When cloud generated this response

### 4. BOX Confirms Receipt (21:42:58 CET)
**BOX → CLOUD** (Frame ID 75766, 20:42:58 UTC):
```xml
<Frame>
  <Result>ACK</Result>
  <Rdt>2025-12-10 21:42:48</Rdt>       ← BOX received time (CET)
  <Reason>Setting</Reason>              ← Special ACK type
  <Tmr>100</Tmr>
  <ver>15802</ver>
  <CRC>53982</CRC>
</Frame>
```

**Notes:**
- **Different from standard ACK!** Has `<Reason>Setting</Reason>` instead of `<ToDo>GetActual</ToDo>`
- `<Rdt>` = BOX receive timestamp
- This is **table_name='unknown'** (special ACK type)

### 5. Cloud Ends Setting Session (21:42:58 CET)
**CLOUD → BOX** (Frame ID 75767, 20:42:58 UTC):
```xml
<Frame>
  <Result>END</Result>
  <Time>2025-12-10 21:42:58</Time>     ← CET time
  <UTCTime>2025-12-10 20:42:58</UTCTime>
  <ToDo>GetActual</ToDo>
  <CRC>23691</CRC>
</Frame>
```

**Notes:**
- END frame with `<ToDo>GetActual</ToDo>` (different from IsNewSet END with CRC 34500)
- BOX returns to normal telemetry polling after this

### 6. BOX Applies Setting (21:43:04 CET)
**BOX → CLOUD** (Frame ID 75768, 20:43:04 UTC):
```xml
<Frame>
  <TblName>tbl_events</TblName>
  <Reason>Table</Reason>
  <ID_Device>2206237016</ID_Device>
  <ID_Set>836602968</ID_Set>
  <DT>2025-12-10 21:42:48</DT>
  <Type>Setting</Type>
  <Confirm>NoNeed</Confirm>
  <Content>Remotely : tbl_box_prms / MODE: [0]->[3]</Content>
  <ver>62135</ver>
  <CRC>53806</CRC>
</Frame>
```

**Notes:**
- Event logged in `tbl_events` with `<Type>Setting</Type>`
- `<Content>` shows old value [0] and new value [3]
- Followed immediately by restart events (HG IV-UPS Start, FMT on [3-No Limit] Start)

## Protocol Summary

### IsNewSet Polling Mechanism
1. **BOX initiates:** Sends `<Result>IsNewSet</Result>` every ~3-5 minutes
2. **Cloud responds:**
   - **No settings:** `<Result>END</Result>` with CRC 34500
   - **Settings available:** Setting frame with `<Reason>Setting</Reason>`

### Setting Delivery Flow
```
User → Cloud (store)
         ↓
BOX → IsNewSet poll →
         ↓
← Setting frame (with <NewValue>)
         ↓
BOX → Special ACK (<Reason>Setting</Reason>)
         ↓
← END frame
         ↓
BOX applies setting → tbl_events confirmation
```

### Timing Characteristics
- **Polling frequency:** ~3-5 minutes (IsNewSet)
- **Delivery latency:** 2min 43s in this case (depends on polling timing)
- **Worst case:** Up to 5 minutes (next poll cycle)
- **Apply time:** ~6 seconds after receiving Setting frame

## Setting Frame Structure

### Complete XML Schema
```xml
<Frame>
  <ID>integer</ID>                      <!-- Setting record ID -->
  <ID_Device>integer</ID_Device>        <!-- BOX identifier -->
  <ID_Set>integer</ID_Set>              <!-- Setting version/sequence -->
  <ID_SubD>integer</ID_SubD>            <!-- Sub-device (0 for main BOX) -->
  <DT>DD.MM.YYYY HH:MM:SS</DT>         <!-- User request timestamp (CET) -->
  <NewValue>value</NewValue>            <!-- New parameter value -->
  <Confirm>New</Confirm>                <!-- Status: New/Old/... -->
  <TblName>string</TblName>             <!-- Target table (tbl_box_prms, tbl_batt_prms, etc.) -->
  <TblItem>string</TblItem>             <!-- Target parameter (MODE, etc.) -->
  <ID_Server>integer</ID_Server>        <!-- Cloud server ID -->
  <mytimediff>integer</mytimediff>      <!-- Time difference in seconds? -->
  <Reason>Setting</Reason>              <!-- Frame type identifier -->
  <TSec>YYYY-MM-DD HH:MM:SS</TSec>     <!-- Response generation time (UTC) -->
  <ver>integer</ver>                    <!-- Version/checksum -->
  <CRC>integer</CRC>                    <!-- Frame CRC -->
</Frame>
```

### Known Parameters (Examples)
- **tbl_box_prms / MODE:** Operating mode (0=?, 3=No Limit, ...)
- **tbl_box_prms / ...:** Other BOX parameters
- **tbl_batt_prms / ...:** Battery parameters
- More tables likely exist

## Special ACK Response

### When BOX Receives Setting
```xml
<Frame>
  <Result>ACK</Result>
  <Rdt>YYYY-MM-DD HH:MM:SS</Rdt>       <!-- BOX receive time (CET) -->
  <Reason>Setting</Reason>              <!-- Identifies Setting ACK -->
  <Tmr>integer</Tmr>                    <!-- Timer/timeout? -->
  <ver>integer</ver>
  <CRC>integer</CRC>
</Frame>
```

**Different from Standard ACK:**
- Standard: `<Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC>`
- Setting: `<Result>ACK</Result><Reason>Setting</Reason><Rdt>...</Rdt><Tmr>100</Tmr><CRC>53982</CRC>`

## Offline Mode Implications

### Critical Requirements
1. **PROXY CANNOT intercept IsNewSet polling**
   - IsNewSet goes FROM BOX TO CLOUD (not cloud to BOX)
   - PROXY cannot trigger BOX to poll
   - BOX polls on its own schedule (~3-5 min)

2. **Setting delivery requires CLOUD**
   - Cloud stores settings and waits for BOX to poll
   - PROXY cannot generate Setting frames (doesn't know parameter schemas)
   - PROXY cannot push settings to BOX (no push mechanism exists)

3. **During Cloud Outage:**
   - BOX continues IsNewSet polling (will get no response)
   - User setting changes on cloud are queued there
   - When cloud returns, next IsNewSet poll delivers all pending settings
   - **NO PROXY INVOLVEMENT NEEDED**

### What PROXY Must Do
**NOTHING SPECIAL FOR SETTINGS!**

Setting changes are **cloud-managed**, not proxy-managed:
- Cloud stores pending settings
- BOX polls cloud when ready
- PROXY just forwards IsNewSet ↔ Setting frames transparently
- If cloud offline: BOX gets no response to IsNewSet (no settings delivered)
- When cloud returns: Next IsNewSet poll catches up automatically

### What PROXY Must NOT Do
❌ **Do NOT queue IsNewSet requests** - they're polls, not data
❌ **Do NOT try to generate Setting frames** - only cloud knows parameter schemas
❌ **Do NOT replay IsNewSet during reconnect** - BOX will poll on its own schedule
❌ **Do NOT try to ACK IsNewSet** - only cloud can respond with Setting or END

## Comparison with Telemetry Data

### Telemetry (tbl_actual, tbl_events, etc.)
- **Direction:** BOX → CLOUD (data upload)
- **Frequency:** BOX-initiated (tbl_actual ~9s, others ~5min)
- **Cloud response:** ACK required (blocks BOX)
- **Offline handling:** **PROXY MUST queue and ACK** (BOX deletes after ACK)
- **Replay:** PROXY sends to cloud when reconnected

### Settings (IsNewSet → Setting)
- **Direction:** BOX → CLOUD (poll), CLOUD → BOX (setting delivery)
- **Frequency:** BOX-initiated IsNewSet (~3-5min)
- **Cloud response:** Setting frame OR END frame
- **Offline handling:** **PROXY does NOTHING** (cloud manages queue)
- **Replay:** NOT NEEDED (cloud delivers when BOX polls again)

## Database Schema

### Settings in Database
Settings appear in TWO tables:

1. **frames.table_name = 'unknown'**
   - IsNewSet requests (BOX → CLOUD)
   - Setting ACK responses (BOX → CLOUD)
   - Not recognized as specific table

2. **frames.table_name = 'tbl_events'**
   - Confirmation events after applying setting
   - `<Type>Setting</Type>`
   - `<Content>Remotely : tbl_box_prms / MODE: [0]->[3]</Content>`

### Why 'unknown'?
Current proxy code only recognizes standard telemetry tables (tbl_actual, tbl_dc_in, etc.)
IsNewSet and Setting frames don't match these patterns → marked as 'unknown'

## Implementation Notes

### For Offline Mode (Phase 0-2)
**NO CHANGES NEEDED FOR SETTINGS!**

Settings are entirely cloud-managed:
- Cloud queues pending settings
- BOX polls when ready
- PROXY just forwards transparently
- Automatic catch-up when cloud returns

### For Future Enhancement (Phase 3+)
If we want to improve setting delivery during outages:

**Option 1: Local Setting Queue (Complex)**
- PROXY learns parameter schemas from cloud responses
- PROXY stores pending settings during outage
- PROXY generates Setting frames when BOX polls
- **RISK:** Schema changes, validation errors, conflicts

**Option 2: Notify User (Simple)**
- PROXY detects IsNewSet during offline
- PROXY sends MQTT notification: "Settings pending, cloud offline"
- User knows to wait for reconnection
- **SAFE:** No protocol modification

**Recommendation:** Option 2 (notify only) or do nothing
- Current behavior is acceptable (max 5min delay after reconnect)
- Complexity of Option 1 not worth the benefit
- Most settings are not time-critical

## Testing Strategy

### Verify Setting Delivery Works After Outage
1. Simulate cloud outage (block port 5003)
2. Change MODE on cloud interface during outage
3. Restore cloud connection
4. Wait for next IsNewSet poll (~3-5 min)
5. **Verify:** Setting delivered and applied
6. **Verify:** tbl_events shows confirmation

### Expected Behavior
- BOX continues IsNewSet polling during outage (no response)
- Cloud queues setting change
- After reconnect, next IsNewSet delivers setting
- **No data loss, automatic recovery**

## Conclusion

**Settings delivery is PULL-based (BOX polls), not PUSH-based.**

This means:
- ✅ No special offline handling needed in PROXY
- ✅ Cloud automatically manages setting queue
- ✅ BOX automatically catches up after outage
- ✅ Transparent forwarding is sufficient

**Focus offline mode development on TELEMETRY DATA (BOX → CLOUD), not settings.**
