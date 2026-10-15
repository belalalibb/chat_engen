"""CatalogIndex — O(1) lookups over the upstream registries (D1 / P0-6).

Upstream ``BindingRegistry.bindings_for_model`` is a linear scan (measured in
CHAT_ENGINE_REALITY_AUDIT §6: catalog projection 404 ms @10k providers). This
index is a READ layer: it never mutates the registries; it is rebuilt when the
runtime registers/removes a provider (``Runtime.register_provider`` calls
``rebuild``) and lazily if it detects a size change.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from core.contracts.domain import Model, ProviderModelBinding
from core.providers.registry import BindingRegistry, ModelRegistry, ProviderRegistry

from chat_engine.util.time import utc_now


@dataclass
class IndexStats:
    models: int = 0
    providers: int = 0
    bindings: int = 0
    built_at: datetime = field(default_factory=utc_now)
    build_ms: float = 0.0
    rebuilds: int = 0


class CatalogIndex:
    def __init__(
        self, providers: ProviderRegistry, models: ModelRegistry, bindings: BindingRegistry
    ) -> None:
        self._providers = providers
        self._models = models
        self._bindings = bindings
        self._bindings_by_model: dict[UUID, list[ProviderModelBinding]] = {}
        self._bindings_by_provider: dict[UUID, list[ProviderModelBinding]] = {}
        self._provider_key_by_id: dict[UUID, str] = {}
        self._model_by_key: dict[str, Model] = {}
        self._binding_count = -1
        self.stats = IndexStats()
        self.rebuild()

    # -- maintenance -----------------------------------------------------------------

    def rebuild(self) -> None:
        import time

        started = time.perf_counter()
        by_model: dict[UUID, list[ProviderModelBinding]] = defaultdict(list)
        by_provider: dict[UUID, list[ProviderModelBinding]] = defaultdict(list)
        # BindingRegistry exposes no iterator; read its mapping once (private but
        # stable in the pinned SHA — guarded by tests/compat).
        raw = getattr(self._bindings, "_bindings", {})
        for binding in raw.values():
            by_model[binding.model_id].append(binding)
            by_provider[binding.provider_id].append(binding)
        self._bindings_by_model = dict(by_model)
        self._bindings_by_provider = dict(by_provider)
        self._provider_key_by_id = {
            self._providers.get(key).provider.id: key for key in self._providers.all_keys()
        }
        self._model_by_key = {m.model_key: m for m in self._models.all_models()}
        self._binding_count = len(raw)
        self.stats = IndexStats(
            models=len(self._model_by_key),
            providers=len(self._provider_key_by_id),
            bindings=self._binding_count,
            built_at=utc_now(),
            build_ms=(time.perf_counter() - started) * 1000,
            rebuilds=self.stats.rebuilds + 1,
        )

    def _ensure_fresh(self) -> None:
        """Cheap staleness check (O(1)): sizes of the registries' own dicts.

        Reading ``all_keys()`` here would be O(N log N) per lookup — measured as
        the quadratic hot-spot at 10k providers (17 s/projection). Mutations go
        through ``Runtime.register_provider/unregister_provider`` which call
        ``rebuild()`` explicitly; this guard only catches direct registry edits.
        """
        raw = getattr(self._bindings, "_bindings", {})
        prov = getattr(self._providers, "_by_key", None)
        n_prov = len(prov) if prov is not None else self.stats.providers
        if len(raw) != self._binding_count or n_prov != self.stats.providers:
            self.rebuild()

    # -- reads -------------------------------------------------------------------------

    def bindings_for_model(self, model_id: UUID) -> list[ProviderModelBinding]:
        self._ensure_fresh()
        return self._bindings_by_model.get(model_id, [])

    def bindings_for_provider(self, provider_id: UUID) -> list[ProviderModelBinding]:
        self._ensure_fresh()
        return self._bindings_by_provider.get(provider_id, [])

    def provider_key(self, provider_id: UUID) -> str | None:
        self._ensure_fresh()
        return self._provider_key_by_id.get(provider_id)

    def model_by_key(self, model_key: str) -> Model | None:
        self._ensure_fresh()
        return self._model_by_key.get(model_key)

    def model_keys(self) -> list[str]:
        self._ensure_fresh()
        return sorted(self._model_by_key)

    def provider_keys_for_model(self, model_id: UUID) -> list[str]:
        keys = {self.provider_key(b.provider_id) for b in self.bindings_for_model(model_id)}
        return sorted(k for k in keys if k is not None)
