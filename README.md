# OIG Proxy for Home Assistant

TCP proxy pro OIG Box, která dekóduje XML rámce, publikuje data do MQTT (HA autodiscovery), dekóduje warningy a loguje neznámé senzory pro doplnění mapy. Součástí je DNS přepis, aby Box mluvil na lokální proxy místo cloudu.

## Struktura
- `proxy/` – hlavní Python proxy (`main.py`), dynamické mapování ze `sensor_map.json`, dekódování warning bitů (`ERR_*`).
- `proxy/sensor_map.json` – data z Excelu + inventář z logů (239+ klíčů): Modbus registry, warningy 3F, hlášky, FSP parametry. Lze editovat bez rebuildu, reload za běhu (`MAP_RELOAD_SECONDS`).
- `addon/oig-proxy/` – Home Assistant add-on (config.json, Dockerfile, run).
- `dnsmasq.conf`, `Corefile` – ukázka DNS přepisu.
- `logs/` – prázdné (logy necommitujeme).

## Co proxy umí
- Publikuje data do MQTT topicu `oig_box/<device_id>/state`, posílá HA discovery (`homeassistant/sensor/.../config`) a availability `oig_box/<device_id>/availability`.
- Načítá senzory z JSON mapy; neznámé klíče auto-discovery s generickým názvem a logováním do `/data/unknown_sensors.json`.
- Dekóduje bitové warningy `ERR_*` podle Excelu (list „warining 3F“) a přidává `<ERR_X>_warnings` (seznam textů).
- Mapu lze reloadovat za běhu (`MAP_RELOAD_SECONDS` > 0).

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

## Repo
GitHub: https://github.com/Muriel2Horak/oig-proxy
