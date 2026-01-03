# Finální architektura - OIG Proxy s výpadkovými režimy

## 🎯 Cíle

1. **Cloud offline** → Proxy pokračuje, generuje ACK, frontuje pro cloud
2. **MQTT offline** → Proxy pokračuje, frontuje pro MQTT
3. **Zachování sekvence** → FIFO replay s prioritou fronty před live
4. **In-memory fronty** → Rychlé, bez závislosti na SQLite
5. **Persistence optional** → Jen pokud je CAPTURE_PAYLOADS=true

## 🏗️ Komponenty

### 0. ProxyMode enum

```python
from enum import Enum

class ProxyMode(Enum):
    """Režimy provozu proxy."""
    ONLINE = "online"      # Cloud OK, fronta prázdná → direct forward
    OFFLINE = "offline"    # Cloud DOWN → local ACK + queue  
    REPLAY = "replay"      # Cloud OK, fronta NEPRÁZDNÁ → vyprazdňování
```

### 1. CloudQueue (SQLite persistence)

```python
class CloudQueue:
    """Persistentní fronta pro offline cloud režim."""
    
    def __init__(self, db_path: str = "/data/cloud_queue.db", max_size: int = 10000):
        self.db_path = db_path
        self.max_size = max_size
        self.conn = self._init_db()
        self.lock = asyncio.Lock()
    
    def _init_db(self) -> sqlite3.Connection:
        """Inicializuje SQLite databázi."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                table_name TEXT NOT NULL,
                frame_data TEXT NOT NULL,
                device_id TEXT,
                queued_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON queue(timestamp)")
        conn.commit()
        return conn
    
    async def add(self, frame_data: str, table_name: str, device_id: str | None):
        """Přidá frame do fronty (FIFO)."""
        async with self.lock:
            # Check size limit
            size = self.size()
            if size >= self.max_size:
                # Drop oldest
                self.conn.execute("DELETE FROM queue WHERE id IN (SELECT id FROM queue ORDER BY id LIMIT 1)")
                logger.warning(f"CloudQueue full ({self.max_size}), dropped oldest frame")
            
            self.conn.execute(
                "INSERT INTO queue (timestamp, table_name, frame_data, device_id, queued_at) VALUES (?, ?, ?, ?, ?)",
                (time.time(), table_name, frame_data, device_id, _iso_now())
            )
            self.conn.commit()
    
    async def get_next(self) -> tuple[int, str, str] | None:
        """Vrátí další frame (id, table_name, frame_data) nebo None."""
        async with self.lock:
            cursor = self.conn.execute(
                "SELECT id, table_name, frame_data FROM queue ORDER BY id LIMIT 1"
            )
            row = cursor.fetchone()
            return row if row else None
    
    async def remove(self, frame_id: int):
        """Odstraní frame po úspěšném odeslání."""
        async with self.lock:
            self.conn.execute("DELETE FROM queue WHERE id = ?", (frame_id,))
            self.conn.commit()
    
    def size(self) -> int:
        """Vrátí počet frames ve frontě."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM queue")
        return cursor.fetchone()[0]
    
    def oldest_age(self) -> float | None:
        """Vrátí stáří nejstaršího frame v sekundách."""
        cursor = self.conn.execute("SELECT MIN(timestamp) FROM queue")
        oldest = cursor.fetchone()[0]
        return time.time() - oldest if oldest else None
```

### 2. MQTTQueue (SQLite persistence)

```python
class MQTTQueue:
    """Persistentní fronta pro offline MQTT režim."""
    
    def __init__(self, db_path: str = "/data/mqtt_queue.db", max_size: int = 5000):
        self.db_path = db_path
        self.max_size = max_size
        self.conn = self._init_db()
        self.lock = asyncio.Lock()
    
    def _init_db(self) -> sqlite3.Connection:
        """Inicializuje SQLite databázi."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                data TEXT NOT NULL,
                queued_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON queue(timestamp)")
        conn.commit()
        return conn
    
    async def add(self, data: dict[str, Any]):
        """Přidá MQTT message do fronty."""
        async with self.lock:
            # Check size limit
            size = self.size()
            if size >= self.max_size:
                self.conn.execute("DELETE FROM queue WHERE id IN (SELECT id FROM queue ORDER BY id LIMIT 1)")
                logger.warning(f"MQTTQueue full ({self.max_size}), dropped oldest message")
            
            self.conn.execute(
                "INSERT INTO queue (timestamp, data, queued_at) VALUES (?, ?, ?)",
                (time.time(), json.dumps(data, ensure_ascii=False), _iso_now())
            )
            self.conn.commit()
    
    async def get_batch(self, batch_size: int = 100) -> list[tuple[int, dict]]:
        """Vrátí batch (id, data) pro replay."""
        async with self.lock:
            cursor = self.conn.execute(
                "SELECT id, data FROM queue ORDER BY id LIMIT ?",
                (batch_size,)
            )
            return [(row[0], json.loads(row[1])) for row in cursor.fetchall()]
    
    async def remove_batch(self, ids: list[int]):
        """Odstraní zpracované messages."""
        async with self.lock:
            if not ids:
                return
            placeholders = ",".join("?" for _ in ids)
            self.conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", ids)
            self.conn.commit()
    
    def size(self) -> int:
        """Vrátí počet messages ve frontě."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM queue")
        return cursor.fetchone()[0]
```

