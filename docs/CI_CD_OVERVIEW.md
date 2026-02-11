# CI/CD Overview for OIG Proxy

This document describes all CI/CD workflows running in GitHub Actions and how to run them locally.

## GitHub CI/CD Workflows

### 1. Main CI Workflow (`.github/workflows/ci.yml`)

**Triggers:**
- Push to any branch
- Pull requests

**What it runs:**
1. Checkout code
2. Setup Python 3.11
3. Install dependencies (from `requirements-dev.txt`)
4. Run unit tests with coverage
5. Run security scan (Bandit)

**Steps:**
```yaml
- Checkout code
- Setup Python 3.11
- Install dependencies
- Run pytest with coverage
- Run bandit (Python SAST)
```

**Generated Reports:**
- `reports/junit.xml` - Test results
- `reports/coverage.xml` - Coverage report

---

### 2. Pylint Workflow (`.github/workflows/pylint.yml`)

**Triggers:**
- Push to any branch
- Pull requests

**What it runs:**
1. Checkout code
2. Setup Python 3.11
3. Install dependencies
4. Run Pylint (code quality linting)

**Steps:**
```yaml
- Checkout code
- Setup Python 3.11
- Install dependencies
- Run pylint addon/oig-proxy/*.py tests/*.py
```

**Generated Reports:**
- Console output with Pylint results

---

### 3. Security Scan Workflow (`.github/workflows/security-scan.yml`)

**Triggers:**
- Push to `main` or `develop` branches
- Pull requests to `main` or `develop`
- Daily schedule at 2 AM UTC
- Manual workflow dispatch

**What it runs:**
1. Checkout code
2. Setup Python 3.13
3. Install dependencies
4. Install security tools (Semgrep, Trivy, Gitleaks)
5. Run **Bandit** (Python SAST)
6. Run **Safety** (Dependency vulnerabilities)
7. Run **Semgrep** (Advanced SAST with custom rules)
8. Run **Trivy** (Container/dependency scanning)
9. Run **Gitleaks** (Secret leak detection)
10. Run security unit tests
11. Run penetration tests
12. Upload reports as artifacts
13. Comment on PR with security results

**Steps:**
```yaml
- Checkout code
- Setup Python 3.13
- Install dependencies
- Install security tools
- Run bandit
- Run safety
- Run semgrep
- Run trivy
- Run gitleaks
- Run security unit tests
- Run penetration tests
- Upload artifacts
- Comment on PR
```

**Generated Reports (artifacts, 90-day retention):**
- `bandit-report` - Python SAST scan results
- `safety-report` - Dependency vulnerability scan results
- `semgrep-report` - Advanced SAST scan results
- `trivy-report` - Container/dependency scan results
- `gitleaks-report` - Secret leak detection results
- `security-tests-report` - Unit security test results
- `penetration-tests-report` - Penetration test results

---

### 4. SonarQube Scan (`.github/scripts/run_sonar.sh`)

**Triggers:**
- Manual run
- Can be integrated into CI pipeline

**What it runs:**
1. Check for Sonar credentials (from `.env`)
2. Run unit tests (if `RUN_TESTS=1`)
3. Run security scan (if `RUN_SECURITY=1`)
4. Run SonarQube scanner with Docker
5. Upload coverage and security reports

**Steps:**
```bash
- Check .env for SONAR_TOKEN
- Run unit tests
- Run bandit
- Normalize report paths
- Run SonarQube scanner (Docker)
```

**Generated Reports:**
- Uploaded to SonarQube server
- Requires SonarQube server running (local or cloud)

---

## Comparison: GitHub CI vs Local CI

| Feature | GitHub CI | Local CI (`ci.sh`) |
|---------|-----------|---------------------|
| **Setup** | Automatic on push/PR | Manual (`./.github/scripts/ci.sh`) |
| **Python version** | 3.11/3.13 | System Python or venv |
| **Dependencies** | Auto-installed | Auto-installed |
| **Unit tests** | ✅ | ✅ (default, skip with `--no-tests`) |
| **Coverage** | ✅ | ✅ |
| **Pylint** | ✅ (separate workflow) | ✅ (default, skip with `--no-lint`) |
| **MyPy** | ❌ | ✅ |
| **Bandit** | ✅ (in CI) | ✅ (in security) |
| **Safety** | ✅ (in security) | ✅ (in security) |
| **Semgrep** | ✅ (in security) | ✅ (in security) |
| **Trivy** | ✅ (in security) | ✅ (in security) |
| **Gitleaks** | ✅ (in security) | ✅ (in security) |
| **Security tests** | ✅ (in security) | ✅ (in security) |
| **Penetration tests** | ✅ (in security) | ✅ (in security) |
| **SonarQube** | ❌ (manual) | ✅ (with `--sonar`) |
| **PR comments** | ✅ | ❌ |
| **Artifacts** | ✅ | ✅ (local files) |
| **Parallel execution** | ✅ (matrix) | ❌ (sequential) |

