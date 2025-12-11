# Implementation Guide - Offline Mode s Queue

> **Kompletní implementační dokumentace**  
> Všechny klíčové informace z analýzy databáze, protocol reverse engineering a performance testování.
> 
> **Zdroje:** `payloads_live.db` (73,765 framů, 2 dny provozu), ranní outage 2025-12-10 (08:27-09:59)

---

## 📋 Executive Summary

**Problem:** Cloud outages způsobují ztrátu dat (current proxy closes BOX socket)

**Solution:** Offline mode s POVINNÝM queueing v PROXY

**Why:** BOX vymazává data z fronty po obdržení ACK → PROXY musí mít backup

**Memory:** 444 KB / 80min outage (zanedbatelné)

**Implementation time:** 6-7 hodin (3 fáze)

---

## 🎯 Finální Strategie (po kompletní analýze)

### Hlavní zjištění z databáze (payloads_live.db)

**1. BOX má interní queueing**
- ✅ Evidence: 80min výpadku, BOX postupně posílal 17 batchů historických dat
- ✅ BOX ukládá data během výpadku
- ⚠️ **CRITICAL:** BOX vymaže data z fronty PO OBDRŽENÍ ACK!

**2. BOX transmission speed**
- Cloud ACK: **8-14ms** (average 10ms) - velmi rychlý
- BOX rate limit: **4-8 sekund** mezi framy - gentleman approach
- Pattern: BOX čeká na ACK (blocking), pak čeká dalších 4-8s před dalším framem

**3. ACK response patterns**
- 92.4% - Standard ACK: `<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>`
- 0.5% - Unstable ACK: `<Frame><Result>ACK</Result><CRC>54590</CRC></Frame>` (během outage)
- 0.3% - END: `<Frame><Result>END</Result><CRC>34500</CRC></Frame>` (IsNewSet bez settings)

**4. CRC není odvozené z obsahu**
- Cloud posílá konstantní CRC pro daný typ odpovědi
- **Ale:** Nemůžeme garantovat univerzálnost mezi BOXy
- **Řešení:** Learning mode (proxy se naučí z cloudu)

---

## 🔬 Protocol Analysis - Klíčové poznatky

### 1. Communication Frequency (normální provoz)

**Telemetrie z databáze (36,993 cloud responses):**

| Tabulka | Frekvence | Použití |
|---------|-----------|---------|
| `tbl_actual` | ~9.4s (27,351 framů) | Primární telemetrie (temp, humidity, atd.) |
| `tbl_dc_in` | ~299s (~5min) | DC vstup (FV panely) |
| `tbl_ac_in` | ~299s (~5min) | AC vstup (síť) |
| `tbl_ac_out` | ~299s (~5min) | AC výstup (spotřeba) |
| `tbl_batt` | ~299s (~5min) | Baterie |
| `tbl_boiler` | ~299s (~5min) | Boiler |
| `tbl_box` | ~299s (~5min) | Box status |
| `tbl_batt_prms` | ~393s (~6.5min) | Baterie parametry |
| `tbl_invertor_prms` | ~2665s (~44min) | Invertor parametry |
| `tbl_box_prms` | ~4119s (~69min) | Box parametry |
| `IsNewSet` | ~299s (~5min) | Polling pro nová nastavení |
| `IsNewFW` | ~299s (~5min) | Polling pro firmware update |
| `IsNewWeather` | ~299s (~5min) | Polling pro weather data |

**Pattern:**
```
Typický 5min batch (7-8 framů):
├─ tbl_dc_in
├─ tbl_ac_in
├─ tbl_ac_out
├─ tbl_batt
├─ tbl_boiler
├─ tbl_box
└─ tbl_actual (může být vícekrát během batche)

Mezi batchi: tbl_actual každých ~9s
```

**Implikace pro offline queue:**
```
80min outage:
├─ tbl_actual: ~960 framů (80*60/5 ≈ 960)
├─ 5min batches: ~16 batchů * 7 tabulek = 112 framů
└─ Total: ~1080 framů
```

### 2. ACK Response Patterns (36,993 analyzovaných odpovědí)

**Distribuce cloud odpovědí:**

| Typ | Count | % | CRC | Struktura |
|-----|-------|---|-----|-----------|
| ACK Standard | 34,186 | 92.4% | 00167 | `<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>` |
| ACK Unstable | 196 | 0.5% | 54590 | `<Frame><Result>ACK</Result><CRC>54590</CRC></Frame>` |
| END No Settings | 103 | 0.3% | 34500 | `<Frame><Result>END</Result><CRC>34500</CRC></Frame>` |
| END with ToDo | 16 | 0.04% | 21021 | `<Frame><Result>END</Result><ToDo>GetActual</ToDo><CRC>21021</CRC></Frame>` |
| NACK Wrong CRC | 6 | 0.02% | 21736 | `<Frame><Result>NACK</Result><Reason>WC</Reason><CRC>21736</CRC></Frame>` |
| END with Time | ~2,500 | 6.8% | varies | `<Frame><Result>END</Result><Time>...</Time><CRC>XXXXX</CRC></Frame>` |

**Kdy se používá které ACK:**

