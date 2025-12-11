# Komunikační mapa OIG Box ↔ Proxy ↔ Cloud

## Zjištění z live databáze (7.-10.12.2025)

### Frekvence komunikace pro každou tabulku

| Tabulka | Počet zpráv | Interval | Popis |
|---------|-------------|----------|-------|
| **tbl_actual** | 27,351 | **~9.4s** | Aktuální stav (teplota, vlhkost, výkon, baterie) - **NEJČASTĚJŠÍ** |
| **unknown** | 2,606 | ~98.5s | Neidentifikované zprávy |
| **tbl_dc_in** | 860 | **~299s** (~5min) | DC vstup (FV panely) |
| **tbl_box** | 860 | **~299s** | Info o boxu |
| **tbl_boiler** | 860 | **~299s** | Stav bojleru |
| **tbl_batt** | 860 | **~299s** | Baterie detailně |
| **tbl_ac_out** | 860 | **~299s** | AC výstup (spotřeba) |
| **tbl_ac_in** | 860 | **~299s** | AC vstup (síť) |
| **tbl_events** | 847 | ~293s (~5min) | Události |
| **tbl_batt_prms** | 654 | ~393s (~6.5min) | Parametry baterie |
| **tbl_invertor_prms** | 96 | ~2665s (~44min) | Parametry střídače |
| **tbl_box_prms** | 58 | ~4119s (~69min) | Parametry boxu |

### TCP spojení - KLÍČOVÉ ZJIŠTĚNÍ!

**BOX vytváří JEDNO dlouhodobé TCP spojení, které drží hodiny:**

```
conn_id | frames | start               | end                 | duration_sec
--------|--------|---------------------|---------------------|-------------
132     | 464    | 2025-12-10 18:17:22 | 2025-12-10 19:07:39 | 3017s (50min)
131     | 1019   | 2025-12-10 16:25:42 | 2025-12-10 18:16:36 | 6653s (111min)
130     | 551    | 2025-12-10 15:21:25 | 2025-12-10 16:21:32 | 3607s (60min)
129     | 301    | 2025-12-10 14:43:54 | 2025-12-10 15:17:06 | 1991s (33min)
128     | 1028   | 2025-12-10 12:48:54 | 2025-12-10 14:43:22 | 6868s (114min)
```

**Krátká spojení (1 frame) = výpadky:**
```
125     | 1      | 2025-12-10 08:58:15 | 2025-12-10 08:58:15 | 0s
124     | 1      | 2025-12-10 08:57:35 | 2025-12-10 08:57:35 | 0s
123     | 1      | 2025-12-10 08:57:08 | 2025-12-10 08:57:08 | 0s
```
👉 **To jsou výpadky z rána o kterých jsi mluvil! 08:57-08:58**

---

## Komunikační tok - Normální provoz

### 1. BOX iniciuje TCP spojení
```
BOX → PROXY (port 5710)
PROXY → CLOUD (oigservis.cz:5710)
```

### 2. Opakující se cyklus (každých ~9s)

```mermaid
sequenceDiagram
    participant Box
    participant Proxy
    participant Cloud
    
    Box->>Proxy: tbl_actual (telemetrie)
    Proxy->>Cloud: tbl_actual (forward)
    Cloud->>Proxy: ACK GetActual (CRC 00167)
    Proxy->>Box: ACK GetActual (forward)
    
    Note over Box: Čeká ~9s
    
    Box->>Proxy: IsNewSet? (polling nastavení)
    Proxy->>Cloud: IsNewSet?
    Cloud->>Proxy: END (CRC 34500) - žádná změna
    Proxy->>Box: END
```

### 3. Každých ~5 minut (299s)

Box posílá postupně **všechny** ostatní tabulky:
```
1. tbl_ac_in     → ACK GetActual
2. tbl_ac_out    → ACK GetActual
3. tbl_batt      → ACK GetActual
4. tbl_boiler    → ACK GetActual
5. tbl_box       → ACK GetActual
6. tbl_dc_in     → ACK GetActual
7. tbl_events    → ACK GetActual
```

### 4. Méně časté tabulky

- **tbl_batt_prms** každých ~6.5 minut
- **tbl_invertor_prms** každých ~44 minut
- **tbl_box_prms** každých ~69 minut

---

## PROBLÉM: Současná architektura při výpadku

### Co se děje v `handle_connection()`:

