# OIG Proxy v2.0 - Specifikace nové verze

## Základní informace

**Verze**: 2.0.0  
**Cíl**: Úplně nová implementace od nuly  
**Důvod**: Současný kód (v1.6.x) je neudržovatelný, plný technického dluhu  

---

## Architektura v2.0

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────┐
│   OIG Box   │────▶│  OIG Proxy v2.0 │────▶│  Cloud       │
│             │     │   (Port 5710)   │     │oigservis.cz: │
│ Device ID   │     │                 │     │5710          │
└─────────────┘     └────────┬────────┘     └──────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  MQTT Broker    │
                    │  (Home Assistant)│
                    └─────────────────┘
```

---

## Klíčové komponenty v2.0

### 1. Transport Layer
- **TCP Server**: Port 5710, přijímá připojení od Boxu
- **Cloud Client**: Persistentní spojení s oigservis.cz:5710
- **Failover**: Automatický přepínání ONLINE ↔ HYBRID ↔ OFFLINE

### 2. Protocol Parser
- **XML Parser**: Parsuje frame z Boxu
- **ACK Generator**: Generuje ACK v HYBRID/OFFLINE módu
- **Validator**: Kontroluje CRC a strukturu

### 3. MQTT Publisher
- **State Publisher**: Publikuje `oig_local/{device_id}/{table}/state`
- **Discovery**: Auto-discovery pro Home Assistant
- **Force Update**: Vždy publikovat (pro správné timestampy v HA)

### 4. Mode Manager
- **Online Mode**: Normální provoz (cloud dostupný)
- **Hybrid Mode**: Cloud nefunguje, generujeme lokalní ACK
- **Offline Mode**: Úplný výpadek, čekáme na obnovení

### 5. Twin/Sidecar (Wave 2)
- **Activation**: Při cloud výpadku (threshold=3 fails)
- **Hysteresis**: Deaktivace po 5 minutách stabilního cloudu
- **Local Control**: Zpracování nastavení v offline módu

---

## Protokol - Detailní specifikace

### Formát zpráv

**Box → Proxy** (HTTP/1.1 POST):
```xml
<Frame>
    <TblName>tbl_batt_prms</TblName>
    <ID_Device>2206237016</ID_Device>
    <DT>2025-12-18T19:08:17.557413</DT>
    <ID_SubD>0</ID_SubD>
    <Voltage>48.5</Voltage>
    <Current>25.3</Current>
    ...
</Frame>
```

**Cloud → Proxy** (ACK):
```xml
<ACK>
    <TblName>tbl_batt_prms</TblName>
    <Status>OK</Status>
    <DT>2025-12-18T19:08:17.589465</DT>
</ACK>
```

**MQTT Topic**:
```
oig_local/2206237016/tbl_batt_prms/state
```

### Tabulky (kompletní seznam)

| Tabulka | Frekvence | Popis | Obsahuje data pro senzory |
|---------|-----------|-------|---------------------------|
| tbl_actual | ~5 min | Aktuální hodnoty | ✅ ANO |
| tbl_ac_out | ~5 min | AC výstup | ✅ ANO |
| tbl_ac_in | ~5 min | AC vstup | ✅ ANO |
| tbl_batt | ~5 min | Baterie | ✅ ANO |
| tbl_batt_prms | ~5 min | Parametry baterie | ✅ ANO |
| tbl_boiler | ~5 min | Kotel/topení | ✅ ANO |
| tbl_box | ~5 min | Box info | ✅ ANO |
| tbl_dc_in | ~5 min | DC vstup | ✅ ANO |
| tbl_events | řídké | Události | ✅ ANO |
| tbl_invertor | ~5 min | Střídač | ✅ ANO |
| **IsNewSet** | ~20-30s | Polling nastavení | ✅ ANO (ACO_P, FV_P1, Temp, atd.) |
| **IsNewWeather** | ~20-30s | Polling počasí | ✅ ANO |
| **IsNewFW** | ~20-30s | Polling firmware | ✅ ANO |

**Poznámky**:
- ID_SubD > 0 se ignoruje (inactive battery bank)
- IsNewSet/IsNewWeather/IsNewFW obsahují stejná data jako tbl_actual
- Všechny tabulky se mapují na stejné MQTT sensory

### Timing

- **ACK timeout**: 10 sekund
- **Hybrid retry interval**: 60 sekund
- **Offline threshold**: 3 consecutive failures
- **Hysteresis**: 300 sekund (5 minut)

---

## Módy provozu

### Online Mode (výchozí)
```
Box → Proxy → Cloud → ACK → Proxy → Box
                    ↓
                    MQTT
