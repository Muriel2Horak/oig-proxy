# Testovací prostředí pro OIG Proxy

## 🎯 Cíl

Otestovat všechny režimy (ONLINE/OFFLINE/REPLAY) lokálně před nasazením na server.

## 📦 Co máme k dispozici

### 1. **Reálná data z databáze** ✅
```
/Users/martinhorak/Projects/oig-proxy/addon/oig-proxy/__pycache__/payloads.db
```
- 36,993 reálných frames z live provozu
- BOX → Cloud komunikace
- Cloud → BOX ACK odpovědi
- Timestampy, tabulky, device_id

### 2. **Analýzy a znalosti** ✅
- ACK patterns (92.4% identických)
- Frekvence tabulek (tbl_actual ~9s, ostatní ~5min)
- Reconnect behavior po výpadku
- BOX očekávání a timeouty

## 🧪 Testovací scénáře

### Scénář 1: ONLINE režim (smoke test)
**Účel:** Ověřit že základní forward funguje

**Setup:**
- Spustit mock cloud server (přijímá frames, posílá ACK)
- Spustit proxy v ONLINE režimu
- Přehrát reálné BOX frames z DB

**Test:**
```bash
./test_online_mode.sh
```

**Expected:**
- ✅ Frames projdou proxy → mock cloud
- ✅ ACK se vrátí zpět
- ✅ MQTT publikování funguje
- ✅ Žádné frontování

---

### Scénář 2: OFFLINE režim
**Účel:** Ověřit local ACK + frontování

**Setup:**
- Mock cloud server NEDOSTUPNÝ (port zavřený)
- Spustit proxy (detekuje offline)
- Přehrát BOX frames

**Test:**
```bash
./test_offline_mode.sh
```

**Expected:**
- ✅ Proxy přechod ONLINE → OFFLINE
- ✅ BOX dostává local ACK (fixní ACK/END s CRC)
- ✅ Frames se ukládají do CloudQueue (SQLite)
- ✅ MQTT publikování (pokud broker online)
- ✅ Health check běží (každých 30s)

**Validace:**
```sql
SELECT COUNT(*) FROM cloud_queue.queue;
-- Mělo by odpovídat počtu poslaných frames
```

---

### Scénář 3: REPLAY režim (kritický!)
**Účel:** Ověřit vyprazdňování fronty + FIFO

**Setup:**
1. Naplnit frontu (OFFLINE režim)
2. Spustit mock cloud server
3. Čekat na auto-recovery
4. Paralelně posílat nové live frames

**Test:**
```bash
./test_replay_mode.sh
```

**Expected:**
- ✅ Health check detekuje cloud recovery
- ✅ Přechod OFFLINE → REPLAY
- ✅ Replay task startuje (1 frame/s)
- ✅ Live frames jdou do fronty (konec)
- ✅ FIFO pořadí zachováno
- ✅ Po vyprázdnění → ONLINE režim

**Validace:**
```python
# Check chronologie na mock cloud serveru
# Měly by přijít: [queued1, queued2, ..., live1, live2, ...]
```

---

### Scénář 4: MQTT offline/recovery
**Účel:** Ověřit MQTT frontu nezávisle

**Setup:**
- MQTT broker OFFLINE při startu
- Proxy dostává frames
- Pak spustit MQTT broker

**Test:**
```bash
./test_mqtt_recovery.sh
```

**Expected:**
- ✅ Data jdou do MQTTQueue
- ✅ MQTT reconnect po startu brokeru
- ✅ Replay 10 msg/s
- ✅ Všechny messages publikovány

---

### Scénář 5: Totální chaos (stress test)
**Účel:** Vícenásobné výpadky a recovery

**Test:**
```bash
./test_chaos.sh
```

**Simulace:**
1. Start ONLINE
2. Cloud DOWN (10s) → OFFLINE → fronta 100 frames
3. Cloud UP → REPLAY
4. Během replay: MQTT DOWN (5s)
5. Během replay: Cloud DOWN znovu (5s)
6. Všechno UP → dokončit replay → ONLINE

**Expected:**
- ✅ Všechny transitions správně
- ✅ Žádná ztráta dat
- ✅ Fronty se správně spravují
- ✅ Konečný stav: vše ONLINE, fronty prázdné

---

## 🛠️ Testovací nástroje

### 1. Mock Cloud Server

```python
# testing/mock_cloud_server.py
"""
Simuluje OIG cloud server:
- Přijímá TCP spojení na portu 5710
- Přijímá frames
- Posílá ACK responses (fixní ACK/END s CRC)
- Loguje všechny frames pro validaci
"""
```

**Features:**
- Reálné ACK patterns z analýzy
- Konfigurovatelné timeouty
- Simulace výpadků (on-demand shutdown)
- Validace CRC
- Logging všech frames do CSV

### 2. Mock BOX Client

```python
# testing/mock_box_client.py
"""
Simuluje OIG BOX:
- Přehrává reálné frames z DB
- Čeká na ACK (timeout detection)
- Konfigurovatelná rychlost
- Může simulovat reconnect behavior
"""
```

**Features:**
- Replay z SQLite DB (filtrovat podle času/tabulky)
- Rate limiting (9s pro actual, 5min pro ostatní)
- ACK timeout detection → error
- Statistiky (odesláno, ACK, timeouts)

### 3. MQTT Mock Broker

```python
# testing/mock_mqtt_broker.py
"""
Jednoduchý MQTT broker pro testy:
- Přijímá MQTT publish
- Loguje všechny messages
- Konfigurovatelné výpadky
"""
```

