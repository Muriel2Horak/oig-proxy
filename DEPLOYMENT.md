# Deployment Guide - OIG Proxy Modular Implementation

## 📦 Co je připraveno k nasazení

### ✅ Commit Status
- **Branch:** main
- **Ahead by:** 3 commits (včetně nového refactoringu)
- **Ochrana dat:** `.gitignore` aktualizován (DB a testovací data nejsou v gitu)

### 📁 Nové soubory v commitu
```
addon/oig-proxy/
├── config.py          (95 lines)  - Konfigurace z env proměnných
├── models.py          (75 lines)  - ProxyMode enum + data modely
├── utils.py           (291 lines) - Pomocné funkce, sensor map
├── parser.py          (100 lines) - XML parser
├── cloud_manager.py   (360 lines) - CloudQueue + ACKLearner + HealthChecker
├── mqtt_publisher.py  (568 lines) - MQTT publikování + queue
├── proxy.py           (300 lines) - Hlavní orchestrace + 3 módy
├── main.py            (80 lines)  - Entry point (zjednodušený)
└── README_MODULAR.md              - Dokumentace modulů

analysis/              (14 souborů) - Kompletní reverse engineering
testing/               (8 souborů)  - Testovací infrastruktura
```

---

## 🚀 Možnosti nasazení

### Varianta 1: Home Assistant Addon (DOPORUČENÁ)

**Výhody:**
- ✅ Nativní integrace do HA
- ✅ Automatický restart při selhání
- ✅ Web UI pro konfiguraci
- ✅ Logs v HA supervisor
- ✅ MQTT už je dostupné (core-mosquitto)

**Kroky:**

#### A) Lokální nasazení (development)
```bash
# 1. Zkopíruj addon do HA addons adresáře
cp -r addon/oig-proxy /path/to/homeassistant/addons/

# 2. V Home Assistant:
#    - Settings → Add-ons → Local add-ons
#    - Najdi "OIG Proxy"
#    - Install
#    - Configure (DEVICE_ID, TARGET_SERVER, atd.)
#    - Start
```

#### B) GitHub Repository (production)
```bash
# 1. Push do GitHub
git push origin main

# 2. V Home Assistant:
#    - Settings → Add-ons → Add-on Store
#    - Three dots (top right) → Repositories
#    - Přidej: https://github.com/Muriel2Horak/oig-proxy
#    - Najdi "OIG Proxy" v Custom repositories
#    - Install
```

**Konfigurace (addon options):**
```json
{
  "device_id": "2206237016",
  "target_server": "oigservis.cz",
  "target_port": 5003,
  "proxy_listen_host": "0.0.0.0",
  "proxy_listen_port": 5003,
  "mqtt_broker": "core-mosquitto",
  "mqtt_port": 1883,
  "mqtt_username": "homeassistant",
  "mqtt_password": "your-password",
  "log_level": "INFO"
}
```

---

### Varianta 2: Docker Compose

**Výhody:**
- ✅ Nezávislé na Home Assistant
- ✅ Jednoduchá správa (docker-compose up/down)
- ✅ Volume pro perzistenci dat

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  oig-proxy:
    build: ./addon/oig-proxy
    container_name: oig-proxy
    restart: unless-stopped
    ports:
      - "5003:5003"
    environment:
      - DEVICE_ID=2206237016
      - TARGET_SERVER=oigservis.cz
      - TARGET_PORT=5003
      - PROXY_LISTEN_HOST=0.0.0.0
      - PROXY_LISTEN_PORT=5003
      - MQTT_BROKER=mosquitto
      - MQTT_PORT=1883
      - MQTT_USERNAME=homeassistant
      - MQTT_PASSWORD=your-password
      - LOG_LEVEL=INFO
      - DATA_DIR=/data
    volumes:
      - ./data:/data
    depends_on:
      - mosquitto

  mosquitto:
    image: eclipse-mosquitto:2
    container_name: mosquitto
    restart: unless-stopped
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto/config:/mosquitto/config
      - ./mosquitto/data:/mosquitto/data
      - ./mosquitto/log:/mosquitto/log
```

**Kroky:**
```bash
# 1. Vytvoř docker-compose.yml v root adresáři
cd /Users/martinhorak/Projects/oig-proxy

# 2. Nastartuj
docker-compose up -d

# 3. Sleduj logy
docker-compose logs -f oig-proxy

# 4. Restartuj po změnách
docker-compose restart oig-proxy

# 5. Zastav
docker-compose down
```

---

### Varianta 3: Standalone Docker

**Výhody:**
- ✅ Maximální kontrola
- ✅ Jednoduchý deployment

**Kroky:**
```bash
# 1. Build image
cd /Users/martinhorak/Projects/oig-proxy/addon/oig-proxy
docker build -t oig-proxy:latest .

