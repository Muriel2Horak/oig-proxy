# Twin Architecture Documentation

## Overview

The Twin Architecture replaces legacy offline/hybrid/setting logic with a unified DigitalTwin that manages the complete setting lifecycle from MQTT subscription through BOX delivery to result publishing.

## Key Principles

- **Twin manages settings queue and delivery**: All settings go through the DigitalTwin
- **No data replay to cloud**: Cloud receives only live data, not queued historical data
- **MQTT state entities**: Home Assistant can monitor Twin state via MQTT sensors
- **Automatic SA after successful setting**: Send All command is auto-queued after settings complete
- **Temporary session takeover in ONLINE mode**: Twin takes over only when needed

## Architecture

### Setting Flow (All Modes)

```
Home Assistant ──MQTT──▶ Proxy ──▶ TwinMQTTHandler
                              │
                              ▼
                         Twin._queue
                              │
                    [Wait for BOX TCP session]
                              │
                              ▼
BOX ──▶ IsNewSet poll ──▶ Twin.on_poll()
                              │
                              ▼
BOX ◀── Setting frame ── Twin
                              │
BOX ──▶ ACK ──▶ Twin.on_ack()
                              │
BOX ──▶ tbl_events ──▶ Twin.on_tbl_event()
                              │
                              ▼
                    MQTT publish (applied)
                              │
                    [Auto-queue SA for next poll]
```

### Data Flow (Non-Setting)

```
BOX ──▶ tbl_* ──▶ Proxy ──▶ Cloud (if available)
                    │
                    ▼
            MQTT publish (always)
```

## Components

### DigitalTwin

The core state machine managing settings queue and delivery.

**Key Methods:**
- `queue_setting(dto)` - Add setting to queue
- `on_poll(tx_id, conn_id, table_name)` - Handle BOX poll requests
- `on_ack(tx_id, conn_id, status)` - Process BOX ACK/NACK
- `on_tbl_event(dto)` - Process tbl_events from BOX
- `handle_frame(frame, conn_id)` - Route incoming frames
- `_publish_state()` - Publish state to MQTT

**State:**
- `_queue` - Pending settings queue
- `_inflight` - Currently delivering setting
- `_last_result` - Result of last completed setting
- `_session_active` - Whether Twin session is active

### TwinMQTTHandler

Handles MQTT subscriptions for settings.

**Topic Pattern:** `oig_local/<device_id>/<tbl_name>/<tbl_item>/set`

**Payload:**
```json
{
  "value": "3",
  "request_key": "optional-tracking-id"
}
```

### MQTT State Topic

**Topic:** `oig_local/oig_proxy/twin_state/state` (retained)

**Payload Schema:**
```json
{
  "queue_length": 2,
  "inflight": {
    "tx_id": "uuid",
    "tbl_name": "tbl_box_prms",
    "tbl_item": "MODE",
    "new_value": "3",
    "stage": "delivered",
    "conn_id": 5,
    "timestamp": "2026-03-05T15:30:00Z"
  },
  "last_result": {
    "tx_id": "uuid",
    "status": "applied",
    "tbl_name": "tbl_box_prms",
    "tbl_item": "MODE",
    "new_value": "3",
    "timestamp": "2026-03-05T15:25:00Z",
    "error": null
  },
  "session_active": true,
  "mode": "twin"
}
```

## Home Assistant Integration

### Sensors

The following sensors are automatically discovered via MQTT:

| Sensor | Type | Description |
|--------|------|-------------|
| `twin_state:queue_length` | sensor | Number of queued settings |
| `twin_state:inflight_tx` | sensor | Transaction ID of inflight setting |
| `twin_state:last_command_status` | sensor | Status: pending/delivered/applied/failed |
| `twin_state:session_active` | binary_sensor | True when Twin session is active |
| `twin_state:mode` | sensor | Current mode: online/hybrid/offline/twin |

### Automation Examples

**Monitor setting completion:**
```yaml
automation:
  - alias: "Setting Applied Notification"
    trigger:
      - platform: state
        entity_id: sensor.twin_last_command_status
        to: "applied"
    action:
      - service: notify.mobile_app
        data:
          message: "Setting {{ trigger.from_state.attributes.tbl_item }} applied successfully"
```