```python
# 92.4% případů - VŠECHNY data tabulky
if table_name in ['tbl_actual', 'tbl_dc_in', 'tbl_ac_in', 'tbl_ac_out', 
                   'tbl_batt', 'tbl_boiler', 'tbl_box', 'tbl_events', ...]:
    response = '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'

# 0.3% případů - IsNewSet když NEJSOU nová nastavení
elif table_name == 'IsNewSet' and no_new_settings:
    response = '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'

# 6.8% případů - IsNewSet když JSOU nová nastavení  
elif table_name == 'IsNewSet' and has_new_settings:
    response = '<Frame><Result>END</Result><Time>2025-12-10 09:52:22</Time><UTCTime>...</UTCTime><CRC>XXXXX</CRC></Frame>'
    # nebo kompletní Setting frame s <ID>, <ID_Set>, <NewValue>, atd.

# 0.5% případů - ACK unstable (během cloud connectivity issues)
elif during_cloud_instability:
    response = '<Frame><Result>ACK</Result><CRC>54590</CRC></Frame>'
```

**ACK Unstable (CRC 54590) - Kdy se objevuje:**

Evidence z databáze:
- Clustering během ranního outage (07:33-08:51): 130 výskytů
- Normální provoz: ~66 výskytů za 2 dny
- **Pattern:** 10x vyšší frekvence během cloud connectivity issues
- **Struktura:** Kratší (53 bytes vs 75 bytes), chybí `<ToDo>GetActual</ToDo>`
- **První výskyt:** Po duplicitním framu od BOXu (retransmission)

**Doporučení pro offline mode:**
```python
# Fáze 1: Použij pouze Standard ACK (pokrývá 92.4%)
ACK_STANDARD = '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'
END_NO_SETTINGS = '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'

# Fáze 2: Přidej learning mode pro správné CRC
# (cloud může mít jiné CRC pro jiné BOXy/firmware)
```

### 3. CRC Analysis - Univerzalita

**Database Evidence:**

```sql
-- Cloud posílá STEJNÉ ACK (CRC 00167) na různé requesty:
Frame 1: tbl_actual (ID_Set=836339341, temp=26.50) → ACK CRC=00167
Frame 3: tbl_dc_in  (ID_Set=836339400, FV_V1=245.2) → ACK CRC=00167  
Frame 5: tbl_ac_in  (ID_Set=836339400, ACI_V=231.8) → ACK CRC=00167
```

**Zjištění:**
- ✅ CRC **NENÍ odvozené** z obsahu BOX requestu
- ✅ Cloud posílá **konstantní CRC** pro daný typ odpovědi
- ⚠️ **Není garantováno** že CRC je stejné pro všechny BOXy/firmware

**Doporučená strategie:**

1. **Hardcoded fallback** (Fáze 1):
   ```python
   # Proven pro ID_Device=2206237016, firmware v.4.4.43.0716
   ACK_STANDARD = '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'
   END_NO_SETTINGS = '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'
   ```

2. **Learning mode** (Fáze 3 - recommended):
   ```python
   # Proxy pozoruje cloud odpovědi během forward mode
   # Ukládá naučené CRC do /data/learned_responses.json
   # Použije learned CRC v offline mode
   # Fallback na hardcoded pokud learning incomplete
   ```

**Proč learning:**
- ✅ BOX-agnostic (funguje s jakýmkoliv BOXem)
- ✅ Firmware-agnostic (adaptuje se na změny)
- ✅ Self-validating (detekuje pokud cloud změní CRC)
- ✅ Minimal overhead (jen observe během forward)

### 4. BOX Queueing Behavior - KRITICKÉ!

**Evidence z ranního outage (2025-12-10):**

**Timeline:**
```
07:33:58  BOX začíná posílat historická data (DT=08:30)
          Cloud odpovídá ACK 54590 (unstable)
          
08:51:40  Poslední ACK 54590
08:51:46  První END (cloud se stabilizuje)

08:59:07  První ACK 00167 (normální ACK)
          BOX dokončuje vyprázdnění fronty (DT=09:55)

09:01:00  BOX přepíná na real-time (DT=10:00:55, send=09:01:00, delta=5s)
```

**BOX fronta během výpadku:**
```
Data range: 08:30 - 09:55 (80 minut)
Batches: 17 batchů (každých 5 minut)
Frames: ~127 data framů

Posílání během výpadku: ~7-8 minut mezi batchi (pomalé)
Posílání po stabilizaci: ~35s na batch (rychlejší)
```

**🚨 KRITICKÉ ZJIŠTĚNÍ:**

```
Během offline mode:
┌─────────────────────────────────────────┐
│ BOX: "Mám data z 08:30"                 │
│ PROXY: "ACK" ✅                         │
│ BOX: VYMAŽE z fronty! 🚨                │
│ BOX: "Mám další data z 08:35"          │
│ PROXY: "ACK" ✅                         │
│ BOX: VYMAŽE z fronty! 🚨                │
└─────────────────────────────────────────┘

Po reconnect:
┌─────────────────────────────────────────┐
│ BOX fronta: [] (prázdná!)               │
│ Cloud: NEDOSTAL data! ❌                │
│ PROXY: Musela mít backup! ✅            │
└─────────────────────────────────────────┘
```

