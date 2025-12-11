# BOX Reconnect Behavior Analysis

## 🎯 Otázka

**Jak BOX reaguje PO obnově spojení s cloudem?**
- Posílá historická data z doby výpadku?
- V jaké frekvenci?
- Vrací se k normální frekvenci?

## 📊 Database Evidence - Ranní výpadek 2025-12-10

### Timeline

**Výpadek:** ~07:33 - 08:59 (1h 26min)

```
07:33:58  BOX začíná posílat data z 08:30 (historická!)
          ↓ Cloud nestabilní (ACK 54590)
08:51:40  Poslední frame s ACK 54590
08:51:46  První END response (cloud se stabilizuje)
08:59:07  První ACK 00167 (návrat k normálu)
09:01:00  BOX přepíná na aktuální real-time data
```

### Fáze 1: Během výpadku (07:33 - 08:51)

**BOX posílá HISTORICKÁ DATA:**

```
Send Time  | Data Timestamp    | Tables
-----------|-------------------|------------------
07:33:58   | 2025-12-10 08:30  | dc_in, ac_in, ac_out, batt, boiler, box, actual
07:42:15   | 2025-12-10 08:35  | dc_in, ac_in, ac_out, batt, boiler, box, actual
07:43:20   | 2025-12-10 08:40  | dc_in, ac_in, ac_out, batt, boiler, box, actual
07:51:01   | 2025-12-10 08:45  | dc_in, ac_in, ac_out, batt, boiler, box, actual
07:56:22   | 2025-12-10 08:50  | dc_in, ac_in, ac_out, batt, boiler, box, actual
...
08:44:02   | 2025-12-10 09:40  | dc_in, ac_in, ac_out, batt, boiler, box, actual
08:51:13   | 2025-12-10 09:50  | ac_out, batt, boiler, box, actual (poslední batch)
```

**Klíčová zjištění:**
- ✅ BOX má **interní frontu** dat z výpadku!
- ✅ Posílá data v **5min batchích** (08:30, 08:35, 08:40...)
- ✅ Celkem **17 batchů** (08:30 - 09:50 = 80 minut dat)
- ✅ Cloud odpovídá **ACK 54590** (bez ToDo) - degradovaný mód!
- ⏱️ Frekvence: ~7-8 minut mezi batchi (BOX posílá pomalu)

**Pattern během výpadku:**
```
BOX má frontu: [08:30, 08:35, 08:40, 08:45, ..., 09:50]
               ↓
BOX: "Mám data z 08:30" → CLOUD: ACK 54590
     (čeká 7min)
BOX: "Mám data z 08:35" → CLOUD: ACK 54590
     (čeká 5min)
BOX: "Mám data z 08:40" → CLOUD: ACK 54590
     ...
```

### Fáze 2: Po stabilizaci (08:51 - 08:59)

**Cloud se stabilizuje:**

```
08:51:46  IsNewWeather → END (CRC 34500)
08:51:58  IsNewFW → END (CRC 34500)
08:52:22  IsNewSet → END with Time (cloud už funguje!)
```

**BOX pokračuje v odesílání fronty:**

```
08:59:07  tbl_dc_in   | 2025-12-10 09:55:00  → ACK 00167 ✅ (normální ACK!)
08:59:12  tbl_ac_in   | 2025-12-10 09:55:00  → ACK 00167
08:59:17  tbl_ac_out  | 2025-12-10 09:55:00  → ACK 00167
08:59:22  tbl_batt    | 2025-12-10 09:55:00  → ACK 00167
08:59:30  tbl_boiler  | 2025-12-10 09:55:00  → ACK 00167
08:59:37  tbl_batt_prms | 2025-12-10 09:55:00  → ACK 00167
08:59:42  tbl_box     | 2025-12-10 09:55:00  → ACK 00167
```

**Klíčová zjištění:**
- ✅ BOX DOKONČUJE frontu (poslední batch z 09:55)
- ✅ Cloud už posílá **normální ACK (00167)**
- ⏱️ Batch trvá ~35 sekund (7 tabulek)

### Fáze 3: Návrat k real-time (09:00+)

