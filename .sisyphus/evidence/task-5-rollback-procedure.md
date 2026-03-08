# Twin Activation Rollback Procedure

> **System**: OIG Proxy (HA add-on, v1.6.3)
> **Purpose**: Deterministic steps to revert Twin activation to pre-cutover state
> **Maximum rollback time**: 3 minutes (kill switch) / 5 minutes (full rollback)
> **Baseline reference**: `.sisyphus/evidence/task-1-baseline.json`

---

## Rollback Triggers

Any of these conditions triggers immediate rollback:

| Trigger ID | Condition | Detection Method | Severity |
|------------|-----------|-----------------|----------|
| RT-1 | Addon crash loop (>2 restarts in 5 min) | HA supervisor logs | CRITICAL |
| RT-2 | BOX disconnected >3 minutes after cutover | `box_data_recent=0` in proxy_status | CRITICAL |
| RT-3 | Python traceback in addon logs | `grep Traceback` in addon logs | CRITICAL |
| RT-4 | Cloud connection lost >2 min post-cutover | `cloud_online=0` in proxy_status | HIGH |
| RT-5 | Settings delivery failure (queue stuck >5 min) | `control_queue_len > 0` persists | HIGH |
| RT-6 | Twin state not publishing after restart | No twin_state MQTT within 60s | HIGH |
| RT-7 | Error rate >50/min sustained for 2 min | Log grep count | HIGH |
| RT-8 | Data corruption (unexpected file changes) | File stat comparison | CRITICAL |
| RT-9 | Operator judgment (anything feels wrong) | Human | ANY |

---

## Rollback Level 1: Kill Switch (fastest - ~30 seconds)

**When to use**: Twin is causing issues but addon is still running and responsive.

### Steps

```bash
# STEP 1: Activate kill switch (immediate, no restart needed if runtime API exists)
echo "true" > /data/twin_kill_switch

# STEP 2: Restart addon to apply
ha addons restart d7b5d5b1_oig_proxy

# STEP 3: Verify kill switch active (wait max 30s for restart)
sleep 30
ha addons logs d7b5d5b1_oig_proxy | grep -i "kill switch"
# Expected: "Twin kill switch: ENABLED"

# STEP 4: Verify proxy operational
mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -W 30 | jq '{status, mode, cloud_online, box_data_recent}'
# Expected: status=online, cloud_online=1, box_data_recent=1 (after BOX reconnects)
```

### Validation

| # | Check | Command | Expected |
|---|-------|---------|----------|
| V1.1 | Addon running | `ha addons info d7b5d5b1_oig_proxy \| jq -r .data.state` | `started` |
| V1.2 | Kill switch confirmed | `ha addons logs d7b5d5b1_oig_proxy \| grep "kill switch"` | "ENABLED" |
| V1.3 | No Twin routing | Twin is bypassed, legacy control_settings active | No twin_state updates |
| V1.4 | BOX data flowing | proxy_status `box_data_recent` | `1` (within 2 min) |

### Duration: ~30 seconds

---

## Rollback Level 2: Disable Twin (moderate - ~2 minutes)

**When to use**: Kill switch applied but want to fully disable Twin to prevent accidental re-enable.

### Steps

```bash
# STEP 1: Set all Twin flags to disabled
echo "false" > /data/twin_enabled
echo "true" > /data/twin_kill_switch
echo "false" > /data/twin_cloud_aligned
echo "auto" > /data/local_control_routing

# STEP 2: Restart addon
ha addons restart d7b5d5b1_oig_proxy

# STEP 3: Wait for restart
sleep 30

# STEP 4: Verify Twin is fully disabled
ha addons logs d7b5d5b1_oig_proxy | grep -E "(Twin|twin)" | tail -5
# Expected: No "Twin: ENABLED" line

# STEP 5: Verify baseline behavior restored
mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -W 60 | jq '{status, mode, cloud_online, box_data_recent, control_queue_len}'
# Expected: status=online, mode matches configured_mode, cloud_online=1

# STEP 6: Verify BOX reconnects and data flows
mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 2 -W 120 | jq -r .last_data
# Expected: Recent timestamps (within last 2 minutes)
```

