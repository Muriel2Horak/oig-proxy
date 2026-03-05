# Learnings - Task 2: Capture and annotate live golden handshake windows

## Initial Investigation (2026-02-18)

### Resources Found:
1. **HA Export Pattern**: `/Users/martinhorak/Projects/oig-proxy/testing/export_ha_session.py`
   - Uses SSH to HA: `ssh ha` → `sudo docker exec addon_d7b5d5b1_oig_proxy`
   - DB path: `/data/payloads.db`
   - Queries frames with conn_id grouping
   - Extracts full frame sequences with direction, timestamps, raw data

2. **Setting Acceptance Contract**: `/Users/martinhorak/Projects/oig-diagnostic-cloud/analysis/setting-acceptance-contract.yaml`
   - Defines successful sequence: Setting → ACK(Reason=Setting) → END
   - Required fields in frames
   - Timing constraints: 6-7 seconds observed
   - Success rate: 1.4% (6 success windows, 436 failed)

3. **Missing Success Window Reference**: No prior success-window.json found
   - Will need to create golden fixtures based on contract requirements

### Key Requirements for Golden Fixtures:
- **Sequence Pattern**: MODE frame → ACK(Reason=Setting) → END linkage
- **Data Sources**: Today's live HA data (2026-02-18)
- **Metadata**: Source timestamp range, extraction query
- **Format**: Structured JSON with mode_id, ack_id, end_id fields
- **Storage**: `/Users/martinhorak/Projects/oig-diagnostic-cloud/analysis/golden-handshake-fixtures/`

### Extraction Strategy:
1. Query HA DB for today's MODE changes
2. Find complete sequences with ACK and END frames
3. Annotate sequence boundaries
4. Export with proper metadata
5. Validate against acceptance contract

### Technical Approach:
- Extend existing HA export pattern
- Add sequence detection logic
- Include validation fields (mode_id, ack_id, end_id)
- Store in target directory with proper naming

---
## Task 6: Define explicit state-machine transition contract (2026-02-18)

### Implementation:
- Created comprehensive transition contract document at `/Users/martinhorak/Projects/oig-diagnostic-cloud/docs/setting-transition-contract.md`
- Document defines all 6 states: IDLE, PENDING, DELIVERED, ACKED, ENDED, FAILED
- Explicitly documented all allowed transitions with trigger conditions and log evidence signatures
- Defined forbidden transitions with violation detection mechanisms
- Established key invariants, especially the critical ACK invariant

### Critical Invariant Established:
**INVARIANT: No END emission while state is DELIVERED waiting for valid ACK**
- END frame can only be sent from ACKED state
- Any DELIVERED → ENDED transition must be blocked and logged as violation
- This prevents protocol violations and inconsistent state

### Key Components Defined:
1. **Allowed Transitions Matrix**: Complete mapping of all valid state transitions
2. **Forbidden Transitions Table**: Explicit documentation of illegal transitions
3. **Retry/Backoff Table**: Exponential backoff formula (2^retry_count) and fail-closed behavior
4. **Log Evidence Signatures**: Required log patterns for each transition
5. **Validation Checklist**: Verification criteria for implementation

### Commit Details:
- Message: `docs(mock): define setting transition contract`
- Files: `docs/setting-transition-contract.md`
- Successfully passed pre-commit hook validation (40 tests collected)

### Next Steps:
- This contract serves as the foundation for implementing state machine validation
- Will be referenced in subsequent tasks for compliance verification
- Provides clear specification for debugging state-related issues

---
## Implementation Notes (to be filled during implementation)
