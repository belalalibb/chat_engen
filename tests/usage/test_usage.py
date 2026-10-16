"""CE-15: exactly one UsageRecord per request (sync / stream / partial / orchestration /
OpenAI-compat / failed), cost from the right pricing_ref, aggregates, redaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from chat_engine.observability.logging import JsonFormatter
from chat_engine.observability.redaction import redact, redact_text
from chat_engine.usage import (
    PricingBook,
    PricingUpsert,
    TokenCounts,
    UsageRecorder,
    tokens_from_usage,
)

from tests.orchestration.test_strategies import add_scripted
from tests.streaming.test_five_cases import add_streaming_provider, events_of


def _records(client):
    return client.get("/v1/usage/records", params={"limit": 1000}).json()["records"]


def test_pricing_book_snapshot_and_override() -> None:
    book = PricingBook()
    n = book.load_models_dev()
    assert n > 1000 and book.snapshot_loaded and book.snapshot_fetched_at
    rec = book.lookup("openai", "gpt-4o")
    assert (
        rec is not None
        and rec.source == "models.dev"
        and rec.ref.startswith("models.dev:openai/gpt-4o@")
    )
    assert rec.input_per_1m > 0 and rec.output_per_1m > 0
    # fallback by model tail
    assert book.lookup("some-proxy", "openai/gpt-4o") is not None
    # manual override wins for its provider
    manual = book.upsert(
        PricingUpsert(
            provider_key="local_echo", model_key="local-echo-1", input_per_1m=1.0, output_per_1m=2.0
        )
    )
    assert manual.source == "manual" and book.lookup("local_echo", "local-echo-1") is manual
    cost = book.cost_for(manual, TokenCounts(input=1_000_000, output=500_000, total=1_500_000))
    assert cost == 2.0
    cached = book.upsert(
        PricingUpsert(model_key="m", input_per_1m=10, output_per_1m=0, cached_input_per_1m=1)
    )
    assert (
        book.cost_for(cached, TokenCounts(input=1_000_000, cached=500_000, total=1_000_000)) == 5.5
    )
    assert book.lookup(None, "unknown-model") is None


def test_tokens_normalisation() -> None:
    t = tokens_from_usage({"prompt_tokens": 10, "completion_tokens": 5})
    assert (t.input, t.output, t.total, t.estimated) == (10, 5, 15, False)
    t = tokens_from_usage({"input_tokens": 3, "output_tokens": 4, "total_tokens": 9})
    assert t.total == 9
    t = tokens_from_usage({"units": 1}, fallback_chars=400)
    assert t.estimated and t.input == 100 and t.total == 100


def test_recorder_idempotent_and_unknown_cost() -> None:
    rec = UsageRecorder(PricingBook())
    tid = uuid4()
    a = rec.record(
        request_id="r1",
        tenant_id=tid,
        surface="chat",
        status="succeeded",
        provider_key="p",
        model_key="m",
        usage={"total_tokens": 7},
    )
    b = rec.record(
        request_id="r1",
        tenant_id=tid,
        surface="chat",
        status="failed",
        provider_key="p",
        model_key="m",
    )
    assert a is b and len(rec.all()) == 1 and not a.cost.known and a.cost.amount == 0
    rec.pricing.upsert(PricingUpsert(model_key="m", input_per_1m=1, output_per_1m=1))
    c = rec.record(
        request_id="r2",
        tenant_id=tid,
        surface="chat",
        status="succeeded",
        provider_key="p",
        model_key="m",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 0},
    )
    assert (
        c is not None
        and c.cost.known
        and c.cost.amount == 1.0
        and c.cost.pricing_ref.startswith("manual:*/m@")
    )
    rep = rec.report(group_by=["status"])
    assert {r.key["status"]: r.requests for r in rep.rows} == {"succeeded": 2}
    assert rep.rows[0].cost_unknown_requests == 1 and rep.total_cost_amount == 1.0
    off = UsageRecorder(PricingBook(), enabled=False)
    assert (
        off.record(
            request_id="x",
            tenant_id=tid,
            surface="chat",
            status="succeeded",
            provider_key=None,
            model_key=None,
        )
        is None
    )


def test_exactly_one_record_per_surface(client, runtime) -> None:
    add_streaming_provider(runtime, "sp15")
    flaky = add_streaming_provider(runtime, "flaky15", model="flaky-model")
    flaky.fail_after_words = 1
    add_scripted(runtime, "pa", ["model-a"], {"model-a": "A"})
    add_scripted(runtime, "pb", ["model-b"], {"model-b": "B"})
    add_scripted(runtime, "pbroken", ["broken-m"], {}, fail={"broken-m"})

    # 1 sync chat
    r = client.post("/v1/chat", json={"message": "one", "model": "local-echo-1"})
    assert r.status_code == 200
    cid = r.json()["conversation_id"]
    # 2 stream complete
    evs = events_of(
        client.post(
            "/v1/chat/stream",
            json={"conversation_id": cid, "message": "two words", "model": "s-model"},
        )
    )
    assert evs[-1]["type"] == "completed"
    # 3 stream partial
    evs = events_of(
        client.post(
            "/v1/chat/stream",
            json={"conversation_id": cid, "message": "a b c", "model": "flaky-model"},
        )
    )
    assert evs[-1]["type"] == "failed"
    # 4 orchestration (2 steps ⇒ 2 execution records; the chosen one is NOT duplicated)
    r = client.post(
        "/v1/chat",
        json={
            "conversation_id": cid,
            "message": "orch",
            "strategy": "sequential_handoff",
            "strategy_options": {"stages": ["model-a", "model-b"]},
        },
    )
    assert r.status_code == 200
    # 5 openai-compat
    assert (
        client.post(
            "/v1/chat/completions",
            json={"model": "local-echo-1", "messages": [{"role": "user", "content": "x"}]},
        ).status_code
        == 200
    )
    # 6 failed single turn
    r = client.post(
        "/v1/chat", json={"conversation_id": cid, "message": "fail", "model": "broken-m"}
    )
    assert r.status_code == 502

    recs = _records(client)
    by_surface: dict[str, list] = {}
    for x in recs:
        by_surface.setdefault(x["surface"], []).append(x)
    assert len(by_surface["chat"]) == 2  # sync ok + failed
    assert sorted(x["status"] for x in by_surface["chat"]) == ["failed", "succeeded"]
    assert len(by_surface["chat_stream"]) == 2
    assert sorted(x["status"] for x in by_surface["chat_stream"]) == ["partial", "succeeded"]
    assert len(by_surface["orchestration"]) == 2 and all(
        x["status"] == "succeeded" for x in by_surface["orchestration"]
    )
    assert len(by_surface["openai_compat"]) == 1
    assert len({x["request_id"] for x in recs}) == len(recs) == 7
    conv_recs = [x for x in recs if x.get("conversation_id") == cid]
    assert len(conv_recs) == 6
    # aggregates
    rep = client.get("/v1/usage", params={"group_by": "surface,status"}).json()
    assert rep["total_requests"] == 7 and {tuple(r["key"].values()) for r in rep["rows"]} >= {
        ("chat", "failed"),
        ("chat_stream", "partial"),
    }
    rep2 = client.get("/v1/usage", params={"group_by": "day", "conversation_id": cid}).json()
    assert rep2["total_requests"] == 6 and len(rep2["rows"]) == 1
    one = client.get(f"/v1/usage/records/{recs[0]['request_id']}").json()
    assert one["request_id"] == recs[0]["request_id"]
    assert client.get("/v1/usage/records/nope").status_code == 404


def test_cost_flows_from_pricing_ref(client) -> None:
    r = client.put(
        "/v1/admin/pricing",
        json={
            "provider_key": "local_echo",
            "model_key": "local-echo-1",
            "input_per_1m": 100.0,
            "output_per_1m": 0.0,
        },
    )
    assert r.status_code == 201
    assert (
        client.get(
            "/v1/admin/pricing", params={"model_key": "local-echo-1", "provider_key": "local_echo"}
        ).json()["pricing"]["source"]
        == "manual"
    )
    client.post("/v1/chat", json={"message": "x" * 400})
    rec = _records(client)[-1]
    assert rec["cost"]["pricing_ref"].startswith("manual:local_echo/local-echo-1@")
    assert rec["tokens"]["estimated"] is True and rec["cost"]["amount"] > 0
    book = client.get("/v1/admin/pricing").json()
    assert book["snapshot_loaded"] and book["total"] > 1000


def test_budget_denial_via_admission(client, runtime) -> None:
    from chat_engine.admission import Limits

    runtime.extensions["admission"].set_limits(
        "provider:local_echo", Limits.conservative().model_copy(update={"budget": 0.5})
    )
    r = client.post("/v1/chat", json={"message": "x"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "admission_denied"


def test_redaction_masks_secrets_and_content() -> None:
    s = "key ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123 and sk-abcdefghijklmnopqrstuvwxyz and Bearer abcdefghijklmnopqrstu"
    out = redact_text(s)
    assert (
        "ghp_" not in out
        and "sk-abc" not in out
        and "Bearer abc" not in out
        and out.count("[REDACTED]") == 3
    )
    payload = redact(
        {
            "messages": [{"role": "user", "content": "private"}],
            "api_key": "gsk_x",
            "provider_key": "groq",
            "nested": {"text": "hidden", "n": 3},
        }
    )
    assert payload["messages"].startswith("[REDACTED]") and payload["api_key"] == "[REDACTED]"
    assert payload["provider_key"] == "[REDACTED]"  # *_key is sensitive by rule
    assert payload["nested"]["text"] == "[REDACTED]" and payload["nested"]["n"] == 3
    rec = logging.LogRecord(
        "chat_engine",
        logging.INFO,
        __file__,
        1,
        "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
        None,
        None,
    )
    rec.ce = {"event": "x", "content": "secret body", "latency_ms": 5}  # type: ignore[attr-defined]
    line = json.loads(JsonFormatter().format(rec))
    assert "ghp_" not in line["msg"] and line["content"] == "[REDACTED]" and line["latency_ms"] == 5
    assert datetime.fromisoformat(line["ts"]).tzinfo is not None and datetime.now(UTC)


def test_otel_noop_without_endpoint() -> None:
    from chat_engine.observability.otel import build_tracer

    tr = build_tracer({})
    assert not tr.enabled
    with tr.span("x", messages=[{"content": "hidden"}], provider="p") as s:
        s.set_attribute("a", 1)
