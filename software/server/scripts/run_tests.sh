#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
cd "$(dirname "$0")/.."
exec "$PYTHON_BIN" -m unittest discover -s tests -v
