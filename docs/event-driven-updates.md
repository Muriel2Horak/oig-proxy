# Event-driven updates

BOX `tbl_events` frames are semantic observations, not transport ACKs. Version 2.2.0
keeps two event uses deliberately separate.

## Local-setting execution evidence

An exact CRC-valid `tbl_events`, `Type=Setting` event may confirm one durable local
command when device, table, item, canonical value, UUID/session ownership, deadline,
and ordering constraints match. The event receipt is inserted in SQLite before command
state changes, so repeated evidence is idempotent. Only the committed transition to
`confirmed` publishes the local setting state to MQTT/Home Assistant.

The earlier `ACK/Setting` transport frame proves delivery only and moves the command to
`awaiting_event`; it never publishes confirmed state. Cloud-originated Settings and
their observations remain passive telemetry and cannot mutate a local transaction.

## Ordinary sensor change events

Other allowlisted `tbl_events` observations, including `Type=Change`, may update the
normal sensor projection. They do not confirm a local Setting merely because their text
looks related. Invalid CRC, malformed content, unknown targets, non-finite/off-step
values, wrong direction/device/session, ambiguous evidence, and expired evidence remain
observable but cannot change the durable local command.

## Processing path

Complete frames are bounded before parsing. The BOX read pump registers strict event
evidence before its next await; the single dialogue router commits matching/deduplication
and invokes publication only after the store transaction succeeds. Cleanup flushes all
registered events for the connection before deciding whether an owned attempt needs a
retry or failure transition.

See [`v2/twin.md`](v2/twin.md) for the state machine and residual sequential ACK/NACK
limitation.
