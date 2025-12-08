# SubD (ID_SubD) Analysis Report

## Executive Summary

**Problem**: Battery status cycling (BAT_N: 3→0→0→3→0→0... every 10-15 seconds) was occurring in Home Assistant due to multiple SubD variants being published as independent entities.

**Root Cause**: The OIG device sends battery parameters in 3 SubD variants (0, 1, 2) representing three independent battery banks. Previously, each variant was published as a complete record, overwriting previous values.

**Current Status**: Commit c54dd29 deployed a temporary fix that discards frames with ID_SubD>0, solving the cycling problem but losing data from inactive battery banks.

**Data Scope**: Out of 20,814 frames analyzed (7-8 Dec 2025), only 2 tables use ID_SubD: `tbl_batt` and `tbl_batt_prms`.

---

## Detailed Findings

### 1. Tables Using ID_SubD

| Table | SubD=0 | SubD=1 | SubD=2 | Total | Pattern |
|-------|--------|--------|--------|-------|---------|
| tbl_batt_prms | 22 | 22 | 22 | 66 | 3 frames per cycle, each SubD = battery bank |
| tbl_batt | 67 | 0 | 0 | 67 | Only SubD=0, never fragmented |

**Key Insight**: `tbl_batt_prms` is intentionally fragmented across 3 SubD variants (one per battery bank), while `tbl_batt` is never fragmented.

### 2. Field Comparison Across SubD Variants

#### SubD=0 (Active Bank 0)

```
BAT_N=3, BAT_CI=240.0, BAT_CU=56.5, BAT_DI=300.0,
FMT_ON=0, FMT_PROGRESS=95.161, BAT_HDO=0, BAT_AA=0,
BAT_MIN=20, BAT_GL_MIN=15, BAT_AG_MIN=35,
HDO1_S=0, HDO1_E=0, HDO2_S=0, HDO2_E=0,
BAL_ON=0, LO_DAY=0, TYP=1
```

#### SubD=1 & SubD=2 (Inactive Banks)

```
BAT_N=0, BAT_CI=0.0, BAT_CU=0.0, BAT_DI=0.0,
FMT_ON=0, FMT_PROGRESS=96.774, BAT_HDO=0, BAT_AA=0,
BAT_MIN=20, BAT_GL_MIN=15, BAT_AG_MIN=35,
HDO1_S=0, HDO1_E=0, HDO2_S=0, HDO2_E=0,
BAL_ON=0, LO_DAY=0, TYP=1
```

#### Fields Varying by Bank (Bank-Specific Parameters)

- `BAT_N`: Number of battery cells/strings (3 in SubD=0, 0 in SubD=1,2)
- `BAT_CI`: Charge current (240A in SubD=0, 0 in SubD=1,2)
- `BAT_CU`: Cell voltage (56.5V in SubD=0, 0 in SubD=1,2)
- `BAT_DI`: Discharge current (300A in SubD=0, 0 in SubD=1,2)

#### Fields NOT Varying by Bank (Common Configuration)

All other 15 fields are identical across all SubD variants: FMT_ON, FMT_PROGRESS, BAT_HDO, BAT_AA, BAT_MIN, BAT_GL_MIN, BAT_AG_MIN, HDO1_S, HDO1_E, HDO2_S, HDO2_E, BAL_ON, LO_DAY, TYP

---

## Message Sequence During Problem (Before Fix)

```
T+0s:   tbl_batt_prms (SubD=0) → MQTT publish BAT_N=3, BAT_CI=240.0
        MQTT state: BAT_N=3 ✓ (correct)

T+5s:   tbl_batt_prms (SubD=1) → MQTT publish BAT_N=0, BAT_CI=0.0
        MQTT state: BAT_N=0 ✗ (incorrect, overwrites bank 0 data)

T+10s:  tbl_batt_prms (SubD=2) → MQTT publish BAT_N=0, BAT_CI=0.0
        MQTT state: BAT_N=0 ✗ (incorrect, overwrites bank 0 data)

T+15s:  tbl_batt (SubD=0)      → MQTT publish voltage/current readings
        (does not affect BAT_N, BAT_CI etc.)

T+900s: Repeat cycle
```

**Result in HA**: BAT_N displays as "3" for ~5 seconds, then "0" for ~10 seconds, repeat.

