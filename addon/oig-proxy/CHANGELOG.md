<!-- markdownlint-disable MD025 -->

# 2.2.0-rc.11

> **Experimentální lokální ovládání OIG Boxu.** Nastavení měníte na vlastní
> odpovědnost. Před aktualizací si zazálohujte doplněk. Každou změnu nejdřív
> vyzkoušejte ručně a ověřte výsledek na svém boxu. Home Assistant se
> zapnutými automatickými aktualizacemi může tuto verzi nainstalovat sám.

- **Hybrid a grafy v OIG Cloudu.** Při výpadku cloudu proxy ukládá podporované
  archivní tabulky do trvalé fronty. Po obnovení spojení je doposílá v pořadí
  s původními časy měření. Graf se proto může doplňovat postupně.
- **Synchronizace po změně nastavení.** Proxy počká na obvyklou tabulku boxu.
  Když v ní potvrzenou hodnotu nedostane, spojí čekající změny do jednoho
  vyžádání parametrů. Zobrazení režimu nebo nabíjecího proudu v HA a OIG
  Cloudu se tak může aktualizovat s různým odstupem.
- **Denní obnova parametrů.** Volba `full_refresh_interval_hours` určuje,
  po jak dlouhé době bez potvrzené obnovy se proxy pokusí parametry vyžádat.
  Po upgradu se započítá i hodnota uložená v nastavení doplňku. Výchozí
  hodnota je 24 hodin. Po odeslaném denním dotazu bez potvrzení proxy s
  dalším denním pokusem počká stejnou dobu. Nastavení na 0 vypne denní obnovu.
- **Ověření lokálního zápisu.** Proxy při obnově ověřeného profilu z archivu
  zohlední posunuté hodiny boxu. V Diagnostice OIG ověřte stav entity
  **Lokální zápis – ověřený profil ver** před použitím místního ovládání.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.11/docs/v2/local-control.md)
[Konfigurace doplňku](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.11/docs/v2/configuration.md)

# 2.2.0-rc.10

> **Experimentální lokální ovládání OIG Boxu.** Nastavení měníte na vlastní
> odpovědnost. Před aktualizací si zazálohujte aplikaci. Každou změnu nejdřív
> vyzkoušejte ručně a ověřte její dopad na svém boxu. Home Assistant se
> zapnutými automatickými aktualizacemi může tuto verzi nainstalovat sám.

- Při změně režimu počkejte na výsledek zápisu. Proxy teď pozná potvrzenou
  změnu režimu i tehdy, když mezi potvrzovacími událostmi dorazí běžná data
  z boxu. Když před opakováním přijde potvrzení požadovaného režimu, proxy
  druhý zápis zruší.
- U korekcí L1, L2 a L3 ukazuje ovladač `_cfg` poslední potvrzený zápis
  s časem a zdrojem potvrzení. Senzor stejné fáze ukazuje korekci, kterou box
  právě používá, včetně nuly. Když se dotaz na parametry podaří poslat později,
  lhůta pro odpověď se počítá od jeho odeslání.
- V **Diagnostika OIG → Souhrn chyb** najdete čitelné aktivní varování
  a nedávné záznamy s časem a zdrojem. Nula v chybové masce varování uzavře.
  Nepotvrzené kódy zůstávají v historii a tma bez výroby FVE se nehlásí jako
  porucha.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.10/docs/v2/local-control.md)

# 2.2.0-rc.9

> **Experimentální lokální ovládání OIG Boxu.** Nastavení měníte na vlastní
> odpovědnost. Před aktualizací zálohujte aplikaci. Než zapnete automatizace,
> vyzkoušejte změnu ručně a ověřte její skutečný dopad na svém boxu.
> Home Assistant se zapnutými automatickými aktualizacemi může tuto verzi
> nainstalovat po vydání sám.

