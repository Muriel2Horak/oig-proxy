# OIG Proxy pro Home Assistant

TCP proxy pro OIG Box, která dekóduje XML rámce, publikuje data do MQTT (HA autodiscovery), dekóduje warningy a loguje neznámé senzory pro doplnění mapy. Součástí je DNS přepis, aby Box mluvil na lokální proxy místo cloudu.

## Klíčové funkce
- 🔄 **Multi‑mode proxy**: ONLINE (forward) / OFFLINE (lokální ACK + queue) / REPLAY (vyprazdňování fronty)
- 💾 **Persistentní fronty**: SQLite fronta pro cloud (frames) i MQTT (messages)
- 🔌 **Odolnost vůči výpadkům**: automatická detekce výpadku cloudu, lokální ACK generování
- 📡 **MQTT autodiscovery**: entity se zakládají přes `homeassistant/.../config` (retain)
- 🧭 **Diagnostika komunikace**: samostatné zařízení „OIG Proxy“ se stavovými senzory (stav, fronty, poslední data, IsNewSet telemetrie)
- 🧾 **Eventy**: `tbl_events` se publikuje a mapuje do HA (Type/Confirm/Content)

## Struktura
- `proxy/` – hlavní Python proxy (`main.py`), načítá mapping ze sdíleného `sensor_map.json`, dekóduje warning bity (`ERR_*`).
- `addon/oig-proxy/` – Home Assistant add-on (config.json, Dockerfile, run), používá stejný `sensor_map.json`.
- `dnsmasq.conf`, `Corefile` – ukázka DNS přepisu.
- `logs/` – prázdné (logy necommitujeme).

## Co proxy umí
- **Publikuje tabulky do MQTT**: `oig_local/<device_id>/<tbl_name>/state` (payload JSON).
- **Zakládá entity v HA** přes MQTT discovery (`homeassistant/sensor/.../config`, `homeassistant/binary_sensor/.../config`).
- **Načítá mapu senzorů** z `/data/sensor_map.json`; neznámé klíče loguje do `/data/unknown_sensors.json`.
- **Dekóduje warningy** z bitových polí `ERR_*` (warnings_3f) a přidává `<ERR_X>_warnings` se seznamem hlášek.
- **Udržuje režimy komunikace** a fronty:
  - ONLINE: forward BOX ↔ cloud, ACK z cloudu, učení ACK patternů
  - OFFLINE: lokální ACK, ukládání frame do `cloud_queue.db`
  - REPLAY: vyprazdňování `cloud_queue.db` po obnovení cloudu
- **Publikuje diagnostiku proxy** do samostatného zařízení:
  - Topic: `oig_local/oig_proxy/proxy_status/state` (default)
  - Entity zakládá z `proxy_status:*` v mapě (stav, fronty, poslední data, IsNewSet)
- **Publikuje eventy** do proxy zařízení:
  - Topic: `oig_local/oig_proxy/tbl_events/state` (default)
  - Entity: `tbl_events:Type`, `tbl_events:Confirm`, `tbl_events:Content`
- **Volitelně ukládá capture** všech frames do `/data/payloads.db` (pokud `capture_payloads=true`).

## Tok komunikace
```
OIG Box  --DNS override-->  HA host (addon OIG Proxy, port 5710)  --TCP-->  oigservis.cz (cloud)
   |                             |
   |  XML frame                  |  Parse + map + warnings decode
   |---------------------------->|  Publish state to MQTT: oig_local/<device_id>/<table>/state
                                 |  Send HA discovery: homeassistant/sensor/.../config
                                 |  Availability: oig_local/<device_id>/availability
MQTT Broker (mosquitto addon) <--+
   |
   v
Home Assistant (entities vytvářené z discovery)
```

## Zařízení a entity v HA (MQTT discovery)

Proxy typicky vytvoří dvě „větve“ zařízení:

1) **OIG Proxy (`oig_proxy`)** – diagnostika komunikace (stálé zařízení, bez vazby na box ID)
   - `proxy_status:*` (stav, fronty, poslední data, IsNewSet)
   - `tbl_events:*` (Type/Confirm/Content)

2) **OIG zařízení podle `device_id`** (autodetekce z komunikace)
   - skupiny podle `device_mapping` (např. Střídač/Baterie/Síť/FVE/Spotřeba…) – jeden `device_id`, více zařízení

Poznámka: změny typu entity (sensor ↔ binary_sensor) vyžadují vymazat staré retained discovery config topics, jinak HA drží původní component.

## Požadavky na uživatele
### 1) MQTT broker (Mosquitto)
1. V HA otevři **Nastavení → Doplňky → Obchod s doplňky**.
2. Nainstaluj doplněk **Mosquitto broker** a spusť ho.
3. V **Nastavení → Zařízení a služby → MQTT** (integrace) přidej MQTT integraci.
   - Host: obvykle `core-mosquitto`
   - Port: `1883`
   - Uživatelské jméno/heslo: dle konfigurace Mosquitto (doporučeno vytvořit separátní účet).

