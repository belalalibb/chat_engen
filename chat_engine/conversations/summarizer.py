"""Summarizer (CE-09): folds older turns into a STRUCTURED summary (objective,
decisions, facts, open items) — never chain-of-thought. Uses the cheapest
eligible model through the ONE Router; falls back to a deterministic
extractive summary when no model is reachable (honestly labelled)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from core.contracts.base import JsonObject
from core.contracts.model_policy import AutoModelPolicy
from core.contracts.provider import ProviderGenerateRequest, ProviderOperation
from core.contracts.routing import RoutingRequest, ScoringWeights

from chat_engine.conversations.contracts import Turn

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime

PROMPT = (
    "Summarize the conversation below for a model that will continue it. "
    "Output ONLY these sections, concise, no reasoning:\n"
    "## Objective\n## Decisions taken\n## Facts & constraints\n## Open items\n"
)


class Summarizer:
    def __init__(self, rt: Runtime, *, max_chars: int = 12_000) -> None:
        self._rt = rt
        self._max_chars = max_chars

    async def summarize(
        self, turns: list[Turn], *, previous_summary: str | None, objective: str | None
    ) -> tuple[str, JsonObject]:
        transcript = "\n".join(f"{t.role.value}: {t.text()}" for t in turns if t.text())
        if len(transcript) > self._max_chars:
            transcript = transcript[-self._max_chars :]
        prefix = f"Previous summary:\n{previous_summary}\n\n" if previous_summary else ""
        obj = f"Objective: {objective}\n\n" if objective else ""
        ask = f"{PROMPT}\n{obj}{prefix}Conversation:\n{transcript}"
        try:
            weights = ScoringWeights(
                version="summarizer-cost-first",
                quality=0.1,
                reliability=0.2,
                cost=0.5,
                latency=0.15,
                context_fit=0.05,
                policy_preference=0.0,
            )
            decision = self._rt.router.route(
                RoutingRequest(
                    operation=ProviderOperation.GENERATE_TEXT,
                    model_policy=AutoModelPolicy(type="auto"),
                    weights=weights,
                )
            )
            cand = decision.selected
            binding = self._rt.bindings.get(cand.provider_id, cand.model_id)
            adapter = self._rt.adapters[cand.provider_id]
            resp = await adapter.generate(
                ProviderGenerateRequest(
                    request_id=uuid4(),
                    tenant_id=self._rt.tenant_id,
                    operation=ProviderOperation.GENERATE_TEXT,
                    provider_model_name=binding.provider_model_name,
                    credential_ref=self._rt.credential_refs.get(cand.provider_id, ""),
                    payload={
                        "ask": ask,
                        "messages": [{"role": "user", "content": ask}],
                        "generation": {"temperature": 0, "max_tokens": 600},
                    },
                    timeout_ms=45_000,
                )
            )
            content = resp.output.get("content") if resp.succeeded else None
            if isinstance(content, str) and content.strip() and "## " in content:
                pk = self._rt.extensions["catalog_index"].provider_key(cand.provider_id)
                return content.strip(), {
                    "summary_source": "model",
                    "summary_provider": pk,
                    "summary_model": binding.provider_model_name,
                }
        except Exception:
            pass
        return self._extractive(turns, previous_summary, objective), {
            "summary_source": "extractive",
            "note": "no model summary available — deterministic fallback",
        }

    @staticmethod
    def _extractive(turns: list[Turn], previous_summary: str | None, objective: str | None) -> str:
        users = [t.text() for t in turns if t.role.value == "user" and t.text()]
        assistants = [t.text() for t in turns if t.role.value == "assistant" and t.text()]
        lines = ["## Objective", objective or (users[0][:200] if users else "(none recorded)")]
        if previous_summary:
            lines += ["", "## Previously", previous_summary[:1500]]
        lines += (
            ["", "## Decisions taken"] + [f"- {a[:160]}" for a in assistants[-3:]]
            if assistants
            else ["", "## Decisions taken", "- (none)"]
        )
        lines += ["", "## Facts & constraints"] + [f"- user asked: {u[:160]}" for u in users[-5:]]
        lines += [
            "",
            "## Open items",
            f"- continue from the last user request: {users[-1][:200] if users else '(none)'}",
        ]
        return "\n".join(lines)
