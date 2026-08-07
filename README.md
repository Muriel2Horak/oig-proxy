# OIG Proxy 2.2 pro Home Assistant

OIG Proxy je TCP proxy mezi OIG BOXem a cloudem `bridge.oigpower.cz`. Validuje rámce a CRC, publikuje senzory přes MQTT discovery a volitelně provádí odolné lokální změny nastavení. Produkční zdroj je v `addon/oig-proxy/`.

## Hlavní vlastnosti

- ONLINE, HYBRID a OFFLINE režim s transparentním cloudovým forwardem;
- MQTT state/discovery pro Home Assistant;
- přesná vazba na jedno naučené `device_id`;
- perzistentní lokální transakce v `/data/twin_queue.db`;
- cloud-first lokální Setting v ONLINE/HYBRID a exactly one odpověď v OFFLINE;
- potvrzení provedení pouze přes přesný `tbl_events`, `Type=Setting` event;
- retry/restart recovery, limity vstupu, audit, capture a fail-closed chování;
- blokující unit/integration/loopback E2E, statement/branch coverage, lint, type a security gates.

Lokální control je po instalaci vypnutý (`control_mqtt_enabled: false`). Disabled restart databázi validuje a zachová, ale nevytvoří control subscription ani neodešle lokální Setting.

## Datový tok

```text
OIG BOX --TCP:5710--> OIG Proxy --TCP:5710--> bridge.oigpower.cz
                         |
                         +--> MQTT broker --> Home Assistant
                         +--> /data/twin_queue.db (opt-in local control)
```

ONLINE a online část HYBRID jsou cloud-first: proxy nejprve dokončí korelovaný cloudový `IsNewSet` dialog a pouze jeho koncový `END` může nahradit jedním durabilně připraveným lokálním Settingem. Pokud není příkaz způsobilý nebo selže lokální evidence, cloudové bajty zůstanou beze změny.

OFFLINE neotevírá cloud. Každý platný kompletní BOX request dostane přesně jednu odpověď; `IsNewSet` dostane jeden lokální Setting, nebo jeden `END`.

## Bezpečný lokální control

Po zapnutí se používá pouze přesné téma `oig/{device_id}/control/set`. Retained zprávy, jiné zařízení, payload nad 1 MiB, neplatné UTF-8/JSON, nepovolený target, neomezená numerická práce a nebezpečný XML text jsou odmítnuty před vytvořením příkazu. Žádný wildcard-device subscription ani no replay path neexistuje.

Transportní `ACK/Setting` znamená pouze doručení a čekání na provedení. Potvrzený MQTT/HA state publikuje až přesně korelovaný BOX event. Sekvenční ACK/NACK omezení a jeho zbytkové riziko jsou popsány v [dokumentaci transakcí](docs/v2/twin.md).

## Instalace add-onu

1. V Home Assistantu přidejte repository `https://github.com/Muriel2Horak/oig-proxy`.
2. Nainstalujte add-on OIG Proxy.
3. Nastavte cloud, MQTT účet a režim; přesných 30 parametrů je v [konfigurační referenci](docs/v2/configuration.md).
4. Zajistěte, aby BOX resolveoval `bridge.oigpower.cz` na IP Home Assistantu (router host override nebo vestavěný dnsmasq).
5. Spusťte add-on a ověřte naučené `device_id`, MQTT availability a sensor state.

Základní konfigurace:

```yaml
target_server: bridge.oigpower.cz
target_port: 5710
proxy_port: 5710
proxy_mode: hybrid
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: oig_proxy
mqtt_password: "<secret>"
control_mqtt_enabled: false
```

Změny konfigurace jsou vzorkované při startu a vyžadují restart add-onu.

## Upgrade a rollback

Při upgradu na 2.2.0 ponechte `/data/twin_queue.db` v `/data`. Verze 2.2.0 kontroluje schema v1 a nikdy downgraduje ani nerecreateuje future/corrupt DB. 2.1.1 rollback databázi nerozumí, ale ignoruje ji a preserves `/data/twin_queue.db`; po návratu na 2.2.0 recovery pokračuje.

## Ověření změn

```bash
./ci/ci.sh
```

Lokální/CI release gate spouští celý `tests/v2`, loopback E2E, samostatné statement a branch coverage nad 80 %, mypy, Flake8, Pylint, Bandit, Semgrep, Gitleaks a Safety. Testy nepoužívají živý BOX, produkční MQTT ani aktivní cloudové příkazy.

## Dokumentace

- [Architektura](docs/v2/architecture.md)
- [Režimy proxy](docs/v2/proxy_modes.md)
- [Lokální transakce](docs/v2/twin.md)
- [Konfigurace](docs/v2/configuration.md)
- [CI/CD](docs/CI_CD_OVERVIEW.md)
- [Security testing](docs/SECURITY_TESTING.md)
- [Security policy](SECURITY.md)
