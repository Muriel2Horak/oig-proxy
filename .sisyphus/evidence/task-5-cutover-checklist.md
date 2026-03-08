# Twin Activation Cutover Checklist

> **System**: OIG Proxy (HA add-on, v1.6.3)
> **Target**: Enable DigitalTwin setting management (TWIN_ENABLED + TWIN_CLOUD_ALIGNED)
> **Date**: 2026-03-07
> **Baseline reference**: `.sisyphus/evidence/task-1-baseline.json`, `task-1-runtime-snapshot.json`

---

## Phase 0: Pre-Cutover Baseline Snapshot

| # | Check | Command | Expected | Owner | Status |
|---|-------|---------|----------|-------|--------|
| 0.1 | Capture current addon version | `docker inspect d7b5d5b1_oig_proxy --format '{{.Config.Image}}'` | `*:1.6.3` or target version | Operator | ☐ |
| 0.2 | Capture current config options | `ha addons info d7b5d5b1_oig_proxy \| jq .data.options` | JSON snapshot saved | Operator | ☐ |
| 0.3 | Capture MQTT proxy_status | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -h core-mosquitto` | `status: online`, `cloud_online: 1` | Operator | ☐ |
| 0.4 | Capture MQTT twin_state (if exists) | `mosquitto_sub -t 'oig_local/oig_proxy/twin_state/state' -C 1 -W 5 -h core-mosquitto` | Empty or baseline | Operator | ☐ |
| 0.5 | Record cloud_queue.db row count | `sqlite3 /data/cloud_queue.db "SELECT COUNT(*) FROM frames"` | 0 (queue empty) | Operator | ☐ |
| 0.6 | Record prms_state.json timestamp | `stat -c %Y /data/prms_state.json 2>/dev/null` | Timestamp recorded | Operator | ☐ |
| 0.7 | Backup full /data directory | `cp -a /data /data.pre-twin-backup` | Backup exists | Operator | ☐ |
| 0.8 | Verify baseline JSON matches task-1 | Compare with `.sisyphus/evidence/task-1-baseline.json` | Matches or documented drift | Operator | ☐ |

---

## Phase 1: Preflight Checks (GO/NO-GO)

### 1A. System Health (all must PASS for GO)

| # | Check | Command | GO Condition | ABORT Action |
|---|-------|---------|-------------|--------------|
| 1.1 | Addon is running | `ha addons info d7b5d5b1_oig_proxy \| jq -r .data.state` | `started` | **ABORT** - fix addon first |
| 1.2 | BOX connected recently | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 \| jq -r .box_data_recent` | `1` | **ABORT** - BOX offline, unsafe to cutover |
| 1.3 | Cloud is online | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 \| jq -r .cloud_online` | `1` | **ABORT** - Cloud down, must cutover from known-good state |
| 1.4 | Cloud queue empty | `sqlite3 /data/cloud_queue.db "SELECT COUNT(*) FROM frames"` | `0` | **ABORT** - Drain queue first before cutover |
| 1.5 | Control queue empty | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 \| jq -r .control_queue_len` | `0` | **ABORT** - Wait for in-flight settings to complete |
| 1.6 | No active control inflight | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 \| jq -r .control_inflight` | `""` (empty) | **ABORT** - Wait for completion |
| 1.7 | Disk space adequate | `df -h /data \| awk 'NR==2{print $5}'` | `<80%` | **ABORT** - Free disk space |
| 1.8 | MQTT broker responding | `mosquitto_pub -t 'oig_local/test/ping' -m 'pong' -h core-mosquitto` | exit 0 | **ABORT** - Fix MQTT first |

### 1B. Code Readiness (all must PASS for GO)

| # | Check | Command | GO Condition | ABORT Action |
|---|-------|---------|-------------|--------------|
| 1.9 | digital_twin.py present in image | `docker exec d7b5d5b1_oig_proxy ls /app/digital_twin.py` | File exists | **ABORT** - Image missing Twin code |
| 1.10 | twin_transaction.py present | `docker exec d7b5d5b1_oig_proxy ls /app/twin_transaction.py` | File exists | **ABORT** - Image incomplete |
| 1.11 | twin_state.py present | `docker exec d7b5d5b1_oig_proxy ls /app/twin_state.py` | File exists | **ABORT** - Image incomplete |
| 1.12 | twin_adapter.py present | `docker exec d7b5d5b1_oig_proxy ls /app/twin_adapter.py` | File exists | **ABORT** - Image incomplete |
| 1.13 | sensor_map has Twin sensors | `grep -c twin_state /data/sensor_map.json` | `>=5` | **ABORT** - Sensor map not updated |
| 1.14 | Config schema accepts twin flags | `jq '.schema.twin_enabled' addon/oig-proxy/config.json` | Not null | **ABORT** - Schema not updated |

### 1C. Timing Window

| # | Check | Condition | GO Condition | ABORT Action |
|---|-------|-----------|-------------|--------------|
| 1.15 | Not during BOX data burst | Check `last_data` recency | Not within active session | **DELAY** - Wait for session gap |
| 1.16 | Operator available for 30 min | Human confirmation | Yes | **DELAY** - Schedule for available window |
| 1.17 | No pending HA updates | Check HA supervisor | No updates in progress | **DELAY** - Complete updates first |

---

## Phase 2: Cutover Execution

### Step 2.1: Enable Twin (conservative - Twin only, no cloud-aligned)

```bash
# Set TWIN_ENABLED=true via HA addon config or flag file
echo "true" > /data/twin_enabled
# Ensure kill switch is OFF
echo "false" > /data/twin_kill_switch
# Keep cloud-aligned OFF initially
echo "false" > /data/twin_cloud_aligned
# Keep routing on auto (Twin only activates when cloud unavailable or offline)
echo "auto" > /data/local_control_routing
```

**Alternative: Via HA Addon Config UI**
```
twin_enabled: true
twin_kill_switch: false
twin_cloud_aligned: false
local_control_routing: auto
```

### Step 2.2: Restart addon
```bash
ha addons restart d7b5d5b1_oig_proxy
```

### Step 2.3: Immediate Post-Restart Verification (within 60s)

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 2.3.1 | Addon restarted successfully | `ha addons info d7b5d5b1_oig_proxy \| jq -r .data.state` | `started` |
| 2.3.2 | No crash loops | `ha addons logs d7b5d5b1_oig_proxy \| tail -20` | No Python tracebacks |
| 2.3.3 | Twin log line present | `ha addons logs d7b5d5b1_oig_proxy \| grep -i "Twin: ENABLED"` | Found |
| 2.3.4 | Kill switch OFF | `ha addons logs d7b5d5b1_oig_proxy \| grep -i "kill switch"` | Not found (or DISABLED) |
| 2.3.5 | Proxy mode correct | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -W 30 \| jq -r .mode` | `online` or `hybrid` |
| 2.3.6 | BOX reconnects | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -W 120 \| jq -r .box_data_recent` | `1` (within 2 min) |
| 2.3.7 | Twin state topic published | `mosquitto_sub -t 'oig_local/oig_proxy/twin_state/state' -C 1 -W 30` | JSON with `session_active` |
| 2.3.8 | Cloud still online | `mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -W 30 \| jq -r .cloud_online` | `1` |

---

## Phase 3: Soak Period (15 minutes minimum)

| # | Check | When | Command | Expected |
|---|-------|------|---------|----------|
| 3.1 | No error rate spike | +5 min | `ha addons logs d7b5d5b1_oig_proxy \| grep -c ERROR` | `< 5` in 5 min |
| 3.2 | BOX data flowing | +5 min | `jq -r .last_data` from proxy_status | Recent timestamp |
| 3.3 | Cloud still connected | +5 min | `jq -r .cloud_online` from proxy_status | `1` |
| 3.4 | Twin state stable | +10 min | `jq -r .session_active` from twin_state | Consistent (true when BOX connected) |
| 3.5 | No queue buildup | +15 min | `jq -r .control_queue_len` from proxy_status | `0` |
| 3.6 | HA entities present | +15 min | Check HA UI for twin_* sensors | 5 sensors visible |

---

## Phase 4: Optional - Enable Cloud-Aligned Mode

> **Only proceed if Phase 3 passed ALL checks.**

```bash
echo "true" > /data/twin_cloud_aligned
ha addons restart d7b5d5b1_oig_proxy
```

Repeat Phase 2.3 and Phase 3 checks.

---

## Phase 5: Optional - Force Twin Routing

> **Only after Phase 4 soak (24h recommended).**

```bash
echo "force_twin" > /data/local_control_routing
ha addons restart d7b5d5b1_oig_proxy
```

### Functional Validation:
```bash
# Send test setting via MQTT (non-destructive read-only SA command)
mosquitto_pub -t 'oig_local/<device_id>/tbl_box_prms/SA/set' \
  -m '{"value":"1","request_key":"cutover-test-1"}' \
  -h core-mosquitto

