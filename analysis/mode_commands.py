#!/usr/bin/env python3
"""
Validní příkazy pro změnu MODE - zachyceno z reálné komunikace cloud→box
=======================================================================

Tyto příkazy mají platné CRC a byly úspěšně přijaty boxem (ACK).
Lze je použít pro replay attack - lokální změnu MODE bez cloudu.

MODE hodnoty:
- MODE=0: Standard (FMT/No Limit vypnuto)
- MODE=3: No Limit (FMT aktivní)

Použití:
- Replay celého příkazu na box pro změnu MODE
- Box validuje CRC - modifikace jakékoliv hodnoty způsobí NACK

Omezení (k ověření):
- ID_Set může být jednoúčelové (box si pamatuje zpracované)
- DT/TSec časová razítka - možná kontrola stáří
- ver - možná musí být novější než předchozí

Zachyceno: 7.-8. prosince 2025
"""

# MODE=0 (vypnout FMT/No Limit) - 5 validních příkazů
MODE_0_COMMANDS = [
    {
        "timestamp": "2025-12-07 20:18:34",
        "id_set": "1765135114",
        "ver": "10918",
        "crc": "47999",
        "frame": '<Frame><ID>13584151</ID><ID_Device>2206237016</ID_Device><ID_Set>1765135114</ID_Set><ID_SubD>0</ID_SubD><DT>07.12.2025 20:18:34</DT><NewValue>0</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-07 19:46:54</TSec><ver>10918</ver><CRC>47999</CRC></Frame>',
    },
    {
        "timestamp": "2025-12-07 21:03:21",
        "id_set": "1765137801",
        "ver": "29288",
        "crc": "12898",
        "frame": '<Frame><ID>13584215</ID><ID_Device>2206237016</ID_Device><ID_Set>1765137801</ID_Set><ID_SubD>0</ID_SubD><DT>07.12.2025 21:03:21</DT><NewValue>0</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-07 20:09:28</TSec><ver>29288</ver><CRC>12898</CRC></Frame>',
    },
    {
        "timestamp": "2025-12-07 21:55:01",
        "id_set": "1765140901",
        "ver": "57855",
        "crc": "16543",
        "frame": '<Frame><ID>13584263</ID><ID_Device>2206237016</ID_Device><ID_Set>1765140901</ID_Set><ID_SubD>0</ID_SubD><DT>07.12.2025 21:55:01</DT><NewValue>0</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-07 20:55:11</TSec><ver>57855</ver><CRC>16543</CRC></Frame>',
    },
    {
        "timestamp": "2025-12-08 04:08:59",
        "id_set": "1765163339",
        "ver": "05839",
        "crc": "50194",
        "frame": '<Frame><ID>13584519</ID><ID_Device>2206237016</ID_Device><ID_Set>1765163339</ID_Set><ID_SubD>0</ID_SubD><DT>08.12.2025 4:08:59</DT><NewValue>0</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-08 03:11:25</TSec><ver>05839</ver><CRC>50194</CRC></Frame>',
    },
    {
        "timestamp": "2025-12-08 06:09:08",
        "id_set": "1765170548",
        "ver": "18562",
        "crc": "19797",
        "frame": '<Frame><ID>13584575</ID><ID_Device>2206237016</ID_Device><ID_Set>1765170548</ID_Set><ID_SubD>0</ID_SubD><DT>08.12.2025 6:09:08</DT><NewValue>0</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-08 05:09:17</TSec><ver>18562</ver><CRC>19797</CRC></Frame>',
    },
]

