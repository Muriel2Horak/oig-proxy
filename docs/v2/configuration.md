# Nastavení doplňku OIG Proxy v2

Volby najdete v Home Assistantu v nastavení doplňku **OIG Proxy** na kartě **Konfigurace**. Po uložení změn doplněk restartujte. Nové hodnoty čte při startu. Pro kontrolu otevřete kartu **Protokol** doplňku nebo zařízení **Diagnostika OIG** v integraci MQTT.

Tabulky pokrývají všech 30 voleb doplňku. Typy a výchozí hodnoty odpovídají [`config.json`](../../addon/oig-proxy/config.json), načítání určuje [startovací skript](../../addon/oig-proxy/run) a [konfigurace Pythonu](../../addon/oig-proxy/config.py). Znak `?` je součást schématu a označuje volitelný klíč. `""` je prázdný řetězec.

## Parameter Table

| Klíč | Typ v HA | Výchozí hodnota | Účel a účinek | Jak ověřit |
| --- | --- | --- | --- | --- |
| `target_server` | `str` | `bridge.oigpower.cz` | Jméno cíle, na který proxy posílá provoz do cloudu OIG. Vestavěný dnsmasq současně přepisuje stejné jméno pro Box. Pro výchozí doménu přepisuje i starší `oigservis.cz`. | Při startu hledejte `Cloud host … resolved to … via …`. Z LAN zvlášť ověřte, že DNS používané Boxem vrací adresu proxy a že upstream použitý proxy vrací skutečný cloud. |
| `target_port` | `int` | `5710` | Vzdálený TCP port cloudu OIG. Změna upraví odchozí spojení proxy. | Chybný port se projeví neúspěšným připojením do cloudu v protokolu. Příchozí port kontrolujte samostatně. |
| `proxy_port` | `int` | `5710` | Příchozí TCP port, na který se připojuje Box. Doplněk sdílí síť hostitele HA. Samotný přepis DNS mění IP adresu, proto při jiném portu musí přesměrování příchozího spojení odpovídat nastavení Boxu. | V protokolu hledejte `OIG Proxy v2 naslouchá na …` a následné `BOX připojen`. Port už může používat jiná služba na stejném hostiteli. |
| `proxy_mode` | `list(online\|hybrid\|offline)` | `online` | `online` vyžaduje spojení do cloudu OIG. `hybrid` začíná s cloudem a při selhání může Box obsloužit lokálně. `offline` neotevírá cloudová TCP spojení pro Box a odpovídá lokálně. V offline režimu může doplněk nadále používat DNS, MQTT a zapnutou telemetrii. | V MQTT stavu `oig_local/oig_proxy/proxy_status/state` sledujte `configured_mode`. Skutečný přechod hybridu ověřte podle hlášek `HYBRID` v protokolu. Samotné `mode` v této zprávě je odvozené z čerstvosti dat Boxu. |
| `cloud_ack_timeout` | `float` | `1800.0` | Dřívější volba pro čekání na odpověď cloudu. Doplněk načte hodnotu do konfigurace, ale aktuální server ji při přeposílání nepoužívá. Limit samotného navázání TCP spojení je samostatné `CLOUD_CONNECT_TIMEOUT`, výchozích 10 sekund. | Změna této volby v této verzi nezkrátí čekání na ACK ani neurychlí přechod hybridu. Případné hlášení o timeoutu navázání spojení se řídí `CLOUD_CONNECT_TIMEOUT`. |
| `local_getactual_enabled` | `bool?` | `false` | Proxy může do aktivní relace Boxu přidat vlastní `GetActual`, když potřebujete častěji žádat okamžitá data. Používá se v relaci s cloudem i při lokální obsluze. Jde o volitelný dotaz, jehož odpověď závisí na Boxu a firmwaru. | Na úrovni `DEBUG` hledejte `Sent local GetActual` a potom zkontrolujte, zda přibyly čerstvé hodnoty `tbl_actual`. |
| `local_getactual_interval_s` | `int?` | `10` | Rozestup vlastních `GetActual` v sekundách, pouze při zapnutém `local_getactual_enabled`. První pokus přichází po spuštění obsluhy relace. Hodnoty pod 10 se zvýší na 10 a během čekání na odpověď na lokální zápis se dotaz může odložit. | Ověřte `interval=…s` a časový rozestup v hláškách `Sent local GetActual`. Interval mezi skutečnými datovými rámci Boxu se může lišit. |
| `hybrid_retry_interval` | `int` | `60` | Počet sekund, po kterých nové připojení Boxu v hybridním offline stavu může znovu zkusit cloud. Neúspěšný pokus čekání obnoví. | V protokolu hledejte `HYBRID: retry interval … passed, trying cloud`. Pokud Box nové spojení nezahájí, po uplynutí intervalu se žádný pokus neprovede. |
| `hybrid_fail_threshold` | `int` | `1` | Počet po sobě jdoucích zaznamenaných selhání cloudu potřebný k přechodu do lokální obsluhy. Úspěšné připojení čítač nuluje. Platí pro `hybrid`. | V protokolu hledejte `HYBRID: … failures` a přechod do `offline`. Výchozí chování doplňku spustí přechod už při prvním zaznamenaném selhání. |
| `mqtt_host` | `str` | `core-mosquitto` | Adresa hlavního brokeru pro stavy, MQTT discovery a místní příkazy. `core-mosquitto` je běžný interní název doplňku Mosquitto. | Hledejte `MQTT connected to …` a aktualizace entit MQTT v HA. Při nedostupném brokeru TCP proxy běží dál, ale HA přes MQTT nedostane nové stavy. |
| `mqtt_port` | `int` | `1883` | TCP port hlavního brokeru. Nastavení portu samo nezapne TLS. | Zkontrolujte hostitele a port v `MQTT connected to …` nebo chybu připojení. HA a proxy musí používat stejný broker nebo propojené brokery. |
| `mqtt_username` | `str` | `""` | Uživatel brokeru. Prázdný uživatel způsobí, že klient nepředá brokeru uživatelské jméno ani heslo. | Zkontrolujte úspěšné připojení a oprávnění brokeru pro stav, discovery a případné příkazy. Pro rozlišení chyb účtu a oprávnění pomůže i protokol brokeru. |
| `mqtt_password` | `str` | `""` | Heslo k `mqtt_username`. Proxy je použije pouze při neprázdném uživatelském jménu. | Při odmítnutí spojení zkontrolujte účet na brokeru. Heslo při sdílení konfigurace odstraňte. |
| `ha_ip` | `str?` | `""` | Adresa IPv4 hostitele HA pro lokální přepis DNS. Pokud není nastavená, skript zkusí vybrat adresu podle síťové trasy a potom podle výstupu `hostname -I`. | Dotaz na jméno z LAN přes DNS hostitele HA má vrátit zvolenou adresu. Vyplňte ji, pokud automatika použije jiné rozhraní, než ze kterého je Box dostupný. |
| `dns_override_ip` | `str?` | `""` | Výslovný cíl lokálního přepisu DNS, například adresa samostatného TCP relé. Má přednost před `ha_ip` a automatickým výběrem. Odchozí cíl proxy zůstává určen `target_server` a `target_port`. | Ověřte odpověď lokálního DNS a dostupnost proxy z cílové adresy. Hodnota, která neprojde jednoduchou kontrolou formátu IPv4, vede na `127.0.0.1`. |
| `dns_upstream` | `str?` | `8.8.8.8` | Resolver pro ostatní dotazy dnsmasq. Python používá stejný resolver pro přímý dotaz na cloudový A záznam. Při jeho selhání zkusí použít jméno přímo. Prázdná hodnota nebo hodnota mimo očekávaný formát IPv4 použije `8.8.8.8`. | Porovnejte `dig @<ha_ip> <target_server> A` a `dig @<dns_upstream> <target_server> A`. Jestli upstream vrací lokální přepis pro Box, proxy se při pokusu o cloud může připojit sama k sobě. |
| `log_level` | `list(INFO\|DEBUG\|TRACE)` | `INFO` | `INFO` ukazuje start, spojení a běžný provoz. `DEBUG` přidává například obsah XML rámců a průběh MQTT discovery. `TRACE` přidává i hexadecimální bajty rámců a další podrobnou diagnostiku. | Zkontrolujte úrovně záznamů na kartě **Protokol**. Podrobné logy zvyšují objem dat a mohou obsahovat hodnoty nebo identifikátory zařízení. |
| `proxy_status_interval` | `int` | `60` | Sekundy mezi pravidelnými publikacemi diagnostiky proxy. Hodnota 0 nebo záporná zastaví periodickou smyčku. Změna režimu, výsledek příkazu nebo obnova MQTT mohou stav publikovat i bez smyčky. | Sledujte `oig_local/oig_proxy/proxy_status/state` a startovní hlášku `Proxy status: periodic publish every …` nebo `Proxy status loop disabled`. Dostupnost Boxu ve zprávě vychází z příjmu dat v posledních 90 sekundách a je odlišná od dostupnosti cloudu. |
| `full_refresh_interval_hours` | `int` | `24` | Dřívější volba pro periodickou obnovu. Start exportuje `FULL_REFRESH_INTERVAL_H`, kterou tato verze dál nečte. Discovery se aktualizuje při startu a při obnově MQTT nebo po zprávě o startu HA. Samostatný plánovač načítání parametrů Boxu má vlastní interval. | Změna hodnoty neposune další discovery ani denní dotaz na parametry. Po obnově sledujte `Restored confirmed MQTT states` a stav entit MQTT v HA. |
| `capture_payloads` | `bool?` | `false` | Ukládá zachycené rámce do SQLite `/data/payloads.db`, včetně XML textu, zpracovaných polí a metadat. Při přeplněné frontě může jednotlivý rámec vynechat. | Ověřte `Frame payload capture enabled` a přírůstek záznamů v databázi. Ukládání rámců je diagnostika a vypnutí nezastaví zpracování stavů ani kvalifikaci profilu `ver`. |
| `capture_raw_bytes` | `bool?` | `false` | Při zapnutém `capture_payloads` přidá původní bajty rámce v base64 do sloupce `raw_b64`. Text XML se ukládá i při `false`. | Hledejte `raw_bytes=True` ve startovním logu a neprázdné `raw_b64` u nových záznamů. Databáze bude větší. |
| `capture_retention_days` | `int?` | `7` | Stáří zachycených rámců v SQLite ve dnech. Běžící zapisovač záznamy promazává přibližně po 10 minutách. Nula a záporné hodnoty promazávání vypnou. | Na úrovni `DEBUG` hledejte `FrameCapture pruned … old frames`. Retence neřeší velikost databáze okamžitě a neplatí pro PCAP ani telemetrickou frontu. |
| `capture_pcap` | `bool?` | `false` | Samostatně spouští `tcpdump` pro TCP provoz na `proxy_port` a ukládá pakety do `/data/capture.pcap`. Vyžaduje funkční `tcpdump` a přístup k síťovým paketům. Výchozí rotace vytváří další soubory po zhruba 100 MB a neurčuje jejich celkový limit. | Ověřte `PcapCapture started`, případné chyby `tcpdump` a růst souboru. PCAP může obsahovat celý obsah zachycené komunikace. |
| `control_mqtt_enabled` | `bool?` | `false` | Povolení místních zápisů do Boxu. Proxy vyžaduje podporovaný parametr, povolenou hodnotu a aktuálně kvalifikovaný profil `ver`. Při `false` zůstává čtení a místní ovládání režimu samotné proxy dostupné. Zapnutí rovněž umožní dennímu plánovači požádat Box o parametry přes `SA`. | V `oig_local/oig_proxy/proxy_status/state` sledujte `control_ver_ready`, `control_ver_reason` a `control_result`. Pozorovaný stav parametru ověřte odděleně. Zobrazení `_cfg` v HA samo neznamená, že je zápis povolený. |
| `ac_charge_max_a` | `int(0,125)?` | `0` | Instalační strop pro místní příkaz `tbl_invertor_prm1:AAC_MAX_CHRG`. Při kladné hodnotě lze požadovat celé ampéry od 0 po strop. Samotné uložení volby proud v Boxu nezmění. Nula v konfiguraci ponechá parametr pouze ke čtení. | Pro povolený parametr zkontrolujte rozsah `_cfg` v HA nebo jeho MQTT discovery. Po zápisu zkontrolujte `control_result` i následnou hodnotu ze zařízení. |
| `grid_export_max_w` | `int(0,4294967295)?` | `0` | Instalační strop místního příkazu `tbl_invertor_prm1:P_MAX_FEED_GRID` v celých wattech. Kladná hodnota umožní požadovat 1 W až nastavený strop a zpřístupní příkazy `GRID_PV_OFF`, `GRID_PV_ON` a `TO_GRID`. Nula v konfiguraci nechá všechny čtyři parametry pouze ke čtení. Horní hranice schématu odpovídá datovému typu. Zvolte limit odpovídající konkrétní instalaci. | V HA ověřte rozsah `_cfg` a potvrzený limit Boxu před zapnutím výdeje do sítě. `GRID_PV_OFF` a `GRID_PV_ON` povolují výdej z baterie bez dostupné FVE a při dostupné FVE. `TO_GRID` ovládá povolení dodávky do sítě. Všechny tři přepínače používají `0` pro vypnuto a `1` pro zapnuto. |
| `phase_calibration_enabled` | `bool?` | `false` | Zpřístupní experimentální zápis `tbl_invertor_prm1:P_CAL_R`, `P_CAL_S` a `P_CAL_T` pro fáze L1, L2 a L3. Proxy přijímá surová celá čísla v rozsahu kódování U16 (`0` až `65535`, krok `1`). | Provozní rozsah a jednotka pro jednotlivé instalace zůstávají neověřené. Před změnou zaznamenejte původní hodnotu a ověřte změnu i návrat v senzoru stejné fáze. |
| `maxfg_as_export_limit_readback` | `bool?` | `false` | Volitelně čte `tbl_actual:MAXFG` jako potvrzený stav `tbl_invertor_prm1:P_MAX_FEED_GRID`. Proxy potřebuje pro stejný Box předchozí přímý údaj o limitu s časem a pak novější časově srovnatelnou kladnou celočíselnou hodnotu `MAXFG`. Změna firmwaru může vyžadovat nový přímý údaj. Volba nepovoluje zápisy. | Nejprve na daném Boxu porovnejte přímý údaj `P_MAX_FEED_GRID` a `MAXFG` a ověřte, že označují stejný limit. Po zapnutí sledujte oba MQTT stavy a čas dat. Údaj o limitu označuje nastavení Boxu. Okamžitý výkon do sítě ověřte na odpovídajícím výkonovém senzoru. |
| `telemetry_enabled` | `bool?` | `true` | Spouští provozní telemetrii na oddělený broker `telemetry.muriel-cz.cz:1883`. Běžný interval je 300 sekund. Odesílaná data mohou zahrnovat ID Boxu, hash instalace, adresu protistrany, statistiky, vybraná datová pole Boxu, události a audit příkazů. Při problémech nebo během některých událostí mohou obsahovat i výňatky z logu. Neodeslané zprávy může ukládat `/data/telemetry_buffer.db`. | V protokolu hledejte `TelemetryCollector started` a `Telemetry client connected`. Při `false` se externí sběrač nespustí. Lokální MQTT stavy a provoz Boxu přes proxy fungují samostatně. |
| `max_concurrent_connections` | `int?` | `5` | Strop současných TCP relací od Boxu. Přebytečné spojení proxy zavře hned při přijetí. Použijte kladné číslo, hodnota 0 odmítne každé nové spojení. | Odmítnutí ukazuje log `Max concurrent connections (…) reached`. Jde o relace, takže jeden Box může během opakovaného připojování vyčerpat více míst. |

