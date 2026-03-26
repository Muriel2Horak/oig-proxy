#!/bin/bash
set -e

echo "====================================================="
echo " OIG Proxy - Deployment na HAOS server"
echo "====================================================="
echo ""

# ── Konfigurace ──────────────────────────────────────────
HA_HOST="ha"  # SSH alias z ~/.ssh/config
ADDON_SLUG="d7b5d5b1_oig_proxy"
CONTAINER_NAME="addon_${ADDON_SLUG}"
LOCAL_SOURCE="./addon/oig-proxy"

HOST_ADDON_BASE="/mnt/data/supervisor/addons/git/d7b5d5b1/addon"
HOST_ADDON_DIR_LEGACY="${HOST_ADDON_BASE}/oig-proxy"
HOST_ADDON_DIR_V2="${HOST_ADDON_BASE}/oig-proxy-v2"
HOST_ADDON_DIR=""

# Soubory k deployi
DEPLOY_FILES=(
    __init__.py
    config.py
    device_id.py
    settings_constraints.py
    logging_config.py
    main.py
    run
    sensor_map.json
    config.json
    Dockerfile
    requirements.txt
    mqtt/__init__.py
    mqtt/client.py
    mqtt/status.py
    proxy/__init__.py
    proxy/server.py
    proxy/mode.py
    proxy/local_ack.py
    protocol/__init__.py
    protocol/parser.py
    protocol/frame.py
    protocol/crc.py
    protocol/frames.py
    sensor/__init__.py
    sensor/loader.py
    sensor/warnings.py
    sensor/processor.py
    twin/__init__.py
    twin/state.py
    twin/handler.py
    twin/delivery.py
    twin/ack_parser.py
    telemetry/__init__.py
    telemetry/collector.py
    telemetry/client.py
    capture/__init__.py
    capture/frame_capture.py
    capture/pcap_capture.py
)

# ── Pomocné funkce ───────────────────────────────────────

host_run() {
    ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c '$1'"
}

ha_cli() {
    ssh "$HA_HOST" "source /etc/profile.d/homeassistant.sh && ha $*"
}

# ── Main ─────────────────────────────────────────────────

echo "[0/7] Kontrola struktury..."
if [ ! -d "$LOCAL_SOURCE" ]; then
    echo "CHYBA: $LOCAL_SOURCE neexistuje!"
    exit 1
fi

echo "Detekuji aktivní addon adresář na HA hostu..."
if ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c 'test -f /host${HOST_ADDON_DIR_LEGACY}/config.json'"; then
    HOST_ADDON_DIR="$HOST_ADDON_DIR_LEGACY"
elif ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c 'test -f /host${HOST_ADDON_DIR_V2}/config.json'"; then
    HOST_ADDON_DIR="$HOST_ADDON_DIR_V2"
else
    HOST_ADDON_DIR="$HOST_ADDON_DIR_LEGACY"
fi

echo " Cílový addon adresář: $HOST_ADDON_DIR"
echo ""

# Krok 1: Backup
echo "[1/7] Backup současných souborů..."
host_run "mkdir -p /host/tmp/oig-proxy-backup && \
cp -r /host${HOST_ADDON_DIR}/* /host/tmp/oig-proxy-backup/ 2>/dev/null || true"
echo " Backup v /tmp/oig-proxy-backup/"
echo ""

# Krok 2: Vytvoření adresářové struktury
echo "[2/7] Vytvářím adresářovou strukturu..."
ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c 'mkdir -p \
  /host${HOST_ADDON_DIR}/mqtt \
  /host${HOST_ADDON_DIR}/proxy \
  /host${HOST_ADDON_DIR}/protocol \
  /host${HOST_ADDON_DIR}/sensor \
  /host${HOST_ADDON_DIR}/twin \
  /host${HOST_ADDON_DIR}/telemetry \
  /host${HOST_ADDON_DIR}/capture'"
echo " Adresáře vytvořeny"
echo ""

# Krok 3: Upload souborů
echo "[3/7] Kopíruji soubory..."
UPLOAD_COUNT=0
UPLOAD_FAILED=0

