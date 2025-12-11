# Aktualizace OIG Proxy na HA serveru

## 📊 Současný stav

**Běžící addon:**
- **Název:** OIG Proxy
- **Slug:** `d7b5d5b1_oig_proxy`
- **Verze:** 1.2.22
- **Repository:** OIG Proxy Add-ons (slug: d7b5d5b1)
- **Status:** Běží 32+ hodin
- **Auto-update:** ✅ Zapnuto
- **Boot:** auto
- **Protected:** ✅ Ano

**Konfigurace:**
```yaml
target_server: oigservis.cz
target_port: 5710
proxy_port: 5710
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: oig
mqtt_password: oig
capture_payloads: true
log_level: DEBUG
map_reload_seconds: 0
```

**Současná implementace:**
- Monolitický `main.py` (38,690 bytes)
- `sensor_map.json` (114,612 bytes)
- `requirements.txt`

---

## 🎯 Plán aktualizace na novou modulární verzi

### Možnost 1: Přímá aktualizace v repository (DOPORUČENÁ)

Pokud máš přístup k repository `d7b5d5b1` (OIG Proxy Add-ons):

#### Kroky:

1. **Update repository s novými soubory:**
```bash
# V repository oig-proxy-addon:
cd /path/to/oig-proxy-addon

# Zkopíruj nové moduly
cp /Users/martinhorak/Projects/oig-proxy/addon/oig-proxy/*.py .
cp /Users/martinhorak/Projects/oig-proxy/addon/oig-proxy/README_MODULAR.md .

# Backup starého main.py
mv main.py main_old.py

# Update config.json (přidej DEVICE_ID option)
# Update Dockerfile (pokud potřeba)
# Update CHANGELOG

# Commit a push
git add .
git commit -m "feat: Modular refactoring v2.0.0"
git tag v2.0.0
git push origin main --tags
```

2. **Rebuild addon v repository:**
```bash
# Repository by mělo mít GitHub Actions nebo trigger na rebuild
# Pokud ne, je potřeba manuální build a push do Docker registry
```

3. **Update na HA serveru:**
```bash
ssh ha

# Zkontroluj dostupnost updatu
ha addons info d7b5d5b1_oig_proxy

# Update addon
ha addons update d7b5d5b1_oig_proxy

# Restart (pokud auto_update=false)
ha addons restart d7b5d5b1_oig_proxy

# Sleduj logy
ha addons logs d7b5d5b1_oig_proxy -f
```

---

### Možnost 2: Lokální addon (TEST před publikací)

Vytvoř lokální addon pro testování před publikací do repository:

#### Kroky:

1. **Vytvoř lokální addon adresář:**
```bash
ssh ha

# Vytvoř adresář pro lokální addony (pokud neexistuje)
mkdir -p /addons/oig-proxy-test

# Opusť SSH (budeme kopírovat soubory)
exit
```

2. **Zkopíruj nové soubory na HA server:**
```bash
# Z tvého Macu:
cd /Users/martinhorak/Projects/oig-proxy

# Zkopíruj celý addon adresář
scp -r addon/oig-proxy/* ha:/addons/oig-proxy-test/

# Přejmenuj slug v config.json aby nekolidoval
ssh ha "sed -i 's/\"d7b5d5b1_oig_proxy\"/\"local_oig_proxy_test\"/g' /addons/oig-proxy-test/config.json"
```

3. **Reload addons v HA:**
```bash
ssh ha

# Reload addon list
ha addons reload

# Najdi nový addon
ha addons info local_oig_proxy_test
```

4. **Nastav konfiguraci:**
```bash
# Zkopíruj config ze současného addonu
ha addons info d7b5d5b1_oig_proxy | grep -A 20 "options:" > /tmp/config.yaml

# Nastav config pro test addon
ha addons options local_oig_proxy_test --options '{
  "target_server": "oigservis.cz",
  "target_port": 5710,
  "proxy_port": 5711,
  "mqtt_host": "core-mosquitto",
  "mqtt_port": 1883,
  "mqtt_username": "oig",
  "mqtt_password": "oig",
  "log_level": "DEBUG",
  "capture_payloads": true,
  "device_id": "2206237016"
}'
```

