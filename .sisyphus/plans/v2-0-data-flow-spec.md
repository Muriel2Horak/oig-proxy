# OIG Proxy v2.0 - Data Flow Specification

## Zjištěný problém v1.x

Současný kód má problém s publikováním tbl_* dat:
- tbl_* data přicházejí od boxu (např. `tbl_actual`)
- Někdy se publikují na MQTT, někdy ne
- IsNew* data (přicházející z cloudu) se publikují častěji
- Příčina: Nejasná logika v `publish_data` / `_execute_publish`

## Požadavek pro v2.0: Jednoduchá a spolehlivá publikace

### Princip: ŽÁDNÁ DEDUPLIKACE
- Publikovat **vždy**, když přijdou data
- Bez ohledu na předchozí hodnoty
- Bez ohledu na to, zda se hodnota změnila

### Zdroje dat

**Zdroj 1: tbl_* (z boxu)**
```
Box → Proxy → Cloud → ACK → Proxy → MQTT
                    ↓
              (tbl_* data v odpovědi)
```
- tbl_actual, tbl_ac_out, tbl_batt, atd.
- Přicházejí přímo od boxu jako XML frame
- Obsahují aktuální hodnoty senzorů

**Zdroj 2: IsNew* (z cloudu)**
```
Box → Proxy → Cloud (IsNewSet POLL)
                  ↓
            Cloud odpoví (ACK + data)
                  ↓
              Proxy → MQTT
```
- IsNewSet, IsNewWeather, IsNewFW
- Přicházejí jako ACK odpověď od cloudu
- Obsahují **stejná data** jako tbl_actual

### Mapování na MQTT

Oba zdroje se mapují na **stejné MQTT sensory**:

| Zdroj | Field | MQTT Topic | HA Sensor |
|-------|-------|------------|-----------|
| tbl_actual | ACO_P | oig_local/2206237016/tbl_actual/state | sensor.fve_power |
| IsNewSet | ACO_P | oig_local/2206237016/IsNewSet/state | sensor.fve_power |

**Důležité**: V HA se slučují na jeden senzor (pomocí stejného unique_id).

### Frekvence

- **IsNew***: Každých ~20-30 sekund (polling)
- **tbl_***: Každých ~30-60 sekund (push)
- Výsledek: Senzory se aktualizují každých ~20-30 sekund

## Implementace v2.0

### Jednoduchý flow

```python
class ProxyEngine:
    async def handle_box_frame(self, frame):
        # 1. Parsuj XML
        parsed = parser.parse(frame)
        table = parsed.get("_table")  # tbl_actual, IsNewSet, atd.
        
        # 2. Forward do cloudu (pokud je online)
        if self.mode == ONLINE:
            ack = await cloud.send(frame)
            # Z ACK můžeme také extrahovat data (IsNew* obsahují data v ACK)
            if ack:
                ack_parsed = parser.parse(ack)
                await mqtt.publish(ack_parsed)  # Publikuj z ACK
        
        # 3. Vždy publikuj na MQTT (bez ohledu na režim)
        await mqtt.publish(parsed)
        
        # 4. Pošli ACK boxu
        if self.mode == ONLINE:
            box.send(ack)  # Forward cloud ACK
        else:
            box.send(generate_local_ack(parsed))  # Local ACK
```

### Klíčové rozhodnutí

**Každý frame se publikuje 1x nebo 2x:**
1. **tbl_* frame** → publikujeme přímo (obsahuje data)
2. **IsNew* POLL** → forward do cloudu, cloud odpoví ACK s daty → publikujeme z ACK

### Bez deduplikace + Retain flag

