# Setting Frame Experiment - Mock Server

## TL;DR

> **Quick Summary**: Modify mock server to send Setting frames to BOX on IsNewSet requests, observe BOX behavior (ACK vs disconnect).
> 
> **Deliverables**:
> - Modified `server.py` with Setting experiment capability
> - Live observation of BOX response
> - Evidence of what works/doesn't work
> 
> **Estimated Effort**: Quick (30 min)
> **Parallel Execution**: NO - sequential experiment
> **Critical Path**: Modify code → Deploy → Observe → Iterate

---

## Context

### Original Request
BOX ignoruje Setting příkazy z proxy. Máme mock server na NAS, kde BOX komunikuje. Chceme experimentovat s posíláním Setting framů a sledovat reakci BOXu.

### Research Findings
**Real cloud Setting format** (from payloads_ha_full.db, 238 samples):
```xml
<Frame>
  <ID>13601763</ID>
  <ID_Device>2206237016</ID_Device>
  <ID_Set>1766084105</ID_Set>
  <ID_SubD>0</ID_SubD>
  <DT>18.12.2025 19:55:05</DT>
  <NewValue>0</NewValue>
  <Confirm>New</Confirm>
  <TblName>tbl_box_prms</TblName>
  <TblItem>MODE</TblItem>
  <ID_Server>5</ID_Server>
  <mytimediff>0</mytimediff>
  <Reason>Setting</Reason>
  <TSec>2025-12-18 19:26:23</TSec>
  <ver>64568</ver>
  <CRC>33382</CRC>
</Frame>
```

**BOX ACK format** (when it accepts):
```xml
<Frame><Result>ACK</Result><Rdt>2025-12-18 20:26:21</Rdt><Reason>Setting</Reason><Tmr>100</Tmr><ver>44568</ver><CRC>07629</CRC></Frame>
```

### Current State
- Mock server: `oig-diagnostic-cloud` container on NAS
- BOX polls every ~15s: IsNewFW → IsNewSet → IsNewWeather
- Currently responds: END+Time on IsNewSet

---

## Work Objectives

### Core Objective
Modify mock server to respond with Setting frame on IsNewSet, observe if BOX sends ACK or disconnects.

### Concrete Deliverables
- Modified `server.py` with Setting experiment mode
- Container restart with new env vars
- Log evidence of BOX response

### Definition of Done
- [ ] BOX receives Setting frame (visible in logs)
- [ ] BOX response captured (ACK or disconnect)
- [ ] Findings documented

### Must Have
- Setting frame format must match cloud exactly
- CRC must be valid
- Experiment must be toggleable via env var

### Must NOT Have (Guardrails)
- DO NOT break normal mock server operation
- DO NOT modify production proxy code
- DO NOT commit secrets or credentials

---

## Verification Strategy

### QA Policy
Manual observation of logs. Evidence saved to `.sisyphus/evidence/`.

| Deliverable Type | Verification Tool | Method |
|------------------|-------------------|--------|
| Mock server modification | Bash (ssh nas) | Check container logs |
| BOX response | Bash (ssh nas) | Grep logs for ACK or disconnect |

---

## TODOs

- [ ] 1. Modify server.py to add Setting experiment mode

  **What to do**:
  1. Add config vars at top of server.py (after line 35):
     ```python
     # Setting experiment: send Setting frame on IsNewSet instead of END+Time
     SETTING_EXPERIMENT = os.environ.get("SETTING_EXPERIMENT", "0") == "1"
     SETTING_NEW_VALUE = os.environ.get("SETTING_NEW_VALUE", "1")
     SETTING_TABLE = os.environ.get("SETTING_TABLE", "tbl_box_prms")
     SETTING_ITEM = os.environ.get("SETTING_ITEM", "MODE")
     ```
  
  2. Modify `_generate_ack()` function (around line 584) to check experiment flag:
     ```python
     # IsNewSet: either Setting experiment or normal END+Time
     if result == "IsNewSet" or table_name.endswith("IsNewSet"):
         if SETTING_EXPERIMENT:
             # Generate Setting frame like real cloud
             import random
             now_local = datetime.now(LOCAL_TZ)
             now_utc = datetime.now(timezone.utc)
             
             inner = (
                 f"<ID>{random.randint(10000000, 99999999)}</ID>"
                 f"<ID_Device>2206237016</ID_Device>"
                 f"<ID_Set>{random.randint(1000000000, 2000000000)}</ID_Set>"
                 f"<ID_SubD>0</ID_SubD>"
                 f"<DT>{now_local.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
                 f"<NewValue>{SETTING_NEW_VALUE}</NewValue>"
                 f"<Confirm>New</Confirm>"
                 f"<TblName>{SETTING_TABLE}</TblName>"
                 f"<TblItem>{SETTING_ITEM}</TblItem>"
                 f"<ID_Server>5</ID_Server>"
                 f"<mytimediff>0</mytimediff>"
                 f"<Reason>Setting</Reason>"
                 f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
             )
             logger.info(f"  -> [EXPERIMENT] Sending Setting: {SETTING_TABLE}.{SETTING_ITEM}={SETTING_NEW_VALUE}")
             return local_oig_crc.build_frame(inner)
         
         # Normal behavior: END+Time
         now_local = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
         ...
     ```

  **Must NOT do**:
  - Do not change behavior when SETTING_EXPERIMENT is not set

  **References**:
  - `server.py:28-37` - Configuration section
  - `server.py:565-609` - `_generate_ack()` function
  - `local_oig_crc.py:37-43` - `build_frame()` function

  **Acceptance Criteria**:
  - [ ] Code compiles without errors
  - [ ] Container starts successfully

  **QA Scenarios**:
  ```
  Scenario: Normal mode (experiment off)
    Tool: Bash (ssh nas)
    Steps:
      1. Restart container without SETTING_EXPERIMENT env
      2. Wait for IsNewSet in logs
    Expected Result: "responding with END+Time" in logs
    Evidence: .sisyphus/evidence/task-1-normal-mode.log

  Scenario: Experiment mode (experiment on)
    Tool: Bash (ssh nas)
    Steps:
      1. Restart container with SETTING_EXPERIMENT=1
      2. Wait for IsNewSet in logs
    Expected Result: "[EXPERIMENT] Sending Setting" in logs
    Evidence: .sisyphus/evidence/task-1-experiment-mode.log
  ```

