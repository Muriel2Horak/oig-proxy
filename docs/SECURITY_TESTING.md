# Security Testing for OIG Proxy

This document describes the security testing framework for OIG Proxy, including static analysis, penetration testing, and manual security guidelines.

## Overview

The project includes multiple layers of security testing:

1. **Static Analysis (SAST)** - Automated scanning of code for security vulnerabilities
2. **Unit Security Tests** - Python tests for security properties
3. **Penetration Tests** - Simulation of attack vectors
4. **Secret Detection** - Detection of hardcoded secrets
5. **Dependency Scanning** - Checking for known vulnerabilities in dependencies
6. **Container Scanning** - Scanning Docker images for vulnerabilities
7. **Advanced SAST** - Custom Semgrep rules for project-specific security issues

## Running Security Tests

### Run All Security Tests Locally

```bash
# Run complete security scan
./.github/scripts/run_security.sh
```

This script will:
- Run Bandit (Python SAST)
- Run Safety (dependency vulnerability check)
- Run Gitleaks (secret leak detection)
- Run Semgrep (advanced SAST)
- Run Trivy (container and dependency scanning)
- Run unit security tests
- Run penetration tests

### Individual Security Tests

#### Bandit (Python SAST)

```bash
.venv/bin/python -m bandit -r addon/oig-proxy -f json -o reports/bandit.json
```

Reports saved to: `reports/bandit.json`

#### Safety (Dependency Vulnerabilities)

```bash
.venv/bin/python -m safety check -r addon/oig-proxy/requirements.txt --json --output reports/safety.json
```

Reports saved to: `reports/safety.json`

#### Gitleaks (Secret Detection)

```bash
gitleaks detect --source . --report-path reports/gitleaks.json --report-format json
```

Reports saved to: `reports/gitleaks.json`

#### Semgrep (Advanced SAST)

```bash
semgrep --config=auto --json --output reports/semgrep.json addon/oig-proxy
```

Reports saved to: `reports/semgrep.json`

#### Trivy (Container and Dependency Scanning)

```bash
# Scan dependencies
trivy filesystem --quiet --security-checks vuln,license --format json --output reports/trivy.json addon/oig-proxy

# Scan Docker image (optional)
trivy image ghcr.io/muriel2horak/oig-proxy:latest
```

Reports saved to: `reports/trivy.json`

#### Nikto (Port and Vulnerability Scanning)

```bash
# Nikto scan if Control API is running (optional, set RUN_NIKTO=1)
RUN_NIKTO=1 ./.github/scripts/run_security.sh
```

Reports saved to: `reports/nikto.json`

#### Unit Security Tests

```bash
.venv/bin/python -m pytest tests/test_security.py -v
```

#### Penetration Tests

```bash
.venv/bin/python -m pytest tests/test_penetration.py -v
```

### Continuous Integration

Security tests run automatically:
- **Bandit**: Integrated in `.github/scripts/run_sonar.sh`
- **All security tools**: Integrated in `.github/workflows/security-scan.yml`
- **Unit security tests**: Integrated in CI pipeline
- **Penetration tests**: Integrated in CI pipeline
- **Automatic PR comments**: Security scan results posted to PRs

## Security Test Coverage

### Unit Security Tests (`tests/test_security.py`)

**Telemetry Security:**
- ✅ Instance hash length (32 chars)
- ✅ Instance hash is hexadecimal
- ✅ Instance hash is deterministic
- ✅ Instance hash entropy (128 bits)
- ✅ Timestamp includes timezone

**Control API Security:**
- ✅ Minimal input validation
- ✅ JSON input validation

**Session Management:**
- ✅ Cloud session uses locks
- ✅ Cloud session has stats tracking
- ✅ Cloud session handles disconnects gracefully

**Input Validation:**
- ✅ Parser handles XML injection
- ✅ Parser ignores unknown fields
- ✅ Parser converts values safely

**Secrets Management:**
- ✅ No hardcoded passwords
- ✅ No hardcoded tokens

**Replay Protection:**
- ✅ Telemetry timestamp includes timezone
- ✅ Telemetry buffer limits messages
- ✅ Telemetry buffer has TTL

