# Draft: NAS autonomní provoz OIG Proxy

## Requirements (confirmed)
- Uživatel chce vědět, co je potřeba, aby projekt běžel pouze na jejich NAS serveru a byl autonomní.
- Upřesnění: Primární téma je **development prostředí** v OpenCode serveru (kde probíhá vývoj), ale s možností lokálně dělat testy/volání/nasazení bez "instalovat mraky věcí" do venv.
- Cíl: mít opakovatelný a co nejvíc autonomní dev workflow mezi OpenCode prostředím a lokálním NAS deploymentem.
- Lokální vývoj **nepočítá se spuštěným Home Assistantem**.
- Lokální vývoj **nepočítá se spuštěným MQTT brokerem**.
- V lokále se řeší hlavně: **testy, lint, sonar a další quality gates**.
- Existuje **speciální skript** pro nasazení na lokální NAS.
- Uživatel je hlavní operátor/promptuje sám; není nutné optimalizovat onboarding pro širší tým.
- Uživatel nechce duplicitní runtime kontejner vedle toho, co běží v HA.

## Technical Decisions
- Zatím nerozhodnuto: čistě offline vs. offline-first s občasným internetem.
- Rozhodnuto: HA/MQTT nejsou součástí běžného lokálního vývojového běhu.
- Zatím nerozhodnuto: preferovaný model dev prostředí (Docker-first vs. venv-first vs. hybrid).
- Kandidát přístupu: "shared infra + isolated app stacks" (sdílená síť/monitoring, ale OIG proxy běží separátně s minimem couplingu).
- Potvrzeno uživatelem: oficiální local CI entrypoint bude `ci/ci.sh`.
- Potvrzeno uživatelem: odstranit duplicitní security běh v `ci/ci.sh` (security pouze jednou).
- Nové upřesnění: není potřeba brát ohled na více lidských vývojářů; workflow může být optimalizovaný pro solo operátora + OpenCode agenty.
- Nové omezení: vyhnout se modelu "dva různé app kontejnery" (lokální + HA) se stejnou funkcí.

## Research Findings
- Oracle: nejdříve ověřit hard blockery (TLS/cert pinning, cloud-only auth, DNS override v síti).
- Oracle: minimální lokální stack = OIG proxy + lokální DNS override + perzistence + restart/healthcheck.
- Oracle: autonomii zásadně ovlivní chování OIG Boxu při odpojení od vendor cloudu.
- Detekované mountnuté repozitáře v `/repos`: `oig-proxy`, `core-platform`, `majestic-ai`, `licence-server`, `ha-promtail`, `cez-pnd-data`.
- Existující Docker ekosystém: `core-platform` a `majestic-ai` mají rozsáhlé compose stacky (DB, Redis, monitoring, reverse proxy).
- `licence-server` je lehký samostatný compose stack se SQLite a loopback publikací portu.
- `oig-proxy` aktuálně nemá root docker-compose; má add-on Dockerfile (`addon/oig-proxy/Dockerfile`).
- Potenciál sdílení komponent existuje, ale pro autonomní OIG je vhodné minimalizovat runtime závislosti na cizích stackách.
- Lokální CI entrypoint je v repu `ci/ci.sh` (ne `.github/scripts/ci.sh`). Dokumentace v několika místech ukazuje zastaralou cestu.
- `ci/ci.sh` provádí: pip install dev deps, pylint, pytest+coverage, security skeny (bandit/safety/semgrep/gitleaks/trivy), mypy, volitelně Sonar, a **na konci ještě volá** `.github/scripts/run_security.sh`.
- `ci/ci.sh` tím pádem security část běží dvojmo (jednou inline + podruhé přes `run_security.sh`).
- `run_tests.sh` a `run_sonar.sh` preferují `.venv/bin/python`, ale umí fallback na `python3` (venv je preferovaný, ne absolutně povinný).
- NAS deployment skript je potvrzen: `deploy_to_haos.sh` (SSH alias `ha`, kopie souborů do HA add-on adresáře, rebuild/start addonu).
- Librarian best-practice (2026): pro local CI bez host venv použít Docker-first "toolbox" image (lint/test/security/sonar), report artifacts do mountnutého `reports/`, Sonar scanner přes `sonarsource/sonar-scanner-cli` kontejner.
- Důležité guardraily z best-practice: path mapping pro `coverage.xml` vs Sonar (`/app` vs repo root), a Linux file ownership (`UID:GID`) aby reports nebyly root-owned.
- Doporučení z best-practice: sjednotit vstup přes jeden task runner (Makefile/Taskfile) a držet CI commands deterministické vůči produkční image/toolchain.

## Recommendation Snapshot
- Best practice pro rok 2026: Docker-first local CI (toolbox image), host bez Python toolchain driftu.
- Praktický rollout: 2-krokově — nejdřív rychlý cleanup shell workflow, pak Docker-first migrace.
- Důvod: minimalizace rizika při přechodu + rychlé zlepšení hned (entrypoint, duplicity, docs).

## Open Questions
- Má systém fungovat trvale bez internetu, nebo jen přežít výpadky internetu?
- Máte pod kontrolou DNS pro OIG Box (router/NAS DNS)?
- Má být vývoj i testy primárně spouštěné v kontejnerech, aby se minimalizovaly lokální Python instalace?
- Chcete OIG napojit na centrální reverse proxy/monitoring z `core-platform`/`majestic-ai`, nebo jej držet plně izolovaně bez cross-repo runtime závislostí?
- Jak se jmenuje a kde leží stávající deploy skript na NAS (aby šel zapojit do standardního workflow)?
- Má být oficiální local quality entrypoint `ci/ci.sh` (a sjednotit dokumentaci), nebo chcete zachovat alias kompatibilní s `.github/scripts/ci.sh`?
- Chcete v plánu řešit odstranění duplicitního security běhu v `ci/ci.sh` (kratší běh), nebo to ponechat kvůli záměrné redundanci?
- Chcete v tom samém plánu rovnou přejít na Docker-first lokální CI runner (bez host pip install), nebo nejdřív pouze sjednotit stávající shell workflow?
- Potvrdit cílový model bez duplicitního runtime kontejneru: CI v ephemeral toolbox kontejneru + jediný runtime image/promoce do HA?

## Scope Boundaries
- INCLUDE: dev prostředí, testovací workflow a deployment workflow mezi OpenCode a lokálním NAS.
- EXCLUDE: implementace změn v kódu v této fázi.
