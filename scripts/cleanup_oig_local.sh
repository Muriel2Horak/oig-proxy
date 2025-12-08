#!/bin/bash
# =============================================================================
# Skript pro úplné vymazání OIG Proxy MQTT entit z Home Assistant
# 
# CO SE SMAŽE:
#   - MQTT retained messages (discovery, state, availability)
#   - HA entity s unique_id obsahujícím "oig_local"
#   - HA zařízení s identifikátorem obsahujícím "oig_local"
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

echo "=============================================="
echo "  Cleanup OIG Proxy MQTT entities"
echo "=============================================="
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
echo "[1/4] Zastavuji OIG Proxy addon..."
ssh $HA_SSH "ha addons stop d7b5d5b1_oig_proxy 2>/dev/null || true"
sleep 2
echo "   ✓ Addon zastaven"

# =============================================================================
# KROK 2: Smazat MQTT retained messages
# =============================================================================
echo ""
echo "[2/4] Mažu MQTT retained messages..."

# Získat seznam discovery témat
TOPICS_FILE=$(mktemp)
ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_sub -h localhost -u $MQTT_USER -P $MQTT_PASS -t 'homeassistant/#' -v --retained-only -W 3 2>/dev/null | grep oig_local | cut -d' ' -f1" > "$TOPICS_FILE" 2>/dev/null || true

TOPIC_COUNT=$(wc -l < "$TOPICS_FILE" | tr -d ' ')
echo "   Nalezeno $TOPIC_COUNT discovery témat"

if [ "$TOPIC_COUNT" -gt 0 ]; then
    while read topic; do
        if [ -n "$topic" ]; then
            ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub -h localhost -u $MQTT_USER -P $MQTT_PASS -t '$topic' -n -r"
        fi
    done < "$TOPICS_FILE"
    echo "   ✓ Discovery témata vyčištěna"
fi
rm -f "$TOPICS_FILE"

# Vyčistit state/availability témata
echo "   Čistím state/availability témata..."
ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub -h localhost -u $MQTT_USER -P $MQTT_PASS -t 'oig_local/2206237016/state' -n -r" 2>/dev/null || true
ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub -h localhost -u $MQTT_USER -P $MQTT_PASS -t 'oig_local/2206237016/availability' -n -r" 2>/dev/null || true
# Nové table-specific témata
for table in tbl_actual tbl_box_prms tbl_invertor_prms tbl_dc_in tbl_ac_in tbl_boiler; do
    ssh $HA_SSH "docker exec addon_core_mosquitto mosquitto_pub -h localhost -u $MQTT_USER -P $MQTT_PASS -t 'oig_local/2206237016/$table/state' -n -r" 2>/dev/null || true
done
echo "   ✓ State/availability témata vyčištěna"

# =============================================================================
# KROK 3: Smazat entity a zařízení z HA registrů
# =============================================================================
echo ""
echo "[3/4] Mažu entity a zařízení z HA registrů..."

# Entity registry
ssh $HA_SSH 'docker exec homeassistant python3 -c "
import json
with open(\"/config/.storage/core.entity_registry\") as f:
    data = json.load(f)
before = len(data[\"data\"][\"entities\"])
data[\"data\"][\"entities\"] = [e for e in data[\"data\"][\"entities\"] if \"oig_local\" not in e.get(\"unique_id\", \"\")]
after = len(data[\"data\"][\"entities\"])
with open(\"/config/.storage/core.entity_registry\", \"w\") as f:
    json.dump(data, f, indent=2)
print(f\"   Entity: {before} -> {after} (smazano {before - after})\")"'

# Device registry
ssh $HA_SSH 'docker exec homeassistant python3 -c "
import json
with open(\"/config/.storage/core.device_registry\") as f:
    data = json.load(f)
before = len(data[\"data\"][\"devices\"])
data[\"data\"][\"devices\"] = [d for d in data[\"data\"][\"devices\"] if not any(\"oig_local\" in str(i) for i in d.get(\"identifiers\", []))]
after = len(data[\"data\"][\"devices\"])
with open(\"/config/.storage/core.device_registry\", \"w\") as f:
    json.dump(data, f, indent=2)
print(f\"   Devices: {before} -> {after} (smazano {before - after})\")"'

echo "   ✓ Registry vyčištěny"

# =============================================================================
# KROK 4: Restartovat Home Assistant Core
# =============================================================================
echo ""
echo "[4/4] Restartuji Home Assistant Core..."
ssh $HA_SSH "ha core restart"
echo ""
echo "=============================================="
echo "  HOTOVO!"
echo "=============================================="
echo ""
echo "Počkej až HA naběhne a pak spusť addon:"
echo "  ssh $HA_SSH \"ha addons start d7b5d5b1_oig_proxy\""
echo ""
