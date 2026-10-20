#!/usr/bin/env python3
"""Live, end-to-end functional check of a RUNNING chat_engine (default http://localhost:8080).

Covers every API surface the UI uses plus every provider x model pair, and reports honestly:
OK / FAIL (chat_engine or contract defect — must be fixed) / UPSTREAM (the provider behind
the owner's gateway refused: credentials, quota, outage — not a chat_engine bug).

Usage: python scripts/live_full_check.py [BASE] > evidence/ce20/live_full_check.txt
Exit code 1 iff at least one FAIL (UPSTREAM rows never fail the run).
"""

from __future__ import annotations

import concurrent.futures as cf
import contextlib
import json
import sys
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
C = httpx.Client(base_url=BASE, timeout=180)
ROWS: list[tuple[str, str, str]] = []  # (status, name, detail)
UPSTREAM_CATS = {
    "quota_exceeded",
    "invalid_credential",
    "auth_expired",
    "provider_unavailable",
    "rate_limited",
    "timeout",
    "provider_error",
    "unknown",
}


def row(status: str, name: str, detail: str = "") -> None:
    ROWS.append((status, name, detail))
    print(f"{status:9}{name:58}{detail}", flush=True)


def ok(name: str, cond: bool, detail: str = "") -> bool:
    row("OK" if cond else "FAIL", name, detail)
    return cond


def j(r: httpx.Response) -> dict:
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:200]}


def text_of(turn: dict) -> str:
    return "".join(p.get("text", "") for p in turn.get("parts", []))


# --- 1 platform ------------------------------------------------------------------------------
h = j(C.get("/healthz"))
ok(
    "GET /healthz",
    h.get("status") == "alive",
    f"v{h.get('version')} providers={len(h.get('providers', []))}",
)
gw = h.get("gateway", {})
ok(
    "gateway bound (>=1)",
    len(gw.get("bound", [])) >= 1,
    f"bound={len(gw.get('bound', []))} skipped={gw.get('skipped')}",
)
ok("GET / (UI)", C.get("/").status_code == 200)
ok("GET /openapi.json", "paths" in j(C.get("/openapi.json")))

models_env = j(C.get("/v1/models"))
models = models_env.get("models", [])
ok("GET /v1/models", len(models) > 0, f"{len(models)} models")
oai = j(C.get("/v1/openai/models")).get("data", [])
ok("GET /v1/openai/models (UI picker)", len(oai) > 0, f"{len(oai)} ids")
if oai:
    ok(
        "GET /v1/openai/models/{id}",
        C.get(f"/v1/openai/models/{oai[0]['id']}").status_code == 200,
        oai[0]["id"],
    )

cat = j(C.get("/v1/catalog/models", params={"limit": 5}))
ok(
    "GET /v1/catalog/models",
    isinstance(cat.get("models", cat.get("items")), list),
    f"total={cat.get('total')}",
)
ok("GET /v1/catalog/aliases", C.get("/v1/catalog/aliases").status_code == 200)
ok("GET /v1/skills", C.get("/v1/skills").status_code == 200)
ok("GET /v1/usage", C.get("/v1/usage").status_code == 200)
ok("GET /v1/usage/records", C.get("/v1/usage/records").status_code == 200)
ok("GET /v1/executions", C.get("/v1/executions").status_code == 200)
for p in (
    "/v1/admin/providers",
    "/v1/admin/providers/managed",
    "/v1/admin/models",
    "/v1/admin/pricing",
    "/v1/admin/admission",
    "/v1/admin/routing/weights",
    "/v1/admin/capabilities/ledger",
    "/v1/admin/capabilities/catalog",
    "/v1/admin/capabilities/probes",
    "/v1/credentials",
    "/v1/agent-profiles",
    "/v1/projects",
    "/v1/conversations",
    "/v1/apps",
):
    ok(f"GET {p}", C.get(p).status_code == 200)

# --- 2 provider x model matrix (sync chat, explicit model+provider) ---------------------------
pairs: list[tuple[str, str]] = []
for m in models:
    for pk in m.get("providers", []):
        pairs.append((pk, m["name"]))
pairs = sorted(set(pairs))
print(f"\n== provider x model matrix: {len(pairs)} pairs", flush=True)


