# OIG Proxy Twin control

Set `control_mqtt_enabled` to `true` to enable local Box commands. The proxy
validates commands against the supported settings and their value ranges.

## MQTT commands and confirmed state

The existing MQTT input accepts a JSON object on
`oig/<device_id>/control/set`. For example, to select Home 1:

```json
{"table":"tbl_box_prms","key":"MODE","value":0}
```

Home Assistant discovery uses the existing per-setting command topics, for
example `oig_local/<device_id>/set/tbl_box_prms/MODE` with scalar payload `0`.
Send a fresh command with QoS 0 and retain disabled. Home Assistant's read-only
entity and its `_cfg` counterpart display the last Box-confirmed value. The
`_cfg` entity also accepts requests.

The proxy reports settings in `oig_local/oig_proxy/proxy_status/state`. The
`control_result` object identifies the table, key, value, source, result, and
time. It includes an `event_id` for consumers to deduplicate. A protocol
`ack` confirms that the Box accepted the frame. If an automation depends on
the applied mode or value, wait for the corresponding confirmed state from
the Box. Handle `nack`, `timeout`, and `dropped` as terminal results before
deciding whether to issue a new command.

## Profile qualification

The proxy learns the Setting checksum (`ver`) from the installed Box. It
uses fresh native Box frames and independently observed Cloud Setting and
Box reply evidence to qualify the observed layout. Different Box identities,
firmware contexts, and protocol layouts can qualify on the same rules.

A previously qualified profile can be revalidated from fresh Box frames
with device timestamps after the proxy starts.
A new installation that starts without a qualified profile can read and
publish telemetry while it gathers evidence. An unknown, stale, or changed
profile blocks local writes until it is qualified again. For initial qualification, use hybrid mode and
let the live Box acknowledge a normal Cloud Setting on the same connection.
Then check
`control_ver_ready` and `control_ver_reason` in the proxy status. Qualification
uses live frames and works with capture logging disabled. A fresh install without a previously
qualified profile can read telemetry offline. Local writes wait for the
initial Cloud exchange.

## Offline and hybrid delivery

When offline, the proxy sends a qualified Setting in response to
`IsNewSet`. It waits for the Box ACK on that connection and responds with a
timed `END`. Returning to hybrid closes offline connections so the Box can
reconnect through the Cloud path. A Setting already on the wire can reach
its bounded terminal outcome.

The queue is in memory. A newer request for the same setting can replace an
unsent request. Restart, reconnection, profile changes, and Cloud return do
not replay uncertain writes. Values confirmed since the upgrade can be
restored on MQTT reconnect and proxy restart without emitting a command.

## Upgrade checks

Back up the add-on data before updating. Existing command topics and
entity identities stay the same. After upgrading, verify live telemetry,
profile readiness, and one supported setting's reported result and
confirmed state before enabling automations that depend on local writes.
If a context is still qualifying, check the status reason and allow normal
Box and Cloud exchanges to provide evidence.

See the [Home Assistant walkthrough](local-control.md) for user-facing
controls and the [configuration reference](configuration.md) for installation
limits.
