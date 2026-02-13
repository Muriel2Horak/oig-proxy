#!/bin/bash
set -e

echo "====================================================="
echo "  OIG Proxy - Deployment na HAOS server (SMB/SSH)"
echo "====================================================="
echo ""

# ── Konfigurace ──────────────────────────────────────────
HA_HOST="ha"                                    # SSH alias
ADDON_SLUG="d7b5d5b1_oig_proxy"
CONTAINER_NAME="addon_${ADDON_SLUG}"
LOCAL_SOURCE="./addon/oig-proxy"
# Cesta na HOST filesystemu (pristupna pres docker -v /:/host)
HOST_ADDON_DIR="/mnt/data/supervisor/addons/git/d7b5d5b1/addon/oig-proxy"
TMP_DIR="/tmp/oig-proxy-deploy-$$"

# Python soubory + konfigurace k deployi (vše z addon/oig-proxy)
DEPLOY_FILES=(
  config.py
  models.py
  utils.py
  parser.py
  cloud_manager.py
  cloud_session.py
  cloud_forwarder.py
  mqtt_publisher.py
  mqtt_state_cache.py
  proxy.py
  proxy_status.py
  telemetry_client.py
  telemetry_collector.py
  control_api.py
  control_pipeline.py
  control_settings.py
  local_oig_crc.py
  oig_frame.py
  main.py
  backoff.py
  db_utils.py
  hybrid_mode.py
  mode_persistence.py
  config.json
  sensor_map.json
  Dockerfile
  requirements.txt
  run
)

# ── Pomocné funkce ───────────────────────────────────────

# Spustí příkaz na HAOS hostu přes docker (obejde kontejnerový sandbox SSH addonu)
host_run() {
  ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c '$1'"
}

# Přenos souboru na HA server přes SSH stdin (SCP/SFTP není dostupné v SSH addonu)
ssh_upload() {
  local local_file="$1"
  local remote_path="$2"
  cat "$local_file" | ssh "$HA_HOST" "cat > $remote_path"
}

# Spustí ha CLI příkaz (potřebuje SUPERVISOR_TOKEN z profilu)
ha_cli() {
  ssh "$HA_HOST" "source /etc/profile.d/homeassistant.sh && ha apps $*"
}

# Zjistí nainstalovanou verzi addonu z docker image tagu
get_installed_version() {
  ssh "$HA_HOST" "sudo docker inspect $CONTAINER_NAME --format '{{.Config.Image}}'" \
    | sed 's/.*://'
}