**Proč BOX vymaže frontu:**
- BOX používá **request-response blocking protocol**
- Po obdržení ACK → BOX předpokládá že **cloud má data**
- BOX **commit** (vymaže frame z fronty)
- BOX posílá **další frame** z fronty

**Implikace:**
- ❌ ~~Transparent mode (jen ACK, bez queue)~~ NEFUNGUJE!
- ✅ **PROXY MUSÍ frontovat!** (BOX fronta se vyprázdní po ACK)
- ✅ Po reconnect: PROXY replay → cloud dostane všechno

### 5. BOX Transmission Speed - Rate Limiting

**Timing Analysis po obnově spojení:**

```
┌──────────────┬────────────┬─────────────────┬──────────────┐
│ Frame        │ ACK Delay  │ Next Frame Delay│ Pattern      │
├──────────────┼────────────┼─────────────────┼──────────────┤
│ tbl_dc_in    │ 11ms       │ 5.5s            │ WAIT         │
│ tbl_ac_in    │ 9ms        │ 4.6s            │ WAIT         │
│ tbl_ac_out   │ 10ms       │ 4.7s            │ WAIT         │
│ tbl_batt     │ 14ms       │ 8.2s            │ WAIT (long)  │
│ tbl_boiler   │ 8ms        │ 7.6s            │ WAIT (long)  │
│ tbl_batt_prms│ 11ms       │ 4.8s            │ WAIT         │
│ tbl_box      │ 9ms        │ 7.1s            │ WAIT (long)  │
│ tbl_actual   │ 10ms       │ 23.7s           │ WAIT (VERY)  │
└──────────────┴────────────┴─────────────────┴──────────────┘

Cloud ACK average: 10ms ⚡ (velmi rychlý!)
BOX delay average: 4-8s 🐌 (interní rate limit)
```

**BOX Behavior Pattern:**

```python
# Pseudokod BOX logiky:
for frame in queue:
    send(frame)
    response = wait_for_ack(timeout=30s)  # Blocking!
    
    if response == ACK:
        commit()  # Vymaž z fronty
        sleep(4-8 seconds)  # 🐌 INTERNÍ RATE LIMIT!
        continue
    else:
        # Timeout nebo NACK
        retry_or_reconnect()
```

**Evidence:**
```
08:59:07.309  BOX → tbl_dc_in
08:59:07.320  CLOUD → ACK (+11ms) ✅
              BOX: "Dostal jsem ACK, commit, čekám 5.5s..."
08:59:12.777  BOX → tbl_ac_in (+5.5s later!)
```

**Proč je to důležité:**

1. **PROXY ACK může být pomalejší než cloud:**
   ```
   Cloud ACK: 10ms
   PROXY ACK: 100-500ms (Python overhead, queue operations)
   BOX delay: 4-8 sekund
   
   → BOX to ANI NEPOZNÁ! (jeho delay >> PROXY overhead)
   ```

2. **PROXY replay může být rychlejší:**
   ```
   BOX rate: 4-8s per frame
   PROXY rate: 0.1s per frame (40-80x rychlejší!)
   
   1080 framů:
   - BOX speed: 4320-8640s (72-144 minut)
   - PROXY speed: 108s (2 minuty)
   ```

3. **Timeout tolerance:**
   ```python
   # BOX čeká až 30s na ACK (estimate)
   # PROXY má dost času i při pomalejším ACK generování:
   
   PROXY_ACK_BUDGET = 2s  # Safe
   PROXY_ACK_WARNING = 5s  # Still OK
   PROXY_ACK_CRITICAL = 10s  # Risk timeout
   ```

### 6. TCP Connection Durability

**Evidence z databáze:**

```
Nejdelší connection: 208,174 sekund (57.8 hodin!)
├─ Frames: 2,218
├─ Start: 2025-12-07 20:29:01
└─ End: 2025-12-10 10:47:35

Average connection: 60-120 minut
Median: ~30 minut
```

**Důvod stability:**
- ✅ tbl_actual každých ~9s → keepalive traffic
- ✅ NAT/firewall: Session zůstává aktivní (data flow)
- ✅ Stateless protocol: Žádná session state
- ✅ CRC per frame: Integrity check

**Implikace pro offline mode:**

```python
# BOX socket může žít NEOMEZENĚ dlouho
# Dokud posíláme ACK každých ~9s, BOX je spokojený

# Zombie detection:
BOX_READ_TIMEOUT = 120  # sekund
# Pokud BOX nic nepošle 120s → považuj za dead
```

### 7. Socket Architecture - Independence

**Současný problém (main.py lines 742-819):**

```python
# ŠPATNĚ: Socket 1 (BOX) coupled s Socket 2 (CLOUD)
async def handle_connection(client_reader, client_writer):
    try:
        # Line 754-756: Cloud connection MUSÍ uspět
        target_reader, target_writer = await open_connection(
            TARGET_SERVER, TARGET_PORT
        )
    except Exception:
        # Line 789: Zavře BOX socket! ❌
        client_writer.close()
        return
    
    # Line 774: FIRST_EXCEPTION cancels both!
    await wait(tasks, return_when=FIRST_EXCEPTION)
```

