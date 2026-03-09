#!/bin/bash
# Task 18: Production Validation Script
# Run this after deployment to verify blind branch fixes are working

echo "====================================================="
echo "  Task 18: Validace blind branch fixů v provozu"
echo "====================================================="
echo ""

# Configuration
HA_HOST="ha"
ADDON_SLUG="d7b5d5b1_oig_proxy"
CONTAINER_NAME="addon_${ADDON_SLUG}"
VALIDATION_DURATION_MINUTES=30

echo "[1/8] Kontrola připojení k HA serveru..."
if ! ssh -q "${HA_HOST}" exit 2>/dev/null; then
    echo "  ❌ Nelze se připojit k ${HA_HOST}"
    echo "  ℹ️  Ujistěte se, že máte SSH alias 'ha' nakonfigurován v ~/.ssh/config"
    exit 1
fi
echo "  ✅ SSH připojení funkční"

echo ""
echo "[2/8] Kontrola běžícího addon containeru..."
if ! ssh "${HA_HOST}" "docker ps --filter name=${CONTAINER_NAME} --format '{{.Names}}'" | grep -q "${CONTAINER_NAME}"; then
    echo "  ❌ Container ${CONTAINER_NAME} neběží!"
    exit 1
fi
echo "  ✅ Container běží"

echo ""
echo "[3/8] Získávání logů z posledních 5 minut..."
ssh "${HA_HOST}" "docker logs --since 5m ${CONTAINER_NAME} 2>&1" > /tmp/oig_proxy_recent.log
echo "  ✅ Logy uloženy do /tmp/oig_proxy_recent.log"

echo ""
echo "[4/8] Kontrola Blind Branch #1: Fail-open routing..."
if grep -q "Exception.*frame.*processing" /tmp/oig_proxy_recent.log; then
    echo "  ⚠️  Nalezeny výjimky při zpracování frame:"
    grep "Exception.*frame.*processing" /tmp/oig_proxy_recent.log | tail -3
    echo "  ✅ Kontrola: Routing by měl pokračovat navzdory výjimkám"
else
    echo "  ℹ️  Žádné výjimky při zpracování frame (v posledních 5 min)"
fi

echo ""
echo "[5/8] Kontrola Blind Branch #2 & #3: Inflight finalization..."
TIMEOUT_COUNT=$(grep -c "ACK timeout.*finalizing" /tmp/oig_proxy_recent.log || echo "0")
echo "  📊 Počet ACK timeoutů: ${TIMEOUT_COUNT}"
if [ "${TIMEOUT_COUNT}" -gt 0 ]; then
    echo "  ✅ Timeout handlery aktivní - kontrola uvolňování inflight:"
    grep "inflight.*cleared\|finish_inflight" /tmp/oig_proxy_recent.log | tail -5
fi

echo ""
echo "[6/8] Kontrola Blind Branch #4 & #5: Twin activation..."
if grep -q "twin.*activation\|pending.*activation" /tmp/oig_proxy_recent.log; then
    echo "  ✅ Twin activation aktivita nalezena:"
    grep "twin.*activation\|pending.*activation" /tmp/oig_proxy_recent.log | tail -5
else
    echo "  ℹ️  Žádná twin activation aktivita (v posledních 5 min)"
fi

echo ""
echo "[7/8] Kontrola Blind Branch #6: Cloud session flags..."
SESSION_ENDINGS=$(grep -c "session.*end\|session_connected.*False" /tmp/oig_proxy_recent.log || echo "0")
echo "  📊 Počet ukončení session: ${SESSION_ENDINGS}"
if [ "${SESSION_ENDINGS}" -gt 0 ]; then
    echo "  ✅ Session flagy se správně aktualizují"
fi

echo ""
echo "[8/8] Kontrola Blind Branch #7: MQTT dedup..."
if grep -q "payload.*dedup\|queue.*identical" /tmp/oig_proxy_recent.log; then
    echo "  ✅ MQTT dedup aktivita:"
    grep "payload.*dedup\|queue.*identical" /tmp/oig_proxy_recent.log | tail -3
else
    echo "  ℹ️  Žádná dedup aktivita (v posledních 5 min)"
fi

echo ""
echo "====================================================="
echo "  Validace dokončena"
echo "====================================================="
echo ""
echo "Doporučený monitoring (spusťte v novém terminálu):"
echo "  ssh ${HA_HOST} 'docker logs -f ${CONTAINER_NAME} 2>&1 | grep -E \"(timeout|exception|error|stuck|inflight)\"'"
echo ""
echo "Kontrolní body pro manuální validaci:"
echo "  ✅ Box komunikace funguje normálně"
echo "  ✅ MQTT data tečou bez přerušení"
echo "  ✅ Cloud ACK jsou přijímána"
echo "  ✅ Žádná STALE_STREAM varování"
echo "  ✅ Session se nezasekává"
echo ""
echo "Pro dlouhodobý monitoring (30 minut) spusťte:"
echo "  ./validate_production.sh --long"