### 2) Instalace add-onu OIG Proxy (krok za krokem)
1. V HA otevři **Nastavení → Doplňky → Obchod s doplňky**.
2. Vpravo nahoře klikni na **⋮** (tři tečky) → **Repozitáře**.
3. Přidej repo: `https://github.com/Muriel2Horak/oig-proxy` a potvrď.
4. Najdi doplněk **OIG Proxy** a klikni **Instalovat**.
5. Otevři záložku **Konfigurace** a nastav minimálně:
   - `target_server`: `oigservis.cz`
   - `target_port`: `5710`
   - `proxy_port`: `5710`
   - `mqtt_host`: `core-mosquitto`
   - `mqtt_port`: `1883`
   - `mqtt_username`, `mqtt_password`: účet z Mosquitto
   - `log_level`: `INFO` (na ladění `DEBUG`)
6. (Volitelné) ladicí záznam do SQLite:
   - `capture_payloads: true` – ukládá rámce do `/data/payloads.db`
   - `capture_raw_bytes: true` – ukládá i hrubé bajty (`raw_b64`) pro low-level analýzu
7. Klikni **Uložit**.
8. Na záložce **Info** dej **Spustit**.
9. Ověření:
   - v logu add-onu uvidíš `🚀 OIG Proxy naslouchá na 0.0.0.0:5710`
   - po připojení BOXu uvidíš `BOX připojen`
   - v MQTT by měly vznikat retained discovery topicy `homeassistant/.../config`

### 3) DNS přesměrování (aby BOX volal proxy)
BOX musí místo cloudu (`oigservis.cz`) chodit na IP Home Assistanta, kde běží add-on.
Nejjednodušší je udělat DNS override v lokální síti.

#### Rychlá kontrola, že DNS funguje
- Na PC v síti: `nslookup oigservis.cz <IP_DNS_serveru>`
- Na HA (Terminal & SSH add-on): `nslookup oigservis.cz`
- Očekávání: `oigservis.cz` se překládá na IP HA (ne na veřejnou IP).

#### Varianta A: Router umí DNS override/host record (doporučeno)
1. V routeru najdi sekci typu **LAN / DHCP / DNS** nebo **DNS Rebind / Hostnames / Local DNS**.
2. Přidej statický záznam:
   - hostname: `oigservis.cz`
   - typ: `A`
   - IP: `<IP Home Assistanta v LAN>`
3. Ujisti se, že DHCP rozdává jako DNS server router (nebo DNS, který ten override umí).
4. Restartuj BOX (nebo aspoň jeho síť), aby si načetl nový DNS.
5. Pokud router používá DoH/DoT nebo „DNS proxy“, zkontroluj, že **lokální host override má prioritu** (u některých routerů je potřeba vypnout DoH pro LAN).

#### Varianta B: DHCP rozdává DNS = IP Home Assistanta (nejjednodušší, když běží dnsmasq v add-onu)
Tento add-on spouští **dnsmasq** (poslouchá na `53/udp` a `53/tcp`) a umí lokálně přepsat `oigservis.cz` na IP HA (viz `ha_ip` + `dns_upstream` v konfiguraci add-onu).

1. V HA v add-onu **OIG Proxy** nastav (Konfigurace):
   - `ha_ip`: IP Home Assistanta v LAN (nebo ponech prázdné – add-on se ji pokusí autodetekovat)
   - `dns_upstream`: DNS upstream (default `8.8.8.8`, nebo dej IP tvého routeru/DNS)
2. Ujisti se, že add-on běží a že port 53 je dostupný z LAN (add-on používá `host_network: true`).
3. V routeru (DHCP server) nastav jako **DNS server** IP Home Assistanta.
4. Restartuj BOX (nebo obnov DHCP lease), aby si načetl nový DNS.
5. Ověř z klienta v LAN: `nslookup oigservis.cz <IP_HA>` → musí vracet IP HA.

Poznámky:
- Tohle ovlivní **všechny zařízení v LAN**, které používají DNS z DHCP. Pokud nechceš ovlivnit celou síť, nastav DNS jen pro BOX (pokud router umí per‑device DHCP options), nebo použij Variantu A/C.

#### Varianta B: Pi-hole / AdGuard Home / dnsmasq (když router neumí override)
1. Provozuj lokální DNS server (Pi-hole / AdGuard Home / dnsmasq) v LAN.
2. Nastav v něm DNS přepis:
   - `oigservis.cz` → `<IP Home Assistanta>`
3. Nastav v routeru DHCP tak, aby klientům (včetně BOXu) rozdával jako DNS právě tento DNS server.
4. Zkontroluj, že klienti nemají „fallback“ na veřejné DNS (např. 8.8.8.8).

#### Varianta C: Jen pro test (hosts na jednom PC)
Funguje jen pro testování z PC, ne pro BOX:
- Přidej do hosts: `oigservis.cz <IP_HA>`

