# Aktualizace a návrat OIG Proxy na Home Assistant OS

Pro novou instalaci postupujte podle [návodu k nasazení](DEPLOYMENT.md). Existující aplikaci aktualizujte v Home Assistantu přes Supervisor.

## Před aktualizací

1. Přečtěte [změny verze](CHANGELOG.md) a v **Nastavení → Aplikace → OIG Proxy** ověřte nainstalovanou a nabízenou verzi. U soukromě sestaveného obrazu zaznamenejte i jeho úplné ID a odpovídající zdrojový commit. Verze zobrazená v HA pochází z metadat Supervisoru.
2. V **Nastavení → Systém → Zálohy** vytvořte zálohu s aplikací OIG Proxy a jejími daty nebo při aktualizaci použijte volbu zálohy aplikace. Ověřte dokončení zálohy a uložte její kopii mimo hostitele společně s potřebným klíčem pro obnovu. Záloha může obsahovat heslo brokeru a záznamy komunikace. [Zálohy aplikací a obnova](https://www.home-assistant.io/common-tasks/general/backups/)
3. Pokud zkoušíte soukromý kandidát, uchovejte také přesný předchozí obraz mimo jeho běžný tag. Ověřte kontrolní součet archivu a ID obrazu. Při ručním zálohování SQLite použijte online zálohovací API SQLite nebo kopírujte data až po řízeném zastavení aplikace. Samotná kopie běžícího `payloads.db` může vynechat data z WAL.

## Aktualizace a ověření

1. Spusťte dostupnou aktualizaci z detailu aplikace nebo z **Nastavení → Systém → Aktualizace**. Životní cyklus aplikace ponechte Supervisoru. Soukromé kandidáty ověřujte z pevně identifikovaného obrazu. Skript `deploy_to_haos.sh` upravuje pracovní kopii Supervisoru a spouští nový build, takže jeho výstup nelze použít jako doklad nasazení ověřeného obrazu.
2. Na kartě **Protokol** ověřte start bez opakovaných pádů, připojení MQTT a nové rámce Boxu. Zkontrolujte, že Box přes očekávaný DNS a port komunikuje s proxy. V režimech s cloudovým spojením ověřte i průchod běžného provozu do cloudu.
3. Zkontrolujte nové MQTT hodnoty a dostupnost entit. Historický retained stav může být zobrazen i dříve, než po restartu přijde první živý rámec. Pro zkoušku zápisu nejprve počkejte na `control_ver_ready` a sledujte také potvrzenou hodnotu Boxu podle [návodu k ovládání](docs/v2/local-control.md#potvrzení-a-diagnostika).

## Návrat

Před obnovou pozastavte automatizace odesílající příkazy a uložte potřebnou diagnostiku. V **Nastavení → Systém → Zálohy** vyberte zálohu před aktualizací a obnovte OIG Proxy a její data. Obnovou dat se mohou ztratit novější záznamy z proxy.

U soukromého obrazu postupujte podle plánu návratu pro danou zkoušku. Zastavte aplikaci přes Supervisor, obnovte přesný předchozí obraz a spusťte ji přes Supervisor. Pokud předchozí verze potřebuje obnovu dat či konfigurace, použijte k tomu ověřenou zálohu a zastavenou aplikaci. Po spuštění ověřte identitu obrazu, připojení Boxu a nové zprávy MQTT.

Návrat aplikace nevrací hodnoty uložené v samotném Boxu. Nejprve přečtěte aktuální stav ze zařízení. Pokud výsledek předchozího příkazu není jasný, ověřte ho před dalším zápisem.
