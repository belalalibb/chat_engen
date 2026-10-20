"""CE-10 — OpenAI-compatible surface: ``POST /v1/chat/completions`` (sync + stream)
and ``GET /v1/openai/models`` (OpenAI list shape; upstream ``/v1/models`` keeps its
own byte-compatible shape).

Translation only — no business logic lives here:
  messages → HandoffPacket (CE-03) → ONE router → ExecutionService / StreamService.
Errors are returned in the OpenAI envelope ``{"error": {message, type, code, param}}``
with the chat_engine / upstream code preserved in ``code`` (ADR-CE-0002).

Feature flag: ``CE_FEATURE_OPENAI_COMPAT=0`` disables the router (rollback path).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from core.contracts.provider import ProviderOperation
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from chat_engine.admission.port import failure_category
from chat_engine.api.errors import ChatEngineErrorCode
from chat_engine.auth.context import extra_scopes
from chat_engine.composition.runtime import Runtime
from chat_engine.conversations.service import ChatError
from chat_engine.handoff import CanonicalMessage, HandoffBudget, HandoffCompiler, HandoffPacket
from chat_engine.providers.error_details import enrich_details, is_context_window_error
from chat_engine.streaming.contracts import StreamEventType

# --- request / response shapes (OpenAI subset) --------------------------------------------


class OAIMessage(ContractModel):
    role: BoundedStr
    content: str | list[JsonObject] | None = None
    name: BoundedStr | None = None
    tool_call_id: BoundedStr | None = None
    tool_calls: list[JsonObject] | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return "".join(
                str(p.get("text", "")) for p in self.content if p.get("type") in (None, "text")
            )
        return ""


class ChatCompletionRequest(ContractModel):
    model: BoundedStr
    messages: list[OAIMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    tools: list[JsonObject] | None = None
    tool_choice: str | JsonObject | None = None
    response_format: JsonObject | None = None
    user: BoundedStr | None = None
    n: int | None = Field(default=None, ge=1, le=1)
    stream_options: JsonObject | None = None
    # chat_engine extensions (ignored by stock SDKs, allowed as extra_body)
    provider: BoundedStr | None = None
    required_capabilities: list[BoundedStr] = Field(default_factory=list)
    skills: list[BoundedStr] | None = None
    conversation_id: BoundedStr | None = None


# --- OpenAI error envelope -----------------------------------------------------------------

_OAI_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    409: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "api_error",
}


def oai_error(
    status: int, message: str, *, code: str, param: str | None = None, details: Any = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "message": message,
            "type": _OAI_TYPE_BY_STATUS.get(status, "api_error"),
            "code": code,
            "param": param,
        }
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def from_chat_error(exc: ChatError) -> JSONResponse:
    return oai_error(exc.http_status, exc.message, code=exc.code, details=exc.details or None)


# --- translation -----------------------------------------------------------------------------


def _generation(body: ChatCompletionRequest) -> JsonObject:
    gen: JsonObject = {}
    if body.temperature is not None:
        gen["temperature"] = body.temperature
    if body.top_p is not None:
        gen["top_p"] = body.top_p
    mt = body.max_completion_tokens or body.max_tokens
    if mt is not None:
        gen["max_tokens"] = mt
    if body.stop is not None:
        gen["stop"] = body.stop
    if body.response_format is not None:
        gen["response_format"] = dict(body.response_format)
    return gen


def compile_payload(rt: Runtime, body: ChatCompletionRequest) -> tuple[JsonObject, str]:
    compiler: HandoffCompiler = rt.extensions["handoff_compiler"]
    msgs: list[CanonicalMessage] = []
    for m in body.messages:
        cm = CanonicalMessage.of(m.role, m.text(), source="openai_compat")
        if m.tool_call_id:
            cm = cm.model_copy(update={"tool_call_id": m.tool_call_id})
        msgs.append(cm)
    packet = HandoffPacket(
        packet_id=str(uuid4()),
        messages=msgs,
        generation=_generation(body),
        budget=HandoffBudget(
            max_tokens_hint=None,
            context_window=None,
        ),
    )
    compiled = compiler.compile(packet)
    last_user = next((m.text() for m in reversed(body.messages) if m.role == "user"), "")
    payload: JsonObject = {
        "ask": last_user,
        "messages": compiled.messages,
        "handoff_packet_id": packet.packet_id,
    }
    if packet.generation:
        payload["generation"] = dict(packet.generation)
    if body.tools:
        payload["tools"] = list(body.tools)
        payload["tool_choice"] = body.tool_choice if body.tool_choice is not None else "auto"
    return payload, packet.packet_id


def _usage(usage: JsonObject) -> JsonObject:
    p = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    c = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    t = usage.get("total_tokens") or (p + c if isinstance(p, int) and isinstance(c, int) else 0)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}


def completion_object(
    *,
    completion_id: str,
    model: str,
    content: str | None,
    tool_calls: list[JsonObject] | None,
    usage: JsonObject,
    finish_reason: str,
    provider_key: str | None,
    extra: JsonObject | None = None,
) -> JsonObject:
    message: JsonObject = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    obj: JsonObject = {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": _usage(usage),
        "system_fingerprint": f"chat_engine:{provider_key or 'unknown'}",
    }
    if extra:
        obj["chat_engine"] = extra
    return obj


def chunk(
    completion_id: str, model: str, delta: JsonObject, finish_reason: str | None, **extra: Any
) -> str:
    obj: JsonObject = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    obj.update(extra)
    return f"data: {json.dumps(obj)}\n\n"


def models_list(rt: Runtime) -> JsonObject:
    """OpenAI ``GET /v1/models`` shape from the catalog: one row per canonical model
    plus one per concrete ``model_key`` (both are accepted in ``model``)."""
    catalog = rt.extensions["catalog"]
    data: list[JsonObject] = []
    seen: set[str] = set()
    for e in catalog.entries():
        owner = e.canonical.vendor or (
            e.providers[0].provider_key if e.providers else "chat_engine"
        )
        rows = [(e.canonical.canonical_id, owner)] + [
            (k, p.provider_key) for p in e.providers for k in e.model_keys if k == p.model_key
        ]
        for ident, owned_by in rows:
            if ident in seen:
                continue
            seen.add(ident)
            data.append({"id": ident, "object": "model", "created": 0, "owned_by": owned_by})
    data.sort(key=lambda x: str(x["id"]))
    return {"object": "list", "data": data}


# --- router ------------------------------------------------------------------------------------


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["openai-compat"])
    chat_svc = rt.extensions["chat"]

    @router.get("/v1/openai/models")
    async def list_models() -> Response:
        return JSONResponse(content=models_list(rt))

    @router.get("/v1/openai/models/{model_id}")
    async def get_model(model_id: str) -> Response:
        for m in models_list(rt)["data"]:
            if m["id"] == model_id:
                return JSONResponse(content=m)
        return oai_error(404, f"The model '{model_id}' does not exist", code="model_not_found")

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request, body: ChatCompletionRequest) -> Response:
        try:
            decision = chat_svc.route(body.model, body.provider, list(body.required_capabilities))
        except ChatError as exc:
            return from_chat_error(exc)
        model_row = rt.models.get_by_id(decision.selected.model_id)
        pk = rt.extensions["catalog_index"].provider_key(decision.selected.provider_id) or "?"
        payload, packet_id = compile_payload(rt, body)
        completion_id = f"chatcmpl-{uuid4().hex[:24]}"

        if body.stream:
            svc = rt.extensions["streaming"]
            include_usage = bool((body.stream_options or {}).get("include_usage"))

            async def gen() -> AsyncIterator[str]:
                yield chunk(completion_id, body.model, {"role": "assistant", "content": ""}, None)
                usage: JsonObject = {}
                async for ev in svc.run(decision=decision, payload=payload):
                    t = ev.type
                    if t is StreamEventType.MESSAGE_DELTA:
                        yield chunk(completion_id, body.model, {"content": ev.data["delta"]}, None)
                    elif t is StreamEventType.TOOL_CALL_START:
                        yield chunk(
                            completion_id,
                            body.model,
                            {
                                "tool_calls": [
                                    {
                                        "index": ev.data["index"],
                                        "id": ev.data.get("id"),
                                        "type": "function",
                                        "function": {"name": ev.data.get("name"), "arguments": ""},
                                    }
                                ]
                            },
                            None,
                        )
                    elif t is StreamEventType.TOOL_CALL_DELTA:
                        yield chunk(
                            completion_id,
                            body.model,
                            {
                                "tool_calls": [
                                    {
                                        "index": ev.data["index"],
                                        "function": {"arguments": ev.data["arguments_delta"]},
                                    }
                                ]
                            },
                            None,
                        )
                    elif t is StreamEventType.USAGE:
                        usage.update(ev.data)
                    elif t is StreamEventType.COMPLETED:
                        finish = "tool_calls" if svc.summary(ev.stream_id).tool_calls else "stop"
                        yield chunk(
                            completion_id,
                            body.model,
                            {},
                            finish,
                            **({"usage": _usage(usage)} if include_usage else {}),
                            chat_engine={"stream_id": ev.stream_id, "provider_key": pk},
                        )
                    elif t in (StreamEventType.FAILED, StreamEventType.CANCELLED):
                        yield chunk(
                            completion_id,
                            body.model,
                            {},
                            "length" if t is StreamEventType.CANCELLED else "error",
                            error={
                                "message": str(ev.data.get("detail") or ev.data.get("reason")),
                                "type": "api_error",
                                "code": ev.data.get("reason", t.value),
                            },
                            chat_engine={
                                k: ev.data.get(k)
                                for k in ("resumable", "resume_url", "delivered_until_seq")
                                if k in ev.data
                            },
                        )
                    if await request.is_disconnected():
                        svc.cancel(ev.stream_id)
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                gen(),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            )

        # ---- sync -------------------------------------------------------------------------
        admission = rt.extensions.get("admission")
        reservation = None
        if admission is not None:
            from chat_engine.admission import Denied

            est = sum(len(m.text()) for m in body.messages) // 4
            got = await admission.reserve(
                [f"tenant:{rt.tenant_id}", f"provider:{pk}", *extra_scopes()],
                tokens_estimate=est,
                units=1.0,
            )
            if isinstance(got, Denied):
                return oai_error(
                    429,
                    "Admission denied for this request.",
                    code=ChatEngineErrorCode.ADMISSION_DENIED.value,
                    details={"retry_after_ms": got.retry_after_ms, "scope": got.scope},
                )
            reservation = got
        try:
            report = await rt.execution.execute_single(
                tenant_id=rt.tenant_id,
                user_id=rt.user_id,
                decision=decision,
                operation=ProviderOperation.GENERATE_TEXT,
                payload=payload,
                request_hash=completion_id,
            )
        except Exception:
            if admission is not None and reservation is not None:
                await admission.release(reservation)
            raise
        node = report.nodes[-1] if report.nodes else None
        resp = node.response if node else None
        usage = dict(resp.usage) if resp else {}
        if admission is not None and reservation is not None:
            total = usage.get("total_tokens")
            await admission.settle(
                reservation,
                tokens_actual=total if isinstance(total, int) else None,
                success=report.execution.status.value == "succeeded",
                category=failure_category(report),
            )
        rt.executions.put(report)
        recorder = rt.extensions.get("usage_recorder")
        if recorder is not None:
            recorder.record(
                request_id=str(report.execution.id),
                tenant_id=rt.tenant_id,
                surface="openai_compat",
                status="succeeded"
                if report.execution.status.value == "succeeded" and resp and resp.succeeded
                else "failed",
                provider_key=pk,
                model_key=model_row.model_key,
                usage=usage,
                latency_ms=resp.latency_ms if resp else None,
                fallback_chars=sum(len(m.text()) for m in body.messages),
                metadata={"completion_id": completion_id},
            )
        if report.execution.status.value != "succeeded" or resp is None or not resp.succeeded:
            err = resp.error if resp else None
            status = 502
            code = "execution_failed"
            if err is not None:
                cat = err.category.value
                if is_context_window_error(err):
                    status, code = 400, "context_length_exceeded"  # OpenAI's own code
                elif cat == "rate_limited":
                    status, code = 429, "rate_limited"
                elif cat in ("auth_failed", "invalid_credentials"):
                    status, code = 401, "unauthenticated"
                elif cat in ("model_unavailable", "provider_unavailable"):
                    status, code = 503, cat
            return oai_error(
                status,
                err.safe_message if err else "Execution failed.",
                code=code,
                details=enrich_details(
                    {
                        "execution_id": str(report.execution.id),
                        "provider_error_category": err.category.value if err else None,
                    },
                    err,
                ),
            )
        content = resp.output.get("content")
        tool_calls = resp.output.get("tool_calls")
        if not isinstance(content, str):
            content = (
                None if isinstance(tool_calls, list) else json.dumps(resp.output, sort_keys=True)
            )
        return JSONResponse(
            content=completion_object(
                completion_id=completion_id,
                model=body.model,
                content=content,
                tool_calls=tool_calls if isinstance(tool_calls, list) else None,
                usage=usage,
                finish_reason="tool_calls" if isinstance(tool_calls, list) else "stop",
                provider_key=pk,
                extra={
                    "execution_id": str(report.execution.id),
                    "provider_key": pk,
                    "model_key": model_row.model_key,
                    "handoff_packet_id": packet_id,
                    "latency_ms": resp.latency_ms,
                },
            )
        )

    return router


__all__ = ["ChatCompletionRequest", "build_router", "models_list", "oai_error"]
