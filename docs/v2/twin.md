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
The outbound layout and counters come from a normal Cloud Setting and its
matching Box ACK on the same TCP connection. This live qualification works
with capture logging disabled and with or without a firmware report.

After a restart or upgrade, the proxy can revalidate a retained profile from
fresh Box frames. During an upgrade with a local frame archive, it can also
restore missing outbound evidence from the most recent verifiable Cloud
transaction for this Box. The scan covers up to seven days and 250,000
retained frames. It reads the archive without editing it and preserves
higher outbound counters seen after the qualifying exchange.

A reported firmware change, checksum mismatch, or conflicting Setting
layout requires new qualification. Telemetry continues while the proxy
gathers evidence. A fresh offline installation without a verified archive
exchange waits for a confirmed Cloud Setting before accepting local writes.
Check `control_ver_ready` and `control_ver_reason` in the proxy status.

## Offline and hybrid delivery

When offline, the proxy sends a qualified Setting in response to
`IsNewSet`, `IsNewFW`, or `IsNewWeather`. It waits for the Box ACK on that
connection and responds with a timed `END`. Returning to hybrid closes
offline connections so the Box can
reconnect through the Cloud path. A Setting already on the wire can reach
its bounded terminal outcome.

In hybrid mode, each qualified `IsNewSet`, `IsNewFW`, or `IsNewWeather`
can carry one queued command. The proxy substitutes the selected poll
with a local Setting. A Cloud write already in progress retains priority.
During Cloud recovery, the probe forwards polls until it gets a valid
Cloud reply.
Following a matched ACK, a distinct qualified poll can carry the next
command on that connection. If a normal command receives no ACK within
30 seconds of transmission, it can be replayed once on the next eligible
poll. The replay ACK must supply enough evidence to reopen the connection
for a different command. After a replay, the next command is confirmed by
a matching wire identifier, a fresh reply naming a distinct parameter, or a
matching Box change event. A poll can be forwarded when its frame is part of
a larger TCP read, repeats the previous bytes, or arrives during another
write.

The queue is in memory. Adjacent identical requests are deduplicated, and a
newer request for the same setting can replace an unsent request. A request
can wait up to five minutes at the head of the queue, within a 30-minute
batch window. After an accepted write or a native change event, the proxy
can ask for parameters once with `SA` after 60 seconds without a matching
native table. Confirmed values can be restored on MQTT reconnect and proxy
restart without emitting a command.

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
