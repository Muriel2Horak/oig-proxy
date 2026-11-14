# Nasazení OIG Proxy na Home Assistant OS

OIG Proxy se instaluje jako aplikace Home Assistantu (dříve doplněk). Supervisor ji sestavuje z adresáře [addon/oig-proxy](addon/oig-proxy/) pro `amd64` nebo `aarch64`. Verzi v obchodě určuje [konfigurace aplikace](addon/oig-proxy/config.json) ve větvi, kterou obchod sleduje.

## Instalace

1. Spusťte MQTT broker a připojte k němu integraci MQTT v Home Assistantu. Pro OIG Proxy připravte samostatný účet. Při použití aplikace Mosquitto je hostitel obvykle `core-mosquitto` a port `1883`. [Nastavení MQTT v Home Assistantu](https://www.home-assistant.io/integrations/mqtt/#setting-up-a-broker)
2. V **Nastavení → Aplikace → Instalovat aplikaci** otevřete nabídku **Repozitáře**, přidejte `https://github.com/Muriel2Horak/oig-proxy` a nainstalujte **OIG Proxy**. [Přidání repozitáře aplikací](https://www.home-assistant.io/common-tasks/os/#installing-a-third-party-app-repository)
3. Na kartě **Konfigurace** nastavte `mqtt_host`, `mqtt_port`, `mqtt_username` a `mqtt_password`. Výchozí cloud je `bridge.oigpower.cz:5710` a výchozí port proxy je `5710`. Přístup k TCP proxy a brokeru omezte na důvěryhodnou síť. [Konfigurace](docs/v2/configuration.md) · [Zabezpečení MQTT](SECURITY.md#local-mqtt-control)
4. Nastavte DNS podle postupu níže.
5. Uložte nastavení a spusťte aplikaci. V protokolu zkontrolujte naslouchání na nastaveném portu, přihlášení k brokeru a připojení Boxu. V HA ověřte nové hodnoty stavu a discovery. Diagnostika je na `oig_local/oig_proxy/proxy_status/state`.

Při zprovozňování ponechte zápisy do Boxu přes MQTT vypnuté. Pokud je chcete používat, postupujte podle [návodu k lokálnímu ovládání](docs/v2/local-control.md) a po změně ověřujte čerstvý stav ze zařízení. Režimy `online`, `hybrid` a `offline` popisuje [přehled režimů](docs/v2/proxy_modes.md).

## DNS přesměrování

- V resolveru, který používá Box, přepište cloudové jméno (výchozí `bridge.oigpower.cz`) na IP hostitele s proxy. Přepis můžete nastavit na routeru nebo v samostatném resolveru. Pokud chcete použít dnsmasq z OIG Proxy, nastavte adresu cíle volbou `ha_ip` nebo `dns_override_ip` a zajistěte, že Box používá tento DNS server.
- Aplikace používá síť hostitele a spouští dnsmasq na portu 53. Ověřte, že na stejné adrese a portu nekoliduje s jiným DNS serverem. Volba `dns_upstream` musí proxy umožnit přeložit cloudové jméno na skutečnou adresu cloudu.
- Z klienta ve stejné síti ověřte odpověď dotazem `nslookup bridge.oigpower.cz <IP-resolveru-boxu>`. Očekávejte IP proxy. Pak ověřte, že Box navazuje spojení na nastaveném portu proxy. [Volby DNS](docs/v2/configuration.md)

## Aktualizace a zkouška kandidáta

Pro běžný upgrade použijte Supervisor a zálohu aplikace. Pro soukromou zkoušku ověřte zdrojový commit, architekturu a identitu obrazu, zaznamenejte původní stav a mějte připravený návrat před přepnutím Boxu. Dvě instance na jednom hostiteli mohou kolidovat na DNS portu nebo používat stejné MQTT identity. Testovací instanci provozujte na odděleném hostiteli či v izolované síti a s odděleným brokerem. [Aktualizace a návrat](UPDATE_HA_SERVER.md)

Před zveřejněním ověřte build pro obě architektury, testy, pokrytí, lint, typy a bezpečnostní kontroly. Změny do vzdáleného repozitáře patří do PR. Pokud kandidát změní verzi ve větvi sledované Supervisorem, může se nabídnout k aktualizaci uživatelům této větve. Označení GitHub release jako prerelease tuto nabídku neřídí. [Ověření kandidáta](docs/SECURITY_TESTING.md) · [Verze aplikace a její sestavení](https://contributors.home-assistant.io/apps/reference/configuration/)
