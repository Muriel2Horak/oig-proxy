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
- `settings_audit[]`

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
- `telemetry_settings_audit`

The `telemetry_settings_audit` measurement is stored in the `telemetry_settings_audit` InfluxDB bucket with **180-day retention**. The broker is `telemetry.muriel-cz.cz:1883`.

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

## 5) Settings Audit Transport Shape

The `window_metrics.settings_audit[]` array carries **settings audit records**,
not generic frame captures. Each record tracks a single settings lifecycle step.

**Transport shape** (one record per step):

```json
{
  "timestamp": "2026-04-20T18:28:39Z",
  "device_id": "ABC123",
  "table": "tbl_set",
  "step": "incoming",
  "result": "pending",
  "audit_id": "aud_20250420120000000_123456",
  "key": "T_Room",
  "session_id": "sess_abc123",
  "msg_id": 42,
  "id_set": 7,
  "value_text": "22",
  "confirmed_value_text": "",
  "value_kind": "int",
  "confirmed_value_kind": "",
  "value_num_float": 22.0,
  "confirmed_value_num_float": null,
  "raw_text": "Set tbl_set T_Room=22",
  "raw_text_truncated": false,
  "raw_text_bytes_original": 23,
  "audit_payload_capped": false
}
```

**Influx schema:**

- **Tags:** `device_id`, `table`, `step`, `result`
- **Fields:** all remaining high-cardinality strings and numeric values

**Storage:** The `telemetry_settings_audit` measurement is written to the
`telemetry_settings_audit` InfluxDB bucket with **180-day retention**.
External stack path: `telemetry.muriel-cz.cz:1883` via MQTT, stored in
`telemetry_settings_audit` bucket.

**Step taxonomy (lifecycle):**

- `incoming` - first seen inbound command
- `rejected_not_allowed` - setting not in allowlist
- `rejected_validation` - value failed validation
- `enqueued` - accepted and queued for delivery
- `superseded` - prior pending setting replaced by new one
- `deliver_selected` - chosen from queue for delivery
- `injected_box` - sent to BOX device
- `ack_box_observed` - BOX acknowledged the setting
- `ack_tbl_events` - confirmed via tbl_events
- `ack_reason_setting` - confirmed via cloud reason=Setting
- `nack` - BOX or cloud rejected
- `timeout` - no response within timeout window
- `session_cleared` - session ended without ACK (graceful shutdown)

**Result enum:**

- `pending` - awaiting further lifecycle step
- `rejected` - setting was rejected
- `superseded` - replaced by another setting for same key
- `confirmed` - successfully confirmed
- `failed` - failed (nack, timeout)
- `incomplete` - session cleared without confirmation

**Important:** `raw_text` is stored **only** for the `incoming` step (the
initial settings flow). Subsequent steps do not carry raw text. This is not a
generic frame-capture mechanism.

## 6) Common Failure Modes

1. `telemetry_top` fields missing in Influx
   - Telegraf not writing them, or
   - type conflicts prevent ingestion

2. `device_id` missing as a tag
   - Fleet list and drilldown cannot resolve devices

3. window_metrics arrays missing
   - Telegraf `json_query` mismatch
   - payload does not contain expected arrays

## 7) Debug Burst Logging (Telemetry Logs)

Logs are collected in a **300-second (5-minute) rolling buffer**. At telemetry
interval, the buffer is flushed and transmitted. The buffer replaces the
previous 60-second fixed window.

**Burst semantics:**

- **WARNING/ERROR burst:** When a log record at `WARNING` or higher is recorded,
  the proxy activates a burst for the **next 2 telemetry windows**. All log
  levels are included during the burst.

- **Setting-triggered burst:** When a settings audit step is recorded
  (`incoming` step), the current window is forced to include logs, and the
  **next window** also has logs forced. This ensures the setting delivery flow
  is captured even if no WARNING+ occurred.

- **Coalescing:** If a WARNING burst and a setting burst overlap, they
  coalesce. The stronger of the two window counts applies. Logs are emitted
  for the combined duration.

**Accepted gap:** If the proxy process crashes abruptly (e.g., SIGKILL, power
loss) before the telemetry interval flushes the buffer, the audit trail for
that window is incomplete. This is an accepted gap. Graceful shutdown (SIGTERM)
emits a `session_cleared` step in the settings audit and flushes pending
records before exit.

## 8) Standard Debug Workflow

1. Verify Telegraf writes `telemetry_top` and `telemetry_*` measurements
2. If type conflict is suspected, delete and re-ingest data
3. Publish test payload and confirm in Influx
4. Then validate Grafana queries
