
## 2026-02-17: Extrakce Setting Windows

### Skript pro extrakci
- **Cesta**: `/Users/martinhorak/Projects/oig-diagnostic-cloud/scripts/extract_setting_windows.py`
- **Výstup**: `/tmp/setting-contract/success-window.json` a `failed-window.json`

### Kritérium pro success/failed
- **Success**: Cloud pošle Setting frame (Reason=Setting) → Box odpoví ACK s Reason=Setting
- **Failed**: Cloud pošle Setting frame → Box NEodpoví s ACK(Reason=Setting)

### Klíčové zjištění - duplicitní XML tagy
OIG protokol obsahuje **duplicitní Reason tagy** v rámci jednoho frame:
```xml
<Frame><Result>ACK</Result><Reason>Setting</Reason>...<Reason>Table</Reason>...</Frame>
```
- **První Reason** = typ ACK (Setting/Table/...)
- **Druhý Reason** = data payload

Řešení: Parser musí zachytit **PRVNÍ** výskyt tagu Reason, nikoliv poslední.

### Statistiky z 36MB DB capture
- **Success windows**: 6 (variační `ver` hodnoty: 00051, 13194, 00508, ...)
- **Failed windows**: 436 (statické `ver` hodnoty: 55734=83%, 25113=15%, 10712=2%)

### Pattern: Variabilita ver hodnoty
- **Success**: `ver` hodnoty se výrazně liší mezi frames (enkapsulace je variabilní)
- **Failed**: `ver` hodnoty jsou statické/repetitivní (enkapsulace se nemění)

Toto je klíčový rozlišovací znak pro detekci úspěšných vs neúspěšných Setting sekvencí.

