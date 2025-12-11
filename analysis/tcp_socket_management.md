# TCP Socket Management - Detailní vysvětlení

## Klíčový koncept: DVA NEZÁVISLÉ SOCKETY

```
┌─────────────────────────────────────────────────────────────────┐
│                         PROXY                                   │
│                                                                 │
│  ┌──────────────────┐              ┌──────────────────┐        │
│  │   Socket 1       │              │   Socket 2       │        │
│  │   BOX ↔ PROXY    │              │   PROXY ↔ CLOUD  │        │
│  │                  │              │                  │        │
│  │  client_reader   │              │  server_reader   │        │
│  │  client_writer   │              │  server_writer   │        │
│  └──────────────────┘              └──────────────────┘        │
│          │                                  │                  │
└──────────┼──────────────────────────────────┼──────────────────┘
           │                                  │
           │                                  │
      ┌────▼─────┐                      ┌─────▼────┐
      │   BOX    │                      │  CLOUD   │
      │ (master) │                      │(optional)│
      └──────────┘                      └──────────┘
```

**KLÍČ:** Tyto dva sockety musí být **NEZÁVISLÉ**!

---

## Problém v současném kódu

### Řádek 753-756: Vytvoření spojení ke cloudu
```python
server_reader, server_writer = await asyncio.open_connection(
    TARGET_SERVER, TARGET_PORT
)
```

**Problém:** Pokud toto selže (cloud offline):
- Vyhodí `Exception`
- Skok do `finally` bloku (řádek 784)
- Zavře **OBA** sockety včetně spojení k BOXu!

### Řádek 774: Čekání na tasky
```python
done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
```

**Problém:** Pokud kterýkoli task skončí:
- `FIRST_EXCEPTION` → okamžité ukončení
- Ruší všechny pending tasks (řádek 775-776)
- Skok do `finally` → zavře spojení k BOXu!

### Řádek 784-792: Finally blok
```python
finally:
    if server_writer:
        server_writer.close()  # ← OK (cloud)
    
    client_writer.close()      # ← ❌ PROBLÉM! (box)
    await client_writer.wait_closed()
```

**Problém:** Zavírá spojení k BOXu i když problém byl v cloudu!

---

## Jak poznáme že cloud neodpovídá - 4 způsoby

### 1️⃣ Connection Refused (při navazování spojení)

```python
# Současný kód - řádek 753
server_reader, server_writer = await asyncio.open_connection(
    TARGET_SERVER, TARGET_PORT
)
# ❌ Pokud cloud port zavřený → ConnectionRefusedError
# ❌ Exception propaguje → finally blok → zavře BOX spojení
```

**Nově:**
```python
try:
    server_reader, server_writer = await asyncio.wait_for(
        asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
        timeout=5.0
    )
    cloud_available = True
except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
    logger.warning(f"Cloud unavailable: {e}")
    cloud_available = False  # ✅ Nepropaguje exception!
    # ✅ Socket 1 (BOX) zůstává otevřený
```

**Detekce:**
- `ConnectionRefusedError` - port zavřený
- `asyncio.TimeoutError` - firewall/timeout
- `OSError` / `socket.gaierror` - DNS/network error

**Reakce:** `cloud_available = False` → jdi do OFFLINE mode

---

### 2️⃣ Timeout během provozu (cloud přestane odpovídat)

```python
# Současný kód - řádek 802-805
async def _forward(...):
    while True:
        data = await reader.read(4096)  # ❌ Bez timeoutu!
        if not data:
            break
        writer.write(data)
```

**Problém:** Pokud cloud zamrzne a nepošle ACK:
- `reader.read()` čeká **donekonečna**
- BOX čeká na ACK → timeout (~60s)
- BOX zavře spojení → reconnect
- Nové spojení → opět čeká donekonečna...

**Nově:**
```python
async def _forward(..., direction: str):
    while True:
        # ⏱️ TIMEOUT závislý na směru
        timeout = 30.0 if "CLOUD" in direction else 120.0
        
        data = await asyncio.wait_for(
            reader.read(8192),
            timeout=timeout
        )
        
        if not data:
            logger.info(f"{direction} EOF")
            break
            
        writer.write(data)
        await writer.drain()
```

**Scénář:**
```
T+0s:  BOX → tbl_actual → PROXY
T+0s:  PROXY → tbl_actual → CLOUD
T+5s:  ... čekáme na ACK ...
T+10s: ... stále čekáme ...
T+30s: ⏰ asyncio.TimeoutError!
       │
       └─► if "CLOUD→BOX" in direction:
               raise CloudTimeoutError()  # Custom exception
```

**Detekce:** `asyncio.TimeoutError` po 30 sekundách bez dat od cloudu