# Patchne config.json na serveru - nastaví "version" na nainstalovanou verzi
# Tím zajistíme, že Supervisor povolí rebuild (verze musí sedět)
patch_version_on_server() {
  local installed_ver="$1"
  echo "   Patchuji config.json na serveru: version -> $installed_ver"
  ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c '
    cd /host${HOST_ADDON_DIR} && \
    sed -i \"s/\\\"version\\\": \\\"[^\\\"]*\\\"/\\\"version\\\": \\\"${installed_ver}\\\"/\" config.json
  '"
  # Ověření
  local patched_ver
  patched_ver=$(ssh "$HA_HOST" "sudo docker run --rm -v /:/host alpine sh -c '
    grep -o \"\\\"version\\\": \\\"[^\\\"]*\\\"\" /host${HOST_ADDON_DIR}/config.json
  '" | head -1)
  echo "   Ověření: $patched_ver"
}

# ── Main ─────────────────────────────────────────────────

# Krok 0: Zjistit nainstalovanou verzi
echo "[0/7] Zjistuji nainstalovanou verzi addonu..."
INSTALLED_VER=$(get_installed_version)
LOCAL_VER=$(python3 -c "import json; print(json.load(open('$LOCAL_SOURCE/config.json'))['version'])")
echo "   Nainstalovaná verze (docker image): $INSTALLED_VER"
echo "   Lokální verze (repo):               $LOCAL_VER"
if [ "$INSTALLED_VER" != "$LOCAL_VER" ]; then
  echo "   -> Verze se liší - config.json bude patchnut na serveru na $INSTALLED_VER"
else
  echo "   -> Verze se shodují"
fi
echo ""

# Krok 1: Backup
echo "[1/7] Backup soucasnych souboru na serveru..."
host_run "mkdir -p /host/tmp/oig-proxy-backup && \
  cp /host${HOST_ADDON_DIR}/proxy.py     /host/tmp/oig-proxy-backup/ 2>/dev/null; \
  cp /host${HOST_ADDON_DIR}/main.py      /host/tmp/oig-proxy-backup/ 2>/dev/null; \
  cp /host${HOST_ADDON_DIR}/config.json  /host/tmp/oig-proxy-backup/ 2>/dev/null; \
  true"
echo "   Backup v /tmp/oig-proxy-backup/ na HA serveru"
echo ""

# Krok 2: Upload souborů na HA server (PŘED zastavením addonu!)
# SSH addon nemá SFTP, používáme ssh stdin redirect (cat | ssh cat >)
echo "[2/7] Kopíruji soubory na HA server..."
ssh "$HA_HOST" "mkdir -p $TMP_DIR"
UPLOAD_COUNT=0
for f in "${DEPLOY_FILES[@]}"; do
  if [ -f "$LOCAL_SOURCE/$f" ]; then
    ssh_upload "$LOCAL_SOURCE/$f" "$TMP_DIR/$f"
    echo "   + $f"
    UPLOAD_COUNT=$((UPLOAD_COUNT + 1))
  else
    echo "   - $f (neexistuje, preskakuji)"
  fi
done
echo "   Nahrano $UPLOAD_COUNT souboru"
echo ""

# Krok 3: Zastavení addonu
echo "[3/7] Zastavuji addon..."
ssh "$HA_HOST" "sudo docker stop $CONTAINER_NAME" 2>/dev/null && echo "   Addon zastaven" \
  || echo "   Addon byl jiz zastaveny"
echo ""

# Krok 4: Instalace souborů do addon adresáře na hostu
echo "[4/7] Instaluji soubory do addon adresare..."
ssh "$HA_HOST" "sudo docker run --rm \
  -v /:/host \
  -v $TMP_DIR:/source:ro \
  alpine sh -c 'cp /source/* /host${HOST_ADDON_DIR}/ 2>/dev/null || true'"
echo "   Soubory nainstalovany"

# Patch verze v config.json na serveru (pokud se liší)
if [ "$INSTALLED_VER" != "$LOCAL_VER" ]; then
  echo ""
  echo "   ** Patch verze v config.json na serveru **"
  patch_version_on_server "$INSTALLED_VER"
fi

# Úklid tmp
ssh "$HA_HOST" "rm -rf $TMP_DIR" 2>/dev/null || true
echo ""

# Krok 5: Rebuild addon
echo "[5/7] Rebuild addonu..."
ha_cli rebuild $ADDON_SLUG 2>&1 && echo "   Addon rebuildovan" \
  || {
    echo "   Rebuild selhal."
    echo "   Zkousim alternativne: docker start..."
    ssh "$HA_HOST" "sudo docker start $CONTAINER_NAME" 2>&1
  }

echo ""

# Krok 6: Start addonu
echo "[6/7] Startuji addon..."
ha_cli start $ADDON_SLUG 2>&1 && echo "   Addon nastartovan" \
  || echo "   Start pres ha CLI selhal (addon mozna jiz bezi po rebuild)"

echo ""
echo "   Cekam 5 sekund na inicializaci..."
sleep 5

# Krok 7: Kontrola
echo "[7/7] Kontrola stavu..."
STATUS=$(ssh "$HA_HOST" "sudo docker inspect $CONTAINER_NAME --format '{{.State.Status}}'" 2>/dev/null || echo "unknown")
echo "   Stav kontejneru: $STATUS"
echo ""

if [ "$STATUS" = "running" ]; then
  echo "Posledni logy:"
  echo "-----------------------------------------------------"
  ssh "$HA_HOST" "sudo docker logs $CONTAINER_NAME --tail 30" 2>&1
  echo "-----------------------------------------------------"
  echo ""
  echo "Deployment USPESNY!"
else
  echo "VAROVANI: Kontejner nebezi (stav: $STATUS)"
  echo "Zkontroluj logy:"
  echo "  ssh ha \"sudo docker logs $CONTAINER_NAME --tail 50\""
  echo ""
  echo "Pro rollback:"
  echo "  ssh ha \"sudo docker run --rm -v /:/host alpine sh -c \\"
  echo "    'cp /host/tmp/oig-proxy-backup/* /host${HOST_ADDON_DIR}/' && \\"
  echo "    sudo docker start $CONTAINER_NAME\""
fi

echo ""
echo "Uzitecne prikazy:"
echo "  Logy:     ssh ha \"sudo docker logs $CONTAINER_NAME -f --tail 50\""
echo "  Restart:  ssh ha \"sudo docker restart $CONTAINER_NAME\""
echo "  Rollback: ssh ha \"sudo docker run --rm -v /:/host alpine sh -c \\"
echo "              'cp /host/tmp/oig-proxy-backup/* /host${HOST_ADDON_DIR}/'\""
