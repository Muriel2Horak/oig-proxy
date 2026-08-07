#!/usr/bin/env bash
# Reproduce the blocking GitHub gates locally.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"
RUN_TESTS=1
RUN_SECURITY=1
RUN_LINT=1
RUN_SONAR=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tests) RUN_TESTS=0 ;;
    --no-security) RUN_SECURITY=0 ;;
    --no-lint) RUN_LINT=0 ;;
    --sonar) RUN_SONAR=1 ;;
    --all) RUN_TESTS=1; RUN_SECURITY=1; RUN_LINT=1; RUN_SONAR=1 ;;
    *)
      echo "Usage: $0 [--no-tests] [--no-security] [--no-lint] [--sonar] [--all]" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing Python environment: ${PYTHON_BIN}" >&2
  exit 2
fi

cd "${ROOT_DIR}"
mkdir -p "${REPORT_DIR}"
export PYTHONPATH="${ROOT_DIR}/addon/oig-proxy${PYTHONPATH:+:${PYTHONPATH}}"

if [[ "${RUN_TESTS}" == 1 ]]; then
  LOCAL_CONTROL_EGRESS_REPORT="${REPORT_DIR}/egress-guard.json" \
    "${PYTHON_BIN}" -m pytest tests/v2 \
      --junitxml="${REPORT_DIR}/junit.xml" \
      --cov=addon/oig-proxy \
      --cov-branch \
      --cov-report=term-missing \
      --cov-report="xml:${REPORT_DIR}/coverage.xml" \
      --cov-fail-under=80.01
  "${PYTHON_BIN}" ci/check_coverage.py "${REPORT_DIR}/coverage.xml" \
    --minimum 80.0 \
    --output "${REPORT_DIR}/coverage-gate.json"
fi

if [[ "${RUN_LINT}" == 1 ]]; then
  INIT_PATH="${ROOT_DIR}/addon/oig-proxy/__init__.py"
  INIT_BACKUP="${INIT_PATH}.mypy-backup"
  restore_init() {
    if [[ -f "${INIT_BACKUP}" ]]; then
      mv "${INIT_BACKUP}" "${INIT_PATH}"
    fi
  }
  trap restore_init EXIT
  mv "${INIT_PATH}" "${INIT_BACKUP}"
  set +e
  MYPYPATH="${ROOT_DIR}/addon/oig-proxy" \
    "${PYTHON_BIN}" -m mypy addon/oig-proxy --ignore-missing-imports
  MYPY_STATUS=$?
  set -e
  restore_init
  trap - EXIT
  if [[ "${MYPY_STATUS}" -ne 0 ]]; then
    exit "${MYPY_STATUS}"
  fi

  "${PYTHON_BIN}" -m flake8 addon/oig-proxy tests/v2
  "${PYTHON_BIN}" -m pylint addon/oig-proxy tests/v2 \
    --output-format=json > "${REPORT_DIR}/pylint.json"
fi

if [[ "${RUN_SECURITY}" == 1 ]]; then
  "${ROOT_DIR}/.github/scripts/run_security.sh"
fi

if [[ "${RUN_SONAR}" == 1 ]]; then
  test -s "${REPORT_DIR}/coverage.xml"
  "${ROOT_DIR}/.github/scripts/run_sonar.sh"
fi

echo "Blocking local CI gates passed. Reports: ${REPORT_DIR}"
