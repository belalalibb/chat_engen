"""CE-11: Python SDK against the hermetic app (ASGI transport) — sync, async, SSE,
retry honouring retry_after_ms, error typing, and the consumer example's flow."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

SDK = Path(__file__).resolve().parents[2] / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from chat_engine_client import (  # noqa: E402
    AsyncChatEngine,
    ChatEngine,
    ChatEngineAPIError,
    RetryExhausted,
)
from chat_engine_client.client import parse_sse  # noqa: E402

from tests.streaming.test_five_cases import add_streaming_provider  # noqa: E402


class _SyncASGI(httpx.BaseTransport):
    """Run the ASGI app for a sync httpx.Client (ASGITransport is async-only)."""

    def __init__(self, app) -> None:
        self._inner = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def go() -> httpx.Response:
            resp = await self._inner.handle_async_request(request)
            await resp.aread()
            return httpx.Response(
                resp.status_code, headers=resp.headers, content=resp.content, request=request
            )

        return asyncio.run(go())


@pytest.fixture
def sdk(client) -> ChatEngine:
    return ChatEngine("http://testserver", transport=_SyncASGI(client.app), max_retries=2)


def test_sync_flow(sdk: ChatEngine, runtime) -> None:
    add_streaming_provider(runtime, "sp11")
    h = sdk.health()
    assert h["upstream_sha"].startswith("0a13556")
    assert any(m["id"] == "local-echo-1" for m in sdk.models())
    p = sdk.create_project("sdk proj", system_instructions="brief")
    r1 = sdk.chat("first", project_id=p["id"])
    assert r1.conversation_id and r1.turn.role == "assistant" and r1.turn.status == "complete"
    r2 = sdk.chat("second", conversation_id=r1.conversation_id)
    assert r2.turn.seq == 4 and r2.handoff["packet_id"]
    text = ""
    types = []
    for ev in sdk.chat_stream("stream me", conversation_id=r1.conversation_id, model="s-model"):
        types.append(ev.type)
        text += ev.delta
    assert types[0] == "started" and types[-1] == "completed" and text == "stream me"
    turns = sdk.turns(r1.conversation_id)
    assert len(turns) == 6 and turns[-1]["status"] == "complete"
    c = sdk.completions("local-echo-1", [{"role": "user", "content": "ping"}])
    assert c.id.startswith("chatcmpl-") and c.finish_reason == "stop"
    ex = sdk.execute("hello upstream")
    assert ex["status"] == "succeeded" and sdk.execution(ex["execution_id"])["status"]


def test_error_typing(sdk: ChatEngine) -> None:
    with pytest.raises(ChatEngineAPIError) as ei:
        sdk.get_conversation("00000000-0000-0000-0000-000000000000")
    assert ei.value.status == 404 and ei.value.code == "not_found"
    with pytest.raises(ChatEngineAPIError) as ei2:
        sdk.chat("")
    assert ei2.value.status == 422


def test_retry_honours_retry_after_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": "admission_denied",
                        "message": "busy",
                        "retryable": True,
                        "details": {"retry_after_ms": 50},
                    }
                },
            )
        return httpx.Response(200, json={"status": "ok"})

    import chat_engine_client.client as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))
    ce = ChatEngine("http://x", transport=httpx.MockTransport(handler), max_retries=3)
    assert ce.health() == {"status": "ok"}
    assert calls["n"] == 3 and slept == [0.05, 0.05]

    ce2 = ChatEngine("http://x", transport=httpx.MockTransport(handler), max_retries=0)
    calls["n"] = 0
    with pytest.raises(ChatEngineAPIError) as ei:
        ce2.health()
    assert ei.value.status == 429 and ei.value.retry_after_s == 0.05

    always = httpx.MockTransport(lambda r: httpx.Response(503, json={"error": {"code": "x"}}))
    with pytest.raises(RetryExhausted):
        ChatEngine("http://x", transport=always, max_retries=1).health()


def test_parse_sse_tolerant() -> None:
    lines = [
        ": comment",
        "id: e1",
        "event: message_delta",
        'data: {"seq": 1, "type": "message_delta", "stream_id": "s", "data": {"delta": "hi"}}',
        "",
        "data: [DONE]",
        "",
        'data: {"seq": 2, "type": "completed", "stream_id": "s", "data": {}}',
        "",
    ]
    evs = list(parse_sse(lines))
    assert len(evs) == 1 and evs[0].event_id == "e1" and evs[0].delta == "hi"


def test_async_flow(client, runtime) -> None:
    add_streaming_provider(runtime, "sp11")

    async def run() -> None:
        async with AsyncChatEngine(
            "http://testserver", transport=httpx.ASGITransport(app=client.app)
        ) as ce:
            r = await ce.chat("async first")
            got = ""
            async for ev in ce.chat_stream(
                "async stream", conversation_id=r.conversation_id, model="s-model"
            ):
                got += ev.delta
            assert got == "async stream"
            assert len(await ce.turns(r.conversation_id)) == 4
            c = await ce.completions("local-echo-1", [{"role": "user", "content": "x"}])
            assert c.finish_reason == "stop"

    asyncio.run(run())


def test_consumer_example_imports_nothing_from_reference() -> None:
    src = (Path(__file__).resolve().parents[2] / "examples/consumer_project/main.py").read_text()
    assert "apps." not in src and "from ui" not in src and "import ui" not in src
