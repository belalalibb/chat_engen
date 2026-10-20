"""CE-24/I: Anthropic /v1/messages — sync message object, SSE event stream, tools mapping,
error envelope, and parity with /v1/chat/completions (same governed handler)."""

from __future__ import annotations

import json

from chat_engine.api.anthropic_compat import (
    AnthropicRequest,
    to_anthropic_message,
    to_openai_request,
)

from tests.orchestration.test_strategies import add_scripted
from tests.streaming.test_five_cases import add_streaming_provider


def test_request_translation() -> None:
    req = AnthropicRequest(
        model="m",
        max_tokens=50,
        system=[{"type": "text", "text": "be brief"}],
        messages=[
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": 1}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "42"},
                    {"type": "text", "text": "thanks"},
                ],
            },
        ],
        tools=[
            {
                "name": "lookup",
                "description": "d",
                "input_schema": {"type": "object", "properties": {"q": {"type": "integer"}}},
            }
        ],
        tool_choice={"type": "any"},
        stop_sequences=["END"],
        metadata={"user_id": "u1"},
    )
    oai = to_openai_request(req)
    roles = [m.role for m in oai.messages]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert oai.messages[2].tool_calls[0]["function"]["name"] == "lookup"
    assert oai.messages[3].tool_call_id == "t1" and oai.messages[3].content == "42"
    assert oai.tools[0]["function"]["parameters"]["properties"]["q"]["type"] == "integer"
    assert oai.tool_choice == "required" and oai.stop == ["END"] and oai.max_tokens == 50
    assert oai.user == "u1" and oai.stream is False


def test_response_translation() -> None:
    oai = {
        "choices": [
            {
                "message": {
                    "content": "hello",
                    "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": '{"a":1}'}}],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
        "chat_engine": {"execution_id": "e1", "provider_key": "p"},
    }
    out = to_anthropic_message(oai, model="m")
    assert out["type"] == "message" and out["stop_reason"] == "tool_use"
    assert out["content"][0] == {"type": "text", "text": "hello"}
    assert out["content"][1]["type"] == "tool_use" and out["content"][1]["input"] == {"a": 1}
    assert out["usage"] == {"input_tokens": 3, "output_tokens": 5} and out["execution_id"] == "e1"


def test_messages_sync_and_errors(client, runtime) -> None:
    add_scripted(runtime, "ap", ["a-m"], {"a-m": "an anthropic-shaped answer"})
    r = client.post(
        "/v1/messages",
        json={"model": "a-m", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "message" and body["role"] == "assistant" and body["model"] == "a-m"
    assert body["content"] == [{"type": "text", "text": "an anthropic-shaped answer"}]
    assert (
        body["stop_reason"] == "end_turn"
        and body["provider_key"] == "ap"
        and body["id"].startswith("msg_")
    )
    assert body["usage"]["input_tokens"] >= 0
    # missing max_tokens → 422 (contract) ; unknown model → not_found envelope
    assert (
        client.post(
            "/v1/messages", json={"model": "a-m", "messages": [{"role": "user", "content": "hi"}]}
        ).status_code
        == 422
    )
    nf = client.post(
        "/v1/messages",
        json={"model": "nope", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert nf.status_code in (404, 503) and nf.json()["type"] == "error"
    assert nf.json()["error"]["type"] in ("not_found_error", "overloaded_error")
    # same handler → a usage record on the openai_compat surface exists
    recs = client.get("/v1/usage/records", params={"limit": 50}).json()["records"]
    assert any(x["provider_key"] == "ap" for x in recs)


def test_messages_stream_events(client, runtime) -> None:
    add_streaming_provider(runtime, "asp", "a-s")
    with client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "a-s",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "one two three"}],
        },
    ) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    events = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
    assert events[0] == "message_start" and events[1] == "content_block_start"
    assert "content_block_delta" in events and events[-2:] == ["message_delta", "message_stop"]
    deltas = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    streamed = "".join(d["delta"]["text"] for d in deltas if d.get("type") == "content_block_delta")
    assert "one two three" in streamed
    assert any(
        d.get("type") == "message_delta" and d["delta"]["stop_reason"] == "end_turn" for d in deltas
    )


def test_messages_stream_fallback_buffer_for_sync_only_provider(client, runtime) -> None:
    """CE-24/I: ``stream_fallback:"buffer"`` reaches the streaming service through the dialect,
    so gateway (sync-only) providers produce a real text_delta instead of an error event."""
    add_scripted(runtime, "asp2", ["a-sync"], {"a-sync": "buffered via anthropic"})
    r = client.post(
        "/v1/messages",
        json={
            "model": "a-sync",
            "max_tokens": 32,
            "stream": True,
            "stream_fallback": "buffer",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    types = [e["type"] for e in events]
    assert "error" not in types and types[0] == "message_start" and types[-1] == "message_stop"
    text = "".join(e["delta"]["text"] for e in events if e["type"] == "content_block_delta")
    assert text == "buffered via anthropic"
