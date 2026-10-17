# CHANGELOG — chat_engine

## v0.1.2 — 2026-10-17 (CE-20 Remote Gateway Providers)

- `chat_engine/providers/gateway.py`: bind providers behind the owner's Remote Provider Gateway (ADR-0008) from `CE_GATEWAY_*` using upstream `RemoteGatewayAdapter`; `/v1/describe`-driven discovery; `/healthz.gateway` bound/skipped report.
- Routing: AUTO uses `max_escalation` failover; buffered stream fallback fails over across candidates; tenant admission ceiling configurable (`CE_TENANT_RPM`, `CE_TENANT_CONCURRENCY`).
- Catalog: models.dev `context: 0` treated as unknown (CanonicalModel `ge=1`).
- UI: `stream_fallback=buffer`; failure detail shown next to the status pill.
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
