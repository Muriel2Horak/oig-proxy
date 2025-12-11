# Analýza frontování dat během výpadku cloudu

## Klíčové zjištění z protokolu

### 1. **Request-Response pattern**

```
08:59:07.309  BOX → PROXY: tbl_dc_in frame
08:59:07.320  PROXY → BOX: ACK (11ms delay)

08:59:12.777  BOX → PROXY: tbl_ac_in frame
08:59:12.786  PROXY → BOX: ACK (9ms delay)

08:59:17.420  BOX → PROXY: tbl_ac_out frame
08:59:17.430  PROXY → BOX: ACK (10ms delay)
```

**Závěr:** BOX **NEPOSÍLÁ** další frame dokud nedostane ACK!

### 2. **ACK je POTVRZENÍ přijetí dat**

- Cloud posílá ACK okamžitě po přijetí každého frame (8-15ms)
- BOX čeká na ACK před odesláním dalšího frame
- **ACK = "Data jsem přijal a zpracoval"**

### 3. **Co se stane při výpadku cloudu?**

#### Současný stav (bez fallback módu):
```
BOX → PROXY: tbl_actual frame
PROXY → CLOUD: (connection failed)
PROXY: closes BOX socket ❌
BOX: detects disconnect → reconnect loop
```

**Problém:** Data jsou ztracená navždy! 📉

#### S fallback módem (bez frontování):
```
BOX → PROXY: tbl_actual frame
PROXY: cloud offline, send local ACK ✅
BOX: happy, continues sending

PROXY → MQTT: publish data ✅
BOX → PROXY: next frame (after ~9s)
```

**Výsledek:** Data jdou do MQTT, ale CLOUD je nikdy neuvidí 📊

#### S frontováním (queue mode):
```
BOX → PROXY: tbl_actual frame
PROXY: cloud offline, QUEUE frame + send ACK ✅
BOX: happy, continues sending

PROXY → MQTT: publish data ✅
PROXY → QUEUE: store frame for replay

--- cloud is back ---
PROXY: cloud online! 🎉
PROXY → CLOUD: replay queued frames
CLOUD → PROXY: ACK for each queued frame
```

**Výsledek:** Data v MQTT i v CLOUDu! 🚀

---

## Otázka 1: Je tam potvrzení z cloudu?

**ANO! ACK je potvrzení!** ✅

```xml
BOX → CLOUD: <Frame><TblName>tbl_actual</TblName>...data...</Frame>
CLOUD → BOX: <Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>
```

**Význam:**
- ACK = "Data jsem přijal a zpracoval"
- BOX čeká na ACK před odesláním dalšího frame
- Bez ACK se BOX "zasekne" a čeká (nebo timeout)

---

## Otázka 2: Musíme při výpadku taky posílat ACK?

**ANO, ABSOLUTNĚ!** ✅✅✅

### Proč musíme posílat ACK během offline módu:

1. **BOX čeká na ACK**
   - Pokud nedostane ACK → timeout → disconnect
   - Každý frame MUSÍ dostat ACK odpověď

2. **ACK je součástí protokolu**
   - Není to jen "nice to have"
   - Je to **povinná součást** request-response cyklu

3. **Bez ACK = mrtvé spojení**
   ```
   BOX → PROXY: tbl_actual
   PROXY: ... (silence) ...
   BOX: timeout after 30s? 60s?
   BOX: disconnect → reconnect loop ❌
   ```

### Správný offline mód:

```python
async def _run_offline_mode(self, conn_id, box_reader, box_writer):
    '''Offline mode with ACK generation'''
    
    while True:
        # Read frame from BOX
        data = await asyncio.wait_for(box_reader.read(8192), timeout=120.0)
        
        # Parse frame
        frame = data.decode('utf-8', errors='ignore')
        table_name = self._extract_table_name(frame)
        
        # CRITICAL: Send ACK immediately!
        ack_response = self._generate_ack(table_name)  # ACK or END
        box_writer.write(ack_response.encode('utf-8'))
        await box_writer.drain()
        
        # THEN process (MQTT, queue, etc.)
        await self._process_frame(frame, conn_id, table_name)
```

**Pořadí je klíčové:**
1. ✅ Přijmi frame od BOXu
2. ✅ **OKAMŽITĚ** pošli ACK zpět
3. ✅ Teprve pak zpracuj (MQTT, fronta, atd.)

---

## Frontování dat - možnosti

### Možnost A: Bez frontování (Simple)

