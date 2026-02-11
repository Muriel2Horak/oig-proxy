#!/bin/bash
# Test 3: REPLAY Mode - klíčový test!
#
# Scénář:
# 1. Naplnit frontu v OFFLINE režimu
# 2. Spustit cloud server (recovery)
# 3. Čekat na auto-přechod do REPLAY
# 4. Paralelně posílat nové live frames
# 5. Validovat FIFO pořadí a přechod do ONLINE


SEPARATOR="========================================="
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
if [[ "$PROXY_PORT" = "$CLOUD_PORT" ]]; then
    PROXY_PORT="$(find_free_port)"
fi

echo "$SEPARATOR"
echo "TEST 3: REPLAY MODE"
echo "$SEPARATOR"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

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
        return 0
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

wait_for_replay() {
    local deadline=$((SECONDS + 60))
    while [ $SECONDS -lt $deadline ]]; do
        if grep -Eq "Mode changed: .*replay" /tmp/proxy_test3.log; then
            return 0
        fi
        sleep 1
    done
    return 1
}

wait_for_queue_empty() {
    local deadline=$((SECONDS + 120))
    while [ $SECONDS -lt $deadline ]]; do
        COUNT=$(queue_count)
        echo "  Queue size: $COUNT"
        if [[ "$COUNT" = "0" ]]; then
            return 0
        fi
        sleep 5
    done
    return 1
}

wait_for_pid() {
    local pid="$1"
    local deadline=$((SECONDS + 30))
    while kill -0 "$pid" 2>/dev/null; do
        if [ $SECONDS -ge $deadline ]]; then
            return 1
        fi
        sleep 1
    done
    wait "$pid" 2>/dev/null || return 1
    return 0
}

# Cleanup function
cleanup() {
    echo ""
    echo "${YELLOW}Cleaning up...${NC}"
    pkill -f "mock_cloud_server.py" 2>/dev/null || true
    pkill -f "mock_box_client.py" 2>/dev/null || true
    pkill -f "main.py" 2>/dev/null || true
    sleep 2
}

trap cleanup EXIT

# Step 1: Start proxy (cloud will be offline)
echo "${YELLOW}Step 1: Starting proxy (cloud offline)...${NC}"
cd ../addon/oig-proxy
export DEVICE_ID=TEST001
export DATA_DIR=/tmp
export MQTT_HOST=localhost
export TARGET_SERVER=localhost
export TARGET_PORT="${CLOUD_PORT}"
export PROXY_PORT="${PROXY_PORT}"
export LOG_LEVEL=DEBUG
export CAPTURE_PAYLOADS=false
export CLOUD_REPLAY_RATE=20
export MQTT_REPLAY_RATE=50

# Clean queues
rm -f /tmp/cloud_queue.db /tmp/mqtt_queue.db /tmp/payloads.db

"${PYTHON_BIN}" main.py > /tmp/proxy_test3.log 2>&1 &
PROXY_PID=$!
cd "$SCRIPT_DIR"

sleep 3
pass "Proxy started (PID: $PROXY_PID)"

# Step 2: Send frames OFFLINE (no cloud) → fill queue
echo ""
echo "${YELLOW}Step 2: Sending frames OFFLINE (fill queue)...${NC}"
echo "  → Expecting: Local ACK, frames queued"

"${PYTHON_BIN}" mock_box_client.py \
    --data test_data/test_data/box_frames_5min.json \
    --proxy-host localhost \
    --proxy-port "${PROXY_PORT}" \
    --rate-limit 0.05 \
    > /tmp/box_offline.log 2>&1

pass "Queued $(queue_count) frames"

# Step 3: Start cloud server (recovery!)
echo ""
echo "${YELLOW}Step 3: Starting cloud server (recovery)...${NC}"
rm -f mock_cloud_frames.json
"${PYTHON_BIN}" mock_cloud_server.py --port "${CLOUD_PORT}" > /tmp/cloud_test3.log 2>&1 &
CLOUD_PID=$!
sleep 2
pass "Cloud server started (PID: $CLOUD_PID)"

# Step 4: Wait for replay to start
echo ""
echo "${YELLOW}Step 4: Waiting for REPLAY mode...${NC}"
echo "  → Health check should detect cloud (30s max)"

if wait_for_replay; then
    pass "REPLAY mode activated"
else
    fail "Timeout waiting for REPLAY"
fi

# Step 5: Send NEW live frames during replay
echo ""
echo "${YELLOW}Step 5: Sending LIVE frames during replay...${NC}"
echo "  → Should go to end of queue (FIFO)"

sleep 2  # Let some replay happen first

"${PYTHON_BIN}" mock_box_client.py \
    --data test_data/test_data/box_frames_actual.json \
    --proxy-host localhost \
    --proxy-port "${PROXY_PORT}" \
    --rate-limit 0.2 \
    > /tmp/box_live.log 2>&1 &
BOX_LIVE_PID=$!

# Step 6: Wait for queue to empty
echo ""
echo "${YELLOW}Step 6: Waiting for queue to empty...${NC}"

if wait_for_queue_empty; then
    pass "Queue empty"
else
    fail "Timeout waiting for queue to empty"
fi

# Step 7: Check ONLINE mode transition
echo ""
echo "${YELLOW}Step 7: Checking ONLINE mode transition...${NC}"

if grep -q "Replay complete" /tmp/proxy_test3.log; then
    pass "Switched to ONLINE mode"
else
    fail "No ONLINE mode detected"
fi

if [ -n "${BOX_LIVE_PID:-}" ]]; then
    if wait_for_pid "${BOX_LIVE_PID}"; then
        pass "Live frames client finished"
    else
        fail "Live frames client timeout"
    fi
fi

# Stop cloud to flush stats
echo ""
echo "${YELLOW}Stopping mock cloud to flush stats...${NC}"
kill -TERM "${CLOUD_PID}" 2>/dev/null || true
wait "${CLOUD_PID}" 2>/dev/null || true
for _ in $(seq 1 25); do
    if [ -f mock_cloud_frames.json ]]; then
        break
    fi
    sleep 0.2
done

# Step 8: Validation
echo ""
echo "$SEPARATOR"
echo "VALIDATION"
echo "$SEPARATOR"

# Check cloud received frames
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
if [[ "$CLOUD_FRAMES" = "0" ]]; then
    fail "No frames received by cloud"
fi

# Check FIFO order (queued before live)
echo ""
echo "Checking FIFO order..."
if "${PYTHON_BIN}" - <<'PY'
import json
import sys
from pathlib import Path

p = Path("mock_cloud_frames.json")
if not p.exists():
    sys.exit(1)
try:
    stats = json.loads(p.read_text())
except Exception:
    sys.exit(1)

frames = stats.get("frames", [])
if not frames:
    sys.exit(1)

first = frames[0].get("table_name", "")
last = frames[-1].get("table_name", "")
if "actual" not in first and "actual" in last:
    sys.exit(0)
sys.exit(1)
PY
then
    pass "FIFO order correct (queued -> live)"
else
    fail "FIFO order wrong"
fi

echo ""
echo "$SEPARATOR"
echo "TEST 3 COMPLETE"
echo "$SEPARATOR"
echo ""
echo "Logs:"
echo "  Proxy:  /tmp/proxy_test3.log"
echo "  Cloud:  /tmp/cloud_test3.log"
echo "  BOX:    /tmp/box_offline.log, /tmp/box_live.log"
echo "  Frames: mock_cloud_frames.json"

if [[ "$FAILURES" -gt 0 ]]; then
    exit 1
fi
