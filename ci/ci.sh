#!/usr/bin/env bash
# Local CI - runs same checks as GitHub CI locally

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEFAULT_PYTHON_BIN="python3"
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"
COVERAGE_FAIL_UNDER="${COVERAGE_FAIL_UNDER:-69}"

# Check if running on GitHub Actions
if [[ -n "${GITHUB_ACTIONS+x}" ]]; then
  RUNNING_ON_GITHUB=1
else
  RUNNING_ON_GITHUB=0
fi

# ==============================================================================
# Parse command line arguments
# ==============================================================================

RUNNING_ON_GITHUB="${RUNNING_ON_GITHUB:-0}"

RUN_TESTS="${RUN_TESTS:-1}"
RUN_SECURITY="${RUN_SECURITY:-1}"
RUN_LINT="${RUN_LINT:-1}"
RUN_SONAR="${RUN_SONAR:-0}"

# Parse flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --no-tests)
      RUN_TESTS=0
      shift
      ;;
    --no-security)
      RUN_SECURITY=0
      shift
      ;;
    --no-lint)
      RUN_LINT=0
      shift
      ;;
    --sonar)
      RUN_SONAR=1
      shift
      ;;
    --all)
      RUN_TESTS=1
      RUN_SECURITY=1
      RUN_LINT=1
      RUN_SONAR=1
      shift
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--no-tests] [--no-security] [--no-lint] [--sonar] [--all]"
      exit 1
      ;;
  esac
done

mkdir -p "${REPORT_DIR}"

echo "==========================================="
echo "LOCAL CI FOR OIG PROXY"
echo "==========================================="
echo ""
echo "Python: $(${PYTHON_BIN})"
echo "Report directory: ${REPORT_DIR}"
echo ""
echo "Flags:"
echo "  Tests:     ${RUN_TESTS}"
echo "  Security:   ${RUN_SECURITY}"
echo "  Lint:      ${RUN_LINT}"
echo "  Sonar:     ${RUN_SONAR}"
echo ""

# ==============================================================================
# STEP 1: Setup
# ==============================================================================

echo "📦 1/6 Setting up environment..."
echo ""

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "❌ Python not found: ${PYTHON_BIN}"
  exit 1
fi

# Check if venv exists
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  echo "✅ Using venv: ${ROOT_DIR}/.venv/bin/python"
else
  echo "⚠️  Venv not found, using system Python"
fi

# Install/update dependencies
echo "Installing dependencies..."
"${PYTHON_BIN}" -m pip install --upgrade pip --quiet
"${PYTHON_BIN}" -m pip install -r "${ROOT_DIR}/requirements-dev.txt" --quiet
echo "✅ Dependencies installed"
echo ""

# ==============================================================================
# STEP 2: Lint (Pylint)
# ==============================================================================

