# XLS metadata audit (2026-03-13)

Source workbook: `docs/CBB Box - modbus TCP.xls`
Target map: `addon/oig-proxy/sensor_map.json`
Target control whitelist: `addon/oig-proxy/mqtt/client.py` (`CONTROL_WRITE_WHITELIST`)

## Scope and parsing notes

- Sheets analyzed: `Databaze`, `protokol RTU`, `protokol FSP`, `warining 3F`, `hlasky`.
- `Databaze` was parsed with stable column mapping:
  - table: column A (`TBL_*` section headers)
  - access: column I (`Cteni`/`Zapis`)
  - key: column K
  - type: column L
  - description: column N
- Duplicate rows across product variants (3F/1F/Easy/SKAO/...) were deduplicated by `table:key`.
- ID/meta rows (`ID`, `ID_Device`, `ID_Set`, `DT`) were excluded.

## High-confidence findings

### 1) Coverage against v2 map

- `Databaze` unique keys: **288**
- Unique RW keys in `Databaze`: **155**
- Missing in `sensor_map` (unique): **55**
- Missing RW in `sensor_map` (unique): **16**

Most missing keys are concentrated in tables that appear to be outside the current HA-focused scope:

- `tbl_car_charge`: 18
- `tbl_mermodul`: 14
- `tbl_h_pump`: 3
- `tbl_aircon`: 3
- `tbl_wl_charge`: 3
- `tbl_recuper`: 3

RW missing keys are mainly:

- `tbl_mermodul:*` (14 keys)
- `tbl_box_prms:MODE1`
- `tbl_car_charge_prms:IDENT`

### 2) RTU protocol sheet coverage

- `protokol RTU` unique keys: **95**
- Missing in `sensor_map`: **1**
- Missing key: `tbl_box_prms:FV2` (description: "ochrana proti prepeti - 2FV/DC")

Interpretation: core runtime telemetry appears almost fully mapped for RTU-exposed keys.

### 3) FSP protocol (settings/control relevance)

- `protokol FSP` keys parsed: **23**
- Found in v2 map: **19**
- Missing in map: **4**
  - `PV_PRTY` (has setting command)
  - `CHRG_SRC`
  - `LOAD_SRC_PVY`
  - `LOAD_SRC_PVN`

For FSP keys that have command syntax and are present in map:

- Commandable keys in map: **18**
- Currently whitelisted for control publish: **2**
  - `tbl_invertor_prm1:A_MAX_CHRG`
  - `tbl_invertor_prm1:AAC_MAX_CHRG`
- Commandable but not whitelisted: **16**

Not-whitelisted examples:

- `tbl_invertor_prm1:V_MIN_AC`
- `tbl_invertor_prm1:V_MAX_AC`
- `tbl_invertor_prm1:F_MIN_AC`
- `tbl_invertor_prm1:F_MAX_AC`
- `tbl_invertor_prm1:V_CHRG`
- `tbl_invertor_prm1:V_CHAR_FLO`
- `tbl_invertor_prm1:V_CUT_GRID`
- `tbl_invertor_prm1:V_RE_GRID`
- `tbl_invertor_prm1:A_MAX_DIS_HYB`
- `tbl_invertor_prm1:P_CAL_R`
- `tbl_invertor_prm1:P_CAL_S`
- `tbl_invertor_prm1:P_CAL_T`
- `tbl_invertor_prm1:BUZ_MUT`
- `tbl_invertor_prm1:GEN_AC_SRC`
- `tbl_invertor_prms:PRLL_OUT`
- `tbl_invertor_prms:P_ADJ_STRT`

### 4) Warning and message sheet validation

- `warining 3F` warning groups in map: `ERR_PV`, `ERR_BATT`, `ERR_GRID`, `ERR_AC`, `ERR_ELSE` (all present).
- `warining 3F` groups not present in map: `P004CFS`, `P006HFS`.

- `hlasky` key references checked: **60**
- Missing references in map: **8**
  - `tbl_device:LastCall`
  - `tbl_device:LastUpdate`
  - `tbl_car_charge:ACT_P_L1`
  - `tbl_car_charge:ACT_P_L2`
  - `tbl_car_charge:ACT_P_L3`
  - `tbl_car_charge_prms:CHARGING`
  - `tbl_car_charge:ETOCAR_D`
  - `tbl_box_prms:FV2`

### 5) Binary candidates from FSP

Keys marked as binary/TINYINT in FSP but not flagged with `is_binary` in map:

- `tbl_invertor_prm1:BUZ_MUT`
- `tbl_invertor_prm1:GEN_AC_SRC`
- `tbl_invertor_prms:MODE` (enum-like mode selector)

## Important caveat on unit mismatch checks

Naive extraction of units from free-text parentheses in `Databaze` creates false positives (for example `(Victron)`, `(C.V. voltage)`, `(-/+W)`).
Therefore this audit treats unit comparison as advisory unless unit is explicit in protocol columns (RTU/FSP) or unambiguous.

## Suggested implementation order (not yet applied)

1. Add safe missing telemetry key from RTU: `tbl_box_prms:FV2`.
2. Curate FSP-based control policy update:
   - decide which of the 16 commandable-but-not-whitelisted keys should be enabled.
3. Add explicit binary/enum semantics for:
   - `BUZ_MUT`, `GEN_AC_SRC`, `MODE`.
4. Decide scope for car-charge/mermodul tables:
   - either map them fully, or keep intentionally out-of-scope and document it.

## Update after enum pass (2026-03-13, later)

Applied in `sensor_map.json`:

- Added enum maps:
  - `tbl_invertor_prms:MODE` (`0..5` -> `Home 1..6`, with `3=Home UPS`)
  - `tbl_box_prms:MODE` (`0..3` -> `Home 1..3`, `Home UPS`)
  - `tbl_invertor_prms:PRRTY` (`0..2` flow-priority labels)
- Added missing RTU key: `tbl_box_prms:FV2`
- Added binary typing: `tbl_invertor_prm1:BUZ_MUT`, `tbl_invertor_prm1:GEN_AC_SRC`, `tbl_box_prms:ACCON`, `tbl_box_prms:BYPASS_M`, `tbl_box_prms:ENLOADS`, `tbl_box_prms:NB`

Current remaining high-confidence gaps in main scope:

- Warnings groups from XLS not yet mapped as sensors:
  - `P004CFS`
  - `P006HFS`
- High-confidence unit discrepancy (RTU vs map):
  - `tbl_batt_prms:BAT_AA` -> XLS suggests `Wh`, map currently uses `W`.
    - This may be XLS wording inconsistency; needs runtime/sample-value confirmation before changing.