```python
# Řádek 753: BOX se připojí
client_reader, client_writer = await asyncio.open_connection(...)

# Řádek 754-756: Proxy se snaží připojit ke cloudu
server_reader, server_writer = await asyncio.open_connection(
    TARGET_SERVER, TARGET_PORT  # oigservis.cz:5710
)
# ❌ POKUD CLOUD NEFUNGUJE -> ConnectionRefusedError/TimeoutError

# Řádek 784-792: Finally blok
finally:
    # ❌ ZAVŘE SPOJENÍ KE CLOUDU (OK)
    server_writer.close()
    # ❌❌❌ ZAVŘE I SPOJENÍ K BOXU! (ŠPATNĚ!)
    client_writer.close()
```

### Důsledek:

1. **Cloud spadne** (např. 08:57)
2. **Proxy nemůže navázat spojení** na řádku 754
3. **Exception v handle_connection()**
4. **Finally blok zavře spojení k BOXu** (řádek 789)
5. **BOX zjistí že spojení spadlo**
6. **BOX okamžitě zkusí reconnect** → nové conn_id (123, 124, 125...)
7. **Proxy zase nemůže ke cloudu** → opět zavře
8. **Smyčka opakování** dokud cloud nevstane

**Výsledek z databáze:**
```
conn_id 123: 1 frame, 0s  (pokus 1)
conn_id 124: 1 frame, 0s  (pokus 2) 
conn_id 125: 1 frame, 0s  (pokus 3)
conn_id 126: 1025 frames, 6843s (cloud vstal, běží normálně)
```

---

## ŘEŠENÍ: Fallback režim s oddělenými spojeními

### Nová architektura:

```python
async def handle_connection(self, client_reader, client_writer):
    """
    ✅ VŽDY přijme spojení od BOXu
    ✅ Zkusí připojit ke cloudu, ale neselhává pokud to nejde
    ✅ Pokud cloud offline → lokální ACK/END odpovědi
    ✅ Pokud cloud online → průhledný forward
    """
    conn_id = self.connection_count + 1
    client_addr = client_writer.get_extra_info("peername")
    
    # ✅ Spojení s BOXem je vždy aktivní
    logger.info(f"[#{conn_id}] BOX připojen: {client_addr}")
    
    # Pokus o cloud (neblokující)
    cloud_available = False
    server_reader = None
    server_writer = None
    
    try:
        server_reader, server_writer = await asyncio.wait_for(
            asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
            timeout=5.0  # Max 5s na spojení
        )
        cloud_available = True
        logger.info(f"[#{conn_id}] Cloud dostupný")
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
        logger.warning(f"[#{conn_id}] Cloud offline, použit fallback režim: {e}")
        cloud_available = False
    
    if cloud_available:
        # Průhledný forward mode
        await self._forward_mode(client_reader, client_writer, 
                                server_reader, server_writer, conn_id)
    else:
        # Offline mode - lokální ACK
        await self._offline_mode(client_reader, client_writer, conn_id)
```

### Offline mode - komunikační tok:

```mermaid
sequenceDiagram
    participant Box
    participant Proxy
    participant Cloud as Cloud (OFFLINE)
    
    Box->>Proxy: tbl_actual (telemetrie)
    Note over Proxy: Parsuje data<br/>Publikuje do MQTT
    Proxy->>Box: ACK GetActual (CRC 00167)
    
    Note over Box: Čeká ~9s
    
    Box->>Proxy: IsNewSet?
    Proxy->>Box: END (CRC 34500)
    
    Note over Box,Proxy: Spojení zůstává aktivní!<br/>Žádné reconnecty
    
    Box->>Proxy: tbl_actual
    Proxy->>Box: ACK GetActual
```

### Výhody:

✅ **TCP spojení BOX↔PROXY zůstává aktivní** i když cloud padne  
✅ **Žádné reconnect smyčky** (conn_id 123, 124, 125...)  
✅ **Data se zpracovávají do MQTT** i offline  
✅ **BOX funguje normálně**, dostává ACK  
✅ **Monitoring zachován** - Home Assistant vidí data  

### Co se neděje offline:

❌ **Setting frames** - cloud nemůže poslat nové nastavení  
❌ **Cloud storage** - data se neukládají na OIG serveru (ale máme je v MQTT/HA)  
❌ **NACK response** - nepotřebujeme, protože negenerujeme chyby  

---

## Implementační detaily

### Potřebné odpovědi v offline režimu:

#### 1. Standardní telemetrie (99% případů)
```xml
<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>
```
**Použití:** odpověď na tbl_actual, tbl_batt, tbl_ac_in, tbl_ac_out, atd.

#### 2. IsNewSet polling
```xml
<Frame><Result>END</Result><CRC>34500</CRC></Frame>
```
**Použití:** odpověď na `<Result>IsNewSet</Result>` - říká "žádná nová nastavení"

#### 3. Setting confirmation (vzácné)
```xml
<Frame><Result>ACK</Result><CRC>54590</CRC></Frame>
```
**Použití:** pokud box potvrzuje přijaté nastavení (ID_Set, ID_SubD)

