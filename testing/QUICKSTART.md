# 🚀 Rychlý start - Testování

## Příprava testovacích dat

```bash
cd /Users/martinhorak/Projects/oig-proxy/testing

# 1. Extrahuj reálné frames z databáze
python3 test_data/extract_frames.py
```

**Výstup:**
```
✅ Extracted 100 frames...
   → test_data/box_frames_100.json
   → test_data/box_frames_actual.json  
   → test_data/box_frames_5min.json
```

## Test 1: ONLINE Mode (smoke test) ✅

**Co testuje:** Základní průchod proxy (BOX → Proxy → Cloud)

```bash
./test_online_mode.sh
```

**Expected:**
- ✅ 100% ACK rate
- ✅ Všechny frames doručeny do cloudu
- ✅ Queue prázdná (direct forward)

**Trvání:** ~15 sekund

---

## Test 3: REPLAY Mode ⭐ (klíčový!)

**Co testuje:** Vyprazdňování fronty + FIFO pořadí

```bash
./test_replay_mode.sh
```

**Scénář:**
1. Naplní frontu v OFFLINE režimu (70 frames)
2. Spustí cloud (recovery)
3. Proxy přejde do REPLAY
4. Paralelně pošle nové live frames (50 frames)
5. Validuje FIFO pořadí
6. Ověří přechod do ONLINE

**Expected:**
- ✅ OFFLINE → REPLAY → ONLINE transitions
- ✅ FIFO pořadí: [queued frames] → [live frames]
- ✅ Všechny frames doručeny
- ✅ Queue prázdná na konci

**Trvání:** ~3 minuty

---

## Manuální test s live sledováním

### Terminal 1: Mock Cloud
```bash
cd testing
python3 mock_cloud_server.py
```

### Terminal 2: Proxy
```bash
cd addon/oig-proxy
export MQTT_HOST=localhost
export TARGET_SERVER=localhost
export LOG_LEVEL=DEBUG
python3 main.py
```

### Terminal 3: Mock BOX
```bash
cd testing
python3 mock_box_client.py --data test_data/box_frames_100.json --rate-limit 1.0
```

### Terminal 4: Sledování
```bash
# Queue size
watch -n 1 'sqlite3 /tmp/cloud_queue.db "SELECT COUNT(*) FROM queue" 2>/dev/null || echo 0'

# Proxy logs
tail -f /tmp/proxy.log | grep MODE
```

---

## Debug

### Inspekce front

```bash
# Cloud queue
sqlite3 /tmp/cloud_queue.db "SELECT id, table_name, queued_at FROM queue LIMIT 10;"

# MQTT queue  
sqlite3 /tmp/mqtt_queue.db "SELECT COUNT(*) FROM queue;"
```

### Přijaté frames na cloudu

```bash
cat mock_cloud_frames.json | jq '.total_frames'
cat mock_cloud_frames.json | jq '.tables'
cat mock_cloud_frames.json | jq '.frames[0]'
```

### Logování proxy

```bash
# Všechny mode transitions
grep "MODE:" /tmp/proxy_test*.log

# Health check
grep "Cloud" /tmp/proxy_test*.log | grep -E "ONLINE|OFFLINE"

# Queue operations
grep "CloudQueue" /tmp/proxy_test*.log

# Replay progress
grep "Replay" /tmp/proxy_test*.log
```

---

## Očekávané výsledky

### ✅ Test 1 (ONLINE)
```
Frames sent:     100
ACKs received:   100
Success rate:    100.0%
Cloud received:  100 frames
Queue size:      0
```

### ✅ Test 3 (REPLAY)
```
Phase 1 (OFFLINE):
  Queued: 70 frames
  
Phase 2 (REPLAY):
  MODE: OFFLINE → REPLAY
  Replay: 70 frames (1 frame/s)
  Live: 50 frames (added to queue)
  
Phase 3 (COMPLETE):
  MODE: REPLAY → ONLINE
  Cloud received: 120 frames (70 + 50)
  FIFO order: ✓
  Queue: 0 frames
```

---

## Troubleshooting

### Port už používán
```bash
# Zkontroluj co běží na 5710
lsof -i :5710

# Zabij staré procesy
pkill -f mock_cloud_server
pkill -f main.py
```

### Databáze neexistuje
```bash
# Zkopíruj z addon
cp addon/oig-proxy/__pycache__/payloads.db testing/
```

### Permission denied
```bash
chmod +x test_*.sh
chmod +x test_data/extract_frames.py
```
