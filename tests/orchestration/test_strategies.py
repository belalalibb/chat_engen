"""CE-12: the three strategies on scripted hermetic adapters, with full traces.

- sequential_handoff: B receives A's output in the SAME packet (golden D4 shape);
  A→B after A fails with 429 is covered by the router fallback test below.
- judge_select: heuristic judge picks by output_spec; LLM judge pick honoured.
- cost_tiered: upgrades exactly once; accepted cheap tier never upgrades.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from chat_engine.composition.runtime import LocalEchoAdapter, Runtime, echo_manifest
from chat_engine.orchestration import OutputSpec, score_against_spec
from core.contracts.domain import AuthType, Provider, ProviderStatus
from core.contracts.provider import (
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerateRequest,
    ProviderGenerateResponse,
)


class ScriptedAdapter(LocalEchoAdapter):
    """Reply per model name; ``fail`` set ⇒ normalized error; records every call."""

    def __init__(self, manifest, replies: dict[str, str], fail: set[str] | None = None):
        super().__init__(manifest)
        self.replies = replies
        self.fail = fail or set()
        self.calls: list[dict] = []

    async def generate(self, request: ProviderGenerateRequest) -> ProviderGenerateResponse:
        model = request.provider_model_name
        self.calls.append({"model": model, "messages": list(request.payload.get("messages", []))})
        if model in self.fail:
            return ProviderGenerateResponse(
                request_id=request.request_id,
                succeeded=False,
                output={},
                usage={},
                latency_ms=1,
                error=ProviderError(
                    category=ProviderErrorCategory.RATE_LIMITED,
                    retryable=True,
                    retry_after_ms=10,
                    safe_message="scripted 429",
                ),
            )
        text = self.replies.get(model, f"{model} says: {request.payload.get('ask', '')}")
        return ProviderGenerateResponse(
            request_id=request.request_id,
            succeeded=True,
            output={"content": text},
            usage={"total_tokens": 10 + len(text) // 4, "units": 1},
            latency_ms=2,
        )


def add_scripted(
    rt: Runtime, key: str, models: list[str], replies: dict[str, str], fail: set[str] | None = None
) -> ScriptedAdapter:
    manifest = echo_manifest().model_copy(update={"id": key, "name": key})
    adapter = ScriptedAdapter(manifest, replies, fail)
    rt.register_provider(
        provider=Provider(
            id=uuid4(),
            provider_key=key,
            display_name=key,
            status=ProviderStatus.ACTIVE,
            auth_types=[AuthType.API_KEY],
            supports_account_pool=False,
        ),
        manifest=manifest,
        adapter=adapter,
        credential_ref=f"secret-ref://{key}",
        model_names=models,
    )
    rt.extensions["capabilities"].record_external_verification(key, "chat", passed=True, detail="t")
    return adapter


def _chat(client, **body):
    return client.post("/v1/chat", json=body)


# --- output_spec ---------------------------------------------------------------------------------


def test_score_against_spec() -> None:
    spec = OutputSpec(min_chars=5, must_include=["ok"], must_not_include=["bad"], json_object=True)
    s, f = score_against_spec('{"ok": 1}', spec)
    assert s == 1.0 and f == []
    s, f = score_against_spec("bad", spec)
    assert s == 0.0 and set(f) == {
        "min_chars:5",
        "must_include:ok",
        "must_not_include:bad",
        "json_object",
    }
    assert score_against_spec("anything", OutputSpec()) == (1.0, [])


# --- sequential_handoff --------------------------------------------------------------------------


def test_sequential_handoff_passes_draft_in_packet(client, runtime: Runtime) -> None:
    a = add_scripted(runtime, "pa", ["model-a"], {"model-a": "DRAFT from A"})
    b = add_scripted(runtime, "pb", ["model-b"], {"model-b": "FINAL from B"})
    r = _chat(
        client,
        message="write a haiku",
        strategy="sequential_handoff",
        strategy_options={
            "stages": ["model-a", "model-b"],
            "stage_instructions": ["", "Polish it."],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["turn"]["parts"][0]["text"] == "FINAL from B"
    assert body["model_key"] == "model-b"
    tr = body["trace"]
    assert tr["strategy"] == "sequential_handoff" and tr["selected_step"] == 2
    assert [s["role"] for s in tr["steps"]] == ["draft", "continue"]
    assert all(s["status"] == "succeeded" and s["execution_id"] for s in tr["steps"])
    assert tr["total_usage"]["total_tokens"] > 0
    # B saw A's draft as an assistant message followed by the stage instruction
    assert len(a.calls) == 1 and len(b.calls) == 1
    roles = [m["role"] for m in b.calls[0]["messages"]]
    assert roles[-2:] == ["assistant", "user"]
    assert b.calls[0]["messages"][-2]["content"] == "DRAFT from A"
    assert b.calls[0]["messages"][-1]["content"] == "Polish it."
    # the same packet id threads both steps
    assert body["handoff"]["packet_id"]
    # persisted trace on the turn
    turns = client.get(f"/v1/conversations/{body['conversation_id']}/turns").json()["turns"]
    assert turns[-1]["metadata"]["trace"]["selected_step"] == 2
    assert turns[-1]["metadata"]["strategy"] == "sequential_handoff"


def test_sequential_handoff_stage_failover_same_packet(client, runtime: Runtime) -> None:
    """Golden D4: stage A on provider pa fails (429) → router fallback to another
    provider serving the SAME model with the same packet; B still receives the draft."""
    add_scripted(runtime, "pa", ["model-a"], {}, fail={"model-a"})
    alt = add_scripted(runtime, "pa2", ["model-a"], {"model-a": "DRAFT via pa2"})
    b = add_scripted(runtime, "pb", ["model-b"], {"model-b": "FINAL"})
    r = _chat(
        client,
        message="q",
        strategy="sequential_handoff",
        strategy_options={"stages": ["model-a", "model-b"]},
    )
    assert r.status_code == 200, r.text
    tr = r.json()["trace"]
    assert tr["steps"][0]["provider_key"] == "pa2" and len(alt.calls) == 1
    assert b.calls[0]["messages"][-2]["content"] == "DRAFT via pa2"


def test_sequential_handoff_requires_two_stages(client) -> None:
    r = _chat(
        client, message="q", strategy="sequential_handoff", strategy_options={"stages": ["x"]}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "conflict"
    r = _chat(client, message="q", strategy="nope")
    assert r.status_code == 422 and "allowed" in r.json()["error"]["details"]


# --- judge_select --------------------------------------------------------------------------------


def test_judge_select_heuristic_by_output_spec(client, runtime: Runtime) -> None:
    add_scripted(
        runtime,
        "pj",
        ["c1", "c2", "c3"],
        {"c1": "short", "c2": '{"answer": "yes", "ok": true}', "c3": "a longer plain answer ok"},
    )
    r = _chat(
        client,
        message="answer as json",
        strategy="judge_select",
        strategy_options={
            "candidates": ["c1", "c2", "c3"],
            "output_spec": {"json_object": True, "must_include": ["ok"], "min_chars": 8},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_key"] == "c2"
    tr = body["trace"]
    assert tr["selected_step"] == 2
    by = {s["step"]: s for s in tr["steps"]}
    assert by[2]["spec_score"] == 1.0 and by[1]["spec_score"] < by[3]["spec_score"] < 1.0
    assert "must_include:ok" in by[1]["spec_failures"]
    assert len(tr["steps"]) == 3 and "heuristic" not in tr["decision_reason"]


def test_judge_select_llm_judge_pick_and_failed_candidate(client, runtime: Runtime) -> None:
    add_scripted(
        runtime,
        "pj",
        ["c1", "c2", "judge-m"],
        {"c1": "alpha", "c2": "beta", "judge-m": "The best is [1]."},
        fail=set(),
    )
    add_scripted(runtime, "pbroken", ["c-broken"], {}, fail={"c-broken"})
    r = _chat(
        client,
        message="pick",
        strategy="judge_select",
        strategy_options={"candidates": ["c1", "c2", "c-broken"], "judge": "judge-m"},
    )
    assert r.status_code == 200, r.text
    tr = r.json()["trace"]
    roles = [s["role"] for s in tr["steps"]]
    assert roles == ["candidate", "candidate", "candidate", "judge"]
    assert tr["steps"][2]["status"] == "failed" and "execution_failed" in tr["steps"][2]["reason"]
    assert tr["selected_step"] == 1 and "judge model chose [1]" in tr["decision_reason"]
    assert r.json()["turn"]["parts"][0]["text"] == "alpha"


def test_judge_select_all_fail_is_502_with_failed_turn(client, runtime: Runtime) -> None:
    add_scripted(runtime, "pbroken", ["x1", "x2"], {}, fail={"x1", "x2"})
    r = _chat(
        client, message="q", strategy="judge_select", strategy_options={"candidates": ["x1", "x2"]}
    )
    assert r.status_code == 502 and r.json()["error"]["code"] == "execution_failed"
    turn_id = r.json()["error"]["details"]["turn_id"]
    assert turn_id


# --- cost_tiered ---------------------------------------------------------------------------------


def test_cost_tiered_upgrades_exactly_once(client, runtime: Runtime) -> None:
    cheap = add_scripted(runtime, "pcheap", ["cheap-m"], {"cheap-m": "meh"})
    good = add_scripted(runtime, "pgood", ["good-m"], {"good-m": "a thorough answer, ok"})
    r = _chat(
        client,
        message="q",
        strategy="cost_tiered",
        strategy_options={"tiers": ["cheap-m", "good-m"], "output_spec": {"min_chars": 10}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_key"] == "good-m" and len(cheap.calls) == 1 and len(good.calls) == 1
    tr = body["trace"]
    assert tr["upgrades"] == 1 and tr["selected_step"] == 2
    assert tr["steps"][0]["spec_failures"] == ["min_chars:10"]
    assert "upgrade once" in tr["steps"][0]["reason"]


def test_cost_tiered_accepts_cheap_without_upgrade(client, runtime: Runtime) -> None:
    cheap = add_scripted(runtime, "pcheap", ["cheap-m"], {"cheap-m": "good enough answer"})
    good = add_scripted(runtime, "pgood", ["good-m"], {"good-m": "never called"})
    r = _chat(
        client,
        message="q",
        strategy="cost_tiered",
        strategy_options={"tiers": ["cheap-m", "good-m"], "output_spec": {"min_chars": 5}},
    )
    assert r.status_code == 200
    tr = r.json()["trace"]
    assert tr["upgrades"] == 0 and tr["selected_step"] == 1 and r.json()["model_key"] == "cheap-m"
    assert len(cheap.calls) == 1 and len(good.calls) == 0


def test_cost_tiered_final_tier_failing_spec_is_still_returned(client, runtime: Runtime) -> None:
    add_scripted(runtime, "pcheap", ["cheap-m"], {"cheap-m": "x"})
    add_scripted(runtime, "pgood", ["good-m"], {"good-m": "yy"})
    r = _chat(
        client,
        message="q",
        strategy="cost_tiered",
        strategy_options={"tiers": ["cheap-m", "good-m"], "output_spec": {"min_chars": 100}},
    )
    assert r.status_code == 200
    tr = r.json()["trace"]
    assert tr["upgrades"] == 1 and tr["selected_step"] == 2
    assert tr["steps"][1]["reason"] == "final tier (no further upgrade)"


def test_cost_tiered_derives_tiers_when_absent(client) -> None:
    r = _chat(client, message="q", strategy="cost_tiered")
    assert r.status_code == 200, r.text
    assert r.json()["trace"]["strategy"] == "cost_tiered"


def test_sync_chat_tolerates_stream_fallback_knob(client) -> None:
    """Regression (CE-20 live smoke): the UI reuses one body for /v1/chat and
    /v1/chat/stream; the stream-only ``stream_fallback`` knob must not 422 the sync path."""
    r = _chat(client, message="q", strategy="cost_tiered", stream_fallback="buffer")
    assert r.status_code == 200, r.text
    assert r.json()["trace"]["strategy"] == "cost_tiered"


# --- governance ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steps_go_through_admission(runtime: Runtime) -> None:
    from chat_engine.admission import Limits
    from chat_engine.conversations import ChatRequest

    add_scripted(runtime, "pa", ["model-a"], {"model-a": "A"})
    add_scripted(runtime, "pb", ["model-b"], {"model-b": "B"})
    adm = runtime.extensions["admission"]
    adm.set_limits("provider:pb", Limits.conservative().model_copy(update={"budget": 0.5}))
    svc = runtime.extensions["chat"]
    from chat_engine.conversations import ChatError

    with pytest.raises(ChatError) as ei:
        await svc.chat(
            ChatRequest(
                message="q",
                strategy="sequential_handoff",
                strategy_options={"stages": ["model-a", "model-b"]},
            )
        )
    assert ei.value.code == "admission_denied" and ei.value.details["step_model"] == "model-b"


def test_default_single_strategy_unchanged(client) -> None:
    r = _chat(client, message="plain")
    assert r.status_code == 200 and r.json().get("trace") is None
