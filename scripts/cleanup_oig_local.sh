#!/bin/bash
# =============================================================================
# Skript pro úplné vymazání oig_local entit z Home Assistant
# Zachová oig_cloud entity!
# =============================================================================

set -e

# Konfigurace - uprav podle potřeby
HA_SSH="ha"  # SSH alias pro Home Assistant
MQTT_USER="oig"
MQTT_PASS="oig"
NAMESPACE="oig_local"  # Namespace používaný v oig-proxy addonu

echo "=== Cleanup OIG Local entities ==="
echo "Namespace: $NAMESPACE"
echo "Tento skript smaže POUZE entity s prefixem '$NAMESPACE'"
echo ""

# 1. Zastavit OIG Proxy addon (pokud běží)
echo "[1/6] Zastavuji OIG Proxy addon..."
ssh $HA_SSH "ha addons stop local_oig_proxy 2>/dev/null || true"
sleep 2

# 2. Získat seznam všech retained MQTT topicků s oig_local
echo "[2/6] Hledám MQTT discovery topicy pro $NAMESPACE..."
TOPICS=$(ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_sub \
    -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
    -t 'homeassistant/sensor/${NAMESPACE}_#' \
    --retained-only -W 3 -v 2>/dev/null | grep '/config' | cut -d' ' -f1" || echo "")

if [ -z "$TOPICS" ]; then
    echo "   Žádné MQTT discovery topicy nenalezeny."
else
    echo "   Nalezeno $(echo "$TOPICS" | wc -l | tr -d ' ') topiců"
fi

# 3. Smazat retained MQTT zprávy
echo "[3/6] Mažu retained MQTT zprávy..."
if [ -n "$TOPICS" ]; then
    for topic in $TOPICS; do
        echo "   Mažu: $topic"
        ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub \
            -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
            -r -n -t '$topic'"
    done
fi

# Smazat i state a availability topicy
echo "   Mažu state/availability topicy..."
ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_sub \
    -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
    -t '${NAMESPACE}/#' --retained-only -W 2 -v 2>/dev/null | cut -d' ' -f1 | \
    while read topic; do
        docker exec addon_core_mosquitto mosquitto_pub \
            -h localhost -p 1883 -u $MQTT_USER -P $MQTT_PASS \
            -r -n -t \"\$topic\"
    done" || true

# 4. Zastavit Home Assistant Core
echo "[4/6] Zastavuji Home Assistant Core..."
ssh $HA_SSH "ha core stop"
sleep 5

# 5. Smazat entity z registry (pouze oig_local)
echo "[5/6] Odstraňuji $NAMESPACE entity z HA registry..."

# Backup registrů
echo "   Vytvářím zálohu registrů..."
ssh $HA_SSH "cp /config/.storage/core.entity_registry /config/.storage/core.entity_registry.backup.$(date +%Y%m%d_%H%M%S)"
ssh $HA_SSH "cp /config/.storage/core.device_registry /config/.storage/core.device_registry.backup.$(date +%Y%m%d_%H%M%S)"

# Smazat entity obsahující oig_local (ale ne oig_cloud)
echo "   Mažu entity s unique_id obsahujícím '$NAMESPACE'..."
ssh $HA_SSH "cat /config/.storage/core.entity_registry | \
    jq 'if .data.entities then .data.entities = [.data.entities[] | select(.unique_id | tostring | contains(\"${NAMESPACE}\") | not)] else . end' \
    > /tmp/entity_registry_clean.json && \
    mv /tmp/entity_registry_clean.json /config/.storage/core.entity_registry"

# Smazat zařízení obsahující oig_local
echo "   Mažu zařízení s identifikátorem obsahujícím '$NAMESPACE'..."
ssh $HA_SSH "cat /config/.storage/core.device_registry | \
    jq 'if .data.devices then .data.devices = [.data.devices[] | select(.identifiers | tostring | contains(\"${NAMESPACE}\") | not)] else . end' \
    > /tmp/device_registry_clean.json && \
    mv /tmp/device_registry_clean.json /config/.storage/core.device_registry"

# 6. Spustit Home Assistant
echo "[6/6] Spouštím Home Assistant Core..."
ssh $HA_SSH "ha core start"

echo ""
echo "=== Hotovo! ==="
echo "Entity s prefixem '$NAMESPACE' byly odstraněny."
echo "Entity 'oig_cloud' zůstaly zachovány."
echo ""
echo "Zálohy registrů uloženy v /config/.storage/"
echo ""
echo "Pro spuštění OIG Proxy addon znovu použijte:"
echo "  ssh $HA_SSH ha addons start local_oig_proxy"
