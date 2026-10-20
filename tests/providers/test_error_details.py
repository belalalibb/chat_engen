"""CE-24/G: error-detail normalization — ``details.rate_limit_type``,
``details.reason="context_window_exceeded"`` + ``max_input_tokens``; a context-window
overflow is request-indicting: HTTP 422 ``validation_error`` on /v1/chat, no tier
escalation in cost_tiered, no failover in streaming, never counted by the breaker.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
from core.contracts.domain import AuthType, Provider, ProviderStatus
from core.contracts.provider import (
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerateRequest,
    ProviderGenerateResponse,
)

from chat_engine.admission import BreakerPolicy, InMemoryAdmission, Reservation
from chat_engine.composition.runtime import Runtime, echo_manifest
from chat_engine.providers.error_details import (
    CONTEXT_WINDOW_CODE,
    breaker_category,
    context_window_facts,
    enrich_details,
    is_context_window_error,
    is_context_window_message,
    rate_limit_type,
)
from chat_engine.providers.openai_compatible import OpenAICompatibleAdapter
from chat_engine.streaming.contracts import StreamChunk
from tests.orchestration.test_strategies import ScriptedAdapter, add_scripted
from tests.streaming.test_five_cases import add_streaming_provider, events_of

OPENAI_MSG = (
    "This model's maximum context length is 128000 tokens. However, your messages "
    "resulted in 130123 tokens. Please reduce the length of the messages."
)
ANTHROPIC_MSG = "prompt is too long: 250000 tokens > 200000 maximum"


def _ctx_error() -> ProviderError:
    return ProviderError(
        category=ProviderErrorCategory.BAD_REQUEST,
        retryable=False,
        provider_code=CONTEXT_WINDOW_CODE,
        safe_message=(
            "prompt exceeds the model's context window; maximum 8000 tokens; "
            "requested 9000 tokens"
        ),
    )


# --- pure helpers ---------------------------------------------------------------------------------


def test_context_window_detection_and_facts() -> None:
    assert is_context_window_message(OPENAI_MSG)
    assert is_context_window_message(ANTHROPIC_MSG)
    assert is_context_window_message(None, "context_length_exceeded")
    assert not is_context_window_message("invalid api key", "invalid_api_key")
    f = context_window_facts(OPENAI_MSG)
    assert f["max_input_tokens"] == 128000 and f["requested_tokens"] == 130123
    f2 = context_window_facts(ANTHROPIC_MSG)
    assert f2["max_input_tokens"] == 200000 and f2["requested_tokens"] == 250000
    assert context_window_facts(None) == {}


def test_rate_limit_type_classification() -> None:
    assert rate_limit_type("rate_limit_exceeded", "Rate limit reached for requests per min") == (
        "requests"
    )
    assert rate_limit_type(None, "You exceeded your tokens per minute (TPM) limit") == "tokens"
    assert rate_limit_type("insufficient_quota", "billing hard limit") == "quota"
    assert rate_limit_type(None, "too many concurrent requests") == "concurrency"
    assert rate_limit_type("http_429", None, status=429) == "unknown"


def test_enrich_details_and_breaker_category() -> None:
    err = _ctx_error()
    assert is_context_window_error(err) and breaker_category(err) == "context_window"
    d = enrich_details({"execution_id": "x"}, err)
    assert d["reason"] == "context_window_exceeded"
    assert d["max_input_tokens"] == 8000 and d["requested_tokens"] == 9000
    # caller-set reason is preserved; marker moves to `cause`
    d_stream = enrich_details({"reason": "validation_error"}, err)
    assert d_stream["reason"] == "validation_error" and d_stream["cause"] == CONTEXT_WINDOW_CODE
    rl = ProviderError(
        category=ProviderErrorCategory.RATE_LIMITED,
        retryable=True,
        retry_after_ms=1500,
        provider_code="rate_limit_exceeded",
        safe_message="requests per minute exceeded",
    )
    assert enrich_details({}, rl) == {"rate_limit_type": "requests", "retry_after_ms": 1500}
    q = ProviderError(
        category=ProviderErrorCategory.QUOTA_EXCEEDED, retryable=False, safe_message="quota"
    )
    assert enrich_details({}, q)["rate_limit_type"] == "quota"
    assert enrich_details({"a": 1}, None) == {"a": 1}
    assert breaker_category(None) is None and breaker_category(rl) == "rate_limited"


def test_openai_compatible_adapter_classifies_context_window() -> None:
    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    r = httpx.Response(
        400,
        json={
            "error": {
                "message": OPENAI_MSG,
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        },
        request=req,
    )
    err = adapter._normalize_http(r)
    assert err.category is ProviderErrorCategory.BAD_REQUEST and not err.retryable
    assert err.provider_code == CONTEXT_WINDOW_CODE
    assert "128000" in err.safe_message and "130123" in err.safe_message
    # message-only detection (no code)
    r2 = httpx.Response(413, json={"error": {"message": ANTHROPIC_MSG}}, request=req)
    assert adapter._normalize_http(r2).provider_code == CONTEXT_WINDOW_CODE
    # an ordinary 400 is still bad_request without the marker
    r3 = httpx.Response(
        400,
        json={"error": {"message": "missing field", "code": "invalid_request"}},
        request=req,
    )
    assert adapter._normalize_http(r3).provider_code == "invalid_request"


# --- breaker never counts context-window failures ------------------------------------------------


async def test_breaker_ignores_context_window_even_without_policy() -> None:
    adm = InMemoryAdmission()  # legacy policy, no rules
    for _ in range(12):
        r = await adm.reserve(["provider:p"])
        assert isinstance(r, Reservation)
        await adm.settle(r, success=False, category="context_window")
    assert isinstance(await adm.reserve(["provider:p"]), Reservation)
    rule = BreakerPolicy.legacy().rule_for("context_window")
    assert rule is not None and rule.counts is False
    # explicit policy may still opt in
    opt = BreakerPolicy.parse('{"context_window": 2}').rule_for("context_window")
    assert opt is not None and opt.fails == 2


# --- surfaces ------------------------------------------------------------------------------------


class CtxAdapter(ScriptedAdapter):
    async def generate(self, request: ProviderGenerateRequest) -> ProviderGenerateResponse:
        model = request.provider_model_name
        self.calls.append({"model": model})
        if model in self.fail:
            return ProviderGenerateResponse(
                request_id=request.request_id,
                succeeded=False,
                output={},
                usage={},
                latency_ms=1,
                error=_ctx_error(),
            )
        return await super().generate(request)


def _add_ctx(rt: Runtime, key: str, models: list[str], fail: set[str]) -> CtxAdapter:
    manifest = echo_manifest().model_copy(update={"id": key, "name": key})
    adapter = CtxAdapter(manifest, {}, fail)
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


def test_chat_context_window_is_422_with_details(client, runtime: Runtime) -> None:
    _add_ctx(runtime, "ctxp", ["small-m"], {"small-m"})
    r = client.post("/v1/chat", json={"message": "x" * 50, "model": "small-m"})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["retryable"] is False
    assert err["details"]["reason"] == "context_window_exceeded"
    assert err["details"]["max_input_tokens"] == 8000
    assert err["details"]["provider_code"] == CONTEXT_WINDOW_CODE
    # the provider breaker did not count it
    snap = {s["scope"]: s for s in runtime.extensions["admission"].snapshot()}
    assert not snap.get("provider:ctxp", {}).get("breaker_open")


def test_cost_tiered_does_not_escalate_on_context_window(client, runtime: Runtime) -> None:
    small = _add_ctx(runtime, "ctxs", ["tiny-m"], {"tiny-m"})
    big = add_scripted(runtime, "ctxb", ["big-m"], {"big-m": "a thorough answer, ok"})
    r = client.post(
        "/v1/chat",
        json={
            "message": "q",
            "strategy": "cost_tiered",
            "strategy_options": {"tiers": ["tiny-m", "big-m"]},
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["reason"] == "context_window_exceeded"
    assert len(small.calls) == 1 and len(big.calls) == 0  # no escalation to the next tier


def test_stream_context_window_fails_final_without_failover(client, runtime: Runtime) -> None:
    a = add_streaming_provider(runtime, "sctx", "s-ctx")
    b = add_streaming_provider(runtime, "sok", "s-ctx")

    async def ctx_stream(request):
        yield StreamChunk(error=_ctx_error())

    a.stream = ctx_stream  # type: ignore[method-assign]
    hits = {"b": 0}
    orig = b.stream

    async def counting(request):
        hits["b"] += 1
        async for c in orig(request):
            yield c

    b.stream = counting  # type: ignore[method-assign]
    r = client.post("/v1/chat/stream", json={"message": "hello world", "model": "s-ctx"})
    evs = events_of(r)
    kinds = [e["type"] for e in evs]
    assert kinds[-1] == "failed" and "completed" not in kinds
    last = evs[-1]["data"]
    assert last["reason"] == "validation_error" and last["cause"] == "context_window_exceeded"
    assert last["final"] is True and last["max_input_tokens"] == 8000
    assert hits["b"] == 0  # no failover attempted
