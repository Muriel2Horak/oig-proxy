# OIG Proxy – plán refaktoru discovery/mapování (analýza, bez implementace)

## 1) Stávající stav (shrnutí)
- Mapování: `addon/oig-proxy/sensor_map.json` se načítá při startu a doplní do SENSORS; humanizace jen pár popisů. Unknown klíče se logují do `unknown_sensors.json`, ale do mapy se nedoplňují automaticky.
- Discovery: pro známé klíče se pošle discovery `homeassistant/sensor/<unique_id>/config`; `unique_id` je prefix+device+sensor (suffixy byly workaround). HA existující entitě nepřepíše název, pokud se nezmění `unique_id`.
- Eventy: publikujeme event_last, ale historicky se zakládaly senzory z Type/Content/Result… → HWID apod. Kvůli návratům v kódu se někdy neposlalo discovery (prefix se neprojevil).
- Debug: RAW/PARSED se loguje jen při přítomnosti `<Frame>`; event-only rámce často bez detailu.

## 2) Požadavky a dohoda
- Prefix hardcoded (např. `oig_local`), žádné suffixy v UI; jednotný způsob tvorby `unique_id`.
- Event senzor: `oig_local_<device_id>_event_log`, state = Content/Result/Type, attributes = celý payload (audit).
- Unknown klíče: při prvním výskytu doplnit do mapy s popisem `TODO <attr>` a flagem `todo=true`; v názvu senzoru viditelný suffix `TODO` (prefix + číslo boxu + název atributu + `TODO`).
- Staré discovery configy smazat jednorázově (ne při každém startu) – publish retained empty na `homeassistant/sensor/<starý_prefix>*`.
- Hot reload mapy: umožnit načíst novou verzi `/data/sensor_map.json` bez restartu (periodický reload každou minutu) a umět nahrát nový mapping bez restartu pluginu.
- `unknown_sensors.json` po migraci nepoužívat jako primární; jednorázově stáhnout aktuální z HA a zkonzumovat do mapy, dál nové neznámé rovnou merge do mapy jako TODO.
- Nový formát `sensor_map.json` (centrální mapping pro addon, inspirovaný cloudem):
  - `sensors`: klíč → `{name, name_cs, unit_of_measurement, device_class|null, state_class|null, sensor_type_category, device_mapping, todo}`; default `sensor_type_category=measured`, `device_mapping=main`.
  - `warnings_3f`: seznam bitmask (bit, key, remark, remark_cs, warning_code|null) – jen pro dekódování, neposílá se jako senzory.
  - Bez metadat ze zdrojového XLS (table/address/scale/dtype/get_cmd/response/in_inventory... odstraněno).
  - `unique_id`/entity_id: `oig_local_<device_id>_<sensor_key>`; `name`/`name_cs` z mapy použít pro UI název.
- Logika warningů: dekódovat bity přes `warnings_3f` (vč. `remark_cs`) a posílat do `<key>_warnings`; auto-registrace ERR_*_warnings už nebude potřeba po doplnění klíčů v mapě.
- Async proxy: ošetřit korektní zavírání spojení (zrušit/awaitnout `_forward` tasky, zavřít client/server writer) – v logu se objevilo `Task was destroyed but it is pending!` po ukončení spojení.
- Deprecation v addonu: nahradit `datetime.utcnow()` za timezone-aware `datetime.now(datetime.UTC)` (varování v logu).

## 3) Kroky před implementací
- Jednorázově stáhnout aktuální `/data/unknown_sensors.json` z HA a doplnit chybějící klíče do `sensor_map.json` jako `TODO …`; tím tento soubor dál opouštíme.
- Stáhnout aktuální payload `oig_box/<id>/state`; ověřit, že všechny klíče jsou v mapě, chybějící doplnit s `todo=true`.
- Potvrdit prefix (`oig_local`) a název event senzoru (`event_log`).
- Domluvit jednorázové smazání starých configů (skript pro broker) – bude spuštěno jen na tvém serveru při migraci, ne při každém startu.
- Vymyslet mechanismus hot reload mapy a nahrání nové mapy za běhu (bez restartu pluginu).
- Po doplnění mapy projít identifikované senzory (excel, objevené payloady) a sladit významy v diskuzi; stále žádná implementace.
- TODO z `unknown_sensors.json`: doplněno do mapy `ERR_PV_warnings` (bitmask ERR_PV), `Geobaseid` (ID), `LoadedOn` (DateTime). `Fw` a `Up` v XLS nenalezeno – čekáme na vzorek/datový kontext.
- Zbývající TODO klíče (např. WFIX, SKOS, PVMIN0, BAT_*_HI, HGVI_E/S, FA2, EX, CARCHARGE, LO_DAY_MAX, TYP, BAT_DI, SSID, DOMAIN…) zatím ponechány k doplnění; bez manuálu/zdroje.

## 4) Návrh nové logiky (pro implementaci)
- Mapování: při neznámém klíči zapsat do mapy placeholder `description: "TODO <attr>", unit: "", todo: true` a uložit zpět do `/data/sensor_map.json`; discovery jméno pro TODO senzory `prefix <device_id> <attr> TODO` (aby suffix TODO byl vidět v HA), `unique_id` zůstává bez TODO.
- Discovery: `unique_id = oig_local_<device_id>_<sensor>.lower()`; při změně mapy (TODO -> definováno) jednorázově smazat retained config a poslat nový discovery se stejným `unique_id`, ale novým name/unit.
- Eventy: publikovat event senzor `oig_local_<device_id>_event_log` (state + attributes = payload). Nezakládat z event klíčů běžné senzory.
- Debug: pokud LOG_LEVEL=DEBUG, vždy logovat RAW i PARSED pro všechny payloady (vč. event-only).

## 5) Automatizace úklidu starých entit
- Jednorázově na brokeru: publish retained empty na `homeassistant/sensor/oig_*` (starý prefix) → HA odstraní staré entity. Poté poslat nové discovery s prefixem `oig_local`.
- V HA lze pak smazat staré zařízení; nové entity se vytvoří z nových configů.

## 6) Otevřené body k potvrzení
- Hot reload mapy: periodický reload 1× za minutu (fixní parametr) – OK.
- Nahrání nové mapy za běhu: přes SSH se nahradí celý soubor `/data/sensor_map.json`, reload si ho vezme během minuty.
- Smazání starých configů: provedeš jednorázově přes SSH před nasazením; domluvit konkrétní příkaz a termín spuštění.
