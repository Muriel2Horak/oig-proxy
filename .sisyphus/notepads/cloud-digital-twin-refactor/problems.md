# F1 Audit Open Problems (2026-03-04)

- Full command parity evidence is incomplete in CI because `tests/test_whitelist_parity_matrix.py` remains intentionally RED (stub adapter raising `NotImplementedError`).
- `DigitalTwin.restore_from_snapshot()` is unimplemented, and corresponding RED test fails in full regression.
- Plan scope mentions REPLAY mode routing, but runtime `ProxyMode` only defines ONLINE/HYBRID/OFFLINE; REPLAY local-control routing cannot be verified as implemented.


## Task 18: Final Validation Status (2026-03-04)

### Resolved
- No dead legacy ACK references found (pending_frame is intentionally used)
- Routing contract validated as conforming to hybrid+failover+twin policy
- Emergency rollback controls (TWIN_KILL_SWITCH) verified and intact

### Remaining Open Problems
1. **RED Test for restore_from_snapshot**: Intentional TDD pattern, not blocking cutover
2. **Deprecated datetime.utcnow()**: Warning only, should be fixed in future cleanup

### Cutover Ready
All Task 18 requirements met:
- [x] No dead legacy ACK references remain
- [x] Routing behavior conforms to hybrid+failover+twin policy
- [x] Legacy cleanup tests pass
- [x] Final routing conformance tests pass
- [x] Emergency rollback controls remain active

## 2026-03-04 04:17:25Z - F1 unresolved problems
- No green, exhaustive command parity gate currently verifies every CONTROL_WRITE_WHITELIST command across terminal outcomes in the twin route.
- Observability task from the plan appears incomplete (no twin-specific telemetry dimensions found in telemetry_client.py/proxy_status.py).
