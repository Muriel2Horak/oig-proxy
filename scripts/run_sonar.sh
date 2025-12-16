#!/usr/bin/env bash
set -euo pipefail

SONAR_HOST_URL="${SONAR_HOST_URL:-http://sonarqube:9000}"
SONAR_DOCKER_NETWORK="${SONAR_DOCKER_NETWORK:-oig_cloud_default}"

if [[ -z "${SONAR_TOKEN:-}" ]]; then
  echo "Missing SONAR_TOKEN. Create a token in SonarQube (My Account -> Security) and run:" >&2
  echo "  SONAR_TOKEN=... $0" >&2
  exit 2
fi

exec docker run --rm \
  --network "${SONAR_DOCKER_NETWORK}" \
  -v "$(pwd):/usr/src" \
  sonarsource/sonar-scanner-cli:latest \
  -Dsonar.host.url="${SONAR_HOST_URL}" \
  -Dsonar.login="${SONAR_TOKEN}"
