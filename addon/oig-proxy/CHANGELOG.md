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