# Monitor result
mosquitto_sub -t 'oig_local/oig_proxy/twin_state/state' -C 3 -W 120
```

---

## Critical Abort Conditions

| ID | Condition | Detection | Action | Max Response Time |
|----|-----------|-----------|--------|-------------------|
| ABORT-1 | Addon fails to start after restart | `state != started` after 30s | Rollback immediately | 30 seconds |
| ABORT-2 | Python traceback in logs | `grep -c Traceback` in logs | Activate kill switch | 60 seconds |
| ABORT-3 | BOX fails to reconnect | `box_data_recent=0` for >3 min | Rollback to baseline | 3 minutes |
| ABORT-4 | Cloud connection lost | `cloud_online=0` for >2 min | Activate kill switch | 2 minutes |
| ABORT-5 | Settings not delivered | `control_queue_len > 0` for >5 min | Activate kill switch + rollback | 5 minutes |
| ABORT-6 | MQTT broker unreachable | `mosquitto_pub` fails | Rollback to baseline | 30 seconds |
| ABORT-7 | HA entities disappear | Twin sensors missing from HA | Investigate (non-critical) | 10 minutes |
| ABORT-8 | Continuous error log spam | `>50 ERROR lines/min` | Activate kill switch | 2 minutes |
| ABORT-9 | Data corruption suspected | `prms_state.json` changes unexpectedly | Rollback + restore backup | 5 minutes |

---

## GO/NO-GO Decision Matrix

| Category | Total Checks | Must Pass | Current |
|----------|-------------|-----------|---------|
| System Health (1A) | 8 | 8/8 | ☐ |
| Code Readiness (1B) | 6 | 6/6 | ☐ |
| Timing (1C) | 3 | 3/3 | ☐ |
| **TOTAL** | **17** | **17/17** | ☐ |

**Decision**: GO only if ALL 17 preflight checks PASS. Any single failure = NO-GO.