# MODE=3 (zapnout FMT/No Limit) - 4 validní příkazy
MODE_3_COMMANDS = [
    {
        "timestamp": "2025-12-07 20:41:21",
        "id_set": "1765136481",
        "ver": "10712",
        "crc": "16664",
        "frame": '<Frame><ID>13584179</ID><ID_Device>2206237016</ID_Device><ID_Set>1765136481</ID_Set><ID_SubD>0</ID_SubD><DT>07.12.2025 20:41:21</DT><NewValue>3</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-07 19:47:07</TSec><ver>10712</ver><CRC>16664</CRC></Frame>',
    },
    {
        "timestamp": "2025-12-07 21:40:03",
        "id_set": "1765140003",
        "ver": "22023",
        "crc": "27350",
        "frame": '<Frame><ID>13584249</ID><ID_Device>2206237016</ID_Device><ID_Set>1765140003</ID_Set><ID_SubD>0</ID_SubD><DT>07.12.2025 21:40:03</DT><NewValue>3</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-07 20:43:09</TSec><ver>22023</ver><CRC>27350</CRC></Frame>',
    },
    {
        "timestamp": "2025-12-07 22:25:21",
        "id_set": "1765142721",
        "ver": "52968",
        "crc": "45985",
        "frame": '<Frame><ID>13584312</ID><ID_Device>2206237016</ID_Device><ID_Set>1765142721</ID_Set><ID_SubD>0</ID_SubD><DT>07.12.2025 22:25:21</DT><NewValue>3</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-07 21:26:43</TSec><ver>52968</ver><CRC>45985</CRC></Frame>',
    },
    {
        "timestamp": "2025-12-08 05:09:04",
        "id_set": "1765166944",
        "ver": "16492",
        "crc": "56179",
        "frame": '<Frame><ID>13584537</ID><ID_Device>2206237016</ID_Device><ID_Set>1765166944</ID_Set><ID_SubD>0</ID_SubD><DT>08.12.2025 5:09:04</DT><NewValue>3</NewValue><Confirm>New</Confirm><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server><mytimediff>0</mytimediff><Reason>Setting</Reason><TSec>2025-12-08 04:09:07</TSec><ver>16492</ver><CRC>56179</CRC></Frame>',
    },
]


# Shrnutí protokolu
PROTOCOL_NOTES = """
## Komunikační protokol OIG BatteryBox

### Flow pro Setting příkaz:
1. BOX → CLOUD: <Result>IsNewSet</Result> + Lat + ver + CRC
   "Máš pro mě nové nastavení?"
   
2. CLOUD → BOX: <Frame>...</Frame> s TblItem, NewValue, ver, CRC
   "Ano, nastav MODE na 3"
   
3. BOX → CLOUD: <Result>ACK</Result><Reason>Setting</Reason>
   "OK, hotovo"

### CRC validace:
- Box validuje CRC příchozích příkazů
- Neplatné CRC → NACK s Reason=WC (Wrong CRC)
- CRC algoritmus je proprietární (není CRC-16, CRC-32, MD5, SHA1)
- CRC závisí na CELÉM obsahu frame včetně ver, NewValue, timestamps

### Replay attack:
- ✅ Přesný replay zachyceného příkazu funguje (ACK)
- ❌ Jakákoliv modifikace způsobí NACK (WC)

### Otevřené otázky:
- Kontroluje box ID_Set pro deduplikaci?
- Kontroluje box stáří DT/TSec?
- Je ver sekvenční nebo náhodné?
"""


if __name__ == "__main__":
    print(f"=== MODE příkazy zachycené 7.-8.12.2025 ===")
    print(f"MODE=0 (Standard): {len(MODE_0_COMMANDS)} příkazů")
    print(f"MODE=3 (No Limit): {len(MODE_3_COMMANDS)} příkazů")
    print()
    print("MODE=0 příkazy:")
    for cmd in MODE_0_COMMANDS:
        print(f"  {cmd['timestamp']} | ver={cmd['ver']} | CRC={cmd['crc']}")
    print()
    print("MODE=3 příkazy:")
    for cmd in MODE_3_COMMANDS:
        print(f"  {cmd['timestamp']} | ver={cmd['ver']} | CRC={cmd['crc']}")
