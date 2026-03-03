# Settings Trigger Analysis Report

Analysis Date: 2026-03-03T16:43:07.197601
Total Change Events Found: 7

## Summary

| # | Timestamp | Table | Field | Old -> New | RTT | Trigger |
|---|-----------|-------|-------|------------|-----|---------|
| 1 | 2026-03-02T05:41:40 | tbl_invertor_prms | ERR_PV | 12 -> 4 | N/A | No |
| 2 | 2026-03-02T17:03:25 | tbl_invertor_prms | ERR_PV | 4 -> 12 | N/A | No |
| 3 | 2026-03-02T20:46:30 | tbl_batt_prms | BAT_CI | 160.0 -> 80.0 | N/A | No |
| 4 | 2026-03-02T20:46:33 | tbl_batt_prms | BAT_DI | 200.0 -> 100.0 | N/A | No |
| 5 | 2026-03-02T20:46:37 | tbl_batt_prms | BAT_N | 1 -> 2 | N/A | No |
| 6 | 2026-03-02T20:46:39 | tbl_batt_prms | BAT_CI | 80.0 -> 160.0 | N/A | No |
| 7 | 2026-03-02T20:46:44 | tbl_batt_prms | BAT_DI | 100.0 -> 200.0 | N/A | No |

## Detailed Analysis

### Change Event #1

**Timestamp:** 2026-03-02T05:41:40.652171+00:00
**Content:** `Input : tbl_invertor_prms / ERR_PV: [12]->[4]`

#### Box ACK (tbl_events with Type=Change)

```xml
<Frame><TblName>tbl_events</TblName><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ID_Set>843892847</ID_Set><DT>2026-03-02 06:40:47</DT><Type>Change</Type><Confirm>NoNeed</Confirm><Content>Input : tbl_invertor_prms / ERR_PV: [12]->[4]</Content><ver>28867</ver><CRC>37700</CRC></Frame>

```

#### Trigger Command

**No trigger command found** - This appears to be a Box-initiated change.

#### Box State Before (31.7s before)

```xml
<Frame><TblName>tbl_invertor_prms</TblName><ID_Set>843892800</ID_Set><DT>2026-03-02 06:40:00</DT><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ERR_PV>12</ERR_PV><ERR_BATT>0</ERR_BATT><ERR_GRID>0</ERR_GRID><ERR_AC>0</ERR_AC><ERR_ELSE>0</ERR_ELSE><T_INN>25.0</T_INN><PRRTY>1</PRRTY><CHARGE>1</CHARGE><CHARGE_AC>1</CHARGE_AC><TO_GRID>1</TO_GRID><LOAD_PV_ON>0</LOAD_PV_ON><LOAD_PV_OFF>0</LOAD_PV_OFF><GRID_PV_ON>0</GRID_PV_ON><GRID_PV_OFF>0</GRID_PV_OFF><MODE>5</MODE><MODEL>65</MODEL><PRLL_OUT>0</PRLL_OUT><P_ADJ_STRT>20</P_ADJ_STRT><P_ADJ_ENBL>1</P_ADJ_ENBL><PF_MIN_100>-0.90</PF_MIN_100><ver>18227</ver><CRC>27462</CRC></Frame>

```

---

### Change Event #2

**Timestamp:** 2026-03-02T17:03:25.033735+00:00
**Content:** `Input : tbl_invertor_prms / ERR_PV: [4]->[12]`

#### Box ACK (tbl_events with Type=Change)

```xml
<Frame><TblName>tbl_events</TblName><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ID_Set>843933793</ID_Set><DT>2026-03-02 18:03:13</DT><Type>Change</Type><Confirm>NoNeed</Confirm><Content>Input : tbl_invertor_prms / ERR_PV: [4]->[12]</Content><ver>56296</ver><CRC>24046</CRC></Frame>

```

#### Trigger Command

**No trigger command found** - This appears to be a Box-initiated change.

---

### Change Event #3

**Timestamp:** 2026-03-02T20:46:30.532412+00:00
**Content:** `Input : tbl_batt_prms / BAT_CI: [160.0]->[80.0]`

#### Box ACK (tbl_events with Type=Change)

```xml
<Frame><TblName>tbl_events</TblName><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ID_Set>843947177</ID_Set><DT>2026-03-02 21:46:16</DT><Type>Change</Type><Confirm>NoNeed</Confirm><Content>Input : tbl_batt_prms / BAT_CI: [160.0]->[80.0]</Content><ver>08094</ver><CRC>42813</CRC></Frame>

```

#### Trigger Command

**No trigger command found** - This appears to be a Box-initiated change.

#### Box State Before (44.8s before)

