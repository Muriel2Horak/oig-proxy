# OIG Proxy - Kompletní návrh pojmenování senzorů

## Konvence pojmenování
- Formát: `[Kategorie] - [Co měří] [Upřesnění]`
- Kategorie určuje zařazení do HA zařízení
- `entity_category: diagnostic` pro konfigurační/systémové hodnoty

---

## 1. BATERIE (device: OIG Baterie)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| BAT_C | Baterie - Stav nabití | % | battery | measurement | - |
| BAT_V | Baterie - Napětí | V | voltage | measurement | - |
| BAT_I | Baterie - Proud | A | current | measurement | - |
| BAT_P | Baterie - Výkon | W | power | measurement | - |
| BAT_T | Baterie - Teplota | °C | temperature | measurement | - |
| BAT_Q | Baterie - Kvalita (SoH) | % | - | measurement | - |
| BAT_N | Baterie - Počet článků | - | - | - | diagnostic |
| BAT_APD | Baterie - Energie nabito dnes | Wh | energy | total_increasing | - |
| BAT_AND | Baterie - Energie vybito dnes | Wh | energy | total_increasing | - |
| BAT_MIN | Baterie - Minimum pro vybíjení | % | battery | - | config |
| BAT_GL_MIN | Baterie - Záložní minimum | % | battery | - | config |
| BAT_AG_MIN | Baterie - Minimum pro agregát | % | battery | - | config |
| BAT_HDO | Baterie - Nabíjení v HDO | - | - | - | config |
| BAT_AA | Baterie - Nabíjení ze sítě výkon | W | power | measurement | - |
| BAT_AD | Baterie - Ze sítě energie dnes | Wh | energy | total_increasing | - |
| BAT_AM | Baterie - Ze sítě energie měsíc | kWh | energy | total_increasing | - |
| BAT_AY | Baterie - Ze sítě energie rok | MWh | energy | total_increasing | - |
| BAT_AC | Baterie - Dobití AC cíl | % | battery | - | config |
| BAT_CI | Baterie - Max. nabíjecí proud | A | current | - | config |
| BAT_CU | Baterie - Max. nabíjecí napětí | V | voltage | - | config |
| BAT_FORMAT | Baterie - Formátování povoleno | - | - | - | config |
| BAT_CH_HI | Baterie - Horní mez nabíjení | % | - | - | config |
| BAT_DI | Baterie - Vybíjení povoleno | - | - | - | config |
| BAT_DI_HI | Baterie - Horní mez vybíjení | % | - | - | config |
| P_BAT | Baterie - Instalovaná kapacita | Wh | energy | - | diagnostic |
| BAL_ON | Baterie - Balancování aktivní | - | - | - | diagnostic |
| FMT_ON | Baterie - Formátování aktivní | - | - | - | diagnostic |
| FMT_PROGRESS | Baterie - Formátování progress | % | - | measurement | diagnostic |
| LO_DAY | Baterie - Dní do balancování | - | - | - | diagnostic |
| LO_DAY_MAX | Baterie - Max dní balancování | - | - | - | config |
| COMBATT | Baterie - Stav komunikace | - | - | - | diagnostic |
| BTR_TYPE | Baterie - Typ (Li-Fe) | - | - | - | diagnostic |
| EBATMIN | Auto ECO - Baterie minimum | % | battery | - | config |
| EBATREC | Auto ECO - Baterie obnovení | % | battery | - | config |
| CHARGE | Baterie - DC nabíjení aktivní | - | - | - | - |
| CHARGE_AC | Baterie - AC nabíjení aktivní | - | - | - | - |
| A_CHRG | Nabíječka - Ruční proud | A | current | - | config |
| A_MAX_CHRG | Nabíječka - Max. proud | A | current | - | config |
| AAC_MAX_CHRG | Nabíječka - Max. AC proud | A | current | - | config |
| A_MAX_DIS_HYB | Baterie - Max. vybíjecí proud hybrid | A | current | - | config |
| V_CHRG | Nabíječka - Bulk napětí | V | voltage | - | config |
| V_CHAR_FLO | Nabíječka - Float napětí | V | voltage | - | config |
| CHRG_T | Nabíječka - Čas nabíjení | - | - | - | config |
| CHRG_X | Nabíječka - Float proud limit | A | current | - | config |
| CHRG_Y | Nabíječka - Vypnutí napětí | V | voltage | - | config |

