# OIG Proxy v2.2 Local-Setting Transactions

## Scope and disabled default

Local setting control is an opt-in transaction engine. `control_mqtt_enabled` defaults to `false`. A disabled restart still opens, validates, and recovers `/data/twin_queue.db`, but starts no control subscription and performs no local write. This preserves evidence and permits a later safe re-enable.

There is no replay path and no operator file-injection path. In particular, `/data/replay_setting_frame.xml` is not read or written.

## Exact-device MQTT ingress

The runtime subscribes only after learning and persisting one CRC-valid BOX device identity. The primary topic is `oig/{device_id}/control/set`; the compatibility topic is `oig_local/{device_id}/set/{table}/{key}`. No `+` device wildcard is used. A topic for another, missing, or `unknown` device is rejected and audited without creating a command.

Primary JSON payload:

```json
{"table":"tbl_box_prms","key":"MODE","value":"2"}
```

MQTT `retained` input is rejected before UTF-8 or JSON decoding, preventing broker replay after restart. Payload size is bounded to 1 MiB. Targets are deny-by-default in `CONTROL_WRITE_WHITELIST`; values use exact bounded `Decimal` constraints and canonical text. Dynamic XML text must satisfy XML 1.0 and is escaped exactly once. INFO logs contain classifications and identifiers, never raw control payloads.

## Durable schema

SQLite schema v1 records:

- `devices`: exact identity and latest observed wire counters;
- `commands`: immutable audit ID, target, canonical value, state, TTL, sequence, and retry count;
- `control_ingress_audit`: accepted and rejected ingress disposition before command creation;
- `command_attempts`: exact wire bytes, CRC, stable IDs, refreshed fields, session owner, and write outcome;
- `command_transitions`: append-only state transitions;
- `event_receipts`: exact event fingerprint and deduplication outcome;
- `settings_audit_deliveries`: durable proposal/acceptance accounting for telemetry projection.

Schema, pragma, integrity, accounting, and recovery checks run before subscriptions. A future version, corruption, lock error, failed migration, or accounting drift disables local control. Version 2.2.0 never downgrades or recreates the database.

## State machine

Nonterminal states are `pending`, `retry_pending`, `awaiting_ack`, and `awaiting_event`. Terminal states are `confirmed`, `incomplete`, `failed`, `expired`, and `superseded`.

```text
pending/retry_pending
  -> awaiting_ack       exact bytes prepared, started, and drained
  -> awaiting_event     correlated ACK/Setting delivery evidence
  -> confirmed          exact tbl_events execution evidence

any nonterminal
  -> retry_pending      bounded recoverable delivery failure
  -> incomplete         ambiguous write or exhausted evidence deadline
  -> failed             terminal NACK or fail-closed integrity failure
  -> expired            command TTL elapsed
  -> superseded         newer same-target command became eligible first
```

Same-target commands are ordered durably. A newer value never overwrites or deletes sent work. At most one command for the exact `(device, table, key)` target owns delivery; other eligible targets retain deterministic sequence order.

## Wire contract

Every local Setting is serialized by the shared builder in this order:

| Field | Contract |
|---|---|
| `ID` | stable attempt message ID |
| `ID_Device` | exact learned device |
| `ID_Set` | stable attempt setting ID |
| `ID_SubD` | `0` |
| `DT` | stable Czech civil timestamp derived from `ID_Set` |
| `NewValue` | canonical escaped value |
| `Confirm` | `New` |
| `TblName` / `TblItem` | allowlisted escaped target |
| `ID_Server` | `9` (`ID_Server=9`) |
| `mytimediff` | `0` |
| `Reason` | `Setting` |
| `TSec` | refreshed retry time |
| `ver` | refreshed zero-padded uint16 |
| `CRC` | recomputed over the exact inner bytes |

Retries preserve command/audit identity, `ID`, `ID_Set`, `DT`, target, and canonical value. Only `TSec`, `ver`, CRC, wire length, owner, and write milestones are refreshed. `control_max_attempts` is shipped and hard-clamped to 1–8.

## ONLINE and HYBRID cloud-first sequence

ONLINE and online-state HYBRID are cloud-first:

1. Forward the BOX `IsNewSet` poll to cloud unchanged.
2. Collect the complete correlated cloud dialogue under one owner and deadline.
3. Forward every cloud Setting unchanged.
4. If eligible local work exists, replace only the correlated terminal cloud `END` with the local Setting and retain that `END` for completion after ACK/NACK.
5. If no eligible command exists or any local claim/render/store condition fails, forward the exact cloud terminal response.

The proxy never injects before cloud completion. Unrelated cloud traffic, wrong connection ownership, a stale session, a direction mismatch, or timeout leaves the dialogue cloud-owned.

## OFFLINE exactly-one response

OFFLINE never connects to cloud. Every complete BOX request receives exactly one response.

| BOX request | Local response |
|---|---|
| eligible `IsNewSet` | one Setting |
| ineligible/empty `IsNewSet` | one `END` |
| sensor or event frame | protocol ACK/END selected by the offline response contract |
| malformed/incomplete frame | no fabricated transaction response; connection fails closed |

No firmware or weather Setting trigger participates in local control.

## ACK/NACK correlation

`ACK/Setting is delivery evidence`, not execution confirmation. Only the current session owner, strict direction, expected dialogue phase, exact response shape, and bounded ordering may move `awaiting_ack` to `awaiting_event`. A NACK records its stable reason and either schedules a bounded retry or terminates the command. Unknown/ambiguous write outcome is never treated as success.

The serialized BOX writer owns all proxy-to-BOX writes and records `prepared`, `started`, `drained`, `unknown`, or `failed` before lifecycle mutation. Cancellation and cleanup retain complete error provenance while releasing locks and owners.

## Exact event confirmation and deduplication

Execution is confirmed only by a CRC-valid BOX-to-proxy `tbl_events` frame with direct `Type=Setting`, exact learned device, exact table/key/value content, correct session/direction, and an event time not older than the attempt. The event fingerprint is inserted durably before state mutation; duplicate frames are idempotent.

Only this transition publishes the confirmed HA/MQTT setting state. ACK, cloud Setting, timeout, retry, or mere transport success never publishes confirmed state.

## Deadlines, restart, and recovery

- `control_ack_timeout_s`: time from drained write to delivery evidence;
- `control_event_timeout_s`: time from ACK/Setting to exact execution event;
- `control_command_ttl_s`: lifetime before pending work expires;
- `control_max_attempts`: total prepared attempts.

Restart recovery reconciles incomplete attempt milestones and leases before any subscription. A known drained write can continue waiting for evidence; ambiguous ownership/write states become retryable or incomplete according to durable evidence. Expired work is terminalized. Deadlines use committed state, so cancellation cannot silently erase a transition.

## Audit, capture, and state publication

Every committed transition produces an immutable `SettingsAuditRecord` with stable audit/command/attempt identity. Proposal and acceptance are accounted in the same SQLite authority before asynchronous publication. Optional frame capture stores command ID, audit ID, attempt number, direction, owner, and exact bytes. Telemetry payloads are bounded and raw payload logging is avoided.

## Database failure and rollback

No local Setting is generated when the store is absent, unhealthy, locked, future-versioned, corrupt, or cannot durably record required evidence. The proxy retains transparent cloud bytes whenever it has not committed local ownership. It does not delete, reset, or auto-recreate evidence to recover availability.

A 2.1.1 rollback ignores and preserves `/data/twin_queue.db`. After returning to 2.2.0, recovery resumes from schema v1. Operators must preserve the DB during rollback.

## Security boundaries

- exact learned device and exact topics;
- retained-before-decode rejection and bounded inputs;
- parameterized/static SQLite statements;
- `defusedxml`, CRC validation, direct-field parsing, and XML escaping;
- UUID connection ownership, session epochs, half-duplex dialogue ownership, and serialized writes;
- fail-closed deadlines, durable deduplication, and no replay path;
- loopback-only automated E2E; no active production command is part of release validation.

## Residual sequential ACK/NACK protocol limitation

The captured ACKs lack command-side `ID` and `ID_Set`; multi-command batches therefore depend on strict half-duplex ordering, exact fingerprint deduplication, session ownership, and `Rdt` ordering. A novel regenerated duplicate remains indistinguishable from the expected next ACK/NACK.

This residual risk cannot be eliminated inside the observed protocol. Eliminating it requires one local Setting per `IsNewSet` cycle. The current implementation documents and bounds the sequential behavior but does not claim cryptographic command correlation.