**BOX přepíná na aktuální data:**

```
Send Time  | Data Timestamp    | Delta (send - data)
-----------|-------------------|---------------------
08:59:49   | 2025-12-10 09:55  | -5min 11s (historické)
09:00:52   | 2025-12-10 10:00  | +52s (skoro real-time!)
09:01:00   | 2025-12-10 10:00:55 | +5s (REAL-TIME!)
09:01:05   | 2025-12-10 10:00:59 | +6s
09:01:10   | 2025-12-10 10:01:04 | +6s
09:01:18   | 2025-12-10 10:01:13 | +5s
09:01:26   | 2025-12-10 10:01:20 | +6s
09:01:31   | 2025-12-10 10:01:25 | +6s
...
```

**Klíčová zjištění:**
- ✅ BOX **vyprázdnil frontu** (poslední historický frame: 09:55)
- ✅ Přepnul na **real-time mode** (data timestamp + 5-6s = send time)
- ✅ Frekvence: **~5-8 sekund** (normální tbl_actual interval)
- ✅ BOX **automaticky** detekuje konec fronty

## 🔬 BOX Fronting Strategy Analysis

### BOX má interní queueing!

**Evidence:**
```
Výpadek začal: ~07:30 (odhad)
První historická data: 08:30
Poslední historická data: 09:55
Rozsah: 80 minut dat (17 batchů po 5min)
```

**Pattern:**
1. BOX detekuje cloud problémy (ACK 54590 nebo timeouts?)
2. Začne **ukládat data do fronty**
3. Průběžně se **pokouší odeslat**:
   - Posílá nejstarší batch
   - Čeká na ACK (dostává ACK 54590)
   - Po ~7min zkusí další batch
4. Když cloud odpovídá normálně (ACK 00167):
   - **Rychle vyprázdní frontu** (batche po ~35s)
   - Přepne na real-time mode

### BOX Recovery Timeline

```
T=0      Výpadek cloudu
         ↓
         BOX ukládá do fronty: [08:30, 08:35, 08:40, ...]
         ↓ (pokouší se posílat každých ~7min)
T+63min  Cloud částečně odpovídá (ACK 54590)
         BOX: "OK, cloud žije, ale je slow"
         ↓
T+78min  Cloud se stabilizuje (ACK 00167)
         BOX: "Cloud OK! Rychle vyprázdním frontu!"
         ↓ (posílá batche každých 35s)
T+86min  Fronta prázdná
         BOX: "Přepínám na real-time"
         ↓
T+87min  Real-time provoz (každých ~6s)
```

## 💡 Implikace pro Offline Mode

### ❌ Problem 1: Double Queueing (pokud PROXY frontuje)

**Pokud PROXY také frontuje:**

```
BOX Queue: [08:30, 08:35, 08:40, ...]
           ↓
PROXY Queue: [08:30, 08:35, 08:40, ...]
             ↓
CLOUD: Dostane každý batch 2x! 🚫
```

### ✅ Problem 2: BOX Queue Flush (KRITICKÉ!)

**Když PROXY odpovídá ACK během offline:**

```
Offline mode:
BOX: "Mám data z 08:30"
PROXY: "ACK" ← BOX myslí že cloud dostal data!
BOX: **VYMAŽE z fronty!** 🚨

Po reconnect:
BOX fronta: [] (prázdná!)
Cloud: NEDOSTAL data z offline periody! ❌
```

**Evidence z databáze:**
- BOX čeká na ACK (blocking protocol)
- Po ACK → BOX posílá další frame (= vymazal předchozí z fronty)
- Není žádný "retry" mechanismus (BOX nevyhodnocuje kvalitu ACK)

### ✅ Solution 1: Transparent Replay (NEFUNGUJE! ❌)

~~**PROXY pouze přeposílá, BOX se stará o frontu:**~~

