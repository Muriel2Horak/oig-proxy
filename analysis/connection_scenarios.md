# Scénáře správy TCP spojení - OIG Proxy

## Zjištění z live databáze

### Bilance komunikace normálního spojení:
```
conn_id | box→proxy | cloud→box | duration
--------|-----------|-----------|----------
132     | 464       | 480       | 3017s (50min)
131     | 1019      | 1047      | 6653s (111min)  
130     | 551       | 570       | 3607s (60min)
128     | 1028      | 1050      | 6868s (114min)
127     | 1018      | 1048      | 6901s (115min)
126     | 1025      | 1049      | 6843s (114min)
```

**Klíčové zjištění:** Cloud posílá **více** odpovědí než box dotazů!
- Průměr: +20-30 odpovědí navíc za spojení
- Důvod: Cloud občas posílá **unsolicited frames** (nastavení, příkazy)

### Bilance výpadkových spojení:
```
conn_id | box→proxy | cloud→box | duration
--------|-----------|-----------|----------
125     | 1         | 1         | <1s
124     | 1         | 1         | <1s
123     | 1         | 1         | <1s
122     | 1         | 1         | <1s
121     | 1         | 1         | <1s
120     | 1         | 1         | <1s
119     | 1         | 1         | <1s
118     | 1         | 1         | <1s
```

**Vzor výpadku:** Box posílá 1 frame (IsNewSet/IsNewFW/IsNewWeather), dostane END, spojení končí.

---

## Scénář 1: Normální ukončení spojení cloudem

### Poslední frámy spojení #131 (normální konec):

```
19:16:31 BOX→PROXY: tbl_actual (ID_Set 836594184)
19:16:31 CLOUD→BOX: ACK GetActual (CRC 00167)

19:16:36 BOX→PROXY: IsNewFW? (Fw v.4.4.43.0716)
19:16:36 CLOUD→BOX: END (CRC 34500)

>>> SPOJENÍ UKONČENO <<<
```

### Co se stalo:

1. **Cloud poslal END** na IsNewFW dotaz
2. **Cloud zavřel TCP spojení** (FIN packet)
3. **BOX detekoval uzavřené spojení** 
4. **BOX počkal 46 sekund** a vytvořil nové spojení #132
5. **Nové spojení #132 začalo v 18:17:22** (normální provoz pokračuje)

### Důvod ukončení:

❓ **Neznámý** - cloud pravděpodobně:
- Periodicky resetuje spojení (každých 60-120 minut)
- Chce vynutit reconnect kvůli load balancingu
- Detekoval nějakou interní podmínku

### Chování:

✅ **Graceful shutdown** - cloud poslal END před zavřením  
✅ **Box respektuje** - počká a reconnectuje  
✅ **Žádná ztráta dat** - data se zpracovala před ukončením  

---

## Scénář 2: Výpadek cloudu (nedostupnost)

### Výpadková sekvence (08:54-08:58):

```
08:54:21 conn #118: 1 frame → 1 response → KONEC
08:54:51 conn #119: 1 frame → 1 response → KONEC
08:55:20 conn #120: 1 frame → 1 response → KONEC
08:55:52 conn #121: 1 frame → 1 response → KONEC
08:56:20 conn #122: IsNewWeather → END → KONEC
08:57:08 conn #123: IsNewFW → END → KONEC
08:57:35 conn #124: IsNewSet → END (with Time!) → KONEC
08:58:15 conn #125: IsNewWeather → END → KONEC
08:58:59 conn #126: IsNewFW → END GetActual (!) → NORMÁLNÍ PROVOZ
```

### Detail conn #124 (zajímavá anomálie):

```
BOX→PROXY: <Frame><Result>IsNewSet</Result><ID_Device>2206237016</ID_Device><Lat>521</Lat>...
CLOUD→BOX: <Frame><Result>END</Result><Time>2025-12-10 09:57:35</Time><UTCTime>2025-12-10 08:57:35</UTCTime><CRC>32306</CRC></Frame>
                                      ^^^^^^^^^^^^^^ NESTANDARDNÍ! Normálně jen CRC 34500
```