### Validation

| # | Check | Command | Expected |
|---|-------|---------|----------|
| V2.1 | Addon running | `ha addons info d7b5d5b1_oig_proxy \| jq -r .data.state` | `started` |
| V2.2 | Twin disabled | `ha addons logs d7b5d5b1_oig_proxy \| grep "Twin: ENABLED"` | NOT found |
| V2.3 | Cloud online | proxy_status `.cloud_online` | `1` |
| V2.4 | BOX data recent | proxy_status `.box_data_recent` | `1` |
| V2.5 | Legacy control active | proxy_status `.control_queue_len` | `0` |
| V2.6 | No Twin state topic | `mosquitto_sub -t 'oig_local/oig_proxy/twin_state/state' -C 1 -W 10` | Timeout (no message) |

### Duration: ~2 minutes

---

## Rollback Level 3: Full Baseline Restore (complete - ~5 minutes)

**When to use**: Something is deeply wrong - data corruption, unknown state, or Levels 1/2 didn't resolve the issue.

### Steps

```bash
# STEP 1: Stop the addon immediately
ha addons stop d7b5d5b1_oig_proxy

# STEP 2: Restore /data from pre-cutover backup
if [ -d /data.pre-twin-backup ]; then
  # Preserve the backup itself
  cp -a /data /data.failed-cutover-$(date +%Y%m%d_%H%M%S)
  
  # Restore from backup
  rm -rf /data/twin_enabled /data/twin_kill_switch /data/twin_cloud_aligned /data/local_control_routing
  cp /data.pre-twin-backup/prms_state.json /data/prms_state.json 2>/dev/null || true
  cp /data.pre-twin-backup/sensor_map.json /data/sensor_map.json 2>/dev/null || true
  
  # Set explicit disable flags
  echo "false" > /data/twin_enabled
  echo "true" > /data/twin_kill_switch
  echo "false" > /data/twin_cloud_aligned
  echo "auto" > /data/local_control_routing
  
  echo "RESTORE COMPLETE"
else
  echo "ERROR: Backup /data.pre-twin-backup not found!"
  echo "Manually set flags:"
  echo "false" > /data/twin_enabled
  echo "true" > /data/twin_kill_switch
  echo "false" > /data/twin_cloud_aligned
  echo "auto" > /data/local_control_routing
fi

# STEP 3: Start addon
ha addons start d7b5d5b1_oig_proxy

# STEP 4: Wait for full startup
sleep 45

# STEP 5: Full validation
ha addons logs d7b5d5b1_oig_proxy | tail -30

# STEP 6: Verify proxy_status matches pre-cutover baseline
mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -W 60 | python3 -c "
import json, sys
status = json.loads(sys.stdin.read())
checks = {
    'status_online': status.get('status') == 'online',
    'cloud_online': status.get('cloud_online') == 1,
    'box_data_recent': status.get('box_data_recent') == 1,
    'control_queue_empty': status.get('control_queue_len', 0) == 0,
}
for k, v in checks.items():
    print(f'  {k}: {\"PASS\" if v else \"FAIL\"} ({status.get(k.split(\"_\")[0], \"?\")})')
all_pass = all(checks.values())
print(f'\nOverall: {\"ALL PASS\" if all_pass else \"FAILURES DETECTED\"} ({sum(checks.values())}/{len(checks)})')
sys.exit(0 if all_pass else 1)
"

# STEP 7: Wait for BOX reconnection and full data cycle
echo "Waiting for BOX reconnection (up to 3 minutes)..."
for i in $(seq 1 18); do
  STATUS=$(mosquitto_sub -t 'oig_local/oig_proxy/proxy_status/state' -C 1 -W 10 2>/dev/null | jq -r .box_data_recent 2>/dev/null)
  if [ "$STATUS" = "1" ]; then
    echo "BOX reconnected after ~$((i*10)) seconds"
    break
  fi
  echo "  Waiting... ($((i*10))s)"
done
```

