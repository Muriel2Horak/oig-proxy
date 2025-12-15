# OIG Proxy - Modular Architecture

Nová modulární implementace s podporou ONLINE/OFFLINE/REPLAY režimů.

## Struktura modulů

```
addon/oig-proxy/
├── main.py              # Entry point
├── config.py            # Konfigurace a env vars (95 lines)
├── models.py            # Data modely a enums (75 lines)
├── utils.py             # Helper funkce (291 lines)
├── parser.py            # XML frame parser (100 lines)
├── cloud_manager.py     # CloudQueue, CloudHealthChecker, ACKLearner (360 lines)
├── mqtt_publisher.py    # MQTTPublisher s frontou (568 lines)
├── proxy.py             # OIGProxy orchestrace (300 lines)
└── main_old.py          # Legacy monolit (deprecated)
```

**Celkem:** ~1869 lines (vs. original 1601 lines)
- Přidáno: +268 lines nové funkcionality (SQLite queues, REPLAY mode, callbacks)
- Struktura: 8 samostatných modulů místo monolitu

## Proxy režimy

### 🟢 ONLINE
- Cloud dostupný + fronta prázdná
- Transparentní forward: BOX ↔ Proxy ↔ Cloud
- ACK od cloudu
- Učení ACK patterns z cloud responses

### 🔴 OFFLINE  
- Cloud nedostupný
- Lokální ACK generování (naučené patterns)
- Frames do CloudQueue (SQLite)
- MQTT data do MQTTQueue pokud broker offline

### 🟡 REPLAY
- Cloud se vrátil + fronta neprázdná
- Replay fronty (1 frame/s)
- Nové live frames → append na konec fronty (FIFO zachováno)
- Po vyprázdnění → automatický přechod na ONLINE

## Persistence

### CloudQueue (`/data/cloud_queue.db`)
- Max 10,000 frames
- FIFO pořadí
- Přežije restart proxy

### MQTTQueue (`/data/mqtt_queue.db`)
- Max 5,000 messages
- Replay po reconnectu (10 msg/s)
- Přežije restart proxy

### TableState (`/data/prms_state.json`)
- Snapshot posledních známých hodnot tabulek (typicky pomalé/konfigurační `tbl_*`)
- Po startu a MQTT reconnectu se znovu publikuje do MQTT (aby senzory nebyly `unknown`)

### PayloadsDB (`/data/payloads.db`)
- Debug capture všech frames
- BOX rx/tx, Cloud rx/tx

## Testování

### Import test
```bash
cd /Users/martinhorak/Projects/oig-proxy/addon/oig-proxy
python3 -c "
import config, models, utils, parser, cloud_manager, mqtt_publisher, proxy
print('✅ OK')
"
```

### Použití testing infrastructure
```bash
cd /Users/martinhorak/Projects/oig-proxy/testing

# 1. Extrahuj real data z DB
python3 test_data/extract_frames.py

# 2. Smoke test - ONLINE režim
./test_online_mode.sh

# 3. Critical test - REPLAY režim
./test_replay_mode.sh
```

## Migrace z main.py na main_new.py

```bash
# Backup original
cp main.py main_old.py

# Replace with new
mv main_new.py main.py

# Update run script pokud potřeba
# (Dockerfile už spouští main.py)
```

## Environment variables

Nové/změněné:
- `DEVICE_ID` - volitelné (pokud není, detekuje se z BOX komunikace)
- `PROXY_LISTEN_HOST` - default `0.0.0.0`
- `PROXY_LISTEN_PORT` - default `5710`
- `PROXY_DEVICE_ID` - default `oig_proxy` (proxy/status/event senzory jdou sem)
- `CLOUD_REPLAY_RATE` - Default: `1.0` (frames/s)
- `MQTT_REPLAY_RATE` - Default: `10.0` (msg/s)
- `CLOUD_QUEUE_MAX_SIZE` - Default: `10000`
- `MQTT_QUEUE_MAX_SIZE` - Default: `5000`

Více viz `config.py`.

## Klíčové změny oproti original

1. **3 režimy** místo 2 (přidán REPLAY)
2. **SQLite persistence** místo in-memory
3. **Automatické transitions** (cloud down/recovered)
4. **FIFO garantováno** během REPLAY
5. **Callback systém** pro mode changes
6. **Rate limiting** na replay (1 frame/s cloud, 10 msg/s MQTT)
7. **Modulární** (8 souborů místo 1)

## Next Steps

- [ ] Integration test s mock servery
- [ ] Production deployment test
- [ ] Mode transitions logging/metrics
- [ ] Queue age monitoring
- [ ] Grafana dashboard pro režimy
