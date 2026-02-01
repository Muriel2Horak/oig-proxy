# Instalace OIG Proxy add-onu (Home Assistant)

Tento navod vychazi z dodanych screenshotu v `analysis/picture/`. Kroky jsou serazene podle casu.

Predpoklady:
- Home Assistant s povolenym Add-on Store
- Pristup k internetu
- MQTT broker (doplnime v prubehu)

Postup:

1) Nastaveni > Doplnky
- Titulek: Otevri Nastaveni a klikni na Doplnky.
- Screenshot: `analysis/picture/2025-12-27_19-35-02.snagx`

2) Obchod s doplnky
- Titulek: Otevri Obchod s doplnky.
- Screenshot: `analysis/picture/2025-12-27_19-36-02.snagx`

3) Repozitare
- Titulek: V menu (tri tecky) otevri Repozitare.
- Screenshot: `analysis/picture/2025-12-27_19-37-18.snagx`

4) Pridat repozitar
- Titulek: Pridej repo URL `https://github.com/Muriel2Horak/oig-proxy`.
- Screenshot: `analysis/picture/2025-12-27_19-38-34.snagx`

5) OIG Cloud Proxy je videt
- Titulek: OIG Cloud Proxy se objevi v Local add-ons.
- Screenshot: `analysis/picture/2025-12-27_19-39-24.snagx`

6) Vyhledat Mosquitto broker
- Titulek: V obchode vyhledej "mqtt" a vyber Mosquitto broker.
- Screenshot: `analysis/picture/2025-12-27_19-40-29.snagx`

7) Obnovit seznam
- Titulek: Menu > Vyhledat aktualizace (obnov seznam doplnku).
- Screenshot: `analysis/picture/2025-12-27_19-41-55.snagx`

8) Doplnky na prehledu
- Titulek: V prehledu doplnku uvidis Mosquitto broker a OIG Proxy.
- Screenshot: `analysis/picture/2025-12-27_19-44-29.snagx`

9) Otevrit Mosquitto broker
- Titulek: Klikni na Mosquitto broker.
- Screenshot: `analysis/picture/2025-12-27_19-48-22.snagx`

10) Nastavit MQTT login
- Titulek: V Nastaveni Mosquitto pridej uzivatele (napr. "oig").
- Screenshot: `analysis/picture/2025-12-27_19-48-50.snagx`

11) Otevrit OIG Proxy
- Titulek: Vrat se do Doplnku a otevri OIG Proxy.
- Screenshot: `analysis/picture/2025-12-27_19-50-59.snagx`

12) Nastavit MQTT pristup
- Titulek: V OIG Proxy nastav `mqtt_username` a `mqtt_password` (stejne jako v Mosquitto).
- Screenshot: `analysis/picture/2025-12-27_19-51-20.snagx`

13) Log level a prepinace
- Titulek: Nastav `log_level` a zapni `capture_payloads`, `capture_raw_bytes`, `control_mqtt_enabled`.
- Screenshot: `analysis/picture/2025-12-27_19-52-05.snagx`

14) Ulozit nastaveni
- Titulek: Uloz nastaveni (tlacitko Ulozit).
- Screenshot: `analysis/picture/2025-12-27_19-53-04.snagx`

15) Zarizeni a sluzby
- Titulek: Nastaveni > Zarizeni a sluzby.
- Screenshot: `analysis/picture/2025-12-27_20-12-54.snagx`

16) MQTT integrace
- Titulek: Otevri MQTT integraci a zkontroluj pripojeni.
- Screenshot: `analysis/picture/2025-12-27_20-13-26.snagx`

17) Kontrola prepinacu
- Titulek: Over, ze prepinace v OIG Proxy jsou zapnute.
- Screenshot: `analysis/picture/2025-12-27_20-18-00.snagx`

18) Sit a porty
- Titulek: Zkontroluj porty v sekci Sit (5710/tcp, 53/udp, 53/tcp).
- Screenshot: `analysis/picture/2025-12-27_20-18-24.snagx`

19) Volitelne parametry
- Titulek: Volitelne uprav `full_refresh_interval_hours`.
- Screenshot: `analysis/picture/2025-12-27_20-18-47.snagx`

Poznamky:
- Pokud uz mas Mosquitto broker, kroky 6-10 muzes preskocit.
- Nazvy a hodnoty v nastaveni OIG Proxy odpovidaji aktualni verzi add-onu.
