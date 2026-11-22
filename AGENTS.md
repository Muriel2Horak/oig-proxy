# AGENTS.md — OpenCode + oh-my-opencode instructions

## Environment
- Running on OpenCode server (NAS), behind Authelia.
- SSH aliases available from container: `nas`, `ha`, `github.com`.
- Main repos are mounted under `/repos`.

## Primary workflow
1. Open correct project directory (do not assume default):
   - e.g. `/repos/oig-proxy`, `/repos/core-platform`, etc.
2. Use OpenCode/oh-my-opencode tools first (grep/read/edit/bash/lsp/sonarqube).
3. Verify changes with relevant tests/build before finishing.

## GitHub CLI account policy
Use explicit wrappers (server-global):
- `gh-muriel` → GitHub account `Muriel2Horak`
- `gh-o2`     → GitHub account `martin-horak_o2cz`

### Which one to use
- If repository origin is `Muriel2Horak/*` → use `gh-muriel`.
- If repository belongs to O2 org/account → use `gh-o2`.
- Do **not** use plain `gh` for PR operations unless explicitly required.

## PR / issue operations
- Status: `gh-muriel pr status` (or `gh-o2 ...` for O2 repos)
- Create PR: `gh-muriel pr create --title "..." --body "..."`
- View checks: `gh-muriel pr checks <pr-number>`

## Safety rules
- Never commit secrets (`.env`, tokens, credentials, auth files).
- Prefer existing project conventions and keep changes minimal/surgical.
- If uncertain about target account, inspect remote origin first:
  - `git remote get-url origin`

## Code quality
- `scripts/quality/changed-functions-check --base <rev>` blocks a PR (CI) when a Python function it adds or modifies breaks the Sonar-mirroring rules: cognitive complexity <= 15, nested conditional expression, parameters <= 13, unused parameter/local/dead store. Cite its output in author, fix and review slices.
- Ratchet: a NEW function must pass; a MODIFIED function may not get worse than at the merge-base; untouched functions are never checked. File scope = `.sonar/local.properties` (main code only). Exit 0 clean, 1 findings, 2 usage/git/scope error.

## SonarQube via oh-my-opencode
- Sonar is configured in `oh-my-opencode.json` (server-level).
- Use tool calls such as:
  - `sonarqube({ action: "status" })`
  - `sonarqube({ action: "newissues" })`
  - `sonarqube({ action: "analyze" })`
