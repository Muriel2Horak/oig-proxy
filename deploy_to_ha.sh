#!/bin/bash
set -e

echo "🚀 OIG Proxy - Deployment na HA server"
echo "========================================"
echo ""

# Konfigurace
HA_HOST="ha"
ADDON_SLUG="d7b5d5b1_oig_proxy"
CONTAINER_NAME="addon_d7b5d5b1_oig_proxy"
LOCAL_SOURCE="./addon/oig-proxy"

echo "📦 Krok 1: Backup současných souborů z kontejneru..."
ssh $HA_HOST "mkdir -p /tmp/oig-proxy-backup && \
              docker cp $CONTAINER_NAME:/app/main.py /tmp/oig-proxy-backup/ && \
              docker cp $CONTAINER_NAME:/app/sensor_map.json /tmp/oig-proxy-backup/ 2>/dev/null || true"
echo "✅ Backup vytvořen v /tmp/oig-proxy-backup/"
echo ""

echo "📋 Krok 2: Zastavení addonu..."
ssh $HA_HOST "ha addons stop $ADDON_SLUG"
echo "✅ Addon zastaven"
echo ""

echo "📤 Krok 3: Kopírování nových souborů na HA server..."
ssh $HA_HOST "mkdir -p /tmp/oig-proxy-new"

scp $LOCAL_SOURCE/config.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/models.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/utils.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/parser.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/cloud_manager.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/mqtt_publisher.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/proxy.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/main.py $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/sensor_map.json $HA_HOST:/tmp/oig-proxy-new/
scp $LOCAL_SOURCE/README_MODULAR.md $HA_HOST:/tmp/oig-proxy-new/ 2>/dev/null || true

echo "✅ Soubory zkopírovány na HA server"
echo ""

echo "🔨 Krok 4: Rebuild addonu s novými soubory..."
# Ha addon rebuild automaticky použije soubory z git repository
# Musíme je tam zkopírovat před rebuildem
ssh $HA_HOST "docker run --rm -v /var/lib/homeassistant/addons/git/d7b5d5b1/addon/oig-proxy:/target \
                          -v /tmp/oig-proxy-new:/source \
                          alpine sh -c 'cp /source/* /target/ 2>/dev/null || true' || \
              echo 'Zkouším alternativní metodu...' && \
              sudo cp /tmp/oig-proxy-new/* /var/lib/homeassistant/addons/git/d7b5d5b1/addon/oig-proxy/ || \
              echo '⚠️  Nelze zkopírovat do git repo, použiji přímý update do kontejneru'"

ssh $HA_HOST "ha addons rebuild $ADDON_SLUG"
echo "✅ Addon rebuilded"
echo ""

echo "▶️  Krok 5: Start addonu..."
ssh $HA_HOST "ha addons start $ADDON_SLUG"
echo "✅ Addon nastartován"
echo ""

echo "⏳ Čekám 5 sekund na inicializaci..."
sleep 5
echo ""

echo "📊 Krok 6: Kontrola stavu..."
ssh $HA_HOST "ha addons info $ADDON_SLUG | grep -E 'state:|version:'"
echo ""

echo "📋 Poslední logy:"
ssh $HA_HOST "ha addons logs $ADDON_SLUG | tail -30"
echo ""

echo "✅ Deployment dokončen!"
echo ""
echo "Pro sledování logů použij:"
echo "  ssh ha \"ha addons logs $ADDON_SLUG -f\""
echo ""
echo "Pro rollback použij:"
echo "  ssh ha \"ha addons stop $ADDON_SLUG && \\"
echo "            cp /tmp/oig-proxy-backup/main.py /var/lib/homeassistant/addons/git/d7b5d5b1/addon/oig-proxy/ && \\"
echo "            ha addons rebuild $ADDON_SLUG && \\"
echo "            ha addons start $ADDON_SLUG\""