if [[ "${RUN_LINT}" == "1" ]]; then
  echo "🔍 2/6 Running Pylint (linting)..."
  echo ""

  if ! "${PYTHON_BIN}" -m pylint --version >/dev/null 2>&1; then
    echo "⚠️  Pylint not found, skipping..."
  else
    if PYTHONPATH="${ROOT_DIR}/addon/oig-proxy" "${PYTHON_BIN}" -m pylint tests/v2/*.py \
      --disable=import-outside-toplevel,unused-import,reimported,redefined-outer-name \
      --disable=line-too-long,f-string-without-interpolation,comparison-of-constants,comparison-with-itself,unused-argument,wrong-import-order \
      --output-format=json --output="${REPORT_DIR}/pylint.json"; then
      echo "✅ Pylint passed"
    else
      echo "⚠️  Pylint found issues (report: ${REPORT_DIR}/pylint.json)"
    fi
  fi
  echo ""
else
  echo "⏭️  Skipping Pylint (--no-lint)"
  echo ""
fi

# ==============================================================================
# STEP 3: Unit Tests
# ==============================================================================

if [[ "${RUN_TESTS}" == "1" ]]; then
  echo "🧪 3/6 Running Unit Tests..."
  echo ""

  if ! "${PYTHON_BIN}" -m pytest --version >/dev/null 2>&1; then
    echo "❌ pytest not found"
    exit 1
  fi

  export PYTHONPATH="${ROOT_DIR}/addon/oig-proxy"

  if "${PYTHON_BIN}" -m pytest \
    --junitxml="${REPORT_DIR}/junit.xml" \
    --cov=addon/oig-proxy \
    --cov-report=xml:"${REPORT_DIR}/coverage.xml" \
    --cov-report=term \
    --cov-fail-under="${COVERAGE_FAIL_UNDER}" \
    -v; then
    echo "✅ Tests passed"
  else
    echo "❌ Tests failed"
    exit 1
  fi
  echo ""
else
  echo "⏭️  Skipping Unit Tests (--no-tests)"
  echo ""
fi

# ==============================================================================
# STEP 4: Security Scan
# ==============================================================================

if [[ "${RUN_SECURITY}" == "1" ]]; then
  echo "🔒 4/6 Running Security Scan..."
  echo ""

  # Bandit
  echo "  → Bandit (Python SAST)..."
  if "${PYTHON_BIN}" -m bandit --version >/dev/null 2>&1; then
    "${PYTHON_BIN}" -m bandit -r "${ROOT_DIR}/addon/oig-proxy" -f json -o "${REPORT_DIR}/bandit.json" --exit-zero
    echo "    ✅ Bandit complete"
  else
    echo "    ⚠️  Bandit not found"
  fi

  # Safety
  echo "  → Safety (Dependency vulnerabilities)..."
  if "${PYTHON_BIN}" -m safety --version >/dev/null 2>&1; then
    "${PYTHON_BIN}" -m safety check -r "${ROOT_DIR}/addon/oig-proxy/requirements.txt" --json --output "${REPORT_DIR}/safety.json" || true
    echo "    ✅ Safety complete"
  else
    echo "    ⚠️  Safety not found"
  fi

  # Semgrep
  echo "  → Semgrep (Advanced SAST)..."
  if command -v semgrep >/dev/null 2>&1; then
    semgrep --config=auto --json --output "${REPORT_DIR}/semgrep.json" "${ROOT_DIR}/addon/oig-proxy" || true
    echo "    ✅ Semgrep complete"
  else
    echo "    ⚠️  Semgrep not found"
  fi

  # Gitleaks
  echo "  → Gitleaks (Secret leak detection)..."
  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks detect --source "${ROOT_DIR}" --report-path "${REPORT_DIR}/gitleaks.json" --report-format json --exit-code 0 || true
    echo "    ✅ Gitleaks complete"
  else
    echo "    ⚠️  Gitleaks not found"
  fi

  # Trivy
  echo "  → Trivy (Container/dependency scanning)..."
  if command -v trivy >/dev/null 2>&1; then
    trivy filesystem --quiet --security-checks vuln,license --format json --output "${REPORT_DIR}/trivy.json" "${ROOT_DIR}/addon/oig-proxy" || true
    echo "    ✅ Trivy complete"
  else
    echo "    ⚠️  Trivy not found"
  fi

  echo ""
  echo "✅ Security scan complete"
  echo ""
else
  echo "⏭️  Skipping Security Scan (--no-security)"
  echo ""
fi

# ==============================================================================
# STEP 5: MyPy (Type checking)
# ==============================================================================

echo "🔡 5/6 Running MyPy (type checking)..."
echo ""

if ! "${PYTHON_BIN}" -m mypy --version >/dev/null 2>&1; then
  echo "⚠️  MyPy not found, skipping..."
else
  mv "${ROOT_DIR}/addon/oig-proxy/__init__.py" "${ROOT_DIR}/addon/oig-proxy/__init__.py.bak"
  if ! MYPYPATH="${ROOT_DIR}/addon/oig-proxy" "${PYTHON_BIN}" -m mypy addon/oig-proxy --ignore-missing-imports --no-error-summary; then
    mv "${ROOT_DIR}/addon/oig-proxy/__init__.py.bak" "${ROOT_DIR}/addon/oig-proxy/__init__.py"
    echo "⚠️  MyPy found type errors"
  else
    mv "${ROOT_DIR}/addon/oig-proxy/__init__.py.bak" "${ROOT_DIR}/addon/oig-proxy/__init__.py"
    echo "✅ MyPy passed"
  fi
fi
echo ""

# ==============================================================================
# STEP 6: SonarQube (optional)
# ==============================================================================

if [[ "${RUN_SONAR}" == "1" ]]; then
  echo "📊 6/6 Running SonarQube scan..."
  echo ""

  if [[ ! -f "${REPORT_DIR}/coverage.xml" ]]; then
    echo "❌ Coverage report not found. Run with --tests first."
    exit 1
  fi

  # Skip Sonar on GitHub Actions (use separate security-scan.yml workflow)
  if [[ "${RUNNING_ON_GITHUB}" == "1" ]]; then
    echo "⏭️  Skipping SonarQube on GitHub Actions (use Security Scan workflow)"
  else
    "${ROOT_DIR}/.github/scripts/run_sonar.sh"
  fi
  echo ""
else
  echo "⏭️  Skipping SonarQube (use --sonar to enable)"
  echo ""
fi

# ==============================================================================
# SUMMARY
# ==============================================================================

echo "==========================================="
echo "LOCAL CI COMPLETE"
echo "==========================================="
echo ""
echo "Reports generated:"
if [[ "${RUN_LINT}" == "1" ]]; then
  echo "  - Pylint:     ${REPORT_DIR}/pylint.json"
fi
if [[ "${RUN_TESTS}" == "1" ]]; then
  echo "  - Tests:       ${REPORT_DIR}/junit.xml"
  echo "  - Coverage:    ${REPORT_DIR}/coverage.xml"
fi
if [[ "${RUN_SECURITY}" == "1" ]]; then
  echo "  - Bandit:      ${REPORT_DIR}/bandit.json"
  echo "  - Safety:      ${REPORT_DIR}/safety.json"
  echo "  - Semgrep:     ${REPORT_DIR}/semgrep.json"
  echo "  - Gitleaks:    ${REPORT_DIR}/gitleaks.json"
  echo "  - Trivy:       ${REPORT_DIR}/trivy.json"
fi
echo ""
echo "💡 Tips:"
echo "  - Run './ci/ci.sh --all' for full CI + Sonar"
echo "  - Run './ci/ci.sh --no-tests' to skip tests"
echo "  - Run './ci/ci.sh --no-security' to skip security"
echo "  - Run './ci/ci.sh --no-lint' to skip linting"
echo ""
