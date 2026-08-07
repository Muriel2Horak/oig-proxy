# OIG protocol behavior specification

This document separates passive observations of the OIG protocol from the proxy's
2.2.0 local-control contract. Captured traffic informs parsing, but it is not treated as
a stronger command-correlation mechanism than the bytes actually contain.

## Poll and Setting dialogue

- BOX opens TCP exchanges and emits poll/data frames.
- `IsNewSet` is the only poll eligible for a Setting response.
- A cloud Setting is forwarded byte-for-byte and remains cloud-owned.
- ONLINE/HYBRID local substitution is cloud-first: the proxy waits for the correlated
  cloud terminal `END`, then may replace that terminal with one durably prepared local
  Setting.
- OFFLINE returns exactly one local Setting or one `END` for each valid `IsNewSet`.

A Setting carries fields including `Reason=Setting`, `TblName`, `TblItem`, `NewValue`,
`ID`, `ID_Set`, `ID_Server=9`, `DT`, `TSec`, `Confirm`, `Rdt`, `ver`, and CRC in the
serializer's canonical order.

## Delivery and execution evidence

The immediate `Result=ACK`, `Reason=Setting` response is delivery evidence. Captured
ACK/NACK frames do not carry command-side `ID` or `ID_Set`, so the proxy also requires
strict half-duplex phase, exact session ownership, fingerprints, bounded time, and Rdt
ordering. An ACK moves an owned local command from `awaiting_ack` to `awaiting_event`;
it does not prove execution.

Exact CRC-valid `tbl_events`, `Type=Setting` content is execution evidence when its
device, table, item, canonical new value, time, session, and ordering match one eligible
durable command. `Type=Change` events are ordinary parameter observations and do not
substitute for exact command evidence.

## Failure and retry behavior

The proxy persists attempt preparation before writing, records drained/failed/unknown
outcomes, and uses bounded deadlines plus at most `control_max_attempts` attempts. A
retry preserves command, `ID`, `ID_Set`, `DT`, table/item/value, and attempt lineage;
only `TSec`, `ver`, and CRC are refreshed. Restart recovery reads
`/data/twin_queue.db` before subscriptions or local writes.

Invalid CRC, malformed/oversized frames, unexpected direction/session, ambiguous ACK,
store/render/write failure, and expired evidence cannot confirm or publish local state.
When the dialogue is not proxy-owned, ONLINE cloud bytes remain transparent.

## Observed residual limitation

Because captured ACK/NACK frames lack command identity, a newly regenerated duplicate
can be indistinguishable from the next response in a multi-command same-session batch.
The implementation bounds this with sequential ordering and deduplication; eliminating
the residual ambiguity requires one local Setting per `IsNewSet` cycle.

Timing values in historical captures are observations, not protocol guarantees and not
release timeout defaults. See [`v2/twin.md`](v2/twin.md) and
[`v2/configuration.md`](v2/configuration.md) for the normative 2.2.0 contract.