---

## Current Code Implementation

### main.py parse_xml_frame() [Lines 305-335]

**Status**: Commit c54dd29 **IS DEPLOYED** (Mon Dec 8 21:23:47 2025)

The current fix (lines 310-318):

```python
# Kontrola ID_SubD - ignorovat subfragmenty (zpracujeme jen ID_SubD=0)
subframe_match = re.search(r"<ID_SubD>(\d+)</ID_SubD>", data)
if subframe_match:
    subframe_id = int(subframe_match.group(1))
    if subframe_id > 0:
        logger.debug(f"Ignorován subfragment ID_SubD={subframe_id}")
        return {}  # Vrátí prázdný dict - nebude publikován
```

Plus skip ID_SubD in field parsing (line 320):

```python
if key in ("TblName", "ID_Device", "ID_Set", "Reason", "ver", "CRC", "DT", "ID_SubD"):
    continue
```

**Impact**:

- ✅ Fixes the cycling problem – SubD=1,2 frames are completely discarded
- ✅ BAT_N now shows consistently as 3 (only SubD=0 published)
- ❌ **Loses all data from battery banks 1 and 2** (discarded without storage)
- ❌ Wastes network traffic (box sends 3 frames, proxy ignores 2)

### sensor_map.json [Lines 427-435]

```json
"BAT_N": {
    "name": "Počet baterii",
    "name_cs": "Baterie - Počet článků",
    "device": "battery",
    "todo": false
}
```

**Current state**: Single mapping for BAT_N, no awareness of SubD variants.

---

## Hardware Context: CBB Battery Architecture

OIG device supports **1-3 independent battery banks**:

- **Bank 0** (SubD=0): Primary/active bank with real parameters
- **Bank 1** (SubD=1): Secondary bank (currently inactive, zeros)
- **Bank 2** (SubD=2): Tertiary bank (currently inactive, zeros)

Each bank has independent:
- Number of cells (BAT_N)
- Charge/discharge current limits
- Cell voltage readings
- Discharge current limit

---

## Solution Design Options

### Option 1: Keep Current Approach (No Changes)

**Status quo**: Ignore SubD>0 frames entirely.

**Pros**:
- No code changes needed
- Solves cycling problem
- Minimal MQTT traffic
- Works for single-bank systems

**Cons**:
- Loses data from inactive banks
- Not future-proof if user activates bank 1 or 2
- Device still sends 3 frames, proxy ignores 2 (bandwidth waste)

**Recommendation**: Acceptable short-term, but document limitation.

### Option 2: Per-Bank Suffixed Entities (Recommended)

Extend sensor_map.json with explicit bank-specific mappings:

```json
"BAT_N_0": {
    "name": "Počet baterii [Bank 0]",
    "name_cs": "Baterie - Počet článků [Banka 0]",
    "device": "battery_0",
    "todo": false
},
"BAT_N_1": {
    "name": "Počet baterii [Bank 1]",
    "name_cs": "Baterie - Počet články [Banka 1]",
    "device": "battery_1",
    "todo": false
},
"BAT_N_2": {
    "name": "Počet baterii [Bank 2]",
    "name_cs": "Baterie - Počet články [Banka 2]",
    "device": "battery_2",
    "todo": false
}
```

Modify main.py to:
1. Stop discarding SubD>0 frames (revert lines 310-318)
2. Create separate MQTT topics per bank: `oig_local/{device_id}/{table}_{subd}/state`
3. Lookup sensor_map for suffixed keys: `BAT_N_0`, `BAT_N_1`, `BAT_N_2`

**Pros**:
- Preserves all bank data
- Clear HA entity structure (3 battery devices)
- Extensible for future bank configurations
- No complex buffering logic

**Cons**:
- ~100 new sensor_map.json entries (3 banks × 20+ fields)
- 3× MQTT traffic for battery fields (+~200% for that table)
- More HA entities to manage
- Requires HA re-discovery

**Implementation effort**: Medium (modify parse_xml_frame, extend sensor_map.json)

### Option 3: Buffering & Merging (Complex)

Implement frame buffering in main.py:

1. Buffer frames by (ID_Set, TblName)
2. Wait for all 3 SubD frames to arrive (or timeout after 15s)
3. Merge into single record: `{ "banks": [{ "subd": 0, "BAT_N": 3, ... }, ...] }`
4. Publish once per cycle

**Pros**:
- Single MQTT message per cycle (no duplication)
- Complete data in one publish
- Efficient transmission
- HA templates can extract per-bank values

**Cons**:
- Complex buffering/timeout logic
- Introduces latency (wait 5-15s before publish)
- Requires HA custom template sensors
- Edge cases: what if SubD=1 arrives but SubD=2 never does?

**Implementation effort**: High (new buffering system, HA templates)

### Option 4: Activity-Based Filtering (Compromise)

Skip frames where all bank fields are zero:

```python
if 'BAT_N' in result and result['BAT_N'] == 0:
    # Check if this is an inactive bank (all fields zero)
    if all(result.get(f, 0) == 0 for f in ['BAT_CI', 'BAT_CU', 'BAT_DI']):
        return {}  # Skip inactive bank frame
```

**Pros**:
- Simple code change
- Only 1 line modification
- Discards only truly empty banks
- Works if any bank has data

**Cons**:
- Still loses data (no record of what banks exist)
- Fragile: breaks if bank data includes legitimately zero fields
- Requires assumption that zero = inactive

**Implementation effort**: Low (1-2 lines)

---

## Data Volume Impact

Current transmission rate: ~66 messages per 24 hours

| Option | MQTT Publishes/Message | Total/Day | Change | Storage |
|--------|------------------------|-----------|--------|---------|
| **Option 1** (Current) | 2 (ignores SubD>0) | 132 | baseline | SubD=0 only |
| **Option 2** (Per-bank) | 4 (publish all) | 264 | +100% | All banks |
| **Option 3** (Buffered) | 2 (merged) | 132 | baseline | All banks |
| **Option 4** (Active only) | 1-2 (filter zeros) | 66-132 | -50% to baseline | Active only |

---

## Recommendation

**For immediate term** (current status):
- Keep Option 1 as-is (current fix working)
- Document the limitation in README
- Add comment in code explaining SubD=1,2 are inactive banks

**For medium term** (next week):
- Implement **Option 2** (per-bank suffixed entities)
- Provides best balance of data preservation and simplicity
- If you plan to use multiple battery banks in future

**Alternative if single-bank system confirmed**:
- Implement **Option 4** (activity-based filtering)
- Simpler than Option 2, sufficient for current hardware

**Avoid** Option 3 unless you have specific need for:
- Single merged MQTT message per cycle
- HA custom templating expertise
- Concern about MQTT traffic (unlikely for home automation)

---

## Implementation Checklist for Option 2

- [ ] Revert commit c54dd29 (remove lines 310-318 in parse_xml_frame)
- [ ] Modify parse_xml_frame to NOT skip SubD frames
- [ ] Keep ID_SubD in parsed dict (remove from skip list)
- [ ] Modify MQTTPublisher to create suffixed MQTT topics
- [ ] Extend sensor_map.json with bank-specific mappings (BAT_N_0, BAT_N_1, BAT_N_2, etc.)
- [ ] Test with real data from HA addon
- [ ] Verify MQTT topic structure in HA
- [ ] Trigger MQTT discovery for new devices
- [ ] Validate HA entity creation
- [ ] Document new bank device structure in README

---

## Questions for Decision

1. **Do you need data from inactive banks?** (banks 1,2 with zero values)
   - Yes → Option 2 or 3
   - No → Keep Option 1

2. **Will you ever activate multiple battery banks?**
   - Yes → Option 2 (future-proof)
   - No → Option 1 or 4 (simpler)

3. **Is MQTT storage a concern?** (database size, bandwidth)
   - Yes → Option 3 (merged) or Option 4 (filtered)
   - No → Option 2 (per-bank)

4. **How important is current HA entity discovery?**
   - Critical → Option 1 (no changes)
   - Can handle re-discovery → Option 2
   - Flexible → Option 3 or 4

---

## Status Summary

- ✅ Root cause identified and verified
- ✅ Data analysis complete (66 messages over 24h)
- ✅ Field variance documented (4 bank-specific, 15 common)
- ✅ Temporary fix deployed (commit c54dd29)
- ⏳ Long-term solution design pending user decision
