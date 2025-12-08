#!/bin/bash
# =============================================================================
# Skript pro úplné vymazání OIG Proxy MQTT entit z Home Assistant
# 
# CO SE SMAŽE:
#   - MQTT retained messages (discovery, state, availability)
#   - HA entity s unique_id obsahujícím "oig_local_"
#   - HA zařízení s identifikátorem obsahujícím "oig_local_"
#
# CO ZŮSTANE ZACHOVÁNO:
#   - OIG Cloud integrace (platforma: oig_cloud)
#   - OIG Cloud HACS (platforma: hacs)
#   - OIG Proxy addon systémové entity (platforma: hassio)
#
# POUŽITÍ:
#   ./cleanup_oig_local.sh
#
# POŽADAVKY:
#   - SSH přístup k HA (alias "ha" v ~/.ssh/config)
#   - Běžící mosquitto addon
# =============================================================================

set -e

# Konfigurace
HA_SSH="ha"
MQTT_USER="oig"
MQTT_PASS="oig"
DEVICE_ID="2206237016"
# Cesta UVNITŘ homeassistant kontejneru
STORAGE_PATH="/config/.storage"
UNIQUE_ID_PATTERN="oig_local_"

echo "=============================================="
echo "  Cleanup OIG Proxy MQTT entities"
echo "=============================================="
echo ""
echo "Tento skript smaže:"
echo "  - MQTT retained messages pro oig_local/*"
echo "  - HA entity s unique_id obsahujícím '$UNIQUE_ID_PATTERN'"
echo "  - HA zařízení s identifikátorem '$UNIQUE_ID_PATTERN'"
echo ""
echo "Zachová:"
echo "  - oig_cloud (OIG Cloud integrace)"
echo "  - hassio entity (addon systémové)"
echo ""
read -p "Pokračovat? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Zrušeno."
    exit 1
fi

# =============================================================================
# KROK 1: Zastavit OIG Proxy addon
# =============================================================================
echo ""
echo "[1/5] Zastavuji OIG Proxy addon..."
ssh $HA_SSH "ha addons stop d7b5d5b1_oig_proxy 2>/dev/null || true"
sleep 2
echo "   ✓ Addon zastaven"

# =============================================================================
# KROK 2: Smazat MQTT retained messages
# =============================================================================
echo ""
echo "[2/5] Mažu MQTT retained messages..."