# 2. Run container
docker run -d \
  --name oig-proxy \
  --restart unless-stopped \
  -p 5003:5003 \
  -e DEVICE_ID=2206237016 \
  -e TARGET_SERVER=oigservis.cz \
  -e TARGET_PORT=5003 \
  -e MQTT_BROKER=your-mqtt-ip \
  -e MQTT_PORT=1883 \
  -e MQTT_USERNAME=user \
  -e MQTT_PASSWORD=pass \
  -e LOG_LEVEL=INFO \
  -v /path/to/data:/data \
  oig-proxy:latest

# 3. Sleduj logy
docker logs -f oig-proxy

# 4. Restart
docker restart oig-proxy

# 5. Stop
docker stop oig-proxy
docker rm oig-proxy
```

---

## ⚙️ Konfigurace před nasazením

### 1. Povinné environment variables

```bash
DEVICE_ID          # ID tvého BatteryBoxu (např. 2206237016)
TARGET_SERVER      # OIG cloud server (oigservis.cz)
TARGET_PORT        # Cloud port (5003)
```

### 2. Volitelné environment variables

```bash
# Proxy listening
PROXY_LISTEN_HOST=0.0.0.0    # Default: 0.0.0.0
PROXY_LISTEN_PORT=5003       # Default: 5003

# MQTT konfigurace
MQTT_BROKER=core-mosquitto   # Default: localhost
MQTT_PORT=1883               # Default: 1883
MQTT_USERNAME=               # Default: empty (no auth)
MQTT_PASSWORD=               # Default: empty
MQTT_BASE_TOPIC=oig_local    # Default: oig_local

# Storage
DATA_DIR=/data               # Default: /data (v Dockeru)

# Logging
LOG_LEVEL=INFO               # Default: INFO (DEBUG pro detaily)
```

### 3. Kontrola MQTT přístupu

```bash
# Test MQTT připojení (z HA nebo jiného systému)
mosquitto_sub -h core-mosquitto -t "oig_local/#" -v -u homeassistant -P your-password

# Měl bys vidět zprávy když proxy běží
```

---

## 🔄 Přechod z původní verze

### Plán migrace:

#### Fáze 1: Testování nové verze (DEV)
```bash
# 1. Nechej běžet současný proxy (na portu 5003)

# 2. Spusť nový proxy na jiném portu (např. 5004)
export PROXY_LISTEN_PORT=5004
docker run ... oig-proxy:latest

# 3. Přesměruj BOX na nový proxy (dočasně)
#    BOX → localhost:5004 → OIG Cloud

# 4. Sleduj logy 24-48 hodin
docker logs -f oig-proxy

# 5. Zkontroluj:
#    - Žádné chyby v lozích
#    - MQTT zprávy přicházejí
#    - HA sensory fungují
```

#### Fáze 2: Přepnutí na produkci
```bash
# 1. Zastav starý proxy
docker stop old-oig-proxy

# 2. Přepni nový proxy na port 5003
docker stop oig-proxy
docker rm oig-proxy

docker run -d \
  --name oig-proxy \
  -p 5003:5003 \
  ... (ostatní params) \
  oig-proxy:latest

# 3. BOX se automaticky reconnectne na port 5003

# 4. Sleduj první hodinu aktivně
docker logs -f oig-proxy | grep -E "Mode:|Cloud queue:|MQTT queue:"
```

#### Fáze 3: Rollback plán (pokud něco selže)
```bash
# 1. Rychlé vrácení na starý proxy
docker stop oig-proxy
docker start old-oig-proxy

# 2. BOX se reconnectne automaticky

# 3. Analyzuj logy z nového proxy
docker logs oig-proxy > /tmp/proxy_failure.log
```

---

## 📊 Monitoring po nasazení

### Klíčové metriky ke sledování:

#### 1. Proxy módy
```bash
# V lozích hledej:
grep "Mode:" /data/proxy.log

# Očekávané:
# - ONLINE většinu času
# - OFFLINE pouze při cloud výpadku
# - REPLAY po obnovení cloudu (pár minut)
```

#### 2. Queue velikosti
```bash
# Cloud queue (měla by být většinou 0)
grep "Cloud queue:" /data/proxy.log

# MQTT queue (měla by být malá, < 100)
grep "MQTT queue:" /data/proxy.log
```

#### 3. ACK learning
```bash
# Kontrola že se učí ACK odpovědi
grep "Learned" /data/proxy.log

# Očekávané:
# ✅ Learned ACK_STANDARD (během prvních minut)
# ✅ Learned END_NO_SETTINGS
```

#### 4. Connection duration
```bash
# Sleduj jak dlouho drží spojení s BOXem
grep "Connection duration" /data/proxy.log

