# CHAT_ENGINE_STATE — the ONE mutable state file of chat_engen

```text
STATE_VERSION: 2
PHASE: 1 — FOUNDATION (owner approved full plan 2026-10-15; autonomous execution CE-01..CE-18 + CE-19 Skills)
CURRENT_TASK: CE-12 Multi-model orchestration strategies
CURRENT_TASK_STATUS: IN_PROGRESS
OWNER_APPROVAL: YES — 2026-10-15 (full approval + delegation: no intermediate gates; deliver preview URL only at completion; added requirement: Assignable Skills → CE-19)
UPSTREAM_REF: general_ai_core @ 0a13556305bcf40cbf1b750d7ef1d81f7850cabf
LAST_TRUSTED_COMMIT: (see ledger — updated per checkpoint)
NEXT_TASK: CE-12 sequential_handoff / judge_select / cost_tiered on /v1/chat with trace
```

## What the owner must approve (gate for CE-01)
1. `CHAT_ENGINE_REALITY_AUDIT.md` — findings + P0/P1/P2 gaps
2. `docs/adr/ADR-CE-0001-architecture-lock.md` — D0–D10 (PROPOSED → ACCEPTED on approval)
3. `CHAT_ENGINE_REFERENCE_ASSESSMENT.md` — external repos: licenses, patterns, decisions
4. `CHAT_ENGINE_TASKS.md` — CE-00..CE-18 plan

Approval may be partial (e.g. "approve all except D9 → use React"): record the delta here and in the ADR before CE-01.

## Resume protocol (owner says "تابع")
1. `git status && git rev-parse HEAD && git log --oneline -5 && git diff --stat`
2. read this file; read `README.md`
3. uncommitted files = Recovery Candidates: inspect, classify, never delete blindly
4. restore git credentials + reference venv per `README.md` (sandbox resets wipe `.venv`, `/home/user/ref`, uncommitted files — 4 resets so far)
5. continue ONLY `CURRENT_TASK`; one micro-task per unit; verify; commit; **push immediately**
6. update this file at a verified checkpoint only; record exactly one `NEXT_TASK`
7. owner delegated autonomous execution: do NOT stop for approvals; only stop on a real blocker; final message = preview URL only

