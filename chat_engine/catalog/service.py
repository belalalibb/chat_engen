"""CatalogService — canonical identity, aliases, resolver, catalog projection (D1).

Merge rule: a model_key joins a canonical row ONLY via a ``confirmed`` alias.
Every model_key gets a SYSTEM identity alias (canonical_id == model_key) so a
freshly-registered model is visible immediately, alone, with its own counter.
Proposals (name similarity / models.dev) never change counters until confirmed.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from core.contracts.model_policy import (
    ExplicitModelPolicy,
    ExplicitModelsPolicy,
    FallbackScope,
    ModelRef,
    SelectionStrategy,
)
from core.routing.capacity import ResourceSignalBoard

from chat_engine.catalog.contracts import (
    AliasDecision,
    AliasSource,
    CanonicalModel,
    CatalogEntry,
    CatalogPage,
    CatalogProviderRef,
    ModelAlias,
)
from chat_engine.catalog.index import CatalogIndex
from chat_engine.util.time import utc_now

SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[2] / "compat" / "catalogs" / "models_dev_snapshot.json"
)
_PROPOSAL_MIN_CONFIDENCE = 0.6


class AliasNotFound(LookupError):
    pass


class AliasUnconfirmed(ValueError):
    """Resolving a canonical_id whose only aliases are proposed/rejected."""


def _s(v: object) -> str | None:
    return v if isinstance(v, str) else None


def _i(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _sl(v: object) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def normalize_name(name: str) -> str:
    """Vendor-prefix-insensitive, punctuation-insensitive comparison key."""
    base = name.split("/")[-1].lower()
    base = re.sub(r"[-_.:\s]+", "", base)
    base = re.sub(r"(instruct|chat|latest|preview)$", "", base)
    return base


@dataclass
class _Snapshot:
    by_norm: dict[str, list[tuple[str, dict[str, object]]]]  # norm → [(canonical_id, facts)]
    loaded: bool


class CatalogService:
    def __init__(
        self,
        index: CatalogIndex,
        signals: ResourceSignalBoard | None,
        *,
        snapshot_path: Path | None = SNAPSHOT_PATH,
    ) -> None:
        self._index = index
        self._signals = signals
        self._canonical: dict[str, CanonicalModel] = {}
        self._aliases: dict[tuple[str, str | None], ModelAlias] = {}
        self._snapshot = self._load_snapshot(snapshot_path)
        self.sync_identities()

    # -- snapshot (models.dev) ------------------------------------------------------------

    @staticmethod
    def _load_snapshot(path: Path | None) -> _Snapshot:
        by_norm: dict[str, list[tuple[str, dict[str, object]]]] = defaultdict(list)
        if path is None or not path.exists():
            return _Snapshot(by_norm={}, loaded=False)
        doc = json.loads(path.read_text())
        for pkey, provider in doc.get("providers", {}).items():
            for mkey, m in provider.get("models", {}).items():
                mid = str(m.get("id") or mkey)
                canonical_id = mid if "/" in mid else f"{pkey}/{mid}"
                facts = dict(m)
                facts["_provider"] = pkey
                by_norm[normalize_name(mid)].append((canonical_id, facts))
                if normalize_name(mkey) != normalize_name(mid):
                    by_norm[normalize_name(mkey)].append((canonical_id, facts))
        return _Snapshot(by_norm=dict(by_norm), loaded=True)

    @property
    def snapshot_loaded(self) -> bool:
        return self._snapshot.loaded

    # -- identities ----------------------------------------------------------------------

    def sync_identities(self) -> int:
        """Ensure every registry model_key has a SYSTEM identity alias + canonical row
        and a models.dev PROPOSAL when the name matches. Returns #new keys."""
        added = 0
        for key in self._index.model_keys():
            if (key, None) in self._aliases:
                continue
            model = self._index.model_by_key(key)
            if model is None:
                continue
            self._canonical.setdefault(
                key,
                CanonicalModel(
                    canonical_id=key,
                    display_name=model.display_name,
                    context_window=model.context_window,
                    modalities_in=[m.value for m in model.modalities],
                    capabilities=list(model.capabilities),
                    source=AliasSource.SYSTEM,
                ),
            )
            self._aliases[(key, None)] = ModelAlias(
                alias=key,
                canonical_id=key,
                decision=AliasDecision.CONFIRMED,
                confidence=1.0,
                source=AliasSource.SYSTEM,
                reason="identity",
                decided_by="system",
                decided_at=utc_now(),
            )
            added += 1
            self._propose_from_snapshot(key)
        return added

    def _propose_from_snapshot(self, model_key: str) -> None:
        if not self._snapshot.loaded:
            return
        norm = normalize_name(model_key)
        candidates = self._snapshot.by_norm.get(norm, [])
        seen: set[str] = set()
        for canonical_id, facts in candidates:
            if canonical_id in seen or canonical_id == model_key:
                continue
            seen.add(canonical_id)
            confidence = (
                0.9
                if model_key.split("/")[-1].lower() == canonical_id.split("/")[-1].lower()
                else 0.7
            )
            if confidence < _PROPOSAL_MIN_CONFIDENCE:
                continue
            self._canonical.setdefault(
                canonical_id,
                CanonicalModel(
                    canonical_id=canonical_id,
                    display_name=str(facts.get("name") or canonical_id),
                    family=_s(facts.get("family")),
                    vendor=canonical_id.split("/")[0] if "/" in canonical_id else None,
                    context_window=_i(facts.get("context")),
                    output_limit=_i(facts.get("output_limit")),
                    modalities_in=_sl(facts.get("input")),
                    modalities_out=_sl(facts.get("output")),
                    capabilities=[
                        c
                        for c in ("reasoning", "tool_call", "structured_output", "attachment")
                        if facts.get(c)
                    ],
                    release_date=_s(facts.get("release_date")),
                    source=AliasSource.CATALOG_MODELS_DEV,
                    source_ref=str(facts.get("_provider")),
                ),
            )
            key = (model_key, f"proposal:{canonical_id}")
            if key not in self._aliases:
                self._aliases[key] = ModelAlias(
                    alias=model_key,
                    canonical_id=canonical_id,
                    decision=AliasDecision.PROPOSED,
                    confidence=confidence,
                    source=AliasSource.CATALOG_MODELS_DEV,
                    reason=f"name match ({norm}) in models.dev provider '{facts.get('_provider')}'",
                )

    # -- alias admin -------------------------------------------------------------------------

    def list_aliases(
        self, *, decision: AliasDecision | None = None, alias: str | None = None
    ) -> list[ModelAlias]:
        self.sync_identities()
        rows = list(self._aliases.values())
        if decision is not None:
            rows = [a for a in rows if a.decision is decision]
        if alias is not None:
            rows = [a for a in rows if a.alias == alias]
        return sorted(rows, key=lambda a: (a.alias, a.canonical_id))

    def decide(
        self,
        alias: str,
        canonical_id: str,
        decision: AliasDecision,
        *,
        reason: str | None = None,
        decided_by: str = "admin",
    ) -> ModelAlias:
        """Confirm / reject / (re)propose ``alias → canonical_id``.

        Confirming moves the alias's identity: its SYSTEM identity alias is
        retargeted to ``canonical_id`` so the model_key appears under that
        canonical row. Rejecting restores the identity row.
        """
        if self._index.model_by_key(alias) is None:
            raise AliasNotFound(alias)
        self._canonical.setdefault(
            canonical_id,
            CanonicalModel(
                canonical_id=canonical_id, display_name=canonical_id, source=AliasSource.MANUAL
            ),
        )
        row = ModelAlias(
            alias=alias,
            canonical_id=canonical_id,
            decision=decision,
            confidence=1.0
            if decision is AliasDecision.CONFIRMED
            else 0.0
            if decision is AliasDecision.REJECTED
            else 0.8,
            source=AliasSource.MANUAL,
            reason=reason,
            decided_by=decided_by,
            decided_at=utc_now(),
        )
        self._aliases[(alias, f"proposal:{canonical_id}")] = row
        identity = self._aliases[(alias, None)]
        if decision is AliasDecision.CONFIRMED:
            self._aliases[(alias, None)] = identity.model_copy(
                update={
                    "canonical_id": canonical_id,
                    "decided_by": decided_by,
                    "decided_at": utc_now(),
                    "reason": reason or "confirmed alias",
                }
            )
        elif identity.canonical_id == canonical_id:
            self._aliases[(alias, None)] = identity.model_copy(
                update={
                    "canonical_id": alias,
                    "decided_by": decided_by,
                    "decided_at": utc_now(),
                    "reason": "alias rejected — identity restored",
                }
            )
        return row

    # -- resolution --------------------------------------------------------------------------

    def model_keys_for(self, canonical_or_key: str) -> list[str]:
        """CONFIRMED model_keys behind a canonical_id (or the key itself)."""
        self.sync_identities()
        keys = sorted(
            a.alias
            for (alias, tag), a in self._aliases.items()
            if tag is None
            and a.canonical_id == canonical_or_key
            and a.decision is AliasDecision.CONFIRMED
        )
        if keys:
            return keys
        if self._index.model_by_key(canonical_or_key) is not None:
            return [canonical_or_key]
        return []

    def resolve_policy(
        self, canonical_or_key: str, *, fallback: bool = True, provider_key: str | None = None
    ) -> ExplicitModelPolicy | ExplicitModelsPolicy:
        """canonical_id → an UPSTREAM model policy the Router already understands."""
        keys = self.model_keys_for(canonical_or_key)
        if not keys:
            proposed = [
                a
                for a in self.list_aliases(decision=AliasDecision.PROPOSED)
                if a.canonical_id == canonical_or_key
            ]
            if proposed:
                raise AliasUnconfirmed(canonical_or_key)
            # unknown: hand the literal key to the Router so IT produces the
            # upstream "explicit model not registered" exclusion (one error shape)
            keys = [canonical_or_key]
        if len(keys) == 1:
            return ExplicitModelPolicy(
                type="explicit_model",
                model_id=keys[0],
                provider_id=provider_key,
                allow_fallback=fallback,
                fallback_scope=FallbackScope.SAME_MODEL_DIFFERENT_PROVIDER if fallback else None,
            )
        return ExplicitModelsPolicy(
            type="explicit_models",
            models=[ModelRef(model_id=k, provider_id=provider_key) for k in keys],
            selection_strategy=SelectionStrategy.FALLBACK_CHAIN,
            allow_fallback=fallback,
        )

    # -- projection --------------------------------------------------------------------------

    def entries(self) -> list[CatalogEntry]:
        self.sync_identities()
        groups: dict[str, list[str]] = defaultdict(list)
        for (alias, tag), a in self._aliases.items():
            if tag is None and a.decision is AliasDecision.CONFIRMED:
                groups[a.canonical_id].append(alias)
        proposals: dict[str, int] = defaultdict(int)
        for (_alias, tag), a in self._aliases.items():
            if tag is not None and a.decision is AliasDecision.PROPOSED:
                proposals[a.canonical_id] += 1
        out: list[CatalogEntry] = []
        for canonical_id, keys in groups.items():
            refs: list[CatalogProviderRef] = []
            configured = 0
            eligible = 0
            for key in sorted(keys):
                model = self._index.model_by_key(key)
                if model is None:
                    continue
                for b in self._index.bindings_for_model(model.id):
                    configured += 1
                    pk = self._index.provider_key(b.provider_id) or "?"
                    ok, reason, retry = True, None, None
                    if self._signals is not None:
                        e = self._signals.eligibility(
                            b.provider_id, model.id, limits=b.limits_metadata
                        )
                        ok, reason, retry = e.eligible, e.reason, e.retry_after_ms
                    if ok:
                        eligible += 1
                    refs.append(
                        CatalogProviderRef(
                            provider_key=pk,
                            model_key=key,
                            provider_model_name=b.provider_model_name,
                            availability=b.availability.value,
                            eligible_now=ok,
                            reason=reason,
                            retry_after_ms=retry,
                        )
                    )
            out.append(
                CatalogEntry(
                    canonical=self._canonical[canonical_id],
                    model_keys=sorted(keys),
                    configured_provider_count=configured,
                    eligible_provider_count=eligible,
                    proposed_alias_count=proposals.get(canonical_id, 0),
                    providers=sorted(refs, key=lambda r: (r.provider_key, r.model_key)),
                )
            )
        out.sort(key=lambda e: e.canonical.canonical_id)
        return out

    def page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        q: str | None = None,
        include_providers: bool = True,
    ) -> CatalogPage:
        rows = self.entries()
        if q:
            needle = q.lower()
            rows = [
                r
                for r in rows
                if needle in r.canonical.canonical_id.lower()
                or needle in r.canonical.display_name.lower()
            ]
        total = len(rows)
        slice_ = rows[offset : offset + limit]
        if not include_providers:
            slice_ = [r.model_copy(update={"providers": []}) for r in slice_]
        st = self._index.stats
        return CatalogPage(
            items=slice_,
            total=total,
            offset=offset,
            limit=limit,
            built_at=utc_now(),
            index_stats={
                "models": st.models,
                "providers": st.providers,
                "bindings": st.bindings,
                "build_ms": round(st.build_ms, 2),
                "rebuilds": st.rebuilds,
                "snapshot_loaded": self.snapshot_loaded,
            },
        )

    def canonical(self, canonical_id: str) -> CanonicalModel | None:
        return self._canonical.get(canonical_id)
