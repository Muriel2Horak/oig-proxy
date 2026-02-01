# OIG Diagnostic Cloud

Mock server simulující oigservis.cz:5710 pro diagnostiku problémových instalací.

## Jak to funguje

1. Zákazník změní v konfiguraci proxy `cloud_host` na `muriel-cz.cz`
2. Proxy posílá data na tento server místo reálného cloudu
3. Server loguje vše co přijde a odpovídá ACK
4. Data jsou uložena a lze je analyzovat

## Deployment na Synology

### 1. Zkopírovat soubory na NAS

```bash
# Z lokálního počítače
cd /path/to/oig-proxy/diagnostic-cloud
scp -r . nas:/volume1/docker/oig-diagnostic/
```

### 2. Spustit kontejner

```bash
ssh nas
cd /volume1/docker/oig-diagnostic
/usr/local/bin/docker-compose up -d --build
```

### 3. Ověřit že běží

```bash
/usr/local/bin/docker-compose logs -f
```

### 4. Port forward na routeru

Otevřít port `5710 TCP` a přesměrovat na NAS IP.

## Použití

### Zákazník změní konfiguraci

V Home Assistant add-on nebo options.json:

```yaml
cloud_host: "muriel-cz.cz"
cloud_port: 5710
```

### Sledování logů

```bash
ssh nas
cd /volume1/docker/oig-diagnostic
/usr/local/bin/docker-compose logs -f --tail=100
```

### Prohlížení zachycených dat

```bash
# Seznam klientů
cat /volume1/docker/oig-diagnostic/data/clients.json | jq .

# Framy konkrétního zařízení
ls /volume1/docker/oig-diagnostic/data/frames/2206237016/
cat /volume1/docker/oig-diagnostic/data/frames/2206237016/2026-02-01.jsonl | head
```

## Struktura dat

```
/volume1/docker/oig-diagnostic/data/
├── clients.json              # Seznam všech klientů
└── frames/
    └── 2206237016/           # Device ID
        ├── 2026-02-01.jsonl  # Denní soubor s framy
        └── 2026-02-02.jsonl
```

### clients.json

```json
{
  "2206237016": {
    "device_id": "2206237016",
    "first_seen": "2026-02-01T10:23:45",
    "last_seen": "2026-02-01T15:30:00",
    "client_ips": ["84.42.123.45"],
    "total_frames": 1234,
    "tables_seen": ["tbl_dc_in", "tbl_ac_in", "tbl_batt", ...]
  }
}
```

### Denní soubory (JSONL)

```json
{"timestamp": "2026-02-01T10:23:45", "parsed": {"table_name": "tbl_dc_in", "device_id": "2206237016"}, "raw": "<Frame>..."}
```

## Správa

### Restart

```bash
ssh nas "cd /volume1/docker/oig-diagnostic && /usr/local/bin/docker-compose restart"
```

### Stop

```bash
ssh nas "cd /volume1/docker/oig-diagnostic && /usr/local/bin/docker-compose down"
```

### Update

```bash
# Lokálně
scp diagnostic-cloud/server.py nas:/volume1/docker/oig-diagnostic/

# Na NAS
ssh nas "cd /volume1/docker/oig-diagnostic && /usr/local/bin/docker-compose up -d --build"
```

## Retence dat

- Automaticky maže data starší než 7 dní
- Lze změnit přes environment `RETENTION_DAYS`
