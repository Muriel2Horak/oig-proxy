# OIG Proxy v2: Twin (Device Settings via MQTT)

The "Twin" feature allows pushing settings to the OIG Box through the proxy using MQTT. The name comes from the "device twin" concept: a desired state that the proxy delivers to the physical device at the next opportunity.

The implementation lives in `addon/oig-proxy/twin/`.

---

## Overview

The OIG Box periodically sends an `IsNewSet` frame to ask the cloud "do you have any new settings for me?" In normal operation the cloud responds with `END` (no new settings) or delivers settings in XML format.

When the Twin feature is enabled (`control_mqtt_enabled: true`), the proxy intercepts these `IsNewSet` frames and delivers any pending settings from its in-memory queue. This works in all three proxy modes (ONLINE, HYBRID, OFFLINE).

---

## Components

### `TwinQueue` (`twin/state.py`)

In-memory queue of pending settings. Keyed by `(table, key)` tuple.

- A newer setting for the same `(table, key)` **overwrites** the older one. Queue size stays 1 per unique key.
- Settings are ordered by `enqueued_at` timestamp for delivery (oldest first).
- The queue is not persisted. Proxy restart clears all pending settings.

```python
@dataclass
class TwinSetting:
    table: str        # e.g. "tbl_set"
    key: str          # e.g. "T_Room"
    value: Any        # e.g. 22
    enqueued_at: float
```

### `TwinControlHandler` (`twin/handler.py`)

Subscribes to the MQTT control topic and enqueues settings.

Topic: `oig/{device_id}/control/set`

Payload: JSON object with `table`, `key`, and `value` fields.

```json
{
  "table": "tbl_set",
  "key": "T_Room",
  "value": 22
}
```

### `TwinDelivery` (`twin/delivery.py`)

Handles the actual delivery of pending settings to the Box. Called from `ProxyServer` when an `IsNewSet` frame is seen.

`deliver_pending(device_id)` returns all pending settings from the queue. The proxy then builds and sends an XML frame for each setting.

`acknowledge(table, key)` removes a setting from the queue after the Box confirms it.

### `ack_parser.py` (`twin/ack_parser.py`)

Parses Box ACK responses to detect which setting was acknowledged.

Looks for:
- `<Result>ACK</Result>` or `<Result>END</Result>`
- `<TblName>...</TblName>` (which table was applied)
- `<ToDo>...</ToDo>` (which key was applied)
- `<DT>...</DT>` (timestamp)

---

## MQTT Topics

### Control Topic (inbound)

The proxy **subscribes** to:

```
oig/{device_id}/control/set
```

Where `{device_id}` is the OIG Box device ID detected from the first frame.

Example: if the Box device ID is `ABC123`, subscribe to `oig/ABC123/control/set`.

This topic is only subscribed when `control_mqtt_enabled: true` in config.

### Acknowledgement (no separate topic)

There's no outbound ACK topic. The proxy logs when a setting is acknowledged. If you need to know the current queue state, check `proxy_status:control_queue_len` and `proxy_status:control_inflight`.

---

## Control Payload Format

The control topic expects a JSON object:

```json
{
  "table": "<tbl_name>",
  "key": "<field_name>",
  "value": <value>
}
```

All three fields are required. Missing any field causes the message to be rejected with a warning log.

### Examples

Set room temperature setpoint to 21°C:
```json
{
  "table": "tbl_set",
  "key": "T_Room",
  "value": 21
}
```

Enable a feature flag:
```json
{
  "table": "tbl_set",
  "key": "ISON_BATT_LOAD",
  "value": 1
}
```

Set a string value:
```json
{
  "table": "tbl_set",
  "key": "SSID",
  "value": "MyWifi"
}
```

---

## IsNewSet Delivery Flow

### Step-by-Step

```
Box sends: <TblName>IsNewSet</TblName><ID_Device>ABC123</ID_Device>...
                │
                v
ProxyServer._handle_twin_frames(frame_bytes, box_writer)
                │
                ├── infer_table_name() → "IsNewSet"
                │
                └── _deliver_pending_for_isnewset(frame_text, box_writer)
                        │
                        ├── parse_xml_frame(frame_text) → parsed_frame
                        ├── device_id = parsed_frame["_device_id"]
                        │
                        ├── twin_delivery.deliver_pending(device_id)
                        │    └── returns list[TwinSetting] (oldest first)
                        │
                        └── for each setting:
                              payload = build_setting_xml(table, key, value)
                              frame = build_frame(payload)
                              box_writer.write(frame)
```

### Setting XML Format

`TwinDelivery.build_setting_xml(table, key, value)` produces:

```xml
<TblName>{table}</TblName><{key}>{value}</{key}>
```

Example for `table="tbl_set", key="T_Room", value=22`:

```xml
<TblName>tbl_set</TblName><T_Room>22</T_Room>
```

This is then wrapped in a complete OIG frame (length prefix + CRC) using `build_frame()` before writing to the Box.