### Validation

| # | Check | Command | Expected |
|---|-------|---------|----------|
| V3.1 | Addon running | `ha addons info d7b5d5b1_oig_proxy \| jq -r .data.state` | `started` |
| V3.2 | No tracebacks | `ha addons logs d7b5d5b1_oig_proxy \| grep -c Traceback` | `0` |
| V3.3 | Mode matches baseline | proxy_status `.configured_mode` | Matches task-1-baseline |
| V3.4 | Cloud online | proxy_status `.cloud_online` | `1` |
| V3.5 | BOX data flowing | proxy_status `.box_data_recent` | `1` |
| V3.6 | No Twin artifacts | `ls /data/twin_enabled; cat /data/twin_enabled` | `false` |
| V3.7 | prms_state.json valid | `python3 -c "import json; json.load(open('/data/prms_state.json'))"` | exit 0 |
| V3.8 | HA sensors updating | Check HA UI for tbl_actual sensors | Values updating |

### Duration: ~5 minutes

---

## Post-Rollback Actions

| # | Action | When | Owner |
|---|--------|------|-------|
| PR-1 | Document rollback reason in incident log | Immediately | Operator |
| PR-2 | Capture addon logs from failed cutover | Within 1 hour | Operator |
| PR-3 | Save failed /data snapshot for analysis | Before cleanup | Operator |
| PR-4 | Verify HA entities stable for 30 min | +30 min post-rollback | Operator |
| PR-5 | Clear retained twin_state MQTT topic | After confirmed stable | Operator |
| PR-6 | Remove /data.pre-twin-backup only after 7 days stable | +7 days | Operator |

### Clear retained Twin topics (optional cleanup):
```bash
mosquitto_pub -t 'oig_local/oig_proxy/twin_state/state' -n -r -h core-mosquitto
```

---

## Rollback Decision Tree

```
Issue detected during/after cutover
│
├─ Addon still running?
│  ├─ YES → Level 1: Kill Switch (30s)
│  │        │
│  │        ├─ Issue resolved? → DONE (monitor 30 min)
│  │        └─ Still broken? → Level 2: Disable Twin (2 min)
│  │                 │
│  │                 ├─ Issue resolved? → DONE (monitor 1 hour)
│  │                 └─ Still broken? → Level 3: Full Restore (5 min)
│  │
│  └─ NO (crash loop) → Level 3: Full Restore (5 min)
│
└─ /data corrupted?
   ├─ NO → Level 2 or 3
   └─ YES → Level 3: Full Restore from backup
```

---

## Environment Variables Reference (Rollback-Critical)

| Variable | Baseline (pre-cutover) | During cutover | Rollback value |
|----------|----------------------|----------------|----------------|
| `TWIN_ENABLED` | `false` | `true` | `false` |
| `TWIN_KILL_SWITCH` | `false` | `false` | `true` (safety) |
| `TWIN_CLOUD_ALIGNED` | `false` | `true` (phase 4) | `false` |
| `LOCAL_CONTROL_ROUTING` | `auto` | `auto` → `force_twin` | `auto` |
| `PROXY_MODE` | `hybrid` | unchanged | unchanged |
| `CONTROL_MQTT_ENABLED` | `false`/`true` | unchanged | unchanged |

---

## Emergency Contact Points

| Role | Responsibility | Escalation |
|------|---------------|------------|
| Operator | Execute rollback, validate | If Level 3 fails |
| Developer | Debug root cause from logs | If repeated cutover failures |

---

*Maximum time from trigger detection to restored baseline: **5 minutes** (Level 3)*
*Maximum time for kill switch (preserving Twin code, disabling routing): **30 seconds** (Level 1)*
