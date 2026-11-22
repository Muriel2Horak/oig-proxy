# Changed-functions quality check (P0b, oig-proxy step)

Status: ACCEPTED. Parent design: agent-ops
`docs/superpowers/specs/2026-11-21-code-quality-prevention-design.md` (operator decisions 2026-11-20/21).

- Local SonarQube (`local:oig-proxy`) stays non-blocking: measurement and burn-down backlog only.
- A fast local check that mirrors active Sonar rules BLOCKS a PR, but only for Python functions the PR
  adds or modifies. Untouched functions are never checked.
- Semantics = ratchet (operator form 2026-11-21): a NEW function must pass Q1-Q4; a MODIFIED function
  must not get worse (complexity and parameter count may not rise above the merge-base value, no new
  Q2/Q4 finding inside it).
- Rules (profile "Sonar agentic AI", thresholds equal Sonar's, never stricter):
  - Q1 python:S3776 cognitive complexity <= 15
  - Q2 python:S3358 nested conditional expression
  - Q3 python:S107 parameters <= 13
  - Q4 python:S1172 / S1481 / S1854 unused parameter, unused local, dead store
- Tool: `scripts/quality/changed-functions-check`, a vendored copy of the agent-ops tool (Python
  standard library only). File scope is read from this repo's `.sonar/local.properties`, so the check
  sees exactly the files Sonar analyzes as main code.

<!-- fleet-spec: 2026-11-22-oig-proxy-changed-functions-check -->

### REQ 1 — Changed-functions check tool for oig-proxy

Acceptance: `scripts/quality/changed-functions-check --base <rev>` checks every added or modified
Python function in the diff against the merge-base with ratchet semantics; file scope comes from
`.sonar/local.properties`; exit 0 clean, exit 1 with one `path:line function rule old->new` line per
finding, exit 2 on usage or git error.

```fleet-req
id: 2026-11-22-oig-proxy-changed-functions-check-REQ-1
priority: P1
acceptance:
  ### integration
  - name: tool-tests
    cmd: python3 -m pytest -q tests/v2/test_changed_functions_check.py
    expect: 0
```

### REQ 2 — S3776 port matches Sonar on oig-proxy

Acceptance: the tool's cognitive complexity equals the value SonarQube reported for oig-proxy at the
P0 baseline (analysis `cd805b34`), pinned by a committed fixture of Sonar's per-function S3776
values; no function Sonar flags above 15 scores 15 or less locally.

```fleet-req
id: 2026-11-22-oig-proxy-changed-functions-check-REQ-2
priority: P1
acceptance:
  ### integration
  - name: sonar-conformance
    cmd: python3 -m pytest -q tests/v2/test_changed_functions_sonar_conformance.py
    expect: 0
```

### REQ 3 — Check blocks in CI and is named in repo rules

Acceptance: the CI workflow runs the check against the PR base on pull requests, and `AGENTS.md`
names the command so author, fix and review slices cite its output.

```fleet-req
id: 2026-11-22-oig-proxy-changed-functions-check-REQ-3
priority: P1
acceptance:
  ### integration
  - name: ci-step
    cmd: grep -q 'scripts/quality/changed-functions-check' .github/workflows/ci.yml
    expect: 0
  - name: repo-rules-name-it
    cmd: grep -q 'scripts/quality/changed-functions-check' AGENTS.md
    expect: 0
```
