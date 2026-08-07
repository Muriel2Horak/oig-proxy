# OIG Proxy v2.2 Architecture

OIG Proxy is an asyncio TCP proxy between one OIG BOX and `bridge.oigpower.cz`. It validates framed XML, preserves transparent cloud communication, projects sensor state to MQTT/Home Assistant, and optionally executes durable local-setting transactions.

## Runtime ownership

```text
BOX TCP
  -> ProxyServer / SettingDialog
       -> cloud TCP (cloud-first ONLINE/HYBRID)
       -> SerializedBoxWriter -> BOX
       -> FrameProcessor -> MQTT state/discovery

exact-device MQTT control
  -> TwinControlHandler
  -> TwinCommandStore
  -> TwinCoordinator
  -> SettingDialog
  -> SerializedBoxWriter
```

The ownership boundary is `Store -> Coordinator -> SettingDialog -> SerializedBoxWriter`:

- `TwinCommandStore` is the sole durable authority for device identity, ingress, commands, attempts, transitions, audit delivery, and event receipts.
- `TwinCoordinator` performs all-settled lifecycle mutation, claim, deadline, ACK/NACK, event, cancellation, and audit reconciliation.
- `SettingDialog` owns one correlated `IsNewSet` exchange and decides whether exact cloud bytes remain authoritative or one committed local Setting replaces the terminal response.
- `SerializedBoxWriter` is the only proxy-to-BOX write path and records write milestones around `drain()`.

No component may publish confirmed state from transport evidence alone.

## Startup order

```text
Config and logging
  -> load persisted device identity
  -> open /data/twin_queue.db
  -> validate schema/integrity/pragmas/accounting
  -> recover leases, attempts, and deadlines
  -> create coordinator and proxy
  -> connect MQTT
  -> start exact-device subscription only when enabled and identity is known
  -> start BOX listener and health tasks
```

Store recovery before subscriptions prevents new ingress from racing old transaction reconciliation. A disabled restart still validates and preserves the store but starts no control handler and cannot write a local Setting.

## TCP and dialogue model

`ProxyServer` bounds complete frames to 1 MiB, validates CRC and XML, assigns each connection a UUID owner, and routes bytes by direction. ONLINE/HYBRID uses cloud-first dialogue ownership: BOX polling reaches cloud before local eligibility is considered. OFFLINE uses the same dialog state machine with a local exactly-one response table.

Concurrent BOX sessions cannot share transaction ownership. Stale session writers, wrong direction, timeout, cancellation, or correlation mismatch fail closed. Bidirectional cleanup closes peer tasks and sockets while preserving the primary/cleanup error chain.

## Persistence and recovery

The schema-v1 SQLite database is `/data/twin_queue.db`. It uses foreign keys, WAL, busy timeout, static/parameterized statements, immutable attempt bytes, and append-only transitions. Recovery never guesses from process memory. A future or corrupt schema is not downgraded or recreated.

A 2.1.1 rollback ignores and preserves `/data/twin_queue.db`; returning to 2.2.0 reopens it. Operators must not delete the database as a recovery shortcut.

## Sensor, status, and audit projection

Validated sensor frames flow through `FrameProcessor`, `SensorMapLoader`, and `MQTTClient`. Local setting confirmation uses the same merge path, but only after an exact `tbl_events` execution event. `SettingsAuditPublisher` proposes and accepts each committed transition against the SQLite ledger before projecting bounded telemetry.

## Trust boundaries

- BOX TCP and MQTT are untrusted inputs.
- Cloud TCP is forwarded byte-for-byte unless one local dialogue is durably owned.
- Device identity is learned only from CRC-valid BOX evidence and then bound persistently.
- MQTT retained input is rejected before decoding; there is no replay path.
- XML parsing uses `defusedxml`; serializer values are XML-1.0 checked and escaped.
- CI uses loopback endpoints and fake MQTT only; no live BOX, broker, or production cloud command is sent.
