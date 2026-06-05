#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    exec "./.venv/bin/python" main.py
else 
    exec python3 main.py
fi