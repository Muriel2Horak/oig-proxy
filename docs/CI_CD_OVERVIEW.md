# Testy a kontroly OIG Proxy

GitHub Actions ověřují obsah větve a změny navržené v PR. Nasazení do
Home Assistantu a ověření fyzického boxu probíhají samostatně.

## Co uvidíte v GitHubu

| Kontrola | Co ověřuje |
| --- | --- |
| [CI](../.github/workflows/ci.yml) | Testy na Pythonu 3.11, pokrytí příkazů striktně nad 80 %, mypy, Hadolint a Bandit. Poté odešle analýzu do SonarCloudu. |
| [Pylint](../.github/workflows/pylint.yml) | Lint testů podle parametrů uvedených ve workflow. |
| [Security Scan](../.github/workflows/security-scan.yml) | Gitleaks, Bandit, Safety, Semgrep a Trivy. Trivy analyzuje rozřešené závislosti a jejich licence. Výsledky skenů jsou v artefaktech běhu. |

Pokrytí příkazů se počítá z produkčních souborů v `addon/oig-proxy`.
Přehled pokrytí obsahuje i větve. Hranice 80 % ve workflow se vztahuje
na příkazy. Samostatné skeny hotových obrazů a test provozu na boxu
doplňují kontroly v PR. [Bezpečnostní testy](SECURITY_TESTING.md)

## Lokální ověření

V kořeni repozitáře použijte prostředí s Pythonem 3.11.

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
mkdir -p reports
PYTHONPATH=addon/oig-proxy python -m pytest --cov=addon/oig-proxy --cov-branch \
  --junitxml=reports/junit.xml --cov-report=xml:reports/coverage.xml \
  --cov-report=json:reports/coverage.json
jq -e '.totals | (.num_statements > 0) and
  (.covered_lines * 100 > .num_statements * 80)' reports/coverage.json
MYPYPATH=addon/oig-proxy mypy --explicit-package-bases addon/oig-proxy --ignore-missing-imports
```

Souhrnný lokální skript je [`ci/ci.sh`](../ci/ci.sh). Přesná kritéria
pro dané PR určuje aktuální workflow v repozitáři.

## SonarCloud

[Projekt](https://sonarcloud.io/project/overview?id=Muriel2Horak_oig-proxy)
používá organizaci `muriel2horak` a klíč
`Muriel2Horak_oig-proxy`. Obě hodnoty jsou nastavené v
[`sonar-project.properties`](../sonar-project.properties).

1. V SonarCloudu připravte token s oprávněním **Execute Analysis** pro tento
   projekt. Podle plánu použijte token omezený na projekt nebo token účtu,
   který má příslušné oprávnění.
2. V repozitáři v **Settings → Secrets and variables → Actions** uložte
   token do secretu `SONAR_TOKEN`. Hodnotu tokenu dávejte pouze do secretu.
3. Zopakujte workflow **CI** a otevřete odpovídající analýzu PR v SonarCloudu.
   Výsledek Quality Gate zkontrolujte samostatně. Aktuální krok v CI čeká
   na odeslání analýzy, bez samostatného čekání na verdikt Quality Gate.

HTTP 403 při dotazu na `/analysis/jres` vzniká při startu skeneru před
analýzou projektu. Ověřte platnost tokenu a jeho přiřazení k projektu.
[Dokumentace SonarCloudu pro GitHub Actions](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/ci-based-analysis/github-actions-for-sonarcloud)
