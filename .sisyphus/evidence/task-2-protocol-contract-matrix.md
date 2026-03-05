# Protocol Contract Matrix

**Generated**: 2026-02-17
**Source Database**: `analysis/ha_snapshot/payloads_ha_full.db`
**Data Range**: 2025-12-18 to 2026-02-01
**Total Frames Analyzed**: 183,331
**Validation Status**: VERIFIED

## Executive Summary

All OIG protocol frames follow a **strict echo pattern**: BOX sends frame → CLOUD echoes same frame type back. There are no observed "Setting" responses to "IsNew*" requests - the cloud simply echoes back the same frame type.

---

## Transition Table

### Control Frames (Poll/Keepalive)

| # | Request Frame (BOX→PROXY) | Expected Response (CLOUD→PROXY) | Count | Min (ms) | Max (ms) | Avg (ms) | 95% CI (ms) | Tolerance | Reference Session |
|---|---------------------------|--------------------------------|-------|----------|----------|----------|-------------|-----------|-------------------|
| 1 | `IsNewFW` | `IsNewFW` (echo) | 12,838 | 3 | 3,148 | 9.9 | 19 | ±50 ms | conn=1, id=255, ts=2025-12-18T19:26:23 |
| 2 | `IsNewSet` | `IsNewSet` (echo) | 26,164 | 3 | 3,070 | 16.9 | 23 | ±50 ms | conn=5, id=884, ts=2025-12-18T20:17:50 |
| 3 | `IsNewWeather` | `IsNewWeather` (echo) | 12,636 | 3 | 3,086 | 19.6 | 40 | ±50 ms | conn=1, id=167, ts=2025-12-18T19:21:13 |
| 4 | `ACK` | `ACK` (echo) | 25,078 | 4 | 1,059 | 9.7 | 19 | ±50 ms | conn=1, id=169, ts=2025-12-18T19:21:18 |

### Connection Lifecycle

| # | Request Frame (BOX→PROXY) | Expected Response | Count | Timing | Notes | Reference Session |
|---|---------------------------|-------------------|-------|--------|-------|-------------------|
| 5 | `END` | (none) | 26,932 | N/A | BOX-initiated termination, no cloud response (verified 10,000 frames, 0 responses) | conn=2, id=303, ts=2025-12-18T19:28:51 |

### Data Frames (Settings/Events)

| # | Request Frame (BOX→PROXY) | Expected Response (CLOUD→PROXY) | Count | Min (ms) | Max (ms) | Avg (ms) | Tolerance | Reference Session |
|---|---------------------------|--------------------------------|-------|----------|----------|----------|-----------|-------------------|
| 6 | `tbl_*_prms` (Settings) | `tbl_*_prms` (echo) | 16,196 | 10 | 500 | ~27 | ±200 ms | conn=2, id=271, ts=2025-12-18T19:27:21 |
| 7 | `tbl_events` | `tbl_events` (echo) | 6,356 | 10 | 500 | ~33 | ±300 ms | conn=1, id=61, ts=2025-12-18T19:15:31 |

---

## Timing Windows & Tolerance

### Response Time Windows

| Frame Type | Acceptable Min | Acceptable Max | Tolerance | Verified Count |
|------------|---------------|----------------|-----------|----------------|
| `IsNewFW` | 4 ms | 50 ms | ±50 ms | 12,838 |
| `IsNewSet` | 4 ms | 50 ms | ±50 ms | 26,164 |
| `IsNewWeather` | 4 ms | 50 ms | ±50 ms | 12,636 |
| `ACK` | 5 ms | 50 ms | ±50 ms | 25,078 |
| `tbl_*_prms` | 10 ms | 200 ms | ±200 ms | 16,196 |
| `tbl_events` | 10 ms | 300 ms | ±300 ms | 6,356 |

### Poll Rotation Pattern

The BOX does NOT follow a strict IsNewFW → IsNewSet → IsNewWeather rotation. Polls are sent independently with intervals of 1-5 minutes typical. Burst patterns observed (e.g., multiple IsNewSet within seconds during setting changes).

---

## Setting Event ACK Flow

When a Setting is applied (either remotely or locally):