---

## 2. FVE / SOLÁRNÍ PANELY (device: OIG FVE)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| P_FVE | FVE - Celkový výkon | W | power | measurement | - |
| FV_P1 | FVE - Výkon string 1 | W | power | measurement | - |
| FV_P2 | FVE - Výkon string 2 | W | power | measurement | - |
| FV_V1 | FVE - Napětí string 1 | V | voltage | measurement | - |
| FV_V2 | FVE - Napětí string 2 | V | voltage | measurement | - |
| FV_I1 | FVE - Proud string 1 | A | current | measurement | - |
| FV_I2 | FVE - Proud string 2 | A | current | measurement | - |
| FV_PROC | FVE - Využití výkonu | % | - | measurement | - |
| FV_AD | FVE - Výroba dnes | Wh | energy | total_increasing | - |
| FV_AM | FVE - Výroba měsíc | kWh | energy | total_increasing | - |
| FV_AY | FVE - Výroba rok | MWh | energy | total_increasing | - |
| PVMIN | FVE - Min. úroveň pro HOME | W | power | - | config |
| PVMIN0 | FVE - Min. úroveň nulová | W | power | - | config |
| V_MIN_PV | FVE - Min. vstupní napětí | V | voltage | - | config |
| V_MAX_PV | FVE - Max. vstupní napětí | V | voltage | - | config |
| V_MIN_MPP | FVE - Min. MPP napětí | V | voltage | - | config |
| V_MAX_MPP | FVE - Max. MPP napětí | V | voltage | - | config |
| FV1 | FVE - Jistič string 1 | - | - | - | diagnostic |
| FV2 | FVE - Jistič string 2 | - | - | - | diagnostic |
| ERR_PV | FVE - Stav/chyby | - | - | - | diagnostic |
| ERR_PV_warnings | FVE - Varování | - | - | - | diagnostic |

---

