## Task: Twin inflight deterministic finalization (Blind Branch #2)

- `python` binary was unavailable in this environment; used `python3` for required pytest execution.
- Initial deterministic-release implementation surfaced failing expectations in `tests/test_digital_twin.py` that assumed inflight persisted after `applied`/timeout error transitions.