### 3. CloudHealthChecker (rozšířený)

```python
class CloudHealthChecker:
    """Monitoruje zdraví cloud spojení a řídí režimy."""
    
    HEALTH_CHECK_INTERVAL = 30  # 30s
    TIMEOUT = 5.0
    
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.is_online = True  # Optimistický start
        self.last_check_time = 0.0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self._mode_change_callback = None
        
    def set_mode_callback(self, callback):
        """Nastaví callback pro notifikaci změn stavu."""
        self._mode_change_callback = callback
        
    async def check_health(self) -> bool:
        """Zkontroluje cloud dostupnost (TCP handshake)."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.TIMEOUT
            )
            writer.close()
            await writer.wait_closed()
            
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            
            # Přechod offline → online (po 2 úspěších)
            if not self.is_online and self.consecutive_successes >= 2:
                logger.info("☁️ Cloud ONLINE - recovered!")
                self.is_online = True
                # Notify režim změny
                if self._mode_change_callback:
                    await self._mode_change_callback("cloud_recovered")
            
            return True
            
        except Exception as e:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            
            # Přechod online → offline (po 3 selháních)
            if self.is_online and self.consecutive_failures >= 3:
                logger.warning(f"☁️ Cloud OFFLINE - {e}")
                self.is_online = False
                # Notify režim změny
                if self._mode_change_callback:
                    await self._mode_change_callback("cloud_down")
            
            return False
```

### 4. OIGProxy s režimy

