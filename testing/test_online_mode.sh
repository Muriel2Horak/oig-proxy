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

echo "========================================="
echo "TEST 1: ONLINE MODE (SMOKE TEST)"
echo "========================================="
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

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
python3 mock_cloud_server.py > /tmp/cloud_test1.log 2>&1 &
CLOUD_PID=$!
sleep 2
echo "${GREEN}✓ Cloud server started (PID: $CLOUD_PID)${NC}"

# Step 2: Start proxy
echo ""
echo "${YELLOW}Step 2: Starting proxy...${NC}"
cd ../addon/oig-proxy
export DEVICE_ID=TEST001
export DATA_DIR=/tmp
export MQTT_HOST=localhost
export TARGET_SERVER=localhost
export TARGET_PORT=5710
export PROXY_PORT=5711
export LOG_LEVEL=INFO

# Clean queues
rm -f /tmp/cloud_queue.db /tmp/mqtt_queue.db /tmp/payloads.db

python3 main.py > /tmp/proxy_test1.log 2>&1 &
PROXY_PID=$!
cd "$SCRIPT_DIR"

sleep 3
echo "${GREEN}✓ Proxy started (PID: $PROXY_PID)${NC}"

# Step 3: Send frames
echo ""
echo "${YELLOW}Step 3: Sending 100 frames from BOX...${NC}"

python3 mock_box_client.py \
    --data test_data/test_data/box_frames_100.json \
    --proxy-host localhost \
    --proxy-port 5711 \
    --rate-limit 0.1 \
    > /tmp/box_test1.log 2>&1

echo "${GREEN}✓ Frames sent${NC}"

# Step 4: Validation
echo ""
echo "========================================="
echo "VALIDATION"
echo "========================================="

# Check success rate from BOX
BOX_SENT=$(grep "Frames sent:" /tmp/box_test1.log | awk '{print $3}')
BOX_ACKS=$(grep "ACKs received:" /tmp/box_test1.log | awk '{print $3}')
echo "BOX: Sent $BOX_SENT, ACKs $BOX_ACKS"

if [ "$BOX_SENT" = "$BOX_ACKS" ]; then
    echo "${GREEN}✓ 100% ACK rate${NC}"
else
    echo "${RED}✗ Missing ACKs!${NC}"
fi

# Check cloud received
CLOUD_FRAMES=$(python3 -c "
import json
try:
    with open('mock_cloud_frames.json', 'r') as f:
        stats = json.load(f)
        print(stats['total_frames'])
except:
    print(0)
" 2>/dev/null)

echo "Cloud received: $CLOUD_FRAMES frames"

if [ "$BOX_SENT" = "$CLOUD_FRAMES" ]; then
    echo "${GREEN}✓ All frames delivered to cloud${NC}"
else
    echo "${RED}✗ Frame loss!${NC}"
fi

# Check queue (should be empty in ONLINE mode)
QUEUE_SIZE=$(sqlite3 /tmp/cloud_queue.db 'SELECT COUNT(*) FROM queue' 2>/dev/null || echo "0")
echo "Queue size: $QUEUE_SIZE"

if [ "$QUEUE_SIZE" = "0" ]; then
    echo "${GREEN}✓ Queue empty (direct forward)${NC}"
else
    echo "${RED}✗ Unexpected queuing!${NC}"
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