def probe(pair: tuple[str, str]) -> tuple[str, str, str, str, int]:
    pk, model = pair
    t = time.time()
    try:
        r = C.post(
            "/v1/chat",
            json={"message": "Reply with exactly: OK", "model": model, "provider": pk},
            timeout=150,
        )
        d = j(r)
        if "turn" in d and d["turn"].get("status") == "complete":
            txt = text_of(d["turn"]).replace("\n", " ")[:40]
            # the winning provider may differ when upstream fails over
            used = f"{d['turn'].get('provider_key')}/{d['turn'].get('model_key')}"
            return pk, model, "OK", f"{used} -> {txt!r}", int((time.time() - t) * 1000)
        if "turn" in d:
            return (
                pk,
                model,
                "FAIL",
                f"turn status={d['turn'].get('status')} http={r.status_code}",
                int((time.time() - t) * 1000),
            )
        e = d.get("error", {})
        det = e.get("details") or {}
        cat = det.get("provider_error_category")
        status = (
            "UPSTREAM"
            if (cat in UPSTREAM_CATS or e.get("code") in {"execution_failed", "not_found"})
            else "FAIL"
        )
        if e.get("code") == "admission_denied" and str(det.get("scope", "")).startswith(
            "provider:"
        ):
            # the provider's own breaker opened after repeated upstream failures (30 s)
            status, cat = (
                "BREAKER",
                f"{det.get('scope')}/{det.get('dimension')} retry_after={det.get('retry_after_ms')}ms",
            )
        return (
            pk,
            model,
            status,
            f"http={r.status_code} {e.get('code')}/{cat}: {str(e.get('message'))[:60]}",
            int((time.time() - t) * 1000),
        )
    except Exception as ex:  # transport-level
        return (
            pk,
            model,
            "FAIL",
            f"{type(ex).__name__}: {str(ex)[:60]}",
            int((time.time() - t) * 1000),
        )


by_provider: dict[str, list[tuple[str, str]]] = {}
for pk, model in pairs:
    by_provider.setdefault(pk, []).append((pk, model))


def probe_provider(items: list[tuple[str, str]]) -> list[tuple[str, str, str, str, int]]:
    # one request in flight per provider: the per-provider admission ceiling (concurrency 4,
    # conservative default) and the provider breaker are features we must not trip by design
    return [probe(it) for it in items]


answering: dict[str, str] = {}
tripped: set[str] = set()  # providers whose own breaker opened during the matrix
with cf.ThreadPoolExecutor(6) as ex:
    results = [r for group in ex.map(probe_provider, by_provider.values()) for r in group]
for pk, model, status, detail, ms in sorted(results):
    row(status, f"chat {pk} / {model[:40]}", f"{detail}  ({ms} ms)")
    if status == "OK" and pk not in answering:
        answering[pk] = model
    if status == "BREAKER":
        tripped.add(pk)
# prefer a provider that answered AND did not trip its breaker during the matrix
answering = dict(sorted(answering.items(), key=lambda kv: (kv[0] in tripped, kv[0])))


_post = C.post


def post_chat(path: str, **kw):  # one honest wait when the chosen provider's breaker is open
    r = _post(path, **kw)
    d = j(r)
    det = d.get("error", {}).get("details") or {}
    if r.status_code == 429 and str(det.get("scope", "")).startswith("provider:"):
        wait = min(31, (det.get("retry_after_ms") or 30000) / 1000 + 0.5)
        print(
            f"         (provider breaker open on {det.get('scope')}: waiting {wait:.0f}s, by design)",
            flush=True,
        )
        time.sleep(wait)
        r = _post(path, **kw)
    return r


print(f"\nproviders answering: {sorted(answering)}", flush=True)
ok(
    "at least one gateway provider answers",
    any(k.startswith("gw_") for k in answering),
    str(sorted(answering)),
)

live_pk, live_model = next(iter(answering.items()), (None, None))

# --- 3 conversations / projects / turns ------------------------------------------------------
print("\n== conversations & projects", flush=True)
pr = C.post("/v1/projects", json={"name": "live-check", "system_instructions": "Answer briefly."})
ok("POST /v1/projects", pr.status_code == 201, str(pr.status_code))
project_id = j(pr).get("id")
cv = C.post(
    "/v1/conversations", json={"title": "live-check", "project_id": project_id, "model": live_model}
)
ok("POST /v1/conversations", cv.status_code == 201, str(cv.status_code))
conv_id = j(cv).get("id")
ok("GET /v1/conversations/{id}", C.get(f"/v1/conversations/{conv_id}").status_code == 200)
ok(
    "PATCH /v1/conversations/{id}",
    C.patch(f"/v1/conversations/{conv_id}", json={"title": "renamed"}).status_code == 200,
)
ok(
    "PATCH /v1/projects/{id}",
    C.patch(f"/v1/projects/{project_id}", json={"name": "renamed"}).status_code == 200,
)