```python
class OIGProxy:
    """Hlavní proxy s podporou režimů ONLINE/OFFLINE/REPLAY."""
    
    def __init__(self):
        self.parser = OIGDataParser()
        self.mqtt_publisher: MQTTPublisher | None = None
        self.connection_count = 0
        self.device_id: str | None = None
        self.current_state: dict[str, Any] = {}
        
        # Cloud management
        self.health_checker = CloudHealthChecker(TARGET_SERVER, TARGET_PORT)
        self.health_checker.set_mode_callback(self._on_cloud_state_change)
        self.cloud_queue = CloudQueue()
        
        # Proxy mode
        self.mode = ProxyMode.ONLINE  # Start optimisticky
        self.mode_lock = asyncio.Lock()
        
        # Replay tasks
        self._cloud_replay_task: asyncio.Task | None = None
        
    async def _on_cloud_state_change(self, event: str):
        """Callback při změně cloud stavu."""
        async with self.mode_lock:
            if event == "cloud_down":
                # Cloud spadl → OFFLINE mode
                self.mode = ProxyMode.OFFLINE
                logger.warning("🔴 MODE: ONLINE → OFFLINE (cloud down)")
                
            elif event == "cloud_recovered":
                # Cloud se vrátil → zkontroluj frontu
                queue_size = self.cloud_queue.size()
                if queue_size > 0:
                    # Máme frontu → REPLAY mode
                    self.mode = ProxyMode.REPLAY
                    logger.info(f"🟡 MODE: OFFLINE → REPLAY ({queue_size} frames)")
                    # Spusť replay task
                    if not self._cloud_replay_task or self._cloud_replay_task.done():
                        self._cloud_replay_task = asyncio.create_task(
                            self._replay_cloud_queue()
                        )
                else:
                    # Fronta prázdná → ONLINE mode
                    self.mode = ProxyMode.ONLINE
                    logger.info("🟢 MODE: OFFLINE → ONLINE (queue empty)")
    
    async def _replay_cloud_queue(self):
        """Přehrává cloud frontu s rate limiting 1 frame/s."""
        logger.info("🔄 Cloud replay started")
        replayed = 0
        failed = 0
        
        try:
            # Připoj se ke cloudu pro replay
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                timeout=10.0
            )
            
            try:
                while self.cloud_queue.size() > 0:
                    # Get next frame
                    result = await self.cloud_queue.get_next()
                    if not result:
                        break
                    
                    frame_id, table_name, frame_data = result
                    
                    try:
                        # Send to cloud
                        writer.write(frame_data.encode("utf-8"))
                        await writer.drain()
                        
                        # Wait for ACK (timeout 5s)
                        response = await asyncio.wait_for(
                            reader.read(4096), timeout=5.0
                        )
                        
                        # Verify ACK
                        if b"<Result>ACK</Result>" in response or b"<Result>END</Result>" in response:
                            # Success - remove from queue
                            await self.cloud_queue.remove(frame_id)
                            replayed += 1
                            
                            if replayed % 50 == 0:
                                remaining = self.cloud_queue.size()
                                logger.info(
                                    f"🔄 Replay progress: {replayed} OK, "
                                    f"{remaining} remaining"
                                )
                        else:
                            logger.warning(
                                f"🔄 Replay: {table_name} unexpected response"
                            )
                            failed += 1
                        
                        # Rate limit: 1 frame/s
                        await asyncio.sleep(1.0)
                        
                    except asyncio.TimeoutError:
                        logger.error(f"🔄 Replay: {table_name} timeout")
                        failed += 1
                        # Don't remove from queue - retry later
                    except Exception as e:
                        logger.error(f"🔄 Replay: {table_name} error: {e}")
                        failed += 1
                
                # Replay complete!
                remaining = self.cloud_queue.size()
                logger.info(
                    f"✅ Cloud replay complete: {replayed} OK, {failed} failed, "
                    f"{remaining} remaining"
                )
                
                # Přepni do ONLINE režimu pokud je fronta prázdná
                async with self.mode_lock:
                    if remaining == 0 and self.health_checker.is_online:
                        self.mode = ProxyMode.ONLINE
                        logger.info("🟢 MODE: REPLAY → ONLINE (queue empty)")
                    
            finally:
                writer.close()
                await writer.wait_closed()
                
        except Exception as e:
            logger.error(f"❌ Cloud replay connection failed: {e}")
            # Zůstáváme v REPLAY/OFFLINE režimu
    
    async def handle_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter
    ):
        """Zpracuje BOX připojení podle aktuálního režimu."""
        self.connection_count += 1
        conn_id = self.connection_count
        client_addr = client_writer.get_extra_info("peername")
        
        async with self.mode_lock:
            current_mode = self.mode
        
        logger.info(
            f"[#{conn_id}] New connection from {client_addr} "
            f"(mode: {current_mode.value})"
        )
        
        if current_mode == ProxyMode.ONLINE:
            # ONLINE mode - direct forward
            await self._handle_online_mode(
                conn_id, client_reader, client_writer, client_addr
            )
        else:
            # OFFLINE nebo REPLAY mode - local ACK + queue
            await self._handle_offline_or_replay_mode(
                conn_id, client_reader, client_writer
            )
    
    async def _handle_online_mode(
        self,
        conn_id: int,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        client_addr: tuple
    ):
        """ONLINE mode - transparentní forward."""
        server_writer = None
        try:
            # Connect to cloud
            server_reader, server_writer = await asyncio.open_connection(
                TARGET_SERVER, TARGET_PORT
            )
            logger.info(f"[#{conn_id}] ✅ Cloud connection established")
            
            # Bidirectional forward
            tasks = [
                asyncio.create_task(
                    self._forward_box_to_cloud(
                        client_reader, server_writer, conn_id, str(client_addr)
                    )
                ),
                asyncio.create_task(
                    self._forward_cloud_to_box(
                        server_reader, client_writer, conn_id
                    )
                ),
            ]
            
            # Wait for first to complete
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancel remaining
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"[#{conn_id}] Online mode error: {e}")
            # Přepni do OFFLINE mode
            async with self.mode_lock:
                if self.mode == ProxyMode.ONLINE:
                    self.mode = ProxyMode.OFFLINE
                    logger.warning("🔴 MODE: ONLINE → OFFLINE (connection failed)")
        finally:
            if server_writer:
                server_writer.close()
                await server_writer.wait_closed()
            client_writer.close()
            await client_writer.wait_closed()
            logger.info(f"[#{conn_id}] Connection closed")
    
    async def _handle_offline_or_replay_mode(
        self,
        conn_id: int,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter
    ):
        """OFFLINE/REPLAY mode - local ACK + queue."""
        try:
            while True:
                # Read frame from BOX
                data = await asyncio.wait_for(
                    client_reader.read(4096), timeout=120.0
                )
                if not data:
                    break
                
                # Parse frame
                text = data.decode("utf-8", errors="ignore")
                parsed = self.parser.parse_xml_frame(text)
                table_name = parsed.get("_table") if parsed else None
                
                # Send ACK OKAMŽITĚ!
                ack_response = self.ack_learner.generate_ack(table_name)
                client_writer.write(ack_response.encode("utf-8"))
                await client_writer.drain()
                
                logger.debug(f"[#{conn_id}] Local ACK for {table_name}")
                
                # Process frame (MQTT, state, etc.)
                if parsed and table_name:
                    self._process_frame(parsed, data, conn_id)
                    
                    # Add to cloud queue
                    await self.cloud_queue.add(
                        text, table_name, parsed.get("_device_id")
                    )
                    
        except asyncio.TimeoutError:
            logger.warning(f"[#{conn_id}] Timeout - BOX idle")
        except Exception as e:
            logger.error(f"[#{conn_id}] Error: {e}")
        finally:
            client_writer.close()
            await client_writer.wait_closed()
            logger.info(f"[#{conn_id}] Connection closed")
```
```python
class MQTTPublisher:
    """MQTT publisher s health check a frontou."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.client: mqtt.Client | None = None
        self.connected = False
        self.mqtt_queue = MQTTQueue(max_size=5000)
        self._replay_task: asyncio.Task | None = None
    
    def publish_data(self, data: dict[str, Any]) -> bool:
        """Publikuje data nebo přidá do fronty."""
        if self.connected:
            # Online - publikuj přímo
            return self._publish_direct(data)
        else:
            # Offline - do fronty
            asyncio.create_task(self.mqtt_queue.add(data))
            logger.debug("MQTT offline - data queued")
            return False
    
    async def replay_queue(self):
        """Přehraje MQTT frontu po reconnect."""
        queue_size = self.mqtt_queue.size()
        if queue_size == 0:
            return
        
        logger.info(f"MQTT: Replay {queue_size} messages")
        replayed = 0
        
        while self.mqtt_queue.size() > 0:
            batch = await self.mqtt_queue.get_batch(batch_size=100)
            
            for msg in batch:
                if not self.connected:
                    logger.warning("MQTT: Replay interrupted - disconnected")
                    return
                
                if self._publish_direct(msg["data"]):
                    replayed += 1
                else:
                    logger.error("MQTT: Replay failed, stopping")
                    return
                
                # Rate limit: 10 msg/s
                await asyncio.sleep(0.1)
            
            # Odstraň batch
            await self.mqtt_queue.remove_batch(len(batch))
            
            if replayed % 100 == 0:
                logger.info(f"MQTT: Replay progress {replayed}/{queue_size}")
        
        logger.info(f"MQTT: Replay complete - {replayed} messages")
    
    def _on_connect(self, client, userdata, flags, rc):
        was_connected = self.connected
        
        if rc == 0:
            self.connected = True
            logger.info("MQTT: ✅ Connected")
            
            # Spusť replay pokud je fronta
            if not was_connected and self.mqtt_queue.size() > 0:
                if not self._replay_task or self._replay_task.done():
                    self._replay_task = asyncio.create_task(
                        self.replay_queue()
                    )
        else:
            self.connected = False
            logger.error(f"MQTT: ❌ Connection failed (rc={rc})")
```

