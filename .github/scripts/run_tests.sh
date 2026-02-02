#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"
DEFAULT_PYTHON_BIN="python3"
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"
COVERAGE_FAIL_UNDER="${COVERAGE_FAIL_UNDER:-80}"
COVERAGE_RCFILE="${COVERAGE_RCFILE:-${ROOT_DIR}/.coveragerc}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Missing ${PYTHON_BIN}. Install Python 3 and retry." >&2
  exit 2
fi

export PYTHONPATH="${ROOT_DIR}/addon/oig-proxy:${PYTHONPATH:-}"
if [[ -d "${ROOT_DIR}/.venv/bin" ]]; then
  export PATH="${ROOT_DIR}/.venv/bin:${PATH}"
fi
export COVERAGE_PROCESS_START="${COVERAGE_RCFILE}"
export COVERAGE_FILE="${REPORT_DIR}/.coverage"

mkdir -p "${REPORT_DIR}"

if ! "${PYTHON_BIN}" -m coverage --version >/dev/null 2>&1; then
  echo "Missing coverage. Install dev deps: pip install -r requirements-dev.txt" >&2
  exit 2
fi

rm -f "${REPORT_DIR}/.coverage"*

"${PYTHON_BIN}" -m coverage run -m pytest \
  --junitxml="${REPORT_DIR}/junit.xml"

if [[ "${RUN_INTEGRATION:-0}" == "1" ]]; then
  echo "Running integration scripts..." >&2
  for script in testing/test_online_mode.sh testing/test_replay_mode.sh; do
    if [[ -x "${ROOT_DIR}/${script}" ]]; then
      PYTHON_BIN="${PYTHON_BIN}" "${ROOT_DIR}/${script}"
    else
      echo "Skipping ${script} (missing or not executable)" >&2
    fi
  done
fi

"${PYTHON_BIN}" -m coverage combine
"${PYTHON_BIN}" -m coverage xml -o "${REPORT_DIR}/coverage.xml"
report_args=(--show-missing --skip-covered)
if [[ -n "${COVERAGE_FAIL_UNDER}" ]]; then
  report_args+=(--fail-under="${COVERAGE_FAIL_UNDER}")
fi
"${PYTHON_BIN}" -m coverage report "${report_args[@]}"