**Reakce:** 
1. Catch `CloudTimeoutError` v `handle_connection()`
2. Cancel cloud forward tasks
3. Close socket 2 (cloud)
4. **Socket 1 (BOX) zůstává otevřený!**
5. Switch to OFFLINE mode
6. Generuj lokální ACK (< 1ms)

---

### 3️⃣ TCP FIN (graceful close od cloudu)

```python
# Současný kód - řádek 803
data = await reader.read(4096)
if not data:  # ← EOF detekce ✅
    break
```

**Co se stane:**
```
Cloud posílá:
  TCP: FIN, ACK
  │
  └─► Python StreamReader dostane EOF
      │
      └─► reader.read() vrátí b'' (prázdný bytes)
          │
          └─► if not data: break
              │
              └─► Forward loop končí
                  │
                  └─► Task končí normálně
                      │
                      └─► FIRST_EXCEPTION detekuje konec
                          │
                          └─► ❌ Zruší druhý task
                              │
                              └─► Finally blok → zavře BOX!
```

**Nově:**
```python
data = await asyncio.wait_for(reader.read(8192), timeout=30.0)

if not data:
    logger.info(f"{direction} EOF - peer closed")
    if "CLOUD→BOX" in direction:
        raise CloudDisconnectError()  # Kontrolované ukončení
    else:
        raise BoxDisconnectError()
```

**Detekce:** `data == b''` (prázdné bytes)

**Reakce:** 
- Cloud EOF → CloudDisconnectError → switch to OFFLINE
- Box EOF → BoxDisconnectError → ukončit celé spojení

---

### 4️⃣ TCP RST (abrupt close)

```python
# Může nastat při:
await writer.drain()  # Pokud peer resetoval spojení
```

**Co se stane:**
```
Cloud crashed/restartuje:
  TCP: RST
  │
  └─► Python socket dostane ECONNRESET
      │
      └─► writer.drain() vyhodí ConnectionResetError
          │
          └─► Task končí s exception
              │
              └─► FIRST_EXCEPTION → zruší druhý task
                  │
                  └─► ❌ Finally → zavře BOX!
```

**Nově:**
```python
try:
    writer.write(data)
    await writer.drain()
except ConnectionResetError:
    logger.warning(f"{direction} connection reset by peer")
    if "CLOUD→BOX" in direction:
        raise CloudDisconnectError()
    else:
        raise BoxDisconnectError()
```

**Detekce:** `ConnectionResetError` exception

**Reakce:** Stejně jako EOF - switch to OFFLINE nebo ukončit

---

## Jak udržíme spojení s BOXem - KROK ZA KROKEM

### Současný přístup (ŠPATNĚ):

```python
async def handle_connection(self, client_reader, client_writer):
    try:
        # Socket 1: BOX připojen ✅
        
        # Socket 2: Pokus o cloud
        server_reader, server_writer = await open_connection(...)
        # ❌ Pokud selže → Exception → Finally → Zavře Socket 1!
        
        # Forward tasks
        tasks = [
            forward(BOX→CLOUD),   # Závislý na Socket 2
            forward(CLOUD→BOX)    # Závislý na Socket 2
        ]
        
        await wait(tasks, FIRST_EXCEPTION)
        # ❌ Pokud Socket 2 selže → Finally → Zavře Socket 1!
        
    finally:
        server_writer.close()  # OK
        client_writer.close()  # ❌ PROBLÉM!
```

**Výsledek:** Socket 1 a Socket 2 jsou svázané → cloud padne = box se odpojí

---

### Nový přístup (SPRÁVNĚ):

```python
async def handle_connection(self, client_reader, client_writer):
    conn_id = self.connection_count + 1
    
    # ============================================
    # KROK 1: Socket 1 (BOX) - VŽDY ÚSPĚŠNÉ
    # ============================================
    logger.info(f"[#{conn_id}] BOX connected from {client_addr}")
    
    # ============================================
    # KROK 2: Socket 2 (CLOUD) - POKUS, ALE NE REQUIREMENT
    # ============================================
    cloud_available = False
    server_reader = None
    server_writer = None
    
    try:
        server_reader, server_writer = await asyncio.wait_for(
            asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
            timeout=5.0
        )
        cloud_available = True
        logger.info(f"[#{conn_id}] Cloud connected ✅")
        
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
        logger.warning(f"[#{conn_id}] Cloud unavailable: {e}")
        cloud_available = False
        # ✅ EXCEPTION JE ZACHYCENÁ, NEPROPAGUJE SE!
        # ✅ Socket 1 zůstává otevřený
    
    # ============================================
    # KROK 3: ROZHODNUTÍ - FORWARD nebo OFFLINE
    # ============================================
    try:
        if cloud_available:
            # Socket 2 OK → FORWARD MODE
            await self._run_forward_mode(
                conn_id, 
                client_reader, client_writer,    # Socket 1
                server_reader, server_writer     # Socket 2
            )
        else:
            # Socket 2 FAIL → OFFLINE MODE
            await self._run_offline_mode(
                conn_id,
                client_reader, client_writer     # Pouze Socket 1!
            )
    
    # ============================================
    # KROK 4: CATCH CLOUD ERRORS - SWITCH TO OFFLINE
    # ============================================
    except CloudTimeoutError:
        logger.warning(f"[#{conn_id}] Cloud timeout → switching to offline")
        # ✅ Socket 1 je stále aktivní!
        # ✅ Socket 2 už je zavřený (v _run_forward_mode)
        await self._run_offline_mode(
            conn_id,
            client_reader, client_writer
        )
    
    except CloudDisconnectError:
        logger.warning(f"[#{conn_id}] Cloud disconnected → switching to offline")
        # ✅ Socket 1 je stále aktivní!
        await self._run_offline_mode(
            conn_id,
            client_reader, client_writer
        )
    
    # ============================================
    # KROK 5: FINALLY - POUZE Socket 1 (BOX)
    # ============================================
    finally:
        # Socket 2 už je zavřený (pokud existoval)
        # Zavíráme pouze Socket 1 když BOX odpojil
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass
        logger.info(f"[#{conn_id}] Connection closed")
```

