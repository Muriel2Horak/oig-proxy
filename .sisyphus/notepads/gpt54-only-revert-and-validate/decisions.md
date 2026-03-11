## 2026-03-10

- Replaced existing `tests/test_correlation_id.py` with branch-complete tests covering all public API surfaces rather than incrementally patching old tests; this ensures stable 100% coverage guarantees for future regressions.
- Added `sys.path` injection in the test module to import `correlation_id` from `addon/oig-proxy`, matching existing repository layout without modifying production import behavior.
