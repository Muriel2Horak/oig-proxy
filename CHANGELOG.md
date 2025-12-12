# Changelog

## [1.3.2] - 2025-12-12

### Fixed
- **sensor_map.json**: Změna `entity_category` z `config` na `diagnostic` pro 127 senzorů
  - HA nepřijímá `entity_category: config` pro binary_sensor a sensor entity
- **sensor_map.json**: Oprava jednotek z `MWh` na `kWh` pro roční senzory:
  - `AC_PY` (Síť - Dodávka rok)
  - `EN_YEAR` (Spotřeba - Odběr rok)
  - `ETOCAR_Y` (Wallbox - Energie rok)
  - `ETOCAR_PVB_Y` (Wallbox - Z FVE/bat. rok)
  - `ETOCAR_G_Y` (Wallbox - Ze sítě rok)
- **config.py**: Přidány chybějící `DEVICE_NAMES` mapování:
  - `pv` → "FVE"
  - `grid` → "Síť"
  - `load` → "Spotřeba"
  - Entity se nyní správně přiřazují do odpovídajících zařízení

## [1.3.1] - 2025-12-11

### Added
- Modularizace kódu (proxy.py, mqtt_publisher.py, cloud_manager.py, utils.py)
- Device mapping v sensor_map.json pro rozdělení entit do zařízení
- Capture payloads do SQLite databáze

## [1.3.0] - 2025-12-10

### Added
- Initial modular release
