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

The proxy learns the Setting checksum (`ver`) from fresh native Box frames.
To qualify the Setting layout for the first time, it also observes a normal
Cloud Setting and the Box ACK on the same connection. This works with capture
logging disabled and with or without a firmware report.

After a restart or upgrade, the proxy can revalidate a previously qualified
profile from fresh Box frames with device timestamps. A reported change in
firmware, a checksum mismatch, or a conflicting Setting layout sends the
profile back through qualification. The proxy can continue to publish
telemetry while it gathers evidence. A fresh offline installation can read
telemetry and must wait for the initial Cloud exchange to enable local writes.
Check `control_ver_ready` and `control_ver_reason` in the proxy status.

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
