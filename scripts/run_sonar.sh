#!/usr/bin/env bash
set -euo pipefail

SONAR_HOST_URL="${SONAR_HOST_URL:-http://host.docker.internal:9001}"
SONAR_PROJECT_KEY="${SONAR_PROJECT_KEY:-oig_proxy}"
SONAR_PROJECT_NAME="${SONAR_PROJECT_NAME:-oig_proxy}"
SONAR_PROJECT_VERSION="${SONAR_PROJECT_VERSION:-}"
SONAR_DOCKER_NETWORK="${SONAR_DOCKER_NETWORK:-}"
SONAR_SCANNER_IMAGE="${SONAR_SCANNER_IMAGE:-sonarsource/sonar-scanner-cli:latest}"

if [[ -z "${SONAR_TOKEN:-}" ]]; then
  echo "Missing SONAR_TOKEN. Create a token in SonarQube (My Account -> Security) and run:" >&2
  echo "  SONAR_TOKEN=... $0" >&2
  echo "" >&2
  echo "Optional env overrides:" >&2
  echo "  SONAR_HOST_URL=http://host.docker.internal:9001" >&2
  echo "  SONAR_PROJECT_KEY=oig_proxy" >&2
  echo "  SONAR_PROJECT_NAME=oig_proxy" >&2
  echo "  SONAR_PROJECT_VERSION=1.2.3" >&2
  echo "  SONAR_DOCKER_NETWORK=" >&2
  exit 2
fi

docker_args=(--rm -v "$(pwd):/usr/src")
if [[ -n "${SONAR_DOCKER_NETWORK}" ]]; then
  docker_args+=(--network "${SONAR_DOCKER_NETWORK}")
fi

scan_args=(
  -Dsonar.host.url="${SONAR_HOST_URL}"
  -Dsonar.login="${SONAR_TOKEN}"
  -Dsonar.projectKey="${SONAR_PROJECT_KEY}"
  -Dsonar.projectName="${SONAR_PROJECT_NAME}"
)
if [[ -n "${SONAR_PROJECT_VERSION}" ]]; then
  scan_args+=(-Dsonar.projectVersion="${SONAR_PROJECT_VERSION}")
fi

exec docker run "${docker_args[@]}" \
  "${SONAR_SCANNER_IMAGE}" \
  "${scan_args[@]}"
