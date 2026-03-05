# Signal Timing Documentation

## Overview

This document describes observed timing patterns for OIG protocol signals based on analysis of 871,952 frames.

## Timing Summary

| Signal Type | Average (ms) | Typical Range |
|-------------|-------------|---------------|
| ACK echo | 9.7 | 8-12 ms |
| IsNewFW echo | 9.9 | 8-13 ms |
| IsNewSet echo | 16.9 | 12-25 ms |
| IsNewWeather echo | 19.6 | 15-30 ms |
| Settings echo | 27.4 | 20-40 ms |

## Detailed Timing Analysis

### ACK Frames

- **Purpose**: Acknowledgment of successful frame processing
- **Average latency**: 9.7ms
- **Notes**: Fastest response type, indicates basic cloud connectivity

### IsNewFW Frames

- **Purpose**: Firmware update polling
- **Average latency**: 9.9ms
- **Notes**: Similar to ACK timing

### IsNewSet Frames

- **Purpose**: Settings polling
- **Average latency**: 16.9ms
- **Notes**: Slightly longer due to settings validation

### IsNewWeather Frames

- **Purpose**: Weather data polling
- **Average latency**: 19.6ms
- **Notes**: Longer due to weather data fetch

### Settings Echo

- **Purpose**: Settings delivery confirmation
- **Average latency**: 27.4ms
- **Notes**: Longest due to full settings processing

## Performance Tuning

### Timeout Configuration

Adjust `CLOUD_ACK_TIMEOUT` based on observed timing:
- Default: Based on config
- Recommended: 2-3x average response time

### Monitoring Recommendations

Track RTT metrics in telemetry:
- `_isnew_last_rtt_ms` field in proxy status
- Monitor for degradation over time

## Related Documentation

- [Cloud Reliability](cloud_reliability.md)
- [Connection Lifecycle](connection_lifecycle.md)
