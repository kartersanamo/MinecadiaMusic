#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

BOT_NAME="$(basename "$(pwd)")"
LOCK_FILE="/tmp/minecadia-${BOT_NAME,,}.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$BOT_NAME is already running." >&2
  echo "Check: pgrep -af '${BOT_NAME}.*main.py'" >&2
  exit 1
fi

if [ -x ".venv/bin/python" ]; then
    exec "./.venv/bin/python" main.py
else
    exec python3 main.py
fi