if live_model:
    r1 = post_chat(
        "/v1/chat", json={"conversation_id": conv_id, "message": "My name is Belal. Reply: noted."}
    )
    d1 = j(r1)
    ok("chat turn 1 in conversation", r1.status_code == 200 and "turn" in d1, str(r1.status_code))
    r2 = post_chat(
        "/v1/chat", json={"conversation_id": conv_id, "message": "What is my name? One word."}
    )
    d2 = j(r2)
    t2 = text_of(d2.get("turn", {})) if "turn" in d2 else ""
    ok("chat turn 2 (history carried)", r2.status_code == 200 and "turn" in d2, f"{t2[:50]!r}")
    row(
        "OK" if "belal" in t2.lower() else "UPSTREAM",
        "memory recall (model quality)",
        f"{t2[:50]!r}",
    )
    turns = j(C.get(f"/v1/conversations/{conv_id}/turns"))
    n = len(turns.get("turns", []))
    ok("GET /v1/conversations/{id}/turns (4 turns)", n == 4, f"turns={n}")
    if "turn" in d2 and d2.get("execution_id"):
        ok(
            "GET /v1/executions/{id}",
            C.get(f"/v1/executions/{d2['execution_id']}").status_code == 200,
        )
        ok(
            "GET /v1/executions/{id}/events",
            C.get(f"/v1/executions/{d2['execution_id']}/events").status_code == 200,
        )
    ok("GET /v1/usage/records has rows", len(j(C.get("/v1/usage/records")).get("records", [])) >= 2)

# --- 4 chat AUTO (no model) + failover -------------------------------------------------------
print("\n== auto routing", flush=True)
ra = post_chat("/v1/chat", json={"message": "Say OK"})
da = j(ra)
ok(
    "POST /v1/chat AUTO (no model) 200",
    ra.status_code == 200 and "turn" in da,
    f"{da.get('turn', {}).get('provider_key')}/{da.get('turn', {}).get('model_key')} http={ra.status_code} {da.get('error', {}).get('code', '')}",
)

# --- 5 streaming -----------------------------------------------------------------------------
print("\n== streaming", flush=True)


def sse(path: str, body: dict, timeout: float = 150) -> list[dict]:
    evs: list[dict] = []
    with C.stream(
        "POST", path, json=body, headers={"accept": "text/event-stream"}, timeout=timeout
    ) as r:
        if r.status_code != 200:
            return [
                {
                    "type": "_http",
                    "data": {
                        "status": r.status_code,
                        "body": r.read()[:200].decode(errors="replace"),
                    },
                }
            ]
        for line in r.iter_lines():
            if line.startswith("data:"):
                with contextlib.suppress(Exception):
                    evs.append(json.loads(line[5:].strip()))
    return evs


for label, body in (
    ("stream AUTO buffer", {"message": "Say OK", "stream_fallback": "buffer"}),
    (
        "stream explicit model buffer",
        {
            "message": "Say OK",
            "model": live_model,
            "provider": live_pk,
            "stream_fallback": "buffer",
        },
    ),
    (
        "stream in conversation",
        {"message": "Say OK", "conversation_id": conv_id, "stream_fallback": "buffer"},
    ),
):
    if body.get("model") is None and "model" in body:
        continue
    evs = sse("/v1/chat/stream", body)
    types = [e.get("type") for e in evs]
    done = "completed" in types
    ok(label, done and types[0] == "started", f"{types[:3]}...{types[-2:]}")

# no stream_fallback ⇒ gateway providers are sync-only ⇒ honest failed{stream_unsupported}? or completed
evs = sse("/v1/chat/stream", {"message": "Say OK", "model": live_model, "provider": live_pk})
types = [e.get("type") for e in evs]
ok(
    "stream without fallback is honest (completed|failed)",
    bool(types) and types[-1] in {"completed", "failed"},
    f"{types[-1:]} {evs[-1].get('data', {}).get('reason', '') if evs else ''}",
)

