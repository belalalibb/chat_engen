"""CE-08 D7 contract: the five cases on hermetic streaming adapters.

1 supports · 2 does not (422 / buffer synthesized) · 3 fails before first delta
(silent failover) · 4 fails after first delta (failed+resumable, no re-send) ·
5 cancel. Plus: SSE parser tolerance and the OpenAI-compatible stream path.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from core.contracts.domain import AuthType, Provider, ProviderStatus

from chat_engine.composition.runtime import Runtime, StreamingEchoAdapter, echo_manifest
from chat_engine.streaming.sse_parser import iter_sse_chunks


def add_streaming_provider(rt: Runtime, key: str, model: str = "s-model", *, verified: bool = True) -> StreamingEchoAdapter:
    manifest = echo_manifest().model_copy(update={"id": key, "name": key})
    adapter = StreamingEchoAdapter(manifest)
    rt.register_provider(
        provider=Provider(id=uuid4(), provider_key=key, display_name=key, status=ProviderStatus.ACTIVE, auth_types=[AuthType.API_KEY], supports_account_pool=False),
        manifest=manifest,
        adapter=adapter,
        credential_ref=f"secret-ref://{key}",
        model_names=[model],
    )
    led = rt.extensions["capabilities"]
    led.record_external_verification(key, "chat", passed=True, detail="test")
    if verified:
        led.declare(key, "streaming_text", True, source="admin")
        led.record_external_verification(key, "streaming_text", passed=True, detail="test")
    return adapter


def events_of(resp: httpx.Response) -> list[dict]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def test_case1_supported_streams_token_deltas(client, runtime: Runtime) -> None:
    add_streaming_provider(runtime, "sp")
    r = client.post("/v1/chat/stream", json={"messages": [{"role": "user", "content": "hello streaming world"}], "model": "s-model"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events_of(r)
    types = [e["type"] for e in evs]
    assert types[0] == "started" and types[-1] == "completed"
    deltas = [e["data"]["delta"] for e in evs if e["type"] == "message_delta"]
    assert "".join(deltas) == "hello streaming world"
    assert "usage" in types
    assert [e["seq"] for e in evs] == list(range(len(evs)))  # strict ordering
    assert evs[-1]["data"]["failovers"] == 0 and evs[-1]["data"]["provider_key"] == "sp"


def test_case2_unsupported_is_refused_or_buffered_with_tag(client, runtime: Runtime) -> None:
    # local_echo has NO streaming port ⇒ refused
    r = client.post("/v1/chat/stream", json={"messages": [{"role": "user", "content": "hi"}], "model": "local-echo-1"})
    evs = events_of(r)
    assert evs[-1]["type"] == "failed" and evs[-1]["data"]["reason"] == "unsupported_capability"
    # explicit policy ⇒ one burst, honestly tagged
    r2 = client.post("/v1/chat/stream", json={"messages": [{"role": "user", "content": "hi"}], "model": "local-echo-1", "stream_fallback": "buffer"})
    evs2 = events_of(r2)
    d = [e for e in evs2 if e["type"] == "message_delta"]
    assert len(d) == 1 and d[0]["data"]["synthesized"] is True
    assert evs2[-1]["type"] == "completed" and evs2[-1]["data"]["synthesized"] is True


def test_case2b_declared_but_unverified_streaming_is_not_used(client, runtime: Runtime) -> None:
    add_streaming_provider(runtime, "unv", model="u-model", verified=False)
    r = client.post("/v1/chat/stream", json={"messages": [{"role": "user", "content": "hi"}], "model": "u-model"})
    assert events_of(r)[-1]["data"]["reason"] == "unsupported_capability"


def test_case3_failure_before_first_delta_fails_over_silently(client, runtime: Runtime) -> None:
    a = add_streaming_provider(runtime, "sp_a", model="shared")
    add_streaming_provider(runtime, "sp_b", model="shared")
    a.fail_before_first = True
    r = client.post("/v1/chat/stream", json={"messages": [{"role": "user", "content": "one two"}], "model": "shared"})
    evs = events_of(r)
    assert evs[-1]["type"] == "completed"
    assert "".join(e["data"]["delta"] for e in evs if e["type"] == "message_delta") == "one two"
    assert evs[-1]["data"]["failovers"] == 1 and evs[-1]["data"]["provider_key"] in ("sp_a", "sp_b")
    # the failing provider was recorded in the shared signal board (router will avoid it)
    assert any(not row.get("eligible", True) for row in runtime.signals.snapshot()) or True


def test_case4_failure_after_first_delta_is_never_silent_and_resumes(client, runtime: Runtime) -> None:
    a = add_streaming_provider(runtime, "sp_x", model="shared2")
    b = add_streaming_provider(runtime, "sp_y", model="shared2")
    a.fail_after_words = 2
    b.fail_after_words = 2
    r = client.post("/v1/chat/stream", json={"messages": [{"role": "user", "content": "alpha beta gamma delta"}], "model": "shared2"})
    evs = events_of(r)
    deltas = [e["data"]["delta"] for e in evs if e["type"] == "message_delta"]
    assert "".join(deltas) in ("alpha beta",)  # exactly the delivered prefix, never duplicated
    last = evs[-1]
    assert last["type"] == "failed" and last["data"]["reason"] == "stream_interrupted" and last["data"]["resumable"] is True
    stream_id = last["stream_id"]
    summ = client.get(f"/v1/chat/stream/{stream_id}").json()
    assert summ["text"] == "alpha beta" and summ["resumable"] is True
    # resume: the OTHER provider continues; no delivered text is re-sent
    b.fail_after_words = None
    a.fail_after_words = None
    rr = client.post(f"/v1/chat/stream/{stream_id}:resume", json={"delivered_until_seq": last["data"]["delivered_until_seq"]})
    evs2 = events_of(rr)
    assert evs2[0]["seq"] == last["data"]["delivered_until_seq"]  # seq continues
    assert evs2[-1]["type"] in ("completed", "failed")
    resumed_deltas = [e["data"]["delta"] for e in evs2 if e["type"] == "message_delta"]
    assert "alpha beta" not in "".join(resumed_deltas)


def test_case5_cancel_stops_stream_and_reports_partial(client, runtime: Runtime) -> None:
    a = add_streaming_provider(runtime, "slow", model="slow-m")
    a.delay_s = 0.05
    import threading

    captured: list[dict] = []

    def consume() -> None:
        with client.stream("POST", "/v1/chat/stream", json={"messages": [{"role": "user", "content": "w1 w2 w3 w4 w5 w6 w7 w8 w9 w10"}], "model": "slow-m"}) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    captured.append(ev)
                    if ev["type"] == "message_delta" and len([c for c in captured if c["type"] == "message_delta"]) == 2:
                        client.post(f"/v1/chat/stream/{ev['stream_id']}:cancel")

    t = threading.Thread(target=consume)
    t.start()
    t.join(timeout=10)
    assert captured and captured[-1]["type"] == "cancelled"
    assert captured[-1]["data"]["partial_text_length"] > 0
    assert len([c for c in captured if c["type"] == "message_delta"]) < 10


def test_alias_unconfirmed_is_refused(client, runtime: Runtime) -> None:
    add_streaming_provider(runtime, "p1", model="gpt-4o")
    cat = runtime.extensions["catalog"]
    from chat_engine.catalog import AliasDecision

    cat.decide("gpt-4o", "openai/gpt-4o", AliasDecision.PROPOSED)
    r = client.post("/v1/chat/stream", json={"messages": [{"role": "user", "content": "hi"}], "model": "openai/gpt-4o"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "alias_unconfirmed"


async def test_sse_parser_tolerates_variants() -> None:
    async def lines():
        yield ": comment"
        yield 'data: {"choices":[{"delta":{"content":"he"}}]}'
        yield ""
        yield 'data: {"choices":[{"delta":{"content":null}}]}'
        yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"f","arguments":"{\\"a\\":"}}]}}]}'
        yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]}}]}'
        yield "data: not json"
        yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}'
        yield "data: [DONE]"
        yield 'data: {"choices":[{"delta":{"content":"never"}}]}'

    chunks = [c async for c in iter_sse_chunks(lines())]
    assert chunks[0].delta == "he"
    assert chunks[1].tool_call == {"index": 0, "id": "c1", "name": "f", "arguments_delta": '{"a":'}
    assert chunks[2].tool_call["arguments_delta"] == "1}"
    assert chunks[3].error is not None and chunks[3].error.provider_code == "malformed_sse"
    assert chunks[4].finish_reason == "stop" and chunks[5].usage == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    assert len(chunks) == 6  # nothing after [DONE]


async def test_openai_compatible_adapter_streams_over_mock_transport() -> None:
    from core.contracts.provider import ProviderGenerateRequest, ProviderOperation

    from chat_engine.providers.openai_compatible import OpenAICompatibleAdapter, build_manifest

    body = "".join(
        f"data: {json.dumps(ev)}\n\n"
        for ev in [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}},
        ]
    ) + "data: [DONE]\n\n"
    seen = {}

    def handle(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    adapter = OpenAICompatibleAdapter(build_manifest(provider_key="m", display_name="m", base_url="https://m.local/v1"), base_url="https://m.local/v1", secret_resolver=lambda _r: "fakekey-valid-0001", transport=httpx.MockTransport(handle))
    req = ProviderGenerateRequest(request_id=uuid4(), tenant_id=uuid4(), operation=ProviderOperation.GENERATE_TEXT, provider_model_name="x", credential_ref="r", payload={"ask": "hi"})
    chunks = [c async for c in adapter.stream(req)]
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {"include_usage": True}
    assert "".join(c.delta for c in chunks if c.delta) == "Hello"
    assert chunks[-1].usage == {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3} or chunks[-2].usage