```
- Všechna data jdou přes cloud
- MQTT publikace z ACK odpovědí

### Hybrid Mode (cloud nefunguje)
```
Box → Proxy → [generujeme ACK lokálně] → Box
        ↓
        MQTT
```
- Proxy generuje ACK místo cloudu
- Data se neposílají do cloudu
- Retry každých 60s

### Offline Mode (dlouhodobý výpadek)
```
Box → Proxy → [generujeme ACK lokálně] → Box
        ↓
        MQTT
        ↓
    [čekáme na obnovení cloudu]
```
- Aktivuje se po 3 failnutých pokusech
- Čeká se na stabilní cloud (5 minut)

---

## Implementační vlny

### Wave 1: Základní funkčnost (MVP)
✅ TCP Server na portu 5710  
✅ XML Parser pro Box framech  
✅ Cloud Client (oigservis.cz:5710)  
✅ MQTT Publisher  
✅ Online Mode (forward do cloudu + MQTT)  
✅ ACK forwarding  
✅ Auto-discovery pro HA  

### Wave 2: Hybrid/Offline módy
✅ Mode Manager (ONLINE/HYBRID/OFFLINE)  
✅ Local ACK generator  
✅ Cloud health check  
✅ Failover logika  
✅ Recovery detection  

### Wave 3: Twin/Sidecar
✅ Activation trigger (3 fails)  
✅ Deactivation hysteresis (5 min)  
✅ Local control handling  
✅ Settings persistence  

### Wave 4: Monitoring & Telemetry
✅ Health metrics  
✅ Performance stats  
✅ Cloud gap detection  
✅ Diagnostics  

---

## Konfigurace

```python
# config.py
DEVICE_ID = "AUTO"  # nebo konkrétní ID
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 5710
CLOUD_HOST = "oigservis.cz"
CLOUD_PORT = 5710

# MQTT
MQTT_BROKER = "core-mosquitto"
MQTT_PORT = 1883
MQTT_NAMESPACE = "oig_local"

# Timing
ACK_TIMEOUT = 10.0
HYBRID_RETRY_INTERVAL = 60.0
OFFLINE_THRESHOLD = 3
HYSTERESIS_TIME = 300.0

# Feature flags
TWIN_ENABLED = True
LEGACY_FALLBACK = False
```

---

## Acceptance Criteria

### Funkční požadavky
- [ ] Box se připojí na port 5710
- [ ] Všechny tbl_* tabulky se publikují na MQTT
- [ ] Cloud dostává data v Online módu
- [ ] MQTT publikace funguje i v Hybrid/OFFLINE módu
- [ ] Timestampy v HA se aktualizují každých ~30s
- [ ] Discovery funguje pro všechny sensory

### Výkonnostní požadavky
- [ ] Latence < 50ms (Box → MQTT)
- [ ] Zero data loss při cloud výpadku
- [ ] Automatic recovery po obnovení cloudu

### Robustnost
- [ ] Graceful handling disconnectů
- [ ] No memory leaks
- [ ] Clear logging pro debug
- [ ] Configurable log levels

---

## Technické rozhodnutí

### Jazyk: Python 3.11+
- Asyncio pro concurrency
- Pydantic pro validaci
- aio-mqtt pro MQTT

### Architektura: Clean Architecture
- Core: Domain logic
- Adapters: TCP, MQTT, Cloud
- Use Cases: Business logic
- Config: Settings

### Testing
- Unit tests: 90%+ coverage
- Integration tests: Box simulation
- E2E tests: Full flow

---

## Risks & Mitigation

| Risk | Mitigation |
|------|------------|
| Protocol changes | Maintain compatibility layer |
| Performance issues | Profiling + optimization |
| Memory leaks | Regular testing + monitoring |
| Breaking changes | Feature flags + gradual rollout |

---

## Success Criteria

✅ Senzory v HA se aktualizují každých 30-60 sekund  
✅ Cloud má všechna data  
✅ MQTT funguje i při výpadku cloudu  
✅ Žádné memory leaky  
✅ Čistý, udržovatelný kód  
✅ Snadné debugování  

---

**Status**: READY FOR DEVELOPMENT  
**Next Step**: Create work plan for Wave 1
