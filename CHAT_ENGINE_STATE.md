# CHAT_ENGINE_STATE — the ONE mutable state file of chat_engen

```text
STATE_VERSION: 1
PHASE: 0 — AUDIT (no product code allowed until OWNER_APPROVAL = YES)
CURRENT_TASK: CE-00 Reality Audit & Gate Check
CURRENT_TASK_STATUS: IN_PROGRESS
OWNER_APPROVAL: PENDING   (owner writes approval in chat → set YES + date here)
UPSTREAM_REF: general_ai_core @ 0a13556305bcf40cbf1b750d7ef1d81f7850cabf
LAST_TRUSTED_COMMIT: (filled after first push)
NEXT_TASK: finish CE-00 deliverables → wait for owner approval → CE-01
```

## Resume protocol (owner says "تابع")
1. `git status && git rev-parse HEAD && git log --oneline -5 && git diff --stat`
2. read this file; read `README.md`
3. uncommitted files = Recovery Candidates: inspect, classify, never delete blindly
4. continue ONLY `CURRENT_TASK`; one micro-task per unit; verify; commit; **push immediately** (sandbox resets are real — 2 happened during CE-00)
5. update this file at a verified checkpoint only; record exactly one `NEXT_TASK`

## Ledger
| # | unit | commit | evidence | result |
|---|------|--------|----------|--------|
