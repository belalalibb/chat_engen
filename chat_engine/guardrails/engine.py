"""GuardrailEngine — ordered chain over GuardrailPort instances (CE-24/E)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.contracts.base import JsonObject

from chat_engine.guardrails.builtins import KeywordBlock, PiiRedact
from chat_engine.guardrails.port import GuardrailPort, Verdict

BUILTIN_TYPES = ("keyword_block", "pii_redact")


@dataclass
class GuardrailVerdict:
    verdict: Verdict
    text: str
    applied: list[JsonObject] = field(default_factory=list)  # [{name, verdict, reason, ...}]
    blocked_by: str | None = None
    reason: str | None = None
    details: JsonObject = field(default_factory=dict)


def build_guardrails(specs: list[JsonObject] | None) -> list[tuple[GuardrailPort, str]]:
    """Validate + instantiate built-ins. Unknown ``type`` ⇒ ValueError (contract: no
    custom code). Returns [(guardrail, stage)]."""
    out: list[tuple[GuardrailPort, str]] = []
    for i, spec in enumerate(specs or []):
        if not isinstance(spec, dict):
            raise ValueError(f"guardrails[{i}] must be an object")
        kind = str(spec.get("type") or "")
        stage = str(spec.get("stage") or "both")
        if stage not in ("pre", "post", "both"):
            raise ValueError(f"guardrails[{i}].stage must be pre|post|both")
        if kind == "keyword_block":
            kws = spec.get("keywords")
            if not isinstance(kws, list) or not kws:
                raise ValueError(f"guardrails[{i}].keywords must be a non-empty list")
            out.append(
                (
                    KeywordBlock(
                        [str(k) for k in kws],
                        action=str(spec.get("action") or "block"),
                        mask=str(spec.get("mask") or "[blocked]"),
                    ),
                    stage,
                )
            )
        elif kind == "pii_redact":
            kinds = spec.get("kinds")
            out.append(
                (
                    PiiRedact(
                        [str(k) for k in kinds] if isinstance(kinds, list) else None,
                        action=str(spec.get("action") or "redact"),
                    ),
                    stage,
                )
            )
        else:
            raise ValueError(
                f"guardrails[{i}].type must be one of {BUILTIN_TYPES} (custom code is not allowed)"
            )
    return out


class GuardrailEngine:
    def __init__(self, defaults: list[JsonObject] | None = None) -> None:
        self._defaults = build_guardrails(defaults)

    @classmethod
    def from_env(cls, raw: str | None) -> GuardrailEngine:
        text = (raw or "").strip()
        if not text:
            return cls()
        doc = json.loads(text)
        if not isinstance(doc, list):
            raise ValueError("CE_GUARDRAILS_JSON must be a JSON array of guardrail specs")
        return cls(doc)

    @property
    def default_specs_count(self) -> int:
        return len(self._defaults)

    def run(
        self, stage: str, text: str, project_specs: list[JsonObject] | None = None
    ) -> GuardrailVerdict:
        chain = [*self._defaults, *build_guardrails(project_specs)]
        if not chain:
            return GuardrailVerdict(Verdict.ALLOW, text)
        current = text
        applied: list[JsonObject] = []
        for g, g_stage in chain:
            if g_stage != "both" and g_stage != stage:
                continue
            res = g.pre_request(current) if stage == "pre" else g.post_response(current)
            if res.verdict is Verdict.ALLOW:
                continue
            applied.append(
                {"name": g.name, "verdict": res.verdict.value, "reason": res.reason, **res.details}
            )
            if res.verdict is Verdict.BLOCK:
                return GuardrailVerdict(
                    Verdict.BLOCK,
                    current,
                    applied,
                    blocked_by=g.name,
                    reason=res.reason,
                    details=dict(res.details),
                )
            current = res.text
        verdict = Verdict.REDACT if applied else Verdict.ALLOW
        return GuardrailVerdict(verdict, current, applied)


__all__ = ["BUILTIN_TYPES", "GuardrailEngine", "GuardrailVerdict", "build_guardrails"]
