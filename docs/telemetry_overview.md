# OIG Proxy outbound telemetry

The add-on enables outbound telemetry by default. It uses a separate MQTT
connection to `telemetry.muriel-cz.cz:1883` and sends metrics every 300
seconds by default. The telemetry client uses MQTT over TCP without TLS.
Set `telemetry_enabled: false` in the add-on configuration to disable this
feed. Local MQTT state and Home Assistant discovery use the local broker.
See the [configuration](v2/configuration.md) and
[Home Assistant diagnostics](v2/sensor_map.md#proxy-status-in-home-assistant).

## Data sent

Periodic updates go to `oig/telemetry/<device_id>`. Lifecycle events such
as startup go to `oig/events/<device_id>`. The topics and payloads contain
the Box ID. The payloads also carry the add-on version, timestamp and hashed
instance identifier.

Periodic updates can include the following installation data.

- Box and OIG Cloud connection metrics, including Box peer address, session
  durations, connection errors and frame counts.
- Configured mode and routing metrics. When configured for hybrid, the
  outbound `mode` reports `hybrid`. `cloud_online` summarizes upstream
  connectivity observed in the telemetry window.
- Device data such as firmware, reported latitude, signal strength,
  Box events and event content.
- Settings commands, audit records, error context and log excerpts.

The `window_metrics` object carries record groups including `box_sessions`,
`cloud_sessions`, `hybrid_sessions`, `offline_events`, `tbl_events`,
`error_context`, `logs`, `stats` and `settings_audit`. Log excerpts use a
rolling 300-second buffer. Warnings and errors extend the log burst. An
incoming settings audit step requests logs for the current and next
windows. Windows without Box activity can also include logs.

If the service is unreachable, the client can buffer telemetry payloads
in `/data/telemetry_buffer.db` and send them after connectivity returns.
Events and audit records still in memory are subject to loss on process
exit.

## Settings audit records

Each `settings_audit` entry records a lifecycle step and carries an
`audit_id` to correlate it with other steps for the same request. Values,
protocol IDs and the session ID are included where available.

| Step | Meaning |
| --- | --- |
| `incoming` | Command received. |
| `rejected_not_allowed`, `rejected_validation` | Command denied by the control policy or validation. |
| `enqueued`, `superseded` | Command queued or replaced before delivery. |
| `deliver_selected`, `injected_box` | Command selected or written to the Box socket. |
| `ack_box_observed`, `ack_reason_setting` | Protocol acknowledgement evidence. |
| `ack_tbl_events` | A Box event has been attributed to the command. |
| `nack`, `timeout`, `session_cleared` | Rejection, response deadline, or an incomplete session. |

A protocol acknowledgement records receipt. Check a confirmed Box value
before using the new setting in an automation. See the
[Twin control reference](v2/twin.md#mqtt-commands-and-confirmed-state).

Audit records can carry command or frame excerpts in `raw_text`. They
redact text for sensitive-looking setting keys and cap text at 16 KiB per
field and 64 KiB for an audit ID within the tracking window. The fields
`raw_text_truncated`, `raw_text_bytes_original` and `audit_payload_capped`
describe those limits. Event text and log excerpts may contain installation
or setting details.

## Telegraf, InfluxDB and Grafana

The telemetry pipeline uses Telegraf to write the top-level fields and
window records into separate InfluxDB measurements. Fleet Overview reads
`telemetry_top`, while Box Detail also uses measurements such as
`telemetry_logs`, `telemetry_events`, `telemetry_box_sessions`,
`telemetry_cloud_sessions` and `telemetry_settings_audit`. The `device_id`
tag connects the fleet list to Box Detail.

For missing dashboard data, verify the broker message arrives, check
Telegraf's field mappings and measurement tags, then inspect the InfluxDB
write logs and Grafana query. A `field type conflict` or `partial write`
error can indicate a mismatch in the written field type. Retention and
measurement storage are server-side settings.
