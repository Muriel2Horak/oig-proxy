#!/bin/bash
# =============================================================================
# Skript pro úplné vymazání OIG Proxy MQTT entit z Home Assistant
# 
# CO SE SMAŽE:
#   - MQTT entity s unique_id obsahujícím "oig_local_" (platforma: mqtt)
#
# CO ZŮSTANE ZACHOVÁNO:
#   - OIG Cloud integrace (platforma: oig_cloud)
#   - OIG Cloud HACS (platforma: hacs)
#   - OIG Proxy addon systémové entity (platforma: hassio)
# =============================================================================

set -e

# Konfigurace - uprav podle potřeby
HA_SSH="ha"  # SSH alias pro Home Assistant
MQTT_USER="oig"
MQTT_PASS="oig"
STORAGE_PATH="/var/lib/homeassistant/homeassistant/.storage"

# Pattern pro filtrování - smaže pouze MQTT entity s oig_local
UNIQUE_ID_PATTERN="oig_local_"

echo "=============================================="
echo "  Cleanup OIG Proxy MQTT entities"
echo "=============================================="
echo ""
echo "Tento skript smaže POUZE:"
echo "  - MQTT entity s unique_id obsahujícím '$UNIQUE_ID_PATTERN'"
echo ""
echo "Zachová:"
echo "  - oig_cloud (OIG Cloud integrace)"
echo "  - hassio entity (addon systémové)"
echo "  - hacs entity (OIG Cloud HACS)"
echo ""
read -p "Pokračovat? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Zrušeno."
    exit 1
fi

# 1. Zastavit OIG Proxy addon (pokud běží)
echo ""
echo "[1/6] Zastavuji OIG Proxy addon..."
ssh $HA_SSH "ha addons stop d7b5d5b1_oig_proxy 2>/dev/null || true"
sleep 2

# 2. Získat seznam všech retained MQTT topicků s oig_local
echo "[2/6] Hledám MQTT discovery topicy..."
echo "   Hledám v homeassistant/sensor/oig_local_*..."

TOPICS=$(ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_sub \
    -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
    -t 'homeassistant/sensor/oig_local_#' \
    --retained-only -W 5 -v 2>/dev/null | grep '/config' | cut -d' ' -f1" 2>/dev/null || echo "")

TOPIC_COUNT=0
if [ -n "$TOPICS" ]; then
    TOPIC_COUNT=$(echo "$TOPICS" | wc -l | tr -d ' ')
fi
echo "   Nalezeno $TOPIC_COUNT discovery topiců"

# 3. Smazat retained MQTT zprávy
echo "[3/6] Mažu retained MQTT zprávy..."
if [ -n "$TOPICS" ]; then
    echo "$TOPICS" | while read topic; do
        if [ -n "$topic" ]; then
            echo "   Mažu: $topic"
            ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub \
                -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
                -r -n -t '$topic'" 2>/dev/null || true
        fi
    done
fi

# Smazat i state a availability topicy
echo "   Mažu state/availability topicy pro oig_local..."
ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_sub \
    -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
    -t 'oig_local/#' --retained-only -W 3 -v 2>/dev/null | cut -d' ' -f1 | \
    while read topic; do
        if [ -n \"\$topic\" ]; then
            docker exec addon_core_mosquitto mosquitto_pub \
                -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
                -r -n -t \"\$topic\" 2>/dev/null || true
        fi
    done" 2>/dev/null || true

# 4. Zastavit Home Assistant Core
echo "[4/6] Zastavuji Home Assistant Core..."
ssh $HA_SSH "ha core stop"
echo "   Čekám na zastavení..."
sleep 8

# 5. Smazat entity z registry (pouze oig_local MQTT entity)
echo "[5/6] Odstraňuji entity z HA registry..."

# Backup registrů
BACKUP_SUFFIX=$(date +%Y%m%d_%H%M%S)
echo "   Vytvářím zálohu registrů (suffix: $BACKUP_SUFFIX)..."
ssh $HA_SSH "cp $STORAGE_PATH/core.entity_registry $STORAGE_PATH/core.entity_registry.backup.$BACKUP_SUFFIX"
ssh $HA_SSH "cp $STORAGE_PATH/core.device_registry $STORAGE_PATH/core.device_registry.backup.$BACKUP_SUFFIX"

# Počet entit před smazáním
BEFORE_COUNT=$(ssh $HA_SSH "cat $STORAGE_PATH/core.entity_registry | jq '[.data.entities[] | select(.unique_id | tostring | contains(\"${UNIQUE_ID_PATTERN}\"))] | length'")
echo "   Nalezeno $BEFORE_COUNT entit ke smazání"

# Smazat MQTT entity s oig_local_ v unique_id
echo "   Mažu entity s unique_id obsahujícím '${UNIQUE_ID_PATTERN}'..."
ssh $HA_SSH "cat $STORAGE_PATH/core.entity_registry | \
    jq '.data.entities = [.data.entities[] | select(.unique_id | tostring | contains(\"${UNIQUE_ID_PATTERN}\") | not)]' \
    > /tmp/entity_registry_clean.json && \
    mv /tmp/entity_registry_clean.json $STORAGE_PATH/core.entity_registry"

# Smazat MQTT zařízení s oig_local v identifiers
echo "   Mažu zařízení s identifikátorem obsahujícím '${UNIQUE_ID_PATTERN}'..."
ssh $HA_SSH "cat $STORAGE_PATH/core.device_registry | \
    jq '.data.devices = [.data.devices[] | select(.identifiers | tostring | contains(\"${UNIQUE_ID_PATTERN}\") | not)]' \
    > /tmp/device_registry_clean.json && \
    mv /tmp/device_registry_clean.json $STORAGE_PATH/core.device_registry"

# 6. Spustit Home Assistant
echo "[6/6] Spouštím Home Assistant Core..."
ssh $HA_SSH "ha core start"

echo ""
echo "=============================================="
echo "  HOTOVO!"
echo "=============================================="
echo ""
echo "Smazáno: $BEFORE_COUNT entit s prefixem '${UNIQUE_ID_PATTERN}'"
echo ""
echo "Zachováno:"
echo "  ✓ OIG Cloud integrace (oig_cloud_*)"
echo "  ✓ OIG Proxy addon systémové entity (hassio)"
echo "  ✓ OIG Cloud HACS entity"
echo ""
echo "Zálohy registrů: $STORAGE_PATH/*.backup.$BACKUP_SUFFIX"
echo ""
echo "Pro spuštění OIG Proxy addon znovu použijte:"
echo "  ssh $HA_SSH ha addons start d7b5d5b1_oig_proxy"
