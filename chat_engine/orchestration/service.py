"""CE-12 orchestration strategies composed over ChatService.route + ExecutionService.

Every step is ONE routed call through the same pipeline a plain turn uses
(admission → router → ExecutionService), so nothing here bypasses upstream
governance. The trace explains which model ran, why, and what it cost.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.contracts.base import JsonObject
from core.contracts.provider import ProviderOperation

from chat_engine.conversations.service import ChatError
from chat_engine.orchestration.contracts import (
    OrchestrationTrace,
    OutputSpec,
    StepResult,
    StepTrace,
    Strategy,
    StrategyOptions,
    TraceBuilder,
)

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime
    from chat_engine.conversations.service import ChatService

PREVIEW = 160
CONTINUE_PROMPT = "Continue and improve the previous draft. Keep what is correct; fix what is not."


def score_against_spec(text: str, spec: OutputSpec) -> tuple[float, list[str]]:
    """1.0 = every criterion met; each failure subtracts proportionally."""
    checks: list[tuple[str, bool]] = []
    if spec.min_chars is not None:
        checks.append((f"min_chars:{spec.min_chars}", len(text) >= spec.min_chars))
    if spec.max_chars is not None:
        checks.append((f"max_chars:{spec.max_chars}", len(text) <= spec.max_chars))
    low = text.lower()
    for s in spec.must_include:
        checks.append((f"must_include:{s}", s.lower() in low))
    for s in spec.must_not_include:
        checks.append((f"must_not_include:{s}", s.lower() not in low))
    if spec.json_object:
        try:
            ok = isinstance(json.loads(text), dict)
        except ValueError:
            ok = False
        checks.append(("json_object", ok))
    if not checks:
        return 1.0, []
    failures = [name for name, passed in checks if not passed]
    return 1.0 - len(failures) / len(checks), failures


class Orchestrator:
    def __init__(self, rt: Runtime, chat: ChatService) -> None:
        self._rt = rt
        self._chat = chat

    # -- one governed step ---------------------------------------------------------------------

    async def run_step(
        self,
        *,
        model: str,
        provider: str | None,
        messages: list[JsonObject],
        ask: str,
        packet_id: str,
        conversation_id: Any = None,
        generation: JsonObject | None = None,
    ) -> StepResult:
        decision = self._chat.route(model, provider, [])
        index = self._rt.extensions["catalog_index"]
        pk = index.provider_key(decision.selected.provider_id) or "?"
        row = self._rt.models.get_by_id(decision.selected.model_id)
        admission = self._rt.extensions.get("admission")
        reservation = None
        if admission is not None:
            from chat_engine.admission import Denied

            est = sum(len(str(m.get("content", ""))) for m in messages) // 4
            got = await admission.reserve(
                [f"tenant:{self._rt.tenant_id}", f"provider:{pk}"], tokens_estimate=est, units=1.0
            )
            if isinstance(got, Denied):
                raise ChatError(
                    "admission_denied",
                    "Admission denied for this orchestration step.",
                    http_status=429,
                    details={
                        "scope": got.scope,
                        "dimension": got.dimension.value,
                        "retry_after_ms": got.retry_after_ms,
                        "step_model": model,
                    },
                )
            reservation = got
        payload: JsonObject = {"ask": ask, "messages": messages, "handoff_packet_id": packet_id}
        if generation:
            payload["generation"] = dict(generation)
        try:
            report = await self._rt.execution.execute_single(
                tenant_id=self._rt.tenant_id,
                user_id=self._rt.user_id,
                decision=decision,
                operation=ProviderOperation.GENERATE_TEXT,
                payload=payload,
                request_hash=f"orch:{packet_id}:{uuid4().hex[:8]}",
                conversation_id=conversation_id,
            )
        except Exception:
            if admission is not None and reservation is not None:
                await admission.release(reservation)
            raise
        node = report.nodes[-1] if report.nodes else None
        resp = node.response if node else None
        usage = dict(resp.usage) if resp else {}
        ok = report.execution.status.value == "succeeded" and resp is not None and resp.succeeded
        if admission is not None and reservation is not None:
            total = usage.get("total_tokens")
            await admission.settle(
                reservation, tokens_actual=total if isinstance(total, int) else None, success=ok
            )
        self._rt.executions.put(report)
        recorder = self._rt.extensions.get("usage_recorder")
        if recorder is not None:
            recorder.record(
                request_id=str(report.execution.id),
                tenant_id=self._rt.tenant_id,
                surface="orchestration",
                status="succeeded" if ok else "failed",
                provider_key=pk,
                model_key=row.model_key,
                usage=usage,
                latency_ms=resp.latency_ms if resp else None,
                conversation_id=conversation_id if isinstance(conversation_id, UUID) else None,
                fallback_chars=sum(len(str(m.get("content", ""))) for m in messages),
                metadata={"packet_id": packet_id, "step_model": model},
            )
        if not ok or resp is None:
            err = resp.error if resp else None
            node_err = dict(node.node.error) if node and isinstance(node.node.error, dict) else {}
            category = err.category.value if err else node_err.get("category")
            message = (
                err.safe_message
                if err
                else str(node_err.get("safe_message") or "Execution failed.")
            )
            raise ChatError(
                "execution_failed",
                message,
                http_status=502,
                details={
                    "execution_id": str(report.execution.id),
                    "step_model": model,
                    "provider_error_category": category,
                    "provider_code": node_err.get("provider_code"),
                },
            )
        # the provider/model that actually answered (router fallback may differ from selected)
        winning = next((a for a in reversed(node.attempts) if a.succeeded), None) if node else None
        if winning is not None:
            pk = index.provider_key(winning.candidate.provider_id) or pk
            row = self._rt.models.get_by_id(winning.candidate.model_id)
        content = resp.output.get("content")
        text = content if isinstance(content, str) else json.dumps(resp.output, sort_keys=True)
        catalog = self._rt.extensions["catalog"]
        ids = [
            a.canonical_id
            for a in catalog.list_aliases(alias=row.model_key)
            if a.source.value == "system"
        ]
        return StepResult(
            text=text,
            provider_key=pk,
            model_key=row.model_key,
            canonical_id=ids[0] if ids else row.model_key,
            execution_id=str(report.execution.id),
            usage=usage,
            latency_ms=resp.latency_ms,
            cost_score=row.cost_score,
            raw_output=dict(resp.output),
        )

    # -- helpers -------------------------------------------------------------------------------

    @staticmethod
    def _step(
        n: int, role: str, model: str, r: StepResult | None, *, reason: str | None = None, **kw: Any
    ) -> StepTrace:
        if r is None:
            return StepTrace(step=n, role=role, model=model, status="failed", reason=reason, **kw)
        return StepTrace(
            step=n,
            role=role,
            model=model,
            provider_key=r.provider_key,
            model_key=r.model_key,
            execution_id=r.execution_id,
            status="succeeded",
            latency_ms=r.latency_ms,
            usage=r.usage,
            cost_score=r.cost_score,
            reason=reason,
            output_preview=r.text[:PREVIEW],
            **kw,
        )

    @staticmethod
    def _accumulate(trace: TraceBuilder, r: StepResult) -> None:
        for k, v in r.usage.items():
            if isinstance(v, int | float):
                trace.total_usage[k] = float(trace.total_usage.get(k, 0)) + v
        if r.cost_score is not None:
            trace.total_cost_score += r.cost_score

    @staticmethod
    def _with_system(messages: list[JsonObject], extra: str) -> list[JsonObject]:
        msgs = [dict(m) for m in messages]
        if msgs and msgs[0].get("role") == "system":
            msgs[0]["content"] = f"{msgs[0]['content']}\n\n{extra}"
        else:
            msgs.insert(0, {"role": "system", "content": extra})
        return msgs

    def _cheapest_first(self, n: int = 2) -> list[str]:
        """[cheap -> better] from registered models the router can place RIGHT NOW
        (upstream: higher cost_score = cheaper). Models in cooldown / without an eligible
        provider are skipped, and tiers are spread across DIFFERENT providers first so an
        outage or plan refusal on one provider never exhausts every tier."""
        rows = sorted(
            self._rt.models.all_models(),
            key=lambda m: (-(m.cost_score or 0.0), -(m.quality_score or 0.0), m.model_key),
        )
        index = self._rt.extensions["catalog_index"]
        placed: list[tuple[str, str]] = []  # (provider_key, model_key)
        for m in rows:
            try:
                d = self._chat.route(m.model_key, None, [])
            except ChatError:
                continue
            placed.append((index.provider_key(d.selected.provider_id) or "?", m.model_key))
        out: list[str] = []
        seen_providers: set[str] = set()
        for pk, mk in placed:  # first pass: one model per provider (diversity)
            if pk not in seen_providers:
                seen_providers.add(pk)
                out.append(mk)
            if len(out) >= n:
                return out
        for _pk, mk in placed:  # second pass: fill remaining slots
            if mk not in out:
                out.append(mk)
            if len(out) >= n:
                break
        return out

    # -- dispatch ------------------------------------------------------------------------------

    async def run(
        self,
        strategy: Strategy,
        opts: StrategyOptions,
        *,
        messages: list[JsonObject],
        ask: str,
        packet_id: str,
        conversation_id: Any = None,
        generation: JsonObject | None = None,
        default_model: str | None = None,
    ) -> tuple[StepResult, OrchestrationTrace]:
        common: dict[str, Any] = {
            "ask": ask,
            "packet_id": packet_id,
            "conversation_id": conversation_id,
            "generation": generation,
        }
        if strategy is Strategy.SEQUENTIAL_HANDOFF:
            return await self.sequential_handoff(opts, messages, **common)
        if strategy is Strategy.JUDGE_SELECT:
            return await self.judge_select(opts, messages, **common)
        if strategy is Strategy.COST_TIERED:
            return await self.cost_tiered(opts, messages, default_model=default_model, **common)
        raise ChatError("conflict", f"unknown strategy '{strategy}'", http_status=422)

    # -- strategies ----------------------------------------------------------------------------

    async def sequential_handoff(
        self, opts: StrategyOptions, messages: list[JsonObject], **common: Any
    ) -> tuple[StepResult, OrchestrationTrace]:
        stages = list(opts.stages)
        if len(stages) < 2:
            raise ChatError(
                "conflict",
                "sequential_handoff needs strategy_options.stages with >= 2 models",
                http_status=422,
            )
        trace = TraceBuilder(strategy=Strategy.SEQUENTIAL_HANDOFF)
        result: StepResult | None = None
        for i, model in enumerate(stages):
            role = "draft" if i == 0 else "continue"
            instr = opts.stage_instructions[i] if i < len(opts.stage_instructions) else None
            msgs = messages
            if result is not None:
                # previous stage output travels in the SAME packet as an assistant turn
                msgs = [
                    *messages,
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": instr or CONTINUE_PROMPT},
                ]
            elif instr:
                msgs = self._with_system(messages, instr)
            result = await self.run_step(
                model=model, provider=opts.provider, messages=msgs, **common
            )
            trace.steps.append(self._step(i + 1, role, model, result, reason="stage order"))
            self._accumulate(trace, result)
        assert result is not None
        trace.selected_step = len(stages)
        trace.decision_reason = "final stage output (each stage saw the previous output in-packet)"
        return result, trace.build()

    async def judge_select(
        self, opts: StrategyOptions, messages: list[JsonObject], **common: Any
    ) -> tuple[StepResult, OrchestrationTrace]:
        cands = list(opts.candidates)[: opts.max_candidates]
        if len(cands) < 2:
            raise ChatError(
                "conflict",
                "judge_select needs strategy_options.candidates with >= 2 models",
                http_status=422,
            )
        trace = TraceBuilder(strategy=Strategy.JUDGE_SELECT)
        results: dict[int, StepResult] = {}
        scores: dict[int, float] = {}
        for i, model in enumerate(cands):
            n = i + 1
            try:
                r = await self.run_step(
                    model=model, provider=opts.provider, messages=messages, **common
                )
            except ChatError as exc:
                trace.steps.append(
                    self._step(n, "candidate", model, None, reason=f"{exc.code}: {exc.message}")
                )
                continue
            score, fails = score_against_spec(r.text, opts.output_spec)
            trace.steps.append(
                self._step(n, "candidate", model, r, spec_score=score, spec_failures=fails)
            )
            self._accumulate(trace, r)
            results[n] = r
            scores[n] = score
        if not results:
            raise ChatError(
                "execution_failed", "all judge_select candidates failed", http_status=502
            )
        best_step = sorted(results, key=lambda n: (-scores[n], -(results[n].cost_score or 0), n))[0]
        reason = "highest output_spec score (ties -> cheaper, then order)"
        if opts.judge and len(results) > 1:
            listing = "\n\n".join(
                f"[{n}] ({r.model_key})\n{r.text[:2000]}" for n, r in results.items()
            )
            spec = opts.output_spec.model_dump(exclude_defaults=True)
            judge_msgs: list[JsonObject] = [
                {
                    "role": "system",
                    "content": "You are a strict judge. Reply with ONLY the number of the best candidate.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Task: {common['ask']}\nCriteria: {json.dumps(spec)}\n\n"
                        f"Candidates:\n{listing}"
                    ),
                },
            ]
            try:
                jr = await self.run_step(
                    model=opts.judge,
                    provider=opts.provider,
                    messages=judge_msgs,
                    ask="judge",
                    packet_id=common["packet_id"],
                    conversation_id=common.get("conversation_id"),
                )
                self._accumulate(trace, jr)
                pick = next(
                    (
                        int(tok)
                        for tok in jr.text.replace("[", " ").replace("]", " ").split()
                        if tok.isdigit() and int(tok) in results
                    ),
                    None,
                )
                if pick is not None:
                    best_step, reason = pick, f"judge model chose [{pick}]"
                else:
                    reason += "; judge reply unparsable -> heuristic kept"
                trace.steps.append(
                    self._step(len(trace.steps) + 1, "judge", opts.judge, jr, reason=reason)
                )
            except ChatError as exc:
                trace.steps.append(
                    self._step(
                        len(trace.steps) + 1,
                        "judge",
                        opts.judge,
                        None,
                        reason=f"{exc.code}: {exc.message}",
                    )
                )
                reason += "; judge failed -> heuristic kept"
        trace.selected_step = best_step
        trace.decision_reason = reason
        return results[best_step], trace.build()

    async def cost_tiered(
        self,
        opts: StrategyOptions,
        messages: list[JsonObject],
        *,
        default_model: str | None,
        **common: Any,
    ) -> tuple[StepResult, OrchestrationTrace]:
        tiers = list(opts.tiers) or self._cheapest_first(3)
        if default_model and default_model not in tiers:
            tiers = [tiers[0], default_model] if tiers else [default_model]
        tiers = tiers[:3] if not opts.tiers else tiers[:2]
        if not tiers:
            raise ChatError("conflict", "cost_tiered: no models available", http_status=503)
        trace = TraceBuilder(strategy=Strategy.COST_TIERED)
        last_exc: ChatError | None = None
        for i, model in enumerate(tiers):
            n = i + 1
            can_upgrade = i == 0 and len(tiers) > 1
            try:
                r = await self.run_step(
                    model=model, provider=opts.provider, messages=messages, **common
                )
            except ChatError as exc:
                # a FAILED tier (quota, outage) is not a spec miss: escalate to the next
                # routable tier; the single spec-upgrade budget is only spent on spec misses
                last_exc = exc
                trace.steps.append(
                    self._step(n, "tier", model, None, reason=f"{exc.code}: {exc.message}")
                )
                continue
            score, fails = score_against_spec(r.text, opts.output_spec)
            self._accumulate(trace, r)
            if fails and can_upgrade:
                trace.steps.append(
                    self._step(
                        n,
                        "tier",
                        model,
                        r,
                        spec_score=score,
                        spec_failures=fails,
                        reason="cheap tier failed output_spec -> upgrade once",
                    )
                )
                trace.upgrades += 1
                continue
            trace.steps.append(
                self._step(
                    n,
                    "tier",
                    model,
                    r,
                    spec_score=score,
                    spec_failures=fails,
                    reason="accepted" if not fails else "final tier (no further upgrade)",
                )
            )
            trace.selected_step = n
            trace.decision_reason = (
                "cheapest tier met output_spec"
                if i == 0
                else "upgraded tier result (max 1 upgrade)"
            )
            return r, trace.build()
        assert last_exc is not None
        raise last_exc


__all__ = ["Orchestrator", "score_against_spec"]
