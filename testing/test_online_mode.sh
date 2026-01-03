#!/bin/bash
# Test 1: ONLINE Mode (smoke test)
#
# Scénář:
# 1. Spustit mock cloud server
# 2. Spustit proxy
# 3. Poslat frames z BOXu
# 4. Validovat že vše prošlo transparentně

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

find_free_port() {
    "${PYTHON_BIN}" - <<'PY'
import socket
s = socket.socket()
s.bind(("", 0))
print(s.getsockname()[1])
s.close()
PY
}

CLOUD_PORT="${CLOUD_PORT:-$(find_free_port)}"
PROXY_PORT="${PROXY_PORT:-$(find_free_port)}"
if [ "$PROXY_PORT" = "$CLOUD_PORT" ]; then
    PROXY_PORT="$(find_free_port)"
fi

echo "========================================="
echo "TEST 1: ONLINE MODE (SMOKE TEST)"
echo "========================================="
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

FAILURES=0
fail() {
    echo "${RED}✗ $*${NC}"
    FAILURES=$((FAILURES + 1))
}
pass() {
    echo "${GREEN}✓ $*${NC}"
}

queue_count() {
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 /tmp/cloud_queue.db 'SELECT COUNT(*) FROM queue' 2>/dev/null || echo "0"
        return
    fi
    "${PYTHON_BIN}" - <<'PY'
import sqlite3
try:
    conn = sqlite3.connect("/tmp/cloud_queue.db")
    cur = conn.execute("SELECT COUNT(*) FROM queue")
    print(cur.fetchone()[0])
except Exception:
    print("0")
PY
}

cleanup() {
    echo ""
    echo "${YELLOW}Cleaning up...${NC}"
    pkill -f "mock_cloud_server.py" 2>/dev/null || true
    pkill -f "mock_box_client.py" 2>/dev/null || true
    pkill -f "main.py" 2>/dev/null || true
    sleep 2
}

trap cleanup EXIT

# Step 1: Start mock cloud
echo "${YELLOW}Step 1: Starting mock cloud server...${NC}"
rm -f mock_cloud_frames.json
"${PYTHON_BIN}" mock_cloud_server.py --port "${CLOUD_PORT}" > /tmp/cloud_test1.log 2>&1 &
CLOUD_PID=$!
sleep 2
pass "Cloud server started (PID: $CLOUD_PID)"

# Step 2: Start proxy
echo ""
echo "${YELLOW}Step 2: Starting proxy...${NC}"
cd ../addon/oig-proxy
export DEVICE_ID=TEST001
export DATA_DIR=/tmp
export MQTT_HOST=localhost
export TARGET_SERVER=localhost
export TARGET_PORT="${CLOUD_PORT}"
export PROXY_PORT="${PROXY_PORT}"
export LOG_LEVEL=INFO

# Clean queues
rm -f /tmp/cloud_queue.db /tmp/mqtt_queue.db /tmp/payloads.db

"${PYTHON_BIN}" main.py > /tmp/proxy_test1.log 2>&1 &
PROXY_PID=$!
cd "$SCRIPT_DIR"

sleep 3
pass "Proxy started (PID: $PROXY_PID)"

# Step 3: Send frames
echo ""
echo "${YELLOW}Step 3: Sending 100 frames from BOX...${NC}"

"${PYTHON_BIN}" mock_box_client.py \
    --data test_data/test_data/box_frames_100.json \
    --proxy-host localhost \
    --proxy-port "${PROXY_PORT}" \
    --rate-limit 0.1 \
    > /tmp/box_test1.log 2>&1

pass "Frames sent"

# Stop cloud to flush stats
echo ""
echo "${YELLOW}Step 3b: Stopping mock cloud to flush stats...${NC}"
kill -TERM "${CLOUD_PID}" 2>/dev/null || true
wait "${CLOUD_PID}" 2>/dev/null || true
for _ in $(seq 1 25); do
    if [ -f mock_cloud_frames.json ]; then
        break
    fi
    sleep 0.2
done

# Step 4: Validation
echo ""
echo "========================================="
echo "VALIDATION"
echo "========================================="

# Check success rate from BOX
BOX_SENT=$(grep "Frames sent:" /tmp/box_test1.log 2>/dev/null | awk '{print $NF}' || true)
BOX_ACKS=$(grep "ACKs received:" /tmp/box_test1.log 2>/dev/null | awk '{print $NF}' || true)
echo "BOX: Sent $BOX_SENT, ACKs $BOX_ACKS"

if [ -n "$BOX_SENT" ] && [ "$BOX_SENT" = "$BOX_ACKS" ]; then
    pass "100% ACK rate"
else
    fail "Missing ACKs"
fi

# Check cloud received
CLOUD_FRAMES=$("${PYTHON_BIN}" - <<'PY'
import json
from pathlib import Path
p = Path("mock_cloud_frames.json")
if not p.exists():
    print(0)
    raise SystemExit
try:
    stats = json.loads(p.read_text())
except Exception:
    print(0)
    raise SystemExit
print(stats.get("total_frames", 0))
PY
)

echo "Cloud received: $CLOUD_FRAMES frames"

if [ "$BOX_SENT" = "$CLOUD_FRAMES" ]; then
    pass "All frames delivered to cloud"
else
    fail "Frame loss"
fi

# Check queue (should be empty in ONLINE mode)
QUEUE_SIZE=$(queue_count)
echo "Queue size: $QUEUE_SIZE"

if [ "$QUEUE_SIZE" = "0" ]; then
    pass "Queue empty (direct forward)"
else
    fail "Unexpected queuing"
fi

echo ""
echo "========================================="
echo "TEST 1 COMPLETE"
echo "========================================="
echo ""
echo "Logs:"
echo "  Cloud:  /tmp/cloud_test1.log"
echo "  Proxy:  /tmp/proxy_test1.log"
echo "  BOX:    /tmp/box_test1.log"
echo "  Stats:  mock_cloud_frames.json"

if [ "$FAILURES" -gt 0 ]; then
    exit 1
fi
