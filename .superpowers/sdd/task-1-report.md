# Task 1 Report - Configuration Surface

## Scope

Implemented the local GetActual configuration surface only:

- `addon/oig-proxy/config.json`
- `addon/oig-proxy/run`
- `addon/oig-proxy/config.py`
- `docs/v2/configuration.md`
- `tests/v2/test_addon_dns_config.py`

No proxy scheduler behavior was changed.

## RED Evidence

Command:

```bash
python3 -m pytest tests/v2/test_addon_dns_config.py -q
```

Result before implementation:

- `5 failed, 2 passed`
- Failures were expected:
  - `Config` did not expose `local_getactual_enabled`
  - `Config` did not expose `local_getactual_interval_s`
  - `config.json` was still `2.1.0`
  - `run` did not export `LOCAL_GETACTUAL_*`

## GREEN Evidence

Command:

```bash
python3 -m pytest tests/v2/test_addon_dns_config.py -q
```

Result after implementation:

- `7 passed in 0.02s`

## Files Changed

- `tests/v2/test_addon_dns_config.py`
- `addon/oig-proxy/config.json`
- `addon/oig-proxy/run`
- `addon/oig-proxy/config.py`
- `docs/v2/configuration.md`

## Self-Review

- Config defaults now match the requested behavior: disabled by default and interval clamped to at least 10 seconds.
- The add-on surface now exposes the two new options and the shell entrypoint exports them into the runtime environment.
- Docs were updated to include the new parameters, the environment reference, and the parameter count bump to 23.

## Concerns

- Verification was limited to the targeted config test file from the brief; broader repo-wide test coverage was not run in this task.
- The new options are configuration-only. Task 2 still needs to implement the runtime/proxy scheduler behavior that consumes them.
