# Cloud Reliability Documentation

## Overview

This document describes cloud response ratio metrics and reliability monitoring for the OIG Proxy.

## Cloud Response Ratio

### Observed Range

Based on analysis of 871,952 frames over 45 days:
- **Minimum ratio**: 0.63
- **Maximum ratio**: 0.982
- **Typical healthy range**: 0.85 - 0.98

### What Affects the Ratio

1. **Network Conditions**
   - Latency to oigservis.cz
   - Packet loss
   - DNS resolution time

2. **Cloud Availability**
   - Server response time
   - Cloud service health
   - Load balancing behavior

3. **Timeout Settings**
   - CLOUD_ACK_TIMEOUT configuration
   - Connection timeout thresholds
   - Retry intervals in hybrid mode

### Monitoring Recommendations

#### Alert Thresholds

| Ratio | Status | Action |
|-------|--------|--------|
| > 0.90 | Healthy | No action |
| 0.80 - 0.90 | Warning | Monitor closely |
| 0.70 - 0.80 | Degraded | Investigate |
| < 0.70 | Critical | Check cloud connectivity |

#### Metrics to Track

- `cloud_connects` - Total cloud connection attempts
- `cloud_disconnects` - Unexpected disconnections
- `cloud_timeouts` - ACK timeout events
- `cloud_errors` - Generic error count
- `cloud_online` - Current cloud availability status

### Troubleshooting Low Ratios

1. **Check network connectivity**
   ```bash
   ping oigservis.cz
   curl -v https://oigservis.cz
   ```

2. **Review proxy logs**
   - Look for timeout warnings
   - Check for connection failures

3. **Verify configuration**
   - CLOUD_ACK_TIMEOUT (default: varies)
   - HYBRID_RETRY_INTERVAL
   - HYBRID_FAIL_THRESHOLD

4. **Check telemetry data**
   - Review cloud_sessions in window_metrics
   - Analyze gap durations

## Related Documentation

- [Connection Lifecycle](connection_lifecycle.md)
- [Signal Timing](signal_timing.md)