**Správný přístup:**

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│  Socket 1: BOX ↔ PROXY (nezávislý lifecycle!)     │
│  ├─ Žije i když cloud nedostupný                  │
│  ├─ Timeout: 120s read (zombie detection)         │
│  └─ Close pouze při: BOX disconnect, timeout      │
│                                                     │
│  Socket 2: PROXY ↔ CLOUD (nezávislý lifecycle!)   │
│  ├─ Try/except: selhání NEPROPAGUJE do Socket 1   │
│  ├─ Timeout: 5s connect, 30s read                 │
│  ├─ Background probe: každých 60s test dostupnosti│
│  └─ Close pouze při: cloud disconnect, timeout    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Detekce cloud failure:**

```python
# 4 způsoby jak cloud může selhat:

1. Connection Refused (okamžitě)
   → socket.connect() raises ConnectionRefusedError

2. Connect Timeout (po 5s)
   → asyncio.wait_for(..., timeout=5) raises TimeoutError

3. TCP FIN (graceful)
   → reader.read() returns b'' (empty bytes)

4. TCP RST (ungraceful)
   → reader.read() raises ConnectionResetError

# Všechny MUSÍ vést k offline mode, NE k uzavření BOX socketu!
```

---

## ❌ Původní plán (CHYBNÝ!)

### ~~"Transparent mode - nech BOX frontovat"~~

**Myšlenka:** PROXY jen posílá ACK, BOX si data uloží a pošle po reconnect

**Problém:**
```
Offline:
BOX: "Mám data z 08:30"
PROXY: "ACK" ✅
BOX: VYMAŽE z fronty! 🚨

Reconnect:
BOX fronta: [] (prázdná)
Cloud: NEDOSTAL data z offline periody! ❌
```

**Proč to nefunguje:**
- BOX používá request-response blocking protocol
- Po obdržení ACK → BOX předpokládá že cloud má data
- BOX vymaže frame z fronty
- Po reconnect: BOX nemá co poslat (fronta prázdná)

---

## ✅ Správný přístup (POVINNÉ!)

### Fáze 1: Simple Queueing (MUST HAVE)

**PROXY MUSÍ frontovat data během offline mode!**

```python
class OIGProxy:
    def __init__(self):
        self.offline_queue = []  # In-memory queue
        self.mode = 'forward'    # 'forward' | 'offline'
        self.learner = ResponseLearner()  # Learning mode
    
    async def _run_offline_mode(self, box_reader, box_writer):
        """Offline mode: Queue + ACK + MQTT"""
        
        logger.warning("🔴 Entering OFFLINE mode (cloud unavailable)")
        self.mode = 'offline'
        
        while self.mode == 'offline':
            try:
                # Read from BOX (timeout 120s)
                data = await asyncio.wait_for(
                    box_reader.read(8192), 
                    timeout=120
                )
                
                if not data:
                    logger.warning("BOX disconnected during offline mode")
                    break
                
                frame = data.decode('utf-8', errors='ignore')
                table_name = self._extract_table_name(frame)
                
                # 1. ULOŽ DO FRONTY (CRITICAL!)
                self.offline_queue.append({
                    'frame': frame,
                    'table': table_name,
                    'timestamp': datetime.datetime.now().isoformat(),
                })
                logger.info(
                    f"📥 Queued: {table_name} "
                    f"(queue: {len(self.offline_queue)} frames)"
                )
                
                # 2. GENERUJ ACK (BOX vymaže z jeho fronty)
                ack = self._generate_offline_ack(frame)
                box_writer.write(ack.encode('utf-8'))
                await box_writer.drain()
                logger.debug(f"📤 Sent ACK for {table_name}")
                
                # 3. PUBLIKUJ DO MQTT (local monitoring funguje!)
                await self._publish_to_mqtt(frame)
                
            except asyncio.TimeoutError:
                logger.warning("⚠️ BOX timeout (120s), checking connection...")
                # BOX může být zombie - testuj read
                continue
            except Exception as e:
                logger.error(f"Error in offline mode: {e}")
                break
        
        logger.info(f"🔴 Exiting offline mode (queued {len(self.offline_queue)} frames)")
    
    def _generate_offline_ack(self, frame: str) -> str:
        """Generate ACK response during offline mode"""
        
        # Použij learned responses (pokud jsou k dispozici)
        responses = self.learner.get_fallback_responses()
        
        # Detekuj typ requestu
        if '<Result>IsNewSet</Result>' in frame:
            return responses.get(
                'END_NO_SETTINGS',
                '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'
            )
        else:
            return responses.get(
                'ACK_STANDARD',
                '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'
            )
    
    async def _replay_offline_queue(self, cloud_writer, cloud_reader):
        """Po reconnect: Pošli všechna data z fronty na cloud"""
        
        if not self.offline_queue:
            logger.info("✅ No offline queue to replay")
            return
        
        logger.warning(
            f"📤 Replaying {len(self.offline_queue)} frames to cloud "
            f"(estimated time: {len(self.offline_queue) * 0.1:.1f}s)"
        )
        
        replayed = 0
        failed = 0
        
        for i, item in enumerate(self.offline_queue):
            try:
                # Pošli frame na cloud
                cloud_writer.write(item['frame'].encode('utf-8'))
                await cloud_writer.drain()
                
                # Čekej na ACK od cloudu
                # (BOX už dostal ACK, takže nepotřebujeme přeposílat)
                try:
                    ack_data = await asyncio.wait_for(
                        cloud_reader.read(8192),
                        timeout=5.0
                    )
                    if not ack_data:
                        logger.warning(f"Cloud disconnected during replay at {i}/{len(self.offline_queue)}")
                        failed = len(self.offline_queue) - i
                        break
                except asyncio.TimeoutError:
                    logger.warning(f"Cloud ACK timeout for frame {i}")
                    # Pokračuj i bez ACK (best effort)
                
                replayed += 1
                
                # Rate limit (BOX má 4-8s, my můžeme být rychlejší)
                await asyncio.sleep(0.1)
                
                # Progress log každých 100 framů
                if (i + 1) % 100 == 0:
                    logger.info(f"📤 Replay progress: {i+1}/{len(self.offline_queue)}")
                
            except Exception as e:
                logger.error(f"Error replaying frame {i}: {e}")
                failed += 1
                # Pokračuj s dalšími framy (best effort)
        
        logger.warning(
            f"✅ Offline queue replay complete: "
            f"{replayed} sent, {failed} failed"
        )
        
        # Vyprázdni frontu
        self.offline_queue.clear()
```