### Box ACK Flow

After the Box applies a setting, it sends back:

```xml
<Result>ACK</Result><TblName>tbl_set</TblName><ToDo>T_Room</ToDo><DT>2026-01-15 10:30:00</DT>
```

`ack_parser.parse_box_ack(xml_bytes)` returns:

```python
{
    "result": "ACK",
    "table": "tbl_set",
    "todo": "T_Room",
    "timestamp": "2026-01-15 10:30:00"
}
```

`ProxyServer._handle_twin_frames` checks for this pattern:

```python
if (
    parsed_ack
    and parsed_ack.get("result") == "ACK"
    and parsed_ack.get("table")
    and parsed_ack.get("todo")
):
    self.twin_delivery.acknowledge(parsed_ack["table"], parsed_ack["todo"])
```

This removes the setting from `TwinQueue`. If the setting was still in inflight state, the proxy also republishes the confirmed `{key: value}` update through `FrameProcessor`, so MQTT/HA state stays aligned with the Box after the ACK/event confirmation.

---

## Queue Behavior

### Deduplication

If you publish two settings for the same `(table, key)`:

```json
{"table": "tbl_set", "key": "T_Room", "value": 21}
{"table": "tbl_set", "key": "T_Room", "value": 22}
```

Only the second one (value=22) is kept. The queue size stays 1 for this key.

### Multiple Settings

Different keys are independent:

```json
{"table": "tbl_set", "key": "T_Room", "value": 22}
{"table": "tbl_set", "key": "ISON_BATT_LOAD", "value": 1}
```

Both are queued. On the next `IsNewSet`, both are delivered in enqueue order (oldest first).

### No Persistence

The queue is in-memory only. If the proxy restarts while settings are pending, they're lost. Applications that need reliable delivery must re-publish the settings after proxy restart (e.g., by checking `proxy_status` via MQTT).

### Delivery in Offline Mode

The Twin delivery works identically in OFFLINE mode. The `IsNewSet` frame still triggers `_handle_twin_frames`. The local ACK builder handles `IsNewSet` with pending data:

```python
if table_name == "IsNewSet":
    if has_queued_data:
        return build_end_frame_with_timestamp()  # signal: "yes, I have data"
    return build_ack_only_frame()               # signal: "no data"
```

The `has_queued_data` flag is set when the twin queue is non-empty. The `END` response with timestamp tells the Box to expect a settings push.

---

## Full Flow Diagram

```
User/HA automation
    │
    └── MQTT publish to oig/ABC123/control/set
          payload: {"table": "tbl_set", "key": "T_Room", "value": 22}
                │
                v
        TwinControlHandler._on_message()
                │
                └── TwinQueue.enqueue("tbl_set", "T_Room", 22)
                      queue: {("tbl_set","T_Room"): TwinSetting(value=22)}


Later, Box sends IsNewSet frame:

OIG Box ──frame──> ProxyServer._pipe_box_to_cloud()
                        │
                        ├── extract_frame_from_buffer() → frame_bytes
                        ├── _handle_twin_frames(frame_bytes, box_writer)
                        │     │
                        │     └── _deliver_pending_for_isnewset()
                        │           │
                        │           ├── deliver_pending("ABC123")
                        │           │    returns [TwinSetting(tbl_set, T_Room, 22)]
                        │           │
                        │           └── build_setting_xml("tbl_set", "T_Room", 22)
                        │                = "<TblName>tbl_set</TblName><T_Room>22</T_Room>"
                        │                → wrapped in frame → written to Box
                        │
                         └── _process_frame() → MQTT publish (only if frame carries real data)


Box applies setting and sends ACK:

OIG Box ──ACK──> ProxyServer._pipe_box_to_cloud()
                    │
                    └── _handle_twin_frames(ack_bytes, box_writer)
                          │
                          ├── parse_box_ack() → {result:"ACK", table:"tbl_set", todo:"T_Room"}
                          ├── or parse_tbl_events_ack() → {table:"tbl_set", key:"T_Room", value:"22"}
                          ├── acknowledge() removes inflight/queued item
                          └── confirmed value is republished to MQTT state
                          │
                          └── twin_delivery.acknowledge("tbl_set", "T_Room")
                                → TwinQueue removes ("tbl_set","T_Room")
                                queue: {} (empty)
```

---

## Error Handling

### Invalid JSON

If the MQTT control message isn't valid JSON, `TwinControlHandler` logs a warning and discards the message:

```
TwinControlHandler: Failed to parse JSON on oig/ABC123/control/set: ...
```

### Missing Fields

If `table`, `key`, or `value` is absent from the JSON:

```
TwinControlHandler: Invalid message format on oig/ABC123/control/set: missing table/key/value
```

### MQTT Not Ready

If MQTT isn't connected when `TwinControlHandler.start()` is called, the subscription is skipped:

```
TwinControlHandler: MQTT not ready, cannot subscribe
```

### Box Write Failure

