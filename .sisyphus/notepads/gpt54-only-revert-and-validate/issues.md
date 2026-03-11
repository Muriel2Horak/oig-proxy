## 2026-03-10

- Base environment lacked direct `coverage` availability and initially rejected global pip installs (externally managed environment); resolved by using project-local virtualenv `.venv` and running tests through `.venv/bin/pytest`.
