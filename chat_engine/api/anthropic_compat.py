"""POST /v1/messages (CE-24/I) — Anthropic Messages dialect over the same governed pipeline.

A pure *mapper*: the request is translated into the OpenAI-compat ``ChatCompletionRequest``
and handed to the existing ``/v1/chat/completions`` handler (routing, admission, keys,
guardrails-free compat path, usage, pricing, error details all identical); the result is
translated back into Anthropic's ``message`` object / SSE event stream.

Supported: ``model``, ``system`` (string or text blocks), ``messages`` (string or text/
tool_result blocks), ``max_tokens`` (required by Anthropic), ``temperature``, ``top_p``,
``stop_sequences``, ``tools`` (input_schema → OpenAI function tools), ``tool_choice``
(auto|any|tool), ``stream``, ``metadata.user_id``. chat_engine extensions: ``provider``,
``required_capabilities``, ``skills``.
Errors use Anthropic's envelope ``{"type":"error","error":{"type","message"}}``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from chat_engine.api.openai_compat import ChatCompletionRequest, OAIMessage
from chat_engine.composition.runtime import Runtime


class AnthropicMessage(ContractModel):
    role: BoundedStr  # user | assistant
    content: str | list[JsonObject]


class AnthropicRequest(ContractModel):
    model: BoundedStr
    messages: list[AnthropicMessage] = Field(min_length=1)
    max_tokens: int = Field(ge=1)
    system: str | list[JsonObject] | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=0)  # accepted, not forwarded
    stop_sequences: list[str] | None = None
    stream: bool = False
    tools: list[JsonObject] | None = None
    tool_choice: JsonObject | None = None
    metadata: JsonObject | None = None
    # chat_engine extensions
    provider: BoundedStr | None = None
    required_capabilities: list[BoundedStr] = Field(default_factory=list)
    skills: list[BoundedStr] | None = None
    stream_fallback: BoundedStr | None = (
        None  # "buffer" for sync-only providers (see openai_compat)
    )


_ERR_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    409: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
}


def anthropic_error(
    status: int, message: str, *, details: JsonObject | None = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "error",
        "error": {"type": _ERR_TYPE_BY_STATUS.get(status, "api_error"), "message": message},
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


# --- request translation ---------------------------------------------------------------------------


def _text_of(content: str | list[JsonObject]) -> str:
    if isinstance(content, str):
        return content
    return "".join(str(b.get("text", "")) for b in content if b.get("type") == "text")


def to_openai_request(body: AnthropicRequest) -> ChatCompletionRequest:
    msgs: list[OAIMessage] = []
    if body.system:
        msgs.append(OAIMessage(role="system", content=_text_of(body.system)))
    for m in body.messages:
        if isinstance(m.content, str):
            msgs.append(OAIMessage(role=m.role, content=m.content))
            continue
        text_parts: list[str] = []
        tool_calls: list[JsonObject] = []
        for block in m.content:
            kind = block.get("type")
            if kind == "text":
                text_parts.append(str(block.get("text", "")))
            elif kind == "tool_use":
                tool_calls.append(
                    {
                        "id": str(block.get("id") or f"call_{uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or "tool"),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
            elif kind == "tool_result":
                inner = block.get("content")
                msgs.append(
                    OAIMessage(
                        role="tool",
                        tool_call_id=str(block.get("tool_use_id") or ""),
                        content=_text_of(inner) if isinstance(inner, str | list) else "",
                    )
                )
            elif kind == "image":
                text_parts.append("[image omitted]")
        if text_parts or tool_calls:
            msgs.append(
                OAIMessage(
                    role=m.role,
                    content="".join(text_parts) if text_parts else None,
                    tool_calls=tool_calls or None,
                )
            )
    tools = None
    if body.tools:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for t in body.tools
        ]
    tool_choice: str | JsonObject | None = None
    if body.tool_choice:
        tc = str(body.tool_choice.get("type") or "auto")
        if tc == "any":
            tool_choice = "required"
        elif tc == "tool":
            tool_choice = {"type": "function", "function": {"name": body.tool_choice.get("name")}}
        else:
            tool_choice = "auto"
    user = None
    if body.metadata and isinstance(body.metadata.get("user_id"), str):
        user = body.metadata["user_id"]
    return ChatCompletionRequest(
        model=body.model,
        messages=msgs,
        stream=body.stream,
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        stop=body.stop_sequences,
        tools=tools,
        tool_choice=tool_choice,
        user=user,
        provider=body.provider,
        required_capabilities=list(body.required_capabilities),
        skills=body.skills,
        stream_fallback=body.stream_fallback,
        stream_options={"include_usage": True} if body.stream else None,
    )


# --- response translation ---------------------------------------------------------------------------


def _usage(oai_usage: JsonObject | None) -> JsonObject:
    u = oai_usage or {}
    return {
        "input_tokens": int(u.get("prompt_tokens") or 0),
        "output_tokens": int(u.get("completion_tokens") or 0),
    }


def to_anthropic_message(oai: JsonObject, *, model: str) -> JsonObject:
    choice = (oai.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content: list[JsonObject] = []
    if isinstance(msg.get("content"), str) and msg["content"]:
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {"_raw": fn.get("arguments")}
        content.append(
            {
                "type": "tool_use",
                "id": str(tc.get("id") or f"toolu_{uuid4().hex[:8]}"),
                "name": str(fn.get("name") or "tool"),
                "input": args if isinstance(args, dict) else {"_raw": args},
            }
        )
    finish = choice.get("finish_reason")
    stop_reason = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}.get(
        str(finish), "end_turn"
    )
    out: JsonObject = {
        "id": f"msg_{uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": _usage(oai.get("usage")),
    }
    extra = oai.get("chat_engine")
    if isinstance(extra, dict):
        for k in ("execution_id", "provider_key", "model_key", "latency_ms"):
            if k in extra:
                out[k] = extra[k]
    return out


def _sse(event: str, data: JsonObject) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def to_anthropic_stream(oai_lines: AsyncIterator[bytes], *, model: str) -> AsyncIterator[str]:
    """Translate OpenAI chat.completion.chunk SSE into Anthropic message events."""
    msg_id = f"msg_{uuid4().hex[:24]}"
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )
    yield _sse(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    )
    stop_reason = "end_turn"
    usage: JsonObject = {"input_tokens": 0, "output_tokens": 0}
    error: JsonObject | None = None
    buf = b""
    async for raw in oai_lines:
        buf += raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        while b"\n\n" in buf:
            frame, buf = buf.split(b"\n\n", 1)
            for line in frame.decode("utf-8", "replace").splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if "error" in obj and not obj.get("choices"):
                    error = obj["error"]
                    continue
                if obj.get("usage"):
                    usage = _usage(obj["usage"])
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if isinstance(delta.get("content"), str) and delta["content"]:
                        yield _sse(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": delta["content"]},
                            },
                        )
                    fr = ch.get("finish_reason")
                    if fr:
                        stop_reason = {
                            "stop": "end_turn",
                            "length": "max_tokens",
                            "tool_calls": "tool_use",
                            "error": "end_turn",
                        }.get(str(fr), "end_turn")
                        if fr == "error":
                            error = error or {"type": "api_error", "message": "stream failed"}
    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    if error is not None:
        yield _sse(
            "error",
            {
                "type": "error",
                "error": {
                    "type": str(error.get("type") or "api_error"),
                    "message": str(error.get("message") or "stream failed"),
                },
            },
        )
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": usage.get("output_tokens", 0)},
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


def build_router(rt: Runtime, openai_router: APIRouter) -> APIRouter:
    router = APIRouter(tags=["anthropic-compat"])
    handler = next(
        r.endpoint  # type: ignore[attr-defined]
        for r in openai_router.routes
        if getattr(r, "path", "") == "/v1/chat/completions"
    )

    @router.post("/v1/messages")
    async def messages(request: Request, body: AnthropicRequest) -> Response:
        try:
            oai_req = to_openai_request(body)
        except ValueError as exc:
            return anthropic_error(400, str(exc))
        resp = await handler(request, oai_req)
        if isinstance(resp, StreamingResponse):
            return StreamingResponse(
                to_anthropic_stream(resp.body_iterator, model=body.model),  # type: ignore[arg-type]
                media_type="text/event-stream",
                headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            )
        raw = bytes(resp.body) if hasattr(resp, "body") else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return anthropic_error(502, "upstream returned a non-JSON body")
        if resp.status_code >= 400:
            err = data.get("error") or {}
            return anthropic_error(
                resp.status_code,
                str(err.get("message") or "request failed"),
                details={k: v for k, v in err.items() if k not in ("message", "type")} or None,
            )
        return JSONResponse(content=to_anthropic_message(data, model=body.model))

    return router


__all__ = [
    "AnthropicRequest",
    "anthropic_error",
    "build_router",
    "to_anthropic_message",
    "to_openai_request",
]