---

## Running CI Locally

### Prerequisites

1. Install system dependencies:
   ```bash
   # Python 3.11+
   python3 --version
   
   # Docker (for SonarQube)
   docker --version
   ```

2. Create virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install Python dependencies:
   ```bash
   pip install -r requirements-dev.txt
   ```

4. Install security tools (optional):
   ```bash
   # Semgrep
   pip install semgrep
   
   # Trivy
   brew install trivy  # macOS
   # or: apt-get install trivy  # Linux
   
   # Gitleaks
   brew install gitleaks  # macOS
   # or: https://gitleaks.io  # Linux
   ```

### Local CI Script

**Full CI (same as GitHub CI):**
```bash
./.github/scripts/ci.sh
```

This runs:
1. ✅ Pylint (linting)
2. ✅ Unit tests with coverage
3. ✅ Security scan (Bandit, Safety, Semgrep, Trivy, Gitleaks)
4. ✅ Security unit tests
5. ✅ Penetration tests
6. ✅ MyPy (type checking)

**Full CI + SonarQube:**
```bash
./.github/scripts/ci.sh --all
```

This runs everything above + SonarQube scan.

**Skip specific steps:**

Skip tests:
```bash
./.github/scripts/ci.sh --no-tests
```

Skip security:
```bash
./.github/scripts/ci.sh --no-security
```

Skip linting:
```bash
./.github/scripts/ci.sh --no-lint
```

Run Sonar only:
```bash
./.github/scripts/ci.sh --sonar
```

**Custom combinations:**

Run only tests + linting (no security):
```bash
./.github/scripts/ci.sh --no-security
```

Run only security (no tests):
```bash
./.github/scripts/ci.sh --no-tests --no-lint
```

### Running Security Scan Only

**Full security scan:**
```bash
./.github/scripts/run_security.sh
```

This runs:
1. ✅ Bandit (Python SAST)
2. ✅ Safety (Dependency vulnerabilities)
3. ✅ Semgrep (Advanced SAST)
4. ✅ Trivy (Container/dependency scanning)
5. ✅ Gitleaks (Secret leak detection)
6. ✅ Security unit tests
7. ✅ Penetration tests

### Running SonarQube Locally

**Prerequisites:**
1. SonarQube server running (local or cloud)
2. `.env` file configured with Sonar credentials

**Run Sonar scan:**
```bash
# From project root
.venv/bin/python .github/scripts/run_sonar.sh
```

**Environment variables (`.env`):**
```bash
# Required
SONAR_TOKEN=your_sonar_token
SONAR_HOST_URL=http://localhost:9001
SONAR_PROJECT_KEY=oig_proxy
SONAR_PROJECT_NAME=oig_proxy

# Optional (for SonarCloud)
SONAR_ORGANIZATION=your-org
SONAR_CLOUD_TOKEN=your_cloud_token

# Optional (for PR analysis)
SONAR_PR_KEY=123
SONAR_PR_BRANCH=feature-branch
SONAR_PR_BASE=main

# Optional (quality gate)
SONAR_CONFIGURE_QG=1
SONAR_QUALITY_GATE_WAIT=true
SONAR_QUALITY_GATE_TIMEOUT=300
SONAR_QUALITY_GATE_NAME="Security A +0"
```

**Run Sonar with custom Python:**
```bash
PYTHON_BIN=python3.11 .venv/bin/python .github/scripts/run_sonar.sh
```

**Run Sonar with custom report directory:**
```bash
REPORT_DIR=/path/to/reports .venv/bin/python .github/scripts/run_sonar.sh
```

### Running Individual Tools

**Run unit tests only:**
```bash
.venv/bin/python -m pytest tests/ -v --cov=addon/oig-proxy --cov-report=term
```

**Run specific test:**
```bash
.venv/bin/python -m pytest tests/test_telemetry_client.py -v
```

**Run Pylint only:**
```bash
.venv/bin/python -m pylint addon/oig-proxy/*.py tests/*.py
```

**Run MyPy only:**
```bash
.venv/bin/python -m mypy addon/oig-proxy/*.py
```

**Run Bandit only:**
```bash
.venv/bin/python -m bandit -r addon/oig-proxy -f json -o reports/bandit.json
```

**Run Safety only:**
```bash
.venv/bin/python -m safety check -r addon/oig-proxy/requirements.txt
```

**Run Semgrep only:**
```bash
semgrep --config=auto addon/oig-proxy
```