**Interpretace:**
- Cloud **byl dostupný**, ale něco nefungovalo správně
- Odpovídal **modifikovaným END** s časem (jiné CRC: 32306 vs 34500)
- Možná **přetížení**, **reboot**, nebo **databázový problém**
- Box po této odpovědi **ukončil spojení** → rychlý reconnect

### Vzor reconnectů:

```
Interval mezi pokusy:
#118 → #119: 30s
#119 → #120: 29s
#120 → #121: 32s
#121 → #122: 28s
#122 → #123: 48s
#123 → #124: 27s
#124 → #125: 40s
#125 → #126: 44s ✅ (úspěch, normální provoz)
```

**Box má exponenciální backoff:** ~28-48 sekund mezi pokusy

---

## Scénář 3: Ztráta spojení během provozu (timeout)

### Co se může stát:

1. **Síťový výpadek** - packet loss, router restart
2. **Cloud crashed** - proces spadl bez graceful shutdown
3. **Firewall timeout** - NAT session expirovala
4. **TCP keepalive timeout** - žádná aktivita

### Detekce problému:

#### Možnost A: Box detekuje jako první
```python
# Box čeká na ACK po poslání telemetrie
BOX→PROXY: tbl_actual
# ... čeká ...
# TCP timeout (~30-60s)
BOX: ConnectionLost exception
BOX: Reconnect attempt
```

#### Možnost B: Proxy detekuje jako první
```python
# Proxy čeká na data od cloudu
try:
    data = await cloud_reader.read(8192)
    if not data:  # EOF - cloud zavřel spojení
        logger.warning("Cloud uzavřel spojení")
except asyncio.TimeoutError:
    logger.error("Cloud timeout")
except ConnectionResetError:
    logger.error("Cloud resetoval spojení")
```

### Současné chování proxy:

```python
# Z main.py řádek 774-783
done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

# ❌ Pokud kterýkoli task skončí (i normálně), ukončí se i druhý
for t in pending:
    t.cancel()  # ← Zruší forward BOX→CLOUD i když CLOUD→BOX selhal

# Finally blok (řádek 784-792)
finally:
    server_writer.close()  # ← OK
    client_writer.close()  # ← ❌ ZAVŘE SPOJENÍ K BOXU!
```

**Problém:** Pokud cloud spadne, proxy **aktivně ukončí** spojení k boxu!

---

## Scénář 4: Cloud posílá unsolicited Setting frame

### Příklad z databáze:

```sql
-- Našel jsem 75 Setting frames v databázi
-- Obvykle po IsNewSet dotazu, ale ne vždy!
```

### Normální Setting flow:

```
1. BOX→CLOUD: IsNewSet? (polling)
2. CLOUD→BOX: Setting frame (ID 13584xxx, NewValue, TblName)
3. BOX aplikuje nastavení
4. BOX→CLOUD: Potvrzení (stejný frame s <ID_Set>, <ID_SubD>)
5. CLOUD→BOX: ACK (CRC 54590)
```

### Unsolicited Setting (bez dotazu):

```
1. CLOUD→BOX: Setting frame (iniciativa cloudu!)
2. BOX→CLOUD: Potvrzení
3. CLOUD→BOX: ACK (CRC 54590)
```

**To znamená:** Cloud může poslat data **kdykoliv**, nejen jako odpověď!

---

## Scénář 5: Network partition (split brain)

### Co se může stát:

```
BOX ←─────→ PROXY ←─ ✗ ─→ CLOUD
  ✅ aktivní    ❌ nedostupný
```

1. **Spojení BOX↔PROXY** funguje
2. **Spojení PROXY↔CLOUD** padlo
3. **Proxy má otevřené oba sockety**, ale cloud neodpovídá