- Proxy může odeslat čekající místní příkaz `Setting` po ověřeném dotazu
  boxu `IsNewSet`, `IsNewFW` nebo `IsNewWeather`. Využije TCP spojení,
  na kterém box dotaz poslal. Při probíhajícím zápisu z cloudu lokální
  příkaz počká na další vhodný dotaz. Při obnově hybridního spojení dostane
  cloud nejdřív příležitost potvrdit, že opět odpovídá.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.9/docs/v2/local-control.md)

# 2.2.0-rc.8

> **Experimentální lokální ovládání OIG Boxu.** Nastavení měníte na vlastní
> odpovědnost. Před aktualizací zálohujte aplikaci. Než zapnete automatizace,
> vyzkoušejte změnu ručně a ověřte její skutečný dopad na svém boxu.
> Home Assistant se zapnutými automatickými aktualizacemi může tuto verzi
> nainstalovat po vydání sám.

- Když v hybridním režimu selže spojení s cloudem před výměnou dat, proxy
  ponechá box připojený a odpovídá mu místně. Pokud už výměna začala,
  probíhající spojení uzavře bez opakování jeho dat.
- Během obnovy zkouší cloud na jednom spojení. Další spojení boxu během
  zkoušky dál obsluhuje místně. Návrat potvrdí platná odpověď z cloudu
  a box se pak může znovu připojit přes cloud.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.8/docs/v2/local-control.md)

# 2.2.0-rc.7

> **Experimentální lokální ovládání OIG Boxu.** Nastavení měníte na vlastní
> odpovědnost. Před aktualizací zálohujte aplikaci. Než zapnete automatizace,
> vyzkoušejte změnu ručně a ověřte její skutečný dopad na svém boxu.
> Home Assistant se zapnutými automatickými aktualizacemi může tuto verzi
> nainstalovat po vydání sám.

- Lokální požadavek může odejít už při nejbližším vhodném dotazu boxu na
  otevřeném spojení. Proxy zpracuje dotaz i při rozdělení nebo sloučení
  zpráv. V protokolu add-onu uvádí důvod, proč čekající požadavek neodeslala.
- Jednoznačná událost o změně hodnoty může potvrdit příkaz i při chybějícím
  protokolovém ACK. Proxy pak už provedenou změnu zbytečně neopakuje.
- U boxů s posunutým časem může `ver` přejít na `ready` dříve. Proxy si
  čas ověří z běžné komunikace s cloudem a profil dál ověřuje z čerstvých
  rámců boxu. Dostupnost lokálního zápisu uvidíte v Diagnostice OIG v HA.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.7/docs/v2/local-control.md)

# 2.2.0-rc.6

> **Experimentální lokální ovládání OIG Boxu.** Nastavení měníte na vlastní
> odpovědnost. Před aktualizací zálohujte aplikaci a před zapnutím automatizací
> ověřte příkaz a jeho skutečný dopad na své instalaci.
> Home Assistant se zapnutými automatickými aktualizacemi může tuto verzi
> nainstalovat po vydání sám.

- Pokud má proxy v archivu potvrzený cloudový zápis pro váš box, může po
  aktualizaci obnovit údaje potřebné k lokálním zápisům. Jakmile ověří čerstvá
  data boxu, můžete pokračovat bez dalšího cloudového zápisu.
- Box s delším připojením může zpracovat několik lokálních příkazů postupně
  na stejném spojení. Po jednoznačném potvrzení opakovaného příkazu může další
  zápis odejít při vhodném dotazu boxu.
- Když po potvrzení příkazu nebo události změny nepřijde správná tabulka,
  proxy po minutě jednou požádá box o parametry. Box tak může dřív poslat
  potvrzenou hodnotu do Home Assistantu i do cloudu.
- Fázové korekce na všech třech fázích používají formát z tabulky parametrů
  boxu. V ovladačích zmizel seznam vybraných hodnot z dřívějších zkoušek.
  Provozní rozsah korekcí závisí na konkrétní instalaci.
