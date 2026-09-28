#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
PORT="${1:-8000}"
cd "$(dirname "$0")/.."
exec "$PYTHON_BIN" server.py --host 0.0.0.0 --port "$PORT"