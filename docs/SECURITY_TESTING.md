# Security Testing for OIG Proxy

The current tests exercise MQTT command admission, wire qualification, parsing, and state recovery. Run them from the repository root with Python 3.11 and the packages in [requirements-dev.txt](../requirements-dev.txt). Use a local environment or isolated test broker with synthetic data.

## Runtime behavior

```bash
PYTHONPATH=addon/oig-proxy python -m pytest tests/v2
```

For a focused security check, run these suites. The links identify the assertions and boundaries each suite covers.

| Boundary | Test modules |
| --- | --- |
| Fresh MQTT deliveries and the control target and value | [MQTT client](../tests/v2/test_mqtt/test_client.py), [command handler](../tests/v2/test_twin_handler.py) |
| Native profile enrollment, same-session ACKs, replay, and timeouts | [Profile qualification](../tests/v2/test_wire_profile_qualification.py), [command delivery](../tests/v2/test_twin_delivery.py), [integration](../tests/v2/test_integration.py) |
| Frame validity and recovery of historical state | [Parser](../tests/v2/test_protocol/test_parser.py), [proxy server](../tests/v2/test_proxy/test_server.py), [confirmed state store](../tests/v2/test_confirmed_store.py) |

For a broker access check, configure authenticated test accounts and an isolated broker. Verify the proxy can publish state and discovery and that a read-only client cannot publish to either control path. Use a separate publisher with explicit control permissions to verify that retained messages cannot become commands and that fresh, authorized test commands reach the correct device simulator. A successful MQTT publish alone does not prove that a Box accepted a command. [MQTT security and topic paths](../SECURITY.md#local-mqtt-control)

## Static analysis and candidate images

Run these commands with Bandit, Semgrep, Gitleaks, and Trivy installed. Run the image scan separately for each verified architecture archive. Replace the example file name with the saved candidate archive.

```bash
mkdir -p reports
python -m bandit -r addon/oig-proxy -x addon/oig-proxy/tests -f json -o reports/bandit.json
semgrep scan --config=p/default --metrics=off --disable-version-check --error addon/oig-proxy
gitleaks git --config .gitleaks.toml --redact=100 --report-format=json \
  --report-path=reports/gitleaks.json --exit-code=1 .
trivy image --input candidate-amd64.tar.gz --scanners vuln,secret --exit-code 1
```

[Security Scan](../.github/workflows/security-scan.yml) resolves the production dependencies and checks them with Safety and Trivy. It also runs Bandit, Semgrep and Gitleaks, records license data and uploads reports. Follow that workflow's commands and versions when reproducing a CI result. [CI](../.github/workflows/ci.yml) records the V2 test results, enforces statement coverage above 80%, and runs mypy, hadolint, Bandit and SonarCloud. [Pylint](../.github/workflows/pylint.yml) runs separately.

Keep scanner reports and raw evidence private until reviewed for sensitive data. Report reproducible vulnerabilities via the [security policy](../SECURITY.md#reporting-a-vulnerability).