## 3. SÍŤ / GRID (device: OIG Síť)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| P_GRID | Síť - Výkon (+odběr/-dodávka) | W | power | measurement | - |
| ACI_VR | Síť - Napětí L1 | V | voltage | measurement | - |
| ACI_VS | Síť - Napětí L2 | V | voltage | measurement | - |
| ACI_VT | Síť - Napětí L3 | V | voltage | measurement | - |
| ACI_WR | Síť - Výkon L1 | W | power | measurement | - |
| ACI_WS | Síť - Výkon L2 | W | power | measurement | - |
| ACI_WT | Síť - Výkon L3 | W | power | measurement | - |
| ACI_F | Síť - Frekvence | Hz | frequency | measurement | - |
| TO_GRID | Síť - Dodávka aktivní | - | - | - | - |
| AC_AD | Síť - Energie AC dnes | Wh | energy | total_increasing | - |
| AC_AM | Síť - Energie AC měsíc | kWh | energy | total_increasing | - |
| AC_AY | Síť - Energie AC rok | MWh | energy | total_increasing | - |
| AC_PD | Síť - Dodávka dnes | Wh | energy | total_increasing | - |
| AC_PM | Síť - Dodávka měsíc | kWh | energy | total_increasing | - |
| AC_PY | Síť - Dodávka rok | MWh | energy | total_increasing | - |
| HDO | Síť - HDO signál aktivní | - | - | - | - |
| HDO1_S | Síť - HDO pásmo 1 začátek | - | - | - | config |
| HDO1_E | Síť - HDO pásmo 1 konec | - | - | - | config |
| HDO2_S | Síť - HDO pásmo 2 začátek | - | - | - | config |
| HDO2_E | Síť - HDO pásmo 2 konec | - | - | - | config |
| HGVI_S | Síť - HGVI začátek | - | - | - | config |
| HGVI_E | Síť - HGVI konec | - | - | - | config |
| V_NOMIN | Síť - Nominální napětí | V | voltage | - | config |
| F_NOMIN | Síť - Nominální frekvence | Hz | frequency | - | config |
| V_MIN_AC | Síť - Min. připojovací napětí | V | voltage | - | config |
| V_MAX_AC | Síť - Max. připojovací napětí | V | voltage | - | config |
| F_MIN_AC | Síť - Min. připojovací frekvence | Hz | frequency | - | config |
| F_MAX_AC | Síť - Max. připojovací frekvence | Hz | frequency | - | config |
| V_MAX_GRID_AV | Síť - Max. průměrné napětí | V | voltage | - | config |
| P_MAX_FEED_GRID | Síť - Max. výkon do sítě | W | power | - | config |
| PF_FEED | Síť - Účiník dodávky | - | power_factor | - | config |
| PF_MIN_100 | Síť - Min. účiník při 100% | - | power_factor | - | config |
| V_CUT_GRID | Síť - Odpojení při dostupné síti | V | voltage | - | config |
| V_RE_GRID | Síť - Opět. připojení při síti | V | voltage | - | config |
| V_CUT_NOGRID | Síť - Odpojení bez sítě | V | voltage | - | config |
| V_RE_NOGRID | Síť - Opět. připojení bez sítě | V | voltage | - | config |
| T_ON_GRID | Síť - Čekání před připojením | s | duration | - | config |
| IN_AC_WIDE | Síť - Široký rozsah vstupu | - | - | - | config |
| GEN_AC_SRC | Síť - Generátor jako zdroj | - | - | - | config |
| OVERFDER | Síť - Derating přepětí | - | - | - | config |
| OVERVDER | Síť - Derating překmitočet | - | - | - | config |
| GRID_PV_ON | Síť - Vybíjení bat. s FVE | - | - | - | config |
| GRID_PV_OFF | Síť - Vybíjení bat. bez FVE | - | - | - | config |
| A_MAX | Síť - Velikost jističe | A | current | - | config |
| ERR_GRID | Síť - Chyby | - | - | - | diagnostic |

---

## 4. SPOTŘEBA / ZÁTĚŽ (device: OIG Spotřeba)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| P_LOAD | Spotřeba - Celkový výkon | W | power | measurement | - |
| ACO_P | Spotřeba - Výkon domu | W | power | measurement | - |
| ACO_VR | Spotřeba - Napětí L1 | V | voltage | measurement | - |
| ACO_VS | Spotřeba - Napětí L2 | V | voltage | measurement | - |
| ACO_VT | Spotřeba - Napětí L3 | V | voltage | measurement | - |
| ACO_PR | Spotřeba - Výkon L1 | W | power | measurement | - |
| ACO_PS | Spotřeba - Výkon L2 | W | power | measurement | - |
| ACO_PT | Spotřeba - Výkon L3 | W | power | measurement | - |
| ACO_PAR | Spotřeba - Zdánlivý výkon L1 | VA | apparent_power | measurement | - |
| ACO_PAS | Spotřeba - Zdánlivý výkon L2 | VA | apparent_power | measurement | - |
| ACO_PAT | Spotřeba - Zdánlivý výkon L3 | VA | apparent_power | measurement | - |
| ACO_PA | Spotřeba - Zdánlivý výkon celk. | VA | apparent_power | measurement | - |
| ACO_F | Spotřeba - Frekvence výstupu | Hz | frequency | measurement | - |
| EN_DAY | Spotřeba - Odběr dnes | Wh | energy | total_increasing | - |
| EN_MONT | Spotřeba - Odběr měsíc | kWh | energy | total_increasing | - |
| EN_YEAR | Spotřeba - Odběr rok | MWh | energy | total_increasing | - |
| MSC_SELF | Spotřeba - Soběstačnost | % | - | measurement | - |
| ENLOADS | Spotřeba - Zátěž povolena | - | - | - | config |
| LOAD_PV_ON | Spotřeba - Vybíjení s FVE | - | - | - | config |
| LOAD_PV_OFF | Spotřeba - Vybíjení bez FVE | - | - | - | config |
| ACOUPLMT | Spotřeba - AC coupling limit | - | - | - | config |
| BYPASS | Spotřeba - Automatický bypass | - | - | - | - |
| BYPASS_M | Spotřeba - Manuální bypass | - | - | - | config |
| PRLL_OUT | Spotřeba - Paralelní výstup | - | - | - | config |
| NLINEO | Spotřeba - Uzemnění N v bat. režimu | - | - | - | config |

