# OIG Proxy v2: Sensor Map Format

`sensor_map.json` is the core metadata file that maps OIG protocol fields to Home Assistant entities. It lives at `addon/oig-proxy/sensor_map.json` and is loaded at runtime from the path configured by `SENSOR_MAP_PATH` (default `/data/sensor_map.json` in the add-on).

---

## Top-Level Structure

```json
{
  "sensors": {
    "<tbl>:<key>": { ... },
    "<tbl>:<key>": { ... }
  },
  "warnings_3f": [
    { "bit": 8, "key": "ERR_PV", ... },
    ...
  ]
}
```

Two top-level keys:

- `sensors`: map from `tbl:key` to sensor metadata objects
- `warnings_3f`: list of warning bit definitions for bitfield error fields

---

## Sensor Key Format

Every key in `sensors` uses the format `<table_name>:<field_name>`, for example:

```
tbl_actual:FV_V1
tbl_actual:ERR_BATT
proxy_status:status
tbl_events:Type
```

The table name comes from the `<TblName>` element in the OIG XML protocol. The field name matches the XML tag for that data value.

Special table names used in the map:
- `tbl_actual`, `tbl_batt_prms`, `tbl_set`, `tbl_box`, `tbl_invertor_prms`, ... (device data tables)
- `proxy_status` (proxy diagnostic entities, always published for the `oig_proxy` device)
- `tbl_events` (Box event log entries)

---

## Sensor Record Fields

Each sensor entry is a JSON object. All fields are present on every entry; optional fields default to `null` or `false`.

### Mandatory Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | English sensor name (used in HA entity name) |
| `name_cs` | string | Czech sensor name (preferred for HA entity name) |
| `unit_of_measurement` | string or null | Physical unit, e.g. `"V"`, `"W"`, `"°C"`, or `""` for dimensionless |
| `device_class` | string or null | HA device class, e.g. `"voltage"`, `"power"`, `"temperature"`, `"connectivity"` |
| `state_class` | string or null | HA state class: `"measurement"`, `"total_increasing"`, or `null` |
| `sensor_type_category` | string | One of `"measured"`, `"diagnostic"`, `"control"` |
| `device_mapping` | string | Sub-device grouping: `"inverter"`, `"battery"`, `"grid"`, `"pv"`, `"load"`, `"proxy"`, etc. |
| `todo` | bool | `true` if this entry needs review or is a placeholder |

### Optional Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `is_binary` | bool | `false` | When `true`, the proxy creates a `binary_sensor` in HA instead of `sensor`. Values are expected to be `0` (OFF) or `1` (ON). |
| `entity_category` | string or null | `null` | HA entity category: `"diagnostic"`, `"config"`, or `null` for normal entities |
| `warnings_3f` | array | absent | List of warning bit definitions for bitfield decoding. See below. |
| `json_attributes_topic` | string | absent | Extra MQTT topic for HA JSON attributes. Only set on a few proxy_status entries. |

---

## Full Entry Example: Measured Sensor

A solar voltage sensor with units and device class:

```json
"tbl_actual:FV_V1": {
  "name": "Napětí string 1",
  "name_cs": "FVE - Napětí string 1",
  "unit_of_measurement": "V",
  "device_class": "voltage",
  "state_class": "measurement",
  "sensor_type_category": "measured",
  "device_mapping": "pv",
  "todo": false
}
```

This creates an HA sensor entity named "FVE - Napětí string 1" under the `pv` device group, with device class `voltage` and unit `V`.

---

## Full Entry Example: Binary Sensor

A connectivity indicator. The `is_binary: true` flag causes the proxy to register this as `binary_sensor` in HA discovery:

```json
"proxy_status:cloud_online": {
  "name": "Cloud připojen",
  "name_cs": "Cloud připojen",
  "unit_of_measurement": "",
  "device_class": "connectivity",
  "state_class": null,
  "sensor_type_category": "diagnostic",
  "device_mapping": "proxy",
  "todo": false,
  "entity_category": "diagnostic",
  "is_binary": true
}
```

HA binary sensor values: `ON` when the integer value is non-zero, `OFF` when zero.

---

## Full Entry Example: Diagnostic System Sensor

A diagnostic entity with no unit and entity_category set to `"diagnostic"`:

```json
"tbl_actual:ID": {
  "name": "Identifikator zaznamu",
  "name_cs": "Systém - ID záznamu",
  "unit_of_measurement": "",
  "device_class": null,
  "state_class": null,
  "sensor_type_category": "measured",
  "device_mapping": "inverter",
  "todo": false,
  "entity_category": "diagnostic"
}
```

Entities with `entity_category: "diagnostic"` are grouped under the "Diagnostic" section in HA and hidden from the main dashboard by default.

---

## Full Entry Example: Control Sensor

A writable setpoint. The `sensor_type_category` is `"control"`:

```json
"tbl_set:T_Room": {
  "name": "Nastavená teplota místnosti",
  "name_cs": "Vytápění - Nastavená teplota místnosti",
  "unit_of_measurement": "°C",
  "device_class": "temperature",
  "state_class": "measurement",
  "sensor_type_category": "control",
  "device_mapping": "inverter",
  "todo": false
}
```

---

## Warning Bit Definitions (`warnings_3f`)

The `warnings_3f` array at the top level defines all warning bits across all error fields. Individual sensor entries do NOT embed this array; the lookup is done by field key.

Format of each element:

```json
{
  "bit": 8,
  "key": "ERR_PV",
  "warning_code": null,
  "remark": "Solar input 1 loss",
  "remark_cs": "Výpadek FV vstupu 1"
}
```