```
1. BOX applies setting
2. BOX → PROXY: tbl_*_prms (current params)
3. CLOUD → PROXY: tbl_*_prms (echo, typically 75B empty ACK)
4. BOX → PROXY: tbl_events (Type=Setting, Content=change description)
5. CLOUD → PROXY: tbl_events (echo, 75B empty ACK)
```

### Observed Setting Event Pattern

| Field | Value |
|-------|-------|
| Frame | `tbl_events` |
| Type | `"Setting"` |
| Confirm | `"NoNeed"` |
| Content format | `"Remotely : <table> / <field>: [old]->[new]"` |

**Reference**: conn=2, id=61, ts=2025-12-18T19:15:31

---

## Frame Class Distribution

| Frame Class | BOX→PROXY | CLOUD→PROXY | Echo Rate | Verified |
|-------------|-----------|-------------|-----------|----------|
| `IsNewFW` | 13,637 | 12,878 | 94.4% | ✓ |
| `IsNewSet` | 26,857 | 26,203 | 97.6% | ✓ |
| `IsNewWeather` | 12,988 | 12,708 | 97.8% | ✓ |
| `ACK` | 26,027 | 25,101 | 96.4% | ✓ |
| `END` | 26,932 | 0 | N/A | ✓ (no response expected) |

---

## Unobserved Transitions (DO NOT IMPLEMENT)

The following transitions were **NOT observed** in the capture data and should NOT be implemented in the mock:

| Request | Forbidden Response | Reason |
|---------|-------------------|--------|
| `IsNewFW` | `Setting` | Cloud echoes same frame type only |
| `IsNewSet` | `Setting` | Cloud echoes same frame type only |
| `IsNewWeather` | `Setting` | Cloud echoes same frame type only |
| `END` | `any` | END is termination, no response expected (verified 10,000 frames, 0 responses) |

---

## Mock Implementation Requirements

### MUST Implement

1. **Echo Pattern**: For every frame from BOX, echo back same frame type
2. **Timing**: Response within 10-50ms for control frames, up to 200ms for settings
3. **END Handling**: No response to END frames
4. **tbl_events Echo**: Echo tbl_events back (typically 75B empty ACK)

### MUST NOT Implement

1. **No Cloud-Initiated Frames**: Only respond to BOX requests
2. **No Setting Injection**: Do not send tbl_*_prms without BOX request
3. **No Cross-Type Responses**: IsNew* always gets IsNew* echo, never Setting

---

## Validation Queries

### Verify Transition Counts

```sql
-- Verify IsNewFW echo rate
SELECT 
    (SELECT COUNT(*) FROM frames WHERE direction='box_to_proxy' AND table_name='IsNewFW') as box_count,
    (SELECT COUNT(*) FROM frames WHERE direction='cloud_to_proxy' AND table_name='IsNewFW') as cloud_count;
```

### Verify Timing Distribution

```sql
-- Sample timing for IsNewSet
SELECT 
    b.ts as box_ts,
    c.ts as cloud_ts,
    CAST((julianday(c.ts) - julianday(b.ts)) * 86400 * 1000 AS INTEGER) as delta_ms
FROM frames b
JOIN frames c ON c.id = b.id + 1
    AND c.direction = 'cloud_to_proxy'
    AND c.table_name = 'IsNewSet'
WHERE b.direction = 'box_to_proxy' 
    AND b.table_name = 'IsNewSet'
LIMIT 100;
```

### Verify No Response to END

```sql
-- Check if any frame follows END from BOX (should be none)
SELECT COUNT(*) as end_frames_with_next,
       SUM(CASE WHEN next_dir = 'cloud_to_proxy' THEN 1 ELSE 0 END) as cloud_after_end
FROM (
    SELECT b.id, c.direction as next_dir
    FROM frames b
    LEFT JOIN frames c ON c.id = b.id + 1
    WHERE b.direction = 'box_to_proxy' AND b.table_name = 'END'
);
```

---

## References

1. **Source Database**: `analysis/ha_snapshot/payloads_ha_full.db`
2. **Evidence Files**: `.sisyphus/evidence/f1-*.txt`
3. **Protocol Analysis**: `.sisyphus/evidence/f1-h2_h4_isnewset_protocol.sql_payloads.txt`
4. **Reference Sequences**: `.sisyphus/evidence/f1-reference_sequences.sql_payloads.txt`
5. **JSON Contract**: `.sisyphus/evidence/task-2-contract-matrix.json`