### Detekce:

```python
# Proxy čeká na data od cloudu
data = await asyncio.wait_for(
    cloud_reader.read(8192),
    timeout=30.0  # ← Musíme mít timeout!
)
# Pokud timeout → přepnout do offline režimu
```

**Bez timeoutu:** Proxy by čekala donekonečna, box by nedostával ACK → box by timeoutoval a reconnectoval.

---

## Health Check strategie

### 1. TCP Socket State Monitoring

```python
def is_connection_alive(writer: asyncio.StreamWriter) -> bool:
    """Zjistí jestli TCP spojení je stále aktivní"""
    try:
        # Zkusí získat socket info
        sock = writer.get_extra_info('socket')
        if sock is None:
            return False
        
        # Zkontroluje peer address
        peer = writer.get_extra_info('peername')
        if peer is None:
            return False
            
        return not writer.is_closing()
    except Exception:
        return False
```

### 2. Passive Healthcheck (monitoring traffic)

```python
class ConnectionHealth:
    def __init__(self):
        self.last_box_rx = time.time()  # Poslední data od boxu
        self.last_cloud_rx = time.time()  # Poslední data od cloudu
        self.last_box_tx = time.time()  # Poslední ACK k boxu
        
    def update_box_rx(self):
        self.last_box_rx = time.time()
        
    def is_box_timeout(self, threshold: float = 60.0) -> bool:
        """Box neposílá data > 60s = problém"""
        return (time.time() - self.last_box_rx) > threshold
        
    def is_cloud_timeout(self, threshold: float = 30.0) -> bool:
        """Cloud neodpovídá > 30s = offline"""
        return (time.time() - self.last_cloud_rx) > threshold
```

### 3. Active Healthcheck (probe cloud)

```python
async def probe_cloud_health() -> bool:
    """Aktivně testuje dostupnost cloudu"""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
            timeout=5.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False
```

---

## Rozhodovací stromy pro jednotlivé scénáře

### A) Handle Cloud Disconnect během forward mode

```
Cloud spojení selže
    │
    ├─► Socket EOF detected (graceful)
    │   ├─► Log: "Cloud gracefully closed connection"
    │   ├─► Přepni do OFFLINE mode
    │   └─► Keep BOX connection alive ✅
    │
    ├─► ConnectionResetError (abrupt)
    │   ├─► Log: "Cloud connection reset"
    │   ├─► Přepni do OFFLINE mode
    │   └─► Keep BOX connection alive ✅
    │
    ├─► TimeoutError (no response)
    │   ├─► Log: "Cloud timeout after 30s"
    │   ├─► Přepni do OFFLINE mode
    │   └─► Keep BOX connection alive ✅
    │
    └─► OSError / NetworkError
        ├─► Log: "Cloud network error: {error}"
        ├─► Přepni do OFFLINE mode
        └─► Keep BOX connection alive ✅
```

**Klíč:** BOX spojení **nikdy** neukončujeme kvůli cloudu!

### B) Handle BOX Disconnect

```
BOX spojení selže
    │
    ├─► Socket EOF detected (graceful)
    │   ├─► Log: "Box gracefully closed connection #{conn_id}"
    │   ├─► Zavři cloud spojení
    │   ├─► Cleanup resources
    │   └─► Čekej na nové spojení od boxu ✅
    │
    ├─► ConnectionResetError (abrupt)
    │   ├─► Log: "Box connection reset #{conn_id}"
    │   ├─► Zavři cloud spojení
    │   ├─► Cleanup resources
    │   └─► Čekej na nové spojení ✅
    │
    └─► TimeoutError (no data from box)
        ├─► Log: "Box timeout after 120s"
        ├─► Možná síťový problém
        ├─► Zavři obě spojení
        └─► Čekej na reconnect ✅
```

