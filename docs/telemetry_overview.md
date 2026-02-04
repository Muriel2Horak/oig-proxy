# Telemetry + Grafana Overview

This document summarizes how telemetry flows from OIG Proxy through MQTT and Telegraf into InfluxDB and Grafana, plus common failure modes and how to debug them.

## 1) What OIG Proxy Sends

OIG Proxy publishes telemetry periodically to MQTT:

```
oig/telemetry/<device_id>
```

The payload has two parts.

### Top-level (global state)

Examples of fields (not exhaustive):

- `timestamp`, `interval_s`, `uptime_s`
- `mode`, `configured_mode`
- `box_connected`, `box_peer`
- `cloud_online`, `cloud_errors`, `cloud_timeouts`, `cloud_disconnects`
- `frames_received`, `frames_forwarded`
- `mqtt_ok`, `mqtt_queue`
- `set_commands`
- `version`, `device_id`, `instance_hash`

### window_metrics (detail arrays)

Each entry in these arrays is a single record to be stored in Influx:

- `logs[]`
- `tbl_events[]`
- `error_context[]`
- `box_sessions[]`
- `cloud_sessions[]`
- `offline_events[]`

## 2) What Telegraf Does

Telegraf consumes the MQTT payload and splits it into Influx measurements.

### Top-level measurement

- Measurement: `telemetry_top`
- Parses the whole top-level JSON (no `json_query`)
- `device_id` must be a tag (from topic or JSON)
- Fields include `mode`, `mqtt_ok`, `frames_received`, etc.

### window_metrics measurements

Each array is written to its own measurement:

- `telemetry_logs`
- `telemetry_events`
- `telemetry_error_context`
- `telemetry_box_sessions`
- `telemetry_cloud_sessions`
- `telemetry_offline_events`
- `telemetry_stats`

## 3) InfluxDB Behavior (Important)

Influx enforces **field type immutability**:

- If a field (e.g., `mqtt_ok`) is ever written as **string**, it can never be **bool** later.
- Same for numeric vs string, etc.

This is the most common reason data “does not show up” even though it is sent.

**Typical symptom** in logs:
- `field type conflict` or `partial write` errors

**Typical fix**:
- Delete the affected measurement/bucket and re-ingest clean data

## 4) Grafana Dashboards

### Fleet Overview

Uses `telemetry_top` to build:

- `Total Devices`
- `Online/Offline`
- `Avg Uptime`
- Device list table

Device list expects:

- `device_id` (tag)
- `mode`
- `cloud_online`
- `box_connected`
- `version`
- `uptime_s`

The device ID column is also the drilldown link into the Box Detail dashboard.

### Box Detail

- Status tiles use `telemetry_top`
- History graphs use `telemetry_*` measurements
- If any of these are missing, detail panels appear empty

## 5) Common Failure Modes

1. `telemetry_top` fields missing in Influx
   - Telegraf not writing them, or
   - type conflicts prevent ingestion

2. `device_id` missing as a tag
   - Fleet list and drilldown cannot resolve devices

3. window_metrics arrays missing
   - Telegraf `json_query` mismatch
   - payload does not contain expected arrays

## 6) Debug Burst Logging (Telemetry Logs)

By default, logs are **not** sent in telemetry. When a `WARNING` or `ERROR`
is emitted, proxy enables a **debug burst** for the next **2 telemetry windows**
(typically 10 minutes with 5-minute intervals). During the burst, **all log
levels** are included in `telemetry_logs`. After the window expires, logs
stop again.

## 7) Standard Debug Workflow

1. Verify Telegraf writes `telemetry_top` and `telemetry_*` measurements
2. If type conflict is suspected, delete and re-ingest data
3. Publish test payload and confirm in Influx
4. Then validate Grafana queries