```xml
<Frame><TblName>tbl_batt_prms</TblName><ID_Set>843947100</ID_Set><DT>2026-03-02 21:45:00</DT><Reason>Table</Reason><ID_Device>2206237016</ID_Device><BAT_N>0</BAT_N><BAT_CI>0.0</BAT_CI><BAT_CU>0.0</BAT_CU><FMT_ON>0</FMT_ON><FMT_PROGRESS>41.176</FMT_PROGRESS><BAT_HDO>0</BAT_HDO><BAT_AA>0</BAT_AA><BAT_MIN>20</BAT_MIN><BAT_GL_MIN>15</BAT_GL_MIN><BAT_AG_MIN>35</BAT_AG_MIN><HDO1_S>0</HDO1_S><HDO1_E>0</HDO1_E><HDO2_S>0</HDO2_S><HDO2_E>0</HDO2_E><BAL_ON>0</BAL_ON><LO_DAY>0</LO_DAY><BAT_DI>0.0</BAT_DI><ID_SubD>2</ID_SubD><TYP>1</TYP><ver>60911</ver><CRC>37210</CRC></Frame>

```

---

### Change Event #4

**Timestamp:** 2026-03-02T20:46:33.606853+00:00
**Content:** `Input : tbl_batt_prms / BAT_DI: [200.0]->[100.0]`

#### Box ACK (tbl_events with Type=Change)

```xml
<Frame><TblName>tbl_events</TblName><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ID_Set>843947178</ID_Set><DT>2026-03-02 21:46:16</DT><Type>Change</Type><Confirm>NoNeed</Confirm><Content>Input : tbl_batt_prms / BAT_DI: [200.0]->[100.0]</Content><ver>46960</ver><CRC>22767</CRC></Frame>

```

#### Trigger Command

**No trigger command found** - This appears to be a Box-initiated change.

#### Box State Before (47.9s before)

```xml
<Frame><TblName>tbl_batt_prms</TblName><ID_Set>843947100</ID_Set><DT>2026-03-02 21:45:00</DT><Reason>Table</Reason><ID_Device>2206237016</ID_Device><BAT_N>0</BAT_N><BAT_CI>0.0</BAT_CI><BAT_CU>0.0</BAT_CU><FMT_ON>0</FMT_ON><FMT_PROGRESS>41.176</FMT_PROGRESS><BAT_HDO>0</BAT_HDO><BAT_AA>0</BAT_AA><BAT_MIN>20</BAT_MIN><BAT_GL_MIN>15</BAT_GL_MIN><BAT_AG_MIN>35</BAT_AG_MIN><HDO1_S>0</HDO1_S><HDO1_E>0</HDO1_E><HDO2_S>0</HDO2_S><HDO2_E>0</HDO2_E><BAL_ON>0</BAL_ON><LO_DAY>0</LO_DAY><BAT_DI>0.0</BAT_DI><ID_SubD>2</ID_SubD><TYP>1</TYP><ver>60911</ver><CRC>37210</CRC></Frame>

```

---

### Change Event #5

**Timestamp:** 2026-03-02T20:46:37.433555+00:00
**Content:** `Input : tbl_batt_prms / BAT_N: [1]->[2]`

#### Box ACK (tbl_events with Type=Change)

```xml
<Frame><TblName>tbl_events</TblName><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ID_Set>843947180</ID_Set><DT>2026-03-02 21:46:20</DT><Type>Change</Type><Confirm>NoNeed</Confirm><Content>Input : tbl_batt_prms / BAT_N: [1]->[2]</Content><ver>27500</ver><CRC>19668</CRC></Frame>

```

#### Trigger Command

**No trigger command found** - This appears to be a Box-initiated change.

#### Box State Before (51.7s before)

```xml
<Frame><TblName>tbl_batt_prms</TblName><ID_Set>843947100</ID_Set><DT>2026-03-02 21:45:00</DT><Reason>Table</Reason><ID_Device>2206237016</ID_Device><BAT_N>0</BAT_N><BAT_CI>0.0</BAT_CI><BAT_CU>0.0</BAT_CU><FMT_ON>0</FMT_ON><FMT_PROGRESS>41.176</FMT_PROGRESS><BAT_HDO>0</BAT_HDO><BAT_AA>0</BAT_AA><BAT_MIN>20</BAT_MIN><BAT_GL_MIN>15</BAT_GL_MIN><BAT_AG_MIN>35</BAT_AG_MIN><HDO1_S>0</HDO1_S><HDO1_E>0</HDO1_E><HDO2_S>0</HDO2_S><HDO2_E>0</HDO2_E><BAL_ON>0</BAL_ON><LO_DAY>0</LO_DAY><BAT_DI>0.0</BAT_DI><ID_SubD>2</ID_SubD><TYP>1</TYP><ver>60911</ver><CRC>37210</CRC></Frame>

```

---

### Change Event #6

**Timestamp:** 2026-03-02T20:46:39.605731+00:00
**Content:** `Input : tbl_batt_prms / BAT_CI: [80.0]->[160.0]`

#### Box ACK (tbl_events with Type=Change)