---

## 5. BOJLER (device: OIG Bojler)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| P | Bojler - Aktuální výkon | W | power | measurement | - |
| W | Bojler - Instalovaný výkon | W | power | - | config |
| WD | Bojler - Energie pro HDO | Wh | energy | - | config |
| WFIX | Bojler - Fixní výkon | W | power | - | config |
| BOJ_AD | Bojler - Energie dnes | Wh | energy | total_increasing | - |
| BOJ_AM | Bojler - Energie měsíc | kWh | energy | total_increasing | - |
| BOJ_AY | Bojler - Energie rok | MWh | energy | total_increasing | - |
| MANUAL | Bojler - Manuální režim | - | - | - | config |
| TERMOSTAT | Bojler - Termostat aktivní | - | - | - | - |
| OFFSET | Bojler - Offset přetoků | W | power | - | config |
| SSR0 | Bojler - SSR relé 1 | - | - | - | - |
| SSR1 | Bojler - SSR relé 2 | - | - | - | - |
| SSR2 | Bojler - SSR relé 3 | - | - | - | - |

---

## 6. WALLBOX / EV NABÍJEČKA (device: OIG Wallbox)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| CHARGING | Wallbox - Nabíjení aktivní | - | - | - | - |
| MODE | Wallbox - Režim | - | - | - | config |
| MODE1 | Wallbox - Režim CBB | - | - | - | config |
| ACT_P_L1 | Wallbox - Výkon L1 | W | power | measurement | - |
| ACT_P_L2 | Wallbox - Výkon L2 | W | power | measurement | - |
| ACT_P_L3 | Wallbox - Výkon L3 | W | power | measurement | - |
| ACT_U_L1 | Wallbox - Napětí L1 | V | voltage | measurement | - |
| ACT_U_L2 | Wallbox - Napětí L2 | V | voltage | measurement | - |
| ACT_U_L3 | Wallbox - Napětí L3 | V | voltage | measurement | - |
| ACT_A_L1 | Wallbox - Proud L1 | A | current | measurement | - |
| ACT_A_L2 | Wallbox - Proud L2 | A | current | measurement | - |
| ACT_A_L3 | Wallbox - Proud L3 | A | current | measurement | - |
| ETOCAR | Wallbox - Nabito do auta | % | - | measurement | - |
| ETOCAR_D | Wallbox - Energie dnes | Wh | energy | total_increasing | - |
| ETOCAR_M | Wallbox - Energie měsíc | kWh | energy | total_increasing | - |
| ETOCAR_Y | Wallbox - Energie rok | MWh | energy | total_increasing | - |
| ETOCAR_PVB_D | Wallbox - Z FVE/bat. dnes | Wh | energy | total_increasing | - |
| ETOCAR_PVB_M | Wallbox - Z FVE/bat. měsíc | kWh | energy | total_increasing | - |
| ETOCAR_PVB_Y | Wallbox - Z FVE/bat. rok | MWh | energy | total_increasing | - |
| ETOCAR_G_D | Wallbox - Ze sítě dnes | Wh | energy | total_increasing | - |
| ETOCAR_G_M | Wallbox - Ze sítě měsíc | kWh | energy | total_increasing | - |
| ETOCAR_G_Y | Wallbox - Ze sítě rok | MWh | energy | total_increasing | - |
| CARACUSIZE | Wallbox - Kapacita baterie auta | Wh | energy | - | config |
| CARCHARGE | Wallbox - Nabíjení auta | - | - | - | - |
| CONSUM_EL | Wallbox - Spotřeba EV/100km | kWh | energy | - | config |
| CONSUM_LIQ | Wallbox - Spotřeba spalovací/100km | L | - | - | config |
| PRICE_ET1 | Wallbox - Cena el. tarif 1 | Kč/kWh | - | - | config |
| PRICE_ET2 | Wallbox - Cena el. tarif 2 | Kč/kWh | - | - | config |
| PRICE_LIQ | Wallbox - Cena paliva | Kč/l | - | - | config |
| PHSS | Wallbox - Počet fází | - | - | - | config |
| IDENT | Wallbox - ID nabíječky | - | - | - | diagnostic |
| EPOWMAX | Wallbox - ECO max. odběr | W | power | - | config |
| EREGREP | Wallbox - ECO opakování reg. | s | - | - | config |
| EREGSNS | Wallbox - ECO citlivost reg. | A | current | - | config |

