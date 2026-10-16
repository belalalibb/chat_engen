"""CE-15 usage & pricing contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field


class TokenCounts(ContractModel):
    input: int = Field(default=0, ge=0)
    output: int = Field(default=0, ge=0)
    cached: int | None = Field(default=None, ge=0)
    total: int = Field(default=0, ge=0)
    estimated: bool = False  # True when the provider returned no token usage


class Cost(ContractModel):
    amount: float = Field(ge=0)
    currency: BoundedStr = "USD"
    pricing_ref: BoundedStr | None = None  # PricingRecord.ref; None ⇒ unknown price
    known: bool = True  # False ⇒ amount is 0 because no pricing exists


class UsageRecord(ContractModel):
    id: UUID
    request_id: BoundedStr  # execution_id | stream_id | completion id
    tenant_id: UUID
    project_id: UUID | None = None
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    surface: BoundedStr  # chat | chat_stream | openai_compat | execute | orchestration
    provider_key: BoundedStr | None = None
    model_key: BoundedStr | None = None
    canonical_id: BoundedStr | None = None
    tokens: TokenCounts = Field(default_factory=TokenCounts)
    latency_ms: int | None = None
    status: BoundedStr  # succeeded | failed | partial | cancelled
    cost: Cost
    strategy: BoundedStr | None = None
    skills: list[BoundedStr] = Field(default_factory=list)
    created_at: datetime
    metadata: JsonObject = Field(default_factory=dict)


class PricingRecord(ContractModel):
    """USD per 1M tokens. ``source`` names where the number came from and when it was seen."""

    ref: BoundedStr  # f"{source}:{provider_key}/{model_key}@{verified_at.date()}"
    provider_key: BoundedStr | None = None  # None ⇒ applies to any provider serving model_key
    model_key: BoundedStr
    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)
    cached_input_per_1m: float | None = Field(default=None, ge=0)
    currency: BoundedStr = "USD"
    source: BoundedStr  # models.dev | manual | provider_api
    verified_at: datetime
    notes: BoundedStr | None = None


class PricingUpsert(ContractModel):
    provider_key: BoundedStr | None = None
    model_key: BoundedStr
    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)
    cached_input_per_1m: float | None = Field(default=None, ge=0)
    currency: BoundedStr = "USD"
    notes: BoundedStr | None = None


class UsageAggregateRow(ContractModel):
    key: JsonObject  # {"tenant_id": ..., "provider_key": ..., "day": ...} per group_by
    requests: int
    succeeded: int
    failed: int
    tokens_in: int
    tokens_out: int
    tokens_total: int
    cost_amount: float
    cost_unknown_requests: int
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None


class UsageReport(ContractModel):
    group_by: list[BoundedStr]
    rows: list[UsageAggregateRow]
    total_requests: int
    total_cost_amount: float
    currency: BoundedStr = "USD"
    since: datetime | None = None
    until: datetime | None = None


__all__ = [
    "Cost",
    "PricingRecord",
    "PricingUpsert",
    "TokenCounts",
    "UsageAggregateRow",
    "UsageRecord",
    "UsageReport",
]
