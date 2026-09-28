#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
PORT="${1:-7860}"
cd "$(dirname "$0")/.."
exec "$PYTHON_BIN" app.py --mock --host 0.0.0.0 --port "$PORT"