```python
async def _process_frame(self, frame, conn_id, table_name):
    # Publish to MQTT
    await self._publish_to_mqtt(frame, table_name)
    
    # That's it! No cloud, no queue
    logger.info(f"[#{conn_id}] Published {table_name} to MQTT (cloud offline)")
```

**Výhody:**
- Jednoduchá implementace
- Žádná paměťová zátěž
- Data okamžitě v MQTT

**Nevýhody:**
- Cloud nikdy neuvidí data z outage periody
- Chybějící historie na cloudu
- Potenciální reporting gaps

---

### Možnost B: S frontováním (Resilient)

```python
class OfflineQueue:
    def __init__(self):
        self.queue = []  # List of (timestamp, frame, table_name)
        self.max_size = 10000  # Max 10k frames (~4 hours outage)
    
    def add(self, frame, table_name):
        '''Add frame to queue'''
        if len(self.queue) < self.max_size:
            self.queue.append((time.time(), frame, table_name))
            logger.debug(f"Queued {table_name}, queue size: {len(self.queue)}")
        else:
            # Queue full - drop oldest
            dropped = self.queue.pop(0)
            self.queue.append((time.time(), frame, table_name))
            logger.warning(f"Queue full! Dropped {dropped[2]} from {dropped[0]}")
    
    def get_all(self):
        '''Get all queued frames and clear'''
        frames = self.queue.copy()
        self.queue.clear()
        return frames


async def _run_offline_mode(self, conn_id, box_reader, box_writer):
    '''Offline mode with queueing'''
    
    queue = OfflineQueue()
    
    while True:
        # Read frame
        data = await asyncio.wait_for(box_reader.read(8192), timeout=120.0)
        frame = data.decode('utf-8', errors='ignore')
        table_name = self._extract_table_name(frame)
        
        # Send ACK immediately
        ack = self._generate_ack(table_name)
        box_writer.write(ack.encode('utf-8'))
        await box_writer.drain()
        
        # Publish to MQTT
        await self._publish_to_mqtt(frame, table_name)
        
        # Add to queue
        queue.add(frame, table_name)
        
        # Check cloud status (non-blocking)
        if self.cloud_is_online:
            # Switch to forward mode
            await self._replay_queue(queue)
            break


async def _replay_queue(self, queue):
    '''Replay queued frames to cloud'''
    
    frames = queue.get_all()
    
    if not frames:
        logger.info("No queued frames to replay")
        return
    
    logger.info(f"Replaying {len(frames)} queued frames to cloud...")
    
    # Open cloud connection
    cloud_reader, cloud_writer = await asyncio.open_connection(
        TARGET_SERVER, TARGET_PORT
    )
    
    try:
        for ts, frame, table_name in frames:
            # Send frame to cloud
            cloud_writer.write(frame.encode('utf-8'))
            await cloud_writer.drain()
            
            # Wait for ACK from cloud
            response = await asyncio.wait_for(
                cloud_reader.read(8192),
                timeout=5.0
            )
            
            # Verify ACK
            if b'<Result>ACK</Result>' in response:
                logger.debug(f"Cloud ACKed queued {table_name} from {ts}")
            else:
                logger.warning(f"Cloud did not ACK queued {table_name}")
        
        logger.info(f"Successfully replayed {len(frames)} frames to cloud")
    
    except Exception as e:
        logger.error(f"Error replaying queue: {e}")
        # Re-queue failed frames?
    
    finally:
        cloud_writer.close()
        await cloud_writer.wait_closed()
```

**Výhody:**
- Cloud dostane všechna data (i z outage)
- Kompletní historie
- Data persistence

**Nevýhody:**
- Složitější implementace
- Paměťová zátěž (10k frames = ~10MB)
- Replay logika

---

### Možnost C: Frontování do SQLite (Persistent)

```python
import aiosqlite

class PersistentQueue:
    def __init__(self, db_path='/data/offline_queue.db'):
        self.db_path = db_path
    
    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    table_name TEXT,
                    frame TEXT,
                    replayed INTEGER DEFAULT 0
                )
            ''')
            await db.commit()
    
    async def add(self, frame, table_name):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT INTO queue (ts, table_name, frame) VALUES (?, ?, ?)',
                (datetime.utcnow().isoformat(), table_name, frame)
            )
            await db.commit()
    
    async def get_pending(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT id, ts, table_name, frame FROM queue WHERE replayed = 0 ORDER BY id'
            )
            return await cursor.fetchall()
    
    async def mark_replayed(self, frame_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'UPDATE queue SET replayed = 1 WHERE id = ?',
                (frame_id,)
            )
            await db.commit()
```

