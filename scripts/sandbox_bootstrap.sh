#!/usr/bin/env bash
# Rebuild the ephemeral sandbox environment for chat_engine + the owner's Remote Provider Gateway.
# Idempotent. Nothing secret is written into the repo: generated secrets live in $RUN_DIR (chmod 600).
#
#   scripts/sandbox_bootstrap.sh            # env + venv + UI build + (re)generate gateway secrets
#   GATEWAY_REPO_URL=https://x-access-token:$TOKEN@github.com/ORG/REPO.git scripts/sandbox_bootstrap.sh
#
# After this, start both processes with scripts/sandbox_run.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
REF_DIR="${REF_DIR:-$HOME/ref}"
RUN_DIR="${RUN_DIR:-$HOME/run}"
UPSTREAM_SHA="${UPSTREAM_SHA:-0a13556305bcf40cbf1b750d7ef1d81f7850cabf}"
GATEWAY_REPO_URL="${GATEWAY_REPO_URL:-}"
GATEWAY_DIR="$REF_DIR/gateway"
mkdir -p "$REF_DIR" "$RUN_DIR"

# 1) upstream general_ai_core at the pinned SHA + venv with chat_engine editable
if [ ! -d "$REF_DIR/general_ai_core/.git" ]; then
  git clone -q https://github.com/belalalibb/general_ai_core.git "$REF_DIR/general_ai_core"
fi
( cd "$REF_DIR/general_ai_core" && git checkout -q "$UPSTREAM_SHA" )
PY="$REF_DIR/general_ai_core/.venv/bin/python"
if [ ! -x "$PY" ]; then
  python3 -m venv "$REF_DIR/general_ai_core/.venv"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -e ".[dev]"
  # chat_engine's git pin installs upstream as a plain copy; re-point it at the editable
  # checkout LAST so mypy/gate see one source tree (same SHA, no behavioural difference).
  "$PY" -m pip install -q --no-deps -e "$REF_DIR/general_ai_core"
  "$PY" -m pip install -q playwright filelock pytest-asyncio
fi
echo "VENV_READY $PY"

# 2) UI build (memory-capped for ~1 GB sandboxes)
( cd ui_standalone && npm install --no-audit --no-fund --loglevel=error && NODE_OPTIONS=--max-old-space-size=384 npx vite build >/dev/null )
echo "UI_READY"

# 3) owner's gateway repo (optional) + generated secrets/route tokens
if [ -n "$GATEWAY_REPO_URL" ]; then
  if [ ! -d "$GATEWAY_DIR/.git" ]; then
    git -c credential.helper= clone -q "$GATEWAY_REPO_URL" "$GATEWAY_DIR"
    # never keep a token inside the clone's remote url
    ( cd "$GATEWAY_DIR" && git remote set-url origin "$(echo "$GATEWAY_REPO_URL" | sed -E 's#https://[^@]+@#https://#')" )
  fi
  echo "GATEWAY_CLONED $GATEWAY_DIR"
fi
if [ ! -f "$RUN_DIR/gw.env" ] || [ ! -f "$RUN_DIR/ce.env" ]; then
  SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
  SLUGS="${GATEWAY_SLUGS:-groq syntx deepai freebuff youmind ivycraft llamacoder upstage voyage runpod_console parallel notegpt overchat tinyfish}"
  ROUTE_MAP=""; CE_ROUTES=""
  for s in $SLUGS; do
    t="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
    ROUTE_MAP="${ROUTE_MAP:+$ROUTE_MAP,}$t:$s"
    CE_ROUTES="${CE_ROUTES:+$CE_ROUTES,}gw_$s=$t"
  done
  umask 077
  printf 'GW_SECRET_CURRENT=%s\nGW_SECRET_CURRENT_VERSION=1\nGW_ROUTE_MAP=%s\n' "$SECRET" "$ROUTE_MAP" > "$RUN_DIR/gw.env"
  printf 'CE_GATEWAY_BASE_URL=http://127.0.0.1:8800\nCE_GATEWAY_SECRET=%s\nCE_GATEWAY_SECRET_VERSION=1\nCE_GATEWAY_ROUTES=%s\n' "$SECRET" "$CE_ROUTES" > "$RUN_DIR/ce.env"
  echo "SECRETS_GENERATED $RUN_DIR/{gw,ce}.env"
fi
echo "BOOTSTRAP_DONE"
