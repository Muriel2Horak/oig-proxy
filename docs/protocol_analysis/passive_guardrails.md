# Passive Guardrails Runbook

## Purpose

This runbook documents the non-interference preflight guardrails for OIG Protocol 3-Day Passive Analysis. These guardrails ensure **zero active interference** with live OIG Box communication during data collection.

## User Constraint

> **"bez zasahu do live komunikace"** (no interference in live communication)

This is a hard constraint. The analysis must be entirely passive.

## Data Source

> **"Proxy+telemetry only" - no pcap sniffing**

- Proxy capture database: `/data/payloads.db` (if `capture_payloads=true`)
- Telemetry data: `oig_local/oig_proxy/proxy_status/state` (MQTT)
- **NOT** raw packet capture (pcap), network sniffing, or active probing

---

## What Passive-Only Means

### Passive Collection (ALLOWED)

| Activity | Description | Why It's Safe |
|----------|-------------|----------------|
| **Proxy observation** | Watching frames pass through the proxy | Proxy is already in the communication path |
| **Telemetry reading** | Reading MQTT status topics | Telemetry is already being published |
| **Database querying** | Querying captured frames from SQLite | Capture is already enabled, just reading |
| **Log analysis** | Reading proxy logs | Logs exist independently of analysis |

### Active Interference (FORBIDDEN)

| Activity | Description | Why It's Forbidden |
|----------|-------------|---------------------|
| **MQTT control injection** | Sending control commands via MQTT | Modifies BOX behavior, violates non-interference |
| **Packet capture** | Installing tcpdump/Wireshark hooks | Requires network access beyond proxy |
| **Active probing** | Sending test packets or fuzzing | Interferes with normal communication |
| **Replay attacks** | Replaying captured frames against production | Could cause unintended BOX behavior |
| **Traffic manipulation** | Modifying frames in transit | Violates proxy's forward-only contract |

---

## Forbidden Actions

### 1. MQTT Control (CRITICAL - VETO)

**Configuration flag:** `CONTROL_MQTT_ENABLED`

**Forbidden values:** `true`, `1`, `True`

**Why:** MQTT control allows injecting settings changes to the OIG Box. This is the **most dangerous** interference mode as it directly modifies BOX state.

**Detection:**
- Config file: `CONTROL_MQTT_ENABLED: "true"` in `options.json`
- Environment: `CONTROL_MQTT_ENABLED=true`
- Flag file: `/data/control_mqtt_enabled` contains `"true"` or `"1"`

**Remediation:**
```bash
# In Home Assistant add-on config
control_mqtt_enabled: false

# Or delete flag file
rm /data/control_mqtt_enabled
```

### 2. Active Probing Scripts (CRITICAL - VETO)

**Forbidden script indicators:**
- `probe` in filename
- `scan` in filename
- `inject` in filename
- `replay` in filename
- `fuzz` in filename
- `test_network` in filename
- `active_check` in filename

**Why:** These scripts typically send active network traffic or modify traffic patterns.

**Detection:**
- Checker scans script directories for forbidden indicators
- Scripts found in: `/data/`, `scripts/`, `addon/oig-proxy/scripts/`

**Remediation:**
- Rename or move scripts to `scripts/active_tests/` (excluded from analysis)
- Disable scheduled tasks (cron, systemd timers)

### 3. Force Offline Mode (WARNING)

**Configuration flag:** `FORCE_OFFLINE`

**Forbidden values:** `true`, `1`, `True`

**Why:** Force offline prevents normal BOX-cloud communication. While not directly modifying BOX state, it changes communication patterns and may affect data collection accuracy.

**Detection:**
- Config file: `FORCE_OFFLINE: "true"`
- Environment: `FORCE_OFFLINE=true`

**Remediation:**
```bash
# In Home Assistant add-on config
force_offline: false
```

---

## Required Conditions for Safe Collection

### Minimum Config State

```json
{
  "control_mqtt_enabled": false,
  "force_offline": false,
  "proxy_mode": "online"  // or "hybrid"
}
```

### Minimum Environment State

```bash
CONTROL_MQTT_ENABLED=false
FORCE_OFFLINE=false
PROXY_MODE=online  // or hybrid
```

### Minimum File System State

- No `/data/control_mqtt_enabled` flag file
- No active probe scripts in `/data/`, `scripts/`, or `addon/oig-proxy/scripts/`

---

## How to Verify Passive Mode is Active

### Method 1: Automated Preflight Check (RECOMMENDED)

