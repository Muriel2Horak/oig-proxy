# Network Diagnostic Tools

Nástroje pro diagnostiku a zachycení síťové komunikace OIG Proxy.

## 1. Network Diagnostic (`network_diagnostic.py`)

Diagnostický skript pro ověření síťové konfigurace a připojení ke cloudu.

### Použití

```bash
# Základní diagnostika
python scripts/network_diagnostic.py

# Verbose výstup s detaily
python scripts/network_diagnostic.py --verbose

# Testuj jiný cloud server
python scripts/network_diagnostic.py --cloud 192.168.1.100:5710

# Ulož report jako JSON
python scripts/network_diagnostic.py --output /tmp/diag_report.json
```

### Co kontroluje

| Check | Popis |
|-------|-------|
| DNS Resolution | Překlad hostname cloudu na IP |
| Local Network | Lokální IP, gateway, privátní síť |
| TCP Connection | Spojení na cloud port 5710 |
| Ping Latency | ICMP latence (pokud není blokovaný) |
| Traceroute | Cesta paketů ke cloudu |
| OIG Protocol | Test protokolu - pošle testovací frame |
| Proxy Port | Zda proxy běží na portu 5710 |
| Firewall Hints | Detekce potenciálních firewall problémů |

### Výstup

```
✅ DNS Resolution: oigservis.cz → 185.25.185.30
✅ TCP Connection: Connected to 185.25.185.30:5710 (45ms)
✅ OIG Protocol: Cloud responded (120ms)
⚠️ Ping Latency: ICMP may be blocked
⚠️ Firewall Hints: Found 1 potential issues

SUMMARY: MOSTLY OK
```

---

## 2. Mock Cloud Capture Server (`mock_cloud_capture.py`)

Server simulující OIG cloud pro zachycení a analýzu komunikace.

### Použití na NAS

```bash
# Spustit na NAS (např. Synology)
python scripts/mock_cloud_capture.py --port 5710 --output /volume1/captures

# S verbose logováním
python scripts/mock_cloud_capture.py -v --output /tmp/captures
```

### Konfigurace přesměrování

#### Varianta A: DNS override (doporučeno)

Na routeru nebo v `/etc/hosts` na proxy zařízení:
```
185.25.185.30    oigservis.cz
# Nebo IP vašeho NAS:
10.0.0.100       oigservis.cz
```

#### Varianta B: Proxy config

V `options.json` nebo Home Assistant add-on config:
```yaml
cloud_host: "10.0.0.100"  # IP vašeho NAS
cloud_port: 5710
```

### Co zachytává

- Všechny příchozí framy od proxy/BOX
- Parsované informace (tabulka, device ID, timestamp)
- Statistiky spojení
- Automatické ukládání do JSON souborů

### Výstup

```
📥 #1 15:32:45.123 tbl_dc_in       Device=2206237016 Reason=Table Size=450
📥 #1 15:32:45.234 tbl_ac_in       Device=2206237016 Reason=Table Size=520
📥 #1 15:32:45.345 tbl_batt        Device=2206237016 Reason=Table Size=380
...

CAPTURE SUMMARY
  Duration:         0:05:23
  Total connections: 15
  Total frames:      120
  Unique devices:    1
  Tables seen:       tbl_dc_in, tbl_ac_in, tbl_batt, tbl_actual, ...
```

### Uložené soubory

```
/tmp/mock_cloud_capture/
├── captures_20260201_153245.json    # Všechny zachycené framy
├── connections_20260201_153245.json # Statistiky spojení
└── frames_20260201_153245/          # Jednotlivé framy jako XML
    ├── 0001_tbl_dc_in.xml
    ├── 0002_tbl_ac_in.xml
    └── ...
```

---

## Troubleshooting scénáře

### 1. "Cloud nedostupný"

```bash
python scripts/network_diagnostic.py --verbose
```

Kontrolujte:
- DNS Resolution - má být `185.25.185.30`
- TCP Connection - musí projít
- Firewall - zkontrolujte outbound port 5710

### 2. "Data nejdou do cloudu"

Spusťte mock server a přesměrujte provoz:

```bash
# Na NAS
python scripts/mock_cloud_capture.py -v

# Na proxy zařízení - přidejte do /etc/hosts:
# 10.0.0.100  oigservis.cz
```

Pak sledujte co mock server zachytí.

### 3. "Podezření na zásah do sítě"

```bash
python scripts/network_diagnostic.py --verbose --output /tmp/diag.json
```

V reportu hledejte:
- Neočekávané IP v traceroute
- Vysoká latence
- Divné DNS odpovědi

### 4. "BOX posílá, ale cloud nevidí"

1. Spusťte mock server na NAS
2. Přesměrujte DNS/hosts na NAS
3. Ověřte že mock server vidí framy
4. Pokud ano - problém je mezi NAS a cloudem
5. Pokud ne - problém je mezi BOX a proxy

---

## Integrace do Home Assistant

Pro spuštění diagnostiky z HA:

```yaml
# V shell_command.yaml
oig_diagnostic:
  run: "python /config/addons/oig-proxy/scripts/network_diagnostic.py --json --output /config/oig_diagnostic.json"
```

Nebo jako sensor:

```yaml
sensor:
  - platform: command_line
    name: OIG Network Status
    command: "python /config/addons/oig-proxy/scripts/network_diagnostic.py --json | jq -r '.summary.overall_status'"
    scan_interval: 3600
```
