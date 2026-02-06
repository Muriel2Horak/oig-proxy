# Changelog

<!-- markdownlint-disable MD024 -->

## [1.5.1] - 2026-02-06

### Fixed

- HYBRID: only mark cloud success after a valid ACK; prevent fail counter reset on
  connect-only failures (e.g., immediate disconnect).

## [1.5.0] - 2026-02-06

### Added

- Telemetry: hybrid online/offline session tracking in window metrics (state, start/end,
  duration, reason).

### Changed

- HYBRID: attempt cloud once per retry interval even while offline; fallback
  to local ACK only after failed probe.
- HYBRID: retry interval default shortened to 60s.

## [1.4.9] - 2026-02-06

### Changed

- HYBRID: attempt cloud once per retry interval even while offline; fallback
  to local ACK only after failed probe.
- HYBRID: retry interval default shortened to 60s.

### Older

- Older entries have been trimmed for brevity.

- **REPLAY režim** kompletně odstraněn - proxy již neodesílá cached framy do cloudu.

### Changed

- **Stealth mode default**: `CLOUD_HEALTH_CHECK_ENABLED` nyní defaultně `false` - žádné TCP heartbeaty na cloud.
- **Stealth mode default**: `LOCAL_GETACTUAL_ENABLED` nyní defaultně `false` - žádné extra dotazy na BOX.
- Proxy je v základu plně transparentní a nedetektovatelná cloudem.

## [1.3.30] - 2026-01-26

### Added

- Add-on volba `cloud_queue_enabled` (defaultně vypnuto) pro úplné vypnutí ukládání/replay do cloudu.
- CI: nový workflow pro unit testy + coverage, Bandit security scan a SonarCloud.

### Changed

- Pokud je `cloud_queue_enabled` vypnuto, fronta se při startu automaticky vyčistí.
- Pylint workflow běží i na `pull_request`.
- Sonar config: `sonar.python.version` sjednoceno na 3.11.

## [1.3.29] - 2026-01-17

### Added

- Add-on volba `clear_cloud_queue_on_start` pro vymazání cloud fronty při startu.
- REPLAY: debug log payloadu pro neúspěšné odeslání (truncated).

### Changed

- REPLAY: při resetu cloud spojení přepíná proxy do OFFLINE a čeká na HC.
- REPLAY: běžné socket chyby nelogují traceback (jen warning).

## [1.3.28] - 2026-01-03

### Changed

- Pylint konfig a úpravy kódu pro čistý lint.
- Úpravy test double/MQTT helperů kvůli cache a state topicům.
- Úpravy DNS helperu a capture queue hooků pro testy a coverage běh.

## [1.3.27] - 2026-01-03

### Added

- Log sanitizace citlivých hodnot (tokeny/hesla) v logování.

### Changed

- Rozšířené unit testy a drobná zlepšení spolehlivosti testů.
- Sonar/SonarCloud: doplněné parametry skriptů a dokumentace pro běh analýz.

## [1.3.18] - 2025-12-22

- Opravy chyb a drobná vylepšení.

## [1.3.17] - 2025-12-21

### Fixed

- Opravy chyb a drobná vylepšení stability

## [1.3.16] - 2025-12-21

## [1.3.15] - 2025-12-20

### Added

- Optimistické aktualizace MQTT stavů po potvrzení změny z BOXu (control)

## [1.3.14] - 2025-12-20

### Added

- Offline režim: uplatnění hned po startu při `force_offline`

## [1.3.13] - 2025-12-20

### Added

- Offline režim: konfigurační volba `force_offline` pro zapnutí/vypnutí

## [1.3.12] - 2025-12-19

### Fixed

- Add-on build: přidán chybějící modul pro skládání frame zpráv (oprava chyby při buildování image v HA Supervisoru)

## [1.3.11] - 2025-12-19

### Added

- Control přes MQTT: topic `oig_local/oig_proxy/control/set` + `.../result` (queue, dedupe, noop, whitelist, timeouts)

### Fixed

- Control enable bez UI: fallback flag `/data/control_mqtt_enabled`
- Stabilita: `Connection reset by peer` se bere jako normální odpojení BOXu (ne ERROR)
- Control: oprava regex parsování `tbl_events` Setting řádků (unbalanced parenthesis)

### Changed

- MQTT discovery: správné groupování zařízení pro `tbl_boiler_prms`, `tbl_recuper_prms`, `tbl_aircon_prms`, `tbl_h_pump_prms`, `tbl_wl_charge_prms`
- Přejmenování zařízení `Nabíjení` → `Wallbox`

## [1.3.10] - 2025-12-17

### Fixed

- MQTT discovery: odstraněn deprecated `object_id` (nahrazen `default_entity_id`), aby HA přestal logovat warningy
- MQTT timestamp senzory: hodnoty bez timezone se publikují jako ISO8601 s timezone (oprava `Invalid datetime`)
- MQTT publish: de-dupe stejných payloadů per-topic, aby se neprodukoval zbytečný `state_changed` spam
- Proxy status loop: MODE/PRMS se publikuje periodicky jen pokud je pending (po změně/restartu/reconnectu)

## [1.3.9] - 2025-12-16

### Fixed

- OFFLINE: `END` rámce typu `Reason=All data sent` se už vůbec nequeueují do CloudQueue (cloud na ně typicky neodpovídá), aby zbytečně nespouštěly REPLAY
- Dev: detekce dostupnosti MQTT klienta bez importu `paho` (mypy/VS Code už nehlásí chybějící stuby)

## [1.3.8] - 2025-12-16

### Added

- Add-on volba `capture_raw_bytes` (env `CAPTURE_RAW_BYTES`) pro ukládání hrubých bajtů (`raw_b64`) do `/data/payloads.db` pro low-level analýzu

### Fixed

- REPLAY: `defer()` respektuje `timestamp` jako `not_before` a task necyklí jeden frame donekonečna
- REPLAY: problematické `END` rámce typu `Reason=All data sent` se při opakovaném timeoutu zahodí (cloud na ně typicky neodpovídá), aby neblokovaly replay frontu

## [1.3.7] - 2025-12-16

### Added

- Add-on option `cloud_ack_timeout` (env `CLOUD_ACK_TIMEOUT`) pro max čekání na ACK z cloudu v ONLINE režimu

### Fixed

- Stabilnější BOX ↔ proxy spojení: rychlý fallback na lokální ACK + queue při zpomalení/timeoutu cloudu (méně reconnectů BOXu)
- REPLAY se spouští i když vznikne fronta v ONLINE (periodický edge-case), aby se CloudQueue nezasekla plná
- REPLAY se neblokuje na jednom problematickém framu (defer)
- CloudQueue FIFO determinismus: výběr i drop nejstaršího podle `timestamp, id`

## [1.3.6] - 2025-12-15

### Added

- Perzistence snapshotu tabulek (`tbl_*` kromě `tbl_actual`) do `/data/prms_state.json` pro obnovu po restartu
- Re-publish uloženého snapshotu po startu a po MQTT reconnectu (senzory nečekají desítky minut až hodiny na `*_prms`)
- Obnova `device_id` při `DEVICE_ID=AUTO` z uloženého stavu (MODE/table snapshot)

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
