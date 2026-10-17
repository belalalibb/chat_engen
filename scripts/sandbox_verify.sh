#!/usr/bin/env bash
# Full post-reset verification, detached-friendly: bring-up -> gate (servers stopped) ->
# restart servers -> Playwright smoke -> live provider matrix. Writes evidence/ce20/* and
# $RUN_DIR/verify.log; prints VERIFY_DONE at the end. Secrets never leave $RUN_DIR.
set -uo pipefail
cd "$(dirname "$0")/.."
REF_DIR="${REF_DIR:-$HOME/ref}"; RUN_DIR="${RUN_DIR:-$HOME/run}"; export REF_DIR RUN_DIR
PY="$REF_DIR/general_ai_core/.venv/bin/python"
mkdir -p evidence/ce20
kill_port() { for p in $(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | cut -d= -f2); do kill "$p" 2>/dev/null || true; done; }

echo "== 1 bootstrap"; bash scripts/sandbox_bootstrap.sh 2>&1 | grep -v "npm notice" | tail -1
"$PY" -m playwright install chromium >/dev/null 2>&1 || true
sudo "$PY" -m playwright install-deps chromium >/dev/null 2>&1 || true

echo "== 2 gate (servers stopped)"; kill_port 8080; kill_port 8800; sleep 1
timeout 500 bash scripts/gate.sh "$PY" > evidence/ce20/gate.txt 2>&1; echo "GATE_EXIT=$? $(tail -1 evidence/ce20/gate.txt)"

echo "== 3 servers"; WITH_PLAYWRIGHT=0 bash scripts/sandbox_up.sh 2>&1 | grep -E "GATEWAY_UP|CHAT_ENGINE_UP"

echo "== 4 smoke"; timeout 300 "$PY" tests/ui/smoke_playwright.py http://localhost:8080 > evidence/ce20/playwright_smoke.txt 2>&1; tail -1 evidence/ce20/playwright_smoke.txt

echo "== 5 live matrix"; timeout 280 "$PY" - > evidence/ce20/live_matrix.txt 2>&1 <<'EOF'
import httpx, concurrent.futures as cf, time
h=httpx.get("http://localhost:8080/healthz",timeout=10).json()
models=httpx.get("http://localhost:8080/v1/models",timeout=10).json()["models"]
first={}
for m in models:
    for pk in m["providers"]:
        if pk.startswith("gw_") and pk not in first: first[pk]=m["name"]
print(f"chat_engine {h['version']} upstream {h['upstream_sha'][:7]} | providers={len(h['providers'])} gateway bound={len(h['gateway']['bound'])} skipped={h['gateway']['skipped']}")
def probe(pk):
    t=time.time()
    try:
        r=httpx.post("http://localhost:8080/v1/chat",json={"message":"Reply with exactly: OK","model":first[pk],"provider":pk},timeout=120).json()
        if "turn" in r: res="OK   "+"".join(p.get("text","") for p in r["turn"]["parts"])[:40].replace("\n"," ")
        else: e=r["error"]; res=f"FAIL {e['code']} / {e.get('details',{}).get('provider_error_category')} - {e['message'][:50]}"
    except Exception as ex: res=f"ERR  {type(ex).__name__}"
    return pk,first[pk],res,int((time.time()-t)*1000)
with cf.ThreadPoolExecutor(8) as ex:
    for pk,m,res,ms in sorted(ex.map(probe,sorted(first))):
        print(f"{pk:18}{m[:27]:28}{res}  ({ms} ms)")
# strategies
for body in ({"message":"What is 2+2? Number only.","strategy":"cost_tiered"},{"message":"Say OK"}):
    try:
        r=httpx.post("http://localhost:8080/v1/chat",json=body,timeout=170).json()
        print("strategy",body.get("strategy","single/auto"),"->","OK "+r["turn"]["provider_key"]+"/"+r["turn"]["model_key"] if "turn" in r else "FAIL "+r["error"]["code"])
    except Exception as ex: print("strategy",body.get("strategy"),"ERR",type(ex).__name__)
EOF
grep -c "OK   " evidence/ce20/live_matrix.txt | sed 's/^/providers answering: /'; grep "^strategy" evidence/ce20/live_matrix.txt
echo "VERIFY_DONE"
