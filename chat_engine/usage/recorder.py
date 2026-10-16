"""UsageRecorder: exactly one UsageRecord per request (sync, stream, orchestration step,
OpenAI-compat, /v1/execute), deduplicated by request_id. Aggregates for GET /v1/usage."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.contracts.base import JsonObject

from chat_engine.usage.contracts import (
    Cost,
    TokenCounts,
    UsageAggregateRow,
    UsageRecord,
    UsageReport,
)
from chat_engine.usage.pricing import PricingBook

if TYPE_CHECKING:
    from chat_engine.conversations.contracts import Conversation, Turn

GROUPS = {"tenant_id", "project_id", "conversation_id", "provider_key", "model_key", "canonical_id", "surface", "status", "day", "strategy"}


def tokens_from_usage(usage: JsonObject, *, fallback_chars: int = 0) -> TokenCounts:
    def _i(*keys: str) -> int | None:
        for k in keys:
            v = usage.get(k)
            if isinstance(v, int | float):
                return int(v)
        return None

    i = _i("prompt_tokens", "input_tokens")
    o = _i("completion_tokens", "output_tokens")
    c = _i("cached_tokens", "cache_read_input_tokens")
    t = _i("total_tokens")
    if i is None and o is None and t is None:
        est = max(0, fallback_chars // 4)
        return TokenCounts(input=est, output=0, total=est, estimated=True)
    i = i or 0
    o = o or 0
    return TokenCounts(input=i, output=o, cached=c, total=t if t is not None else i + o)


class UsageRecorder:
    def __init__(self, pricing: PricingBook | None = None, *, enabled: bool = True) -> None:
        self.pricing = pricing or PricingBook()
        self.enabled = enabled
        self._records: dict[str, UsageRecord] = {}  # request_id → record (idempotent)

    # -- write ---------------------------------------------------------------------------------

    def record(
        self,
        *,
        request_id: str,
        tenant_id: UUID,
        surface: str,
        status: str,
        provider_key: str | None,
        model_key: str | None,
        canonical_id: str | None = None,
        usage: JsonObject | None = None,
        latency_ms: int | None = None,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        turn_id: UUID | None = None,
        strategy: str | None = None,
        skills: list[str] | None = None,
        fallback_chars: int = 0,
        metadata: JsonObject | None = None,
    ) -> UsageRecord | None:
        if not self.enabled:
            return None
        if request_id in self._records:
            return self._records[request_id]
        tokens = tokens_from_usage(dict(usage or {}), fallback_chars=fallback_chars)
        rec_price = self.pricing.lookup(provider_key, model_key)
        cost = (
            Cost(amount=self.pricing.cost_for(rec_price, tokens), pricing_ref=rec_price.ref)
            if rec_price is not None
            else Cost(amount=0.0, pricing_ref=None, known=False)
        )
        row = UsageRecord(
            id=uuid4(),
            request_id=request_id,
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            surface=surface,
            provider_key=provider_key,
            model_key=model_key,
            canonical_id=canonical_id,
            tokens=tokens,
            latency_ms=latency_ms,
            status=status,
            cost=cost,
            strategy=strategy,
            skills=list(skills or []),
            created_at=datetime.now(UTC),
            metadata=dict(metadata or {}),
        )
        self._records[request_id] = row
        return row

    def record_turn(
        self,
        turn: Turn,
        *,
        conversation: Conversation,
        provider_key: str | None,
        model_key: str | None,
        canonical_id: str | None,
        status: str,
        surface: str = "chat",
    ) -> UsageRecord | None:
        """ChatService hook (``extensions['usage_recorder']``)."""
        rid = turn.execution_id or turn.stream_id or str(turn.id)
        return self.record(
            request_id=rid,
            tenant_id=conversation.tenant_id,
            surface=surface if not turn.stream_id else "chat_stream",
            status=status,
            provider_key=provider_key,
            model_key=model_key,
            canonical_id=canonical_id,
            usage=dict(turn.usage),
            latency_ms=turn.latency_ms,
            project_id=conversation.project_id,
            conversation_id=conversation.id,
            turn_id=turn.id,
            strategy=str(turn.metadata.get("strategy")) if turn.metadata.get("strategy") else None,
            skills=[str(s.get("skill_id") or s.get("id")) for s in turn.skills_used],
            fallback_chars=len(turn.text()),
        )

    # -- read ------------------------------------------------------------------------------------

    def all(self) -> list[UsageRecord]:
        return sorted(self._records.values(), key=lambda r: r.created_at)

    def get(self, request_id: str) -> UsageRecord | None:
        return self._records.get(request_id)

    def report(
        self,
        *,
        group_by: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        **filters: Any,
    ) -> UsageReport:
        groups = [g for g in (group_by or ["provider_key"]) if g in GROUPS]
        rows = self.all()
        if since:
            rows = [r for r in rows if r.created_at >= since]
        if until:
            rows = [r for r in rows if r.created_at <= until]
        for k, v in filters.items():
            if v is not None and k in GROUPS:
                rows = [r for r in rows if str(getattr(r, k, None)) == str(v)]
        buckets: dict[tuple[str, ...], list[UsageRecord]] = {}
        for r in rows:
            key = tuple(
                r.created_at.date().isoformat() if g == "day" else str(getattr(r, g, None))
                for g in groups
            )
            buckets.setdefault(key, []).append(r)
        out: list[UsageAggregateRow] = []
        for key, rs in sorted(buckets.items()):
            lat = sorted(r.latency_ms for r in rs if r.latency_ms is not None)
            out.append(
                UsageAggregateRow(
                    key=dict(zip(groups, key, strict=True)),
                    requests=len(rs),
                    succeeded=sum(1 for r in rs if r.status == "succeeded"),
                    failed=sum(1 for r in rs if r.status == "failed"),
                    tokens_in=sum(r.tokens.input for r in rs),
                    tokens_out=sum(r.tokens.output for r in rs),
                    tokens_total=sum(r.tokens.total for r in rs),
                    cost_amount=round(sum(r.cost.amount for r in rs), 8),
                    cost_unknown_requests=sum(1 for r in rs if not r.cost.known),
                    latency_p50_ms=int(statistics.median(lat)) if lat else None,
                    latency_p95_ms=lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
                )
            )
        return UsageReport(
            group_by=groups,
            rows=out,
            total_requests=len(rows),
            total_cost_amount=round(sum(r.cost.amount for r in rows), 8),
            since=since,
            until=until,
        )


__all__ = ["UsageRecorder", "tokens_from_usage"]
