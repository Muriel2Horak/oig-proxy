#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Missing ${PYTHON_BIN}. Install Python 3 and retry." >&2
  exit 2
fi

export PYTHONPATH="${ROOT_DIR}/addon/oig-proxy:${PYTHONPATH:-}"

mkdir -p "${REPORT_DIR}"

"${PYTHON_BIN}" -m pytest \
  --junitxml="${REPORT_DIR}/junit.xml" \
  --cov="${ROOT_DIR}/addon/oig-proxy" \
  --cov-report=xml:"${REPORT_DIR}/coverage.xml"

if [[ "${RUN_INTEGRATION:-0}" == "1" ]]; then
  echo "Running integration scripts..." >&2
  for script in testing/test_online_mode.sh testing/test_replay_mode.sh; do
    if [[ -x "${ROOT_DIR}/${script}" ]]; then
      "${ROOT_DIR}/${script}"
    else
      echo "Skipping ${script} (missing or not executable)" >&2
    fi
  done
fi