---

## 7. STŘÍDAČ / INVERTOR (device: OIG Střídač)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| T_INN | Střídač - Teplota | °C | temperature | measurement | - |
| FAN1 | Střídač - Teplota ventilátor 1 | °C | temperature | measurement | - |
| FAN2 | Střídač - Teplota ventilátor 2 | °C | temperature | measurement | - |
| Temp | Střídač - Teplota v boxu | °C | temperature | measurement | - |
| Humid | Střídač - Vlhkost v boxu | % | humidity | measurement | - |
| ISON | Střídač - Zapnuto | - | - | - | - |
| S_STOP_ISON | Střídač - Centrální stop | - | - | - | config |
| STAT | Střídač - Status | - | - | - | diagnostic |
| STATUS | Střídač - Stav | - | - | - | diagnostic |
| PRRTY | Střídač - Priorita toku | - | - | - | config |
| P_SET | Střídač - Nastavený výkon | W | power | - | config |
| MODEL | Střídač - Model | - | - | - | diagnostic |
| TYP | Střídač - Typ | - | - | - | diagnostic |
| TYP_ | Střídač - Typ a zapojení | - | - | - | diagnostic |
| SERNO | Střídač - Sériové číslo | - | - | - | diagnostic |
| SW | Střídač - Verze SW | - | - | - | diagnostic |
| Fw | Střídač - Verze FW | - | - | - | diagnostic |
| HWID | Střídač - HW identifikátor | - | - | - | diagnostic |
| LCD_BRIGH | Střídač - Jas LCD | % | - | - | config |
| LED_BRIGH | Střídač - Jas LED | % | - | - | config |
| BUZ_MUT | Střídač - Ztlumit bzučák | - | - | - | config |
| BUZ_MUT_STNB | Střídač - Ztlumit v standby | - | - | - | config |
| AL_MUT_BAT | Střídač - Ztlumit alarm bat. | - | - | - | config |
| SCNT | Střídač - Počet startů | - | - | measurement | diagnostic |
| ACCON | Střídač - AC coupling zapnut | - | - | - | config |
| P_ADJ_ENBL | Střídač - Auto-adjust povolen | - | - | - | config |
| P_ADJ_STRT | Střídač - Auto-adjust start | % | - | - | config |
| AUTQREA | Střídač - Auto jalový výkon | - | - | - | config |
| ONGRQREA | Střídač - Jalový výkon on-grid | - | - | - | config |
| COMINVERT | Střídač - Stav komunikace | - | - | - | diagnostic |
| ERR_AC | Střídač - Chyby AC | - | - | - | diagnostic |
| ERR_ELSE | Střídač - Ostatní chyby | - | - | - | diagnostic |
| CODE_ERR | Střídač - Kód chyby | - | - | - | diagnostic |

