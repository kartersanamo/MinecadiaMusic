#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAVALINK_DIR="${ROOT}/lavalink"

if [[ ! -f "${LAVALINK_DIR}/Lavalink.jar" ]]; then
  echo "Lavalink.jar missing. Run: ${ROOT}/scripts/install-lavalink-native.sh"
  exit 1
fi

if [[ ! -f "${LAVALINK_DIR}/application.yml" ]]; then
  cp "${ROOT}/docker/lavalink-application.yml" "${LAVALINK_DIR}/application.yml"
fi

cd "${LAVALINK_DIR}"
exec java -Xmx512M -jar Lavalink.jar