**Run Trivy only:**
```bash
trivy filesystem --security-checks vuln,license addon/oig-proxy
```

**Run Gitleaks only:**
```bash
gitleaks detect --source .
```

## CI Artifacts and Reports

### Local Reports Location

All reports are generated in `reports/` directory:

```
reports/
├── bandit.json              # Bandit Python SAST results
├── safety.json              # Safety dependency vulnerabilities
├── semgrep.json             # Semgrep advanced SAST results
├── trivy.json              # Trivy container/dependency scan results
├── gitleaks.json            # Gitleaks secret detection results
├── pylint.json              # Pylint linting results
├── junit.xml                # Test results (JUnit format)
├── coverage.xml             # Coverage report (Cobertura format)
├── security-junit.xml       # Security unit tests results
└── penetration-junit.xml    # Penetration tests results
```

### GitHub Artifacts

All reports are uploaded as GitHub Actions artifacts (available for 90 days):

To download artifacts:
1. Go to: Actions → Select workflow run → Scroll to "Artifacts"
2. Click on artifact name to download

### SonarQube Dashboard

SonarQube results are uploaded to SonarQube server:

To view results:
1. Open SonarQube UI: `http://localhost:9001` (or your SonarCloud URL)
2. Navigate to project: `oig_proxy`
3. View metrics:
   - Code smells
   - Bugs
   - Vulnerabilities
   - Security hotspots
   - Coverage
   - Duplicated lines

## CI/CD Workflow Matrix

### GitHub CI Parallelism

GitHub CI runs workflows in parallel:

```
Push/PR → CI (tests + bandit)
        → Pylint (linting)
        → Security Scan (all security tools)

Daily Schedule → Security Scan (all security tools)
```

### Local CI Sequential

Local CI runs steps sequentially (faster for development):

```
./.github/scripts/ci.sh → Setup → Lint → Tests → Security → Sonar
```

## Troubleshooting

### CI Script Fails

**Problem:** `./.github/scripts/ci.sh` fails

**Solutions:**
1. Check Python version: `python3 --version` (need 3.11+)
2. Check venv: `ls .venv/bin/python`
3. Install dependencies: `pip install -r requirements-dev.txt`
4. Run with verbose output: `bash -x ./.github/scripts/ci.sh`

### Security Tools Missing

**Problem:** Security scan skips tools

**Solutions:**
```bash
# Install Semgrep
pip install semgrep

# Install Trivy (macOS)
brew install trivy

# Install Trivy (Linux)
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee -a /etc/apt/sources.list.d/trivy.list
sudo apt-get update
sudo apt-get install trivy

# Install Gitleaks (macOS)
brew install gitleaks

# Install Gitleaks (Linux)
wget https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_8.21.0_linux_x64.tar.gz
tar -xzf gitleaks_8.21.0_linux_x64.tar.gz
sudo mv gitleaks /usr/local/bin/
```

### SonarQube Connection Fails

**Problem:** Sonar scan fails with connection error

**Solutions:**
1. Check SonarQube is running: `docker ps | grep sonar`
2. Check SonarQube URL in `.env`
3. Check Sonar token is valid
4. Check firewall/network access to SonarQube server

### Tests Fail Locally

**Problem:** Tests fail locally but pass in CI

**Solutions:**
1. Check Python version matches CI (3.11)
2. Clean venv: `rm -rf .venv && python3 -m venv .venv`
3. Reinstall dependencies: `pip install -r requirements-dev.txt`
4. Check environment variables: `env | grep PYTHONPATH`

## CI/CD Best Practices

### Before Commit

```bash
# Run full CI locally
./.github/scripts/ci.sh

# If all pass, commit and push
git add .
git commit -m "..."
git push
```

### Before PR

```bash
# Run full CI locally + Sonar
./.github/scripts/ci.sh --all

# If all pass, create PR
gh pr create --title "..." --body "..."
```

### Daily Security Scan

GitHub Actions runs security scan daily at 2 AM UTC. Results are:
- Posted as artifacts (90-day retention)
- Posted as comments on PRs
- Viewable in Actions tab

## References

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [SonarQube Documentation](https://docs.sonarqube.org/)
- [Bandit Documentation](https://bandit.readthedocs.io/)
- [Pylint Documentation](https://pylint.org/)
- [MyPy Documentation](https://mypy.readthedocs.io/)
- [Safety Documentation](https://github.com/pyupio/safety)
- [Semgrep Documentation](https://semgrep.dev/docs/)
- [Trivy Documentation](https://aquasecurity.github.io/trivy/)
- [Gitleaks Documentation](https://github.com/gitleaks/gitleaks)