---

## 8. JISTIČE / OCHRANA (device: OIG Střídač)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| FAIN | Jistič - Vstup síť | - | - | - | diagnostic |
| FAOUT | Jistič - Výstup zátěž | - | - | - | diagnostic |
| FAT | Jistič - Zátěž | - | - | - | diagnostic |
| FAT2 | Jistič - Nezáloha | - | - | - | diagnostic |
| FA2 | Jistič - Rezerva 2 | - | - | - | diagnostic |
| FADC1 | Jistič - DC 1 | - | - | - | diagnostic |
| FADC2 | Jistič - DC 2 | - | - | - | diagnostic |
| FADC3 | Jistič - DC 3 | - | - | - | diagnostic |
| CRCT | Jistič - Krizové ovládání HDO | - | - | - | diagnostic |
| CRCTE | Jistič - Krizové ovládání povoleno | - | - | - | config |

---

## 9. KOMUNIKACE (device: OIG Střídač)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| COMOK | Komunikace - Stav | - | - | - | diagnostic |
| COMBATT | Komunikace - Baterie | - | - | - | diagnostic |
| COMINVERT | Komunikace - Invertor | - | - | - | diagnostic |
| COMELMER | Komunikace - Elektroměr | - | - | - | diagnostic |
| COMEXPANZ | Komunikace - Expanzní deska | - | - | - | diagnostic |
| COMMPP | Komunikace - MPPT regulátor | - | - | - | diagnostic |
| STRNGHT | Komunikace - Síla Wi-Fi | dBm | signal_strength | measurement | diagnostic |
| SSID | Komunikace - Wi-Fi SSID | - | - | - | diagnostic |
| IP | Komunikace - IP adresa | - | - | - | diagnostic |
| PORT | Komunikace - Port | - | - | - | diagnostic |
| ADDR | Komunikace - Nastavení IP | - | - | - | config |
| DOMAIN | Komunikace - Doména | - | - | - | diagnostic |

---

## 10. ČASOVÁ PÁSMA (device: OIG Střídač)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| ZONE1_S | Časové pásmo 1 - Začátek | - | - | - | config |
| ZONE1_E | Časové pásmo 1 - Konec | - | - | - | config |
| ZONE2_S | Časové pásmo 2 - Začátek | - | - | - | config |
| ZONE2_E | Časové pásmo 2 - Konec | - | - | - | config |
| ZONE3_S | Časové pásmo 3 - Začátek | - | - | - | config |
| ZONE3_E | Časové pásmo 3 - Konec | - | - | - | config |
| ZONE4_S | Časové pásmo 4 - Začátek | - | - | - | config |
| ZONE4_E | Časové pásmo 4 - Konec | - | - | - | config |

---

## 11. KALIBRACE / REGULACE (device: OIG Střídač)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| P_CAL_R | Kalibrace - Výkon L1 | - | - | - | config |
| P_CAL_S | Kalibrace - Výkon L2 | - | - | - | config |
| P_CAL_T | Kalibrace - Výkon L3 | - | - | - | config |
| KON_L1 | Kalibrace - Konstanta L1 | - | - | - | config |
| KON_L2 | Kalibrace - Konstanta L2 | - | - | - | config |
| KON_L3 | Kalibrace - Konstanta L3 | - | - | - | config |
| POS_L1 | Kalibrace - Posun L1 | - | - | - | config |
| POS_L2 | Kalibrace - Posun L2 | - | - | - | config |
| POS_L3 | Kalibrace - Posun L3 | - | - | - | config |
| NEC_L1 | Kalibrace - Necitlivost L1 | - | - | - | config |
| NEC_L2 | Kalibrace - Necitlivost L2 | - | - | - | config |
| NEC_L3 | Kalibrace - Necitlivost L3 | - | - | - | config |
| CALA | Kalibrace - Parametr A | - | - | - | config |
| OFFSET | Kalibrace - Offset | - | - | - | config |
| PRUM | Kalibrace - Průměrování | - | - | - | config |
| OPAK_REG | Kalibrace - Opakování regulace | - | - | - | config |
| STRM_REG | Kalibrace - Strmost regulace | - | - | - | config |
| SMER_PROUDU | Kalibrace - Směr proudu | - | - | - | config |
| SKOS | Kalibrace - Skos | - | - | - | config |

