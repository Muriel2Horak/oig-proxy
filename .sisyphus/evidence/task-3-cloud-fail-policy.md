# Cloud Failure Detection Policy Specification

## Overview
This document specifies the cloud failure detection policy used in HYBRID mode to determine when to switch from online to offline operation.

## Policy Components

### 1. Failure Signals
The following cloud communication failures trigger failure counting:

| Signal | Source | Description |
|--------|--------|-------------|
| `connect_failed` | `cloud_forwarder.handle_connection_failed()` | TCP connection to cloud failed |
| `cloud_eof` | `cloud_forwarder.handle_eof()` | Cloud closed connection unexpectedly |
| `ack_timeout` | `cloud_forwarder.handle_timeout()` | No ACK received within CLOUD_ACK_TIMEOUT |
| `cloud_error` | `cloud_forwarder.handle_error()` | General cloud communication error |

#### Timeout Configuration
- **CLOUD_ACK_TIMEOUT**: Default 1800.0 seconds (30 minutes)
- Configurable via `CLOUD_ACK_TIMEOUT` environment variable

#### Connection Failure Conditions
- TCP connection attempt times out
- TCP connection refused
- DNS resolution failure
- Any exception during `asyncio.open_connection()`

### 2. Activation Threshold
- **Threshold**: 3 consecutive failures
- **Config**: `HYBRID_FAIL_THRESHOLD` (default: 1, recommended: 3)
- **Logic**: `fail_count >= fail_threshold` triggers offline mode

### 3. Consecutive Failure Counter
- **Counter**: `HybridModeManager.fail_count` (integer)
- **Increment**: Each call to `record_failure()` increments by 1
- **Reset**: Counter resets to 0 on successful cloud response

### 4. Reset Condition
A successful cloud response resets the failure counter:
- **Trigger**: `HybridModeManager.record_success()` called
- **When**: ACK frame successfully received and forwarded to BOX
- **Action**: `fail_count = 0`, `in_offline = False`

### 5. State Transition
```
ONLINE (fail_count=0, in_offline=False)
    │
    ├─[failure]─> fail_count=1
    │                 │
    │                 ├─[failure]─> fail_count=2
    │                 │                 │
    │                 │                 ├─[failure]─> fail_count=3 ──> OFFLINE
    │                 │                 │                              (in_offline=True)
    │                 │                 │
    │                 │                 └─[success]─> ONLINE (reset)
    │                 │
    │                 └─[success]─> ONLINE (reset)
    │
    └─[success]─> ONLINE (no change)

OFFLINE (in_offline=True)
    │
    ├─[success]─> ONLINE (reset, fail_count=0)
    │
    └─[failure]─> Restart retry window
```

## Implementation Reference

### Key Methods (hybrid_mode.py)
```python
def record_failure(self, *, reason: str | None = None, local_ack: bool | None = None) -> None:
    """Record a cloud failure for HYBRID mode."""
    self.fail_count += 1
    if self.fail_count >= self.fail_threshold:
        self.in_offline = True
        # ... transition to offline

def record_success(self) -> None:
    """Record a cloud success for HYBRID mode."""
    self.fail_count = 0
    self.in_offline = False
    # ... transition to online
```

### Failure Recording Points (cloud_forwarder.py)
- Line 77: `note_failure(reason=reason, local_ack=local_ack)`
- Line 190-191: `record_failure(reason="connect_failed", local_ack=True)`
- Line 232-234: `record_failure(reason="cloud_eof", local_ack=...)`
- Line 274-276: `record_failure(reason="ack_timeout", local_ack=...)`
- Line 343-345: `record_failure(reason="cloud_error", local_ack=...)`

### Success Recording Point (cloud_forwarder.py)
- Line 433: `self._proxy._hm.record_success()` in `forward_ack_to_box()`

## Testability
The policy is testable with a time simulator:
- `HYBRID_FAIL_THRESHOLD` can be patched to 3 for testing
- `CLOUD_ACK_TIMEOUT` can be reduced for faster test cycles
- `HYBRID_RETRY_INTERVAL` controls offline->online retry attempts
- Direct calls to `record_failure()` and `record_success()` for unit testing

## Related Configuration
| Variable | Default | Description |
|----------|---------|-------------|
| `HYBRID_FAIL_THRESHOLD` | 1 | Consecutive failures before offline |
| `CLOUD_ACK_TIMEOUT` | 1800.0 | ACK wait timeout in seconds |
| `HYBRID_RETRY_INTERVAL` | 60.0 | Seconds between offline retry attempts |
| `HYBRID_CONNECT_TIMEOUT` | 10.0 | TCP connection timeout |