## Souvislosti při nastavování

Vestavěný dnsmasq se spouští i při prázdných `ha_ip` a `dns_override_ip`. Odpovědi využijí zařízení, která dotazy posílají na DNS hostitele HA. Pokud tento hostitel pro TCP nebo UDP port 53 už používá jiný DNS server, ověřte v protokolu, zda dnsmasq port otevřel.

Více o přepínání režimů popisují [režimy proxy](proxy_modes.md). Změna režimu přes MQTT `proxy_control:PROXY_MODE` je změna pro běžící proces. Po restartu se použije hodnota `proxy_mode` z konfigurace doplňku.

`ac_charge_max_a`, `grid_export_max_w` a `phase_calibration_enabled` vyžadují povolené `control_mqtt_enabled`. Limit konfigurace určuje, jaké místní příkazy proxy přijme, a neprovádí automatické omezení výkonu na Boxu. MQTT příkazy posílejte jako čerstvé zprávy s QoS 0 a bez příznaku retain. Read-only senzor a jeho `_cfg` u podporovaných výkonových parametrů sdílejí potvrzený stav. Pro navazující automatizaci počkejte na skutečnou hodnotu Boxu i tehdy, když proxy už ohlásila `ack`. Podrobnosti popisuje [ovládání Boxu přes MQTT](twin.md).

