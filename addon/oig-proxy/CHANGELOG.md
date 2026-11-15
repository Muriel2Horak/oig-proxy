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
