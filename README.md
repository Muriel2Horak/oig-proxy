# OIG Proxy for Home Assistant

TCP proxy s dekódováním OIG rámců, publikací do MQTT a automatickým HA discovery. Součástí je DNS přepis (dnsmasq), aby Box mluvil na lokální proxy místo cloudu.

## Složky
- `proxy/` – hlavní Python proxy (`main.py`), MQTT discovery, dynamické mapování senzorů z `sensor_map.json`, dekódování warning bitů (`ERR_*`).
- `proxy/sensor_map.json` – data vytažená z Excelu: Modbus registry (RTU), warningy 3F, hlášky, FSP parametry. Lze editovat bez rebuildu a reloadovat za běhu.
- `docker-compose.yml`, `dnsmasq.conf`, `Corefile` – podpora pro lokální DNS a spuštění proxy.
- `logs/` – prázdné (necommitujeme reálné logy).

## Konfigurace (env)
- `TARGET_SERVER` (default `oigservis.cz`), `TARGET_PORT` (5710) – kam proxy přeposílá.
- `PROXY_PORT` (5710) – lokální port pro Box.
- `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD` – MQTT broker, pro HA discovery se používá `oig_box/<device>/state` a `homeassistant/sensor/.../config`.
- `SENSOR_MAP_PATH` (default `proxy/sensor_map.json`) – JSON s mappingem; lze dát do `/data/sensor_map.json` v add-onu.
- `MAP_RELOAD_SECONDS` (0 = vypnuto) – periodický reload mapy za běhu.
- `UNKNOWN_SENSORS_PATH` (default `/data/unknown_sensors.json`) – logování neznámých klíčů pro doplnění mapy.

## Funkce
- Načte mapu senzorů z JSON (jednotky/popisy/adresy). Neznámé klíče auto‑discovery s generickým názvem, zároveň se ukládají do `unknown_sensors.json`.
- Dekóduje warning bitmasky `ERR_*` podle Excelu (list „warining 3F“) a publikuje odvozený klíč `<ERR_X>_warnings` se seznamem textů.
- MQTT discovery včetně availability (`oig_box/<device>/availability`).

## Lokální spuštění
```
cd proxy
python -u main.py
```
Nebo docker-compose v rootu (doplnit env pro MQTT a cílový server).

## Home Assistant add-on
- V add-onu nastav env podle výše, připoj `/data` pro `sensor_map.json` a `unknown_sensors.json`.
- DNS přepis: nasadit `dnsmasq.conf` nebo jinak zajistit, aby Box resolvoval `oigservis.cz` na IP HA.

## Git
Projekt je připraven v `/Users/martinhorak/Projects/oig-proxy`. Init/push příklad:
```
cd /Users/martinhorak/Projects/oig-proxy
git init
git add .
git commit -m "Initial import"
git remote add origin git@github.com:<user>/<repo>.git
git push -u origin main
```