Nebo použít **mosquitto** v Dockeru:
```bash
docker run -d -p 1883:1883 eclipse-mosquitto
```

### 4. Test Runner

```python
# testing/test_runner.py
"""
Orchestruje testy:
- Spouští mock servery
- Kontroluje assertions
- Generuje report
"""
```

---

## 📁 Struktura testing/

```
testing/
├── README.md                    # tento soubor
├── mock_cloud_server.py         # Mock OIG cloud
├── mock_box_client.py           # Mock BOX (replay z DB)
├── test_online_mode.sh          # Test 1
├── test_offline_mode.sh         # Test 2
├── test_replay_mode.sh          # Test 3
├── test_mqtt_recovery.sh        # Test 4
├── test_chaos.sh                # Test 5 (stress)
├── test_runner.py               # Orchestrátor
├── assertions.py                # Validační funkce
├── test_data/
│   ├── extract_frames.py        # Export frames z payloads.db
│   ├── box_frames_100.json      # 100 reálných frames
│   ├── box_frames_actual.json   # Jen tbl_actual (high freq)
│   └── box_frames_5min.json     # Jen 5min tabulky
└── results/
    ├── test_1_online.log
    ├── test_2_offline.log
    ├── test_3_replay.log
    ├── test_4_mqtt.log
    └── test_5_chaos.log
```

---

## 🚀 Jak spustit testy

### Příprava

```bash
cd /Users/martinhorak/Projects/oig-proxy/testing

# 1. Extrahuj testovací data z DB
python test_data/extract_frames.py

# 2. Nastav prostředí
export MQTT_HOST=localhost
export TARGET_SERVER=localhost
export PROXY_PORT=5710
export LOG_LEVEL=DEBUG
```

### Spuštění jednotlivých testů

```bash
# Test 1: ONLINE mode
./test_online_mode.sh

# Test 2: OFFLINE mode
./test_offline_mode.sh

# Test 3: REPLAY mode (klíčový!)
./test_replay_mode.sh

# Test 4: MQTT recovery
./test_mqtt_recovery.sh

# Test 5: Chaos (stress)
./test_chaos.sh
```

### Spuštění všech testů

```bash
python test_runner.py --all --report results/
```

---

## ✅ Validace

### Co kontrolovat

1. **Žádná ztráta dat**
   ```python
   sent_frames = count_frames_sent_by_box()
   received_frames = count_frames_at_cloud()
   assert sent_frames == received_frames
   ```

2. **FIFO pořadí**
   ```python
   expected_order = ["frame1", "frame2", "live1", "live2"]
   actual_order = get_frames_at_cloud()
   assert expected_order == actual_order
   ```

3. **Režim transitions**
   ```python
   assert_transition("ONLINE", "OFFLINE", cloud_down_timestamp)
   assert_transition("OFFLINE", "REPLAY", cloud_up_timestamp)
   assert_transition("REPLAY", "ONLINE", queue_empty_timestamp)
   ```

4. **ACK responses**
   ```python
   for frame in box_frames:
       assert received_ack(frame, timeout=2.0)
   ```

5. **Fronta persistence**
   ```python
   # Restart proxy během OFFLINE
   restart_proxy()
   queue_size_after = check_queue_size()
   assert queue_size_after == queue_size_before
   ```

---

## 📊 Metriky

### Po každém testu měřit:

```
✅ Success rate: X/Y frames delivered
✅ ACK latency: avg, p50, p95, p99
✅ Queue size: max, avg
✅ Mode transitions: count, duration
✅ Errors: timeouts, exceptions
✅ Performance: CPU, memory
```

---

## 🐛 Debug

### Logování

```bash
# Všechny logy v DEBUG level
tail -f addon/oig-proxy/main.log

# Jen režim transitions
grep "MODE:" addon/oig-proxy/main.log

# Jen fronty
grep "Queue" addon/oig-proxy/main.log
```

### Inspekce databází

```bash
# CloudQueue
sqlite3 /data/cloud_queue.db "SELECT COUNT(*) FROM queue;"
sqlite3 /data/cloud_queue.db "SELECT * FROM queue ORDER BY id LIMIT 10;"

# MQTTQueue
sqlite3 /data/mqtt_queue.db "SELECT COUNT(*) FROM queue;"

# Payloads (testovací data)
sqlite3 addon/oig-proxy/__pycache__/payloads.db "SELECT COUNT(*) FROM frames WHERE direction='box_to_cloud';"
```

---

## 🎓 Očekávané výsledky

### ✅ Test 1 (ONLINE)
- 100% delivery rate
- <50ms ACK latency
- 0 frames v frontách

### ✅ Test 2 (OFFLINE)
- 100% ACK rate (local)
- Všechny frames v CloudQueue
- MQTT publikování OK

### ✅ Test 3 (REPLAY)
- 100% delivery po replay
- Správné FIFO pořadí
- Automatický přechod do ONLINE

### ✅ Test 4 (MQTT)
- Všechny messages doručeny po recovery
- Replay <10s pro 1000 messages

### ✅ Test 5 (CHAOS)
- Žádná ztráta dat
- Správné recovery ze všech situací
- Konečný stav: clean (fronty prázdné)

---

## 📝 Poznámky

- Testy používají **reálná data** z live provozu
- Mock servery **simulují reálné chování** (timeouty, ACK patterns)
- Lze testovat **bez fyzického BOXu**
- **Reprodukovatelné** - stejné výsledky při opakování
- **Izolované** - žádný vliv na produkční systém