| Field | Type | Description |
|---|---|---|
| `bit` | int | Bit position in the bitfield (not a shift count, the actual bit value, e.g. 8 means `value & (1 << 8)`) |
| `key` | string | The `<field_name>` this bit belongs to (e.g. `ERR_PV`, `ERR_BATT`, `ERR_GRID`) |
| `warning_code` | int or null | Optional warning code from the OIG protocol documentation |
| `remark` | string | English description of the warning |
| `remark_cs` | string | Czech description of the warning |

### How Warning Decoding Works

When the proxy processes a frame and finds a field that appears in `warnings_3f` (matched by `key`), it calls `decode_warnings(value, warnings_list)`. This returns a list of remark strings for each bit that is set.

The result is published as an extra key in the MQTT state payload:

```json
{
  "ERR_BATT": 24,
  "ERR_BATT_warnings": ["Battery Low", "Battery under"]
}
```

The `{field}_warnings` key is always a list. An empty list means no active warnings.

### Warning Example

Given `ERR_BATT = 24` (binary `0b00011000`, bits 3 and 4 set):

The relevant definitions are:
```json
{"bit": 16, "key": "ERR_BATT", "remark": "Battery under"},
{"bit":  8, "key": "ERR_BATT", "remark": "Battery Low"},
{"bit":  4, "key": "ERR_BATT", "remark": "Battery open"},
{"bit":  2, "key": "ERR_BATT", "remark": "Battery voltage too higher"},
{"bit":  1, "key": "ERR_BATT", "remark": "Battery low in hybrid mode"}
```

`24 & (1 << 4)` = `24 & 16` = 16 (set) → "Battery under"
`24 & (1 << 3)` = `24 & 8` = 8 (set) → "Battery Low"

Result: `["Battery under", "Battery Low"]`

---

## `device_mapping` Values

The `device_mapping` field determines which HA sub-device a sensor belongs to. One OIG Box device_id can appear as multiple HA devices, grouped by function:

| Value | Description |
|---|---|
| `inverter` | Main inverter / system sensors |
| `battery` | Battery bank data |
| `grid` | Grid (mains) connection sensors |
| `pv` | Photovoltaic / solar input sensors |
| `load` | Load / consumption sensors |
| `heating` | Heating and boiler sensors |
| `proxy` | Proxy diagnostics (fixed to `oig_proxy` device) |

---

## `sensor_type_category` Values

| Value | Meaning |
|---|---|
| `measured` | A real-time sensor reading from the device |
| `diagnostic` | A status or metadata field, not a primary measurement |
| `control` | A writable setting (setpoint, enable flag, etc.) |

---

## Unknown Sensors

When the proxy receives a frame with a key that has no entry in `sensors`, it:

1. Still publishes the raw value to MQTT (no discovery, no HA entity)
2. Logs the key at DEBUG level
3. Records the key to `/data/unknown_sensors.json`

The unknown sensors file can be reviewed to identify new fields that need to be added to the map. Format:

```json
{
  "tbl_actual:SomeNewField": {
    "first_seen": "2026-01-15T10:30:00",
    "sample_value": 42
  }
}
```

---

## Extending the Sensor Map

To add a new sensor entry:

1. Find the OIG protocol field name (visible in `unknown_sensors.json` or captured payloads)
2. Add an entry to `sensors` with the `tbl:key` format
3. Fill all mandatory fields; set `todo: false` when verified
4. If the field is a bitfield with error codes, add entries to `warnings_3f`
5. If it's a 0/1 binary field, add `"is_binary": true`
6. Restart the add-on to reload the map (or use `MAP_RELOAD_SECONDS` for live reload)

Example skeleton:

```json
"tbl_actual:NEW_FIELD": {
  "name": "New Field Description",
  "name_cs": "Nové pole - Popis",
  "unit_of_measurement": "W",
  "device_class": "power",
  "state_class": "measurement",
  "sensor_type_category": "measured",
  "device_mapping": "inverter",
  "todo": false
}
```

---

## Complete Proxy Status Sensors

The following `proxy_status:*` keys are always present for the `oig_proxy` device:

| Key | Description | Binary |
|---|---|---|
| `proxy_status:status` | Overall proxy status string | |
| `proxy_status:mode` | Current runtime mode (online/offline) | |
| `proxy_status:last_data` | Timestamp of last received frame | |
| `proxy_status:cloud_online` | Cloud connectivity | yes |
| `proxy_status:cloud_session_active` | Active cloud TCP session count | |
| `proxy_status:cloud_session_connected` | Cloud TCP connected | yes |
| `proxy_status:box_connected` | Box TCP connected | yes |
| `proxy_status:box_data_recent` | Recent data from Box | yes |
| `proxy_status:box_connections` | Total Box connection count | |
| `proxy_status:box_connections_active` | Active Box connections | |
| `proxy_status:cloud_queue` | Cloud queue depth | |
| `proxy_status:box_device_id` | Box device ID string | |
| `proxy_status:mqtt_queue` | MQTT publish queue depth | |
| `proxy_status:isnewset_polls` | IsNewSet poll count | |
| `proxy_status:isnewset_last_poll` | IsNewSet last poll timestamp | |
| `proxy_status:isnewset_last_response` | IsNewSet last response | |
| `proxy_status:isnewset_last_rtt_ms` | IsNewSet round-trip time (ms) | |
| `proxy_status:control_queue_len` | Twin control queue length | |
| `proxy_status:control_inflight` | Active Twin control command | |
| `proxy_status:control_last_result` | Last Twin control result | |