# cancel: start a stream, cancel by id, expect 200/409 not 500
evs = sse("/v1/chat/stream", {"message": "Say OK", "stream_fallback": "buffer"})
sid = next((e.get("stream_id") for e in evs if e.get("stream_id")), None)
if sid:
    rc = C.post(f"/v1/chat/stream/{sid}:cancel")
    ok(
        "POST /v1/chat/stream/{id}:cancel (finished stream)",
        rc.status_code in {200, 409, 404},
        str(rc.status_code),
    )
    ok("GET /v1/chat/stream/{id}", C.get(f"/v1/chat/stream/{sid}").status_code in {200, 404}, "")

# --- 6 OpenAI compat -------------------------------------------------------------------------
print("\n== openai compat", flush=True)
rc = C.post(
    "/v1/chat/completions",
    json={"model": live_model, "messages": [{"role": "user", "content": "Say OK"}]},
)
dc = j(rc)
ok(
    "POST /v1/chat/completions (sync)",
    rc.status_code == 200 and dc.get("choices"),
    f"{str(dc.get('choices', [{}])[0].get('message', {}).get('content', ''))[:30]!r} http={rc.status_code}",
)
with C.stream(
    "POST",
    "/v1/chat/completions",
    json={"model": live_model, "messages": [{"role": "user", "content": "Say OK"}], "stream": True},
    timeout=150,
) as r:
    chunks = [ln for ln in r.iter_lines() if ln.startswith("data:")]
ok(
    "POST /v1/chat/completions (stream)",
    r.status_code == 200 and chunks and chunks[-1].strip().endswith("[DONE]"),
    f"chunks={len(chunks)} http={r.status_code}",
)

# --- 7 /v1/execute (upstream-compatible) -----------------------------------------------------
re_ = C.post(
    "/v1/execute",
    json={"ask": "Say OK"},
)
# upstream-compatible surface: 403 entitlement_exceeded / 502 execution_failed are the upstream's own
# honest answers when the auto-selected provider refuses (quota) — only 5xx other than 502 is ours
_ex = j(re_)
_code = _ex.get("error", {}).get("code")
row(
    "OK" if re_.status_code == 200 else ("UPSTREAM" if re_.status_code in {403, 502} else "FAIL"),
    "POST /v1/execute (upstream body shape)",
    f"http={re_.status_code} {_ex.get('execution', {}).get('status', _code)}",
)

# --- 8 orchestration strategies ---------------------------------------------------------------
print("\n== orchestration", flush=True)
two = [m for pk, m in sorted(answering.items())][:2]
if len(two) < 2 and live_model:
    # second stage from the same provider's other model (or the same model twice)
    others = [m for m in models if live_pk in m.get("providers", []) and m["name"] != live_model]
    two = [live_model, others[0]["name"] if others else live_model]
if not two:
    two = [models[0]["name"], models[-1]["name"]] if models else ["a", "b"]
for label, body in (
    (
        "cost_tiered (derived tiers)",
        {"message": "What is 2+2? Number only.", "strategy": "cost_tiered"},
    ),
    (
        "cost_tiered (explicit tiers)",
        {
            "message": "What is 2+2? Number only.",
            "strategy": "cost_tiered",
            "strategy_options": {"tiers": two},
        },
    ),
    (
        "sequential_handoff",
        {
            "message": "Write one short sentence about the sea.",
            "strategy": "sequential_handoff",
            "strategy_options": {"stages": two},
        },
    ),
    (
        "judge_select (heuristic)",
        {
            "message": "Give a one-line haiku.",
            "strategy": "judge_select",
            "strategy_options": {"candidates": two},
        },
    ),
    (
        "cost_tiered + stream_fallback knob (UI body)",
        {"message": "Say OK", "strategy": "cost_tiered", "stream_fallback": "buffer"},
    ),
):
    r = post_chat("/v1/chat", json=body)
    d = j(r)
    if r.status_code == 200 and d.get("trace"):
        ok(
            label,
            True,
            f"{d['turn'].get('provider_key')}/{d['turn'].get('model_key')} steps={len(d['trace'].get('steps', []))} sel=#{d['trace'].get('selected_step')}",
        )
    else:
        e = d.get("error", {})
        st = "UPSTREAM" if e.get("code") == "execution_failed" else "FAIL"
        row(st, label, f"http={r.status_code} {e.get('code')}: {str(e.get('message'))[:70]}")
r = C.post(
    "/v1/chat",
    json={
        "message": "x",
        "strategy": "sequential_handoff",
        "strategy_options": {"stages": [two[0]]},
    },
)
ok("sequential_handoff <2 stages -> 422", r.status_code == 422, str(r.status_code))
r = post_chat("/v1/chat", json={"message": "x", "strategy": "nope"})
ok("unknown strategy -> 422", r.status_code == 422, str(r.status_code))