**Memory requirements:**

```
80min výpadek:
├─ tbl_actual: 960 framů * 300 bytes = 288 KB
├─ Ostatní: 120 framů * 400 bytes = 48 KB
├─ Metadata: 1080 * 100 bytes = 108 KB
└─ Total: ~444 KB (včetně overhead)

4h výpadek:
└─ Total: ~1.3 MB (stále zanedbatelné!)
```

**Výhody:**
- ✅ Data NEJSOU ztracena (PROXY má backup)
- ✅ BOX dostává ACK (nevytváří reconnect loop)
- ✅ MQTT funguje offline (local monitoring)
- ✅ Jednoduché (bez timestamp analysis)
- ✅ Memory footprint minimální (< 2 MB i pro 4h)

**Nevýhody:**
- ⚠️ Replay trvá (1080 framů * 0.1s = ~2 minuty)
- ⚠️ Restart PROXY = ztráta fronty (řešení: Fáze 2)

---

### Fáze 2: Persistence Queue (Nice to have)

**Ulož frontu na disk → přežije restart PROXY**

```python
QUEUE_PERSISTENCE_PATH = "/data/offline_queue.json"

async def _save_queue_to_disk(self):
    """Periodicky ukládej frontu na disk"""
    with open(QUEUE_PERSISTENCE_PATH, 'w') as f:
        json.dump({
            'queue': self.offline_queue,
            'mode': self.mode,
            'saved_at': datetime.datetime.now().isoformat(),
        }, f)
    logger.debug(f"💾 Queue saved to disk ({len(self.offline_queue)} frames)")

def _load_queue_from_disk(self):
    """Načti frontu při startu"""
    if not os.path.exists(QUEUE_PERSISTENCE_PATH):
        return
    
    with open(QUEUE_PERSISTENCE_PATH, 'r') as f:
        data = json.load(f)
    
    self.offline_queue = data['queue']
    logger.warning(
        f"📖 Loaded offline queue from disk: {len(self.offline_queue)} frames "
        f"(saved at {data['saved_at']})"
    )
```

**Výhody:**
- ✅ Přežije restart PROXY
- ✅ Minimální overhead (save každých 10 framů)

---

### Fáze 3: Learning Mode (Recommended)

**Proxy se naučí správné CRC z cloudu**

```python
class ResponseLearner:
    """Učí se cloud odpovědi během forward mode"""
    
    def __init__(self):
        self.learned = {
            'ACK_STANDARD': None,
            'ACK_UNSTABLE': None,
            'END_NO_SETTINGS': None,
        }
        self.confidence = {}
    
    def observe(self, box_request: str, cloud_response: str):
        """Zaznamenej cloud response"""
        
        if '<Result>ACK</Result><ToDo>GetActual</ToDo>' in cloud_response:
            response_type = 'ACK_STANDARD'
        elif '<Result>ACK</Result><CRC>' in cloud_response and '<ToDo>' not in cloud_response:
            response_type = 'ACK_UNSTABLE'
        elif '<Result>END</Result><CRC>' in cloud_response and '<Time>' not in cloud_response:
            response_type = 'END_NO_SETTINGS'
        else:
            return
        
        if self.learned[response_type] is None:
            self.learned[response_type] = cloud_response
            self.confidence[response_type] = 1
            logger.info(f"✅ Learned {response_type}")
        elif self.learned[response_type] == cloud_response:
            self.confidence[response_type] += 1
    
    def get_fallback_responses(self) -> dict:
        """Vrať naučené nebo hardcoded responses"""
        
        if self.confidence.get('ACK_STANDARD', 0) < 5:
            # Nedostatečná confidence → hardcoded
            return {
                'ACK_STANDARD': '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
                'END_NO_SETTINGS': '<Frame><Result>END</Result><CRC>34500</CRC></Frame>',
            }
        
        return self.learned
    
    def save_to_disk(self):
        """Ulož naučené responses"""
        with open('/data/learned_responses.json', 'w') as f:
            json.dump({
                'responses': self.learned,
                'confidence': self.confidence,
                'updated_at': datetime.datetime.now().isoformat(),
            }, f)
```

**Integrace do forward mode:**