## Příklad pro Home Assistant

```yaml
target_server: bridge.oigpower.cz
target_port: 5710
proxy_port: 5710
proxy_mode: hybrid
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: oig_proxy
mqtt_password: "<heslo k účtu MQTT>"
log_level: INFO
hybrid_retry_interval: 60
hybrid_fail_threshold: 1
```

Má-li Box používat vestavěný DNS přepis doplňku, upravte adresy podle své sítě a v nastavení sítě Boxu nebo DHCP určete jeho DNS resolver.

```yaml
ha_ip: 192.168.1.100
dns_override_ip: ""
dns_upstream: 192.168.1.1
```

## Proměnné prostředí pro samostatný běh

Mimo Supervisor použijte proměnné prostředí. `HA_IP` a `DNS_OVERRIDE_IP` exportuje startovací skript doplňku, který také nastavuje dnsmasq. Samotné spuštění `main.py` konfiguraci dnsmasq nevytváří. `FULL_REFRESH_INTERVAL_H` v této verzi nemá spotřebitele.

| Volby doplňku | Odpovídající proměnné |
| --- | --- |
| `target_server`, `target_port`, `proxy_port`, `proxy_mode`, `cloud_ack_timeout` | `TARGET_SERVER`, `TARGET_PORT`, `PROXY_PORT`, `PROXY_MODE`, `CLOUD_ACK_TIMEOUT` |
| `local_getactual_enabled`, `local_getactual_interval_s`, `hybrid_retry_interval`, `hybrid_fail_threshold` | `LOCAL_GETACTUAL_ENABLED`, `LOCAL_GETACTUAL_INTERVAL_S`, `HYBRID_RETRY_INTERVAL`, `HYBRID_FAIL_THRESHOLD` |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD` |
| `ha_ip`, `dns_override_ip`, `dns_upstream`, `log_level` | `HA_IP`, `DNS_OVERRIDE_IP`, `DNS_UPSTREAM`, `LOG_LEVEL` |
| `proxy_status_interval`, `full_refresh_interval_hours` | `PROXY_STATUS_INTERVAL`, `FULL_REFRESH_INTERVAL_H` |
| `capture_payloads`, `capture_raw_bytes`, `capture_retention_days`, `capture_pcap` | `CAPTURE_PAYLOADS`, `CAPTURE_RAW_BYTES`, `CAPTURE_RETENTION_DAYS`, `CAPTURE_PCAP` |
| `control_mqtt_enabled`, `ac_charge_max_a`, `grid_export_max_w`, `phase_calibration_enabled`, `maxfg_as_export_limit_readback` | `CONTROL_MQTT_ENABLED`, `AC_CHARGE_MAX_A`, `GRID_EXPORT_MAX_W`, `PHASE_CALIBRATION_ENABLED`, `MAXFG_AS_EXPORT_LIMIT_READBACK` |
| `telemetry_enabled`, `max_concurrent_connections` | `TELEMETRY_ENABLED`, `MAX_CONCURRENT_CONNECTIONS` |

Při přímém spuštění Pythonu je výchozí `HYBRID_FAIL_THRESHOLD=3` a načítaná hodnota `CLOUD_ACK_TIMEOUT=30.0`. Supervisor předává `1` a `1800.0` z konfigurace doplňku. Ostatní nejběžnější proměnné dostupné mimo formulář Supervisoru jsou níže.

| Proměnná | Výchozí hodnota | Význam |
| --- | --- | --- |
| `PROXY_HOST` | `0.0.0.0` | Adresa rozhraní, kde proxy přijímá TCP spojení Boxu. |
| `CLOUD_CONNECT_TIMEOUT` | `10.0` | Limit navázání TCP spojení do cloudu v sekundách. |
| `MQTT_NAMESPACE` | `oig_local` | Předpona lokálních témat MQTT. |
| `MQTT_QOS` | `1` | QoS běžných stavových zpráv. Ovládací odběry vyžadují QoS 0. |
| `MQTT_STATE_RETAIN` | `true` | Zda běžné stavové publikace používají příznak retain. |
| `PROXY_DEVICE_ID` | `oig_proxy` | Identifikátor zařízení pro stav proxy. |
| `SENSOR_MAP_PATH` | `/data/sensor_map.json` | Cesta k mapě senzorů. Startovací skript doplňku ji nastavuje na výchozí hodnotu. |
| `PARAMETER_REFRESH_INTERVAL_S` | `86400` | Interval samostatného plánovače dotazu `SA` v sekundách. Nula plánovač vypne. Dotaz vyžaduje povolené řízení Boxu, připojený Box, platný profil a prázdnou frontu příkazů. Úspěšné potvrzení lze dohledat v `/data/parameter_refresh.json`. |
| `TELEMETRY_MQTT_BROKER` | `telemetry.muriel-cz.cz:1883` | Adresa odděleného telemetrického brokeru. |
| `TELEMETRY_INTERVAL_S` | `300` | Interval odesílání telemetrie v sekundách. |