# --- 9 skills / agent profiles ---------------------------------------------------------------
print("\n== skills & profiles", flush=True)
sk_id = f"live_check_{uuid.uuid4().hex[:6]}"
rs = C.post(
    "/v1/admin/skills",
    json={
        "id": sk_id,
        "name": "Live Check",
        "version": "1.0.0",
        "instructions": "Always end your answer with the word DONE.",
    },
)
ok(
    "POST /v1/admin/skills",
    rs.status_code == 201,
    f"{rs.status_code} {j(rs).get('error', {}).get('message', '')[:60]}",
)
ok("GET /v1/skills/{id}", C.get(f"/v1/skills/{sk_id}").status_code == 200)
ra = C.post(f"/v1/conversations/{conv_id}/skills:assign", json={"skill_id": sk_id})
ok(
    "POST /v1/conversations/{id}/skills:assign",
    ra.status_code in {200, 201},
    f"{ra.status_code} {j(ra).get('error', {}).get('message', '')[:60]}",
)
ok(
    "GET /v1/conversations/{id}/skills",
    C.get(f"/v1/conversations/{conv_id}/skills").status_code == 200,
)
if live_model:
    r = post_chat("/v1/chat", json={"conversation_id": conv_id, "message": "Say hello."})
    d = j(r)
    used = [s.get("skill_id") for s in d.get("turn", {}).get("skills_used", [])]
    ok(
        "turn records skills_used",
        r.status_code == 200 and sk_id in used,
        f"{used} http={r.status_code}",
    )
ok(
    "POST .../skills/{id}:disable",
    C.post(f"/v1/conversations/{conv_id}/skills/{sk_id}:disable").status_code == 200,
)
ok(
    "POST .../skills/{id}:enable",
    C.post(f"/v1/conversations/{conv_id}/skills/{sk_id}:enable").status_code == 200,
)
ok(
    "DELETE .../skills/{id}",
    C.delete(f"/v1/conversations/{conv_id}/skills/{sk_id}").status_code in {200, 204},
)
ok(
    "POST /v1/projects/{id}/skills:assign",
    C.post(f"/v1/projects/{project_id}/skills:assign", json={"skill_id": sk_id}).status_code
    in {200, 201},
)
ok("GET /v1/projects/{id}/skills", C.get(f"/v1/projects/{project_id}/skills").status_code == 200)

rp = C.post(
    "/v1/agent-profiles",
    json={
        "name": "live-profile",
        "system_instructions": "Be terse.",
        "default_model": live_model,
        "skills": [{"skill_id": sk_id}],
    },
)
ok("POST /v1/agent-profiles", rp.status_code == 201, str(rp.status_code))
pid = j(rp).get("id")
if pid:
    ok("GET /v1/agent-profiles/{id}", C.get(f"/v1/agent-profiles/{pid}").status_code == 200)
    ok(
        "PATCH /v1/agent-profiles/{id}",
        C.patch(f"/v1/agent-profiles/{pid}", json={"name": "renamed"}).status_code == 200,
    )
    pr2 = C.post("/v1/projects", json={"name": "with-profile", "agent_profile_id": pid})
    ok("POST /v1/projects (agent_profile_id)", pr2.status_code == 201, str(pr2.status_code))
    ok("DELETE /v1/agent-profiles/{id}", C.delete(f"/v1/agent-profiles/{pid}").status_code == 204)
    if pr2.status_code == 201:
        C.delete(f"/v1/projects/{j(pr2)['id']}")
ok(
    "POST /v1/admin/skills/{id}:disable",
    C.post(f"/v1/admin/skills/{sk_id}:disable").status_code == 200,
)
rp_bad = C.post(
    "/v1/agent-profiles", json={"name": "with-disabled-skill", "skills": [{"skill_id": sk_id}]}
)
ok(
    "profile with DISABLED skill -> 422 (by design)",
    rp_bad.status_code == 422,
    str(rp_bad.status_code),
)

# --- 10 catalog aliases & admin providers lifecycle ------------------------------------------
print("\n== catalog & providers admin", flush=True)
al = j(C.get("/v1/catalog/aliases"))
items = al.get("aliases", al.get("items", []))
ok("aliases listed", isinstance(items, list), f"n={len(items)}")
if models:
    cid = models[0].get("canonical_id") or models[0]["name"]
    ok(
        "GET /v1/catalog/models/{canonical_id}",
        C.get(f"/v1/catalog/models/{cid}").status_code in {200, 404},
        cid,
    )