5. **Spusť test addon na jiném portu:**
```bash
# Install
ha addons install local_oig_proxy_test

# Start
ha addons start local_oig_proxy_test

# Sleduj logy
ha addons logs local_oig_proxy_test -f
```

6. **Testuj 24 hodin:**
```bash
# Přesměruj BOX na test port (5711) dočasně
# Nebo nech současný addon běžet a sleduj jen logy testu
```

7. **Po úspěšném testu - přepni na produkci:**
```bash
# Stop test addon
ha addons stop local_oig_proxy_test

# Změň port na produkční (5710)
ha addons options local_oig_proxy_test --options '{"proxy_port": 5710}'

# Stop produkční addon
ha addons stop d7b5d5b1_oig_proxy

# Start test addon na produkčním portu
ha addons start local_oig_proxy_test

# Sleduj první 2 hodiny
ha addons logs local_oig_proxy_test -f | grep -E "Mode:|queue:"
```

---

### Možnost 3: Manuální update kontejneru (RYCHLÉ, ale nezdravé)

⚠️ **Nedoporučuji** - změny se ztratí při restartu addonu, ale pro rychlý test:

```bash
ssh ha

# Backup současného main.py
docker exec addon_d7b5d5b1_oig_proxy cp /app/main.py /data/main_backup.py

# Zkopíruj nové soubory do kontejneru
docker cp /Users/martinhorak/Projects/oig-proxy/addon/oig-proxy/config.py \
  addon_d7b5d5b1_oig_proxy:/app/

docker cp /Users/martinhorak/Projects/oig-proxy/addon/oig-proxy/models.py \
  addon_d7b5d5b1_oig_proxy:/app/

# ... (všechny ostatní soubory)

# Restart addonu
ha addons restart d7b5d5b1_oig_proxy
```

**PROBLÉM:** Po updatu addonu z repository se změny ztratí!

---

## 🔧 Doporučený postup pro naši situaci

### Krok 1: Lokální test (TEĎ)
```bash
# 1. Vytvoř lokální test addon
scp -r addon/oig-proxy/* ha:/addons/oig-proxy-test/

# 2. Update config.json
ssh ha "cat > /addons/oig-proxy-test/config.json" << 'EOF'
{
  "name": "OIG Proxy Test (Modular)",
  "version": "2.0.0-test",
  "slug": "local_oig_proxy_test",
  "description": "TCP proxy OIG -> MQTT/HA (modulární verze - TEST)",
  "arch": ["amd64", "aarch64"],
  "startup": "services",
  "boot": "auto",
  "host_network": true,
  "options": {
    "target_server": "oigservis.cz",
    "target_port": 5710,
    "proxy_port": 5711,
    "mqtt_host": "core-mosquitto",
    "mqtt_port": 1883,
    "mqtt_username": "oig",
    "mqtt_password": "oig",
    "device_id": "2206237016",
    "log_level": "DEBUG",
    "capture_payloads": true
  },
  "schema": {
    "target_server": "str",
    "target_port": "int",
    "proxy_port": "int",
    "mqtt_host": "str",
    "mqtt_port": "int",
    "mqtt_username": "str",
    "mqtt_password": "password",
    "device_id": "str",
    "log_level": "list(DEBUG|INFO|WARNING|ERROR)",
    "capture_payloads": "bool?"
  }
}
EOF

# 3. Reload a install
ssh ha "ha addons reload && ha addons install local_oig_proxy_test"

# 4. Start a sleduj
ssh ha "ha addons start local_oig_proxy_test && ha addons logs local_oig_proxy_test -f"
```

### Krok 2: Test 24-48 hodin
- Sleduj logy každých pár hodin
- Kontroluj MQTT zprávy v HA
- Zkontroluj že sensory fungují
- Monitoruj memory usage

