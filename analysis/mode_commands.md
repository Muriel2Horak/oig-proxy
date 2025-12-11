# MODE příkazy (replay) – 7.–8. 12. 2025

Zachycené příkazy cloud→box pro změnu `MODE` (úspěšně ACK, platné CRC). Vhodné pro lokální replay bez cloudu.

## Co bylo zachyceno

- MODE hodnoty: `0` = Standard (FMT/No Limit vypnuto), `3` = No Limit (FMT zapnuto).
- Box validuje CRC přes celý `<Frame>` (včetně `ver`, `NewValue`, timestampů). Jakákoliv změna → NACK s Reason=WC.
- Vzorek pochází z 7.–8. 12. 2025.

## Platné příkazy

### MODE = 0 (Standard) – 5 ks

| timestamp           | ID_Set     | ver   | CRC   |
|---------------------|------------|-------|-------|
| 2025-12-07 20:18:34 | 1765135114 | 10918 | 47999 |
| 2025-12-07 21:03:21 | 1765137801 | 29288 | 12898 |
| 2025-12-07 21:55:01 | 1765140901 | 57855 | 16543 |
| 2025-12-08 04:08:59 | 1765163339 | 05839 | 50194 |
| 2025-12-08 06:09:08 | 1765170548 | 18562 | 19797 |

### MODE = 3 (No Limit) – 4 ks

| timestamp           | ID_Set     | ver   | CRC   |
|---------------------|------------|-------|-------|
| 2025-12-07 20:41:21 | 1765136481 | 10712 | 16664 |
| 2025-12-07 21:40:03 | 1765140003 | 22023 | 27350 |
| 2025-12-07 22:25:21 | 1765142721 | 52968 | 45985 |
| 2025-12-08 05:09:04 | 1765166944 | 16492 | 56179 |

### Příklad frame (MODE=3)

```xml
<Frame><ID>13584179</ID><ID_Device>2206237016</ID_Device><ID_Set>1765136481</ID_Set><ID_SubD>0</ID_SubD><DT>07.12.2025 20:41:21</DT><NewValue>3</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-07 19:47:07</TSec><ver>10712</ver><CRC>16664</CRC></Frame>
```

## Jak replayovat

- Pošli zachycený `<Frame>` beze změny na box; musí projít proprietární CRC.
- Pokud potřebuješ přepnout jen jednou, stačí libovolný platný frame dané hodnoty.
- Pokud box deduplikuje `ID_Set`, použij jiný záznam (málo pravděpodobné, ale neověřeno).

## Komunikační flow (Setting)

1. Box → Cloud: `<Result>IsNewSet</Result>` + `Lat`, `ver`, `CRC` – dotaz na nové nastavení.
2. Cloud → Box: `<Frame>...</Frame>` s `TblItem=MODE`, `NewValue`, `ver`, `CRC` – vlastní příkaz.
3. Box → Cloud: `<Result>ACK</Result><Reason>Setting</Reason>` – potvrzení provedení.

## Pozor / otevřené otázky

- ❓ Dedup: kontroluje box `ID_Set` a ignoruje opakování?
- ❓ Čas: hlídá stáří `DT`/`TSec`?
- ❓ `ver`: musí být monotónně rostoucí nebo jen náhodné?
- Replay měň pouze, pokud akceptuješ riziko přepnutí režimu bez cloudové autorizace.

## CRC poznatky (ranní reverse)

- CRC je 5místné číslo (0–65535), pravděpodobně 16bit hodnota převáděná na decimal.
- Nepasuje na běžné CRC16 preset (IBM/ARC/CCITT/X25/MODBUS) ani CRC32; neshoduje se s MD5/SHA1.
- Spočítáno z celého `<Frame>` včetně `ver`, `NewValue`, `DT`, `TSec`, `ID_Set` – jediná změna → `Reason=WC` (Wrong CRC).
- `ver` i `ID_Set` mění CRC, takže nelze snadno generovat novou kombinaci bez znalosti algoritmu.
- Další brute-force/solver TBD; zatím je praktická cesta jen přes přesný replay zachyceného frame.

## Ranní CRC analýza – shrnutí

### Co jsme zjistili

- CRC je proprietární – není to žádný standardní CRC-16/32 preset.
- CRC závisí na celém obsahu frame včetně `NewValue`; `ver` je náhodné/session číslo (ovlivní CRC, ale není z něj přímo odvoditelné).
- Přesný replay zachyceného příkazu funguje (ACK); změna libovolné hodnoty → NACK s `Reason=WC`.

### Možné cesty vpřed

- Reverse engineering firmware boxu a hledání CRC funkce.
- MITM na cloudu: zachytit další validní příkaz (např. změna `BAT_CI` iniciovaná v appce) a porovnat.
- Bruteforce CRC (0–99999) pro známý frame s jednou změnou – náročné, ale možné na malém prostoru.

### Co případně zkusit

- Změnit `BAT_CI` v cloudové appce, zachytit platný příkaz a uložit pro další vzorek.
- Pokus o bruteforce CRC pro jednu konkrétní změnu (počítat s časem/počtem pokusů).
