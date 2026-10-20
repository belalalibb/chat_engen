"""PricingBook: models.dev snapshot (sourced, dated) + LiteLLM community table (secondary)
+ manual overrides. Never guesses.

Precedence on lookup: manual > models.dev > litellm (CE-24/A). The LiteLLM file only
fills (provider, model) pairs models.dev does not price — it never overrides them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from chat_engine.usage.contracts import PricingRecord, PricingUpsert, TokenCounts

SNAPSHOT = Path(__file__).resolve().parents[2] / "compat" / "catalogs" / "models_dev_snapshot.json"
LITELLM_SNAPSHOT = (
    Path(__file__).resolve().parents[2] / "compat" / "catalogs" / "litellm_prices_snapshot.json"
)


class PricingBook:
    def __init__(
        self, *, lazy_snapshot: Path | None = None, lazy_litellm: Path | None = None
    ) -> None:
        self._by_exact: dict[tuple[str, str], PricingRecord] = {}  # (provider_key, model_key)
        self._by_model: dict[str, PricingRecord] = {}  # model_key → any-provider price
        self.snapshot_loaded = False
        self.snapshot_fetched_at: str | None = None
        self.litellm_loaded = False
        self.litellm_fetched_at: str | None = None
        self._lazy = lazy_snapshot  # parsed on first lookup/records(), not at boot
        self._lazy_litellm = lazy_litellm

    def _ensure(self) -> None:
        if self._lazy is not None:
            path, self._lazy = self._lazy, None
            self.load_models_dev(path)
        if self._lazy_litellm is not None:
            path, self._lazy_litellm = self._lazy_litellm, None
            self.load_litellm(path)

    # -- loading ------------------------------------------------------------------------------

    def load_models_dev(self, path: Path = SNAPSHOT) -> int:
        """Load the pinned models.dev snapshot. Prices there are USD per 1M tokens."""
        if not path.is_file():
            return 0
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched = str(data.get("fetched_at") or "")
        try:
            verified_at = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
        except ValueError:
            verified_at = datetime.now(UTC)
        n = 0
        for pkey, prov in (data.get("providers") or {}).items():
            for mkey, m in (prov.get("models") or {}).items():
                ci, co = m.get("cost_input"), m.get("cost_output")
                if not isinstance(ci, int | float) or not isinstance(co, int | float):
                    continue
                cc = (
                    m.get("cost_cache_read")
                    if isinstance(m.get("cost_cache_read"), int | float)
                    else None
                )
                rec = PricingRecord(
                    ref=f"models.dev:{pkey}/{mkey}@{verified_at.date().isoformat()}",
                    provider_key=str(pkey),
                    model_key=str(mkey),
                    input_per_1m=float(ci),
                    output_per_1m=float(co),
                    cached_input_per_1m=float(cc) if cc is not None else None,
                    source="models.dev",
                    verified_at=verified_at,
                )
                self._by_exact[(str(pkey), str(mkey))] = rec
                # first seen wins for the any-provider fallback (deterministic: sorted keys)
                self._by_model.setdefault(str(mkey), rec)
                n += 1
        self.snapshot_loaded = n > 0
        self.snapshot_fetched_at = fetched or None
        return n

    def load_litellm(self, path: Path = LITELLM_SNAPSHOT) -> int:
        """Load the trimmed LiteLLM table written by ``scripts/import_litellm_catalog.py``
        (already USD per 1M). Secondary source: never overwrites an existing record.

        Keys there look like ``gpt-4o`` or ``azure_ai/deepseek-v3``; ``litellm_provider``
        carries the vendor. We register (litellm_provider, tail) exactly and the full key +
        tail as any-provider fallbacks, so ``lookup("gw_deepai", "deepseek-v3.2")`` resolves."""
        if not path.is_file():
            return 0
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched = str(data.get("fetched_at") or "")
        try:
            verified_at = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
        except ValueError:
            verified_at = datetime.now(UTC)
        n = 0
        for key, m in (data.get("models") or {}).items():
            ci, co = m.get("input_cost_per_token"), m.get("output_cost_per_token")
            if not isinstance(ci, int | float) or not isinstance(co, int | float):
                continue
            pkey = str(m.get("litellm_provider") or "")
            if not pkey:
                continue
            cc = m.get("cache_read_input_token_cost")
            tail = str(key).split("/")[-1]
            rec = PricingRecord(
                ref=f"litellm:{key}@{verified_at.date().isoformat()}",
                provider_key=pkey,
                model_key=str(key),
                input_per_1m=float(ci),
                output_per_1m=float(co),
                cached_input_per_1m=float(cc) if isinstance(cc, int | float) else None,
                source="litellm",
                verified_at=verified_at,
                notes=f"mode={m.get('mode')}",
            )
            self._by_exact.setdefault((pkey, str(key)), rec)
            self._by_exact.setdefault((pkey, tail), rec)
            self._by_model.setdefault(str(key), rec)
            self._by_model.setdefault(tail, rec)
            n += 1
        self.litellm_loaded = n > 0
        self.litellm_fetched_at = fetched or None
        return n

    def upsert(self, req: PricingUpsert, *, source: str = "manual") -> PricingRecord:
        now = datetime.now(UTC)
        rec = PricingRecord(
            ref=f"{source}:{req.provider_key or '*'}/{req.model_key}@{now.date().isoformat()}",
            source=source,
            verified_at=now,
            **req.model_dump(),
        )
        if req.provider_key:
            self._by_exact[(req.provider_key, req.model_key)] = rec
        else:
            self._by_model[req.model_key] = rec
        return rec

    # -- lookup / cost ------------------------------------------------------------------------

    def lookup(self, provider_key: str | None, model_key: str | None) -> PricingRecord | None:
        if model_key is None:
            return None
        self._ensure()
        if provider_key and (provider_key, model_key) in self._by_exact:
            return self._by_exact[(provider_key, model_key)]
        # manual any-provider override beats snapshot fallback
        rec = self._by_model.get(model_key)
        if rec is not None:
            return rec
        # normalised match (e.g. "openai/gpt-4o" → "gpt-4o")
        tail = model_key.split("/")[-1]
        return self._by_model.get(tail)

    @staticmethod
    def cost_for(rec: PricingRecord, tokens: TokenCounts) -> float:
        cached = tokens.cached or 0
        billable_in = max(0, tokens.input - cached)
        amount = (
            billable_in * rec.input_per_1m / 1_000_000
            + tokens.output * rec.output_per_1m / 1_000_000
        )
        if cached and rec.cached_input_per_1m is not None:
            amount += cached * rec.cached_input_per_1m / 1_000_000
        elif cached:
            amount += cached * rec.input_per_1m / 1_000_000
        return round(amount, 8)

    def records(self) -> list[PricingRecord]:
        self._ensure()
        seen: dict[str, PricingRecord] = {}
        for r in list(self._by_exact.values()) + list(self._by_model.values()):
            seen[r.ref] = r
        return sorted(seen.values(), key=lambda r: (r.provider_key or "", r.model_key))


__all__ = ["PricingBook"]
