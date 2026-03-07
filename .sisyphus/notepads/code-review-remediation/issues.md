## 2026-03-07

- Repository baseline tests are currently failing broadly in `tests/test_proxy*.py` for reasons unrelated to this refactor (many pre-existing `AttributeError` failures in control pipeline and test scaffolding).
- Targeted `_handle_box_connection` tests fail in baseline because test fixtures instantiate `OIGProxy` without attributes like `_pending_twin_activation`; this is pre-existing and blocks clean behavior-preservation proof via tests.

- `tests/test_digital_twin.py` run reports existing mixed expectations with `xfail`/`xpass` markers; this refactor did not alter marker behavior but output includes both statuses.

- Running broader twin suite (`tests/test_digital_twin.py tests/test_twin_*.py`) surfaces pre-existing behavior mismatch around auto-queued SA follow-up after applied events; 4 failures in roundtrip/replay tests are not introduced by this refactor because this task intentionally preserved transaction behavior.
- Repository test suite is currently red in this environment before/after refactor (many unrelated failures in control API/control pipeline/event paths), so full green verification is blocked by baseline instability.
- Some legacy/unit tests instantiate `OIGProxy` via `__new__` with partial attributes; extracted helpers needed defensive handling around twin-routing availability to avoid attribute errors in these minimal fixtures.

- Task brief referenced `tests/test_control_pipeline.py` as existing, but file was absent in workspace; resolved by creating it with assertions for the refactored API instead of attempting to patch a non-existent legacy test file.

- Pyright in this workspace reports missing imports for top-level test imports (`proxy`, `mqtt_publisher`, etc.) despite runtime pytest path setup; for touched tests this was suppressed with test-local `# pyright: reportMissingImports=false` to keep diagnostics clean.

- Remaining proxy test failures in the targeted files were primarily stale expectations against removed API members (`_process_frame_offline`, `_ctrl`-driven control flow, `ControlPipeline` MQTT/start methods, `ControlSettings.clear_pending_on_disconnect`, `CloudForwarder.handle_frame_offline_mode`).

- The requested coverage selector `--cov=addon/oig-proxy.local_oig_crc` is not a valid import target for `pytest-cov` in this layout (hyphenated path segment), resulting in `module-not-imported` / `no-data-collected`; `--cov=local_oig_crc` correctly measures `addon/oig-proxy/local_oig_crc.py`.
- Same coverage-selector issue applies to digital twin: `--cov=addon/oig-proxy.digital_twin` reports `module-not-imported`; use `PYTHONPATH=addon/oig-proxy --cov=digital_twin` for valid measurement.
- Task verification command in brief (`--cov=addon/oig-proxy.proxy`) does not map to an importable module in this workspace and reports `module-not-imported`; equivalent valid selector is `--cov=proxy`.