```python
# TENTO PŘÍSTUP JE CHYBNÝ! ❌

async def _run_offline_mode(self, box_reader, box_writer):
    """Offline mode: ACK only, NO queueing"""
    
    while True:
        frame = await box_reader.read(8192)
        
        # 1. Pošli ACK (BOX potřebuje potvrzení)
        response = self._generate_offline_response(frame)
        box_writer.write(response.encode('utf-8'))  # ❌ BOX vymaže z fronty!
        
        # 2. Cloud NIKDY data nedostane! ❌
```

**Proč to nefunguje:**
- ❌ BOX vymaže data z fronty po ACK
- ❌ Cloud nikdy data nedostane (offline period = ztráta dat)
- ❌ Po reconnect: BOX nemá co poslat (fronta prázdná)

### ✅ Solution 2: Proxy Queueing (POVINNÉ! ✅)

**PROXY MUSÍ frontovat během offline mode!**

```python
async def _run_offline_mode(self, box_reader, box_writer):
    """Offline mode with MANDATORY queueing"""
    
    self.offline_queue = []
    
    while True:
        frame = await box_reader.read(8192)
        
        # 1. ULOŽ do fronty (CRITICAL!)
        self.offline_queue.append(frame)
        logger.info(f"📥 Queued: {table_name} (queue size: {len(self.offline_queue)})")
        
        # 2. Pošli ACK (BOX vymaže z JEHO fronty)
        ack = self._generate_offline_response(frame)
        box_writer.write(ack.encode('utf-8'))
        await box_writer.drain()
        
        # 3. Publikuj do MQTT (local monitoring)
        await self._publish_to_mqtt(frame)
        
        # BOX: "Dostal jsem ACK, vymažu z fronty a pošlu další"
        # PROXY: "Mám to ve SVOJÍ frontě, pošlu cloudu po reconnect"
```

**Po reconnect:**

```python
async def _replay_offline_queue(self, cloud_writer):
    """Po reconnect: Pošli všechno z PROXY fronty na cloud"""
    
    logger.info(f"📤 Replaying {len(self.offline_queue)} frames to cloud")
    
    for i, frame in enumerate(self.offline_queue):
        # Pošli frame
        cloud_writer.write(frame.encode('utf-8'))
        await cloud_writer.drain()
        
        # Čekej na ACK od cloudu
        # POZOR: Tady MUSÍME přijmout ACK, ale BOX ho už nepotřebuje!
        # Můžeme ho přečíst a zahodit, nebo použít timeout
        
        # BOX rate limit: ~5s mezi framy
        # Můžeme poslat rychleji (cloud zvládne), ale není nutné
        await asyncio.sleep(0.1)  # Malý delay pro rate limit
        
        if i % 100 == 0:
            logger.info(f"📤 Replay progress: {i}/{len(self.offline_queue)}")
    
    logger.info("✅ Offline queue replay complete")
    self.offline_queue.clear()
```

**Výhody:**
- ✅ Data NEJSOU ztracena (PROXY fronta je backup)
- ✅ BOX může vyprázdnit SVOJI frontu (dostává ACK)
- ✅ Cloud dostane všechna data (po reconnect)
- ✅ MQTT funguje offline (local monitoring)

**Nevýhody:**
- ⚠️ Paměťová náročnost (80min = ~300 KB, OK!)
- ⚠️ Replay trvá (1080 framů * 0.1s = 108s = 2 minuty)
- ⚠️ Složitější implementace (queue management)

### ⚡ Alternative: Smart Queueing (optimalizované)

**Kombinace BOX fronty + PROXY fronty:**

```python
async def _run_offline_mode(self, box_reader, box_writer):
    """Offline mode: Detekuj BOX replay vs real-time"""
    
    self.offline_queue = []
    
    while True:
        frame = await box_reader.read(8192)
        data_ts = self._extract_timestamp(frame)
        now = datetime.datetime.now()
        age = (now - data_ts).total_seconds()
        
        if age < 60:
            # REAL-TIME data (< 1min old)
            # BOX posílá aktuální data → MUSÍME frontovat!
            self.offline_queue.append(frame)
            logger.info(f"📥 Queued real-time: {table_name} (age: {age}s)")
        else:
            # HISTORICKÁ data (> 1min old)
            # BOX posílá z JEHO fronty → můžeme IGNOROVAT!
            # (BOX to pošle znovu po reconnect)
            logger.info(f"⏭️ Skipped BOX replay: {table_name} (age: {age}s)")
        
        # Vždy pošli ACK
        ack = self._generate_offline_response(frame)
        box_writer.write(ack.encode('utf-8'))
        await box_writer.drain()
```