---

## 12. METADATA / IDENTIFIKACE (device: OIG Střídač, entity_category: diagnostic)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| ID | Systém - ID záznamu | - | - | - | diagnostic |
| id | Systém - ID záznamu | - | - | - | diagnostic |
| ID_Device | Systém - ID zařízení | - | - | - | diagnostic |
| ID_Set | Systém - ID sady dat | - | - | - | diagnostic |
| ID_Server | Systém - ID serveru | - | - | - | diagnostic |
| ID_Location | Systém - ID umístění | - | - | - | diagnostic |
| ID_SubD | Systém - ID sub-device | - | - | - | diagnostic |
| DT | Systém - Datum čas | - | timestamp | - | diagnostic |
| DateTime | Systém - Datum a čas | - | timestamp | - | diagnostic |
| UTC | Systém - UTC čas | - | - | - | diagnostic |
| Tmr | Systém - Timer | - | - | - | diagnostic |
| Rdt | Systém - Čas dat | - | timestamp | - | diagnostic |
| TMLASTCALL | Systém - Min. od poslání dat | min | - | measurement | diagnostic |
| Name | Systém - Název | - | - | - | diagnostic |
| name | Systém - Název | - | - | - | diagnostic |
| Geobaseid | Systém - Geo ID | - | - | - | diagnostic |
| country | Systém - Země | - | - | - | diagnostic |
| lat | Systém - Zeměpisná šířka | ° | - | - | diagnostic |
| lon | Systém - Zeměpisná délka | ° | - | - | diagnostic |
| Lat | Systém - Latence | ms | - | measurement | diagnostic |
| YRplace | Systém - Místo YR | - | - | - | diagnostic |
| VIZ | Systém - Vizualizace | - | - | - | diagnostic |
| Content | Systém - Obsah hlášky | - | - | - | diagnostic |
| Confirm | Systém - Potvrzení | - | - | - | diagnostic |
| Type | Systém - Typ zařízení | - | - | - | diagnostic |
| Status | Systém - OK/BAN | - | - | - | diagnostic |
| Result | Systém - Výsledek | - | - | - | diagnostic |
| Record | Systém - Záznam | - | - | - | diagnostic |
| NewValue | Systém - Nová hodnota | - | - | - | diagnostic |
| TblName | Systém - Tabulka | - | - | - | diagnostic |
| TblItem | Systém - Položka | - | - | - | diagnostic |
| Up | Systém - Uptime | - | - | - | diagnostic |
| EX | Systém - Expanze | - | - | - | diagnostic |
| NB | Systém - Měření nezálohy | - | - | - | config |
| ENBL | Systém - Povoleno | - | - | - | config |
| RE | Systém - RE | - | - | - | diagnostic |
| RA | Systém - Aktivace FW update | - | - | - | config |
| SA | Systém - Okamžité odeslání dat | - | - | - | config |
| RQRESET | Systém - Požadavek reset LCD | - | - | - | config |
| RQRESEE | Systém - Požadavek reset EEPROM | - | - | - | config |

---

