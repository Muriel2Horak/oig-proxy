# OIG Proxy - Modular Architecture

Modulární implementace OIG Proxy s podporou ONLINE/HYBRID/OFFLINE režimů.

## Struktura modulů

```
addon/oig-proxy/
├── main.py                # Entry point
├── config.py              # Konfigurace a env vars
├── models.py              # Data modely a enums
├── utils.py               # Helper funkce, sensor map, HA integrace
├── parser.py              # XML frame parser
├── oig_frame.py           # CRC výpočet, frame utilities (RESULT_ACK/END)
├── proxy.py               # OIGProxy orchestrace (~660 lines)
├── hybrid_mode.py         # HybridModeManager - HYBRID režim, fail threshold, retry
├── telemetry_collector.py # TelemetryCollector - sběr a odesílání telemetrie
├── telemetry_client.py    # TelemetryClient - MQTT klient pro telemetrii
├── control_pipeline.py    # ControlPipeline - zpracování SET příkazů z HA
├── control_settings.py    # ControlSettings - validace a sestavení control frames
├── mqtt_state_cache.py    # MqttStateCache - cache MQTT stavu tabulek
├── cloud_forwarder.py     # CloudForwarder - TCP spojení s cloudem
├── mode_persistence.py    # ModePersistence - perzistence režimu přes restart
├── proxy_status.py        # ProxyStatusReporter - MQTT status senzory
├── mqtt_publisher.py      # MQTTPublisher - lokální MQTT broker klient
├── control_api.py         # HTTP API pro ovládání z HA
├── Dockerfile             # Alpine kontejner pro HA addon
└── run                    # Entrypoint skript
```

## Proxy režimy

### ONLINE
- Cloud dostupný
- Transparentní forward: BOX ↔ Proxy ↔ Cloud
- ACK od cloudu
- Lokální ACK/END fallback s fixním CRC při timeout

### HYBRID
- Konfigurovaný režim s automatickým fallbackem
- Při dosažení fail threshold → přepne na lokální ACK (in_offline)
- Periodicky zkouší cloud (retry interval)
- Po úspěšném cloudu → reset fail counteru

### OFFLINE
- Vždy lokální ACK generování (fixní ACK/END s CRC)
- Žádná komunikace s cloudem

## Architektura

```
BOX ←TCP→ OIGProxy ←TCP→ Cloud (oigservis.cz)
               │
               ├── HybridModeManager (_hm) - správa režimů
               ├── CloudForwarder (_cf) - TCP session s cloudem
               ├── TelemetryCollector (_tc) - sběr metrik
               ├── ControlPipeline (_ctrl) - SET příkazy z HA
               ├── ControlSettings (_cs) - validace parametrů
               ├── MqttStateCache (_msc) - cache MQTT stavu
               ├── ModePersistence (_mp) - perzistence režimu
               ├── ProxyStatusReporter (_ps) - MQTT status
               └── MQTTPublisher - lokální MQTT broker
```

DNS override (dnsmasq): `oigservis.cz → HA IP` → BOX se připojí k proxy místo cloudu.

## Persistence

### TableState (`/data/prms_state.json`)
- Snapshot posledních známých hodnot tabulek
- Po startu a MQTT reconnectu se znovu publikuje (aby senzory nebyly `unknown`)

## Testování

```bash
# Unit testy
cd /Users/martinhorak/Projects/oig-proxy
PYTHONPATH=addon/oig-proxy:tests python3 -m pytest tests/ -x -q

# Pylint (CI flags)
PYTHONPATH=addon/oig-proxy:tests python3 -m pylint addon/oig-proxy/*.py tests/*.py \
  --disable=import-outside-toplevel,unused-import,reimported,redefined-outer-name \
  --disable=line-too-long,f-string-without-interpolation,comparison-of-constants,comparison-with-itself,unused-argument,wrong-import-order

# Mypy
MYPYPATH=addon/oig-proxy python3 -m mypy addon/oig-proxy/*.py --ignore-missing-imports
```

## Environment variables

Klíčové proměnné:
- `DEVICE_ID` - volitelné, `AUTO` = detekce z BOX komunikace
- `PROXY_LISTEN_HOST` - default `0.0.0.0`
- `PROXY_LISTEN_PORT` - default `5710`
- `PROXY_MODE` - `online` / `hybrid` / `offline`
- `CLOUD_ACK_TIMEOUT` - default `1800.0` (s) - max čekání na ACK z cloudu
- `HYBRID_FAIL_THRESHOLD` - default `3` - počet selhání před fallbackem
- `HYBRID_RETRY_INTERVAL` - default `60` (s)
- `HYBRID_CONNECT_TIMEOUT` - default `5` (s)

Více viz `config.py`.

## Klíčové změny oproti monolitu

1. **Modulární** - 16+ souborů místo jednoho ~3850 řádků monolitu
2. **3 režimy** - ONLINE / HYBRID / OFFLINE (HYBRID nahradil REPLAY)
3. **Telemetrie** - anonymní usage metrics přes MQTT
4. **Control API** - HTTP API pro ovládání z Home Assistant
5. **Type checking** - mypy v CI, pylint 10.00/10