# Ideální: > 24 hodin (při normálním provozu)
```

### MQTT monitoring v Home Assistant

```yaml
# Přidej do configuration.yaml
sensor:
  - platform: mqtt
    name: "OIG Proxy Mode"
    state_topic: "oig_local/proxy/mode"
    
  - platform: mqtt
    name: "OIG Cloud Queue Size"
    state_topic: "oig_local/proxy/cloud_queue_size"
    unit_of_measurement: "frames"
    
  - platform: mqtt
    name: "OIG MQTT Queue Size"
    state_topic: "oig_local/proxy/mqtt_queue_size"
    unit_of_measurement: "messages"

automation:
  - alias: "Alert on Long Offline Mode"
    trigger:
      - platform: state
        entity_id: sensor.oig_proxy_mode
        to: "OFFLINE"
        for: "00:30:00"  # 30 minut
    action:
      - service: notify.mobile_app
        data:
          message: "OIG Proxy v OFFLINE módu > 30 minut!"
```

---

## 🐛 Troubleshooting

### Problem: Proxy crashuje při startu

**Kontrola:**
```bash
docker logs oig-proxy | grep -i error

# Časté příčiny:
# - Chybí DEVICE_ID env variable
# - DATA_DIR není writable
# - MQTT broker nedostupný (není kritické, jen warning)
```

**Řešení:**
```bash
# Zkontroluj env variables
docker inspect oig-proxy | grep -A 20 Env

# Zkontroluj volume permissions
ls -la /path/to/data

# Test bez MQTT (dočasně)
docker run -e MQTT_BROKER="" ... oig-proxy:latest
```

---

### Problem: BOX se nemůže připojit

**Kontrola:**
```bash
# 1. Je proxy listening?
docker exec oig-proxy netstat -tlnp | grep 5003

# 2. Je port otevřený na hostu?
netstat -tlnp | grep 5003

# 3. Firewall?
sudo iptables -L -n | grep 5003
```

**Řešení:**
```bash
# Ujisti se že port je publikovaný
docker run -p 5003:5003 ... oig-proxy:latest

# Test připojení z BOXu
telnet proxy-ip 5003
```

---

### Problem: Cloud queue roste

**Kontrola:**
```bash
# Sleduj queue size
docker logs oig-proxy | grep "Cloud queue:" | tail -20

# Kontrola cloud connectivity
docker exec oig-proxy ping -c 3 oigservis.cz
```

**Řešení:**
```bash
# Pokud cloud je dostupný ale queue roste:
# → Možná replay je pomalý, počkej

# Pokud queue > 10000 framů:
# → Možná potřebuješ restart proxy (drain queue)
docker restart oig-proxy
```

---

## ✅ Checklist před nasazením

- [ ] Git commit je hotový (`git push origin main`)
- [ ] Docker image se buildí bez chyb (`docker build -t oig-proxy .`)
- [ ] Environment variables jsou připravené (DEVICE_ID, atd.)
- [ ] MQTT broker je dostupný a credentials jsou správné
- [ ] DATA_DIR volume je vytvořený s write permissions
- [ ] Port 5003 je volný (nebo jiný port v konfiguraci)
- [ ] Backup současného proxy (pokud běží)
- [ ] Rollback plán je připravený
- [ ] Monitoring v HA je nakonfigurovaný

---

## 📝 Poznámky

### Co funguje:
- ✅ Modulární architektura (8 souborů místo 1)
- ✅ 3 proxy módy (ONLINE/OFFLINE/REPLAY)
- ✅ SQLite persistence pro queues
- ✅ ACK learning z cloudu
- ✅ Automatické mode transitions
- ✅ MQTT publikování
- ✅ Testovací infrastruktura

### Co je připraveno k implementaci:
- ⚠️ Replay queue po offline módu (kód je připravený)
- ⚠️ Cloud reconnect detection (background task)
- ⚠️ Metrics publikování do MQTT

### Co potřebuje testování:
- ⏳ Offline mode v produkci (simulované cloud outage)
- ⏳ Replay queue functionality
- ⏳ Long-term stability (> 48h run)
- ⏳ Memory usage monitoring

---

## 🎯 Doporučený postup

**TEĎ:**
1. Push do GitHubu: `git push origin main`
2. Build Docker image: `docker build -t oig-proxy:latest addon/oig-proxy`
3. Test run na jiném portu (5004): parallel s původním proxy
4. Sleduj logy 24 hodin

**ZA 24 HODIN:**
5. Pokud OK → Přepni BOX na nový proxy (port 5003)
6. Sleduj první 2 hodiny aktivně
7. Pokud problém → Rollback na starý proxy

**ZA TÝDEN:**
8. Pokud stabilní → Smaž starý proxy container
9. Simuluj cloud outage (iptables block)
10. Verify offline mode + replay funguje

**ZA MĚSÍC:**
11. Review metrics (connection duration, queue sizes)
12. Optimize pokud potřeba
13. Dokumentuj findings

---

Potřebuješ pomoct s některým krokem? 🚀