**Logika:** Pokud box spadne, celé spojení končí (box je master).

### C) Offline Mode Reconnect Attempts

```
Offline mode aktivní
    │
    └─► Background task: Cloud reconnect
        │
        ├─► Každých 60s:
        │   ├─► Probe cloud health (5s timeout)
        │   │   │
        │   │   ├─► SUCCESS ✅
        │   │   │   ├─► Log: "Cloud reconnected!"
        │   │   │   ├─► Vytvoř nové cloud spojení
        │   │   │   ├─► Přepni do FORWARD mode
        │   │   │   └─► Stop background task
        │   │   │
        │   │   └─► FAILURE ❌
        │   │       ├─► Log: "Cloud still offline (attempt #{n})"
        │   │       └─► Continue loop
        │   │
        │   └─► Max attempts: None (nekonečná smyčka)
        │
        └─► Stop podmínky:
            ├─► Cloud reconnect úspěšný
            ├─► BOX disconnect
            └─► Proxy shutdown
```

### D) Forward Mode → Offline Mode Transition

```
FORWARD MODE běží
    │
    └─► Cloud exception detected
        │
        ├─► 1. Cancel cloud→box forward task
        │   └─► Gracefully, without error propagation
        │
        ├─► 2. Close cloud connection
        │   ├─► writer.close()
        │   └─► await writer.wait_closed()
        │
        ├─► 3. Set mode flag
        │   └─► self.mode = "OFFLINE"
        │
        ├─► 4. Start offline response handler
        │   └─► Task: _offline_mode_handler()
        │
        ├─► 5. Start cloud reconnect task
        │   └─► Task: _cloud_reconnect_loop()
        │
        └─► 6. Log transition
            └─► "Switched to OFFLINE mode (cloud unavailable)"
```

### E) Offline Mode → Forward Mode Transition

```
OFFLINE MODE běží
    │
    └─► Cloud reconnect successful
        │
        ├─► 1. Vytvoř nové cloud spojení
        │   ├─► server_reader, server_writer = await open_connection(...)
        │   └─► Test connection: write/read probe
        │
        ├─► 2. Cancel offline handler task
        │   └─► Gracefully finish current frame
        │
        ├─► 3. Set mode flag
        │   └─► self.mode = "FORWARD"
        │
        ├─► 4. Start forward tasks
        │   ├─► Task: _forward(box→cloud)
        │   └─► Task: _forward(cloud→box)
        │
        ├─► 5. Stop reconnect loop
        │   └─► Cancel background task
        │
        └─► 6. Log transition
            └─► "Switched to FORWARD mode (cloud reconnected)"
```

---

## Implementační detaily

### 1. Connection State Machine

```python
class ConnectionMode(Enum):
    FORWARD = "forward"    # Normální forward mezi box↔cloud
    OFFLINE = "offline"    # Cloud offline, lokální ACK
    TRANSITION = "transition"  # Přepínání mezi módy

class ProxyConnection:
    def __init__(self):
        self.mode: ConnectionMode = ConnectionMode.FORWARD
        self.box_reader: asyncio.StreamReader | None = None
        self.box_writer: asyncio.StreamWriter | None = None
        self.cloud_reader: asyncio.StreamReader | None = None
        self.cloud_writer: asyncio.StreamWriter | None = None
        self.health = ConnectionHealth()
        self.tasks: list[asyncio.Task] = []
```

### 2. Exception Handling Strategy