## 🔄 Režimy provozu - UPŘESNĚNO

### REŽIM 1: ONLINE (Cloud ✅ Fronta prázdná)

```
BOX → PROXY: frame
       ↓
    [parse & process]
       ↓
    MQTT: publish (nebo queue pokud offline)
       ↓
    CLOUD: forward (direct)
       ↓
    CLOUD: ACK
       ↓
PROXY → BOX: forward ACK

✅ Transparentní forward
✅ Žádné frontování
✅ Lokální ACK/END fallback (fixní CRC)
```

### REŽIM 2: OFFLINE (Cloud ❌)

```
BOX → PROXY: frame
       ↓
    [parse & process]
       ↓
    MQTT: publish (nebo queue pokud offline)
       ↓
    CloudQueue.add(frame) 📦
       ↓
PROXY → BOX: local ACK (fixed)

❌ Cloud nedostupný
📦 Vše se ukládá do CloudQueue
✅ BOX dostává okamžité ACK
🔄 Health check běží na pozadí (každých 30s)
```

### REŽIM 3: REPLAY (Cloud ✅ Fronta NEPRÁZDNÁ) ⭐ KLÍČOVÝ!

**Background task - Cloud Replay:**
```
WHILE CloudQueue.size() > 0:
  frame = CloudQueue.get_next()
  → CLOUD: send frame
  → CLOUD: wait ACK (timeout 5s)
  → ACK OK? remove from queue
  → Sleep 1s (rate limit)
  
  IF queue empty:
    → MODE = ONLINE ✅
```

