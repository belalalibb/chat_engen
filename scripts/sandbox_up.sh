#!/usr/bin/env bash
# One shot after a sandbox reset: bootstrap (venv/UI/gateway clone/secrets) + Playwright deps
# + start gateway and chat_engine detached. Idempotent. Prints READY lines; logs in $RUN_DIR.
set -euo pipefail
cd "$(dirname "$0")/.."
REF_DIR="${REF_DIR:-$HOME/ref}"
RUN_DIR="${RUN_DIR:-$HOME/run}"
export REF_DIR RUN_DIR
bash scripts/sandbox_bootstrap.sh 2>&1 | grep -v "npm notice" | tail -2
PY="$REF_DIR/general_ai_core/.venv/bin/python"
if [ "${WITH_PLAYWRIGHT:-1}" = "1" ]; then
  "$PY" -m playwright install chromium >/dev/null 2>&1 || true
  sudo "$PY" -m playwright install-deps chromium >/dev/null 2>&1 || true
  echo "PW_READY"
fi
GATEWAY_DIR="$REF_DIR/gateway/__gateway-service"
kill_port() { for p in $(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | cut -d= -f2); do kill "$p" 2>/dev/null || true; done; }
kill_port 8800; kill_port 8080; sleep 1
if [ -f "$GATEWAY_DIR/app.py" ]; then
  ( cd "$GATEWAY_DIR" && set -a && . "$RUN_DIR/gw.env" && set +a && setsid nohup "$PY" app.py > "$RUN_DIR/gw.log" 2>&1 < /dev/null & )
  for _ in $(seq 1 40); do curl -sf http://127.0.0.1:8800/healthz >/dev/null 2>&1 && break; sleep 1; done
  echo "GATEWAY_UP"
  CE_ENV="$RUN_DIR/ce.env"
else
  CE_ENV=/dev/null
fi
( set -a && . "$CE_ENV" && set +a && PORT=8080 setsid nohup "$PY" -m chat_engine serve > "$RUN_DIR/ce.log" 2>&1 < /dev/null & )
for _ in $(seq 1 90); do curl -sf http://localhost:8080/healthz >/dev/null 2>&1 && break; sleep 1; done
curl -s http://localhost:8080/healthz | python3 -c 'import json,sys; h=json.load(sys.stdin); print("CHAT_ENGINE_UP providers=%d gateway_bound=%d skipped=%s" % (len(h["providers"]), len(h.get("gateway",{}).get("bound",[])), list(h.get("gateway",{}).get("skipped",{}))))'
echo "ALL_READY"
