# Režimy OIG Proxy

Režim proxy určuje cestu mezi OIG Boxem a cloudem. V Home Assistantu ho
najdete v **Nastavení → Aplikace → OIG Proxy → Nastavení** pod `proxy_mode`.
Tato volba určuje režim po startu. Podporované hodnoty jsou `online`,
`hybrid` a `offline`. Výchozí nastavení aplikace je `online`.

<!-- markdownlint-disable MD013 -->
| Režim proxy | Spojení s cloudem | Příjem dat boxu v MQTT |
| --- | --- | --- |
| `online` | Proxy se připojí při každém spojení boxu. Při neúspěchu spojení ukončí. | Z běžícího spojení přes cloud. |
| `hybrid` | Proxy používá cloud a po dosažení prahu chyb přejde na místní odpovědi. | Z běžícího spojení přes cloud nebo z místní komunikace při výpadku. |
| `offline` | Nové spojení boxu obsluhuje proxy místně. | Z rámců, které box posílá proxy. |
<!-- markdownlint-enable MD013 -->

## Online

Proxy otevře TCP spojení ke cloudu a předává komunikaci oběma směry.
Z přijatých rámců boxu publikuje data do MQTT. Pokud nepůjde navázat spojení
s cloudem, proxy zapíše chybu do protokolu a spojení boxu uzavře. Box může
pokus zopakovat při dalším připojení. Přímé přepnutí do offline režimu
při chybě je součástí režimu `hybrid`.

V běžícím spojení může proxy odeslat místní zápis po ověřeném dotazu boxu
`IsNewSet`, `IsNewFW` nebo `IsNewWeather`. Vybraný dotaz použije k zápisu
na tomtéž spojení. Zbylé rámce předá cloudu. Při probíhající změně z
cloudu místní požadavek čeká na další vhodný dotaz. Ovládání vyžaduje
povolené zápisy a připravený profil boxu.

## Hybrid

Hybrid po startu zkouší cloud. Pokud připojení selže před výměnou dat,
proxy zachová spojení boxu a odpovídá místně. Pokud už výměna začala a
cloud se odpojí, proxy rozpracované spojení ukončí bez opakování dat.
V protokolu uvede důvod ukončení. Selhání před první platnou odpovědí
se počítá do `hybrid_fail_threshold`. Po dosažení prahu přejde proxy
do offline režimu. Výchozí práh aplikace je jedna chyba.

Po době `hybrid_retry_interval` zkusí proxy cloud při příštím připojení
boxu. Výchozí interval je 60 sekund. Než zkouška skončí, obslouží další
spojení místně. Na první platnou odpověď zkouška čeká nejvýše
`cloud_ack_timeout`. Platný rámec z cloudu předaný boxu potvrdí návrat
do online provozu. Proxy uzavře nečinná místní spojení a rozpracované
lokální zápisy nechá doběhnout. Když pokus selže, prodleva začne znovu.

```yaml
proxy_mode: hybrid
hybrid_fail_threshold: 1
hybrid_retry_interval: 60
```

Hybrid použijte při prvním ověření lokálního ovládání. Proxy potřebuje
vidět běžné rámce boxu, firmware a potvrzenou změnu z cloudu. Po přechodu
na místní odpovědi jsou místní příkazy dostupné po dobu, kdy profil zůstává
připravený. Aktuální stav ověření najdete v diagnostice.

## Offline

Proxy obsluhuje nová spojení boxu místně. Naměřené hodnoty může v HA dál
zobrazovat, pokud box posílá data a funguje MQTT. Změny z cloudu se v tomto
režimu přes proxy nedoručují. Pro další místní zápisy musí profil odpovídat
aktuálnímu boxu a firmwaru. Po startu s dosud neověřeným profilem je potřeba
nejprve navázat komunikaci s cloudem v režimu `online` nebo `hybrid`.

Při místním zápisu proxy čeká na vhodný `IsNewSet`, `IsNewFW` nebo
`IsNewWeather` a odpoví rámcem `Setting`.
Po odpovídajícím `ACK` na stejném spojení pošle `END` s časem a spojení
ukončí. Samotné `ACK` potvrzuje přijetí rámce. Použitou hodnotu ověřte v HA
ze zprávy boxu. U `NACK` nebo vypršení lhůty spojení skončí a stav ověřte
před dalším pokusem.

<!-- markdownlint-disable MD013 -->
| Příchozí rámec v offline režimu | Běžná místní odpověď |
| --- | --- |
| `IsNewSet`, `IsNewFW` nebo `IsNewWeather` s připraveným příkazem a vhodným dotazem | `Setting`, po potvrzení boxem `END` s časem. |
| `IsNewSet`, `IsNewFW` nebo `IsNewWeather` bez vhodného příkazu, běžný `END` | `END` s časem a `GetActual`. |
| `tbl_actual` | `ACK` s požadavkem `GetActual`. |
| Ostatní `tbl_*` | `ACK`. |
<!-- markdownlint-enable MD013 -->

## Přepnutí v Home Assistantu

V **Nastavení → Zařízení a služby → Entity** otevřete zařízení
**Diagnostika OIG (oig_proxy)** pod integrací **MQTT**. Ovladač
**Proxy - Režim** (ID končí na `proxy_control_proxy_mode_cfg`) vybírá
`online`, `hybrid` nebo `offline`. Ovladač **Režim** u OIG Boxu nastavuje
Home 1, Home 2, Home 3, Home UPS, Home 5 nebo Home 6.

Volba v ovladači proxy platí do restartu aplikace. Výchozí režim po restartu
změníte v nastavení aplikace pomocí `proxy_mode`. Přepnutí ovlivňuje nová
spojení boxu. Při návratu z `offline` do `hybrid` nebo `online` proxy ukončí
nečinná místní spojení, aby se box mohl připojit znovu. Rozpracovaný místní
zápis má čas na dokončení nebo vypršení lhůty. Po změně vyčkejte na další
spojení boxu a zkontrolujte nová data i protokol.

<!-- markdownlint-disable MD013 -->
| Název v HA | Co ukazuje |
| --- | --- |
| **Proxy - Režim**, **HB - Režim (nastavený)** | Zvolený režim proxy, včetně `hybrid` během místního provozu. |
| **HB - BOX připojen**, **HB - Režim runtime** | V RC3 vycházejí z příjmu rámce boxu během posledních 90 sekund. |
| **Datový tok - Stáří posledních dat** | Čas od posledního rámce boxu, jakmile proxy nějaký přijala. |
| **Lokální zápis – ověřený profil ver** a **Lokální zápis – důvod stavu profilu ver** | Zda má proxy ověřený profil pro místní zápis a jeho aktuální stav. |
| **Control - poslední výsledek** | Výsledek posledního pozorovaného příkazu. Použitou hodnotu ověřte ve stavové entitě. |
<!-- markdownlint-enable MD013 -->

Označení `online` v **HB - Režim runtime** může být vidět i při místní
komunikaci. Skutečné připojení a výměnu s cloudem ověřte v protokolu.
V RC3 jsou příznaky `cloud_online` a `cloud_session_connected` odvozené
od stavu MQTT. Počet `cloud_connects` ukazuje navázaná TCP spojení.

Po výpadku nebo změně režimu může HA chvíli ukazovat dříve potvrzené hodnoty.
Pro ověření návratu zkontrolujte čerstvá data boxu. Podrobnosti k nastavení
zápisů, potvrzení a návratu hodnot jsou v [návodu k lokálnímu ovládání](local-control.md).
