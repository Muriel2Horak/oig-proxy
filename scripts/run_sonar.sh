#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set +u
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
  set -u
fi

SONAR_HOST_URL="${SONAR_HOST_URL:-${SONAR_URL:-http://host.docker.internal:9001}}"
SONAR_ORGANIZATION="${SONAR_ORGANIZATION:-}"
SONAR_PR_KEY="${SONAR_PR_KEY:-}"
SONAR_PR_BRANCH="${SONAR_PR_BRANCH:-}"
SONAR_PR_BASE="${SONAR_PR_BASE:-}"
SONAR_IS_CLOUD=0
if [[ "${SONAR_HOST_URL}" == *"sonarcloud.io"* ]]; then
  SONAR_IS_CLOUD=1
fi
SONAR_DOCKER_HOST_URL="${SONAR_HOST_URL}"
if [[ "${SONAR_HOST_URL}" =~ ^(https?://)(localhost|127\.0\.0\.1)(:[0-9]+)?(.*)?$ ]]; then
  SONAR_DOCKER_HOST_URL="${BASH_REMATCH[1]}host.docker.internal${BASH_REMATCH[3]}${BASH_REMATCH[4]}"
fi
SONAR_PROJECT_KEY="${SONAR_PROJECT_KEY:-oig_proxy}"
SONAR_PROJECT_NAME="${SONAR_PROJECT_NAME:-oig_proxy}"
SONAR_PROJECT_VERSION="${SONAR_PROJECT_VERSION:-}"
SONAR_DOCKER_NETWORK="${SONAR_DOCKER_NETWORK:-}"
SONAR_SCANNER_IMAGE="${SONAR_SCANNER_IMAGE:-sonarsource/sonar-scanner-cli:latest}"
SONAR_CACHE_DIR="${SONAR_CACHE_DIR:-${ROOT_DIR}/.sonar_cache}"
DEFAULT_PYTHON_BIN="python3"
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"
RUN_TESTS="${RUN_TESTS:-1}"
RUN_SECURITY="${RUN_SECURITY:-1}"
BANDIT_STRICT="${BANDIT_STRICT:-0}"
SONAR_CONFIGURE_QG="${SONAR_CONFIGURE_QG:-0}"
SONAR_QUALITY_GATE_WAIT="${SONAR_QUALITY_GATE_WAIT:-true}"
SONAR_QUALITY_GATE_TIMEOUT="${SONAR_QUALITY_GATE_TIMEOUT:-300}"
SONAR_DOCKER_PROJECT_ROOT="${SONAR_DOCKER_PROJECT_ROOT:-/usr/src}"

SONAR_LOGIN_VALUE=""
SONAR_PASSWORD_VALUE=""
if [[ "${SONAR_IS_CLOUD}" == "1" && -n "${SONAR_CLOUD_TOKEN:-}" ]]; then
  SONAR_LOGIN_VALUE="${SONAR_CLOUD_TOKEN}"
elif [[ -n "${SONAR_TOKEN:-}" ]]; then
  SONAR_LOGIN_VALUE="${SONAR_TOKEN}"
elif [[ -n "${SONAR_LOGIN:-}" ]]; then
  SONAR_LOGIN_VALUE="${SONAR_LOGIN}"
  SONAR_PASSWORD_VALUE="${SONAR_PASS:-}"
fi

if [[ -z "${SONAR_LOGIN_VALUE}" ]]; then
  echo "Missing Sonar credentials. Set SONAR_TOKEN (preferred) or SONAR_LOGIN/SONAR_PASS in .env and run:" >&2
  echo "  SONAR_TOKEN=... $0" >&2
  echo "" >&2
  echo "Optional env overrides:" >&2
  echo "  SONAR_HOST_URL=http://host.docker.internal:9001" >&2
  echo "  SONAR_URL=http://host.docker.internal:9001" >&2
  echo "  SONAR_ORGANIZATION=your-org (required for SonarCloud)" >&2
  echo "  SONAR_PROJECT_KEY=oig_proxy" >&2
  echo "  SONAR_PROJECT_NAME=oig_proxy" >&2
  echo "  SONAR_PROJECT_VERSION=1.2.3" >&2
  echo "  SONAR_DOCKER_NETWORK=" >&2
  echo "  PYTHON_BIN=python3" >&2
  echo "  COVERAGE_FAIL_UNDER=80" >&2
  echo "  SONAR_CONFIGURE_QG=1" >&2
  echo "  SONAR_QUALITY_GATE_NAME=Security A +0" >&2
  echo "  SONAR_QUALITY_GATE_WAIT=true" >&2
  echo "  SONAR_QUALITY_GATE_TIMEOUT=300" >&2
  echo "  RUN_TESTS=0" >&2
  echo "  RUN_SECURITY=0" >&2
  echo "  BANDIT_STRICT=1" >&2
  echo "  REPORT_DIR=/path/to/reports" >&2
  exit 2
fi

export PYTHON_BIN
export REPORT_DIR
export SONAR_HOST_URL
export SONAR_PROJECT_KEY

if [[ "${SONAR_CONFIGURE_QG}" == "1" ]]; then
  if [[ "${SONAR_IS_CLOUD}" == "1" ]]; then
    echo "Skipping quality gate configuration for SonarCloud." >&2
  else
    "${PYTHON_BIN}" "${ROOT_DIR}/scripts/configure_sonar_quality_gate.py"
  fi
fi

if [[ "${RUN_TESTS}" == "1" ]]; then
  "${ROOT_DIR}/scripts/run_tests.sh"
fi

if [[ "${RUN_SECURITY}" == "1" ]]; then
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Missing ${PYTHON_BIN}. Install Python 3 and retry." >&2
    exit 2
  fi
  if ! "${PYTHON_BIN}" -m bandit --version >/dev/null 2>&1; then
    echo "Missing bandit. Install dev deps: pip install -r requirements-dev.txt" >&2
    exit 2
  fi
  mkdir -p "${REPORT_DIR}"
  bandit_args=(
    -r "${ROOT_DIR}/addon/oig-proxy"
    -f json
    -o "${REPORT_DIR}/bandit.json"
  )
  if [[ "${BANDIT_STRICT}" != "1" ]]; then
    bandit_args+=(--exit-zero)
  fi
  "${PYTHON_BIN}" -m bandit "${bandit_args[@]}"
fi

if [[ ! -f "${REPORT_DIR}/coverage.xml" ]]; then
  echo "Missing coverage report at ${REPORT_DIR}/coverage.xml. Run scripts/run_tests.sh or set RUN_TESTS=1." >&2
  exit 2
fi
if [[ "${RUN_SECURITY}" == "1" && ! -f "${REPORT_DIR}/bandit.json" ]]; then
  echo "Missing Bandit report at ${REPORT_DIR}/bandit.json." >&2
  exit 2
fi

normalize_report_paths() {
  local report_path="$1"
  if [[ -f "${report_path}" ]]; then
    "${PYTHON_BIN}" - "${report_path}" "${ROOT_DIR}" "${SONAR_DOCKER_PROJECT_ROOT}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
from_prefix = sys.argv[2]
to_prefix = sys.argv[3]
data = path.read_text()
if from_prefix in data:
    path.write_text(data.replace(from_prefix, to_prefix))
PY
  fi
}

normalize_report_paths "${REPORT_DIR}/coverage.xml"
normalize_report_paths "${REPORT_DIR}/bandit.json"

mkdir -p "${SONAR_CACHE_DIR}"
docker_args=(
  --rm
  -v "${ROOT_DIR}:/usr/src"
  -v "${SONAR_CACHE_DIR}:/opt/sonar-scanner/.sonar/cache"
  -w /usr/src
)
if [[ -n "${SONAR_DOCKER_NETWORK}" ]]; then
  docker_args+=(--network "${SONAR_DOCKER_NETWORK}")
fi

scan_args=(
  -Dsonar.host.url="${SONAR_DOCKER_HOST_URL}"
  -Dsonar.login="${SONAR_LOGIN_VALUE}"
  -Dsonar.projectKey="${SONAR_PROJECT_KEY}"
  -Dsonar.projectName="${SONAR_PROJECT_NAME}"
  -Dsonar.qualitygate.wait="${SONAR_QUALITY_GATE_WAIT}"
  -Dsonar.qualitygate.timeout="${SONAR_QUALITY_GATE_TIMEOUT}"
)
if [[ -n "${SONAR_ORGANIZATION}" ]]; then
  scan_args+=(-Dsonar.organization="${SONAR_ORGANIZATION}")
fi
if [[ -n "${SONAR_PASSWORD_VALUE}" ]]; then
  scan_args+=(-Dsonar.password="${SONAR_PASSWORD_VALUE}")
fi
if [[ -n "${SONAR_PROJECT_VERSION}" ]]; then
  scan_args+=(-Dsonar.projectVersion="${SONAR_PROJECT_VERSION}")
fi
if [[ -n "${SONAR_PR_KEY}" && -n "${SONAR_PR_BRANCH}" && -n "${SONAR_PR_BASE}" ]]; then
  scan_args+=(
    -Dsonar.pullrequest.key="${SONAR_PR_KEY}"
    -Dsonar.pullrequest.branch="${SONAR_PR_BRANCH}"
    -Dsonar.pullrequest.base="${SONAR_PR_BASE}"
  )
fi

exec docker run "${docker_args[@]}" \
  "${SONAR_SCANNER_IMAGE}" \
  "${scan_args[@]}"
