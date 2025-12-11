# OIG Proxy for Home Assistant

**Verze 1.3.0** - Modulární architektura s podporou ONLINE/OFFLINE/REPLAY režimů.

TCP proxy pro OIG Box, která dekóduje XML rámce, publikuje data do MQTT (HA autodiscovery), dekóduje warningy a loguje neznámé senzory pro doplnění mapy. Součástí je DNS přepis, aby Box mluvil na lokální proxy místo cloudu.

## Klíčové funkce v1.3.0
- 🔄 **Multi-mode proxy**: ONLINE (forward) / OFFLINE (local ACK) / REPLAY (queue drain)
- 💾 **Persistentní fronty**: SQLite queue pro cloud i MQTT data
- 🔌 **Odolnost vůči výpadkům**: Automatická detekce cloud outage, lokální ACK generování
- 📡 **Auto-discovery**: Automatická detekce DEVICE_ID z BOX komunikace
- ♻️ **Replay mechanismus**: Automatické odeslání zafrontovaných dat po obnovení cloudu

## Struktura
- `proxy/` – hlavní Python proxy (`main.py`), načítá mapping ze sdíleného `sensor_map.json`, dekóduje warning bity (`ERR_*`).
- `addon/oig-proxy/` – Home Assistant add-on (config.json, Dockerfile, run), používá stejný `sensor_map.json`.
- `dnsmasq.conf`, `Corefile` – ukázka DNS přepisu.
- `logs/` – prázdné (logy necommitujeme).

## Co proxy umí
- Publikuje data do MQTT topicu `oig_box/<device_id>/state`, posílá HA discovery (`homeassistant/sensor/.../config`) a availability `oig_box/<device_id>/availability`.
- Načítá senzory z JSON mapy; neznámé klíče auto-discovery s generickým názvem a logováním do `/data/unknown_sensors.json`.
- Dekóduje bitové warningy `ERR_*` přes `warnings_3f` (vč. českých textů) a přidává `<ERR_X>_warnings` se seznamem hlášek.
- Mapu lze reloadovat za běhu (`MAP_RELOAD_SECONDS` > 0); `unique_id`/entity_id má tvar `oig_local_<device_id>_<sensor_key>`.
- Pokud je `LOG_LEVEL=DEBUG`, loguje RAW rámce i PARSED payload pro ladění.
- Volitelně může ukládat payloady do SQLite (`/data/payloads.db`) při zapnuté volbě `capture_payloads`.

## Tok komunikace
```
OIG Box  --DNS override-->  HA host (addon OIG Proxy, port 5710)  --TCP-->  oigservis.cz (cloud)
   |                             |
   |  XML frame                  |  Parse + map + warnings decode
   |---------------------------->|  Publish state to MQTT: oig_box/<device_id>/state
                                 |  Send HA discovery: homeassistant/sensor/.../config
                                 |  Availability: oig_box/<device_id>/availability
MQTT Broker (mosquitto addon) <--+
   |
   v
Home Assistant (entities vytvářené z discovery)
```

## Požadavky na uživatele
1) **MQTT broker** (např. HA add-on Mosquitto), vytvořit účet/heslo a znát host/port.
2) **DNS/route přepis**: zajistit, aby `oigservis.cz` (target) směřoval na IP HA s proxy (router DNS, HA DNS, nebo vlastní dnsmasq z `dnsmasq.conf`). Box musí volat na HA port 5710.
3) **Add-on repo**: v HA → Doplňky → Repos přidat `https://github.com/Muriel2Horak/oig-proxy`.
4) **Instalace add-onu**: „OIG Proxy“ → Configure:
   - `target_server`: `oigservis.cz` (nebo vlastní, pokud se mění název, ale obvykle jen DNS přepis).
   - `target_port`: 5710
   - `proxy_port`: 5710 (stejný port, na který Box volá)
   - `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`: dle Mosquitto.
   - `map_reload_seconds`: 0 (vypnuto) nebo např. 300 pro periodický reload mapy.
   - Mapování senzorů: mountuje `/data/sensor_map.json`; neznámé klíče se logují do `/data/unknown_sensors.json`.
5) **Spustit add-on** a ověřit v logu „Nové připojení“ a publikované discovery v MQTT.

## Lokální spuštění (mimo HA)
```
cd proxy
MQTT_HOST=... MQTT_PORT=1883 python -u main.py
```
Nebo docker-compose v rootu (doplnit env pro MQTT a cílový server).

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
- `SENSOR_MAP_PATH` (default `/data/sensor_map.json` v add-onu).
- `MAP_RELOAD_SECONDS` (0 = vypnuto) – periodický reload mapy.
- `UNKNOWN_SENSORS_PATH` (default `/data/unknown_sensors.json`).

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