```python
class MQTTPublisher:
    async def publish(self, table: str, data: dict):
        """VŽDY publikuj, bez deduplikace."""
        
        # 1. Zjisti device_id
        device_id = data.get("_device_id", self.default_device_id)
        
        # 2. Sestav JSON payload se všemi fields
        sensor_data = {k: v for k, v in data.items() if not k.startswith("_")}
        
        # 3. Publikuj na MQTT (JEDEN topic pro všechna data z tabulky)
        topic = f"oig_local/{device_id}/{table}/state"
        payload = json.dumps(sensor_data)
        
        # 4. VŽDY publikuj s RETAIN=True
        # Důvod: Aby HA mělo poslední hodnotu i když se MQTT reconnectne
        self.client.publish(topic, payload, retain=True)
            
        # 5. Log pro debug
        logger.info(f"PUBLISHED: {table} with {len(sensor_data)} fields")
```

**Proč RETAIN=True?**
- Když se HA restartuje nebo MQTT reconnectne, dostane poslední známou hodnotu
- Sensory nebudou ukazovat "unavailable" nebo "unknown"
- Každý senzor si udrží svou hodnotu

**MQTT State topic struktura:**
```
oig_local/2206237016/tbl_actual/state
  └── {"ACO_P": 1234, "FV_P1": 567, "Temp": 23.5, ...}
  
oig_local/2206237016/IsNewSet/state
  └── {"ACO_P": 1234, "FV_P1": 567, "Temp": 23.5, ...}
```
```

### Řešení mapování: Jeden senzor, více zdrojů

**Problém:**
- `tbl_actual:ACO_P` přijde každých ~60s
- `IsNewSet:ACO_P` přijde každých ~20s
- Obě obsahují stejnou hodnotu pro stejný senzor
- Jak je sloučit do jednoho senzoru v HA?

**Řešení 1: Normalizace (Doporučeno)**
Vytvoříme normalizovaný topic, kam publikujeme data bez ohledu na zdroj:

```python
class ProxyEngine:
    def __init__(self):
        # Cache posledních hodnot (pro všechny senzory)
        self.sensor_cache = {}
        # Poslední tabulka, která přišla
        self.last_table_source = {}
    
    async def handle_frame(self, parsed: dict, source_table: str):
        device_id = parsed.get("_device_id")
        
        # 1. Aktualizuj cache
        for key, value in parsed.items():
            if key.startswith("_"):
                continue
            # Ulož hodnotu + zdroj
            self.sensor_cache[key] = {
                "value": value,
                "source": source_table,
                "timestamp": time.time()
            }
        
        # 2. Publikuj na NORMALIZOVANÝ topic
        # Všechny tabulky publikují na stejný topic!
        normalized_data = {k: v["value"] for k, v in self.sensor_cache.items()}
        await mqtt.publish("sensors/state", normalized_data)
        
        # 3. Log
        logger.info(f"Updated {len(parsed)} fields from {source_table}")
```

**MQTT Topics:**
```
# Zdrojové topicy (pro debug/logy)
oig_local/2206237016/tbl_actual/state
oig_local/2206237016/IsNewSet/state

# Normalizovaný topic (pro HA senzory)  
oig_local/2206237016/sensors/state
  └── {"ACO_P": 1234, "FV_P1": 567, "Temp": 23.5, ...}
