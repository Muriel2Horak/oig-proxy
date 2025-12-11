#!/bin/bash
# Test 3: REPLAY Mode - klíčový test!
#
# Scénář:
# 1. Naplnit frontu v OFFLINE režimu
# 2. Spustit cloud server (recovery)
# 3. Čekat na auto-přechod do REPLAY
# 4. Paralelně posílat nové live frames
# 5. Validovat FIFO pořadí a přechod do ONLINE

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================="
echo "TEST 3: REPLAY MODE"
echo "========================================="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

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
export TARGET_PORT=5710
export PROXY_PORT=5711
export LOG_LEVEL=DEBUG
export CAPTURE_PAYLOADS=false

# Clean queues
rm -f /tmp/cloud_queue.db /tmp/mqtt_queue.db /tmp/payloads.db

python3 main.py > /tmp/proxy_test3.log 2>&1 &
PROXY_PID=$!
cd "$SCRIPT_DIR"

sleep 3
echo "${GREEN}✓ Proxy started (PID: $PROXY_PID)${NC}"

# Step 2: Send frames OFFLINE (no cloud) → fill queue
echo ""
echo "${YELLOW}Step 2: Sending frames OFFLINE (fill queue)...${NC}"
echo "  → Expecting: Local ACK, frames queued"

python3 mock_box_client.py \
    --data test_data/box_frames_5min.json \
    --proxy-host localhost \
    --proxy-port 5710 \
    --rate-limit 0.5 \
    > /tmp/box_offline.log 2>&1

echo "${GREEN}✓ Queued $(sqlite3 /tmp/cloud_queue.db 'SELECT COUNT(*) FROM queue' 2>/dev/null || echo '?') frames${NC}"

# Step 3: Start cloud server (recovery!)
echo ""
echo "${YELLOW}Step 3: Starting cloud server (recovery)...${NC}"
python3 mock_cloud_server.py > /tmp/cloud_test3.log 2>&1 &
CLOUD_PID=$!
sleep 2
echo "${GREEN}✓ Cloud server started (PID: $CLOUD_PID)${NC}"

# Step 4: Wait for replay to start
echo ""
echo "${YELLOW}Step 4: Waiting for REPLAY mode...${NC}"
echo "  → Health check should detect cloud (30s max)"

# Monitor proxy log for mode change
timeout 60 bash -c '
while ! grep -q "MODE.*REPLAY" /tmp/proxy_test3.log; do
    sleep 1
done
' && echo "${GREEN}✓ REPLAY mode activated${NC}" || echo "${RED}✗ Timeout waiting for REPLAY${NC}"

# Step 5: Send NEW live frames during replay
echo ""
echo "${YELLOW}Step 5: Sending LIVE frames during replay...${NC}"
echo "  → Should go to end of queue (FIFO)"

sleep 5  # Let some replay happen first

python3 mock_box_client.py \
    --data test_data/box_frames_actual.json \
    --proxy-host localhost \
    --proxy-port 5710 \
    --rate-limit 1.0 \
    > /tmp/box_live.log 2>&1 &

# Step 6: Wait for queue to empty
echo ""
echo "${YELLOW}Step 6: Waiting for queue to empty...${NC}"

timeout 120 bash -c '
while true; do
    COUNT=$(sqlite3 /tmp/cloud_queue.db "SELECT COUNT(*) FROM queue" 2>/dev/null || echo "999")
    echo "  Queue size: $COUNT"
    if [ "$COUNT" = "0" ]; then
        break
    fi
    sleep 5
done
' && echo "${GREEN}✓ Queue empty${NC}" || echo "${RED}✗ Timeout${NC}"

# Step 7: Check ONLINE mode transition
echo ""
echo "${YELLOW}Step 7: Checking ONLINE mode transition...${NC}"

if grep -q "MODE.*ONLINE" /tmp/proxy_test3.log; then
    echo "${GREEN}✓ Switched to ONLINE mode${NC}"
else
    echo "${RED}✗ No ONLINE mode detected${NC}"
fi

# Step 8: Validation
echo ""
echo "========================================="
echo "VALIDATION"
echo "========================================="

# Check cloud received frames
CLOUD_FRAMES=$(grep -c "ACK" /tmp/cloud_test3.log 2>/dev/null || echo "0")
echo "Cloud received: $CLOUD_FRAMES frames"

# Check FIFO order (queued before live)
echo ""
echo "Checking FIFO order..."
python3 -c "
import json
with open('mock_cloud_frames.json', 'r') as f:
    stats = json.load(f)
    frames = stats['frames']
    print(f'Total frames at cloud: {len(frames)}')
    
    # First frame should be from 5min batch (queued)
    # Last frames should be from actual (live)
    if len(frames) > 0:
        first = frames[0]['table_name']
        last = frames[-1]['table_name']
        print(f'First frame: {first}')
        print(f'Last frame: {last}')
        
        if 'actual' not in first and 'actual' in last:
            print('${GREEN}✓ FIFO order correct (queued → live)${NC}')
        else:
            print('${RED}✗ FIFO order wrong${NC}')
" 2>/dev/null || echo "No frames file"

echo ""
echo "========================================="
echo "TEST 3 COMPLETE"
echo "========================================="
echo ""
echo "Logs:"
echo "  Proxy:  /tmp/proxy_test3.log"
echo "  Cloud:  /tmp/cloud_test3.log"
echo "  BOX:    /tmp/box_offline.log, /tmp/box_live.log"
echo "  Frames: mock_cloud_frames.json"
