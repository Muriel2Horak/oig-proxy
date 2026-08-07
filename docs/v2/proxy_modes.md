# OIG Proxy v2.2 Modes

The configured mode is sampled at startup. `ModeManager` tracks only ONLINE/OFFLINE runtime connectivity; local-setting ownership remains in `SettingDialog` and `TwinCoordinator`.

## ONLINE

ONLINE is cloud-first. Each BOX frame is forwarded to cloud. For `IsNewSet`, the proxy collects the complete correlated cloud dialogue:

1. forward the poll unchanged;
2. forward every cloud Setting unchanged;
3. at the correlated cloud `END`, claim one eligible local command;
4. substitute one local Setting only after durable claim/render evidence;
5. after its ACK/NACK, forward the retained cloud `END` exactly once.

When no command is eligible or local ownership fails, the exact cloud response is forwarded. Unrelated cloud bytes are never swallowed or reordered.

## HYBRID

HYBRID starts with identical cloud-first behavior. After `hybrid_fail_threshold` connection failures it enters the OFFLINE response contract. After `hybrid_retry_interval`, the next session probes cloud. Success returns to ONLINE; failure restarts the retry window.

## OFFLINE

OFFLINE opens no cloud socket. Every complete valid BOX request receives exactly one local protocol response.

| Request | Response |
|---|---|
| `IsNewSet` with one eligible durable command | one local Setting |
| `IsNewSet` without eligible work | one `END` |
| ordinary sensor/event frame | one ACK/END selected by `local_ack.py` |
| invalid/incomplete frame | no fabricated transaction response; close/fail closed |

Local control never uses weather or firmware polls as Setting triggers.

## Control disabled

With `control_mqtt_enabled=false`, a disabled restart recovers and preserves `/data/twin_queue.db` but creates no ingress subscription and no local write. ONLINE/HYBRID remains transparent; OFFLINE always selects its non-Setting response.

## Deadlines and failure precedence

Cloud dialogue, local ACK, execution event, command TTL, and connection cleanup are separate deadlines. Expiry/cancellation first settles every owned worker and lock, then reports complete error provenance. Local failures cannot convert an unowned cloud dialogue into a local response.

## Rollback

A 2.1.1 rollback ignores and preserves `/data/twin_queue.db`. Version 2.2.0 never downgrades or recreates a future/corrupt store. Preserve the file and restore 2.2.0 to resume recovery.