---

### Forward Mode implementace:

```python
async def _run_forward_mode(
    self,
    conn_id: int,
    box_reader, box_writer,      # Socket 1
    cloud_reader, cloud_writer   # Socket 2
) -> None:
    """Forward mode - proxy mezi BOX a CLOUD"""
    
    tasks = [
        asyncio.create_task(
            self._forward(box_reader, cloud_writer, "BOX→CLOUD")
        ),
        asyncio.create_task(
            self._forward(cloud_reader, box_writer, "CLOUD→BOX")
        ),
    ]
    
    try:
        # Čekáme až kterýkoli task skončí
        done, pending = await asyncio.wait(
            tasks, 
            return_when=asyncio.FIRST_COMPLETED  # Ne FIRST_EXCEPTION!
        )
        
        # Analyzuj proč task skončil
        for task in done:
            try:
                await task  # Re-raise exception pokud byla
            except CloudTimeoutError:
                logger.warning(f"[#{conn_id}] Cloud timeout detected")
                raise  # Propaguj do handle_connection
            except CloudDisconnectError:
                logger.warning(f"[#{conn_id}] Cloud disconnect detected")
                raise
            except BoxDisconnectError:
                logger.info(f"[#{conn_id}] Box disconnected")
                raise  # Normální ukončení
        
    finally:
        # Cancel zbývající tasks
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        
        # ✅ Zavři Socket 2 (CLOUD)
        if cloud_writer:
            try:
                cloud_writer.close()
                await cloud_writer.wait_closed()
            except Exception:
                pass
        
        # ✅ Socket 1 (BOX) NEZAVÍRÁME! To je v handle_connection finally
```

---

### Offline Mode implementace:

```python
async def _run_offline_mode(
    self,
    conn_id: int,
    box_reader, box_writer  # Pouze Socket 1
) -> None:
    """Offline mode - lokální ACK odpovědi"""
    
    logger.info(f"[#{conn_id}] OFFLINE mode active")
    
    # Start background reconnect task
    reconnect_task = asyncio.create_task(
        self._cloud_reconnect_loop(conn_id)
    )
    
    try:
        while True:
            # Čti frame od BOXu (Socket 1)
            data = await asyncio.wait_for(
                box_reader.read(8192),
                timeout=120.0  # BOX timeout (2x normal interval)
            )
            
            if not data:
                logger.info(f"[#{conn_id}] Box closed connection")
                break
            
            # Zpracuj data (MQTT publish atd.)
            frame_str = data.decode('utf-8', errors='ignore')
            self._process_data(data, conn_id, None)
            
            # Generuj lokální odpověď
            response = self._generate_offline_response(frame_str)
            
            # Pošli odpověď BOXu (Socket 1)
            box_writer.write(response.encode('utf-8'))
            await box_writer.drain()
            
            logger.debug(f"[#{conn_id}] Sent offline ACK to box")
    
    except asyncio.TimeoutError:
        logger.warning(f"[#{conn_id}] Box timeout - no data for 120s")
        raise BoxDisconnectError()
    
    finally:
        # Zastav reconnect loop
        reconnect_task.cancel()
        try:
            await reconnect_task
        except asyncio.CancelledError:
            pass
```

---

### Cloud Reconnect Loop:

