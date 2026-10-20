"""CE-24/A: LiteLLM community table as a *secondary* pricing + capability source.

Invariants:
* manual > models.dev > litellm — litellm never overrides an existing record;
* fills gaps models.dev does not price (vendor-prefixed keys, gateway-style tails);
* catalog facts from litellm become PROPOSED aliases only (counters unchanged);
* the import script converts per-token → per-1M and keeps only chat/embedding modes;
* CE_PRICING_LITELLM=0 disables the source (default on).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from chat_engine.catalog import AliasDecision, CatalogService
from chat_engine.catalog.contracts import AliasSource
from chat_engine.composition.runtime import Runtime, build_runtime
from chat_engine.usage import PricingBook, PricingUpsert, TokenCounts
from chat_engine.usage.pricing import LITELLM_SNAPSHOT

from tests.catalog.test_identity import add_provider

ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "import_litellm_catalog", ROOT / "scripts" / "import_litellm_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_pinned_litellm_snapshot_exists_and_is_trimmed() -> None:
    assert LITELLM_SNAPSHOT.is_file()
    doc = json.loads(LITELLM_SNAPSHOT.read_text())
    assert doc["fetched_at"] and doc["total"] > 1000
    modes = {m.get("mode") for m in doc["models"].values()}
    assert modes <= {"chat", "responses", "embedding", "completion"}
    # unit already converted: gpt-4o input is 2.5 USD / 1M, not 2.5e-6
    assert doc["models"]["gpt-4o"]["input_cost_per_token"] == 2.5


def test_import_script_convert_filters_and_scales(tmp_path: Path) -> None:
    mod = _load_script()
    raw = {
        "sample_spec": {"mode": "chat"},
        "x/chatty": {
            "mode": "chat",
            "litellm_provider": "x",
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 2e-6,
            "max_input_tokens": 1000,
            "supports_function_calling": True,
            "supports_vision": False,
            "irrelevant_field": "dropped",
        },
        "x/painter": {"mode": "image_generation", "litellm_provider": "x"},
        "noprov": {"mode": "chat", "input_cost_per_token": 1e-6},
    }
    doc = mod.convert(raw, fetched_at="2026-01-01T00:00:00+00:00", source="test")
    assert list(doc["models"]) == ["x/chatty"]
    row = doc["models"]["x/chatty"]
    assert row["input_cost_per_token"] == 1.0 and row["output_cost_per_token"] == 2.0
    assert "irrelevant_field" not in row and row["supports_function_calling"] is True
    out = tmp_path / "snap.json"
    out.write_text(json.dumps(doc))
    st = mod.stats(out)
    assert st["total"] == 1 and st["priced"] == 1 and st["by_mode"] == {"chat": 1}


def test_pricing_precedence_manual_models_dev_litellm() -> None:
    book = PricingBook()
    assert book.load_models_dev() > 1000
    assert book.load_litellm() > 1000 and book.litellm_loaded and book.litellm_fetched_at
    # models.dev wins where both price the same pair
    rec = book.lookup("openai", "gpt-4o")
    assert rec is not None and rec.source == "models.dev"
    # litellm fills a gap: vendor-prefixed key only litellm knows → gateway-style tail lookup
    gap = book.lookup("gw_deepai", "azure_ai/deepseek-v3.2")
    assert gap is not None and gap.source == "litellm" and gap.ref.startswith("litellm:")
    assert gap.input_per_1m >= 0 and gap.output_per_1m >= 0
    # manual beats both
    book.upsert(PricingUpsert(model_key="azure_ai/deepseek-v3.2", input_per_1m=9, output_per_1m=9))
    man = book.lookup(None, "azure_ai/deepseek-v3.2")
    assert man is not None and man.source == "manual" and man.input_per_1m == 9
    cost = PricingBook.cost_for(man, TokenCounts(input=1_000_000, output=0))
    assert cost == 9.0


def test_litellm_disabled_by_env() -> None:
    rt = build_runtime({"CE_PRICING_LITELLM": "0"})
    book: PricingBook = rt.extensions["usage_recorder"].pricing
    book.records()
    assert book.snapshot_loaded and not book.litellm_loaded


def test_litellm_facts_are_proposals_only(runtime: Runtime) -> None:
    # a key models.dev does not know but litellm does (Azure AI Foundry naming)
    add_provider(runtime, "gw_x", ["azure_ai/deepseek-v3.2"])
    svc: CatalogService = runtime.extensions["catalog"]
    assert svc.litellm_loaded
    props = [
        a
        for a in svc.list_aliases(decision=AliasDecision.PROPOSED)
        if a.alias == "azure_ai/deepseek-v3.2"
    ]
    lite = [a for a in props if a.source is AliasSource.CATALOG_LITELLM]
    assert lite and all("litellm" in (a.reason or "") for a in lite)
    # litellm-only vendors (e.g. Bedrock/Vertex spellings) show up as proposals with facts
    assert all(a.decision is AliasDecision.PROPOSED for a in lite)
    rows = {e.canonical.canonical_id: e for e in svc.entries()}
    # identity row only; the proposal did not merge/duplicate counters
    assert rows["azure_ai/deepseek-v3.2"].configured_provider_count == 1
    assert sum(1 for e in svc.entries() if "deepseek-v3.2" in e.canonical.canonical_id) == 1


def test_pricing_admin_reports_litellm(client) -> None:
    body = client.get("/v1/admin/pricing").json()
    assert body["snapshot_loaded"] and body["litellm_loaded"] and body["litellm_fetched_at"]
    one = client.get("/v1/admin/pricing", params={"model_key": "azure_ai/deepseek-v3.2"}).json()
    assert one["pricing"] and one["pricing"]["source"] == "litellm"