### Rozhodovací logika:

```python
def generate_offline_response(box_frame: str) -> str:
    # Polling nastavení
    if '<Result>IsNewSet</Result>' in box_frame:
        return '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'
    
    # Potvrzení nastavení (v offline to není, ale pro jistotu)
    elif '<ID_Set>' in box_frame and '<ID_SubD>' in box_frame:
        return '<Frame><Result>ACK</Result><CRC>54590</CRC></Frame>'
    
    # Vše ostatní = telemetrie
    else:
        return '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'
```

---

## Statistiky z live databáze

### Rozdělení odpovědí od cloudu:
```
ACK GetActual:  36,377 (98.3%) ← toto generujeme offline
ACK (setting):     196 (0.5%)  ← pokud box potvrzuje změnu
Other:             185 (0.5%)  ← neznámé
END:               154 (0.4%)  ← IsNewSet polling
Setting frame:      75 (0.2%)  ← cloud mění nastavení (offline neumíme)
NACK:                6 (0.02%) ← chyby (offline nepotřebujeme)
```

**Závěr:** Offline režim pokryje **99.2%** komunikace (ACK + END).  
Setting frames (0.2%) jsou z cloudu → offline režim je neposílá.

---

## Bezpečnostní úvahy

### ✅ Co je bezpečné:

1. **ACK odpovědi** - pouze potvrzují příjem dat, neovlivňují chování boxu
2. **END odpovědi** - informují že nejsou nová nastavení, bezpečné
3. **MQTT publikace** - data jsou zpracovaná a dostupná v Home Assistant

### ⚠️ Co NEDĚLÁME offline:

1. **Setting frames** - cloud nemůže měnit nastavení boxu (bezpečnější)
2. **Cloud storage** - data nejsou na OIG serveru (ale máme je lokálně)
3. **Proprietary CRC** - neuměli bychom vygenerovat pro setting frames

### 🔄 Cloud reconnect strategie:

```python
# Každých 60s zkusit reconnect
if offline_mode:
    asyncio.create_task(self._try_cloud_reconnect(conn_id))

async def _try_cloud_reconnect(self, conn_id):
    while True:
        await asyncio.sleep(60)
        try:
            test_reader, test_writer = await asyncio.open_connection(...)
            logger.info(f"[#{conn_id}] Cloud znovu dostupný! Přepínám na forward mode")
            # TODO: Přepnout spojení do forward režimu
            test_writer.close()
            break
        except:
            logger.debug(f"[#{conn_id}] Cloud stále offline")
```

---

## Příklad výpadku: 10.12.2025 08:57-08:59

### Současné chování:
```
08:57:08 - conn_id 123 - 1 frame - cloud offline - DISCONNECT
08:57:35 - conn_id 124 - 1 frame - cloud offline - DISCONNECT  
08:58:15 - conn_id 125 - 1 frame - cloud offline - DISCONNECT
08:58:59 - conn_id 126 - normální provoz (6843s)
```
**Ztráta dat:** ~2 minuty (žádná telemetrie do MQTT)

### S fallback režimem:
```
08:57:08 - conn_id 123 - OFFLINE MODE aktivován
08:57:17 - tbl_actual → ACK (local) → MQTT ✅
08:57:26 - tbl_actual → ACK (local) → MQTT ✅
08:57:35 - tbl_actual → ACK (local) → MQTT ✅
...
08:58:59 - Cloud reconnect úspěšný → přepnuto na forward mode
```
**Ztráta dat:** 0 minut (telemetrie pokračuje v MQTT)

---

## Závěr

### Hlavní zjištění:

1. **BOX drží jedno TCP spojení hodiny** (avg 60-110 minut)
2. **Současná proxy zavírá spojení** když cloud offline
3. **BOX dělá rychlé reconnecty** když spojení spadne (conn 123→124→125)
4. **Data se ztrácejí** během výpadků (žádná telemetrie)

### Řešení:

✅ **Oddělit TCP spojení** BOX↔PROXY od PROXY↔CLOUD  
✅ **Vždy přijmout BOX** i když cloud nefunguje  
✅ **Generovat lokální ACK/END** v offline režimu  
✅ **Publikovat do MQTT** i bez cloudu  
✅ **Reconnect na pozadí** každých 60s  

### Implementace:

Potřebujeme upravit:
- `handle_connection()` - oddělit spojení, fallback logika
- Nové metody: `_offline_mode()`, `_forward_mode()`, `_generate_response()`
- Zachovat: `_process_data()`, MQTT publikaci, logging

Chceš vidět konkrétní kód?