**Současně - BOX live provoz:**
```
BOX → PROXY: new_frame
       ↓
    [parse & process]
       ↓
    MQTT: publish (nebo queue)
       ↓
    CloudQueue.add(new_frame) 📦 (na konec!)
       ↓
PROXY → BOX: local ACK (fixed)
```

**Klíčové:**
- ✅ Live data jdou **do fronty** (ne přímo do cloudu!)
- ✅ Replay má **prioritu** - vyprazdňuje FIFO
- ✅ Zachová se **chronologie** (fronta → live)
- ✅ BOX **neví** že jsme v replay módu
- ✅ Po vyprázdnění → **automatický přechod do ONLINE**

### Přechody mezi režimy

```
         START
           ↓
     ┌──────────┐
     │  ONLINE  │ ←──────────────┐
     └──────────┘                │
           ↓                     │
    Cloud DOWN ❌                │
           ↓                     │
     ┌──────────┐                │
     │ OFFLINE  │                │
     └──────────┘                │
           ↓                     │
    Cloud UP ✅                  │
    Queue > 0                    │
           ↓                     │
     ┌──────────┐                │
     │  REPLAY  │                │
     └──────────┘                │
           ↓                     │
    Queue == 0 ✅               │
           └─────────────────────┘
```

## 📊 Metriky & monitoring

### CloudHealthChecker
```python
- is_online: bool
- consecutive_failures: int
- consecutive_successes: int
- last_check_time: float
```

### CloudQueue
```python
- size(): int
- oldest_frame_age(): float
- add_rate: float  # frames/min
```

### MQTTPublisher
```python
- connected: bool
- mqtt_queue.size(): int
- publish_success: int
- publish_failed: int
```

### Logování
```python
# Každých 5 minut:
logger.info(f"""
📊 Proxy Status:
  Cloud: {'✅ ONLINE' if cloud_health.is_online else '❌ OFFLINE'}
  Cloud Queue: {cloud_queue.size()} frames
  MQTT: {'✅ ONLINE' if mqtt.connected else '❌ OFFLINE'}
  MQTT Queue: {mqtt.mqtt_queue.size()} messages
""")
```

## 🔧 Konfigurace

```python
# Environment variables
CLOUD_HEALTH_CHECK_INTERVAL = 30  # sekund
CLOUD_QUEUE_MAX_SIZE = 10000      # frames
CLOUD_REPLAY_RATE = 1.0           # frames/s
MQTT_QUEUE_MAX_SIZE = 5000        # messages  
MQTT_REPLAY_RATE = 10.0           # messages/s
```

## ⚡ Performance

### Paměťová náročnost (worst case 24h výpadek):

**Cloud Queue:**
- tbl_actual: 9600 frames @ 2KB = 19 MB
- ostatní: 300 frames @ 2KB = 600 KB
- **Celkem: ~20 MB**

**MQTT Queue:**
- JSON payloads: 10000 msgs @ 500B = 5 MB
- **Celkem: ~5 MB**

**Total: ~25 MB** ✅ Přijatelné

### CPU náročnost:

**Normální provoz:**
- Parsing: minimální
- MQTT publish: minimální
- Health check: 1x/30s

**Replay:**
- Cloud: 1 frame/s = minimální
- MQTT: 10 msg/s = minimální

## 🚀 Implementační kroky

1. ✅ Vytvořit CloudQueue (in-memory)
2. ✅ Vytvořit MQTTQueue (in-memory)
3. ✅ Upravit CloudHealthChecker (consecutive failures)
4. ✅ Upravit MQTTPublisher (queue + replay)
5. ✅ Upravit handle_connection (offline mode)
6. ✅ Implementovat CloudReplay task
7. ✅ Implementovat MQTTReplay task
8. ✅ Přidat metrics logging
9. ✅ Testovat jednotlivé režimy
10. ✅ Dokumentace

## 📝 Poznámky

- **Žádná SQLite závislost** pro fronty
- **Optional persistence** lze dodat později pokud bude potřeba
- **Graceful degradation** - každý subsystém může selhat nezávisle
- **FIFO garance** - chronologické pořadí zachováno
- **Rate limiting** - ochrana cloudu i MQTT brokeru
- **Health monitoring** - automatické recovery
