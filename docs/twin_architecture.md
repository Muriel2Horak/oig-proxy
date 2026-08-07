# Twin architecture compatibility note

This path previously described the process-memory DigitalTwin prototype. That design
was removed in 2.2.0: there is no `_queue`/global inflight owner, no automatic SA command,
no wildcard control topic, and no ACK-based applied state.

The maintained architecture is split across:

- [`v2/architecture.md`](v2/architecture.md) for component ownership and startup;
- [`v2/twin.md`](v2/twin.md) for durable transaction, evidence, retry, and rollback
  semantics;
- [`v2/proxy_modes.md`](v2/proxy_modes.md) for cloud-first ONLINE/HYBRID and exactly-one
  OFFLINE behavior;
- [`v2/configuration.md`](v2/configuration.md) for the exact MQTT topic and shipped
  limits.

All 2.2.0 local work is stored in `/data/twin_queue.db`, claimed per exact learned
device and UUID connection, written through the serialized BOX writer, moved from
`awaiting_ack` to `awaiting_event` by transport ACK, and confirmed only by exact durable
`tbl_events`, `Type=Setting` evidence.