```python
async def _forward(self, reader_from, writer_to, direction):
    """Bidirectional forward WITH learning"""
    
    while True:
        data = await reader_from.read(8192)
        if not data:
            break
        
        message = data.decode('utf-8', errors='ignore')
        
        # Learning: Pozoruj cloud→BOX odpovědi
        if direction == 'cloud_to_box':
            last_request = getattr(self, '_last_box_request', None)
            if last_request:
                self.learner.observe(last_request, message)
        elif direction == 'box_to_cloud':
            self._last_box_request = message
        
        # Forward
        writer_to.write(data)
        await writer_to.drain()
```

**Výhody:**
- ✅ BOX-agnostic (funguje s jakýmkoliv BOXem)
- ✅ Self-validating (detekuje změny CRC)
- ✅ Minimální overhead (jen observe)

---

## 🚀 Implementation Checklist

### P0 - CRITICAL (bez tohoto = ztráta dat!)

**Core změny v main.py:**

- [ ] **Socket independence** (lines 742-819)
  ```python
  # Oddělit Socket 1 (BOX) od Socket 2 (CLOUD)
  # Cloud failure NESMÍ zavřít BOX socket
  # Odstranit FIRST_EXCEPTION pattern (line 774)
  # Přidat try/except isolation pro cloud connection
  ```

- [ ] **Offline queue** (nová property)
  ```python
  self.offline_queue = []  # List[dict] s frame + metadata
  self.mode = 'forward'    # 'forward' | 'offline'
  ```

- [ ] **_run_offline_mode()** (nová metoda)
  ```python
  # Queue BOX frames
  # Generate ACK (CRC 00167 / 34500)
  # Publish to MQTT
  # Timeout: 120s read (zombie detection)
  ```

- [ ] **_generate_offline_ack()** (nová metoda)
  ```python
  # IsNewSet → END (CRC 34500)
  # Ostatní → ACK (CRC 00167)
  # Fallback na hardcoded CRC
  ```

- [ ] **_replay_offline_queue()** (nová metoda)
  ```python
  # Po reconnect: pošli všechny queued frames na cloud
  # Rate limit: 0.1s per frame
  # Best effort: log failures, pokračuj
  # Clear queue po dokončení
  ```

- [ ] **Cloud reconnect detection** (background task)
  ```python
  # Probe každých 60s: test socket connect
  # Při success: switch offline → forward
  # Při failure: continue offline
  ```

- [ ] **Mode switching** (state machine)
  ```python
  # forward → offline: při cloud failure
  # offline → forward: při cloud reconnect + po replay
  # Atomic transition: avoid race conditions
  ```

**Úpravy existujících metod:**

- [ ] **handle_connection()** (lines 742-790)
  ```python
  # Try cloud connect, ale NEPROPAGUJ exception
  # Při failure → _run_offline_mode()
  # Při success → _run_forward_mode()
  # Background: _cloud_reconnect_probe()
  ```

- [ ] **_forward()** (lines 792-819)
  ```python
  # Přidat exception handling (NESMÍ crashnout BOX socket)
  # Při cloud disconnect → switch to offline mode
  # Continue forwarding BOX→CLOUD i při cloud errors (best effort)
  ```

### P1 - Important (robustnost)

- [ ] **ResponseLearner class** (nový modul)
  ```python
  # observe(box_request, cloud_response) během forward
  # learn ACK_STANDARD, ACK_UNSTABLE, END_NO_SETTINGS
  # save_to_disk() → /data/learned_responses.json
  # load_from_disk() při startu
  # get_fallback_responses() → dict s learned nebo hardcoded
  ```

- [ ] **Learning integration** do _forward()
  ```python
  # Pozoruj cloud→BOX responses
  # learner.observe(last_box_request, cloud_response)
  # Periodic save každých 100 framů
  ```

- [ ] **Queue persistence** (save/load)
  ```python
  # _save_queue_to_disk() → /data/offline_queue.json
  # _load_queue_from_disk() při startu
  # Periodic save každých 10 framů během offline
  # Přežije restart PROXY
  ```

- [ ] **MQTT publishing** během offline
  ```python
  # _publish_to_mqtt(frame) v _run_offline_mode()
  # Local monitoring funguje i bez cloudu
  # Parse frame → extract sensor values
  # Publish to topics: oig_local/{sensor_name}
  ```

- [ ] **Metrics & Logging**
  ```python
  # Queue size: INFO log každých 100 framů
  # Replay duration: WARNING log start/end
  # Failed frames: ERROR log s frame details
  # Mode transitions: WARNING log forward↔offline
  # Connection duration: INFO log při disconnect
  ```

### P2 - Nice to have (optimalizace)

- [ ] **Smart queueing** (timestamp analysis)
  ```python
  # Parse <DT> timestamp z frame
  # Detekuj real-time (age < 60s) vs historický (age > 60s)
  # Queue jen real-time (BOX replay ignoruj)
  # Ušetří memory: ~50% redukce queue size
  ```

- [ ] **Data compression** (selective queueing)
  ```python
  # tbl_actual: každý 10. frame (místo každého)
  # Redukce: 960 → 96 framů (~90%)
  # Trade-off: Granularita vs memory
  ```