```python
async def _forward(
    self,
    src_reader: asyncio.StreamReader,
    dst_writer: asyncio.StreamWriter,
    direction: str
) -> None:
    """Forward data with proper exception handling"""
    try:
        while True:
            # Timeout pro detekci dead connection
            data = await asyncio.wait_for(
                src_reader.read(8192),
                timeout=120.0 if direction == "BOX→CLOUD" else 30.0
            )
            
            if not data:  # EOF
                logger.info(f"[{direction}] Graceful close (EOF)")
                break
                
            dst_writer.write(data)
            await dst_writer.drain()
            self.health.update_rx(direction)
            
    except asyncio.TimeoutError:
        logger.warning(f"[{direction}] Timeout - no data")
        if direction == "CLOUD→BOX":
            # Cloud timeout → switch to offline
            await self._switch_to_offline()
        else:
            # Box timeout → ukončit spojení
            raise
            
    except ConnectionResetError:
        logger.warning(f"[{direction}] Connection reset by peer")
        if direction == "CLOUD→BOX":
            await self._switch_to_offline()
        else:
            raise
            
    except Exception as e:
        logger.error(f"[{direction}] Unexpected error: {e}")
        raise
```

### 3. Timeout Configuration

```python
# Timeouty pro různé scénáře
TIMEOUTS = {
    # Cloud connection
    "cloud_connect": 5.0,      # Max 5s na spojení s cloudem
    "cloud_response": 30.0,    # Max 30s na odpověď od cloudu
    "cloud_probe": 5.0,        # Healthcheck probe timeout
    
    # Box connection  
    "box_data": 120.0,         # Max 120s mezi telemetrií (2x normal interval)
    
    # Reconnect
    "reconnect_interval": 60.0,  # Zkusit reconnect každých 60s
    "reconnect_max_attempts": None,  # Nekonečné pokusy
}
```

### 4. Cloud Reconnect Logic

```python
async def _cloud_reconnect_loop(self, conn_id: int) -> None:
    """Background task: Zkouší reconnect ke cloudu"""
    attempt = 0
    
    while self.mode == ConnectionMode.OFFLINE:
        attempt += 1
        await asyncio.sleep(TIMEOUTS["reconnect_interval"])
        
        logger.debug(f"[#{conn_id}] Cloud reconnect attempt #{attempt}")
        
        if await self._probe_cloud():
            logger.info(f"[#{conn_id}] Cloud is back online!")
            await self._switch_to_forward(conn_id)
            break
        else:
            logger.debug(f"[#{conn_id}] Cloud still offline")

async def _probe_cloud(self) -> bool:
    """Testuje dostupnost cloudu bez ovlivnění hlavního spojení"""
    try:
        test_reader, test_writer = await asyncio.wait_for(
            asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
            timeout=TIMEOUTS["cloud_probe"]
        )
        test_writer.close()
        await test_writer.wait_closed()
        return True
    except Exception as e:
        logger.debug(f"Cloud probe failed: {e}")
        return False
```

### 5. Graceful Mode Switching

```python
async def _switch_to_offline(self) -> None:
    """Přepne z FORWARD do OFFLINE režimu"""
    if self.mode == ConnectionMode.OFFLINE:
        return  # Už jsme offline
        
    logger.warning("Switching to OFFLINE mode")
    self.mode = ConnectionMode.TRANSITION
    
    # 1. Zruš cloud→box forward task
    for task in self.tasks:
        if "CLOUD→BOX" in str(task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    # 2. Zavři cloud spojení
    if self.cloud_writer:
        try:
            self.cloud_writer.close()
            await self.cloud_writer.wait_closed()
        except Exception:
            pass
        self.cloud_writer = None
        self.cloud_reader = None
    
    # 3. Aktivuj offline režim
    self.mode = ConnectionMode.OFFLINE
    
    # 4. Start reconnect loop
    reconnect_task = asyncio.create_task(
        self._cloud_reconnect_loop(self.conn_id)
    )
    self.tasks.append(reconnect_task)
    
    logger.info("OFFLINE mode active - generating local ACK responses")

async def _switch_to_forward(self, conn_id: int) -> None:
    """Přepne z OFFLINE do FORWARD režimu"""
    if self.mode == ConnectionMode.FORWARD:
        return
        
    logger.info("Switching to FORWARD mode")
    self.mode = ConnectionMode.TRANSITION
    
    # 1. Vytvoř nové cloud spojení
    try:
        self.cloud_reader, self.cloud_writer = await asyncio.wait_for(
            asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
            timeout=TIMEOUTS["cloud_connect"]
        )
    except Exception as e:
        logger.error(f"Failed to reconnect to cloud: {e}")
        self.mode = ConnectionMode.OFFLINE
        return
    
    # 2. Zruš reconnect loop
    for task in self.tasks:
        if "reconnect" in str(task):
            task.cancel()
    
    # 3. Start forward tasks
    forward_tasks = [
        asyncio.create_task(
            self._forward(self.box_reader, self.cloud_writer, "BOX→CLOUD")
        ),
        asyncio.create_task(
            self._forward(self.cloud_reader, self.box_writer, "CLOUD→BOX")
        ),
    ]
    self.tasks = forward_tasks
    
    # 4. Aktivuj forward režim
    self.mode = ConnectionMode.FORWARD
    logger.info("FORWARD mode active - proxying to cloud")
```