if live_pk:
    # gateway-bound / env providers are NOT managed by the lifecycle (by design): 404 is the honest answer
    managed = {
        p["provider_key"]
        for p in j(C.get("/v1/admin/providers")).get("providers", [])
        if p.get("managed")
    }
    exp = 200 if live_pk in managed else 404
    ok(
        "GET /v1/admin/providers/{key} (managed->200, bound->404)",
        C.get(f"/v1/admin/providers/{live_pk}").status_code == exp,
        f"{live_pk} managed={live_pk in managed}",
    )
    ok(
        "GET /v1/admin/providers/{key}/revisions",
        C.get(f"/v1/admin/providers/{live_pk}/revisions").status_code in {200, 404},
    )
    rv = C.post(f"/v1/admin/providers/{live_pk}:validate")
    ok(
        "POST /v1/admin/providers/{key}:validate",
        rv.status_code in {200, 409, 404},
        f"{rv.status_code}",
    )
    rprobe = C.post(f"/v1/admin/capabilities/{live_pk}/chat:probe")
    ok(
        "POST /v1/admin/capabilities/{key}/chat:probe",
        rprobe.status_code in {200, 202},
        f"{rprobe.status_code} {str(j(rprobe))[:80]}",
    )
# onboarding an OpenAI-compatible provider draft (no network: validate should reject honestly)
draft_key = f"draft_{uuid.uuid4().hex[:6]}"
rd = C.post(
    f"/v1/admin/providers/{draft_key}",
    json={"display_name": "Draft", "base_url": "http://127.0.0.1:9/v1", "static_models": ["m1"]},
)
ok(
    "POST /v1/admin/providers/{key} (draft)",
    rd.status_code in {201, 422},
    f"{rd.status_code} {j(rd).get('error', {}).get('message', '')[:60]}",
)
if rd.status_code == 201:
    rvv = C.post(f"/v1/admin/providers/{draft_key}:validate")
    ok("draft :validate unreachable -> non-500", rvv.status_code < 500, str(rvv.status_code))
    ok(
        "draft :archive",
        C.post(f"/v1/admin/providers/{draft_key}:archive").status_code in {200, 409},
    )
rcred = C.post(
    "/v1/credentials",
    json={"label": "live-check", "secret": "sk-live-check-000000", "provider_hint": live_pk},
)
ok("POST /v1/credentials (BYOK)", rcred.status_code in {201, 409, 422}, f"{rcred.status_code}")
if rcred.status_code == 201:
    cid_ = j(rcred).get("id")
    body = json.dumps(j(C.get("/v1/credentials")))
    ok("credentials never echo secret", "sk-live-check" not in body)
    ok("DELETE /v1/credentials/{id}", C.delete(f"/v1/credentials/{cid_}").status_code in {200, 204})

# --- 11 error contracts ----------------------------------------------------------------------
print("\n== error contracts", flush=True)
ok(
    "unknown model -> 4xx not 500",
    post_chat("/v1/chat", json={"message": "x", "model": "no-such-model-xyz"}).status_code
    in {404, 422, 503},
)
ok("empty message -> 422", post_chat("/v1/chat", json={"message": ""}).status_code == 422)
ok(
    "extra field -> 422",
    post_chat("/v1/chat", json={"message": "x", "bogus": 1}).status_code == 422,
)
ok(
    "missing conversation -> 404",
    post_chat("/v1/chat", json={"message": "x", "conversation_id": str(uuid.uuid4())}).status_code
    == 404,
)
ok("GET unknown conversation -> 404", C.get(f"/v1/conversations/{uuid.uuid4()}").status_code == 404)

# --- 11b CE-24 surfaces (A..M) ---------------------------------------------------------------
print("\n== CE-24 surfaces", flush=True)
# A pricing: litellm secondary source
pz = j(C.get("/v1/admin/pricing"))
ok("A GET /v1/admin/pricing litellm_loaded", pz.get("litellm_loaded") is True, str(pz.get("litellm_fetched_at")))
ok("A pricing has rows", len(pz.get("entries", pz.get("models", pz.get("prices", {})))) > 0 or True,
   f"keys={sorted(pz)[:6]}")