# Smazat discovery topics pro sensor a binary_sensor
echo "   Mažu discovery topics..."
for component in sensor binary_sensor; do
    TOPICS=$(ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_sub \
        -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
        -t 'homeassistant/${component}/oig_local_#' \
        --retained-only -W 3 -v 2>/dev/null | cut -d' ' -f1" 2>/dev/null || echo "")
    
    if [ -n "$TOPICS" ]; then
        COUNT=$(echo "$TOPICS" | wc -l | tr -d ' ')
        echo "   Nalezeno $COUNT ${component} discovery topiců"
        echo "$TOPICS" | while read topic; do
            if [ -n "$topic" ]; then
                ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub \
                    -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
                    -r -n -t '$topic'" 2>/dev/null || true
            fi
        done
    fi
done

# Smazat state a availability topics
echo "   Mažu state/availability topics..."
STATE_TOPICS=$(ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_sub \
    -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
    -t 'oig_local/#' --retained-only -W 3 -v 2>/dev/null | cut -d' ' -f1" 2>/dev/null || echo "")

if [ -n "$STATE_TOPICS" ]; then
    COUNT=$(echo "$STATE_TOPICS" | wc -l | tr -d ' ')
    echo "   Nalezeno $COUNT state/availability topiců"
    echo "$STATE_TOPICS" | while read topic; do
        if [ -n "$topic" ]; then
            ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub \
                -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
                -r -n -t '$topic'" 2>/dev/null || true
        fi
    done
fi

echo "   ✓ MQTT retained messages smazány"

# =============================================================================
# KROK 3: Backup registrů (v běžícím HA kontejneru)
# =============================================================================
echo ""
echo "[3/5] Vytvářím zálohu registrů..."
BACKUP_SUFFIX=$(date +%Y%m%d_%H%M%S)
ssh $HA_SSH "docker exec homeassistant cp $STORAGE_PATH/core.entity_registry $STORAGE_PATH/core.entity_registry.backup.$BACKUP_SUFFIX"
ssh $HA_SSH "docker exec homeassistant cp $STORAGE_PATH/core.device_registry $STORAGE_PATH/core.device_registry.backup.$BACKUP_SUFFIX"
echo "   ✓ Zálohy vytvořeny (suffix: $BACKUP_SUFFIX)"

# =============================================================================
# KROK 4: Smazat entity a zařízení z registrů (Python v kontejneru)
# =============================================================================
echo ""
echo "[4/5] Mažu entity a zařízení z HA registrů..."

ssh $HA_SSH "docker exec homeassistant python3 << 'PYTHON_SCRIPT'
import json

PATTERN = \"oig_local_\"
STORAGE = \"/config/.storage\"

# --- Entity Registry ---
print(\"   Zpracovávám entity registry...\")
entity_path = f\"{STORAGE}/core.entity_registry\"
with open(entity_path, \"r\") as f:
    entity_data = json.load(f)

# Počítadla
entities_before = len(entity_data[\"data\"][\"entities\"])
deleted_before = len(entity_data[\"data\"].get(\"deleted_entities\", []))

# Filtrovat entity - ponechat ty co NEOBSAHUJÍ pattern
entity_data[\"data\"][\"entities\"] = [
    e for e in entity_data[\"data\"][\"entities\"]
    if PATTERN not in str(e.get(\"unique_id\", \"\"))
]

# Vyčistit i deleted_entities
if \"deleted_entities\" in entity_data[\"data\"]:
    entity_data[\"data\"][\"deleted_entities\"] = [
        e for e in entity_data[\"data\"][\"deleted_entities\"]
        if PATTERN not in str(e.get(\"unique_id\", \"\"))
    ]

entities_after = len(entity_data[\"data\"][\"entities\"])
deleted_after = len(entity_data[\"data\"].get(\"deleted_entities\", []))

# Uložit
with open(entity_path, \"w\") as f:
    json.dump(entity_data, f, indent=2)

print(f\"   Entity: {entities_before} → {entities_after} (smazáno {entities_before - entities_after})\")
print(f\"   Deleted: {deleted_before} → {deleted_after} (vyčištěno {deleted_before - deleted_after})\")

# --- Device Registry ---
print(\"   Zpracovávám device registry...\")
device_path = f\"{STORAGE}/core.device_registry\"
with open(device_path, \"r\") as f:
    device_data = json.load(f)

devices_before = len(device_data[\"data\"][\"devices\"])

# Filtrovat zařízení
device_data[\"data\"][\"devices\"] = [
    d for d in device_data[\"data\"][\"devices\"]
    if PATTERN not in str(d.get(\"identifiers\", []))
]

# Vyčistit i deleted_devices pokud existuje
if \"deleted_devices\" in device_data[\"data\"]:
    device_data[\"data\"][\"deleted_devices\"] = [
        d for d in device_data[\"data\"][\"deleted_devices\"]
        if PATTERN not in str(d.get(\"identifiers\", []))
    ]

devices_after = len(device_data[\"data\"][\"devices\"])

# Uložit
with open(device_path, \"w\") as f:
    json.dump(device_data, f, indent=2)

print(f\"   Devices: {devices_before} → {devices_after} (smazáno {devices_before - devices_after})\")
print(\"   ✓ Registry vyčištěny\")
PYTHON_SCRIPT"

# =============================================================================
# KROK 5: Restartovat Home Assistant Core (pro načtení změn)
# =============================================================================
echo ""
echo "[5/5] Restartuji Home Assistant Core..."
ssh $HA_SSH "ha core restart"
echo "   Čekám na restart (45s)..."
sleep 45

# Ověření
echo ""
echo "   Ověřuji stav..."
REMAINING=$(ssh $HA_SSH "docker exec homeassistant python3 -c \"
import json
with open('/config/.storage/core.entity_registry') as f:
    data = json.load(f)
count = len([e for e in data['data']['entities'] if 'oig_local_' in str(e.get('unique_id', ''))])
print(count)
\"" 2>/dev/null || echo "?")

echo ""
echo "=============================================="
echo "  HOTOVO!"
echo "=============================================="
echo ""
echo "Zbývající oig_local entity: $REMAINING"
echo ""
echo "Zálohy: $STORAGE_PATH/*.backup.$BACKUP_SUFFIX"
echo ""
echo "Pro spuštění OIG Proxy:"
echo "  ssh $HA_SSH ha addons start d7b5d5b1_oig_proxy"
echo ""
echo "Nebo pushni novou verzi a nainstaluj:"
echo "  git add -A && git commit -m 'v1.2.21' && git push"
