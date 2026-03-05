# Connection Lifecycle Documentation

## Overview

This document describes connection lifecycle patterns and mode transitions for the OIG Proxy based on analysis of 18,598 transitions over 45 days.

## State Diagram

```
                    ┌─────────────┐
                    │   ONLINE    │
                    │  (9,831)    │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │ cloud failure           │ cloud recovered
              ▼                         │
       ┌─────────────┐                  │
       │   HYBRID    │◄─────────────────┘
       │  (7,065)    │
       └──────┬──────┘
              │
              │ threshold exceeded
              ▼
       ┌─────────────┐
       │   OFFLINE   │
       │   (166)     │
       └─────────────┘
              │
              │ retry interval passed
              ▼
       ┌─────────────────┐
       │ HYBRID_OFFLINE  │
       │    (1,531)      │
       └─────────────────┘
```

## Transition Statistics

| To State | Count | Percentage |
|----------|-------|------------|
| online | 9,831 | 52.9% |
| hybrid | 7,065 | 38.0% |
| hybrid_offline | 1,531 | 8.2% |
| offline | 166 | 0.9% |

**Total transitions**: 18,598

## Transition Triggers

### Online → Hybrid/Offline

1. **Cloud timeout** - ACK not received within CLOUD_ACK_TIMEOUT
2. **Cloud EOF** - Unexpected connection close
3. **Cloud error** - General exception during cloud communication
4. **Connection failure** - Cannot establish cloud connection

### Hybrid → Online

1. **Cloud recovered** - Successful ACK from cloud
2. **Retry interval passed** - HYBRID_RETRY_INTERVAL elapsed

### Hybrid → Offline

1. **Failure threshold exceeded** - HYBRID_FAIL_THRESHOLD consecutive failures

## Cloud Gap Analysis

Based on 66 detected cloud gaps:

- **Minimum gap**: 301 seconds
- **Maximum gap**: 381 seconds
- **Typical gap duration**: 5-6 minutes

### Gap Detection

Gaps are detected by analyzing frame timestamps:
- Absence of cloud_to_proxy frames
- Mode transition events

## Recovery Patterns

### Typical Recovery Sequence

1. Cloud failure detected
2. Hybrid mode switches to offline
3. Local ACK generation begins
4. Retry interval timer starts
5. After retry interval, cloud probe attempted
6. If successful: return to online
7. If failed: extend offline period

### Monitoring Recommendations

Track in telemetry:
- `hybrid_sessions` - State transitions
- `offline_events` - Fallback events with reasons
- `cloud_gap_histogram` - Duration distribution

## Related Documentation

- [Cloud Reliability](cloud_reliability.md)
- [Signal Timing](signal_timing.md)
