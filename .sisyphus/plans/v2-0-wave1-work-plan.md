# Work Plan: OIG Proxy v2.0 - Wave 1 (Základní funkčnost)

## Wave 1 Scope (STRICT)

### IN SCOPE ✅
1. **TCP Server** - Port 5710, přijímá připojení od OIG Boxu
2. **XML Parser** - Parsuje XML frame od Boxu (tbl_actual, tbl_ac_out, atd.)
3. **Cloud Client** - Persistentní TCP spojení s oigservis.cz:5710
4. **MQTT Publisher** - Publikuje na `oig_local/{device_id}/{table}/state`
5. **Online Mode** - Základní provoz (forward Box→Cloud, publikuj na MQTT)
6. **ACK Forwarding** - Přeposílání ACK z Cloudu do Boxu
7. **MQTT Discovery** - Auto-discovery pro Home Assistant

### OUT OF SCOPE ❌ (bude Wave 2+)
- Hybrid/Offline mód (Wave 2)
- Local ACK generation (Wave 2)
- Twin/Sidecar activation (Wave 3)
- Hysteresis a failovery (Wave 2)
- Telemetry a monitoring (Wave 4)
- Complexní error recovery (bude basic)
- Performance optimalizace (bude later)
- Konfigurace přes UI (bude later)

---

## Úkoly

### T1: Project Structure & Setup
**Co udělat:**
- Vytvořit adresářovou strukturu pro v2.0
- Setup Python 3.11+ projektu
- requirements.txt (asyncio, paho-mqtt, pydantic)
- Dockerfile pro HA addon
- config.json pro HA

**Acceptance Criteria:**
- [ ] `python3 -c "import oig_proxy_v2"` funguje
- [ ] `docker build` projde bez chyb
- [ ] Struktura: src/, tests/, config/

**Estimated:** 1h

---

### T2: XML Protocol Parser
**Co udělat:**
- Implementovat `OIGFrameParser` class
- Parsuje `<Frame><TblName>...</TblName>...</Frame>`
- Extrahuje: `_table`, `_device_id`, `_dt`, a data fields
- Ignoruje `ID_SubD > 0` (inactive battery banks)
- Převádí hodnoty na int/float

**Acceptance Criteria:**
- [ ] Parsuje `tbl_actual` frame
- [ ] Parsuje `tbl_batt_prms` frame  
- [ ] Správně detekuje `ID_SubD`
- [ ] Unit testy: 100% coverage

**Estimated:** 2h

---

### T3: TCP Server (Box Listener)
**Co udělat:**
- Implementovat `BoxServer` class
- Listens na 0.0.0.0:5710
- Přijímá TCP připojení od Boxu
- Čte XML frame (delimited by `</Frame>`)
- Callback na zpracování frame

**Acceptance Criteria:**
- [ ] Box se připojí na port 5710
- [ ] Přijímá a zpracovává frame
- [ ] Loguje connection info
- [ ] Unit test: mock client připojení

**Estimated:** 2h

---

### T4: Cloud Client
**Co udělat:**
- Implementovat `CloudClient` class
- Persistentní TCP spojení s oigservis.cz:5710
- `send_frame(frame_bytes)` - pošle do cloudu
- `read_ack()` - čte ACK odpověď
- Timeout: 10s
- Auto-reconnect on disconnect

**Acceptance Criteria:**
- [ ] Připojí se na cloud
- [ ] Pošle frame, přijme ACK
- [ ] Timeout handling funguje
- [ ] Reconnect on failure

**Estimated:** 2h

---

### T5: MQTT Publisher + Normalizace
**Co udělat:**
- Implementovat `MQTTPublisher` class
- Připojení k broker (core-mosquitto:1883)
- **Normalizace**: Všechny tabulky publikují na `oig_local/{device_id}/sensors/state`
- Cache posledních hodnot (slučuje tbl_* + IsNew*)
- **RETAIN=True** - aby senzory nebyly NA po reconnectu
- Discovery: `homeassistant/sensor/{unique_id}/config`
- Force update: ANO (vždy publikovat)

**Data flow:**
```
tbl_actual (z boxu) ──┐
                      ├──→ Cache ──→ sensors/state ──→ HA
IsNewSet (z cloudu) ──┘
```

**Acceptance Criteria:**
- [ ] Připojí se k MQTT
- [ ] tbl_actual publikuje na sensors/state
- [ ] IsNewSet publikuje na sensors/state
- [ ] Oba zdroje aktualizují stejný senzor v HA
- [ ] Po reconnectu MQTT senzory mají hodnoty (retain)
- [ ] Discovery message pro HA
- [ ] Unit test: mock broker

