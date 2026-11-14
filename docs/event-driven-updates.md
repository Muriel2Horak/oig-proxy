# Události boxu a potvrzený stav v Home Assistantu

Box odesílá události v tabulce `tbl_events`. Proxy zveřejňuje poslední přijatou
událost na `oig_local/oig_proxy/tbl_events/state`. Pole `Type`, `Confirm` a
`Content` jsou dostupná v integraci MQTT. Aktuální MQTT stav události
nenahrazuje historii všech rámců. Pro diagnostiku celého sledu slouží
protokol aplikace a volitelné `capture_payloads`.

Událost `Setting` může obsahovat konkrétní změnu, například
`Remotely : tbl_box_prms / MODE: [0]->[1]`. Pokud je vazba na zařízení,
parametr, hodnotu a čas průkazná, proxy ji může použít k publikování
potvrzené hodnoty. V tomto příkladu je `0` Home 1 a `1` Home 2. Událost
bez jednoznačné hodnoty zůstane v událostech a změna musí být ověřená
jiným důkazem, například čerstvou tabulkou parametrů.

Protokolové `ACK` potvrzuje přijetí příkazu. Výsledek konkrétního příkazu
znázorňuje **Control - poslední výsledek**. Atribut `evidence` rozlišuje typ
důkazu, pokud jej výsledek uvádí. Navazující automatizace má sledovat
potvrzenou hodnotu a při časovém limitu zkontrolovat výsledek a protokol.

- [Použití a příklady automatizací](v2/local-control.md)
- [MQTT a výsledky příkazů](v2/twin.md)
- [Diagnostika a mapování entit](v2/sensor_map.md)
