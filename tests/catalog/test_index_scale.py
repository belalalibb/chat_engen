"""CE-04/CE-17 budget: catalog projection via CatalogIndex at N providers.

Upstream baseline (audit §6): catalog projection 404 ms @10k, linear scans.
Set CE_SCALE_N=10000 for the full run (evidence/ce04/scale_10k.txt).
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

from core.contracts.domain import (
    AuthType,
    BindingAvailability,
    ModelTier,
    Provider,
    ProviderModelBinding,
    ProviderStatus,
)

from chat_engine.catalog import CatalogService
from chat_engine.composition.runtime import LocalEchoAdapter, Runtime, echo_manifest, model_row

N = int(os.environ.get("CE_SCALE_N", "1000"))


def bulk(rt: Runtime, n: int) -> None:
    manifest = echo_manifest()
    adapter = LocalEchoAdapter(manifest)
    shared = model_row("shared-10k", tier=ModelTier.MEDIUM)
    rt.models.register(shared)
    for i in range(n):
        p = Provider(
            id=uuid4(),
            provider_key=f"bulk{i:05d}",
            display_name="b",
            status=ProviderStatus.ACTIVE,
            auth_types=[AuthType.API_KEY],
            supports_account_pool=False,
        )
        rt.providers.register(p, manifest.model_copy(update={"id": p.provider_key}))
        rt.adapters[p.id] = adapter
        rt.credential_refs[p.id] = "secret-ref://bulk"
        own = model_row(f"own-{i:05d}")
        rt.models.register(own)
        for m, name in ((shared, "shared"), (own, "own")):
            rt.bindings.register(
                ProviderModelBinding(
                    provider_id=p.id,
                    model_id=m.id,
                    provider_model_name=name,
                    availability=BindingAvailability.AVAILABLE,
                )
            )
    rt.extensions["catalog_index"].rebuild()


def test_catalog_projection_budget(runtime: Runtime) -> None:
    bulk(runtime, N)
    svc: CatalogService = runtime.extensions["catalog"]
    svc.entries()
    samples = []
    for _ in range(5):
        t = time.perf_counter()
        rows = svc.entries()
        samples.append((time.perf_counter() - t) * 1000)
    rows_by = {e.canonical.canonical_id: e for e in rows}
    assert rows_by["shared-10k"].configured_provider_count == N
    assert len(rows) == N + 2
    p50 = sorted(samples)[len(samples) // 2]
    budget_ms = 150 if N <= 1000 else 1500
    print(f"\ncatalog entries() n={N}: samples_ms={[round(s, 1) for s in samples]} p50={p50:.1f}")
    assert p50 < budget_ms, f"catalog projection too slow: p50={p50:.1f}ms (budget {budget_ms})"


def test_index_lookup_is_constant_time(runtime: Runtime) -> None:
    bulk(runtime, 2000)
    index = runtime.extensions["catalog_index"]
    model = index.model_by_key("shared-10k")
    t = time.perf_counter()
    for _ in range(1000):
        index.bindings_for_model(model.id)
    per_call_us = (time.perf_counter() - t) * 1e6 / 1000
    assert per_call_us < 50, f"indexed lookup should be ~O(1): {per_call_us:.1f}us"
