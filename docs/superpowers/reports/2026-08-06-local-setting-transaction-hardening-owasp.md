# Local-setting transaction hardening: OWASP verification

## Scope and decision

- Release: OIG Proxy `2.2.0`
- Evidence checkout: `3eda48fab72b8539257163fcb18399bf2975a478`
- Verification window: `2026-08-07T18:07:34Z` through `2026-08-07T18:09:16Z`
- Boundary: repository code plus hermetic numeric-loopback fake endpoints
- Excluded: live BOX commands, production cloud/MQTT/telemetry, Home Assistant
  deployment, control enablement, and active network probing
- Result: seven PASS rows; no risk acceptance and no unresolved finding

## Reproducible gate evidence

Full test and exclusive coverage gate, completed at `2026-08-07T18:08:00Z`:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest tests/v2 \
  --junitxml=reports/junit.xml \
  --cov=addon/oig-proxy \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:reports/coverage.xml \
  --cov-fail-under=80.01

.venv/bin/python ci/check_coverage.py reports/coverage.xml \
  --minimum 80.0 --output reports/coverage-gate.json
```

Result: `1422 passed`; statement coverage `89.6732%` (`7711/8599`), branch
coverage `80.5930%` (`2392/2968`), with both percentages strictly greater than
`80.0`.

MNP/smoke E2E, completed at `2026-08-07T18:09:16Z`:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest \
  tests/v2/e2e/test_local_setting_transaction.py::test_e2e_online_cloud_priority_then_local_batch \
  tests/v2/e2e/test_local_setting_transaction.py::test_e2e_restart_retries_with_stable_identity \
  -m e2e -vv
```

Result: `2 passed`; egress status `pass`, five self-probes `pass`,
`blocked_violation_count=0`, and eight allowed numeric-loopback attempts.

Type, lint, and security gates completed by `2026-08-07T18:08:35Z`:

```bash
MYPYPATH=addon/oig-proxy .venv/bin/python -m mypy addon/oig-proxy --ignore-missing-imports
.venv/bin/python -m flake8 addon/oig-proxy tests/v2
PYTHONPATH=addon/oig-proxy .venv/bin/python -m pylint addon/oig-proxy tests/v2 --output-format=json
.github/scripts/run_security.sh
git diff --check
git diff --check origin/main...
```

The repository CI temporarily moves the hyphenated package root's `__init__.py` while
running mypy, then restores it with an EXIT trap. Result: 41 source files type-clean;
zero Flake8 or Pylint findings; zero Bandit medium/high findings or scanner errors;
zero Semgrep findings/errors; zero Gitleaks secrets; zero Safety vulnerabilities; both
diff checks clean.

## Artifact integrity

| Artifact | SHA-256 |
|---|---|
| `reports/junit.xml` | `5a8bfc82a2dd782f601942bb4d294212248f41938d3f525fbd70330309ee0f20` |
| `reports/coverage.xml` | `564b267ba8518399a1ab475bbe78fd79a1b25c658ed3e8ddad2a7e20d9b00f3a` |
| `reports/coverage-gate.json` | `9c47a3e0963540bf2e6c475f1076a8a3af2a97d8a2c70bb9b69a3c5dd8616ee5` |
| `reports/egress-guard.json` | `cbe77e8d7463d54c9fafc539d3e8b9f4bdcdd96ec441094f643046e6cc45a592` |
| `reports/pylint.json` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| `reports/bandit.json` | `c6a29aaaafc95c7adecce41130dae720448ce829a3cccd89cd8502d829606e13` |
| `reports/semgrep.json` | `9bbc1b0986b4024967faa0d21155662a60e6d664c6a40b1ce8f59aa8ddbe5acf` |
| `reports/gitleaks.json` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| `reports/safety.json` | `8c3ea64b3455320c3677ae9fa0e1e5b0c5ba35c982b18d6c82abbce489e61cd4` |

## OWASP risk review

| Risk class | Implemented control | Source symbols | Unit evidence | Integration evidence | E2E evidence | Scanner evidence | Result |
|---|---|---|---|---|---|---|---|
| Input injection | Decimal allowlist/range/step/finite validation, strict UTF-8/JSON, XML 1.0 escaping, parameterized SQLite | `settings_constraints.py`, `twin/handler.py`, `protocol/frames.py`, `twin/store.py` | `test_settings_constraints.py`, `test_local_setting_security.py` | `test_twin_handler.py` | `test_e2e_retained_control_never_enters_local_batch` | Bandit, Semgrep | PASS |
| Authorization and device binding | Exact learned device topic, CRC-valid connection binding, UUID session CAS | `twin/handler.py`, `proxy/server.py`, `twin/store.py` | SI-3 and SI-14 unit nodes | SI-3 and SI-14 integration nodes | SI-3 and SI-14 E2E nodes | Semgrep | PASS |
| Replay | Retained MQTT rejection, ACK fingerprint dedupe, durable event receipts, replay-file removal | `twin/handler.py`, `twin/ack_parser.py`, `twin/store.py`, `proxy/server.py` | SI-9 and SI-13 unit nodes | SI-9 and SI-13 integration nodes | SI-9 and SI-13 E2E nodes | Gitleaks, Semgrep | PASS |
| Resource exhaustion | 16 KiB ingress cap, 1 MiB stream/held caps, one active attempt per device, bounded retries/deadlines | `twin/handler.py`, `protocol/frame.py`, `proxy/dialog.py`, `twin/store.py` | stream and attempt boundary nodes | `test_setting_streams.py` | partial/coalesced and retry-limit E2E nodes | Bandit | PASS |
| Sensitive logging | Bounded/redacted ingress and errors, no INFO raw payload, redacted secret-scan output | `twin/handler.py`, `telemetry/settings_audit.py` | `test_ingress_logs_do_not_include_raw_payload_at_info` | settings-audit contract nodes | audit-identity E2E node | Gitleaks | PASS |
| Insecure defaults | Control disabled, no unknown-device subscription, maximum eight attempts, FULL/WAL/lock, exact discovery tombstones | `config.py`, `main.py`, `mqtt/client.py`, `twin/store.py` | SI-8, SI-12, and SI-14 unit nodes | corresponding integration nodes | corresponding E2E nodes | Bandit, Safety | PASS |
| Fail-open paths | Durable prepare before write; store/CRC/session/write failures prohibit local mutation and preserve transparent unowned cloud flow | `twin/delivery.py`, `proxy/writer.py`, `proxy/server.py`, `main.py` | write/store failure nodes | online/offline failure nodes | writer/restart/disabled E2E nodes | all scanner artifacts | PASS |

The seven rows map primarily to OWASP A01 Broken Access Control, A03 Injection, A04
Insecure Design, A05 Security Misconfiguration, A06 Vulnerable and Outdated Components,
A08 Software and Data Integrity Failures, and A09 Security Logging and Monitoring
Failures. The SI matrix supplies exact test nodes for every abbreviated SI reference.