- [ ] **Selective forwarding** (filter tables)
  ```python
  # Config: QUEUE_TABLES = ['tbl_actual', 'tbl_events']
  # Skip: tbl_*_prms (parameters, low priority)
  # Redukce: ~20% framů
  ```

- [ ] **Health monitoring** (diagnostics)
  ```python
  # Track: connection_duration, frames_per_minute
  # Detect: zombie connections (no data 120s)
  # Alert: long offline periods (> 30min)
  # Stats: /data/health_stats.json
  ```

- [ ] **Graceful shutdown** (cleanup)
  ```python
  # Signal handler: SIGTERM, SIGINT
  # Save queue to disk before exit
  # Close sockets gracefully
  # Final MQTT publish: status=offline
  ```

---

## 📊 Expected Performance

### Normal Operation (forward mode)
- BOX → PROXY → CLOUD: transparent relay
- Latency: +2-5ms (network overhead)
- Learning: observe cloud responses, save to disk

### Offline Mode
- BOX → PROXY: frame received
- PROXY: queue (0.1ms) + ACK (10ms) + MQTT (5ms)
- Total latency: ~15ms (BOX nepozná rozdíl, jeho rate limit = 4-8s)

### Reconnect & Replay
- Queue size: 1080 frames (80min outage)
- Replay speed: 0.1s/frame = 108s total (~2 minuty)
- Cloud ACK: 10ms per frame
- Success rate: > 99% (best effort, loguje failed frames)

### Memory Usage
- 80min outage: ~444 KB
- 4h outage: ~1.3 MB
- 24h outage: ~7.8 MB (extrém, ale OK)

---

## ⚠️ Known Limitations & Risks

### Restart PROXY během offline
- **Risk:** Fronta v paměti je ztracena
- **Mitigation:** Fáze 2 (persistence to disk)
- **Workaround:** Restart PROXY jen při forward mode

### Cloud reconnect během replay
- **Risk:** Replay přeruší, část dat nedoručena
- **Mitigation:** Log failed frames, možnost manual replay
- **Note:** BOX nemá co poslat (jeho fronta prázdná po ACK)

### BOX rate limit
- **Observation:** BOX posílá 4-8s mezi framy
- **Impact:** Replay je 10x rychlejší než real-time
- **Note:** PROXY ACK může být < 2s, BOX to nepozná

### CRC univerzalita
- **Uncertainty:** CRC 00167/34500 může být BOX-specific
- **Mitigation:** Learning mode (naučí se z cloudu)
- **Fallback:** Hardcoded CRC (proven pro ID_Device=2206237016)

---

## 🎯 Finální doporučení

**START WITH:** Fáze 1 (Simple Queueing)
- Implementace: 3-4 hodiny
- Risk: Nízké (simple in-memory queue)
- Testing: Simulovat cloud outage, verify queue + replay

**THEN ADD:** Fáze 3 (Learning Mode)
- Implementace: 2 hodiny
- Risk: Nízké (jen observe, nepřidává logiku)
- Benefit: BOX-agnostic, self-validating

**LATER:** Fáze 2 (Persistence)
- Implementace: 1 hodina
- Risk: Minimální (jen save/load JSON)
- Benefit: Přežije restart PROXY

**Total:** 6-7 hodin kompletní implementace včetně testování

---

## 📚 Reference Documentation

Tento dokument konsoliduje informace z následujících analýz:

### Protocol & Communication
- **`communication_map.md`** (338 lines)
  - Reverse engineering protokolu z 20,814 framů
  - ACK formáty, Request-Response patterns
  - Tabulky struktur, sensor mappings