**Scenario:**

```
Výpadek začíná 09:00:
├─ 09:00-09:05: BOX posílá real-time (age < 1min)
│               PROXY frontuje: [09:00:00, 09:00:05, 09:00:10, ...]
│
├─ 09:05+: BOX přepíná na replay (posílá starší data)
│          BOX: "Mám data z 08:30" (age = 30min)
│          PROXY: "To je starý, ignoruju" (BOX to pošle po reconnect)
│
└─ Reconnect 09:30:
   ├─ PROXY replay: [09:00:00, 09:00:05, ..., 09:05:00] (300 framů)
   └─ BOX replay: [08:30, 08:35, ..., 09:00] (automaticky!)
```

**Výhody:**
- ✅ Menší PROXY fronta (jen real-time, ne BOX replay)
- ✅ Bez duplikátů (BOX replay ignorován)
- ✅ Cloud dostane všechno (PROXY real-time + BOX replay)

**Nevýhody:**
- ❌ Složitější (timestamp analysis)
- ❌ Závislost na timestamp přesnosti
- ❌ Musíš vědět KDY BOX přepíná na replay mode

## 🎯 Doporučení - REVISED!

### ~~Fáze 1: Transparent (NEFUNGUJE!)~~

~~**Nech BOX zpracovat frontu!**~~ ❌

**PROBLÉM:** BOX vymaže frontu po PROXY ACK!

### Fáze 1: Simple Queueing (POVINNÉ!)

**PROXY MUSÍ frontovat!**

```python
# Offline mode
- Přijmi frame od BOX
- ULOŽ DO FRONTY! ← CRITICAL
- Pošli ACK (BOX vymaže z jeho fronty)
- Publikuj do MQTT

# Po reconnect
- Replay PROXY queue na cloud
- BOX: může posílat real-time (jeho fronta prázdná)
- Cloud: dostane vše z PROXY fronty
```

**Proč:**
- ✅ Data NEJSOU ztracena
- ✅ BOX dostává ACK (nepřipojuje se znovu)
- ✅ Jednoduché (bez timestamp analysis)
- ✅ MQTT funguje offline

**Memory:**
```
80min výpadek:
├─ tbl_actual: 960 framů * 300 bytes = 288 KB
├─ Ostatní: 120 framů * 400 bytes = 48 KB
└─ Total: ~336 KB (zanedbatelné!)
```

### Fáze 2: Smart Queueing (optimalizace)

**Pouze pokud:**
- Chcete minimalizovat PROXY queue
- Jste si jisti timestamp přesností
- Víte kdy BOX přepíná na replay

**Implementace:**
- Detekuj real-time vs historická (timestamp delta)
- Frontuj pouze real-time
- Ignoruj BOX replay (> 1min old)
- Po reconnect: PROXY queue + BOX automaticky pošle replay

```python
async def _run_offline_mode(self, box_reader, box_writer):
    """Offline mode: ACK only, NO queueing"""
    
    offline_queue = []  # Ukládáme pro MQTT only!
    
    while True:
        data = await box_reader.read(8192)
        frame = data.decode('utf-8')
        
        # 1. Pošli ACK (BOX potřebuje potvrzení)
        response = self._generate_offline_response(frame)
        box_writer.write(response.encode('utf-8'))
        await box_writer.drain()
        
        # 2. Publikuj do MQTT (local monitoring)
        await self._publish_to_mqtt(frame)
        
        # 3. NEPŘEPOSÍLEJ na cloud! (BOX si to zopakuje po reconnect)
        logger.info(f"📥 Offline: Received {table_name}, sent ACK, published to MQTT")
```

**Po obnově cloudu:**