for f in "${DEPLOY_FILES[@]}"; do
    if [ -f "$LOCAL_SOURCE/$f" ]; then
        if cat "$LOCAL_SOURCE/$f" | ssh "$HA_HOST" "sudo docker run --rm -i -v /:/host alpine sh -c 'cat > /host${HOST_ADDON_DIR}/$f'" 2>/dev/null; then
            echo "  + $f"
            UPLOAD_COUNT=$((UPLOAD_COUNT + 1))
        else
            echo "  ! $f (upload selhal)"
            UPLOAD_FAILED=$((UPLOAD_FAILED + 1))
        fi
    else
        echo "  - $f (neexistuje)"
    fi
done

echo " Nahráno $UPLOAD_COUNT souborů"
if [ $UPLOAD_FAILED -gt 0 ]; then
    echo " CHYBA: $UPLOAD_FAILED souborů se nepodařilo nahrát!"
    exit 1
fi
echo ""

# Krok 4: Ověření uploadu
echo "[4/7] Ověřuji nahrané soubory..."
VERIFY=$(ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c 'ls /host${HOST_ADDON_DIR}/main.py /host${HOST_ADDON_DIR}/config.json /host${HOST_ADDON_DIR}/sensor_map.json 2>&1'")
if echo "$VERIFY" | grep -q "No such file"; then
    echo " CHYBA: Některé soubory nebyly správně nahrány!"
    exit 1
fi
echo " Soubory ověřeny ✓"
echo ""

# Krok 5: Zastavení addonu
echo "[5/7] Zastavuji addon..."
ssh "$HA_HOST" "sudo docker stop $CONTAINER_NAME" 2>/dev/null && echo " Addon zastaven" \
|| echo " Addon byl již zastaven"
echo ""

# Krok 6: Rebuild a start
echo "[6/7] Rebuild addonu..."
ha_cli addons rebuild $ADDON_SLUG 2>&1 && echo " Rebuild OK" || echo " Rebuild selhal (možná není potřeba)"
echo ""

echo " Startuji addon..."
ha_cli addons start $ADDON_SLUG 2>&1 && echo " Addon nastartován" || echo " Start selhal"
echo ""

echo " Čekám 5 sekund na inicializaci..."
sleep 5
echo ""

# Krok 7: Kontrola
echo "[7/7] Kontrola stavu..."
STATUS=$(ssh "$HA_HOST" "sudo docker inspect $CONTAINER_NAME --format '{{.State.Status}}'" 2>/dev/null || echo "unknown")
echo " Stav kontejneru: $STATUS"
echo ""

if [ "$STATUS" = "running" ]; then
    echo "Poslední logy:"
    echo "-----------------------------------------------------"
    ssh "$HA_HOST" "sudo docker logs $CONTAINER_NAME --tail 30" 2>&1 | head -30
    echo "-----------------------------------------------------"
    echo ""
    echo "✓ Deployment ÚSPĚŠNÝ!"
    echo ""
    echo "Ověření:"
    echo "  ssh ha \"ha addons info $ADDON_SLUG | grep state\""
    echo "  ssh ha \"sudo docker logs $CONTAINER_NAME -f --tail 50\""
else
    echo "✗ VAROVÁNÍ: Kontejner neběží (stav: $STATUS)"
    echo ""
    echo "Zkontroluj logy:"
    echo "  ssh ha \"sudo docker logs $CONTAINER_NAME --tail 50\""
    echo ""
    echo "Rollback:"
    echo "  ssh ha \"sudo docker run --rm -v /:/host alpine sh -c 'cp -r /host/tmp/oig-proxy-backup/* /host${HOST_ADDON_DIR}/'\""
fi

echo ""
echo "Užitečné příkazy:"
echo "  Logy:      ssh ha \"sudo docker logs $CONTAINER_NAME -f --tail 50\""
echo "  Restart:   ssh ha \"ha addons restart $ADDON_SLUG\""
echo "  Stop:      ssh ha \"ha addons stop $ADDON_SLUG\""
echo "  Info:      ssh ha \"ha addons info $ADDON_SLUG\""
