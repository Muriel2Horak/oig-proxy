#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
SEMGREP_BIN="${SEMGREP_BIN:-${ROOT_DIR}/.venv/bin/semgrep}"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports}"

cd "${ROOT_DIR}"
mkdir -p "${REPORT_DIR}"

"${PYTHON_BIN}" -m bandit -r addon/oig-proxy \
  -x addon/oig-proxy/tests \
  -ll -f json -o "${REPORT_DIR}/bandit.json"

"${SEMGREP_BIN}" --config .semgrep.yml --error \
  --json --output "${REPORT_DIR}/semgrep.json" addon/oig-proxy

gitleaks detect --source . --config .gitleaks.toml \
  --no-banner --redact \
  --report-format json --report-path "${REPORT_DIR}/gitleaks.json"

"${PYTHON_BIN}" -m safety check \
  -r addon/oig-proxy/requirements.txt \
  --output bare \
  --save-json "${REPORT_DIR}/safety.json" \
  --no-prompt

for report in bandit.json semgrep.json gitleaks.json safety.json; do
  test -s "${REPORT_DIR}/${report}"
done

"${PYTHON_BIN}" - "${REPORT_DIR}" <<'PY'
import json
from pathlib import Path
import sys

report_dir = Path(sys.argv[1])
bandit = json.loads((report_dir / "bandit.json").read_text(encoding="utf-8"))
semgrep = json.loads((report_dir / "semgrep.json").read_text(encoding="utf-8"))
gitleaks = json.loads((report_dir / "gitleaks.json").read_text(encoding="utf-8"))
safety = json.loads((report_dir / "safety.json").read_text(encoding="utf-8"))

failures = {
    "bandit": bandit.get("results", []),
    "semgrep_results": semgrep.get("results", []),
    "semgrep_errors": semgrep.get("errors", []),
    "gitleaks": gitleaks,
    "safety": safety.get("vulnerabilities", []),
}
nonempty = {name: value for name, value in failures.items() if value}
if nonempty:
    raise SystemExit(f"security findings remain: {', '.join(sorted(nonempty))}")
if safety.get("report_meta", {}).get("vulnerabilities_found") != 0:
    raise SystemExit("Safety report does not prove zero vulnerabilities")
PY