- **`communication_flow.md`**
  - Per-table communication patterns (12 tabulek)
  - Frekvence analýza (tbl_actual ~9.4s, ostatní ~5min)
  - Outage pattern analysis (conn #118-125 vs #126)

### Connection & Socket Management
- **`connection_scenarios.md`**
  - 5 failure scenarios (timeout, disconnect, unsolicited, partition)
  - Decision matrix: všechny cloud/box state kombinace
  - Health check strategies (passive, active, TCP state)

- **`tcp_socket_management.md`**
  - Socket independence vysvětlení
  - 4 způsoby detekce cloud failure
  - Socket lifecycle management s diagramy

### Data & Queueing
- **`box_reconnect_behavior.md`** ⭐ KLÍČOVÝ
  - BOX queueing behavior (80min outage evidence)
  - Transmission speed analysis (4-8s rate limit)
  - Proč PROXY MUSÍ frontovat (BOX vymaže po ACK!)
  - Memory estimates (444 KB / 80min)

- **`ack_response_analysis.md`**
  - 36,993 cloud responses analyzed
  - ACK patterns: 92.4% standard, 0.5% unstable
  - CRC correlation s outage events
  - Alternative ACK investigation (CRC 54590)

### CRC & Learning
- **`crc_learning_strategy.md`**
  - Proč CRC může být BOX-specific
  - Learning mode design (observe + save + fallback)
  - ResponseLearner class implementation
  - Timeline: První běh → high confidence → persistence

### Implementation Code
- **`implementation_summary.md`** (TENTO DOKUMENT)
  - Konsolidace všech zjištění
  - Kompletní kódy pro P0/P1/P2
  - Implementation checklist
  - Performance expectations

---

## 🎯 Quick Start Guide

### 1. Před implementací
```bash
# Backup current main.py
cp addon/oig-proxy/main.py addon/oig-proxy/main.py.backup

# Review current code
grep -n "handle_connection\|_forward" addon/oig-proxy/main.py

# Check dependencies
grep "import" addon/oig-proxy/main.py | sort | uniq
```

### 2. Testing Strategy

**Simulace cloud outage:**
```bash
# Terminal 1: Run proxy
docker-compose up oig-proxy

# Terminal 2: Block cloud (simulate outage)
sudo iptables -A OUTPUT -d oigservis.cz -j DROP

# Wait 5-10 minut (BOX continues sending)

# Terminal 2: Restore cloud
sudo iptables -D OUTPUT -d oigservis.cz -j DROP

# Check logs: queue size, replay duration
docker-compose logs -f oig-proxy | grep "Queue\|Replay"
```

**Verify MQTT během offline:**
```bash
# Subscribe to MQTT
mosquitto_sub -h core-mosquitto -t "oig_local/#" -v

# Should see messages i během outage!
```

**Verify queue persistence:**
```bash
# Check queue file
docker exec oig-proxy cat /data/offline_queue.json | jq

# Restart proxy
docker-compose restart oig-proxy

# Check if queue loaded
docker-compose logs oig-proxy | grep "Loaded offline queue"
```

### 3. Monitoring

**Key metrics to watch:**
```python
# Logs to monitor:
"🔴 Entering OFFLINE mode"          # Cloud failure detected
"📥 Queued: {table} (queue: {n})"   # Queue growth
"📤 Replaying {n} frames"            # Replay start
"✅ Offline queue replay complete"   # Replay done
"🟢 Entering FORWARD mode"           # Normal operation restored

# Files to check:
/data/offline_queue.json             # Queue content
/data/learned_responses.json         # Learned CRC
/data/payloads.db                    # Capture database
```

**Performance baselines:**
```
Queue growth: ~13-14 frames/min (normal telemetry)
Memory usage: ~400 bytes/frame average
Replay speed: ~10 frames/sec (0.1s delay)
ACK generation: < 100ms (target < 2s)
```

### 4. Troubleshooting

**Problem: Queue roste příliš rychle**
```python
# Check: Možná BOX posílá replay (historická data)
# Solution: Implementuj timestamp analysis (P2 smart queueing)
# Workaround: Monitor queue size, alert při > 5000 framů
```

**Problem: Replay fails (cloud disconnect)**
```python
# Check: Partial replay completed?
# Solution: Log failed frames, možnost manual replay
# Workaround: Clear queue manually, BOX může mít backup
```

**Problem: BOX timeout během offline**
```python
# Check: Je BOX socket živý?
# Solution: Verify 120s timeout je dostatečný
# Workaround: Snížit timeout na 60s, rychlejší detekce
```

**Problem: CRC mismatch (jiný BOX)**
```python
# Check: Learned responses loaded?
# Solution: Implementuj learning mode (P1)
# Workaround: Update hardcoded CRC pro konkrétní BOX
```

---

## ⚠️ Critical Warnings

### 1. BOX Fronta se vyprázdní po ACK!
```
❌ NIKDY nepouštěj ACK bez queueing!
✅ VŽDY ulož frame PŘED odesláním ACK!
```

### 2. Socket Independence je KRITICKÁ!
```
❌ Cloud failure NESMÍ zavřít BOX socket!
✅ Try/except isolation pro cloud operations!
```

### 3. Replay MUSÍ být Best Effort!
```
❌ Replay failure NESMÍ crashnout proxy!
✅ Log failed frames, pokračuj s dalšími!
```

### 4. Queue Persistence před Restartem!
```
❌ Restart bez save = ztráta queue!
✅ Save to disk periodicky (každých 10 framů)!
```

### 5. Timeout Tolerance!
```
❌ BOX timeout < 30s = risk false disconnect!
✅ BOX read timeout = 120s (proven safe)!
```

---

## 🎉 Success Criteria

Po úspěšné implementaci:

✅ **Cloud outage NEZPŮSOBÍ ztrátu dat**
- BOX posílá framy → PROXY queue
- PROXY posílá ACK → BOX spokojený
- Po reconnect: PROXY replay → cloud dostane vše

✅ **BOX socket přežije cloud failure**
- Connection duration: > 57 hodin (proven)
- Timeout detection: 120s read
- Reconnect loop eliminated

✅ **MQTT funguje offline**
- Local monitoring continues
- Home Assistant displays data
- Alerting works i bez cloudu

✅ **Memory footprint je minimální**
- < 500 KB pro 80min outage
- < 2 MB pro 4h outage
- Periodic cleanup (po replay)

✅ **Performance je přijatelná**
- ACK latency: < 2s (BOX nepozná)
- Replay speed: ~2 minuty / 1080 framů
- No blocking operations

✅ **Robustní error handling**
- Cloud failures logged, nepropagují
- Replay failures logged, pokračuje
- Queue persistence přežije restart

---

## 📞 Need Help?

Refer to individual analysis documents for details:
- Protocol questions → `communication_map.md`
- Socket issues → `tcp_socket_management.md`
- Queue behavior → `box_reconnect_behavior.md`
- CRC problems → `crc_learning_strategy.md`

**Happy coding! 🚀**