If writing a setting frame to the Box fails (TCP error), the delivery loop breaks. The setting remains in the queue and will be re-attempted on the next `IsNewSet`.

---

## Enabling Twin Control

Set `control_mqtt_enabled: true` in the add-on config. The `TwinControlHandler` is only started when MQTT is connected:

```python
if mqtt.is_ready():
    twin_handler = TwinControlHandler(
        mqtt=mqtt,
        twin_queue=twin_queue,
        device_id=mqtt_device_id,
    )
    await twin_handler.start()
```

If MQTT connects after startup, `TwinControlHandler` won't auto-subscribe. Restart the add-on to activate the subscription.

---

## Using Twin from Home Assistant

You can send settings from HA automations, scripts, or via the MQTT integration's publish service.

### Using the MQTT publish service in HA

```yaml
service: mqtt.publish
data:
  topic: oig/{{ states('sensor.oig_proxy_box_id') }}/control/set
  payload: >
    {"table": "tbl_set", "key": "T_Room", "value": 22}
```

### Checking queue status

The `proxy_status:control_queue_len` sensor shows how many settings are pending delivery. Once the Box acknowledges them, it drops to 0.

`proxy_status:control_inflight` shows the key currently being applied (or empty if none).

`proxy_status:control_last_result` shows the last ACK/NACK result.

---

## Settings Audit Lifecycle

When a setting command flows through the proxy, the telemetry system records each
step in the `settings_audit` array. This is **not** a generic frame-capture
mechanism. `raw_text` is captured at the `incoming` step and is lifecycle-continuous:
the same redacted value (truncated to 16 KiB if needed) follows the record through
every subsequent lifecycle step, flagged via `raw_text_truncated`,
`raw_text_bytes_original`, and `audit_payload_capped`.

### Step Taxonomy (lifecycle stages)

| Step | Meaning |
|------|---------|
| `incoming` | First seen inbound command via MQTT control topic |
| `rejected_not_allowed` | Setting key is not in the allowlist |
| `rejected_validation` | Value failed validation |
| `enqueued` | Accepted and placed in the twin queue |
| `superseded` | A newer setting for the same `(table, key)` replaced this one |
| `deliver_selected` | Chosen from the queue for delivery to the Box |
| `injected_box` | Sent to the Box as an XML frame |
| `ack_box_observed` | Box acknowledged the setting |
| `ack_tbl_events` | Confirmed via a `tbl_events` frame from the Box |
| `ack_reason_setting` | Confirmed via cloud `reason=Setting` response |
| `nack` | Box or cloud rejected the setting |
| `timeout` | No response received within the timeout window |
| `session_cleared` | Session ended without confirmation (graceful shutdown) |

### Result Enum

| Result | Meaning |
|--------|---------|
| `pending` | Awaiting further lifecycle steps |
| `rejected` | Setting was rejected (not_allowed or validation) |
| `superseded` | Replaced by another setting for the same key |
| `confirmed` | Successfully confirmed by the Box |
| `failed` | Failed (nack or timeout) |
| `incomplete` | Session cleared without confirmation |

### Lifecycle Flow

```
incoming → enqueued → deliver_selected → injected_box
                                              ↓
                              ack_box_observed → ack_tbl_events → confirmed
                                              ↓
                              ack_reason_setting → confirmed
                                              ↓
                              nack → failed
                              timeout → failed
                              session_cleared → incomplete (graceful shutdown)
```

When a newer setting arrives for the same `(table, key)` before the earlier one
is confirmed, the earlier audit record transitions to `superseded` and the newer
one starts at `incoming`.

### Raw Text Scope

`raw_text` is captured at the `incoming` step and is **lifecycle-continuous**:
the same redacted value (truncated to 16 KiB if needed, flagged via
`raw_text_truncated`, `raw_text_bytes_original`, `audit_payload_capped`) follows
the record through every subsequent lifecycle step. The mechanism is scoped to
settings-flow tracking, not a generic frame-capture export.

**Verification status:** Remote-stack fixture-based verification is complete.
A live cloud/local-HA setting-command end-to-end validation is intentionally
postponed until deployment testing.

### Session Termination

- **Graceful shutdown (SIGTERM/SIGINT):** Any inflight settings record their
  current step, then a `session_cleared` step is added with result `incomplete`.

- **Abrupt crash:** The telemetry buffer is not flushed. The audit trail for
  the current window is incomplete. This is an accepted operational gap.

### Log Burst Semantics

Logs are collected in a **300-second (5-minute) rolling buffer**. When a
settings audit step is recorded (`incoming` step), the current window is
forced to include logs, and the **next window** also has logs forced. This
ensures the setting delivery flow is captured even if no WARNING+ occurred.
Overlapping bursts coalesce; the stronger window count applies.

### Broker and Retention

The settings audit telemetry is published to the same telemetry broker as other
telemetry data (`telemetry.muriel-cz.cz:1883`). It is stored in the
`telemetry_settings_audit` InfluxDB bucket with **180-day retention**.