---

## Logování a observability

### Log Levels:

```python
# INFO - normální provoz
logger.info(f"[#{conn_id}] New connection from {box_addr}")
logger.info(f"[#{conn_id}] Connected to cloud")
logger.info(f"[#{conn_id}] Switched to OFFLINE mode")
logger.info(f"[#{conn_id}] Switched to FORWARD mode")
logger.info(f"[#{conn_id}] Connection closed gracefully")

# WARNING - degradovaný stav
logger.warning(f"[#{conn_id}] Cloud timeout, switching to offline")
logger.warning(f"[#{conn_id}] Cloud connection reset")
logger.warning(f"[#{conn_id}] Box timeout detected")

# ERROR - skutečné chyby
logger.error(f"[#{conn_id}] Failed to connect to cloud: {error}")
logger.error(f"[#{conn_id}] Unexpected exception in forward: {error}")

# DEBUG - detailní info
logger.debug(f"[#{conn_id}] Cloud probe attempt #{n}")
logger.debug(f"[#{conn_id}] Received frame: {frame[:100]}")
logger.debug(f"[#{conn_id}] Health: box_rx={ts}, cloud_rx={ts}")
```

### Metriky:

```python
class ConnectionMetrics:
    total_connections: int = 0
    active_connections: int = 0
    offline_mode_count: int = 0
    cloud_reconnects: int = 0
    box_disconnects: int = 0
    frames_forwarded: int = 0
    frames_offline: int = 0
    
    # Časy
    total_uptime: float = 0.0
    offline_time: float = 0.0
    forward_time: float = 0.0
```

---

## Závěr - Decision Matrix

| Scénář | Cloud State | Box State | Proxy Action | BOX Connection | Cloud Connection |
|--------|-------------|-----------|--------------|----------------|------------------|
| **Normal Operation** | ✅ Online | ✅ Active | FORWARD mode | ✅ Keep | ✅ Keep |
| **Cloud Timeout** | ❌ Timeout | ✅ Active | → OFFLINE mode | ✅ Keep | ❌ Close, retry |
| **Cloud Disconnect** | ❌ Closed | ✅ Active | → OFFLINE mode | ✅ Keep | ❌ Close, retry |
| **Cloud Error** | ❌ Error | ✅ Active | → OFFLINE mode | ✅ Keep | ❌ Close, retry |
| **Cloud Reconnect OK** | ✅ Online | ✅ Active | → FORWARD mode | ✅ Keep | ✅ New connection |
| **Box Timeout** | ✅ Online | ❌ Timeout | Close both | ❌ Close | ❌ Close |
| **Box Disconnect** | ✅ Online | ❌ Closed | Close both | ❌ Closed | ❌ Close |
| **Box Error** | ✅ Online | ❌ Error | Close both | ❌ Close | ❌ Close |
| **Both Offline** | ❌ Offline | ❌ Closed | Cleanup | ❌ Closed | ❌ Closed |

