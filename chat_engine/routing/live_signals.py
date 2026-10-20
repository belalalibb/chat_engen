"""LiveSignals (CE-24/C) — EWMA latency + in-flight per provider, used to reorder a
RoutingDecision's candidates *after* upstream scoring (litellm ``lowest-latency`` /
``least-busy`` strategies, applied as a post-filter so upstream contracts stay intact).

Opt-in: ``CE_ROUTING_LIVE_SIGNALS=1``. Rules (explainable, in ``decision.selected.reasons``):

* candidates are ranked by upstream ``score`` first; only candidates whose score is within
  ``SCORE_BAND`` of the best are eligible for live reordering (never override a clearly
  better upstream choice);
* inside the band, order by ``ewma_ms * (1 + in_flight * BUSY_PENALTY)`` ascending;
  providers with no observation yet keep their upstream position (no penalty, no bonus);
* a reorder is only applied when the live score of the new leader is at least
  ``MIN_GAIN`` better (relative) than the current leader's — hysteresis against flapping.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.contracts.base import JsonObject

SCORE_BAND = 0.15  # relative: candidates within 15% of the best score may be reordered
BUSY_PENALTY = 0.5  # each in-flight request inflates the effective latency by 50%
MIN_GAIN = 0.20  # new leader must be >= 20% faster (effective) to take over
EWMA_ALPHA = 0.3


@dataclass
class ProviderSignal:
    ewma_ms: float | None = None
    samples: int = 0
    in_flight: int = 0
    last_at: datetime | None = None
    errors: int = 0


@dataclass
class LiveSignals:
    enabled: bool = False
    _by_provider: dict[str, ProviderSignal] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- feed ----------------------------------------------------------------------------------

    def start(self, provider_key: str | None) -> None:
        if not provider_key:
            return
        with self._lock:
            self._by_provider.setdefault(provider_key, ProviderSignal()).in_flight += 1

    def finish(
        self, provider_key: str | None, *, latency_ms: int | None, success: bool = True
    ) -> None:
        if not provider_key:
            return
        with self._lock:
            sig = self._by_provider.setdefault(provider_key, ProviderSignal())
            sig.in_flight = max(0, sig.in_flight - 1)
            sig.last_at = datetime.now(UTC)
            if not success:
                sig.errors += 1
            if latency_ms is not None and latency_ms >= 0 and success:
                sig.samples += 1
                sig.ewma_ms = (
                    float(latency_ms)
                    if sig.ewma_ms is None
                    else (1 - EWMA_ALPHA) * sig.ewma_ms + EWMA_ALPHA * float(latency_ms)
                )

    def observe(self, provider_key: str | None, latency_ms: int | None, *, success: bool) -> None:
        """One-shot observation (surfaces that did not call start())."""
        self.start(provider_key)
        self.finish(provider_key, latency_ms=latency_ms, success=success)

    # -- read ----------------------------------------------------------------------------------

    def effective_ms(self, provider_key: str) -> float | None:
        sig = self._by_provider.get(provider_key)
        if sig is None or sig.ewma_ms is None:
            return None
        return sig.ewma_ms * (1.0 + sig.in_flight * BUSY_PENALTY)

    def snapshot(self) -> list[JsonObject]:
        return [
            {
                "provider_key": k,
                "ewma_ms": round(s.ewma_ms, 1) if s.ewma_ms is not None else None,
                "effective_ms": round(e, 1) if (e := self.effective_ms(k)) is not None else None,
                "samples": s.samples,
                "in_flight": s.in_flight,
                "errors": s.errors,
                "last_at": s.last_at.isoformat() if s.last_at else None,
            }
            for k, s in sorted(self._by_provider.items())
        ]

    # -- reorder -------------------------------------------------------------------------------

    def reorder(self, decision: Any, provider_key_of: Any) -> Any:
        """Return a (possibly) re-ordered copy of ``decision``. ``provider_key_of`` maps a
        provider_id → provider_key (CatalogIndex.provider_key)."""
        if not self.enabled:
            return decision
        cands = [decision.selected, *decision.fallback_candidates]
        if len(cands) < 2:
            return decision
        best = max(c.score for c in cands)
        band = [c for c in cands if best <= 0 or (best - c.score) / best <= SCORE_BAND]
        if len(band) < 2:
            return decision
        rest = [c for c in cands if c not in band]

        def key(c: Any) -> tuple[int, float]:
            eff = self.effective_ms(provider_key_of(c.provider_id) or "")
            # unknown providers keep their upstream order, sorted after known-fast ones
            return (0, eff) if eff is not None else (1, 0.0)

        ordered = sorted(band, key=key)
        leader, current = ordered[0], band[0]
        if leader is current:
            return decision
        lead_eff = self.effective_ms(provider_key_of(leader.provider_id) or "")
        cur_eff = self.effective_ms(provider_key_of(current.provider_id) or "")
        if lead_eff is None:
            return decision
        if cur_eff is not None and (cur_eff - lead_eff) / cur_eff < MIN_GAIN:
            return decision  # not enough gain — keep upstream order (hysteresis)
        reason = (
            f"live_signals: {provider_key_of(leader.provider_id)} effective {lead_eff:.0f}ms"
            + (f" vs {cur_eff:.0f}ms" if cur_eff is not None else " vs unobserved")
        )
        new_selected = leader.model_copy(update={"reasons": [*leader.reasons, reason]})
        new_order = [new_selected, *[c for c in ordered[1:]], *rest]
        return decision.model_copy(
            update={"selected": new_selected, "fallback_candidates": new_order[1:]}
        )


__all__ = ["BUSY_PENALTY", "EWMA_ALPHA", "MIN_GAIN", "SCORE_BAND", "LiveSignals", "ProviderSignal"]