**Queue monitoring:**
```yaml
automation:
  - alias: "High Queue Alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.twin_queue_length
        above: 5
    action:
      - service: notify.mobile_app
        data:
          message: "Twin queue has {{ trigger.to_state.state }} pending settings"
```

## Modes

### ONLINE Mode

- **Normal operation**: Settings go to Cloud
- **Twin takeover**: When MQTT setting arrives, Twin takes over for next session
- **Session-based**: Twin activates on new BOX connection after setting queued
- **Data forwarding**: Non-setting frames forwarded to Cloud

### HYBRID Mode

- **Cloud available**: Settings go to Cloud
- **Cloud unavailable**: Twin automatically takes over
- **Auto-detection**: Uses `should_route_settings_via_twin()` method

### OFFLINE Mode

- **Always Twin**: All settings handled by Twin
- **No Cloud**: Data only published to MQTT
- **Queue persistence**: Settings queued until delivered

## SA Auto-Queue

After a successful setting (receives `applied` status), Twin automatically queues a Send All (SA) command:

```python
if event.status == "applied" and original_setting.tbl_item != "SA":
    queue_sa_command()
```

This ensures the BOX sends all current data after a setting change.

## Error Handling

### Invalid MQTT Payload

- Catches `json.JSONDecodeError` and `UnicodeDecodeError`
- Logs error, returns early
- No crash in MQTT callback

### NACK Response

- Setting marked as failed
- Not auto-requeued immediately
- Error logged

### Timeout

- Inflight setting times out after configured duration
- Transaction marked as failed
- Can be retried

## Configuration

No additional configuration required. Twin is automatically enabled when:

1. `digital_twin.py` module is present
2. MQTT publisher is configured
3. Sensor map includes Twin sensors

## Migration from Legacy

### Removed Components

- `control_pipeline.py` - Replaced by Twin
- `pending_frame` logic - Replaced by Twin queue
- `_process_frame_offline()` - Replaced by Twin

### Updated Components

- `proxy.py` - Added Twin routing logic
- `control_settings.py` - Routes to Twin
- `hybrid_mode.py` - Uses `should_route_settings_via_twin()`
- `cloud_forwarder.py` - Removed offline mode handling

## Testing

Run integration tests:

```bash
pytest tests/test_twin_integration.py -v
```

Test scenarios:
1. ONLINE with Twin takeover
2. HYBRID offline with Twin
3. OFFLINE with Twin
4. SA automation
5. MQTT state publishing
6. Error handling

## Troubleshooting

### Twin not activating

1. Check MQTT topic subscription: `oig_local/+/+/set`
2. Verify sensor map has Twin sensors
3. Check logs for MQTT connection errors

### Settings not delivering

1. Check `twin_state:queue_length` sensor
2. Verify BOX is connected (`twin_state:session_active`)
3. Check `twin_state:last_command_status` for errors

### SA not auto-queued

1. Verify setting completed with `applied` status
2. Check original setting was not already SA
3. Review `tbl_events` processing in logs

## API Reference

### QueueSettingDTO

```python
@dataclass(frozen=True)
class QueueSettingDTO:
    tx_id: str
    conn_id: int
    tbl_name: str
    tbl_item: str
    new_value: str
    confirm: str = "New"
    request_key: str | None = None
```

### TransactionResultDTO

```python
@dataclass(frozen=True)
class TransactionResultDTO:
    tx_id: str
    conn_id: int
    status: str
    error: str | None = None
    detail: str | None = None
```

### PollResponseDTO

```python
@dataclass(frozen=True)
class PollResponseDTO:
    tx_id: str | None
    conn_id: int
    table_name: str | None
    has_data: bool
    frame: str | None = None
```

## Changelog

### v1.0.0

- Initial Twin Architecture implementation
- MQTT subscription for settings
- State publishing to MQTT
- SA auto-queue after successful setting
- Session-based Twin activation in ONLINE mode
- HYBRID/OFFLINE mode support
- Integration tests

## References

- Plan: `.sisyphus/plans/twin-architecture-refactor.md`
- Implementation: `addon/oig-proxy/digital_twin.py`
- Tests: `tests/test_twin_integration.py`
- Sensor Map: `addon/oig-proxy/sensor_map.json`