**Estimated:** 3h (včetně normalizace)

---

### T6: Core Engine (Online Mode)
**Co udělat:**
- Implementovat `ProxyEngine` class
- Koordinuje Box → Cloud → MQTT flow:
  1. Přijme frame od Boxu
  2. Parsuje XML
  3. Pošle do Cloudu
  4. Přijme ACK od Cloudu
  5. Publikuje na MQTT
  6. Pošle ACK Boxu
- Error handling (log error, continue)

**Acceptance Criteria:**
- [ ] End-to-end flow funguje
- [ ] Box dostane ACK
- [ ] MQTT má data
- [ ] Cloud má data
- [ ] Unit test: mock box + cloud + mqtt

**Estimated:** 3h

---

### T7: Configuration & Logging
**Co udělat:**
- `config.py` - všechny nastavení
- Environment variables support
- Structured logging (JSON)
- Log levels: DEBUG, INFO, ERROR

**Acceptance Criteria:**
- [ ] Konfigurace přes env vars
- [ ] Logy jsou čitelné
- [ ] Debug logy pro troubleshooting

**Estimated:** 1h

---

### T8: Integration Test
**Co udělat:**
- Test s reálným Boxem nebo mock
- Test MQTT publikace v HA
- Ověřit všechny tbl_* tabulky
- Kontrola timestampů v HA

**Acceptance Criteria:**
- [ ] Box se připojí
- [ ] Všechny tabulky fungují
- [ ] MQTT discovery v HA
- [ ] Senzory se aktualizují

**Estimated:** 2h

---

## Execution Order

```
T1 (Setup)
  ↓
T2 (Parser) → T3 (TCP Server)
  ↓              ↓
T4 (Cloud) ←───┘
  ↓
T5 (MQTT)
  ↓
T6 (Engine)
  ↓
T7 (Config) → T8 (Integration)
```

**Critical Path:** T1 → T2 → T3 → T4 → T5 → T6 → T8

---

## Success Criteria Wave 1

### Funkční
- [ ] Box se připojí na port 5710
- [ ] Data tečou do cloudu (oigservis.cz)
- [ ] Data se publikují na MQTT
- [ ] Všechny tbl_* tabulky fungují:
  - [ ] tbl_actual
  - [ ] tbl_ac_out
  - [ ] tbl_ac_in
  - [ ] tbl_batt
  - [ ] tbl_batt_prms
  - [ ] tbl_boiler
  - [ ] tbl_box
  - [ ] tbl_dc_in
  - [ ] tbl_invertor
- [ ] Všechny IsNew* tabulky fungují (obsahují senzorová data):
- [ ] IsNewSet
- [ ] IsNewWeather
- [ ] IsNewFW
- [ ] **Normalizace**: Všechny tabulky publikují na `sensors/state`
- [ ] **Jeden senzor** je aktualizován z tbl_* i IsNew*
- [ ] **RETAIN=True**: Sensory nejsou NA po reconnectu MQTT
- [ ] Discovery v HA funguje pro všechny sensory
- [ ] Senzory mají timestampy každých ~20-30s (z IsNew*)

### Technické
- [ ] Žádné memory leaky
- [ ] Graceful shutdown
- [ ] Clear error messages
- [ ] Unit test coverage > 80%

---

## Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Protocol complexity | Medium | High | Dokumentace + testy |
| MQTT issues | Low | Medium | Paho-mqtt knihovna |
| Cloud instability | Low | High | Retry logic |
| Time overrun | Medium | Medium | Striktní scope |

---

## Resources Needed

- **Developer**: 1x Python senior
- **Tester**: 1x (může být developer)
- **Environment**: HA instance, OIG Box
- **Time**: 13-15h (2 dny práce)

---

## Definition of Done

1. ✅ Všechny úkoly T1-T8 dokončeny
2. ✅ Success criteria splněna
3. ✅ Code review OK
4. ✅ Testy procházejí
5. ✅ Dokumentace aktuální
6. ✅ Deploy na test HA
7. ✅ User acceptance test OK

---

## Next After Wave 1

**Wave 2: Hybrid/Offline Modes**
- Local ACK generation
- Mode switching (ONLINE → HYBRID → OFFLINE)
- Cloud health monitoring
- Failover logic

**Ready to start Wave 1?**
