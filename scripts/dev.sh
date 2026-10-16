#!/usr/bin/env bash
# One command: venv + deps + UI build + serve. Usage: scripts/dev.sh [port]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-${PORT:-8080}}"
PY="${PYTHON:-python3}"
test -d .venv || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"
if command -v npm >/dev/null 2>&1; then (cd ui_standalone && npm install --no-audit --no-fund --loglevel=error && npx vite build); else echo "npm not found: API-only mode (no UI)"; fi
echo "chat_engine → http://localhost:${PORT}  (UI at /, OpenAPI at /openapi.json)"
exec env PORT="$PORT" .venv/bin/python -m chat_engine serve
