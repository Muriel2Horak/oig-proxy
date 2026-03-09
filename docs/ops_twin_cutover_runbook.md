# Twin Cutover Ops Runbook

This runbook covers rolling out the Twin Architecture to production, verifying it's healthy,
and rolling back cleanly if something goes wrong.

Target audience: operators with SSH access to the Home Assistant host.

---

## Prerequisites

- SSH access to the HA host (alias `ha` assumed throughout)
- Terminal & SSH add-on running in HA
- Token for the Control API (see [Reading the token](#reading-the-control-api-token))
- MQTT broker running (`core-mosquitto`)

### Reading the Control API token

```bash
ssh ha
cat /addon_configs/d7b5d5b1_oig_proxy/data/control_api_token
```

Set it in your shell for the rest of the session:

```bash
export TOKEN=$(cat /addon_configs/d7b5d5b1_oig_proxy/data/control_api_token)
export HA_IP=<IP of your HA instance>
```

---

## Part 1: Rollout

### 1.1 Pre-flight checks

Run these before touching anything.

```bash
# Add-on status
ha addons info d7b5d5b1_oig_proxy

# Control API health
curl -s -H "Authorization: Bearer $TOKEN" http://$HA_IP:8099/api/health
# Expected: {"status": "ok", "mode": "ONLINE", "box_connected": true}

# MQTT connectivity (subscribe for 5 s and check twin_state arrives)
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/oig_proxy/twin_state/state" \
  -C 1 -W 5
# Expected: JSON payload with "mode" field
```

If `box_connected` is `false` or `mosquitto_sub` times out, stop here and see
[Part 4: Troubleshooting](#part-4-troubleshooting).

### 1.2 Deploy new add-on version

```bash
ssh ha

# Backup current data
docker cp addon_d7b5d5b1_oig_proxy:/data/payloads.db /backup/payloads_$(date +%Y%m%d).db
ha addons info d7b5d5b1_oig_proxy > /backup/addon_info_$(date +%Y%m%d).yaml

# Apply update
ha addons update d7b5d5b1_oig_proxy

# Restart
ha addons restart d7b5d5b1_oig_proxy

# Tail logs until you see startup banner
ha addons logs d7b5d5b1_oig_proxy -f
```

Expected log lines within 30 seconds:

```
🚀 OIG Proxy naslouchá na 0.0.0.0:5710
Digital Twin initialised
MQTT connected
```

### 1.3 Verify Twin activation

```bash
# Check twin_state MQTT topic (retained, arrives immediately)
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/oig_proxy/twin_state/state" \
  -C 1 -W 10

# Expected fields in response:
# "mode": "twin"          <- Twin is active
# "session_active": false  <- no BOX session yet, normal at startup
# "queue_length": 0        <- no pending settings
```

### 1.4 Trigger a roundtrip (smoke test)

Send one whitelisted setting and watch it flow through.

```bash
# Queue a MODE read-back (no actual change: write current value back)
# Replace NEW_VALUE with the value currently shown in HA
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tbl_name": "tbl_box_prms", "tbl_item": "MODE", "new_value": "1"}' \
  http://$HA_IP:8099/api/setting
# Expected: {"ok": true}

# Watch twin_state for delivery progress
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/oig_proxy/twin_state/state" \
  -W 60 | python3 -c "
import sys, json
for line in sys.stdin:
    s = json.loads(line)
    print('stage:', s.get('inflight', {}).get('stage'), '| last_status:', s.get('last_result', {}).get('status'))
"
```

Healthy roundtrip produces this sequence in order:

| Field | Value |
|-------|-------|
| `inflight.stage` | `queued` |
| `inflight.stage` | `delivered` |
| `last_result.status` | `applied` |

If `last_result.status` is `applied` within 60 seconds, the Twin is working.
Proceed to Part 2.

If it stays at `queued` or jumps to `failed`, go to
[4.1 Setting stuck in queue](#41-setting-stuck-in-queue).

---

## Part 2: Health Verification Commands

Use these at any time to check proxy health.

### 2.1 Control API health

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://$HA_IP:8099/api/health
```

```json
{"status": "ok", "mode": "ONLINE", "box_connected": true}
```

### 2.2 Twin state snapshot

```bash
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/oig_proxy/twin_state/state" \
  -C 1 -W 10
```

Key fields to read:

| Field | Healthy value | Action if not |
|-------|--------------|---------------|
| `mode` | `twin` | See [4.3 Twin not activating](#43-twin-not-activating) |
| `session_active` | `true` during setting, `false` otherwise | Normal when idle |
| `queue_length` | `0` (or small number, draining) | See [4.1 Setting stuck in queue](#41-setting-stuck-in-queue) |
| `last_result.status` | `applied` | See [4.2 Settings not delivering](#42-settings-not-delivering) |

### 2.3 Add-on live logs

```bash
ssh ha "ha addons logs d7b5d5b1_oig_proxy -f"
```

Healthy steady-state output looks like:

```
Mode: ONLINE | cloud_queue: 0 | mqtt_queue: 0 | connections: 1
```

Watch for these warning signs:

- `ERROR` or `Traceback` in any line
- `Mode: OFFLINE` lasting more than 60 seconds
- `cloud_queue: >10` (growing backlog)
- `BOX disconnected` without `BOX připojen` following within 60 seconds

### 2.4 MQTT data flow check

Confirm that sensor data is arriving from the BOX:

```bash
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/#" \
  -W 30 | head -20
```

You should see lines like `oig_local/<device_id>/tbl_invertor_sums/state` with JSON payloads.
No output within 30 seconds means no BOX data is arriving.

### 2.5 SA auto-queue confirmation

After any successful setting, Twin queues a Send All (SA) automatically.
Confirm it cleared:

```bash
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/oig_proxy/twin_state/state" \
  -C 1 | python3 -c "
import sys, json
s = json.loads(sys.stdin.read())
print('queue_length:', s['queue_length'])
# Should be 0 once SA completes
"
```

---

## Part 3: Rollback

### 3.1 Immediate rollback (< 2 minutes)

If the new add-on is misbehaving, revert to the previous version:

```bash
ssh ha

# Stop new add-on
ha addons stop d7b5d5b1_oig_proxy

# Start old backup add-on (must have been deployed as local_oig_proxy_backup beforehand)
ha addons start d7b5d5b1_oig_proxy_backup

# BOX reconnects automatically within 30-60 seconds
# Confirm:
ha addons logs d7b5d5b1_oig_proxy_backup -f | grep -m 1 "BOX připojen"
```

If you don't have a backup add-on running on standby, use the Docker method:

```bash
ssh ha

# Restore backed-up main.py
docker cp /data/main_backup.py addon_d7b5d5b1_oig_proxy:/app/main.py

# Restart the container (not the add-on, to avoid re-pulling)
docker restart addon_d7b5d5b1_oig_proxy

# Watch for startup
docker logs -f addon_d7b5d5b1_oig_proxy | grep -m 1 "OIG Proxy naslouchá"
```

### 3.2 Rollback verification

```bash
# Health check
curl -s -H "Authorization: Bearer $TOKEN" http://$HA_IP:8099/api/health

# Confirm BOX data is flowing
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/#" \
  -W 30 | head -5
```

Both commands must return data. If not, check add-on logs.

### 3.3 Post-rollback

After rollback:

1. File an incident note with timestamp and symptoms observed.
2. Pull add-on logs before they rotate:
   ```bash
   ha addons logs d7b5d5b1_oig_proxy > /backup/rollback_logs_$(date +%Y%m%d_%H%M%S).txt
   ```
3. Do not attempt re-deployment until root cause is identified.

---

## Part 4: Troubleshooting

### Decision tree for no-roundtrip symptoms

Start at the top. Follow the first branch that matches.

```
Symptom: Setting sent, but last_result.status never reaches "applied"
│
├── Is BOX connected?
│   Check: curl health → "box_connected"
│   ├── NO → [Branch A: BOX not connecting]
│   └── YES → continue
│
├── Is twin_state published to MQTT?
│   Check: mosquitto_sub twin_state/state (-W 10)
│   ├── NO → [Branch B: Twin state not publishing]
│   └── YES → continue
│
├── Is queue_length > 0?
│   Check: twin_state.queue_length field
│   ├── NO (0) → [Branch C: Setting not reaching Twin]
│   └── YES → continue
│
├── Is inflight.stage present?
│   ├── NO (stuck in queue) → [Branch D: BOX not polling]
│   └── YES → continue
│
├── Is inflight.stage == "delivered"?
│   ├── NO → [Branch E: Delivery frame failed]
│   └── YES (delivered but no applied) → [Branch F: ACK not received]
```

---

### Branch A: BOX not connecting

**Observable condition:** `curl health` returns `"box_connected": false`. No `BOX připojen` in logs.

```bash
# Check add-on is listening on port 5710
ha addons logs d7b5d5b1_oig_proxy | grep "naslouchá"

# Check DNS resolves oigservis.cz to HA IP
nslookup oigservis.cz

# Check port is reachable from outside HA
nc -zv $HA_IP 5710
```

**Remediation:**
1. If DNS resolves to the real cloud IP, DNS override isn't working. Fix router DNS entry and restart BOX.
2. If port 5710 is not reachable, confirm add-on's network config lists port 5710.
3. If add-on isn't listening, restart it: `ha addons restart d7b5d5b1_oig_proxy`.

---

### Branch B: Twin state not publishing

**Observable condition:** `mosquitto_sub oig_local/oig_proxy/twin_state/state` returns nothing within 10 seconds.

```bash
# Check MQTT subscription is active in logs
ha addons logs d7b5d5b1_oig_proxy | grep -i "twin"

# Check proxy_status topic (lower-level health)
mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/oig_proxy/proxy_status/state" \
  -C 1 -W 10

# Check sensor_map includes twin_state entries
docker exec addon_d7b5d5b1_oig_proxy \
  python3 -c "
import json
m = json.load(open('/data/sensor_map.json'))
keys = [k for k in m if 'twin_state' in k]
print('twin_state keys:', keys)
"
```

**Remediation:**
1. If sensor_map has no `twin_state` keys, redeploy with the correct `sensor_map.json`.
2. If MQTT connection is failing, check credentials in add-on configuration.
3. Restart add-on after fixing: `ha addons restart d7b5d5b1_oig_proxy`.

---

### Branch C: Setting not reaching Twin

**Observable condition:** `queue_length` is 0 after sending a setting via the Control API.
API returned `{"ok": true}` but Twin never received it.

```bash
# Check Control API accepted and routed it
ha addons logs d7b5d5b1_oig_proxy | grep -E "queue_setting|twin"

# Try sending via MQTT directly (bypasses Control API)
mosquitto_pub -h core-mosquitto -u <mqtt_username> -P <mqtt_password> \
  -t "oig_local/<device_id>/tbl_box_prms/MODE/set" \
  -m '{"value": "1"}'

# Check queue_length again
mosquitto_sub -h core-mosquitto -u <mqtt_username> -P <mqtt_password> \
  -t "oig_local/oig_proxy/twin_state/state" \
  -C 1 | python3 -c "import sys,json; s=json.loads(sys.stdin.read()); print('queue_length:', s['queue_length'])"
```

**Remediation:**
1. If MQTT publish works but Control API doesn't route, check `TwinMQTTHandler` subscription (`oig_local/+/+/set`) in logs.
2. If neither works, verify `digital_twin.py` is present in the container:
   ```bash
   docker exec addon_d7b5d5b1_oig_proxy ls /app/digital_twin.py
   ```
3. Missing module means an incomplete deployment. Redeploy.

---

### Branch D: BOX not polling (stuck in queue)

**Observable condition:** `queue_length > 0` but `inflight` is absent or `stage` stays at `queued`.
BOX is connected but not sending IsNewSet polls.

```bash
# Look for IsNewSet in logs
ha addons logs d7b5d5b1_oig_proxy | grep -i "isnewset\|poll"

# Check BOX session count
ha addons logs d7b5d5b1_oig_proxy | grep -E "BOX připojen|disconnected"
```

**Remediation:**
1. If no recent IsNewSet in logs, BOX may be in an old session. Cycle the BOX power (or its network) to force a fresh TCP connection.
2. A new connection triggers a new session, and Twin will deliver on the next poll.

---

### Branch E: Delivery frame failed

**Observable condition:** Inflight exists but `stage` never advances past `queued` into `delivered`.

```bash
ha addons logs d7b5d5b1_oig_proxy | grep -E "ERROR|delivery|frame"
```

**Remediation:**
1. Look for CRC or framing errors in logs. If present, check the setting payload (`tbl_name`, `tbl_item`, `new_value` are all valid strings).
2. Check the item is on the whitelist (see `SECURITY.md` whitelist table). Off-whitelist writes return 400, not 409.
3. If setting is valid but delivery still fails, restart the add-on to reset the TCP session.

---

### Branch F: ACK not received

**Observable condition:** `inflight.stage == "delivered"` but `last_result.status` never becomes `applied` or `failed`. Sits delivered indefinitely.

```bash
ha addons logs d7b5d5b1_oig_proxy | grep -E "ack|NACK|tbl_events"
```

**Remediation:**
1. A missing ACK usually means the BOX dropped the TCP connection after receiving the frame. Check for `BOX disconnected` near the delivery timestamp in logs.
2. After BOX reconnects, Twin will time out the inflight transaction and mark it failed. Queue the setting again.
3. If BOX repeatedly disconnects after receiving setting frames, lower the write frequency: don't queue more than one setting per BOX session.

---

## Part 5: Reference

### Key topics

| Topic | Payload | Notes |
|-------|---------|-------|
| `oig_local/oig_proxy/twin_state/state` | JSON state | Retained; always present after startup |
| `oig_local/oig_proxy/proxy_status/state` | JSON status | Refreshed every 60 s |
| `oig_local/<device_id>/<tbl>/<item>/set` | `{"value": "..."}` | Trigger a setting via MQTT |
| `oig_local/<device_id>/availability` | `online` / `offline` | BOX connection indicator |

### Whitelist of writable items

Full table from `SECURITY.md`:

| Table | Allowed items |
|-------|--------------|
| `tbl_batt_prms` | `FMT_ON`, `BAT_MIN` |
| `tbl_boiler_prms` | `ISON`, `MANUAL`, `SSR0`, `SSR1`, `SSR2`, `OFFSET` |
| `tbl_box_prms` | `MODE`, `BAT_AC`, `BAT_FORMAT`, `SA`, `RQRESET` |
| `tbl_invertor_prms` | `GRID_PV_ON`, `GRID_PV_OFF`, `TO_GRID` |
| `tbl_invertor_prm1` | `AAC_MAX_CHRG`, `A_MAX_CHRG` |

Anything outside this table returns `400 tbl_name not in whitelist` or `400 tbl_item not in whitelist`.

### Expected twin_state.last_result.status values

| Value | Meaning |
|-------|---------|
| `applied` | BOX confirmed the setting via `tbl_events` |
| `failed` | NACK or timeout; setting did not apply |
| `pending` | Not yet attempted |
| `delivered` | Frame sent; waiting for ACK |

### Useful one-liners

```bash
# Watch twin_state continuously, pretty-print key fields
watch -n 5 'mosquitto_sub -h core-mosquitto -u oig -P oig \
  -t "oig_local/oig_proxy/twin_state/state" -C 1 -W 3 \
  | python3 -c "
import sys,json
s=json.loads(sys.stdin.read())
inf=s.get(\"inflight\") or {}
res=s.get(\"last_result\") or {}
print(\"mode:\", s[\"mode\"], \"| queue:\", s[\"queue_length\"], \"| stage:\", inf.get(\"stage\",\"-\"), \"| last:\", res.get(\"status\",\"-\"))
"'

# Count settings applied today
ha addons logs d7b5d5b1_oig_proxy | grep '"applied"' | wc -l

# Drain stuck queue (cancel all pending via restart)
ha addons restart d7b5d5b1_oig_proxy
```

---

## Changelog

| Date | Change |
|------|--------|
| 2026-03-07 | Initial runbook for Twin cutover |