```python
async def _cloud_reconnect_loop(self, conn_id: int) -> None:
    """Background task - zkouší reconnect ke cloudu každých 60s"""
    
    attempt = 0
    
    while True:
        await asyncio.sleep(60)  # Čekej 60s mezi pokusy
        attempt += 1
        
        logger.debug(f"[#{conn_id}] Cloud reconnect attempt #{attempt}")
        
        # Probe cloud dostupnost
        try:
            test_reader, test_writer = await asyncio.wait_for(
                asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                timeout=5.0
            )
            # Úspěch! Cloud je zpět
            test_writer.close()
            await test_writer.wait_closed()
            
            logger.info(f"[#{conn_id}] ✅ Cloud is back online!")
            
            # TODO: Přepnout do forward mode
            # (vyžaduje složitější orchestraci - zatím jen logujeme)
            break
            
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            logger.debug(f"[#{conn_id}] Cloud still offline (attempt #{attempt})")
            continue
```

---

## Vizualizace - Praktický příklad

### Scénář: Cloud spadne během provozu

```
Čas  | BOX Socket 1        | PROXY                    | CLOUD Socket 2
-----|---------------------|--------------------------|------------------
T+0  | ✅ Connected        | Forward mode active      | ✅ Connected
     |                     |                          |
T+10 | → tbl_actual        | read Socket 1 ✅         |
     |                     | write Socket 2 ✅        | ← received
     |                     | read Socket 2 (waiting)  |
T+15 |                     | ... waiting for ACK ...  | 💥 CRASH
     |                     |                          |
T+40 |                     | ⏰ TIMEOUT (30s)         | (offline)
     |                     | CloudTimeoutError        |
     |                     | Cancel cloud tasks       |
     |                     | Close Socket 2 ❌        |
     |                     | ✅ Socket 1 ALIVE!       |
     |                     |                          |
T+41 |                     | Switch to OFFLINE mode   |
     |                     | Generate local ACK       |
     | ← ACK               | write Socket 1 ✅        |
     | ✅ BOX happy!       |                          |
     |                     |                          |
T+50 | → tbl_actual        | read Socket 1 ✅         |
     |                     | Generate ACK             |
     | ← ACK               | write Socket 1 ✅        |
     | ✅ No disconnect!   | Publish to MQTT ✅       |
     |                     |                          |
T+101|                     | Reconnect probe #1       |
     |                     | Failed → continue        | (still offline)
     |                     |                          |
T+161|                     | Reconnect probe #2       |
     |                     | Socket 2 ✅ SUCCESS      | ✅ Back online
     |                     | (zatím jen log)          |
     |                     |                          |
T+170| → tbl_actual        | read Socket 1 ✅         |
     |                     | (stále offline mode)     |
     | ← ACK (local)       | write Socket 1 ✅        |
```

**Klíč:**
- Socket 1 (BOX↔PROXY) byl aktivní **celou dobu** (T+0 až T+170+)
- BOX **nikdy** nedetekoval problém
- **Žádné** reconnecty od BOXu
- Data šla do MQTT i během výpadku cloudu (T+40 až T+161)

---

## Shrnutí - Jak poznáme že cloud neodpovídá

| Způsob | Kdy nastane | Jak poznáme | Python Exception | Akce |
|--------|-------------|-------------|------------------|------|
| **Connection Refused** | Cloud port zavřený | Při `open_connection()` | `ConnectionRefusedError` | `cloud_available=False` → OFFLINE |
| **Timeout** | Firewall, server down | Po 5s při `open_connection()` | `asyncio.TimeoutError` | `cloud_available=False` → OFFLINE |
| **Network Error** | DNS, routing | Při `open_connection()` | `OSError`, `socket.gaierror` | `cloud_available=False` → OFFLINE |
| **Read Timeout** | Cloud zamrzl | Po 30s bez dat od cloudu | `asyncio.TimeoutError` | Raise `CloudTimeoutError` → switch OFFLINE |
| **TCP FIN** | Graceful close | `reader.read()` vrátí `b''` | (žádná) | Raise `CloudDisconnectError` → switch OFFLINE |
| **TCP RST** | Abrupt close | Při `writer.drain()` | `ConnectionResetError` | Raise `CloudDisconnectError` → switch OFFLINE |

## Shrnutí - Jak udržíme spojení s BOXem

✅ **Socket 1 a Socket 2 jsou NEZÁVISLÉ**
- Socket 2 (cloud) může selhat kdykoliv
- Socket 1 (box) pokračuje v provozu

✅ **Cloud exception se NEPROPAGUJE do finally bloku**
- Catch specifické cloud exceptions
- Switch do offline mode
- Socket 1 zůstává otevřený

✅ **Offline mode používá pouze Socket 1**
- Žádná závislost na cloudu
- Generuje lokální ACK/END
- Publikuje do MQTT

✅ **Background reconnect nezasahuje do Socket 1**
- Probe cloud každých 60s
- Při úspěchu může přepnout mode (future feature)
- Socket 1 běží nezávisle

✅ **Finally blok zavírá pouze Socket 1**
- Socket 2 je už zavřený (pokud existoval)
- Socket 1 se zavře až když BOX odpojí