### Krok 3: Přepnutí na produkci (po úspěšném testu)
```bash
ssh ha

# Stop test addon
ha addons stop local_oig_proxy_test

# Update config - změň port na 5710
ha addons options local_oig_proxy_test --options '{"proxy_port": 5710}'

# Stop starý addon
ha addons stop d7b5d5b1_oig_proxy

# Start nový addon
ha addons start local_oig_proxy_test

# BOX se automaticky reconnectne (během 28-48s)

# Sleduj logy
ha addons logs local_oig_proxy_test -f
```

### Krok 4: Publikace do repository (po týdnu provozu)
```bash
# Pokud vše funguje, publikuj do původního repository
# → Umožní ostatním klientům update
# → Auto-update na HA serveru
```

---

## 📋 Checklist před aktualizací

- [ ] Git push nové verze (`git push origin main`)
- [ ] Backup současné konfigurace (`ha addons info d7b5d5b1_oig_proxy > /tmp/addon_backup.yaml`)
- [ ] Backup payloads.db (pokud existuje: `docker cp addon_d7b5d5b1_oig_proxy:/data/payloads.db /backup/`)
- [ ] Test addon je vytvořený a konfigurovaný
- [ ] DEVICE_ID je správně nastavené (2206237016)
- [ ] Test port 5711 je volný
- [ ] MQTT credentials jsou správné
- [ ] Rollback plán je připravený

---

## 🚨 Rollback plán

Pokud nová verze selže:

```bash
ssh ha

# Rychlý rollback:
# 1. Stop nový addon
ha addons stop local_oig_proxy_test

# 2. Start starý addon
ha addons start d7b5d5b1_oig_proxy

# BOX se reconnectne automaticky během 30-60s
```

---

## 📊 Monitoring po aktualizaci

```bash
# Sleduj logy kontinuálně první 2 hodiny
ssh ha "ha addons logs local_oig_proxy_test -f"

# Klíčové věci k sledování:
# - "Mode: ONLINE" (většinu času)
# - "Cloud queue: 0 frames" (většinou 0)
# - "MQTT queue: X messages" (malé číslo)
# - Žádné "ERROR" nebo "Traceback"
# - Connection duration > 1 hodina

# Kontrola MQTT zpráv v HA
# Developer Tools → MQTT → Listen to topic: oig_local/#

# Kontrola že sensory dostávají data
# Developer Tools → States → filtr: oig
```

---

## 🎯 Timeline

### Dnes (11. prosince):
- [x] Analýza současného stavu
- [ ] Vytvoření lokálního test addonu
- [ ] Spuštění testu na portu 5711
- [ ] První kontrola logů (po 1 hodině)

### Zítra (12. prosince):
- [ ] Kontrola logů z noci
- [ ] Analýza stability
- [ ] Kontrola MQTT zpráv

### 13. prosince (po 48h):
- [ ] Rozhodnutí GO/NO-GO
- [ ] Pokud OK → Přepnutí na port 5710
- [ ] Monitoring první 4 hodiny aktivně

### 18. prosince (po týdnu):
- [ ] Review stability
- [ ] Publikace do repository (pokud OK)
- [ ] Dostupné pro ostatní klienty

---

## 💡 Poznámky

### Co se mění:
- ✅ Modulární architektura (8 souborů místo 1)
- ✅ SQLite queues pro OFFLINE mode
- ✅ Automatické mode transitions
- ✅ ACK learning
- ⚠️ Nový parameter: `device_id` (povinný)

### Co zůstává stejné:
- ✅ MQTT publikování
- ✅ Sensor discovery
- ✅ Payload capture
- ✅ Forward mode chování

### Rizika:
- ⚠️ Nová architektura může mít edge cases
- ⚠️ SQLite může spotřebovat disk space (ale max ~10MB)
- ⚠️ Import errors pokud nějaký modul chybí

### Mitigation:
- ✅ Test addon na jiném portu
- ✅ Současný addon běží paralelně
- ✅ Rychlý rollback možný kdykoliv
- ✅ Backup dat

---

Připraven začít? Použij příkazy z **Krok 1** výše! 🚀
