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
    PYTHON="./.venv/bin/python"
else
    PYTHON="python3"
fi

RESTART_DELAY="${MINECADIA_BOT_RESTART_DELAY:-5}"

while true; do
    set +e
    "$PYTHON" main.py
    code=$?
    set -e

    if [ "$code" -eq 0 ]; then
        echo "$BOT_NAME exited cleanly." >&2
        break
    fi

    if [ "$code" -eq 130 ] || [ "$code" -eq 143 ]; then
        echo "$BOT_NAME interrupted — not restarting." >&2
        break
    fi

    echo "$BOT_NAME exited with code $code — restarting in ${RESTART_DELAY}s..." >&2
    sleep "$RESTART_DELAY"
done
