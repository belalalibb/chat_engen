"""CE-12 orchestration contracts: strategies, options, explainable trace."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field


class Strategy(StrEnum):
    SINGLE = "single"  # default: one routed call (CE-09 behaviour)
    SEQUENTIAL_HANDOFF = "sequential_handoff"  # A drafts → B continues with the packet
    JUDGE_SELECT = "judge_select"  # N candidates → judge picks by output_spec
    COST_TIERED = "cost_tiered"  # cheap first; upgrade once if output_spec fails


class OutputSpec(ContractModel):
    """Machine-checkable acceptance criteria (used by judge_select / cost_tiered)."""

    min_chars: int | None = Field(default=None, ge=0)
    max_chars: int | None = Field(default=None, ge=1)
    must_include: list[BoundedStr] = Field(default_factory=list)
    must_not_include: list[BoundedStr] = Field(default_factory=list)
    json_object: bool = False
    language_hint: BoundedStr | None = None  # informational — surfaced to the judge prompt


class StrategyOptions(ContractModel):
    """``strategy_options`` on POST /v1/chat. Every model is a canonical_id or model_key."""

    # sequential_handoff
    stages: list[BoundedStr] = Field(default_factory=list)  # ordered models, ≥2
    stage_instructions: list[str] = Field(default_factory=list)  # optional per-stage system add-on
    # judge_select
    candidates: list[BoundedStr] = Field(default_factory=list)  # ≥2
    judge: BoundedStr | None = None  # None ⇒ heuristic judge (output_spec score only)
    max_candidates: int = Field(default=3, ge=2, le=6)
    # cost_tiered
    tiers: list[BoundedStr] = Field(default_factory=list)  # [cheap, better]; derived if empty
    # shared
    output_spec: OutputSpec = Field(default_factory=OutputSpec)
    provider: BoundedStr | None = None


class StepTrace(ContractModel):
    step: int
    role: BoundedStr  # draft | continue | candidate | judge | tier
    model: BoundedStr  # requested (canonical or key)
    provider_key: BoundedStr | None = None
    model_key: BoundedStr | None = None
    execution_id: BoundedStr | None = None
    status: BoundedStr  # succeeded | failed | skipped
    latency_ms: int | None = None
    usage: JsonObject = Field(default_factory=dict)
    cost_score: float | None = None
    spec_score: float | None = None
    spec_failures: list[BoundedStr] = Field(default_factory=list)
    reason: str | None = None
    output_preview: str | None = None


class OrchestrationTrace(ContractModel):
    strategy: Strategy
    steps: list[StepTrace] = Field(default_factory=list)
    selected_step: int | None = None
    decision_reason: str = ""
    total_usage: JsonObject = Field(default_factory=dict)
    total_cost_score: float = 0.0
    upgrades: int = 0  # cost_tiered: how many times we escalated (<=1)


@dataclass
class TraceBuilder:
    """Mutable accumulator; ``build()`` emits the frozen contract."""

    strategy: Strategy
    steps: list[StepTrace] = field(default_factory=list)
    selected_step: int | None = None
    decision_reason: str = ""
    total_usage: dict[str, float] = field(default_factory=dict)
    total_cost_score: float = 0.0
    upgrades: int = 0

    def build(self) -> OrchestrationTrace:
        return OrchestrationTrace(
            strategy=self.strategy,
            steps=list(self.steps),
            selected_step=self.selected_step,
            decision_reason=self.decision_reason,
            total_usage=dict(self.total_usage),
            total_cost_score=self.total_cost_score,
            upgrades=self.upgrades,
        )


class StepResult(ContractModel):
    text: str
    provider_key: BoundedStr | None
    model_key: BoundedStr | None
    canonical_id: BoundedStr | None
    execution_id: BoundedStr
    usage: JsonObject = Field(default_factory=dict)
    latency_ms: int | None = None
    cost_score: float | None = None
    raw_output: JsonObject = Field(default_factory=dict)


__all__ = [
    "OrchestrationTrace",
    "OutputSpec",
    "StepResult",
    "StepTrace",
    "Strategy",
    "StrategyOptions",
    "TraceBuilder",
]