**Encryption and Hashing:**
- ✅ SHA-256 used for instance hash
- ✅ Hash truncation is secure (128 bits)

**Network Security:**
- ✅ Control API listens on localhost by default
- ✅ Proxy listens on all interfaces (LAN binding)
- ✅ Cloud timeout is reasonable

### Penetration Tests (`tests/test_penetration.py`)

**Control API Penetration:**
- SQL injection (tbl_name, tbl_item, new_value)
- XSS in new_value
- Command injection in new_value
- XML injection in body
- Path traversal in tbl_name
- LDAP injection in tbl_item
- Buffer overflow in new_value
- Unicode attack in parameters
- Special characters in parameters
- JSON nesting attack (DoS)
- Duplicate parameters

**Telemetry Penetration:**
- Instance hash collision attack
- Telemetry replay attack
- Telemetry spam attack
- Telemetry manipulation attack

**Session Management Penetration:**
- Session hijacking simulation
- Session fixation simulation
- Session timeout bypass simulation

**Network Penetration:**
- DNS rebinding simulation
- Spoofed device ID simulation
- Man-in-the-middle simulation

**Input Validation Penetration:**
- Null byte injection
- Format string attack
- Integer overflow simulation
- Negative number injection
- Float injection

**Rate Limiting Penetration:**
- Brute force password simulation
- DoS by many requests simulation
- Slowloris attack simulation

## Static Analysis Tools

### Bandit

Bandit is a tool designed to find common security issues in Python code.

**What it checks:**
- Use of assert statements
- Use of exec/eval
- Hardcoded passwords
- SQL injection risks
- Shell injection risks
- Hardcoded temp directories
- Insecure random number generation

**Configuration:** `.github/scripts/run_sonar.sh` (lines 101-120)

**Severity Levels:**
- HIGH: Critical vulnerabilities
- MEDIUM: Potentially exploitable
- LOW: Security best practices

### Safety

Safety scans Python dependencies for known security vulnerabilities.

**What it checks:**
- CVEs in installed packages
- Known security advisories
- Outdated versions with security fixes

**Configuration:** `requirements-dev.txt` (added `safety`)

### Gitleaks

Gitleaks scans code for hardcoded secrets.

**What it detects:**
- API keys
- AWS access keys
- GitHub tokens
- Slack tokens
- Database connection strings
- Private keys
- JWT tokens
- Passwords

**Configuration:** `.gitleaks.toml`

**Allowlist:**
- Comments with quotes
- Localhost references
- Test values
- Empty/placeholder values
- Environment variable references
- Log messages

### Semgrep

Semgrep is a fast, open-source static analysis tool for finding bugs and security issues.

**What it checks:**
- Custom project-specific rules (`.semgrep.yml`)
- OWASP Top 10 patterns
- Code injection risks
- Authentication issues

**Configuration:** `.semgrep.yml`

**Custom Rules:**
- Control API input validation
- Telemetry instance hash entropy
- Hardcoded secrets
- SQL injection risks
- eval/exec risks
- XML parsing XXE
- Weak random number generation
- Temp file security
- Pickle risks
- Shell injection risks
- API handler authentication

### Trivy

Trivy scans for vulnerabilities in container images, dependencies, and file systems.

**What it checks:**
- OS package vulnerabilities
- Application dependencies vulnerabilities
- License compliance
- Configuration issues

**Configuration:** `.github/workflows/security-scan.yml`

**Supported Security Checks:**
- `vuln` - Known vulnerabilities
- `license` - License compliance

### Nikto

Nikto is a web server scanner that checks for outdated software, configuration issues, and known vulnerabilities.

**What it checks:**
- Server version disclosure
- Common web vulnerabilities
- Configuration issues
- Default credentials
- Directory traversal

**Configuration:** `.github/scripts/run_security.sh` (requires `RUN_NIKTO=1`)

**Note:** Nikto is optional and only runs when Control API is available.

## Security Best Practices

### 1. Instance Hash

- **Length**: 32 characters (128 bits of entropy)
- **Algorithm**: SHA-256 truncated to 32 chars
- **Collision resistance**: Infeasible to brute-force

