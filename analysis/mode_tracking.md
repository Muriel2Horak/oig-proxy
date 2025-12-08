# MODE Tracking Implementation

## Problém

BatteryBox režim (HOME I, HOME II, HOME III, HOME UPS) se v OIG protokolu:
- **Nastavuje** z cloudu jako `tbl_box_prms:MODE` (Setting frame, hodnoty 0-3)
- **Nepřichází v běžné telemetrii** (box nikdy neposílá tbl_box_prms jako Reason=Table)
- **Může se změnit lokálně** na displayi boxu nebo remotely z aplikace

## Řešení

### 1. Sledování tbl_events

Box zaloguje každou změnu MODE do `tbl_events`:
```xml
<Frame>
  <TblName>tbl_events</TblName>
  <Type>Setting</Type>
  <Content>Remotely : tbl_box_prms / MODE: [0]->[3]</Content>
  ...
</Frame>
```

Pattern: `MODE: [old_value]->[new_value]`

### 2. Virtuální senzor

Proxy emuluje `tbl_box_prms:MODE` senzor:
- Parsuje změny z tbl_events
- Publikuje jako běžný MQTT sensor s device_class="enum"
- Options: ["HOME I", "HOME II", "HOME III", "HOME UPS"]

### 3. Persistence

Stav MODE se ukládá do `/data/mode_state.json`:
```json
{
  "mode": 0,
  "timestamp": "2025-12-08T22:00:00Z"
}
```

Po restartu proxy:
- Načte uložený stav
- Publikuje do HA
- Čeká na další změny z tbl_events

## Implementace

### Parser events (main.py)

```python
def _parse_mode_from_event(content: str) -> int | None:
    """Parsuje MODE hodnotu z tbl_events Content fieldu."""
    match = re.search(r'MODE:\s*\[(\d+)\]->\[(\d+)\]', content)
    if match:
        old_value = int(match.group(1))
        new_value = int(match.group(2))
        logger.info(f"MODE: Event detekován: {old_value} → {new_value}")
        return new_value
    return None
```

### Zpracování v pipeline

```python
def _process_mode_event(self, parsed: dict[str, Any]) -> None:
    """Zpracuje tbl_events frame a detekuje změnu MODE."""
    global _current_mode
    
    content = parsed.get("Content", "")
    if "tbl_box_prms" in content and "MODE:" in content:
        new_mode = _parse_mode_from_event(content)
        if new_mode is not None and new_mode != _current_mode:
            _current_mode = new_mode
            _save_mode_state(new_mode)
            
            # Publikovat virtuální senzor
            mode_data = {
                "_table": "tbl_box_prms",
                "_device_id": parsed.get("_device_id"),
                "MODE": new_mode
            }
            self.mqtt_publisher.publish_data(mode_data)
```

### Persistence

- **Load na startu**: `_load_mode_state()` z `/data/mode_state.json`
- **Save při změně**: `_save_mode_state(mode)` při každém eventu
- **Publish po reconnectu**: Při MQTT reconnectu se publikuje uložená hodnota

## Spolehlivost

✅ **Detekce změn**:
- Lokální (na displayi boxu): Box loguje do tbl_events
- Remote (z aplikace/cloudu): Cloud posílá Setting, box loguje do tbl_events
- **Pattern "Remotely"** neznamená pouze cloud - box používá stejný event i pro lokální změny

✅ **Persistence**:
- Stav přežije restart proxy
- Stav přežije MQTT disconnect/reconnect
- Při startu se publikuje poslední známá hodnota

✅ **Synchronizace**:
- MODE se aktualizuje okamžitě při změně (tbl_events přichází ihned)
- Není závislost na periodickom pollingu
- Event-driven architektura

## Testing

### Test změny MODE

1. Změň MODE v OIG aplikaci (např. HOME I → HOME II)
2. Sleduj logy proxy:
```
MODE: Event detekován: 0 → 1
MODE: Stav uložen: 1
MODE: Publikuji virtuální senzor: MODE=1
```

3. V Home Assistant:
```
sensor.oig_local_2206237016_tbl_box_prms_mode: "HOME II"
```

### Test persistence

1. Zaznamenaj aktuální MODE
2. Restartuj proxy addon
3. Sleduj logy:
```
MODE: Načten uložený stav: 1
MODE: Publikuji obnovený stav při startu: MODE=1
```

4. Sensor v HA má správnou hodnotu ihned po startu

## Hodnoty MODE

| Hodnota | Enum     | Popis                          |
|---------|----------|--------------------------------|
| 0       | HOME I   | Standard (FMT/No Limit vypnuto)|
| 1       | HOME II  | -                              |
| 2       | HOME III | -                              |
| 3       | HOME UPS | No Limit (FMT zapnuto)         |

## Známé omezení

- **Prvotní detekce**: Pokud proxy startuje a MODE se od startu proxy nikdy nezměnil, nebude hodnota známá dokud nepřijde první tbl_events
- **Řešení**: Při prvním startu nech MODE jednou změnit (libovolně a zpět), pak už bude persistence fungovat

## Log messages

```
MODE: Načten uložený stav: 0
MODE: Event detekován: 0 → 3
MODE: Stav uložen: 3
MODE: Publikuji virtuální senzor: MODE=3
MODE: Publikuji obnovený stav při startu: MODE=3
MODE: Obnovuji stav z úložiště: 3
```
