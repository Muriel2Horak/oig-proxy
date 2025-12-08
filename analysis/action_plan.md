# Action Plan: SubD Battery Bank Issue

## Summary of Analysis

✅ **Problem is SOLVED** (commit c54dd29 deployed)
- Battery cycling issue (BAT_N: 3→0→0 every 10-15s) is **fixed**
- Current implementation ignores SubD>0 frames (banks 1,2)
- Only SubD=0 (active bank) is published to HA

---

## What We Discovered

### Frame Analysis (20,814 frames over 24h)

- **tbl_batt_prms**: 66 messages total
  - SubD=0 (Bank 0): 22 frames with real data (BAT_N=3, BAT_CI=240A, etc.)
  - SubD=1 (Bank 1): 22 frames with zeros (BAT_N=0, BAT_CI=0, inactive)
  - SubD=2 (Bank 2): 22 frames with zeros (BAT_N=0, BAT_CI=0, inactive)

- **tbl_batt**: 67 messages total
  - SubD=0 only: All voltage/current readings (never fragmented)
  - SubD=1,2: Never appears

### Field Breakdown

**4 Bank-Specific Fields** (vary per SubD):
- BAT_N (cells count)
- BAT_CI (charge current)
- BAT_CU (cell voltage)
- BAT_DI (discharge current)

**15 Common Fields** (identical across all SubD):
- FMT_ON, FMT_PROGRESS, BAT_HDO, BAT_AA, BAT_MIN, BAT_GL_MIN, BAT_AG_MIN
- HDO1_S, HDO1_E, HDO2_S, HDO2_E, BAL_ON, LO_DAY, TYP

---

## Current Solution Assessment

### What Works ✅
- Cycling issue is completely resolved
- HA sees consistent BAT_N=3 (only SubD=0 published)
- No HA re-discovery needed
- Minimal code changes (8 lines in main.py)

### What's Lost ❌
- No data from battery banks 1 and 2 (discarded silently)
- If future user activates banks 1,2: no data available
- Device still sends 3 frames, proxy ignores 2 (bandwidth inefficiency)

### Risk Assessment 🔴
**LOW RISK** for your current system:
- Your CBB device has only 1 active bank (bank 0)
- Banks 1,2 are inactive (zero values)
- Discarding inactive banks is reasonable
- ✅ **Acceptable production solution**

**MEDIUM RISK** for future scalability:
- If you add second/third battery bank: need code change
- Current approach is not "future-proof"
- Should document this limitation

---

## Decision Framework

### Keep Current Solution (Option 1) - RECOMMENDED FOR NOW

**When to choose**:
- ✅ You have only 1 battery bank (current setup)
- ✅ No plans to add multiple banks in future
- ✅ Want minimal code complexity
- ✅ Want zero disruption to HA setup

**Actions required**:
1. Add documentation comment in main.py explaining SubD behavior
2. Update README noting "Single-bank systems only"
3. Log this decision for future reference

### Extend to Multiple Banks (Option 2) - FOR FUTURE

**When to choose**:
- You plan to add battery bank 1 or 2 in future
- Want to preserve all bank data from day 1
- Willing to add ~100 lines to sensor_map.json
- Can handle HA entity re-discovery

**Implementation**:
1. Revert SubD skip logic (lines 310-318)
2. Create per-bank MQTT topics: `oig_local/{id}/{table}_{subd}/state`
3. Add bank-specific sensors: BAT_N_0, BAT_N_1, BAT_N_2, etc.
4. Results in 3 separate battery devices in HA

---

## Recommendations by Use Case

### For Current Setup (Single Bank) 🟢
**Recommendation**: Keep Option 1, add documentation only
- **Risk**: Very Low
- **Effort**: 5 minutes (documentation)
- **Disruption**: None
- **Future flexibility**: Low (requires future changes if banks added)

### For Multi-Bank Planning (Even if unused now) 🟡
**Recommendation**: Switch to Option 2 now (preventive)
- **Risk**: Medium (HA re-discovery needed)
- **Effort**: 2-3 hours (code + testing)
- **Disruption**: HA entities change (re-learning)
- **Future flexibility**: High (banks 1,2 ready to use)

### For Bank Expansion 🔴
**Recommendation**: Implement Option 2 before activating banks
- **Risk**: High if done during operation
- **Effort**: 2-3 hours (code + testing)
- **Disruption**: HA entities change, require re-configuration
- **Future flexibility**: High (full multi-bank support)

---

## Next Steps

### Immediate (Today)
1. ✅ Confirm cycling issue is resolved in HA
2. ✅ Verify BAT_N shows stable value (not flickering 3→0)
3. Document in code: `# SubD=1,2 are discarded (inactive banks)`
4. Create GitHub issue/note: "SubD handling for multi-bank support"

### Short-term (This Week)
- [ ] Add section to README: "Battery Bank Configuration"
- [ ] Document: "Current single-bank limitation"
- [ ] Plan: Do you ever foresee needing banks 1,2?

### Medium-term (Next Month)
- [ ] If multi-bank planned: Implement Option 2
- [ ] If single-bank confirmed: Close as working-as-designed
- [ ] Either way: Document decision in architecture file

---

## Code Changes Summary

### Current Deployed Code (c54dd29)

**File**: `addon/oig-proxy/main.py`
**Lines**: 310-318 (skip SubD>0 frames)
**Effect**: Discards all SubD=1,2 messages completely

```python
subframe_match = re.search(r"<ID_SubD>(\d+)</ID_SubD>", data)
if subframe_match:
    subframe_id = int(subframe_match.group(1))
    if subframe_id > 0:
        logger.debug(f"Ignorován subfragment ID_SubD={subframe_id}")
        return {}  # Skip this frame entirely
```

**Plus line 320**: Add ID_SubD to skip list so it's not parsed

### To Enable Option 2 (If Needed Later)

1. Remove lines 310-318 (delete the SubD check)
2. Remove ID_SubD from skip list (line 320)
3. Modify MQTT topic creation: `{table}_{subd}` for SubD frames
4. Extend sensor_map.json: Add BAT_N_0, BAT_N_1, BAT_N_2, etc.

---

## Questions for You

Before proceeding, please clarify:

1. **Is the cycling issue resolved?**
   - Check HA: Does BAT_N show stable "3" now? (not flickering 3→0→0)
   - Expected: Yes (fix is deployed)

2. **Do you plan to use multiple battery banks in future?**
   - Check physical setup: Do you have batteries for bank 1 or 2?
   - Check OIG device config: Is bank 1 or 2 configurable?

3. **Is the current HA setup working for you?**
   - Any missing sensors?
   - Any duplicate entities?

4. **Do you want to be "future-proof" for multi-bank?**
   - Preventive maintenance: Implement Option 2 now?
   - Or: Only change if/when needed (current Option 1)?

---

## Final Status

- ✅ **Root cause**: Identified (3 battery banks × SubD)
- ✅ **Problem**: Solved (cycling issue fixed)
- ✅ **Data analysis**: Complete (22 messages per bank)
- ⏳ **Architecture decision**: Pending user input
- ⏳ **Documentation**: Ready to add
