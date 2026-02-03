# Changelog

<!-- markdownlint-disable MD024 -->

## [1.4.3] - 2026-02-03

### Changed

- **Upgrade Legacy Mode**: Režim `online` se nyní v kódu automaticky chová jako `hybrid`
  - Všichni uživatelé (kromě `offline`) získají "smart fallback" funkci
  - Není nutná změna konfigurace
- **Default režim**: `proxy_mode` je nyní `hybrid` (místo `online`) pro nové instalace
- **Type Safety**: Fixed all mypy type errors (7 → 0)
  - `utils.py`: Fixed deprecated `datetime.UTC` → `datetime.timezone.utc`
  - `telemetry_client.py`: Added type annotations and assertions for Optional types
  - `mqtt_publisher.py`: Added assertions before Optional client usage
  - `proxy.py`: Fixed type annotation union confusion

- **Code Quality**: Achieved pylint 10.00/10
  - Fixed line length violations
  - Removed trailing whitespace
  - Multi-line formatting for long statements

- **Test Coverage**: Improved from 56.9% to 96.5%
  - New `test_local_oig_crc.py`: 30 comprehensive tests (100% coverage)
  - New `test_telemetry_client.py`: 150+ tests (92.8% coverage)
  - 279 tests total, all passing

## [1.4.2] - 2026-02-02

### Fixed

- Docker build: přidán chybějící `telemetry_client.py` do image

## [1.4.1] - 2026-02-01

### Changed

- **HYBRID režim**: Okamžité přepnutí do offline při selhání cloudu
  - Při prvním selhání (timeout/connect error) přepne ihned do offline a pošle lokální ACK
  - Předchází restartu modemu na BOXu (BOX restartoval modem když nedostal ACK)
  - Po `hybrid_retry_interval` (default 300s) zkusí znovu cloud
  - `hybrid_fail_threshold` změněn z 3 na 1 (bez čekání na více pokusů)

- **ONLINE režim**: Beze změny - plně transparentní
  - Při selhání cloudu se neposílá lokální ACK
  - BOX řeší timeout sám (jako přímé připojení ke cloudu)

- **run script**: Exportuje `PROXY_MODE` a `HYBRID_*` konfigurace do env proměnných

### Added

- Telemetrie: interní diagnostická data (offline buffer, SET příkazy)

## [1.4.0] - 2026-02-01

### Added

- **Network Diagnostic Tool** (`scripts/network_diagnostic.py`): Skript pro diagnostiku síťové konfigurace a připojení ke cloudu
- **Mock Cloud Capture** (`scripts/mock_cloud_capture.py`): Zachytávání komunikace pro analýzu protokolu
- **DIAGNOSTIC_TOOLS.md**: Dokumentace diagnostických nástrojů

### Changed

- Diagnostický cloud server přesunut do samostatného repozitáře `oig-diagnostic-cloud`
- Vylepšená dokumentace 3-režimového systému (ONLINE/HYBRID/OFFLINE)

## [1.3.33] - 2026-01-30

### Changed

- **Nový 3-režimový systém**: `proxy_mode` konfigurace s hodnotami:
  - `online` (default): Transparentní přeposílání BOX↔Cloud, žádná lokální logika
  - `hybrid`: Chytrý fallback - při selhání cloudu přepne do offline, po intervalu zkusí znovu
  - `offline`: Vždy lokální ACK, nikdy se nepřipojuje ke cloudu
- **HYBRID režim**: Detekce offline na základě timeoutů a chyb připojení
- **HYBRID fallback logika**: Lokální ACK se posílá až po dosažení `hybrid_fail_threshold` (default 3) chyb - BOX má šanci na retry
- Nové konfigurace:
  - `hybrid_retry_interval` (default 300s) - interval pro retry cloudu po přechodu do offline
  - `hybrid_fail_threshold` (default 3) - počet chyb před fallbackem do offline

### Fixed

- **ONLINE režim je nyní plně transparentní**: END timeout již neposílá lokální ACK - BOX dostane timeout stejně jako by komunikoval přímo s cloudem
- Lokální END ACK se posílá pouze v HYBRID režimu po dosažení threshold chyb

### Removed

- **CloudHealthChecker** odstraněn - nahrazen HYBRID logikou detekce na základě skutečných chyb
- Konfigurace `cloud_health_check_enabled`, `cloud_health_check_interval`, `cloud_health_check_fail_threshold` odstraněny

## [1.3.32] - 2026-01-29

### Removed

- **CloudQueue** kompletně odstraněn - žádné ukládání/queueování framů směr cloud.
- Konfigurace `cloud_queue_enabled`, `clear_cloud_queue_on_start`, `CLOUD_QUEUE_DB_PATH`, `CLOUD_QUEUE_MAX_SIZE` odstraněny.

### Changed

- Offline režim pouze posílá lokální ACK - žádné další zpracování.
- Proxy nyní transparentně forwarduje veškerou komunikaci bez úprav.
- MQTTQueue pro offline buffering MQTT zpráv ponechán.

## [1.3.31] - 2026-01-29

### Removed

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
