# CI/CD release gates

The repository has three blocking GitHub Actions workflows. They reproduce the local
release commands and retain machine-readable evidence. No workflow deploys the add-on,
enables local control, or contacts a production BOX, MQTT broker, or cloud endpoint.

## CI

`.github/workflows/ci.yml` runs on every push and pull request with Python 3.11. The
test step executes the complete unit, integration, and loopback E2E suite:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json python -m pytest tests/v2 \
  --junitxml=reports/junit.xml \
  --cov=addon/oig-proxy \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:reports/coverage.xml \
  --cov-fail-under=80.01

python ci/check_coverage.py reports/coverage.xml \
  --minimum 80.0 \
  --output reports/coverage-gate.json
```

The first command requires aggregate coverage above the threshold. The independent
checker requires both statement and branch coverage to be strictly greater than
`80.0`. The same job blocks on mypy, Flake8, Hadolint, missing traceability/OWASP
documents, or missing test evidence.

The `test-coverage-evidence` artifact contains:

- `junit.xml`
- `coverage.xml`
- `coverage-gate.json`
- `egress-guard.json`
- the SI-1 through SI-15 traceability matrix
- `local-setting-transaction-hardening-owasp.md`

The egress guard permits numeric loopback transports only and fails on any unexpected
connection attempt.

## Pylint

`.github/workflows/pylint.yml` runs on every push and pull request. Any Pylint error is
blocking, and `pylint-evidence` uploads `pylint.json` even when the scan fails.

```bash
PYTHONPATH=addon/oig-proxy python -m pylint addon/oig-proxy tests/v2 \
  --output-format=json > reports/pylint.json
```

## Security scan

`.github/workflows/security-scan.yml` runs for pull requests to `main`/`develop`, pushes
to those branches, the daily schedule, and manual dispatch. It invokes the repository
script and blocks on any unresolved result:

```bash
.github/scripts/run_security.sh
```

The `security-evidence` artifact contains `bandit.json`, `semgrep.json`,
`gitleaks.json`, `safety.json`, and
`local-setting-transaction-hardening-owasp.md`. The exact scanner policy is described
in [`SECURITY_TESTING.md`](SECURITY_TESTING.md).

## Local reproduction

Install `requirements-dev.txt` in `.venv`, install Gitleaks 8.24.2, and run:

```bash
./ci/ci.sh
```

This reproduces test, statement/branch coverage, mypy, Flake8, Pylint, Bandit,
Semgrep, Gitleaks, and Safety gates. `./ci/ci.sh --sonar` additionally runs the optional
Sonar integration; Sonar is not a substitute for any blocking pull-request gate.

Generated reports remain under `reports/` and are not committed. A release is blocked
by a non-zero command, absent/empty required artifact, unresolved scanner finding,
coverage at or below 80.0 percent, or an unexpected egress attempt.
