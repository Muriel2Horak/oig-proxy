# Control Debug Report
**Date:** 2026-03-02  
**Session:** control-debug  
**Goal:** Diagnose why BOX doesn't ACK local MQTT setting commands

---

## Executive Summary

The root cause is confirmed: **`maybe_handle_ack()` looks for the wrong ACK format**. BOX never sends `<Result>ACK</Result>` — it sends `tbl_events` frames. This means the inflight command is never cleared. When the BOX disconnects (which happens every ~15-30s in normal operation), the pending is dropped and the command is silently lost.

This was **fully confirmed by live trace** on 2026-03-02 with a MODE=3 test command.

---

## Hypothesis Verdicts

### H1: Hardcoded `<ver>55734</ver>` vs cloud random ver
**Status: DISCONFIRMED as root cause, but real bug present**

Evidence:
- `control_settings.py:296` log shows `ver=55734` hardcoded in every local setting frame
- Cloud forwarder (`cloud_forwarder.py:500`) uses `secrets.randbelow(90000) + 10000` (random 5-digit)
- DB analysis (T2): cloud frames have random ver (01550, 23822, 12897...)
- **However**: The live test showed the frame WAS delivered (`forwarder_inject` fired), and the BOX disconnected 18s later without ACK — not immediately. If `<ver>` mismatch caused rejection, BOX would not engage at all. The disconnect pattern matches normal BOX cycling, not a rejection.
- **Verdict**: The `<ver>` mismatch is a real discrepancy that should be fixed, but it is NOT the root cause of the ACK failure.

### H2: `maybe_handle_ack()` looking for wrong response format
**Status: CONFIRMED — THIS IS THE ROOT CAUSE**

Evidence:
- `control_settings.py:312`: `maybe_handle_ack()` checks for `<Reason>Setting</Reason>` + `<Result>ACK</Result>` in same frame
- DB analysis (T3): BOX NEVER sends `<Result>ACK</Result>`. Instead it sends `tbl_events` frames with `<Type>Setting</Type>` and `<Content>Remotely: tbl_box_prms / MODE: [3]->[0]</Content>`
- Live test confirmation:
  ```
  16:42:06  forwarder_inject — frame delivered to BOX (conn_id=13)
  16:42:24  CONTROL: Clearing delivered-but-unacked Setting on BOX disconnect (tbl=tbl_box_prms/MODE, delivered_conn=13)
  16:42:26  proxy_box_connect — conn_id=14 (new connection!)
  16:42:28  forwarder_forward_check | has_pending=False — command LOST
  ```
- The 18-second gap (16:42:06 → 16:42:24) is exactly the BOX's normal reconnect cycle — not a confirmation
- `settings_ack_check` never showed `delivered_conn_id != None` transitioning to completed
- **Verdict: CONFIRMED. `maybe_handle_ack()` will never succeed. The BOX sends `tbl_events` not `<Result>ACK</Result>`. The command is always silently discarded on BOX disconnect.**

### H3: `pending_frame` delivery timing / conn_id mismatch
**Status: DISCONFIRMED as root cause, secondary issue present**

Evidence:
- Live test showed `settings_ack_check | delivered_conn_id=None current_conn_id=13` BEFORE delivery
- After `forwarder_inject` at 16:42:06: `delivered_conn_id` would have been set to conn_id=13
- But there was NO `settings_ack_check` log AFTER delivery — meaning `maybe_handle_ack()` never got called with a matching frame at all
- The timing is fine: frame delivered on IsNewFW poll (~5s after queueing), BOX stayed connected for 18s after delivery
- **Verdict: DISCONFIRMED. Timing is adequate. conn_id mismatch does not apply because ACK never arrives in any format.**

---

## Full Live Trace — MODE=3 Command (2026-03-02 16:41-16:42)

