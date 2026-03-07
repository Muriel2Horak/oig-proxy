## 2026-03-07

- Repository baseline tests are currently failing broadly in `tests/test_proxy*.py` for reasons unrelated to this refactor (many pre-existing `AttributeError` failures in control pipeline and test scaffolding).
- Targeted `_handle_box_connection` tests fail in baseline because test fixtures instantiate `OIGProxy` without attributes like `_pending_twin_activation`; this is pre-existing and blocks clean behavior-preservation proof via tests.

- `tests/test_digital_twin.py` run reports existing mixed expectations with `xfail`/`xpass` markers; this refactor did not alter marker behavior but output includes both statuses.

- Running broader twin suite (`tests/test_digital_twin.py tests/test_twin_*.py`) surfaces pre-existing behavior mismatch around auto-queued SA follow-up after applied events; 4 failures in roundtrip/replay tests are not introduced by this refactor because this task intentionally preserved transaction behavior.
- Repository test suite is currently red in this environment before/after refactor (many unrelated failures in control API/control pipeline/event paths), so full green verification is blocked by baseline instability.
- Some legacy/unit tests instantiate `OIGProxy` via `__new__` with partial attributes; extracted helpers needed defensive handling around twin-routing availability to avoid attribute errors in these minimal fixtures.
