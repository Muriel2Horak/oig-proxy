# GitHub Actions Workflows

This directory contains CI/CD workflows for the OIG Proxy project.

## Workflows

### 1. Security Scan (`security-scan.yml`)

**Triggers:**
- Push to `main` or `develop` branches
- Pull requests to `main` or `develop` branches
- Daily schedule at 2 AM UTC
- Manual workflow dispatch

**Steps:**
1. Checkout code
2. Set up Python 3.13
3. Install Python dependencies
4. Install security tools (Semgrep, Trivy, Gitleaks)
5. Run Bandit (Python SAST)
6. Run Safety (Dependency vulnerabilities)
7. Run Semgrep (Advanced SAST)
8. Run Trivy (Container/dependency scanning)
9. Run Gitleaks (Secret leak detection)
10. Run security unit tests
11. Run penetration tests
12. Upload reports as artifacts
13. Comment on PR with security results

**Artifacts:**
- `bandit-report`: Python SAST scan results
- `safety-report`: Dependency vulnerability scan results
- `semgrep-report`: Advanced SAST scan results
- `trivy-report`: Container/dependency scan results
- `gitleaks-report`: Secret leak detection results
- `security-tests-report`: Unit security test results
- `penetration-tests-report`: Penetration test results

## Running Workflows

### Manual Trigger

You can manually trigger the security scan workflow from GitHub Actions UI:

1. Go to: Actions → Security Scan
2. Click "Run workflow"
3. Select branch and click "Run workflow button"

### Local Testing

To test workflows locally before pushing:

```bash
# Run security scan locally
./.github/scripts/run_security.sh

# Run unit security tests
.venv/bin/python -m pytest tests/test_security.py -v

# Run penetration tests
.venv/bin/python -m pytest tests/test_penetration.py -v
```

## Security Scan Results

Security scan results are:
1. Uploaded as artifacts (available for 90 days)
2. Posted as comments on PRs
3. Viewable in the Actions tab

### Interpretation

- **Bandit**: Check for Python security issues (HIGH/MEDIUM/LOW severity)
- **Safety**: Check for dependency vulnerabilities (CVEs)
- **Semgrep**: Check for advanced SAST issues (custom rules)
- **Trivy**: Check for container/dependency vulnerabilities
- **Gitleaks**: Check for hardcoded secrets
- **Security tests**: Verify security properties (hash length, input validation)
- **Penetration tests**: Simulate attack vectors

## Troubleshooting

### Workflow Fails

If a workflow fails:

1. Check the workflow logs in Actions tab
2. Identify which step failed
3. Check security reports in artifacts
4. Fix the issue and push new commit

### Security Tests Fail

If security unit tests fail:

```bash
# Run locally with verbose output
.venv/bin/python -m pytest tests/test_security.py -v --tb=short

# Run specific test
.venv/bin/python -m pytest tests/test_security.py::TestTelemetrySecurity::test_instance_hash_length_is_32_chars -v
```

### Penetration Tests Fail

If penetration tests fail:

```bash
# Run locally with verbose output
.venv/bin/python -m pytest tests/test_penetration.py -v --tb=short
```

## Security Best Practices

### 1. Review Security Scan Results

- Check for HIGH/MEDIUM severity issues
- Review new dependency vulnerabilities
- Check for hardcoded secrets

### 2. Address Security Issues

- Fix HIGH severity issues immediately
- Address MEDIUM severity issues promptly
- Document LOW severity issues for later review

### 3. Keep Dependencies Updated

- Regularly update dependencies
- Run `pip install -r requirements-dev.txt --upgrade`
- Test after updates

### 4. Review PRs

- Security scan results are posted as comments
- Review results before merging
- Address any security concerns

## References

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Bandit Documentation](https://bandit.readthedocs.io/)
- [Safety Documentation](https://github.com/pyupio/safety)
- [Semgrep Documentation](https://semgrep.dev/docs/)
- [Trivy Documentation](https://aquasecurity.github.io/trivy/)
- [Gitleaks Documentation](https://github.com/gitleaks/gitleaks)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
