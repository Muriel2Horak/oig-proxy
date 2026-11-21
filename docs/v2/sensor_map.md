# OIG Proxy v2 sensor map

The bundled [`sensor_map.json`](../../addon/oig-proxy/sensor_map.json) maps
Box fields to Home Assistant MQTT discovery metadata. The add-on reads
`/data/sensor_map.json`. At startup it backs up and replaces that file if
it differs from the bundled map. Standalone runs can select the source map
with `SENSOR_MAP_PATH`.

## Sensor entries

The top-level `sensors` object is indexed by `<table>:<field>`. The table
and field names normally come from the Box XML. The proxy also defines
metadata for local `proxy_status` and `proxy_control` data.

| Field | Purpose |
| --- | --- |
| `name_cs`, `name` | Display name. Discovery prefers `name_cs`, then `name`, then the field key. |
| `unit_of_measurement` | Measurement unit, such as `W` or `°C`. |
| `device_class` | Home Assistant device class, such as `power` or `connectivity`. |
| `state_class` | Home Assistant state class, such as `measurement` or `total_increasing`. |
| `device_mapping` | Device group within one Box ID, such as `inverter`, `battery`, `grid`, `pv`, `load`, `box` or `boiler`. The `proxy` group is used for proxy diagnostics. |
| `is_binary` | Discover a `binary_sensor` with values `0` and `1` when set to `true`. |
| `entity_category` | Home Assistant category, such as `diagnostic` or `config`. |
| `enum_map` | Translate raw values into display names, such as `0` to `Home 1` and `3` to `Home UPS` for Box `MODE`. |
| `warnings_3f` | Optional per-field warning bits. See [warning definitions](#warning-definitions). |
| `sensor_type_category`, `todo` | Include metadata about the field and its review status. |

For example, `tbl_actual:FV_P1` appears as a PV power sensor in watts.

```json
{
  "tbl_actual:FV_P1": {
    "name": "Výkon string 1",
    "name_cs": "FVE - Výkon string 1",
    "unit_of_measurement": "W",
    "device_class": "power",
    "state_class": "measurement",
    "sensor_type_category": "measured",
    "device_mapping": "pv",
    "todo": false
  }
}
```

The control policy determines which fields qualify for `_cfg` discovery
and writes. See [local controls](local-control.md) and the
[Twin control reference](twin.md) for activation, validation and confirmed
state.

## Warning definitions

The loader groups the top-level `warnings_3f` definitions under the matching
`tbl_invertor_prms` fields. The vendor's top-level `bit` column contains masks,
so `bit: 8` there tests `value & 8`. A per-field `warnings_3f` list overrides
the top-level group and uses bit indices. For example, `bit: 3` tests
`value & (1 << 3)`. An explicit `mask` takes precedence in either location.
Entries may also provide `remark`, `remark_cs` and `warning_code`.

The MQTT state retains the original integer and includes `<field>_warnings`,
`<field>_warnings_cs` and `<field>_warning_codes`. Entries marked `kind: status`
appear in `<field>_status_cs`. The bundled PV masks for unavailable inputs use
that status list. The PV overvoltage masks use the warning list. Each new mask
replaces the derived lists, including empty lists when no entries are active.

### Fault summary in Home Assistant

**OIG Diagnostika → Souhrn chyb** (`sensor.oig_local_oig_proxy_fault_summary_summary`)
shows the state of the most recently accepted physical warning masks. Its
`active_faults` attribute lists decoded active warnings. `unverified_signs`
shows raw bits outside the verified mask definitions. A zero clears the
corresponding warning and keeps its original occurrence in `recent_records`.
The proxy waits for new mask observations after a restart or loss of BOX
availability.

`recent_records` holds up to ten records with Czech labels, source tables,
original values and timestamps. A separate `last_code` carries the last nonzero
`CODE_ERR`. A standalone code and a BOX warning event record what the BOX
reported at a particular time. They provide no independent confirmation that
the fault is still active. For an unset BOX clock, the record uses the proxy
reception time and preserves the original timestamp.

The code labels use the CBB Modbus table and the
[FSP HySpirit fault table](https://www.fsp-group.com/download/user_manual/HySpiritThreePhase_UserManual.pdf).
[OIG Easy](https://www.oigpower.cz/data/cbb-1f-easy-manual.pdf) and other
inverter manuals differ on some assignments, so the summary uses general
labels for codes 32, 34 and 35. Unverified values retain their code number.

## Unmapped fields and changes

The processor logs a `Missing sensor_map entry` warning once per unmapped
`table:field` and can publish the accepted raw value in the table payload.
Home Assistant discovery requires a metadata entry. To extend the map,
find the original field in MQTT or a captured frame, add its metadata to
`addon/oig-proxy/sensor_map.json`, and restart the proxy with the updated map.

## Proxy status in Home Assistant

The default status topic is `oig_local/oig_proxy/proxy_status/state`.
The diagnostics describe the view from the application at publication time.

| Payload field | Meaning |
| --- | --- |
| `status`, `mode` | `online` when the proxy recorded Box data in the last 90 seconds. With older or absent data the value is `offline`. |
| `configured_mode` | Selected routing policy, `online`, `hybrid` or `offline`. |
| `box_connected`, `box_data_recent` | Binary indicators derived from the same 90-second Box data window. |
| `box_device_id`, `frame_count`, `last_frame_table` | Known Box identity and last reported frame details. |
| `last_data`, `last_data_update`, `last_data_age_s` | Last recorded Box frame time and its age. Timestamp fields appear after the first frame. |
| `mqtt_connected`, `connection_status` | Local MQTT connection state. |
| `cloud_online`, `cloud_session_connected` | Legacy fields that currently mirror the local MQTT connection state. |
| `cloud_connects`, `cloud_disconnects`, `cloud_timeouts`, `cloud_errors` | Counters from the Cloud TCP path. |
| `control_queue_len`, `control_inflight` | Pending command count and current in-flight details. Present when provided by the runtime. |
| `control_ver_ready`, `control_ver_reason` | Whether the Box profile allows local delivery and the current qualification reason. |
| `control_last_result`, `control_result` | Latest command result summary and structured result. The structured result is exposed in the result sensor's attributes. |

The map retains metadata for older diagnostic fields, including queue depth
and IsNewSet timing. Discovery in this release uses the fields the status
publisher emits. The separate [outbound telemetry](../telemetry_overview.md)
feed has its own connection and window metrics.
