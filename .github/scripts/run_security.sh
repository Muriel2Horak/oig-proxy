#!/usr/bin/env bash
# Security scan - runs all security checks for project

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEFAULT_PYTHON_BIN="python3"
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"

mkdir -p "${REPORT_DIR}"

echo "==========================================="
echo "SECURITY SCAN FOR OIG PROXY"
echo "==========================================="
echo ""

# 1. Bandit (Python SAST)
echo "1/8 Running Bandit (Python security scan)..."
if "${PYTHON_BIN}" -m bandit --version >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m bandit -r "${ROOT_DIR}/addon/oig-proxy" -f json -o "${REPORT_DIR}/bandit.json" --exit-zero
  echo "   ✅ Bandit complete - report: ${REPORT_DIR}/bandit.json"
else
  echo "   ⚠️  Bandit not found (install with: pip install -r requirements-dev.txt)"
fi

# 2. Safety (Dependency vulnerability scan)
echo ""
echo "2/8 Running Safety (Python dependencies security check)..."
if "${PYTHON_BIN}" -m safety --version >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m safety check -r "${ROOT_DIR}/addon/oig-proxy/requirements.txt" --json --output "${REPORT_DIR}/safety.json" || true
  echo "   ✅ Safety complete - report: ${REPORT_DIR}/safety.json"
else
  echo "   ⚠️  Safety not found (install with: pip install safety)"
fi

# 3. Gitleaks (Secret leak detection)
echo ""
echo "3/8 Running Gitleaks (secret leak detection)..."
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --source "${ROOT_DIR}" --report-path "${REPORT_DIR}/gitleaks.json" --report-format json --exit-code 0 || true
  echo "   ✅ Gitleaks complete - report: ${REPORT_DIR}/gitleaks.json"
else
  echo "   ⚠️  Gitleaks not found (install with: brew install gitleaks)"
fi

# 4. Semgrep (Advanced SAST)
echo ""
echo "4/8 Running Semgrep (advanced SAST)..."
if command -v semgrep >/dev/null 2>&1; then
  semgrep --config=auto --json --output "${REPORT_DIR}/semgrep.json" "${ROOT_DIR}/addon/oig-proxy" || true
  echo "   ✅ Semgrep complete - report: ${REPORT_DIR}/semgrep.json"
else
  echo "   ⚠️  Semgrep not found (install with: brew install semgrep)"
fi

# 5. Trivy (Container and dependency scanning)
echo ""
echo "5/8 Running Trivy (container and dependency scanning)..."
if command -v trivy >/dev/null 2>&1; then
  # Scan dependencies
  trivy filesystem --quiet --security-checks vuln,license --format json --output "${REPORT_DIR}/trivy.json" "${ROOT_DIR}/addon/oig-proxy" || true
  echo "   ✅ Trivy complete - report: ${REPORT_DIR}/trivy.json"
else
  echo "   ⚠️  Trivy not found (install with: brew install trivy)"
fi

# 6. Custom security tests (unit tests)
echo ""
echo "6/8 Running custom security tests (unit)..."
if "${PYTHON_BIN}" -m pytest tests/test_security.py -v --junitxml="${REPORT_DIR}/security-junit.xml"; then
  echo "   ✅ Custom security tests passed - report: ${REPORT_DIR}/security-junit.xml"
else
  echo "   ❌ Custom security tests failed"
  exit 1
fi

# 7. Penetration tests (simulation)
echo ""
echo "7/8 Running penetration tests (simulation)..."
if "${PYTHON_BIN}" -m pytest tests/test_penetration.py -v --junitxml="${REPORT_DIR}/penetration-junit.xml"; then
  echo "   ✅ Penetration tests passed - report: ${REPORT_DIR}/penetration-junit.xml"
else
  echo "   ❌ Penetration tests failed"
  exit 1
fi

# 8. Nikto (Port scanning - optional, only if CI/CD enabled)
echo ""
echo "8/8 Running Nikto (port and vulnerability scanning)..."
if [[ "${RUN_NIKTO:-0}" == "1" ]] && command -v nikto >/dev/null 2>&1; then
  # Nikto scan if Control API is running (skip in CI by default)
  nikto -host localhost -port ${CONTROL_API_PORT:-8080} -Format json -output "${REPORT_DIR}/nikto.json" 2>&1 || true
  echo "   ✅ Nikto complete - report: ${REPORT_DIR}/nikto.json"
else
  echo "   ⏭️  Nikto skipped (set RUN_NIKTO=1 to enable)"
fi

echo ""
echo "==========================================="
echo "SECURITY SCAN COMPLETE"
echo "==========================================="
echo ""
echo "Reports:"
echo "  - Bandit:         ${REPORT_DIR}/bandit.json"
echo "  - Safety:          ${REPORT_DIR}/safety.json"
echo "  - Gitleaks:        ${REPORT_DIR}/gitleaks.json"
echo "  - Semgrep:         ${REPORT_DIR}/semgrep.json"
echo "  - Trivy:           ${REPORT_DIR}/trivy.json"
echo "  - Security tests:  ${REPORT_DIR}/security-junit.xml"
echo "  - Penetration tests: ${REPORT_DIR}/penetration-junit.xml"
echo "  - Nikto:           ${REPORT_DIR}/nikto.json"
echo ""

# 1. Bandit (Python SAST)
echo "1/4 Running Bandit (Python security scan)..."
if "${PYTHON_BIN}" -m bandit --version >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m bandit -r "${ROOT_DIR}/addon/oig-proxy" -f json -o "${REPORT_DIR}/bandit.json" --exit-zero
  echo "   ✅ Bandit complete - report: ${REPORT_DIR}/bandit.json"
else
  echo "   ⚠️  Bandit not found (install with: pip install -r requirements-dev.txt)"
fi

# 2. Safety (Dependency vulnerability scan)
echo ""
echo "2/4 Running Safety (Python dependencies security check)..."
if "${PYTHON_BIN}" -m safety --version >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m safety check -r "${ROOT_DIR}/addon/oig-proxy/requirements.txt" --json --output "${REPORT_DIR}/safety.json" || true
  echo "   ✅ Safety complete - report: ${REPORT_DIR}/safety.json"
else
  echo "   ⚠️  Safety not found (install with: pip install safety)"
fi

# 3. Gitleaks (Secret leak detection)
echo ""
echo "3/4 Running Gitleaks (secret leak detection)..."
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --source "${ROOT_DIR}" --report-path "${REPORT_DIR}/gitleaks.json" --report-format json --exit-code 0 || true
  echo "   ✅ Gitleaks complete - report: ${REPORT_DIR}/gitleaks.json"
else
  echo "   ⚠️  Gitleaks not found (install with: brew install gitleaks)"
fi

# 4. Custom security tests
echo ""
echo "4/4 Running custom security tests..."
if "${PYTHON_BIN}" -m pytest tests/test_security.py -v --junitxml="${REPORT_DIR}/security-junit.xml"; then
  echo "   ✅ Custom security tests passed - report: ${REPORT_DIR}/security-junit.xml"
else
  echo "   ❌ Custom security tests failed"
  exit 1
fi

echo ""
echo "==========================================="
echo "SECURITY SCAN COMPLETE"
echo "==========================================="
echo ""
echo "Reports:"
echo "  - Bandit:      ${REPORT_DIR}/bandit.json"
echo "  - Safety:      ${REPORT_DIR}/safety.json"
echo "  - Gitleaks:    ${REPORT_DIR}/gitleaks.json"
echo "  - Unit tests:  ${REPORT_DIR}/security-junit.xml"
echo ""
