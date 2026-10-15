# CHAT_ENGINE_STATE — the ONE mutable state file of chat_engen

```text
STATE_VERSION: 2
PHASE: 1 — FOUNDATION (owner approved full plan 2026-10-15; autonomous execution CE-01..CE-18 + CE-19 Skills)
CURRENT_TASK: CE-04 Canonical Model Identity & Catalog Index
CURRENT_TASK_STATUS: IN_PROGRESS
OWNER_APPROVAL: YES — 2026-10-15 (full approval + delegation: no intermediate gates; deliver preview URL only at completion; added requirement: Assignable Skills → CE-19)
UPSTREAM_REF: general_ai_core @ 0a13556305bcf40cbf1b750d7ef1d81f7850cabf
LAST_TRUSTED_COMMIT: (see ledger — updated per checkpoint)
NEXT_TASK: CE-04 CanonicalModel/ModelAlias/CatalogIndex/Resolver + /v1/catalog/models
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
| 15 | CE-03: HandoffPacket v1 + HandoffCompiler (system block w/ objective/constraints/decisions, tool_results injection, oldest-first truncation, legacy ComposedContext bridge) wired into /v1/execute as payload["messages"] (additive; goldens unchanged); mypy now follows real upstream types | db14991 + this | `evidence/ce03/gate_output.txt` | **GATE GREEN: 45 passed** |
| 14 | CE-02: composition runtime (core.* only), /v1/execute compat, /v1/models, /v1/executions(+events SSE), healthz, entrypoint | fb2ce90 + this | `evidence/ce02/gate_output.txt` | **GATE GREEN: 38 passed; 14/14 golden replay; import-linter 2 kept** |

## Decisions log (dependencies / deviations)
| date | decision | why | rejected alternative |
|---|---|---|---|
| 2026-10-15 | consume upstream as git dependency pinned to SHA (D0) | upstream is not namespaced; vendoring = unmaintainable fork | rename upstream (breaking); subtree |
| 2026-10-15 | 0 new runtime dependencies in Phase 0/1 | rule: no dependency without proof | litellm/portkey as deps |
