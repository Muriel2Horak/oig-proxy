# GitHub Actions workflows

All workflows are evidence-producing and fail closed. They do not deploy, enable local
control, or contact live BOX, MQTT, telemetry, or cloud services.

## `ci.yml`

Runs on every push and pull request with Python 3.11. It executes
`.github/scripts/run_tests.sh`, mypy, Flake8, and Hadolint. The test script runs all of
`tests/v2` with `--cov-branch`, then independently verifies both coverage dimensions:

```bash
python ci/check_coverage.py reports/coverage.xml \
  --minimum 80.0 --output reports/coverage-gate.json
```

The `test-coverage-evidence` artifact contains `junit.xml`, `coverage.xml`,
`coverage-gate.json`, `egress-guard.json`, the SI traceability report, and
`local-setting-transaction-hardening-owasp.md`.

## `pylint.yml`

Runs on every push and pull request. It scans `addon/oig-proxy` and `tests/v2`; any
Pylint error blocks the workflow. The `pylint-evidence` artifact contains
`pylint.json`.

## `security-scan.yml`

Runs on pull requests to `main`/`develop`, pushes to those branches, the daily schedule,
and manual dispatch. `.github/scripts/run_security.sh` blocks on:

- any Bandit medium/high finding;
- any repository `.semgrep.yml` finding or scanner error;
- any Gitleaks secret;
- any Safety dependency vulnerability;
- absent scanner or OWASP evidence.

The `security-evidence` artifact contains `bandit.json`, `semgrep.json`,
`gitleaks.json`, `safety.json`, and
`local-setting-transaction-hardening-owasp.md`.

## Local reproduction

```bash
./ci/ci.sh
```

This is the supported local reproduction for unit, integration, loopback E2E, coverage,
type, lint, and security gates. It requires a project `.venv` and Gitleaks. Reports are
written to ignored `reports/` files. A missing/empty required report, non-zero command,
coverage at or below 80.0 percent, unresolved finding, or unexpected non-loopback egress
keeps the pull request blocked.
