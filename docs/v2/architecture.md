# OIG Proxy v2 architecture

The Home Assistant add-on in `addon/oig-proxy/` sits between an OIG Box and
its Cloud service. It forwards the TCP stream when Cloud routing is active
and publishes validated Box data to a local MQTT broker for Home Assistant.

```text
OIG Box ──TCP── ProxyServer ──TCP── OIG Cloud
                     │
                FrameProcessor ── local MQTT ── Home Assistant
                     │
              Sensor map and confirmed state
```

## TCP sessions and modes

`proxy/server.py` runs an asyncio TCP server. Each Cloud-backed session has
one task per direction. These tasks forward the bytes and inspect complete
frames for telemetry and control evidence. The Box and Cloud stay on their
own TCP streams. `protocol/frame.py` extracts `<Frame>…</Frame>` messages
from the stream and handles trailing CRLF and TCP segmentation.

The native frame carries an outer `<CRC>` field with a decimal CRC-16/MODBUS
checksum. The proxy checks the checksum before feeding a Box frame into
state processing. Ordinary Cloud forwarding retains the original bytes.
A qualified local write can replace a single isolated `IsNewSet` poll when
MQTT control is enabled and the Box has a pending command.

`proxy/mode.py` handles three configured modes.

| Configuration | Runtime behavior |
| --- | --- |
| `online` | Open the Cloud connection for each Box connection. A failed Cloud connect closes the Box session. |
| `hybrid` | Prefer Cloud. After `hybrid_fail_threshold` failures, serve the Box locally. A new Box connection can try Cloud after `hybrid_retry_interval` has passed. |
| `offline` | Serve the Box locally without opening a Cloud connection. |

In an offline session, `proxy/local_ack.py` builds responses for ordinary
polls and tables. A qualified Setting follows its own exchange on the same
session. See the [mode and response details](proxy_modes.md).

## Box identity and MQTT state

`device_id.py` persists the first observed Box identity to
`/data/device_id.json`. The proxy uses the identity to scope local state
and rejects mismatched Box IDs from state publication and local control.

`FrameProcessor` in `sensor/processor.py` combines accepted observations with
the [sensor map](sensor_map.md) and publishes table state through `mqtt/client.py`.
`protocol/parser.py` extracts the table, device ID and device timestamp from
`TblName`, `ID_Device` and `DT`. It filters frames with `ID_SubD` greater than
zero from the state path. Unmapped data fields can still appear in the MQTT
payload.

MQTT state uses `{namespace}/{device_id}/{table}/state`. The default namespace
is `oig_local`. The paho network loop runs in a background thread. Discovery
uses retained `homeassistant/{component}/{unique_id}/config` messages and
availability uses `{namespace}/{device_id}/availability`.

Home Assistant groups Box entities by the `device_mapping` in the sensor map.
The separate `oig_proxy` device holds `proxy_status`, `proxy_control` and
`tbl_events` entities by default. Its `status` and `mode` fields reflect recent
Box data. Use `configured_mode` for the selected routing policy. The
[sensor map reference](sensor_map.md#proxy-status-in-home-assistant) explains
how to interpret the connectivity and command fields.

## Local settings

`twin/handler.py` validates MQTT commands against the Box identity and
`settings_constraints.py`. `twin/state.py` keeps pending settings in memory
and allows a newer request to replace an unsent request for the same setting.
`twin/delivery.py` uses protocol evidence from `protocol/ver_profile.py` to
scope delivery to a qualified Box and protocol layout. MQTT Box writes are
opt-in.

Offline delivery sends one Setting on an eligible `IsNewSet` poll. On a
matching Box ACK, the proxy sends a timed `END` and closes that session. The
receipt and the observed parameter value are separate. A Box event or table
readback can update confirmed state. The confirmed state cache supports MQTT
state restoration after reconnect or proxy restart without marking cached
observations as fresh. See the
[Twin control reference](twin.md) for topics, status and upgrade checks.

## Remote telemetry and local capture

`telemetry/collector.py` and `telemetry/client.py` send operational telemetry
through a separate MQTT connection. Payloads include the Box ID, a hashed
instance identifier, and may include Box network details, events, log excerpts
and settings audit records. Set `telemetry_enabled: false` to disable this
service. See the [telemetry overview](../telemetry_overview.md) for details.

Optional local frame and pcap capture run through `capture/`. See the
[configuration reference](configuration.md) for capture and MQTT options.
