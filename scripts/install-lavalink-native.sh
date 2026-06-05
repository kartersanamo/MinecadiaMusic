#!/usr/bin/env bash
# Install and run Lavalink 4 without Docker (Debian/Ubuntu VPS).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAVALINK_DIR="${ROOT}/lavalink"
LAVALINK_VERSION="${LAVALINK_VERSION:-4.2.2}"
JAR_URL="https://github.com/lavalink-devs/Lavalink/releases/download/${LAVALINK_VERSION}/Lavalink.jar"
SERVICE_NAME="minecadia-lavalink"

log() { printf '[lavalink-install] %s\n' "$*"; }

if ! command -v java >/dev/null 2>&1; then
  log "Installing OpenJDK 17..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y openjdk-17-jre-headless curl
fi

mkdir -p "${LAVALINK_DIR}"
if [[ ! -f "${LAVALINK_DIR}/Lavalink.jar" ]]; then
  log "Downloading Lavalink ${LAVALINK_VERSION}..."
  curl -fsSL -o "${LAVALINK_DIR}/Lavalink.jar" "${JAR_URL}"
fi

if [[ ! -f "${LAVALINK_DIR}/application.yml" ]]; then
  cp "${ROOT}/docker/lavalink-application.yml" "${LAVALINK_DIR}/application.yml"
  log "Copied application.yml — set password in ${LAVALINK_DIR}/application.yml and LAVALINK_PASSWORD in .env"
fi

UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
if [[ "${INSTALL_SYSTEMD:-0}" == "1" ]] && [[ "$(id -u)" -eq 0 ]]; then
  cat > "${UNIT}" <<EOF
[Unit]
Description=Minecadia Lavalink
After=network.target

[Service]
Type=simple
WorkingDirectory=${LAVALINK_DIR}
ExecStart=/usr/bin/java -Xmx512M -jar ${LAVALINK_DIR}/Lavalink.jar
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}"
  log "Systemd service ${SERVICE_NAME} enabled. Status: systemctl status ${SERVICE_NAME}"
else
  log "Run manually: ${ROOT}/scripts/run-lavalink-native.sh"
  log "Or as root: INSTALL_SYSTEMD=1 $0"
fi

log "Lavalink listens on 127.0.0.1:2333 (password in application.yml)"