## Ledger
| # | unit | commit | evidence | result |
|---|------|--------|----------|--------|
| 1 | bootstrap state file + .gitignore | 5c6a2eb | — | pushed |
| 2 | scale probe 1k/10k, `apps.cli describe/routes`, upstream freeze baseline @0a13556 | c9fe22f | `evidence/ce00/scale_probe_output_0a13556.txt`, `cli_*_0a13556.json`, `upstream_contract_freeze_baseline_0a13556.json` | N=10k: explicit p95 284ms, AUTO p50 664ms, catalog 404ms |
| 3 | live local smoke of upstream API (hermetic, no keys) | cd3f862 | `evidence/ce00/live_local_smoke_0a13556.txt` | 12 probes; `stream=true` ⇒ 422 confirmed |
| 4 | `CHAT_ENGINE_REALITY_AUDIT.md` | 4f7b004 | §1–§11 | 6×P0, 6×P1, 5×P2 gaps |
| 5 | `docs/adr/ADR-CE-0001-architecture-lock.md` | 3e3beae | D0–D10 | PROPOSED |
| 6 | `CHAT_ENGINE_REFERENCE_ASSESSMENT.md` | 4598d4e | 7 repos, licenses, HEAD SHAs | 0 new runtime deps; Helicone GPL ⇒ no code |
| 7 | `CHAT_ENGINE_TASKS.md` CE-00..CE-18 | 581d917 | dependency map + per-task template | PROPOSED |
| 8 | `README.md` permanent operating contract + resume protocol | 593baf9 | — | pushed |
| 9 | complete hermetic pytest evidence (replaces truncated file) | b40fdf1 | `evidence/ce00/pytest_hermetic_core_slices_0a13556.txt` | **1656 passed, 15 skipped, EXIT=0** |
| 10 | state update: CE-00 COMPLETE, awaiting approval | 47dfad5 | — | gate closed until YES |
| 11 | owner approval recorded; ADR-CE-0001 ACCEPTED | 120821b | — | phase 1 |
| 12 | CE-01: baseline copy (sha256 ==), 14 golden execute + models + events, 14 manifests, COMPATIBILITY_MAP | fe13617 | `evidence/ce01/` | DONE |
| 13 | CE-02: skeleton, pyproject (SHA pin), compat deriver + 3 guards, arch guard, secret scan | 9374cec | — | pushed |
| 24 | CE-11: `sdk/python/chat_engine_client` (sync+async, SSE parser, retry honouring `Retry-After`/`retry_after_ms`, typed views, httpx-only) + `sdk/typescript` (zero-dep fetch+SSE, `tsc --noEmit` clean) + `examples/consumer_project` (multi-turn, stream, OpenAI-compat, R1 core-name check; imports nothing from apps/ui) | this | `evidence/ce11/{gate,tsc}.txt` | **GATE GREEN: 122 passed** |
| 23 | CE-10: OpenAI-compatible `POST /v1/chat/completions` (sync+stream, tools, usage, OpenAI error envelope) + `/v1/openai/models`; `CE_FEATURE_OPENAI_COMPAT` flag; public OpenAPI with `x-ce-stability` per op + `x-ce-upstream-sha`; ADR-CE-0002 (error codes/mapping/downgrade) + `docs/API.md`; official `openai` SDK (test-only extra `test-openai`, MIT) passes sync/stream/errors hermetically | this | `evidence/ce10/gate.txt` | **GATE GREEN: 116 passed** |
| 21 | CE-09: Project/Conversation/Turn contracts + ConversationStorePort (in-memory) + ChatService (turn → HandoffPacket → ONE router/execution, admission, capability gate, failed-turn recording) + Summarizer (model via cost-first weights; extractive fallback labelled `summary_source=extractive`) + `/v1/projects`, `/v1/conversations` (+`/turns`), `POST /v1/chat`; `/v1/chat/stream` accepts `conversation_id`+`message` and persists complete/partial/failed turns; CE-19 hook `extensions["skills"].resolve_for_turn` | 1d7340a..this | `evidence/ce09/gate.txt` (40-turn summarization test) | **GATE GREEN: 102 passed** |
| 22 | CE-04 perf re-measure after a2a7913: catalog `entries()` @10k p50 **284.5 ms** (was 17,270 ms) | this | `evidence/ce04/scale_10k.txt` | budget 1,500 ms MET |
| 20 | CE-08: StreamingAdapterPort (optional) + tolerant SSE parser + OpenAICompatibleAdapter.stream() + StreamService (five D7 cases: supported / unsupported→422 or buffer `synthesized:true` / fail-before-first-delta→silent failover / fail-after→`stream_interrupted` resumable, no re-send / cancel) + `/v1/chat/stream` SSE, :cancel, :resume, summary; streaming_text must be probe-verified in ledger | 9e16fcc..this | `evidence/ce08/gate_output.txt` | **GATE GREEN: 91 passed** |
| 19 | CE-07: AdmissionPort — InMemoryAdmission (rpm/tpm/concurrency/budget, all-or-nothing scopes, relative breaker 5/10, jitter) + RedisAdmission (atomic Lua) + IdempotencyStore; `/v1/execute` reserve→settle/release; 429 mapping; `/v1/admin/admission`; healthz `replicas_safe` | e0b7905..be1905b | tests/admission (200-worker storm, fake clock) | DONE |
| 18 | CE-06: CapabilityLedger (declared ∧ verified ∧ enabled ∧ available_now; TTL stale; auto-enable on pass) + 7 probes (daily cap) + gate in `/v1/execute` → `capability_denied` + `/v1/admin/capabilities/*` | ae1271c.. | tests/capabilities | DONE |
| 17 | CE-05: OpenAICompatibleAdapter (full port, 12-category normalization, MockTransport tests), ProviderLifecycleService (append-only revisions draft→validating→discovered→verified→enabled/disabled/archived, rollback = new revision), BYOK `/v1/credentials` (secret in once → ref), `/v1/admin/providers/*`; e2e: key→draft→validate→verify→enable→routed `/v1/execute`→disable→rollback, zero secret leakage | c3e712b..0fea875 | tests/providers | DONE |
| 16 | CE-04: CanonicalModel/ModelAlias (confirmed/proposed/rejected), CatalogIndex O(1), CatalogService resolver → upstream explicit policies, `/v1/catalog/*`, models.dev snapshot (230 providers / 8,894 models; proposals only). D1 golden: 1000 providers → 1 row counter 1000 | 997e0ed..8788903 | `evidence/ce04/scale_10k.txt` | DONE |
| 15 | CE-03: HandoffPacket v1 + HandoffCompiler (system block w/ objective/constraints/decisions, tool_results injection, oldest-first truncation, legacy ComposedContext bridge) wired into /v1/execute as payload["messages"] (additive; goldens unchanged); mypy now follows real upstream types | db14991 + this | `evidence/ce03/gate_output.txt` | **GATE GREEN: 45 passed** |
| 14 | CE-02: composition runtime (core.* only), /v1/execute compat, /v1/models, /v1/executions(+events SSE), healthz, entrypoint | fb2ce90 + this | `evidence/ce02/gate_output.txt` | **GATE GREEN: 38 passed; 14/14 golden replay; import-linter 2 kept** |

## Decisions log (dependencies / deviations)
| date | decision | why | rejected alternative |
|---|---|---|---|
| 2026-10-15 | consume upstream as git dependency pinned to SHA (D0) | upstream is not namespaced; vendoring = unmaintainable fork | rename upstream (breaking); subtree |
| 2026-10-15 | `openai` (MIT) added as TEST-ONLY extra `test-openai` (CE-10) — never a runtime dep; alternative rejected: hand-rolled client (would not prove SDK compatibility) | proves stock-SDK compat | runtime dep |
| 2026-10-15 | 0 new runtime dependencies in Phase 0/1 | rule: no dependency without proof | litellm/portkey as deps |