## 13. FIRMWARE / AKTUALIZACE (device: OIG Střídač)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| FW_Base | Firmware - Základ | - | - | - | diagnostic |
| FW_LastVersion | Firmware - Poslední verze | - | - | - | diagnostic |
| FW_LastFile | Firmware - Poslední soubor | - | - | - | diagnostic |
| FW_UpdateEnabled | Firmware - Aktualizace povolena | - | - | - | config |
| ResetEnabled | Firmware - Reset po updatu | - | - | - | config |
| Testing | Firmware - Testovací režim | - | - | - | config |
| SettingEnabled | Firmware - Nastavení povoleno | - | - | - | config |
| ReadOutEnabled | Firmware - Čtení povoleno | - | - | - | config |
| LastIP | Firmware - Poslední IP | - | - | - | diagnostic |
| LastCall | Firmware - Poslední volání | - | timestamp | - | diagnostic |
| LastUpdate | Firmware - Poslední update | - | timestamp | - | diagnostic |
| LastSet | Firmware - Poslední nastavení | - | timestamp | - | diagnostic |
| LastWeather | Firmware - Poslední počasí | - | timestamp | - | diagnostic |
| UserLastOn | Firmware - Uživatel online | - | timestamp | - | diagnostic |
| LoadedOn | Firmware - Nahráno | - | timestamp | - | diagnostic |
| NextUpdate | Firmware - Další update | - | timestamp | - | diagnostic |

---

## 14. CHYBY (device: OIG Střídač)

| Klíč | Nový název (name_cs) | Jednotka | device_class | state_class | entity_category |
|------|---------------------|----------|--------------|-------------|-----------------|
| ERR_PV | Chyba - FVE | - | - | - | diagnostic |
| ERR_PV_warnings | Chyba - FVE varování | - | - | - | diagnostic |
| ERR_BATT | Chyba - Baterie | - | - | - | diagnostic |
| ERR_GRID | Chyba - Síť | - | - | - | diagnostic |
| ERR_AC | Chyba - AC | - | - | - | diagnostic |
| ERR_ELSE | Chyba - Ostatní | - | - | - | diagnostic |
| CODE_ERR | Chyba - Kód invertoru | - | - | - | diagnostic |

---

## SOUHRN

| Kategorie | Device v HA | Počet senzorů |
|-----------|-------------|---------------|
| Baterie | OIG Baterie | ~45 |
| FVE | OIG FVE | ~21 |
| Síť | OIG Síť | ~42 |
| Spotřeba | OIG Spotřeba | ~27 |
| Bojler | OIG Bojler | ~13 |
| Wallbox | OIG Wallbox | ~33 |
| Střídač | OIG Střídač | ~30 |
| Jističe | OIG Střídač | ~10 |
| Komunikace | OIG Střídač | ~12 |
| Časová pásma | OIG Střídač | ~8 |
| Kalibrace | OIG Střídač | ~18 |
| Metadata | OIG Střídač | ~36 |
| Firmware | OIG Střídač | ~15 |
| Chyby | OIG Střídač | ~7 |
| **CELKEM** | **7 devices** | **~319** |

---

## ROZDĚLENÍ DO HA DEVICES

Pro lepší organizaci v Home Assistant doporučuji vytvořit **7 samostatných MQTT zařízení**:

1. **OIG Baterie** - vše kolem úložiště energie
2. **OIG FVE** - fotovoltaické panely a MPPT
3. **OIG Síť** - připojení k distribuční síti
4. **OIG Spotřeba** - odběr domu a zátěže
5. **OIG Bojler** - řízení ohřevu vody
6. **OIG Wallbox** - nabíječka elektromobilu
7. **OIG Střídač** - hlavní jednotka + systémové věci

Použití `entity_category`:
- **diagnostic** - technické/ladící hodnoty (ID, chyby, komunikace, FW)
- **config** - konfigurační parametry (limity, pásma, kalibrace)
- **(prázdné)** - hlavní měřené hodnoty viditelné v UI