- Grafana zobrazuje dostupnost boxu za celý telemetrický interval. Počítá se
  i krátké spojení, které skončilo před odesláním statistik.
- Časy událostí boxu se před uložením převádějí z českého času do UTC
  se zohledněním letního času.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.6/docs/v2/local-control.md)

# 2.2.0-rc.5

> **Pozor: lokální ovládání OIG Boxu je experimentální.** Před nasazením do
> automatizací ověřte chování na své instalaci. Home Assistant se zapnutou
> automatickou aktualizací může tuto verzi nainstalovat sám.

- Po události boxu `HG … Start` se senzor režimu i ovladač `_cfg` v Home
  Assistantu aktualizují okamžitě. Týká se to režimů Home 1, Home 2,
  Home 3 a Home UPS.
- Ověřeno na fyzickém boxu při přepnutí Home UPS → Home 2 → Home UPS. Obě
  entity ukázaly nový režim do 0,1 s od příjmu události, před přijetím nové
  tabulky parametrů. Starší tabulka novější potvrzený režim nepřepíše.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/2.2.0-rc.5/docs/v2/local-control.md)

# 2.2.0-rc.4

> **Pozor: lokální ovládání OIG Boxu je experimentální.** Parametry měníte
> na vlastní odpovědnost. Změny mohou ovlivnit nabíjení baterie, spotřebu
> a dodávku do sítě. Kompatibilita se všemi boxy a firmwarem není ověřená.

- Proxy může povolit lokální zápisy i bez aktuálního údaje o firmwaru boxu.
  Po aktualizaci může znovu použít profil `ver` uložený v RC3, pokud se shoduje
  s čerstvými zprávami boxu.
- Při prvním ověření potřebuje proxy také běžný cloudový zápis, který box
  potvrdí. Pokud box ohlásí změnu firmwaru nebo se změní profil či formát
  zápisu, proxy vyžádá nové ověření.
- Před aktualizací vytvořte zálohu aplikace OIG Proxy. Se zapnutou automatickou
  aktualizací může Home Assistant novou verzi nainstalovat sám. Před spuštěním
  automatizací ověřte jeden ruční příkaz a skutečný stav boxu.
- Při návratu na RC3 obnovte také zálohu dat z RC3. Zachováte tím dříve
  ověřený profil pro místní zápis bez cloudu.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/main/docs/v2/local-control.md)

# 2.2.0-rc.3

> **Pozor: lokální ovládání OIG Boxu je experimentální.** Parametry měníte
> na vlastní odpovědnost. Změny mohou ovlivnit nabíjení baterie, spotřebu
> a dodávku do sítě. Kompatibilita se všemi boxy a firmwarem není ověřená.

- Před ruční aktualizací vytvořte zálohu aplikace OIG Proxy. Se zapnutou
  automatickou aktualizací může Home Assistant novou verzi nainstalovat sám.
- Lokální zápisy přes MQTT jsou ve výchozím nastavení vypnuté. Pokud už je
  používáte, automatizace mohou po aktualizaci čekat, než proxy ověří profil
  vašeho boxu. Před jejich spuštěním ověřte jeden ruční příkaz a skutečný
  stav boxu.
- V Home Assistantu můžete přes entity `_cfg` měnit podporované režimy,
  nabíjecí proud, přetoky a fázové korekce. U závislých kroků vždy počkejte
  na potvrzenou hodnotu boxu. Při nabíjení ze sítě nejprve potvrďte režim
  Home UPS a potom nastavte proud v mezích své instalace.
- Na jednom fyzickém boxu jsme s touto verzí v hybridním režimu ověřili
  místní přepnutí Home 1 → Home 2 → Home 1. Zkoušky proudu, přetoků
  a korekcí na této instalaci proběhly s předchozím prototypem.

[Návod k ovládání v Home Assistantu](https://github.com/Muriel2Horak/oig-proxy/blob/4bdee90b04f6036586d354a7102e7c92726c910e/docs/v2/local-control.md)