### Klíčové pravidlo:

**BOX je master, CLOUD je optional.**

- Pokud **BOX** spadne → celé spojení končí ✅
- Pokud **CLOUD** spadne → BOX spojení pokračuje, přepneme do offline ✅
- **NIKDY** neukončujeme BOX spojení kvůli cloudu ✅

---

## Příklad log výstupu během výpadku

```
2025-12-10 08:56:20 [INFO] [#122] New connection from ('192.168.1.50', 54321)
2025-12-10 08:56:20 [INFO] [#122] Connected to cloud oigservis.cz:5710
2025-12-10 08:56:20 [INFO] [#122] FORWARD mode active
2025-12-10 08:56:20 [DEBUG] [#122] BOX→CLOUD: IsNewWeather
2025-12-10 08:56:20 [DEBUG] [#122] CLOUD→BOX: END (CRC 34500)
2025-12-10 08:56:20 [INFO] [#122] Cloud gracefully closed connection
2025-12-10 08:56:20 [INFO] [#122] Switched to OFFLINE mode
2025-12-10 08:56:20 [INFO] [#122] Starting cloud reconnect attempts

2025-12-10 08:56:48 [DEBUG] [#122] Cloud probe attempt #1
2025-12-10 08:56:53 [DEBUG] [#122] Cloud probe failed: Connection refused

2025-12-10 08:57:08 [DEBUG] [#122] BOX→PROXY: IsNewFW
2025-12-10 08:57:08 [DEBUG] [#122] Generated offline response: END (CRC 34500)
2025-12-10 08:57:08 [DEBUG] [#122] PROXY→BOX: END (offline)

2025-12-10 08:57:48 [DEBUG] [#122] Cloud probe attempt #2
2025-12-10 08:57:53 [DEBUG] [#122] Cloud probe failed: Timeout

2025-12-10 08:58:35 [DEBUG] [#122] BOX→PROXY: tbl_actual
2025-12-10 08:58:35 [DEBUG] [#122] Generated offline response: ACK GetActual
2025-12-10 08:58:35 [DEBUG] [#122] PROXY→BOX: ACK (offline)
2025-12-10 08:58:35 [INFO] [#122] 📊 tbl_actual: 16 hodnot [OFFLINE mode]

2025-12-10 08:58:48 [DEBUG] [#122] Cloud probe attempt #3
2025-12-10 08:58:48 [INFO] [#122] Cloud is back online!
2025-12-10 08:58:48 [INFO] [#122] Switched to FORWARD mode
2025-12-10 08:58:48 [INFO] [#122] Cloud reconnect successful after 3 attempts

2025-12-10 08:58:54 [DEBUG] [#122] BOX→CLOUD: tbl_actual
2025-12-10 08:58:54 [DEBUG] [#122] CLOUD→BOX: ACK GetActual
2025-12-10 08:58:54 [INFO] [#122] 📊 tbl_actual: 16 hodnot [latence: 6.2s]
```

---

## Implementace - Prioritní úkoly

### Must-have:
1. ✅ Oddělit lifecycle BOX a CLOUD spojení
2. ✅ Implementovat OFFLINE mode s lokálními ACK/END
3. ✅ Graceful přepínání mezi FORWARD ↔ OFFLINE
4. ✅ Cloud reconnect loop na pozadí
5. ✅ Proper exception handling (timeout, reset, EOF)

### Should-have:
6. ✅ Health monitoring (passive + active probe)
7. ✅ Strukturované logování s conn_id
8. ✅ Metriky (offline time, reconnect count)

### Nice-to-have:
9. ⚪ Buffer frames během offline pro replay
10. ⚪ Exponenciální backoff pro reconnect
11. ⚪ MQTT status topic (mode, cloud_state)

Chceš vidět implementaci do `main.py`?