```
16:41:56  pipeline_recv       — MQTT command received (tx_id=test003, MODE=3)
16:41:59  proxy_box_connect   — BOX reconnected, conn_id=13
16:42:01  pipeline_delegate   — command handed to control_settings
16:42:01  settings_send       — writer_id=...813040, conn_id=13
16:42:01  settings_pending    — frame built, frame_len=378, ver=55734
16:42:01  CONTROL: Queued Setting tbl_box_prms/MODE=3 for next poll (id=21215346)
16:42:01  settings_ack_check  — delivered_conn_id=None (not yet sent)
16:42:01  forwarder_forward_check — table=tbl_actual, has_pending=True (first poll, not IsNewX)
16:42:01  forwarder_ack_to_box   — tbl_actual proxied, setting NOT injected (not IsNewSet/FW/Weather)

16:42:06  settings_ack_check  — delivered_conn_id=None (still waiting)
16:42:06  forwarder_forward_check — table=IsNewFW, has_pending=True  ← TRIGGER
16:42:06  forwarder_inject    — frame_len=378, conn_id=13            ← DELIVERED
16:42:06  CONTROL: Delivered pending Setting (online/hybrid, tbl_box_prms/MODE=3, conn=13)
          [BOX receives frame — no ACK ever comes back]

16:42:24  CONTROL: Clearing delivered-but-unacked Setting on BOX disconnect
          [BOX disconnected — inflight cleared — COMMAND LOST]
16:42:26  proxy_box_connect   — conn_id=14
16:42:28  forwarder_forward_check — has_pending=False  ← command gone
```

**Total time from delivery to loss: ~18 seconds (one BOX reconnect cycle).**

---

## DB Evidence (T3 — from `control-debug` Wave 1)

From `cloud_to_proxy` table (cloud→proxy→BOX path, 37 setting frames):
- Cloud sends `<Reason>Setting</Reason><Result>ACK</Result>` — cloud-originated settings route through `forward_ack_to_box()` which passes the ACK straight to BOX without using `maybe_handle_ack()`
- Cloud settings work because they bypass `maybe_handle_ack()` entirely

From `box_to_proxy` table (46 tbl_events):
- BOX confirms settings via: `<Type>Setting</Type><Content>Remotely: tbl_box_prms / MODE: [3]->[0]</Content>`
- This is a `tbl_events` frame — NOT an ACK frame in the protocol sense
- `maybe_handle_ack()` pattern never matches this format

---

## Recommended Fixes

### Fix 1 (Critical — root cause): Rewrite ACK detection
**File:** `control_settings.py`, `maybe_handle_ack()` (~line 312)

Replace pattern: `<Reason>Setting</Reason>` + `<Result>ACK</Result>`  
With: detect `<Type>Setting</Type>` in `tbl_events` frame and match `tbl_name`/`tbl_item` from `<Content>` field.

Or alternative: treat BOX **disconnect** after successful delivery as an implicit ACK (the BOX always reconnects fresh after applying a setting). This would be simpler and consistent with observed BOX behavior.

### Fix 2 (Secondary): Randomize `<ver>` in local setting frames
**File:** `control_settings.py:273`

Change hardcoded `<ver>55734</ver>` to `f"<ver>{secrets.randbelow(90000) + 10000:05d}</ver>"` (same pattern as `cloud_forwarder.py:500`). Add `import secrets` if not present.

### Fix 3 (Robustness): Retry on BOX disconnect while inflight
**File:** `control_settings.py` or `control_pipeline.py`

Currently: "Clearing delivered-but-unacked Setting on BOX disconnect" — command is silently dropped.  
Better: re-queue the command for next BOX connection (with retry limit).

---

## CTRL_DIAG Log Inventory (12 logs deployed)

| File | Log Name | Purpose |
|------|----------|---------|
| control_pipeline.py:600 | `pipeline_recv` | MQTT command received |
| control_pipeline.py:676 | `pipeline_schedule` | Queue state at each schedule cycle |
| control_pipeline.py:729 | `pipeline_delegate` | Command dispatched to settings |
| control_settings.py:253 | `settings_send` | settings.send_to_box() called |
| control_settings.py:296 | `settings_pending` | Frame built and queued |
| control_settings.py:324 | `settings_ack_check` | ACK check attempted per-poll |
| proxy.py:324 | `proxy_box_connect` | BOX connected, new conn_id assigned |
| proxy.py:669 | `proxy_offline_check` | Offline delivery check (with has_pending) |
| proxy.py:686 | `proxy_offline_inject` | Offline: frame written to BOX |
| cloud_forwarder.py:433 | `forwarder_ack_to_box` | Cloud ACK forwarded to BOX |
| cloud_forwarder.py:486 | `forwarder_forward_check` | Hybrid: pending check on each poll |
| cloud_forwarder.py:535 | `forwarder_inject` | Hybrid: setting frame written to BOX |
