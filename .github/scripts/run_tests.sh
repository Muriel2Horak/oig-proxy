#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"

cd "${ROOT_DIR}"
mkdir -p "${REPORT_DIR}"
export PYTHONPATH="${ROOT_DIR}/addon/oig-proxy${PYTHONPATH:+:${PYTHONPATH}}"

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