### 2. Telemetry

- **Timestamp**: Includes UTC timezone (Z suffix)
- **Buffer limit**: Max 1000 messages
- **Buffer TTL**: Max 24 hours
- **Replay protection**: Timestamp verification recommended

### 3. Control API

- **Default**: Listen on localhost only (127.0.0.1)
- **Input validation**: Minimal validation currently
- **Future**: Add authentication (optional)

### 4. Session Management

- **Cloud session**: Uses locks for thread safety
- **Stats tracking**: Connects, disconnects, errors, timeouts
- **Graceful handling**: Disconnects handled gracefully

### 5. Parser

- **XML injection**: Handles malicious XML gracefully
- **Unknown fields**: Currently includes them (should be addressed)
- **Value conversion**: Safe conversion to int/float

## Security Checklist

- [x] Instance hash is 32 characters (128 bits)
- [x] No hardcoded passwords in code
- [x] No hardcoded tokens in code
- [x] Telemetry timestamp includes timezone
- [x] Telemetry buffer has limits
- [x] Control API listens on localhost by default
- [x] Cloud session uses locks
- [x] Parser handles XML injection
- [x] Bandit scanning integrated
- [x] Safety scanning integrated
- [x] Gitleaks scanning configured
- [x] Unit security tests implemented
- [x] Penetration tests implemented
- [x] Semgrep scanning integrated
- [x] Trivy scanning integrated
- [x] CI/CD security workflow created
- [ ] Input sanitization for Control API (TODO)
- [ ] HMAC signing for telemetry (TODO)
- [ ] Rate limiting implementation (TODO)
- [ ] IP whitelist for Control API (TODO)
- [ ] Timestamp TTL verification (TODO)
- [ ] Control API authentication (optional TODO)

## Manual Security Testing

### 1. Control API Testing

```bash
# Test Control API health
curl http://127.0.0.1:8080/api/health

# Test Control API setting (requires Control API port configured)
curl -X POST http://127.0.0.1:8080/api/setting \
  -H "Content-Type: application/json" \
  -d '{"tbl_name": "tbl_box_prms", "tbl_item": "MODE", "new_value": "0"}'
```

### 2. Telemetry Testing

```bash
# Test telemetry endpoint (if exposed)
# This is typically internal to the add-on
```

### 3. Network Testing

```bash
# Check open ports
netstat -an | grep 5710

# Check if Control API is listening
netstat -an | grep 8080
```

### 4. OWASP ZAP Testing (Manual)

```bash
# Scan Control API with ZAP
zap-cli quick-scan -r http://127.0.0.1:8080/api/

# Generate security report
zap-cli report -o zap-report.html
```

## CI/CD Integration

### GitHub Actions

The `.github/workflows/security-scan.yml` workflow runs:

**Triggers:**
- Push to main/develop branches
- Pull requests to main/develop branches
- Daily schedule at 2 AM UTC
- Manual workflow dispatch

**Steps:**
1. Checkout code
2. Set up Python 3.13
3. Install dependencies
4. Install security tools
5. Run all security scans
6. Run unit security tests
7. Run penetration tests
8. Upload reports as artifacts
9. Comment PR with security results

**Artifacts:**
- Bandit report
- Safety report
- Semgrep report
- Trivy report
- Gitleaks report
- Security tests report
- Penetration tests report

## Reporting Security Issues

If you find a security vulnerability, please report it responsibly:

1. Do not create a public issue
2. Email security contact (if available)
3. Include detailed description and reproduction steps
4. Wait for confirmation before public disclosure

## References

- [OWASP Python Security](https://owasp.org/www-project-python-security/)
- [Bandit Documentation](https://bandit.readthedocs.io/)
- [Safety Documentation](https://github.com/pyupio/safety)
- [Gitleaks Documentation](https://github.com/gitleaks/gitleaks)
- [Semgrep Documentation](https://semgrep.dev/docs/)
- [Trivy Documentation](https://aquasecurity.github.io/trivy/)
- [Nikto Documentation](https://cirt.net/nikto2-docs/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