#### Typické problémy (rychlá diagnostika)
- **BOX má natvrdo veřejné DNS**: v routeru zablokuj odchozí DNS (TCP/UDP 53) mimo lokální DNS server, nebo přesměruj DNS provoz na svůj DNS (policy routing/NAT).
- **DNS cache**: po změně záznamu restartuj BOX; na PC může pomoct flush DNS cache.
- **Více domén**: pokud se v komunikaci objeví i jiné domény, přidej je do override stejně (logy/proxy capture ti to odhalí).

#### Příklady konfigurace (orientačně)
- **OpenWrt (dnsmasq)**: v LuCI → *Network → DHCP and DNS → Hostnames* přidej `oigservis.cz` → `<IP_HA>`.
  - CLI: přidej do `/etc/hosts` řádek `<IP_HA> oigservis.cz` a restartuj dnsmasq: `service dnsmasq restart`.
- **MikroTik**: *IP → DNS → Static* přidej `oigservis.cz` s adresou `<IP_HA>` a zapni `Allow Remote Requests`.

## Lokální spuštění (mimo HA)
```
cd proxy
MQTT_HOST=... MQTT_PORT=1883 python -u main.py
```
Nebo docker-compose v rootu (doplnit env pro MQTT a cílový server).

## Testy a kvalita
- Spuštění testů: `./scripts/run_tests.sh` (volitelné env: `COVERAGE_FAIL_UNDER=80`, `RUN_INTEGRATION=0`).
- Sonar + Bandit: `./scripts/run_sonar.sh` (vyžaduje `SONAR_TOKEN` a `SONAR_HOST_URL` v `.env`; volitelně `SONAR_CONFIGURE_QG=1` a `SONAR_QUALITY_GATE_NAME="Security A +0"`).
- SonarCloud: nastav `SONAR_HOST_URL=https://sonarcloud.io`, `SONAR_ORGANIZATION=<org>`, `SONAR_PROJECT_KEY=<org>_<project>` a `SONAR_CLOUD_TOKEN` (pro PR analýzu i `SONAR_PR_KEY`, `SONAR_PR_BRANCH`, `SONAR_PR_BASE`).
- Reporty se generují do `reports/` (`coverage.xml`, `junit.xml`, `bandit.json`) a jsou v `.gitignore`.

## Build add-on image (multi-arch)
```
cd addon/oig-proxy
docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/muriel2horak/oig-proxy:1.0.0 --push .
```
`config.json` používá image `ghcr.io/muriel2horak/oig-proxy-{arch}`; po pushi lze tag přepsat na konkrétní verzi.

## Konfigurace env (shrnutí)
- `TARGET_SERVER` (default `oigservis.cz`), `TARGET_PORT` (5710) – cíl, kam proxy přeposílá.
- `PROXY_PORT` (5710) – lokální port pro Box.
- `MQTT_HOST/PORT/USERNAME/PASSWORD` – broker.
- `MQTT_NAMESPACE` (default `oig_local`) – prefix topiců.
- `PROXY_DEVICE_ID` (default `oig_proxy`) – pevné `device_id` pro proxy/status/event senzory.
- `PROXY_STATUS_INTERVAL` (default `60`) – periodické publikování `proxy_status` do MQTT (užitečné po restartu HA).
- `SENSOR_MAP_PATH` (default `/data/sensor_map.json` v add-onu).
- `MAP_RELOAD_SECONDS` (0 = vypnuto) – periodický reload mapy.
- `UNKNOWN_SENSORS_PATH` (default `/data/unknown_sensors.json`).
- `CAPTURE_PAYLOADS` (default `false`) – ukládá všechny frames do `/data/payloads.db`.
- `CAPTURE_RAW_BYTES` (default `false`) – ukládá i hrubé bajty (`raw_b64`) pro low-level analýzu.

## Bateriové banky (SubD architektura)

OIG zařízení CBB podporuje až 3 nezávislé bateriové banky. Proxy publikuje pouze aktivní banku (SubD=0) s reálnými daty. Neaktivní banky (SubD=1,2) jsou **úmyslně filtrováním** zahojena, aby se zabránilo cyklování hodnot v HA.

**Aktuální chování**:
- Tabulka `tbl_batt_prms` je fragmentována do 3 variant (SubD=0,1,2), každá reprezentuje jednu banku.
- Pouze SubD=0 (aktivní banka) je publikována do MQTT.
- SubD=1,2 jsou záměrně ignorovány – mají nulové hodnoty, nejsou potřebné.
- Pokud budete v budoucnu aktivovat druhou nebo třetí banku, požaduje se rozšíření mappingu v `sensor_map.json` a úprava logiky proxy.

**Technické detaily**: Viz `analysis/subd_analysis.md` pro popis fragmentace, analýzu polí a možné budoucí rozšíření na multi-bank systémy.

## Repo
GitHub: https://github.com/Muriel2Horak/oig-proxy
