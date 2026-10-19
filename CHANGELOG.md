# CHANGELOG — chat_engine

## v0.1.3 — 2026-10-18 (CE-21 Live full-function audit against the gateway)

- `reference_client/`: single-file Python reference (one function per request, 72 endpoints, stdlib only) with the endpoint URL externalized to `reference_client/config/endpoint.json`; live tour evidence `evidence/ce20/reference_client_tour.txt`.
- Docs: `docs/INTEGRATION_GUIDE.md` — complete integration reference for engineers and coding agents (every request/response shape, SSE protocol, memory, strategies, skills, error playbook, gateway facts, SDKs, app recipe); all examples executed live.

Owner-requested sweep of every function and every provider on the live stack; defects found and fixed:

- **Admission breaker scope** (`chat_engine/admission/memory.py`): the relative circuit breaker tripped on the *tenant* scope after five upstream refusals from *different* providers and 429'd every provider for 30 s — healthy ones included. The breaker is a provider-health signal: `tenant*`/`project*` scopes are exempt (`test_breaker_never_opens_on_tenant_scope`).
- **Cancel during buffered fallback** (`chat_engine/streaming/service.py`, UI): gateway providers are sync-only; a user cancel used to block until the provider answered and the bubble stayed `streaming`. The server now races `generate` against the cancel event (terminal `cancelled`, no failover), the UI aborts the fetch and settles the bubble (`test_case5b_…`).
- **Sync `/v1/chat` body parity**: accepts the stream-only `stream_fallback` knob (was 422 for every strategy request from the UI).
- Harnesses: `scripts/live_full_check.py` (every API surface + provider × model matrix, `OK / UPSTREAM / BREAKER / FAIL`), `tests/ui/e2e_playwright.py` (20 real user flows). Evidence under `evidence/ce20/`.
- `scripts/sandbox_run.sh`: gateway launched with `setsid`, hard failure when it does not come up (was silently starting chat_engine with 0 bound routes).

## v0.1.2 — 2026-10-17 (CE-20 Remote Gateway Providers)

- `chat_engine/providers/gateway.py`: bind providers behind the owner's Remote Provider Gateway (ADR-0008) from `CE_GATEWAY_*` using upstream `RemoteGatewayAdapter`; `/v1/describe`-driven discovery; `/healthz.gateway` bound/skipped report.
- Routing: AUTO uses `max_escalation` failover; buffered stream fallback fails over across candidates; tenant admission ceiling configurable (`CE_TENANT_RPM`, `CE_TENANT_CONCURRENCY`).
- Catalog: models.dev `context: 0` treated as unknown (CanonicalModel `ge=1`).
- UI: `stream_fallback=buffer`; failure detail shown next to the status pill.
- Contracts/UI: sync `POST /v1/chat` accepts (and ignores) the stream-only `stream_fallback` knob so one client body serves both paths; the UI now sends it only on the stream path — fixes the 422 that broke `cost_tiered` from the UI against the live gateway (Playwright 12/12 live). `__version__` synced to `0.1.2`.
- Scripts: `sandbox_bootstrap.sh`, `sandbox_run.sh`, `sandbox_up.sh` (secrets generated outside the repo).

## v0.1.1 — 2026-10-16 (UI visual identity refresh)

- `ui_standalone/`: new visual identity adapted from the owner's reference (dark-first indigo→cyan accent, glass surfaces, Tajawal/Inter/IBM Plex Mono, light theme, collapsible sidebar, welcome hero, avatar message rows, pill composer). Original implementation; no reference assets copied. All Playwright hooks preserved (12/12), gate green.

## v0.1.0 — 2026-10-16 (Phase 1 FOUNDATION)

Standalone chat engine composed from `general_ai_core @ 0a13556` (SHA-pinned git dependency, additive-only; import-linter forbids `apps.*`/`ui/`).

### Compatibility (CE-01/02)
- Byte-compatible upstream surfaces: `/v1/execute`, `/v1/executions*`, `/v1/models`, `/v1/events`, `/v1/skills`, `/healthz` (14 golden execute snapshots, models, six lifecycle SSE events, 14 manifests, 117-contract freeze baseline).

### Engine
- **HandoffPacket v1 + compiler** (CE-03): canonical messages/parts, budget-aware truncation, tool results, output spec, skills.
- **Canonical model identity + CatalogIndex** (CE-04): `CanonicalModel`/`ModelAlias{confirmed|proposed|rejected}`, O(1) reads, 10k providers in ~285 ms.
- **OpenAI-compatible provider adapter + lifecycle + BYOK** (CE-05): draft→validate→verify→enable/disable/archive/rollback with append-only revisions; `/v1/credentials` returns fingerprint+last4 only.
- **Capability ledger** (CE-06): `declared ∧ verified ∧ enabled ∧ available_now`; probes; `unsupported_capability`.
- **Admission** (CE-07): in-memory + Redis (Lua) rpm/tpm/concurrency/budget, breaker, idempotency, `admission_denied` 429.
- **Streaming** (CE-08): optional `StreamingAdapterPort`, tolerant SSE parser, five-case semantics (supported / unsupported→422 or buffered `synthesized:true` / silent failover before first delta / `stream_interrupted` resumable without re-send / cancel).
- **Conversations, projects, turns, summarization** (CE-09): `/v1/projects`, `/v1/conversations(/turns)`, `POST /v1/chat`; summary turn as cut point; stream persists complete/partial/failed turns.
- **OpenAI-compatible API** (CE-10): `POST /v1/chat/completions` (sync+stream+tools+usage), `/v1/openai/models`; official `openai` SDK passes hermetically; ADR-CE-0002 error codes; public OpenAPI with `x-ce-stability`.
- **SDKs** (CE-11): `sdk/python/chat_engine_client` (sync/async/SSE/retry), `sdk/typescript` (zero-dep), `examples/consumer_project`.
- **Orchestration** (CE-12): `sequential_handoff`, `judge_select` (output_spec heuristic + optional LLM judge), `cost_tiered` (≤1 upgrade); explainable `OrchestrationTrace` persisted on turns.
- **Standalone UI** (CE-13): Vite+Lit+TS, original tokens, ar/en RTL, 7 screens, served at `/`; `ui_no_copy_guard` in gate; Playwright smoke.
- **App Factory** (CE-14): spec→scaffold→review as artifacts, approval gate, async jobs; no code execution.
- **Usage, cost, observability** (CE-15): one `UsageRecord` per request, models.dev pricing (sourced+dated) + overrides, `GET /v1/usage`, redaction, JSON logs, optional OTel (no-op without endpoint).
- **Packaging** (CE-16): Dockerfile (non-root, UI embedded), compose, GitHub Actions (ubuntu+windows), `dev.sh`/`dev.ps1`/`gate.ps1`, `docs/OPERATIONS.md`.
- **Assignable Skills** (CE-19): agent profiles, assignment scopes (profile→project→conversation→explicit), enable/disable, version recorded per turn, upstream-shaped `/v1/skills` + assignment API.

### Known limits (honest)
- All stores in-memory (PostgreSQL `ce_*` deferred, ADR-CE-0001 D10).
- No authentication in Phase 1 (demo tenant/user).
- Live-provider paths marked `UNVERIFIED (no live key)` unless probed with an owner key (see `docs/OPERATIONS.md` §5).
- `[otel]` extra not added (no dependency without need); tracer is a no-op without it.
