"""Hermetic scale probe over general_ai_core registries + SimpleScoringRouter.
Run from the general_ai_core checkout: .venv/bin/python <path>/scale_probe.py
Measures routing decision latency and catalog projection with 1k / 10k providers. No network."""
import time, statistics
from uuid import uuid4
from core.contracts.domain import (Provider, ProviderStatus, AuthType, Model, ModelTier, Modality,
                                   ModelStatus, ProviderModelBinding, BindingAvailability)
from core.contracts.provider import ProviderManifest, ProviderOperation
from core.contracts.routing import RoutingRequest
from core.contracts.model_policy import ExplicitModelPolicy
from core.contracts.model_listing import ModelListEntry
from core.providers.registry import ProviderRegistry, ModelRegistry, BindingRegistry
from core.routing.router import SimpleScoringRouter

def manifest(i):
    return ProviderManifest.model_validate({"id":f"p{i}","name":f"P{i}","version":"1","status":"active",
      "auth":{"types":["api_key"]},"account_pool":{"supported":False},"capabilities":{"chat":True},
      "operations":["generate_text"],"models":{"discovery":"static"},"rate_limits":{"strategy":"provider_defined"},
      "health":{"checks":[]},"errors":{"mapping":"x"}})

def pct(v, p):
    v = sorted(v); return v[min(len(v)-1, int(len(v)*p))]

for N_PROV, N_MODELS in [(1000, 50), (10000, 200)]:
    P, M, B = ProviderRegistry(), ModelRegistry(), BindingRegistry()
    t0 = time.perf_counter(); provs = []
    for i in range(N_PROV):
        pr = Provider(id=uuid4(), provider_key=f"p{i}", display_name=f"P{i}", status=ProviderStatus.ACTIVE,
                      auth_types=[AuthType.API_KEY], supports_account_pool=False)
        P.register(pr, manifest(i)); provs.append(pr)
    models = []
    for j in range(N_MODELS):
        m = Model(id=uuid4(), model_key=f"model-{j}", display_name=f"M{j}", tier=ModelTier.MEDIUM,
                  modalities=[Modality.TEXT], capabilities=["chat"], quality_score=.8, reliability_score=.8,
                  cost_score=.8, speed_score=.8, status=ModelStatus.ACTIVE)
        M.register(m); models.append(m)
    for pr in provs:  # model-0 served by ALL providers
        B.register(ProviderModelBinding(provider_id=pr.id, model_id=models[0].id, provider_model_name="x",
                                        availability=BindingAvailability.AVAILABLE))
    for j, m in enumerate(models[1:], 1):  # others by 3 providers each
        for k in range(3):
            pr = provs[(j*7+k) % N_PROV]
            B.register(ProviderModelBinding(provider_id=pr.id, model_id=m.id, provider_model_name="x",
                                            availability=BindingAvailability.AVAILABLE))
    build = time.perf_counter() - t0
    router = SimpleScoringRouter(P, M, B)
    for mk in ["model-0", "model-5"]:
        lat = []
        for _ in range(20):
            t = time.perf_counter()
            router.route(RoutingRequest(operation=ProviderOperation.GENERATE_TEXT,
                                        model_policy=ExplicitModelPolicy(type="explicit_model", model_id=mk)))
            lat.append((time.perf_counter()-t)*1000)
        print(f"N_PROV={N_PROV} route explicit {mk}: p50={pct(lat,.5):.1f}ms p95={pct(lat,.95):.1f}ms")
    lat = []
    for _ in range(5):
        t = time.perf_counter(); router.route(RoutingRequest(operation=ProviderOperation.GENERATE_TEXT)); lat.append((time.perf_counter()-t)*1000)
    print(f"N_PROV={N_PROV} route AUTO ({N_MODELS} models): p50={statistics.median(lat):.1f}ms max={max(lat):.1f}ms")
    lat = []
    for _ in range(5):
        t = time.perf_counter(); [ModelListEntry.from_model(m, B.bindings_for_model(m.id)) for m in M.active_models()]; lat.append((time.perf_counter()-t)*1000)
    print(f"N_PROV={N_PROV} catalog projection ({N_MODELS} models): p50={statistics.median(lat):.1f}ms  build={build:.1f}s")
