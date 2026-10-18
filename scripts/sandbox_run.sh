#!/usr/bin/env bash
# Start (or restart) the gateway (127.0.0.1:8800) and chat_engine (0.0.0.0:8080) as detached processes.
# Logs: $RUN_DIR/gw.log, $RUN_DIR/ce.log. Requires scripts/sandbox_bootstrap.sh first.
set -euo pipefail
cd "$(dirname "$0")/.."
REF_DIR="${REF_DIR:-$HOME/ref}"
RUN_DIR="${RUN_DIR:-$HOME/run}"
PY="$REF_DIR/general_ai_core/.venv/bin/python"
GATEWAY_DIR="${GATEWAY_DIR:-$REF_DIR/gateway/__gateway-service}"

kill_port() { for p in $(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | cut -d= -f2); do kill "$p" 2>/dev/null || true; done; }
kill_port 8800; kill_port 8080; sleep 1

if [ -f "$GATEWAY_DIR/app.py" ]; then
  # setsid + </dev/null: the gateway must outlive this shell (nohup alone is not enough)
  ( cd "$GATEWAY_DIR" && set -a && . "$RUN_DIR/gw.env" && set +a && setsid nohup "$PY" app.py > "$RUN_DIR/gw.log" 2>&1 < /dev/null & )
  for _ in $(seq 1 40); do curl -sf http://127.0.0.1:8800/healthz >/dev/null 2>&1 && break; sleep 1; done
  curl -sf http://127.0.0.1:8800/healthz >/dev/null 2>&1 || { echo "GATEWAY_DOWN (see $RUN_DIR/gw.log)"; tail -20 "$RUN_DIR/gw.log"; exit 1; }
  echo "GATEWAY $(curl -s http://127.0.0.1:8800/healthz)"
  CE_ENV="$RUN_DIR/ce.env"
else
  echo "gateway not present — chat_engine starts without gateway providers"
  CE_ENV=/dev/null
fi

( set -a && . "$CE_ENV" && set +a && PORT=8080 setsid nohup "$PY" -m chat_engine serve > "$RUN_DIR/ce.log" 2>&1 < /dev/null & )
for _ in $(seq 1 60); do curl -sf http://localhost:8080/healthz >/dev/null 2>&1 && break; sleep 1; done
curl -s http://localhost:8080/healthz | python3 -c 'import json,sys; h=json.load(sys.stdin); print("CHAT_ENGINE providers:", h["providers"]); print("gateway:", h.get("gateway"))'
