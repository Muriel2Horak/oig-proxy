# OIG Proxy pro Home Assistant

OIG Proxy předává komunikaci mezi OIG Boxem a cloudem OIG a z dat boxu
vytváří MQTT entity v Home Assistantu. Uvidíte například výrobu FVE,
spotřebu, výkon baterie, tok do sítě, parametry a události boxu. Zařízení
**Diagnostika OIG** ukazuje čerstvost dat, stav MQTT a výsledky místních příkazů.

## Instalace a první kontrola

1. V Home Assistantu spusťte **Mosquitto broker** nebo připravte jiný MQTT broker.
   V **Nastavení → Zařízení a služby** připojte integraci **MQTT** k témuž brokeru.
2. V **Nastavení → Aplikace → Obchod s aplikacemi → ⋮ → Repozitáře** přidejte
   `https://github.com/Muriel2Horak/oig-proxy` a nainstalujte **OIG Proxy**.
   V dřívějších verzích HA se aplikace jmenovaly doplňky.
3. V nastavení OIG Proxy zadejte adresu a přihlašovací údaje MQTT brokeru.
   Pro interní Mosquitto je obvyklý hostitel `core-mosquitto` a port `1883`.
   Výchozí cíl proxy je `bridge.oigpower.cz:5710`.
4. Na resolveru, který používá box, nastavte DNS přepis cloudové domény na
   IP hostitele HA. Můžete využít router, vlastní resolver nebo dnsmasq
   v aplikaci. Podrobnosti a kontrolu přepisu najdete v [nasazení](DEPLOYMENT.md).
5. Spusťte OIG Proxy. V **Protokolu** ověřte naslouchání proxy, připojení boxu
   a MQTT. V integraci **MQTT** zkontrolujte, že přibývají čerstvé hodnoty boxu.

Po uložení voleb restartujte aplikaci. Výchozí režim `online` používá cloud.
`hybrid` při výpadku obslouží box lokálně a později zkusí cloud na dalším
spojení boxu. `offline` zpracovává komunikaci boxu lokálně. Stav `online`
v diagnostice vyjadřuje čerstvá data boxu. Zvolený režim proxy najdete
v `configured_mode` a skutečné připojení ke cloudu v protokolu.

## Experimentální lokální ovládání

> [!WARNING]
> **Parametry měníte na vlastní odpovědnost.** Ovládání je ve výchozím
> nastavení vypnuté. Podpora závisí na boxu a firmwaru. Změny ověřte na
> své instalaci jednotlivě včetně skutečného chování zařízení.

Volba `control_mqtt_enabled` povolí místní požadavky přes MQTT. Po ověření
boxu lze podporované parametry měnit v entitách s příponou `_cfg`.
Přijetí příkazu a potvrzený stav boxu ověřujte zvlášť. Stropy proudu a
přetoků určují, jaké požadavky proxy přijme. Nastavení stropu v aplikaci
nemění hodnotu uloženou v boxu.

[Návod se snímky z Home Assistantu a příklady automatizací](docs/v2/local-control.md)
popisuje přepnutí režimů, nabíjení Home UPS, přetoky a fázové korekce.

## Dokumentace

| Téma | Obsah |
| --- | --- |
| [Konfigurace aplikace](docs/v2/configuration.md) | Všechny volby, výchozí hodnoty, dopad a kontrola v HA. |
| [Režimy proxy](docs/v2/proxy_modes.md) | Chování online, hybrid a offline, návrat cloudu a diagnostika. |
| [Nasazení a návrat](DEPLOYMENT.md) | DNS, instalace, aktualizace a obnova předchozí verze. |
| [Stavy a MQTT entity](docs/v2/sensor_map.md) | Seskupení, ID, měření, události a diagnostika. |
| [MQTT příkazy](docs/v2/twin.md) | Formát příkazů, připravenost, potvrzení a obnova stavu. |
| [Provozní telemetrie](docs/telemetry_overview.md) | Co se odesílá mimo HA a jak sběr vypnout. |
| [Architektura](docs/v2/architecture.md) | Tok dat a implementace proxy. |
| [Kontroly a testy](docs/CI_CD_OVERVIEW.md) | Testy, kontroly kvality a bezpečnostní skeny. |

Změny podle verzí jsou v [changelogu](CHANGELOG.md).