```

**Discovery:**
```json
{
  "name": "FVE Výkon",
  "state_topic": "oig_local/2206237016/sensors/state",
  "value_template": "{{ value_json.ACO_P }}",
  "unique_id": "oig_2206237016_aco_p",
  "force_update": true
}
```

**Výhody:**
- ✅ Jeden senzor = jeden topic
- ✅ Aktualizace z jakéhokoliv zdroje (tbl_* nebo IsNew*)
- ✅ HA vidí vždy poslední známou hodnotu
- ✅ Cache řeší "slučování" dat automaticky

**Řešení 2: Pouze IsNew* (Alternativa)**
- Publikovat senzory jen z IsNewSet/IsNewWeather/IsNewFW
- tbl_* posílat jen do cloudu, ignorovat pro MQTT
- IsNew* chodí častěji (~20s) a obsahují stejná data
- Jednodušší, ale ztrácíme data pokud cloud nefunguje

**Rozhodnutí pro v2.0:**
Použijeme **Řešení 1** (normalizace) - robustnější, funguje i bez cloudu.

---

### Discovery (jednorázové)

```python
class MQTTPublisher:
    async def ensure_discovery(self, table: str, field: str):
        """Pošli discovery jen jednou (při prvním výskytu)."""
        
        discovery_key = f"{table}:{field}"
        if discovery_key in self.discovery_sent:
            return  # Už jsme poslali
        
        # Získej config ze sensor_map
        config = sensor_map.get(discovery_key)
        if not config:
            return
        
        # Pošli discovery
        discovery_topic = f"homeassistant/sensor/{unique_id}/config"
        discovery_payload = {
            "name": config.name,
            "state_topic": f"oig_local/{device_id}/{table}/state",
            "value_template": f"{{{{ value_json.{field} }}}}",
            "unique_id": unique_id,
            "force_update": True,  # Důležité!
        }
        
        self.client.publish(discovery_topic, json.dumps(discovery_payload), retain=True)
        self.discovery_sent.add(discovery_key)
```

## Vypnutí staré logiky (v1.x)

Co se **NEPOUŽIJE** z v1.x:
- ❌ `last_payload_by_topic` - deduplikace
- ❌ `last_publish_time_by_topic` - časová deduplikace
- ❌ `_check_payload_deduplication` - kontrola duplicit
- ❌ `publish_data` s podmínkami - komplexní logika
- ❌ `_maybe_handle_twin_event` - bude až Wave 3
- ❌ `hybrid_mode` decision tree - zjednodušíme

Co se **POUŽIJE** z v1.x (po refaktoringu):
- ✅ XML parser (zjednodušený)
- ✅ TCP server (zjednodušený)
- ✅ Cloud client (zjednodušený)
- ✅ Sensor map (stejný formát)
- ✅ MQTT topics (stejný formát)

## Acceptance Criteria v2.0 Wave 1

### Funkční
- [ ] Box se připojí na port 5710
- [ ] **Každý** frame se publikuje na MQTT (bez výjimky)
- [ ] tbl_* data jsou vidět v HA
- [ ] IsNew* data jsou vidět v HA  
- [ ] Senzory se aktualizují každých ~20-30s
- [ ] Cloud dostává data (v Online módu)
- [ ] Box dostává ACK (z cloudu nebo lokální)

### Technické
- [ ] Žádná deduplikace v kódu
- [ ] Jednoduchý, lineární flow
- [ ] Clear logging (každý krok)
- [ ] Unit testy: 90%+ coverage
- [ ] Žádné memory leaky

### Logging (pro debug)
```
[RECV] table=tbl_actual device=2206237016 fields=27
[FW] Forward to cloud: tbl_actual (572 bytes)
[ACK] Received from cloud: tbl_actual (75 bytes)
[PUB] MQTT: oig_local/2206237016/tbl_actual/state (19 fields)
[ACK] Sent to box: tbl_actual
```

## Rozdíl v1.x vs v2.0

| Aspekt | v1.x | v2.0 |
|--------|------|------|
| Deduplikace | Ano (problém) | Ne (vždy publikuj) |
| Logika | Komplexní, málo logů | Jednoduchá, hodně logů |
| tbl_* publikace | Nespolehlivá | Spolehlivá |
| IsNew* publikace | Funguje | Funguje |
| Počet kódových řádků | ~3000+ | ~1000 (cíl) |
| Debugování | Těžké | Snadné |

## Next Steps

1. ✅ Specifikace hotová
2. 🔄 Vytvořit work plan pro Wave 1
3. ⏳ Implementace T1-T8
4. ⏳ Testing na reálném boxu
5. ⏳ Deployment

**Ready to create Wave 1 work plan?**
