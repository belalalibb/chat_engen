"""CE-04 D1 golden: merge only through confirmed aliases; counters per canonical."""

from __future__ import annotations

from uuid import uuid4

from core.contracts.domain import AuthType, ModelTier, Provider, ProviderStatus
from core.contracts.model_policy import ExplicitModelPolicy, ExplicitModelsPolicy

from chat_engine.catalog import AliasDecision, CatalogService
from chat_engine.composition.runtime import LocalEchoAdapter, Runtime, echo_manifest


def add_provider(rt: Runtime, key: str, model_names: list[str]) -> None:
    manifest = echo_manifest().model_copy(update={"id": key, "name": key})
    provider = Provider(
        id=uuid4(),
        provider_key=key,
        display_name=key,
        status=ProviderStatus.ACTIVE,
        auth_types=[AuthType.API_KEY],
        supports_account_pool=False,
    )
    rt.register_provider(
        provider=provider,
        manifest=manifest,
        adapter=LocalEchoAdapter(manifest),
        credential_ref=f"secret-ref://{key}",
        model_names=model_names,
        tier=ModelTier.MEDIUM,
    )


def test_same_model_key_across_1000_providers_is_one_row(runtime: Runtime) -> None:
    for i in range(1000):
        add_provider(runtime, f"p{i:04d}", ["shared-model"])
    svc: CatalogService = runtime.extensions["catalog"]
    rows = {e.canonical.canonical_id: e for e in svc.entries()}
    assert rows["shared-model"].configured_provider_count == 1000
    assert rows["shared-model"].eligible_provider_count == 1000
    assert rows["shared-model"].model_keys == ["shared-model"]


def test_proposed_does_not_merge_confirmed_merges_rejected_splits(runtime: Runtime) -> None:
    add_provider(runtime, "vendor_a", ["gpt-4o"])
    add_provider(runtime, "vendor_b", ["openai/gpt-4o"])
    svc: CatalogService = runtime.extensions["catalog"]

    before = {e.canonical.canonical_id: e for e in svc.entries()}
    assert before["gpt-4o"].configured_provider_count == 1
    assert before["openai/gpt-4o"].configured_provider_count == 1

    svc.decide("gpt-4o", "openai/gpt-4o", AliasDecision.PROPOSED, reason="looks alike")
    mid = {e.canonical.canonical_id: e for e in svc.entries()}
    assert mid["openai/gpt-4o"].configured_provider_count == 1
    assert mid["openai/gpt-4o"].proposed_alias_count >= 1

    svc.decide("gpt-4o", "openai/gpt-4o", AliasDecision.CONFIRMED, decided_by="owner")
    after = {e.canonical.canonical_id: e for e in svc.entries()}
    assert "gpt-4o" not in after
    assert after["openai/gpt-4o"].configured_provider_count == 2
    assert after["openai/gpt-4o"].model_keys == ["gpt-4o", "openai/gpt-4o"]

    policy = svc.resolve_policy("openai/gpt-4o")
    assert isinstance(policy, ExplicitModelsPolicy)
    assert [m.model_id for m in policy.models] == ["gpt-4o", "openai/gpt-4o"]

    svc.decide("gpt-4o", "openai/gpt-4o", AliasDecision.REJECTED, reason="different weights")
    split = {e.canonical.canonical_id: e for e in svc.entries()}
    assert split["gpt-4o"].configured_provider_count == 1
    assert split["openai/gpt-4o"].configured_provider_count == 1
    single = svc.resolve_policy("openai/gpt-4o")
    assert isinstance(single, ExplicitModelPolicy) and single.model_id == "openai/gpt-4o"


def test_models_dev_snapshot_yields_proposals_only(runtime: Runtime) -> None:
    add_provider(runtime, "groq_like", ["openai/gpt-oss-120b"])
    svc: CatalogService = runtime.extensions["catalog"]
    assert svc.snapshot_loaded
    proposals = [
        a
        for a in svc.list_aliases(decision=AliasDecision.PROPOSED)
        if a.alias == "openai/gpt-oss-120b"
    ]
    assert proposals
    rows = {e.canonical.canonical_id: e for e in svc.entries()}
    assert rows["openai/gpt-oss-120b"].configured_provider_count == 1


def test_catalog_api_pages_and_decides(client, runtime: Runtime) -> None:
    add_provider(runtime, "x", ["m-x"])
    page = client.get("/v1/catalog/models?limit=1").json()
    assert page["total"] >= 2 and len(page["items"]) == 1
    assert page["index_stats"]["bindings"] >= 2
    r = client.post(
        "/v1/catalog/aliases/m-x:decide",
        json={"decision": "confirmed", "canonical_id": "local-echo-1"},
    )
    assert r.status_code == 200, r.text
    one = client.get("/v1/catalog/models/local-echo-1").json()
    assert one["configured_provider_count"] == 2
    missing = client.post(
        "/v1/catalog/aliases/nope:decide", json={"decision": "confirmed", "canonical_id": "x"}
    )
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