---

- [ ] 2. Deploy modified server to NAS

  **What to do**:
  1. Copy modified server.py to NAS:
     ```bash
     scp server.py nas:/volume1/docker/oig-diagnostic/server.py
     ```
  
  2. Restart container with experiment enabled:
     ```bash
     ssh nas "/usr/local/bin/docker restart oig-diagnostic-cloud"
     # Or with env var:
     ssh nas "/usr/local/bin/docker stop oig-diagnostic-cloud"
     ssh nas "/usr/local/bin/docker run -d --name oig-diagnostic-cloud -e SETTING_EXPERIMENT=1 ..."
     ```

  **References**:
  - NAS path: `/volume1/docker/oig-diagnostic/`
  - Container: `oig-diagnostic-cloud`

  **Acceptance Criteria**:
  - [ ] Container running with new code
  - [ ] Logs show server started

  **QA Scenarios**:
  ```
  Scenario: Container healthy after deploy
    Tool: Bash (ssh nas)
    Steps:
      1. ssh nas "/usr/local/bin/docker ps | grep oig-diagnostic"
      2. ssh nas "/usr/local/bin/docker logs oig-diagnostic-cloud --tail 5"
    Expected Result: Container running, no errors in logs
    Evidence: .sisyphus/evidence/task-2-deploy.log
  ```

---

- [ ] 3. Observe BOX response to Setting frame

  **What to do**:
  1. Enable experiment mode
  2. Watch logs for next IsNewSet from BOX
  3. Capture BOX response (ACK or disconnect)
  
  ```bash
  # Watch logs in real-time
  ssh nas "/usr/local/bin/docker logs -f oig-diagnostic-cloud 2>&1" | tee experiment.log
  ```

  **Expected outcomes**:
  - **Success**: BOX sends ACK with `<Reason>Setting</Reason>`
  - **Failure**: BOX disconnects without ACK (current behavior)

  **References**:
  - Real ACK format: `<Frame><Result>ACK</Result><Rdt>...</Rdt><Reason>Setting</Reason><Tmr>100</Tmr>...`

  **Acceptance Criteria**:
  - [ ] Setting frame sent to BOX (visible in logs)
  - [ ] BOX response captured

  **QA Scenarios**:
  ```
  Scenario: BOX receives Setting and responds
    Tool: Bash (ssh nas)
    Steps:
      1. Enable SETTING_EXPERIMENT=1
      2. Wait for IsNewSet (max 60s)
      3. Check if BOX sent response frame
    Expected Result: Either ACK with Reason=Setting OR disconnect logged
    Evidence: .sisyphus/evidence/task-3-box-response.log
  ```

---

- [ ] 4. Document findings and iterate

  **What to do**:
  Based on BOX response:
  
  **If BOX sends ACK**:
  - Success! Document exact frame format that worked
  - Compare with proxy's Setting format
  - Identify difference
  
  **If BOX disconnects**:
  - Try variations:
    - Different timing (send after small delay)
    - Different field values
    - Exact copy of historical frame (including original timestamps)

  **Acceptance Criteria**:
  - [ ] Findings documented in `.sisyphus/evidence/`
  - [ ] Next steps identified

---

## Success Criteria

### Verification Commands
```bash
# Check if experiment is working
ssh nas "/usr/local/bin/docker logs oig-diagnostic-cloud --tail 50 | grep -E '(EXPERIMENT|Setting|ACK)'"
```

### Final Checklist
- [ ] Setting frame sent to BOX
- [ ] BOX response observed and documented
- [ ] Next iteration planned (if needed)