Run the guardrail checker:

```bash
python scripts/protocol_analysis/check_passive_guardrails.py \
  --out .sisyphus/evidence/task-6-preflight-check.json \
  --verbose
```

**Expected output:**
```json
{
  "passive_mode": true,
  "active_probe_detected": false,
  "forbidden_actions": [],
  "warnings": [],
  "issues": [],
  "warning_details": []
}
```

**Exit code:** `0` (success)

### Method 2: Manual Config Verification

Check key configuration:

```bash
# Check add-on config
cat addon/oig-proxy/options.json | grep -E "(CONTROL_MQTT_ENABLED|FORCE_OFFLINE|PROXY_MODE)"

# Check environment
env | grep -E "(CONTROL_MQTT_ENABLED|FORCE_OFFLINE|PROXY_MODE)"

# Check flag files
ls -la /data/control_mqtt_enabled
```

### Method 3: MQTT Control Verification

Check if control topics are being published:

```bash
# Monitor control topics (if control is enabled, you'll see these)
mosquitto_sub -h core-mosquitto -p 1883 -t "oig_local/#" -v | grep -i control

# If no control messages appear, MQTT control is likely disabled
```

---

## Preflight Checklist

Before starting data collection:

- [ ] Run `check_passive_guardrails.py` with `--verbose`
- [ ] Verify exit code is `0` (pass)
- [ ] Review output JSON: `passive_mode` must be `true`
- [ ] Review output JSON: `active_probe_detected` must be `false`
- [ ] Review output JSON: `forbidden_actions` must be empty array
- [ ] Review warnings (if any) - determine if they block collection
- [ ] Document the check result in evidence directory

---

## Common Failure Scenarios

### Scenario 1: MQTT Control Enabled

**Symptom:** Checker reports `forbidden_config: CONTROL_MQTT_ENABLED`

**Impact:** Cannot start passive collection (safety gate)

**Action:**
1. Check config file: `cat /data/options.json | grep control_mqtt_enabled`
2. Disable control in Home Assistant add-on UI
3. Remove flag file: `rm /data/control_mqtt_enabled`
4. Re-run checker

### Scenario 2: Active Probe Script Found

**Symptom:** Checker reports `active_probe_script: /data/test_probing.sh`

**Impact:** Cannot start passive collection (safety gate)

**Action:**
1. Review the script to confirm it's active probing
2. Move script to excluded directory: `mv /data/test_probing.sh scripts/active_tests/`
3. Cancel any scheduled tasks (crontab, systemd)
4. Re-run checker

### Scenario 3: Force Offline Mode Set

**Symptom:** Checker reports warning `proxy_mode: offline` or `force_offline: true`

**Impact:** May affect data collection (WARNING, not blocking)

**Action:**
1. Evaluate if offline mode is acceptable for your analysis
2. If not, disable force offline in Home Assistant add-on UI
3. Allow normal proxy mode (`online` or `hybrid`)
4. Re-run checker

### Scenario 4: No Config File Found

**Symptom:** Checker reports warning `config_missing`

**Impact:** Relies on environment variables only (may miss checks)

**Action:**
1. Verify add-on config location: `ls /data/options.json`
2. For local dev, create test config: `echo '{}' > options.json`
3. Set environment variables explicitly
4. Re-run checker

---

## Emergency Stop

If you accidentally enable active interference during collection:

1. **STOP the checker:** Ctrl+C or kill the process
2. **Stop data collection:** Halt any running collection scripts
3. **Revert config:** Disable MQTT control, remove force offline
4. **Run checker again:** Verify passive mode restored
5. **Document the incident:** Note in evidence directory

---

## Verification Script Integration

The checker can be integrated into your collection workflow:

```bash
# In your collection script
./check_passive_guardrails.py --out /tmp/preflight.json
RESULT=$?

if [ $RESULT -ne 0 ]; then
  echo "PASSIVE GUARDRAIL CHECK FAILED - Aborting collection"
  cat /tmp/preflight.json
  exit 1
fi

echo "PASSIVE GUARDRAIL CHECK PASSED - Starting collection"
# Proceed with collection...
```

---

## References

- User constraint: "bez zasahu do live komunikace"
- Data source: "Proxy+telemetry only"
- Checker script: `scripts/protocol_analysis/check_passive_guardrails.py`
- Output format: `tasks/6-EXPECTED-OUTCOME.md`

---

## Version History

- **2026-02-19:** Initial version for OIG Protocol 3-Day Passive Analysis