```python
async def _run_forward_mode(self, box_reader, box_writer, cloud_reader, cloud_writer):
    """Forward mode: Transparent relay"""
    
    # BOX automaticky pošle historická data!
    # PROXY jen přeposílá obousměrně
    
    await asyncio.gather(
        self._forward(box_reader, cloud_writer, 'box_to_cloud'),
        self._forward(cloud_reader, box_writer, 'cloud_to_box'),
    )
```

**Výhody:**
- ✅ BOX se stará o frontu (už to umí!)
- ✅ Žádné duplikáty
- ✅ PROXY jednoduchá
- ✅ MQTT funguje i offline

### ✅ Solution 2: Proxy Queueing (advanced)

**Pokud chceš PROXY queue:**

```python
async def _run_offline_mode(self, box_reader, box_writer):
    """Offline mode with queueing"""
    
    while True:
        frame = await box_reader.read(8192)
        
        # Detekuj jestli BOX posílá REAL-TIME nebo HISTORICKÁ data
        data_ts = self._extract_timestamp(frame)
        now = datetime.datetime.now()
        
        if (now - data_ts).total_seconds() < 60:
            # REAL-TIME data (< 1min old) → přidej do fronty
            self.offline_queue.append(frame)
            logger.info(f"📥 Queued real-time: {table_name} @ {data_ts}")
        else:
            # HISTORICKÁ data (> 1min old) → BOX replay, IGNORUJ!
            logger.info(f"⏭️ Skipping BOX replay: {table_name} @ {data_ts}")
        
        # Vždy pošli ACK
        box_writer.write(self._generate_ack(frame).encode('utf-8'))
        await box_writer.drain()
```

**Po reconnect:**

```python
async def _replay_queue_to_cloud(self, cloud_writer):
    """Po reconnect: pošli pouze PROXY queue (ne BOX queue!)"""
    
    logger.info(f"📤 Replaying {len(self.offline_queue)} queued frames to cloud")
    
    for frame in self.offline_queue:
        cloud_writer.write(frame.encode('utf-8'))
        await cloud_writer.drain()
        
        # Čekej na ACK?
        # NE - BOX už dostal ACK, cloud nepotřebuje odpovídat
        await asyncio.sleep(0.1)  # Rate limit
    
    self.offline_queue.clear()
    logger.info("✅ Queue replay complete")
```

**Výhody:**
- ✅ PROXY má kontrolu nad tím, co jde na cloud
- ✅ Můžeš filtrovat duplicity (BOX replay vs PROXY queue)
- ✅ Můžeš komprimovat (např. pouze každý 10. tbl_actual)

**Nevýhody:**
- ❌ Složitější implementace
- ❌ Musíš detekovat BOX replay (timestamp analysis)
- ❌ Risk duplikátů pokud se spletou časová razítka

## 🎯 Doporučení

### Fáze 1: Transparent (KISS principle)

**Nech BOX zpracovat frontu!**

```python
# Offline mode
- Přijmi frame od BOX
- Pošli ACK (BOX je spokojený)
- Publikuj do MQTT (local monitoring)
- NEUKLÁDEJ do fronty (BOX to má)

# Po reconnect
- Jen forward BOX ↔ CLOUD
- BOX automaticky pošle historická data
- PROXY je transparent
```

**Proč:**
- ✅ BOX **už to umí** (evidence z databáze!)
- ✅ Jednoduché (bez timestamp analysis)
- ✅ Bez rizika duplikátů
- ✅ MQTT funguje offline

### Fáze 2: Proxy Queue (pokud potřebujete)

**Pouze pokud:**
- Chcete komprimovat data (ne všechny tbl_actual)
- Chcete filtrovat některé tabulky
- Chcete upravovat před odesláním

**Implementace:**
- Detekuj real-time vs historická data (timestamp delta)
- Ukládej pouze real-time do PROXY queue
- Ignoruj BOX replay (> 1min old)
- Po reconnect: pošli PROXY queue + nech BOX poslat jeho queue

## 📊 Performance Metrics - BOX Transmission Speed

### Critical Discovery: BOX má rate limit! ⚠️

**Timing Analysis po obnově spojení:**