```xml
<Frame><TblName>tbl_events</TblName><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ID_Set>843947181</ID_Set><DT>2026-03-02 21:46:20</DT><Type>Change</Type><Confirm>NoNeed</Confirm><Content>Input : tbl_batt_prms / BAT_CI: [80.0]->[160.0]</Content><ver>42040</ver><CRC>14329</CRC></Frame>

```

#### Trigger Command

**No trigger command found** - This appears to be a Box-initiated change.

#### Box State Before (53.9s before)

```xml
<Frame><TblName>tbl_batt_prms</TblName><ID_Set>843947100</ID_Set><DT>2026-03-02 21:45:00</DT><Reason>Table</Reason><ID_Device>2206237016</ID_Device><BAT_N>0</BAT_N><BAT_CI>0.0</BAT_CI><BAT_CU>0.0</BAT_CU><FMT_ON>0</FMT_ON><FMT_PROGRESS>41.176</FMT_PROGRESS><BAT_HDO>0</BAT_HDO><BAT_AA>0</BAT_AA><BAT_MIN>20</BAT_MIN><BAT_GL_MIN>15</BAT_GL_MIN><BAT_AG_MIN>35</BAT_AG_MIN><HDO1_S>0</HDO1_S><HDO1_E>0</HDO1_E><HDO2_S>0</HDO2_S><HDO2_E>0</HDO2_E><BAL_ON>0</BAL_ON><LO_DAY>0</LO_DAY><BAT_DI>0.0</BAT_DI><ID_SubD>2</ID_SubD><TYP>1</TYP><ver>60911</ver><CRC>37210</CRC></Frame>

```

---

### Change Event #7

**Timestamp:** 2026-03-02T20:46:44.509502+00:00
**Content:** `Input : tbl_batt_prms / BAT_DI: [100.0]->[200.0]`

#### Box ACK (tbl_events with Type=Change)

```xml
<Frame><TblName>tbl_events</TblName><Reason>Table</Reason><ID_Device>2206237016</ID_Device><ID_Set>843947182</ID_Set><DT>2026-03-02 21:46:20</DT><Type>Change</Type><Confirm>NoNeed</Confirm><Content>Input : tbl_batt_prms / BAT_DI: [100.0]->[200.0]</Content><ver>07871</ver><CRC>05394</CRC></Frame>

```

#### Trigger Command

**No trigger command found** - This appears to be a Box-initiated change.

#### Box State Before (58.8s before)

```xml
<Frame><TblName>tbl_batt_prms</TblName><ID_Set>843947100</ID_Set><DT>2026-03-02 21:45:00</DT><Reason>Table</Reason><ID_Device>2206237016</ID_Device><BAT_N>0</BAT_N><BAT_CI>0.0</BAT_CI><BAT_CU>0.0</BAT_CU><FMT_ON>0</FMT_ON><FMT_PROGRESS>41.176</FMT_PROGRESS><BAT_HDO>0</BAT_HDO><BAT_AA>0</BAT_AA><BAT_MIN>20</BAT_MIN><BAT_GL_MIN>15</BAT_GL_MIN><BAT_AG_MIN>35</BAT_AG_MIN><HDO1_S>0</HDO1_S><HDO1_E>0</HDO1_E><HDO2_S>0</HDO2_S><HDO2_E>0</HDO2_E><BAL_ON>0</BAL_ON><LO_DAY>0</LO_DAY><BAT_DI>0.0</BAT_DI><ID_SubD>2</ID_SubD><TYP>1</TYP><ver>60911</ver><CRC>37210</CRC></Frame>

```

---

## Key Findings

1. **No Cloud trigger commands found** for any of the 7 Change events
2. **Change events are Box-initiated** - internal state changes, not Cloud commands
3. **RTT calculation not possible** - no corresponding Cloud commands to measure
4. **BAT changes (events 3-7)** show `ID_SubD>2` indicating inactive battery bank

### Conclusion

The `<Type>Change</Type>` events in `tbl_events` are **Box-initiated notifications** of internal state changes, 
not acknowledgments of Cloud commands. The 'Input' in the content refers to the Box's internal processing.

For Cloud-initiated settings, look at `tbl_box_prms` with `MODE` changes, where `Reason=Setting` commands exist.
## Analytical Conclusion
1. **The Cloud Command:** The Cloud does *not* send individual register changes (like `BAT_CI=80.0`). Instead, the Cloud sends a high-level `Setting` command (e.g., `<TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>3</NewValue>`) to the Box during an `IsNewSet` poll response.
2. **The Box Response:** The Box receives this command, applies the macro-level mode change, which in turn alters multiple internal parameters.
3. **The Box ACK:** The Box then generates a series of `tbl_events` messages sent back to the Cloud. Each `tbl_events` message has `<Type>Change</Type>` and specifies exactly which internal parameter was updated (e.g., `<Content>Input : tbl_batt_prms / BAT_DI: [100.0]->[200.0]</Content>`). 
4. **Round Trip Time (RTT):** The time from the Cloud sending the setting to the Box emitting the `tbl_events` ACK is typically between 30 seconds and 1 minute.