**Výhody:**
- Data přežijí proxy restart
- Neomezená fronta (disk space)
- Audit trail (můžeš vidět co bylo replayed)

**Nevýhody:**
- I/O overhead (disk writes)
- Složitější cleanup logika
- Database maintenance

---

## Doporučení

### Fáze 1: Simple offline mode (bez frontování) ✅ RECOMMENDED

```python
# Implementuj:
1. Offline mode s lokálním ACK
2. MQTT publishing během offline
3. Cloud reconnect probes

# NEIMPLEMENTUJ:
- Frontování (zatím)
- Replay logiku
```

**Proč:**
- Rychlá implementace
- Okamžitý benefit (data v MQTT během outage)
- Žádná data loss v MQTT
- Cloud data loss je přijatelný (outage je rare)

### Fáze 2: Queue mode (s frontováním) 🔜 FUTURE

```python
# Po úspěšné fázi 1 přidej:
1. In-memory queue (10k frames limit)
2. Replay logiku po cloud recovery
3. Metrics (queue size, replay success rate)
```

**Proč později:**
- Komplexnější implementace
- Potřebuje testování (edge cases)
- Není kritické (MQTT má data)

---

## Odhad velikosti fronty

### Typický outage scénář:

```
Outage délka: 10 minut
Telemetry rate: ~10 framů/min (tbl_actual + others)
Total frames: 10 min × 10 frames/min = 100 frames
Frame size: ~500 bytes average
Total memory: 100 × 500B = 50 KB
```

**Závěr:** Frontování 10min outage = **~50 KB** (zanedbatelné)

### Extreme outage scénář:

```
Outage délka: 4 hodiny (extreme!)
Telemetry rate: ~10 framů/min
Total frames: 240 min × 10 frames/min = 2400 frames
Total memory: 2400 × 500B = 1.2 MB
```

**Závěr:** I 4h outage = **~1.2 MB** (stále OK)

### Queue limit doporučení:

```python
MAX_QUEUE_SIZE = 10000  # ~5 MB, covers ~16 hours outage
```

---

## Implementační priority

### P0 (CRITICAL - implementuj teď):
- ✅ Offline mode s lokálním ACK generation
- ✅ MQTT publishing během offline
- ✅ Cloud reconnect probes

### P1 (HIGH - implementuj brzy):
- 🔜 In-memory queue (simple list)
- 🔜 Replay logiku po cloud recovery
- 🔜 Queue size metrics

### P2 (MEDIUM - můžeš implementovat později):
- 📅 Persistent queue (SQLite)
- 📅 Intelligent replay (rate limiting)
- 📅 Queue cleanup policy

### P3 (LOW - nice to have):
- 💡 Compression (gzip frames v queue)
- 💡 Deduplication (pokud stejný frame vícekrát)
- 💡 Priority queue (tbl_actual first, then others)

---

## Závěrečné doporučení

### 1. **Musíme posílat ACK?**
   **ANO!** ACK je povinná součást protokolu. Bez ACK se BOX zasekne.

### 2. **Musíme frontovat data?**
   **Ne nutně v první fázi.** 
   
   Start simple:
   - Offline mode + ACK ✅
   - MQTT publishing ✅
   - Cloud dostane data až po recovery (missing gap)
   
   Later add:
   - Queue + replay 🔜
   - Cloud dostane všechna data (no gap)

### 3. **Jak implementovat ACK během offline?**
   ```python
   def _generate_ack(self, table_name):
       if table_name == 'IsNewSet':
           return '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'
       else:
           return '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'
   ```

### 4. **Doporučený přístup:**
   
   **Fáze 1 (teď):**
   - Implementuj offline mode s ACK
   - MQTT publishing
   - Cloud reconnect
   - **Bez frontování**
   
   **Fáze 2 (později):**
   - Přidej in-memory queue
   - Replay logiku
   - Metrics
   
   **Fáze 3 (budoucnost):**
   - Persistent queue
   - Advanced replay strategies

---

## Timeline estimate

### Fáze 1 (bez frontování):
- Implementation: 2-3 hodiny
- Testing: 1 hodina
- Deploy + monitor: 1 den
- **Total: ~1 den práce**

### Fáze 2 (s frontováním):
- Implementation: 3-4 hodiny
- Testing: 2 hodiny (edge cases!)
- Deploy + monitor: 2 dny
- **Total: ~2-3 dny práce**

**Doporučení: Start s Fáze 1, evaluovat po týdnu provozu** ✅