```
Frame Send Pattern (09:00:13 - 09:00:52):
┌─────────────┬────────────┬─────────────────┬──────────────┐
│ Table       │ ACK Delay  │ Next Frame Delay│ Pattern      │
├─────────────┼────────────┼─────────────────┼──────────────┤
│ tbl_dc_in   │ 11ms       │ 5.5s            │ WAIT         │
│ tbl_ac_in   │ 9ms        │ 4.6s            │ WAIT         │
│ tbl_ac_out  │ 10ms       │ 4.7s            │ WAIT         │
│ tbl_batt    │ 14ms       │ 8.2s            │ WAIT (long)  │
│ tbl_boiler  │ 8ms        │ 7.6s            │ WAIT (long)  │
│ tbl_batt_prms│ 11ms      │ 4.8s            │ WAIT         │
│ tbl_box     │ 9ms        │ 7.1s            │ WAIT (long)  │
│ tbl_actual  │ 10ms       │ 23.7s           │ WAIT (VERY)  │
└─────────────┴────────────┴─────────────────┴──────────────┘

Average ACK response: 10ms (cloud je rychlý!)
Average next frame: 4-8 sekund (BOX ČEKÁ!)
```

**Klíčové zjištění:**

1. **Cloud ACK je RYCHLÝ:** 8-14ms (average ~10ms)
2. **BOX NEČEKÁ na ACK delay!** Má **internal rate limit**:
   - Běžné tabulky: **4.5-8 sekund** mezi framy
   - Po tbl_actual: **23.7 sekund** (zvláštní pauza)
3. **BOX neposílá "co to dalo"!** Má gentlemanský přístup 🎩

### BOX Behavior Pattern

```python
# Pseudokod BOX logiky:

for frame in queue:
    send(frame)
    response = wait_for_ack(timeout=30s)  # Čeká na ACK
    
    if response == ACK:
        # BOX DOSTAL ACK (10ms), ALE...
        sleep(4-8 seconds)  # ... ČEKÁ PŘED DALŠÍM FRAMEM! 🐌
        continue
    else:
        # Timeout nebo error
        break
```

**Evidence:**

```
08:59:07.309  BOX → tbl_dc_in
08:59:07.320  CLOUD → ACK (+11ms) ✅
              BOX: "Dostal jsem ACK, ale počkám 5.5s..."
08:59:12.777  BOX → tbl_ac_in (+5.5s later)
08:59:12.786  CLOUD → ACK (+9ms) ✅
              BOX: "Dostal jsem ACK, ale počkám 4.6s..."
08:59:17.420  BOX → tbl_ac_out (+4.6s later)
```

### Proč BOX čeká?

**Možné důvody:**

1. **Ochrana serveru:** Nechce zahlcovat cloud
2. **Firmware limit:** Hardcoded delay mezi framy
3. **Network courtesy:** Dává čas ostatním BOXům
4. **Resource management:** Šetří vlastní CPU/paměť

**Není to:**
- ❌ Čekání na ACK (ACK přijde za 10ms, BOX čeká dalších 4-8s)
- ❌ Network latency (RTT je 10ms, ne 5s)
- ❌ Cloud throttling (cloud odpovídá okamžitě)

### Implikace pro Offline Mode

**PROXY ACK MŮŽE být pomalejší než 10ms!**

```python
# BOX pattern:
send_frame()
wait_for_ack(timeout=30s)  # Blokující!
sleep(4-8s)  # Interní delay
next_frame()

# Pokud PROXY ACK trvá 500ms místo 10ms:
# - BOX čeká 500ms na ACK
# - Pak čeká dalších 4-8s před dalším framem
# - Total: 5-9s mezi framy (vs normálních 4.5-8.5s)
# - Zpomalení: ~500ms na frame
# - Za 80min: ~0.5s * 960 frames = 8 minut navíc
```

**NENÍ kritické!** BOX má tak velký interní delay (4-8s), že:
- PROXY ACK < 1s → zanedbatelné
- PROXY ACK < 2s → OK
- PROXY ACK < 5s → BOX to ani nepozná (interní delay větší)

