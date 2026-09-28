#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "== Python =="
"$PYTHON_BIN" --version

echo "== Required modules =="
"$PYTHON_BIN" - <<'PY'
import importlib.util
for name in ["gradio", "fastapi", "uvicorn", "multipart"]:
    print(f"{name}: {'ok' if importlib.util.find_spec(name) else 'missing'}")
PY

echo "== Remote model configuration =="
printf 'MODEL_CONFIG_PATH=%s\n' "${MODEL_CONFIG_PATH:-model_profiles.json}"
printf 'MODEL_PROFILE=%s\n' "${MODEL_PROFILE:-}"
printf 'MODEL_API_BASE_URL=%s\n' "${MODEL_API_BASE_URL:-}"
printf 'MODEL_NAME=%s\n' "${MODEL_NAME:-}"
if [[ -n "${MODEL_API_KEY:-}" ]]; then
  echo "MODEL_API_KEY=configured"
else
  echo "MODEL_API_KEY=missing (may be supplied by profile-specific env var)"
fi

echo "No local model, torch, CUDA, NPU, or llama.cpp is required."