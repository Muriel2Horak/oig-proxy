# Security testing

OIG Proxy 2.2 uses five blocking security evidence layers: Bandit, repository-configured
Semgrep, Gitleaks, Safety, and executable local-setting security/OWASP contracts. A
release requires zero unresolved vulnerabilities, secrets, scanner errors, or contract
failures.

## Reproduce the scan

Run all four scanners with the checked-in policy:

```bash
.github/scripts/run_security.sh
```

The script is equivalent to these release commands:

```bash
python -m bandit -r addon/oig-proxy -x addon/oig-proxy/tests \
  -ll -f json -o reports/bandit.json

semgrep --config .semgrep.yml --error \
  --json --output reports/semgrep.json addon/oig-proxy

gitleaks detect --source . --config .gitleaks.toml \
  --no-banner --redact --report-format json \
  --report-path reports/gitleaks.json

python -m safety check -r addon/oig-proxy/requirements.txt \
  --output bare --save-json reports/safety.json --no-prompt
```

Bandit uses `-ll`, so every medium/high severity finding is blocking. Semgrep uses only
the reviewed repository `.semgrep.yml` and treats findings and scanner errors as
failures. Gitleaks reports are redacted and must contain zero secrets. Safety must
report zero unresolved dependency vulnerabilities.

Run the fifth layer with the hermetic test suite:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json python -m pytest tests/v2 \
  --cov=addon/oig-proxy --cov-branch --cov-report=xml:reports/coverage.xml \
  --cov-fail-under=80.01
python ci/check_coverage.py reports/coverage.xml \
  --minimum 80.0 --output reports/coverage-gate.json
```

`tests/v2/test_local_setting_security.py` and the SI traceability nodes cover strict
UTF-8/JSON/XML handling, Decimal allowlists and bounds, parameterized SQLite, exact
device/session binding, retained-message rejection, replay evidence, size/deadline
limits, logging redaction, safe defaults, and fail-closed write paths. The egress guard
requires numeric loopback transports and zero unexpected violations.

## OWASP evidence

The checked release records seven resolved risk rows in
`docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-owasp.md`:

- input injection;
- authorization and device binding;
- replay;
- resource exhaustion;
- sensitive logging;
- insecure defaults;
- fail-open paths.

Each PASS row cites production symbols plus unit, integration, E2E, and scanner
evidence. A row may be marked `RISK_ACCEPTED` only with a repository-owner decision that
names the finding, scope, expiry, and exact pull-request review URL.

## Evidence artifacts

The blocking workflows upload:

- `junit.xml`, `coverage.xml`, `coverage-gate.json`, and `egress-guard.json`;
- `pylint.json`;
- `bandit.json`, `semgrep.json`, `gitleaks.json`, and `safety.json`;
- the SI traceability matrix and
  `local-setting-transaction-hardening-owasp.md`.

These tests use fake endpoints on local loopback. Manual BOX traffic, live MQTT,
Home Assistant deployment, production cloud validation, control enablement, and active
device commands are explicitly outside this release verification.