### BOX Recovery Performance

**Teoretické maximum (kdyby BOX neměl rate limit):**

```
1080 framů * 10ms ACK = 10.8 sekund
Real-time: 80 minut = 4800 sekund
Speedup: 444x! 🚀
```

**Skutečnost (s BOX rate limit):**

```
Výpadek: 80 minut dat (17 batchů)
Recovery: 8 minut (vyprázdnění fronty)
Ratio: 10:1 (10x rychlejší replay než real-time)

Batch size: 7 tabulek (~3-5 KB)
Batch interval: ~35 sekund (7 tabulek * 5s delay)
Frame rate: ~5 sekund/frame (gentlemanský rate)
Throughput: ~100 bytes/sec (slow, ale spolehlivé)
```

**BOX strategy:**
- ✅ Spolehlivé (čeká na ACK)
- ✅ Šetrné (neflooding cloud)
- ❌ Pomalé (10x rychleji než real-time, ale mohlo by být 444x!)

**MQTT Only (bez cloud queue):**

```
Výpadek: 80 minut
Frames: ~960 tbl_actual (každých 5s) + 120 ostatních
Total: ~1080 frames
Size: ~300 KB (průměr 300 bytes/frame)
Memory: 300 KB pro celý outage (zanedbatelné!)
```

## 🚀 Implementation Priority

**P0 - Immediate:**
- ✅ Offline mode: ACK only
- ✅ MQTT publishing (local monitoring)
- ✅ NO queueing (BOX se postará)

**P1 - Soon:**
- 🔜 Learning mode (CRC from cloud)
- 🔜 Timestamp analysis (detekce BOX replay)
- 🔜 Metrics (kolik dat BOX poslal po reconnect)

**P2 - Later:**
- 🔜 PROXY queue (pokud je potřeba)
- 🔜 Data compression (redukce tbl_actual)
- 🔜 Selective forwarding (filtrování tabulek)

---

## 🎯 Závěr

**BOX už má queueing!** 🎉

- ✅ Ukládá data během výpadku
- ✅ Postupně je posílá cloudu
- ✅ Přepne na real-time po vyprázdnění fronty
- ✅ Funguje to 80+ minut výpadku

**ALE: PROXY MUSÍ TAKÉ frontovat!** 🚨

- ❌ ~~Transparent mode NEFUNGUJE!~~ BOX vymaže frontu po PROXY ACK
- ✅ **PROXY MUSÍ ukládat data během offline** (BOX fronta se vyprázdní)
- ✅ Po reconnect: PROXY pošle svou frontu na cloud
- ✅ Memory: 336 KB / 80min (zanedbatelné)

**Finální strategie:**

```
Offline mode:
├─ BOX → PROXY: Data frame
├─ PROXY: Ulož do fronty ← CRITICAL!
├─ PROXY → BOX: ACK (BOX vymaže z jeho fronty)
└─ PROXY → MQTT: Publish (local monitoring)

Po reconnect:
├─ PROXY → CLOUD: Replay fronty (1080 framů * 0.1s = 2min)
├─ BOX → CLOUD: Real-time data (normální provoz)
└─ Result: Cloud má všechna data! ✅
```

**Implementační priority:**

**P0 - CRITICAL (bez tohoto = ztráta dat!):**
- ✅ PROXY queue (in-memory list)
- ✅ Offline mode ACK generation
- ✅ Queue replay po reconnect

**P1 - Important:**
- 🔜 Learning mode (CRC from cloud)
- 🔜 MQTT publishing (local monitoring)
- 🔜 Persistence queue to disk (survive restart)

**P2 - Nice to have:**
- 🔜 Smart queueing (timestamp analysis)
- 🔜 Data compression (redukce tbl_actual)
- 🔜 Selective forwarding (filtrování tabulek)

**BOX transmission performance:**
- Cloud ACK: **10ms** (rychlý!)
- BOX rate limit: **4-8 sekund** mezi framy
- PROXY ACK může být **< 2s** (BOX to ani nepozná)
- Replay speed: **0.1s/frame** (10x rychlejší než BOX real-time)