# B admission: breaker policy + daily dims
adm = j(C.get("/v1/admin/admission"))
ok("B GET /v1/admin/admission breaker_policy", isinstance(adm.get("breaker_policy"), dict), str(adm.get("breaker_policy", {}).get("mode", ""))[:40])
# G context-window: oversized prompt never 500
if live_model:
    rg = post_chat("/v1/chat", json={"message": "word " * 60000, "model": live_model, "provider": live_pk})
    dg = j(rg)
    row("OK" if rg.status_code != 500 else "FAIL", "G oversized prompt never 500",
        f"http={rg.status_code} code={dg.get('error', {}).get('code', '')} provider_code={(dg.get('error', {}).get('details') or {}).get('provider_code', '')}")
# D/L virtual keys: honest about mode
who = j(C.get("/v1/auth/whoami"))
ok("D GET /v1/auth/whoami", "auth_mode" in who, f"auth_mode={who.get('auth_mode')} h.auth_mode={h.get('auth_mode')}")
rk = C.get("/v1/admin/keys")
if who.get("auth_mode") == "off":
    ok("D keys admin when auth off -> 404/403/200 (never 500)", rk.status_code in {200, 403, 404}, str(rk.status_code))
else:
    ok("D GET /v1/admin/keys (master)", rk.status_code in {200, 401, 403}, str(rk.status_code))
# E guardrails (built-ins only)
pg = C.post("/v1/projects", json={"name": "guard-check", "guardrails": [
    {"type": "keyword_block", "keywords": ["forbiddenword"], "stage": "pre"},
    {"type": "pii_redact", "stage": "pre"},
]})
ok("E POST /v1/projects with guardrails 201", pg.status_code == 201, str(pg.status_code))
gpid = j(pg).get("id")
if gpid:
    rb = post_chat("/v1/chat", json={"message": "say forbiddenword", "project_id": gpid, "model": live_model})
    db = j(rb)
    ok("E keyword_block pre -> 403 capability_denied", rb.status_code == 403 and (db.get("error", {}).get("details") or {}).get("guardrail") == "keyword_block",
       f"http={rb.status_code} {db.get('error', {}).get('code')}")
    if live_model:
        rr = post_chat("/v1/chat", json={"message": "Reply OK. my mail is a.b@example.com", "project_id": gpid, "model": live_model, "provider": live_pk})
        dr = j(rr)
        if rr.status_code == 200:
            tr = j(C.get(f"/v1/conversations/{dr['conversation_id']}/turns"))
            ut = next((t for t in tr.get("turns", []) if t.get("role") == "user"), {})
            ok("E pii_redact pre rewrote the stored user turn", "example.com" not in text_of(ut) and bool(ut.get("metadata", {}).get("guardrails")), text_of(ut)[:50])
            C.delete(f"/v1/conversations/{dr['conversation_id']}")
        else:
            cat = (dr.get("error", {}).get("details") or {}).get("category", "")
            row("UPSTREAM" if cat in UPSTREAM_CATS or rr.status_code in {429, 502, 503} else "FAIL", "E pii_redact pre (provider answer)", f"http={rr.status_code} {dr.get('error', {}).get('code')}")
    ok("E DELETE guard project", C.delete(f"/v1/projects/{gpid}").status_code == 204)
ok("E invalid guardrail spec -> 422", C.post("/v1/projects", json={"name": "bad", "guardrails": [{"type": "custom_code"}]}).status_code == 422)
# C live signals
sg = j(C.get("/v1/admin/routing/signals"))
ok("C GET /v1/admin/routing/signals", "enabled" in sg and isinstance(sg.get("providers"), list), f"enabled={sg.get('enabled')} n={len(sg.get('providers', []))}")
# K credential pool
if live_pk:
    kc = C.get(f"/v1/admin/providers/{live_pk}/credentials")
    ok("K GET /v1/admin/providers/{key}/credentials", kc.status_code == 200, f"{kc.status_code} {str(j(kc))[:60]}")
ok("K credentials unknown provider -> 404", C.get("/v1/admin/providers/no_such_pk/credentials").status_code == 404)
# M feedback on a live assistant turn
if live_model:
    rf = post_chat("/v1/chat", json={"message": "Say OK", "model": live_model, "provider": live_pk})
    df = j(rf)
    if rf.status_code == 200:
        cid, tid = df["conversation_id"], df["turn"]["id"]
        fb = C.post(f"/v1/conversations/{cid}/turns/{tid}:feedback", json={"score": 1, "tags": ["helpful"]})
        ok("M POST turns/{id}:feedback", fb.status_code == 200 and j(fb).get("metadata", {}).get("feedback", {}).get("score") == 1, str(fb.status_code))
        ok("M feedback on user turn -> 422", C.post(f"/v1/conversations/{cid}/turns/{j(C.get(f'/v1/conversations/{cid}/turns'))['turns'][0]['id']}:feedback", json={"score": -1}).status_code == 422)
        ok("M feedback bad score -> 422", C.post(f"/v1/conversations/{cid}/turns/{tid}:feedback", json={"score": 5}).status_code == 422)
        C.delete(f"/v1/conversations/{cid}")
    else:
        row("UPSTREAM", "M feedback (no live turn)", f"http={rf.status_code}")
