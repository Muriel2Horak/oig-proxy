# Retained Topic Migration Policy

## Overview

This document defines the policy for retained MQTT topics during twin activation cutover. It specifies which retained topics are authoritative post-cutover and should be preserved, versus which can be safely cleaned up.

## Problem Statement

During cutover from legacy control to twin-based control, stale retained messages can cause:
1. **Stale value persistence** - Old state values being delivered to new subscribers
2. **Conflicting state** - Twin state vs. legacy state creating confusion in HA
3. **Incorrect device availability** - Old availability states persisting

## Retained Topic Categories

### Category 1: Authoritative Post-Cutover (PRESERVE)

These topics contain authoritative state and MUST be preserved during cutover:

| Topic Pattern | Description | Reason |
|--------------|-------------|--------|
| `oig_local/oig_proxy/control/set` | Control SET topic | Primary control interface |
| `oig_local/oig_proxy/control/result` | Control RESULT topic | Setting results |
| `oig_local/oig_proxy/control/status/#` | Control STATUS topics | Current control state |
| `oig_local/oig_proxy/twin_state/state` | Twin state (retained) | Primary twin state source |
| `oig_local/oig_proxy/proxy_status/state` | Proxy status | Diagnostic state |
| `oig_local/oig_proxy/tbl_events/state` | Table events | Event tracking |
| `homeassistant/#` | HA discovery | Entity configuration |

### Category 2: State Topics (PRESERVE)

Device state topics should be preserved as they contain current device readings:

| Topic Pattern | Description |
|--------------|-------------|
| `oig_local/<device_id>/<tbl_name>/state` | Device state (all tables) |

**Note**: These topics are refreshed by ongoing BOX communication and don't need cleanup.

### Category 3: Legacy Topics (CONDITIONAL CLEAR)

These topics may exist from previous control implementations and should be evaluated:

| Topic Pattern | Action |
|--------------|--------|
| `oig_local/oig_proxy/legacy/#` | Clear if exists |
| `oig_local/oig_proxy/offline/#` | Clear if exists |
| `oig_local/+/+/result` | Evaluate - may contain stale results |

## Stale Detection Policy

A retained message is considered **stale** if:

1. **Timestamp-based**: The payload contains a timestamp field (`timestamp`, `time`, `ts`) that is older than 24 hours from current time
2. **State-based**: The payload contains `last_result.timestamp` older than 24 hours
3. **Empty payload**: The retained message has empty payload (can be cleared safely)

### Stale Payload Examples

#### Stale Twin State
```json
{
  "queue_length": 0,
  "inflight": null,
  "last_result": {
    "tx_id": "abc-123",
    "status": "applied",
    "timestamp": "2026-03-01T12:00:00Z"  // OLD - stale!
  },
  "session_active": false,
  "mode": "twin"
}
```

#### Fresh Twin State
```json
{
  "queue_length": 0,
  "inflight": null,
  "last_result": {
    "tx_id": "abc-456",
    "status": "applied",
    "timestamp": "2026-03-07T10:30:00Z"  // RECENT
  },
  "session_active": true,
  "mode": "twin"
}
```

## Cleanup Procedure

### Pre-Cutover Checklist

1. **Document current retained topics**
   ```bash
   mosquitto_sub -t '#' -v | grep retained
   ```

2. **Run cleanup script in dry-run mode**
   ```bash
   python cleanup_retained_topics.py --dry-run --output pre-cutover-analysis.json
   ```

3. **Review analysis**
   - Verify no Category 1 topics are marked for deletion
   - Note any Category 3 topics that will be cleared

### Post-Cutover Verification

1. **Verify twin state is retained**
   ```bash
   mosquitto_sub -t 'oig_local/oig_proxy/twin_state/state' -v
   ```

2. **Check control topics exist**
   ```bash
   mosquitto_sub -t 'oig_local/oig_proxy/control/#' -v
   ```

3. **Run post-cutover analysis**
   ```bash
   python cleanup_retained_topics.py --dry-run --output post-cutover-analysis.json
   ```

## Allowlist Configuration

The cleanup script uses a default allowlist that should NEVER be modified:

```python
DEFAULT_ALLOWLIST = {
    # Control topics - authoritative post-cutover
    "oig_local/oig_proxy/control/set",
    "oig_local/oig_proxy/control/result",
    "oig_local/oig_proxy/control/status/#",
    
    # Twin state - authoritative post-cutover
    "oig_local/oig_proxy/twin_state/state",
    
    # Proxy status
    "oig_local/oig_proxy/proxy_status/state",
    "oig_local/oig_proxy/tbl_events/state",
    
    # HA Discovery
    "homeassistant/#",
}
```

### Adding Custom Allowlist Entries

To add additional topics to preserve:

```bash
# Via command line
python cleanup_retained_topics.py --allowlist "topic1,topic2,topic3" --dry-run

# Via file
echo "oig_local/my_custom/topic" > allowlist.txt
python cleanup_retained_topics.py --allowlist-file allowlist.txt --dry-run
```

## Emergency Rollback

If stale retained messages cause issues after cutover:

1. **Immediate action**: Clear specific topic
   ```bash
   mosquitto_pub -t 'oig_local/oig_proxy/twin_state/state' -r -n
   ```

2. **Wait for refresh**: Twin will republish state on next poll

3. **Full reset**: Run cleanup with specific topic pattern
   ```bash
   python cleanup_retained_topics.py --allowlist "" --dry-run
   ```

## Monitoring

After cutover, monitor these metrics:

| Metric | Expected Value |
|--------|---------------|
| `twin_state:session_active` | `true` when BOX connected |
| `twin_state:last_command_status` | Fresh timestamp (< 1 hour) |
| `twin_state:queue_length` | Typically `0` |

## References

- Cleanup script: `.sisyphus/evidence/task-13-cleanup-retained.py`
- Twin architecture: `docs/twin_architecture.md`
- Control topics: `addon/oig-proxy/config.py` (`CONTROL_MQTT_*` constants)
