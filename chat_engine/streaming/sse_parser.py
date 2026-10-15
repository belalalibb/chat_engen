"""Tolerant OpenAI-dialect SSE parser → StreamChunk (CE-08).

Providers differ in small ways (``data: [DONE]`` presence, usage-only final
chunks, tool-call argument deltas, ``content: null``). This parser accepts
them all and never raises on a malformed line — it yields an error chunk and
lets the service decide (fail before/after first delta).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from core.contracts.provider import ProviderError, ProviderErrorCategory

from chat_engine.streaming.contracts import StreamChunk


def parse_sse_data_line(line: str) -> dict[str, Any] | None | str:
    """Return parsed JSON, ``"DONE"`` for the terminator, or None for non-data lines."""
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload:
        return None
    if payload == "[DONE]":
        return "DONE"
    try:
        obj = json.loads(payload)
    except ValueError:
        return {"_malformed": payload[:200]}
    return obj if isinstance(obj, dict) else {"_malformed": payload[:200]}


def chunks_from_event(obj: dict[str, Any]) -> list[StreamChunk]:
    if "_malformed" in obj:
        return [
            StreamChunk(
                error=ProviderError(
                    category=ProviderErrorCategory.RETRYABLE_SERVER_ERROR,
                    retryable=True,
                    provider_code="malformed_sse",
                    safe_message="provider sent a malformed stream event",
                )
            )
        ]
    if "error" in obj and obj["error"]:
        err = obj["error"] if isinstance(obj["error"], dict) else {}
        code = str(err.get("code") or err.get("type") or "stream_error")[:64]
        return [
            StreamChunk(
                error=ProviderError(
                    category=ProviderErrorCategory.RETRYABLE_SERVER_ERROR,
                    retryable=True,
                    provider_code=code,
                    safe_message="provider reported an error mid-stream",
                )
            )
        ]
    out: list[StreamChunk] = []
    model = obj.get("model") if isinstance(obj.get("model"), str) else None
    for choice in obj.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            out.append(StreamChunk(delta=content, raw_model=model))
        for tc in delta.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                out.append(
                    StreamChunk(
                        tool_call={
                            "index": tc.get("index", 0),
                            "id": tc.get("id"),
                            "name": fn.get("name"),
                            "arguments_delta": fn.get("arguments", ""),
                        }
                    )
                )
        finish = choice.get("finish_reason")
        if isinstance(finish, str) and finish:
            out.append(StreamChunk(finish_reason=finish))
    usage = obj.get("usage")
    if isinstance(usage, dict) and usage:
        out.append(
            StreamChunk(
                usage={
                    k: usage[k]
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage.get(k), int)
                }
            )
        )
    return out


async def iter_sse_chunks(lines: AsyncIterator[str]) -> AsyncIterator[StreamChunk]:
    async for raw in lines:
        line = raw.rstrip("\r\n")
        parsed = parse_sse_data_line(line)
        if parsed is None:
            continue
        if parsed == "DONE":
            return
        assert isinstance(parsed, dict)
        for chunk in chunks_from_event(parsed):
            yield chunk