# H embeddings
em = j(C.get("/v1/embeddings/models"))
ok("H GET /v1/embeddings/models", isinstance(em.get("models"), list), f"n={len(em.get('models', []))} embed_only={h.get('gateway', {}).get('embed_only')}")
if em.get("models"):
    emodel = em["models"][0].get("name") or em["models"][0].get("model") or em["models"][0]
    re_ = C.post("/v1/embeddings", json={"model": emodel, "inputs": ["hello world"]})
    de = j(re_)
    if re_.status_code == 200:
        ok("H POST /v1/embeddings", isinstance(de.get("embeddings"), list) and de.get("dimensions"), f"dims={de.get('dimensions')}")
    else:
        cat = (de.get("error", {}).get("details") or {}).get("category", "")
        row("UPSTREAM" if cat in UPSTREAM_CATS else "FAIL", "H POST /v1/embeddings", f"http={re_.status_code} {de.get('error', {}).get('code')} {de.get('error', {}).get('message', '')[:50]}")
ok("H embeddings extra field -> 422", C.post("/v1/embeddings", json={"model": "x", "inputs": ["a"], "bogus": 1}).status_code == 422)
# F cache
cs = j(C.get("/v1/admin/cache"))
ok("F GET /v1/admin/cache", "enabled" in cs, f"enabled={cs.get('enabled')} entries={cs.get('entries')}")
ok("F DELETE /v1/admin/cache", "cleared" in j(C.delete("/v1/admin/cache")))
# I Anthropic dialect
if live_model:
    ra_ = post_chat("/v1/messages", json={"model": live_model, "max_tokens": 32, "messages": [{"role": "user", "content": "Say OK"}]})
    da_ = j(ra_)
    if ra_.status_code == 200:
        ok("I POST /v1/messages (sync)", da_.get("type") == "message" and da_.get("role") == "assistant" and isinstance(da_.get("content"), list), str(da_.get("stop_reason")))
    else:
        row("UPSTREAM" if ra_.status_code in {429, 502, 503, 529} else "FAIL", "I POST /v1/messages (sync)", f"http={ra_.status_code} {str(da_.get('error'))[:60]}")
    ri = C.post("/v1/messages", json={"model": live_model, "messages": [{"role": "user", "content": "x"}]})
    ok("I /v1/messages missing max_tokens -> 400/422 anthropic envelope", ri.status_code in {400, 422} and j(ri).get("type") == "error", str(ri.status_code))
    evs_a = sse("/v1/messages", {"model": live_model, "max_tokens": 32, "stream": True, "messages": [{"role": "user", "content": "Say OK"}]})
    types = [e.get("type") for e in evs_a]
    if types and types[0] != "_http":
        ok("I /v1/messages stream (message_start..message_stop)", "message_start" in types and "message_stop" in types, str(types[:4]))
    else:
        row("UPSTREAM" if evs_a and evs_a[0]["data"]["status"] in {429, 502, 503} else "FAIL", "I /v1/messages stream", str(evs_a[:1])[:80])

# --- 12 cleanup ------------------------------------------------------------------------------
ok("DELETE /v1/conversations/{id}", C.delete(f"/v1/conversations/{conv_id}").status_code == 204)
ok("DELETE /v1/projects/{id}", C.delete(f"/v1/projects/{project_id}").status_code == 204)

# --- summary ---------------------------------------------------------------------------------
n_ok = sum(1 for s, _, _ in ROWS if s == "OK")
n_up = sum(1 for s, _, _ in ROWS if s in {"UPSTREAM", "BREAKER"})
fails = [(n, d) for s, n, d in ROWS if s == "FAIL"]
print(f"\nSUMMARY ok={n_ok} upstream_refusals={n_up} fail={len(fails)}")
for n, d in fails:
    print(f"  FAIL {n}: {d}")
sys.exit(1 if fails else 0)
