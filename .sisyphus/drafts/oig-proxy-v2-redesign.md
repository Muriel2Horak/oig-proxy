# Draft: OIG Proxy v2 - Kompletní Redesign

## Cíl
Vytvořit novou, čistou OIG proxy from scratch s jednoduchou async architekturou.

## Identifikované problémy současné proxy
1. **Příliš komplexní** - 41 souborů, 1542 řádků v proxy.py
2. **Špatná separace zodpovědností** - logika namixovaná
3. **Problémy s režimy** - ONLINE/HYBRID/OFFLINE přepínání nefunguje spolehlivě
4. **Chyby v ACK** - lokální ACK nejsou vždy správná

## Požadavky na novou proxy (MVP)

### Phase 1 - Základní proxy (Teď)
- [ ] TCP proxy: Box ↔ Cloud (transparentní forward)
- [ ] XML parsing rámů
- [ ] MQTT publish do Home Assistant
- [ ] Jednoduchý config (YAML/JSON)

### Phase 2 - Hybrid mode (Později)
- [ ] Detekce výpadku cloudu
- [ ] Automatický fallback na offline
- [ ] Lokální ACK generování

### Phase 3 - Rozšíření (Po stabilizaci)
- [ ] SQLite queue pro offline data
- [ ] Session twin pro nastavení
- [ ] Telemetry a diagnostika

## Architektura

### Principy
1. **Jednoduchost** - co nejméně souborů, jasný tok dat
2. **Testovatelnost** - každá komponenta samostatně testovatelná
3. **Async/await** - asyncio pro všechny I/O operace
4. **Explicitní stavy** - žádné magické přepínání

### Struktura
```
new_proxy/
├── main.py              # Vstupní bod
├── config.py            # Konfigurace (pydantic)
├── proxy.py             # Hlavní proxy logika (jedna třída)
├── protocol.py          # OIG protokol (frame parsing, CRC)
├── mqtt_client.py       # MQTT wrapper
└── tests/
    ├── test_protocol.py
    ├── test_proxy.py
    └── conftest.py
```

## Technické detaily

### Frame Processing Pipeline
```
Box --TCP--> Proxy --[Parse XML]--> MQTT
                |
                +--[Forward]--> Cloud --[Response]--> Proxy --[Parse]--> Box
```

### Config Schema
```yaml
proxy:
  listen_host: "0.0.0.0"
  listen_port: 5710
  target_host: "oigservis.cz"
  target_port: 5710

mqtt:
  host: "core-mosquitto"
  port: 1883
  username: ""
  password: ""
  topic_prefix: "oig_local"

logging:
  level: INFO
```

## Open Questions
1. Máme zachovat CRC výpočet z lokálního souboru?
2. Jaké tabulky jsou kritické pro MVP (tbl_actual, tbl_box_prms)?
3. Máme podporovat více boxů najednou nebo jen jeden?
