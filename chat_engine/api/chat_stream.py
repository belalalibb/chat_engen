"""POST /v1/chat/stream (SSE), POST /v1/chat/stream/{id}:cancel, :resume, GET summary."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from core.contracts.model_policy import ModelPolicy
from core.contracts.provider import ProviderOperation
from core.contracts.routing import RoutingRequest
from core.routing.errors import FallbackNotConfigured, NoEligibleCandidates
from core.routing.router import UnsupportedPolicyType
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from chat_engine.api.errors import ChatEngineErrorCode, ce_error_response
from chat_engine.catalog import AliasUnconfirmed, CatalogService
from chat_engine.composition.runtime import Runtime
from chat_engine.handoff import CanonicalMessage, HandoffCompiler, HandoffPacket
from chat_engine.streaming.contracts import StreamEvent, StreamEventType
from chat_engine.streaming.service import StreamService


class ChatMessageIn(ContractModel):
    role: BoundedStr
    content: str = Field(min_length=0, max_length=400_000)
    name: BoundedStr | None = None
    tool_call_id: BoundedStr | None = None


class ChatStreamRequest(ContractModel):
    """Streaming request. Stateless with ``messages``; conversation-backed (CE-09)
    with ``conversation_id``/``project_id`` + ``message`` — history, project
    instructions, summary and skills come from the ChatService pipeline."""

    messages: list[ChatMessageIn] = Field(default_factory=list)
    conversation_id: UUID | None = None
    project_id: UUID | None = None
    message: str | None = Field(default=None, max_length=400_000)
    model: BoundedStr | None = None  # canonical_id or model_key; None ⇒ AUTO
    provider: BoundedStr | None = None
    model_policy: ModelPolicy | None = None
    objective: str | None = Field(default=None, max_length=100_000)
    constraints: list[BoundedStr] = Field(default_factory=list)
    generation: JsonObject = Field(default_factory=dict)
    tools: list[JsonObject] = Field(default_factory=list)
    stream_fallback: BoundedStr | None = None  # "buffer"
    deadline_ms: int | None = Field(default=None, ge=1_000)
    required_capabilities: list[BoundedStr] = Field(default_factory=list)
    skills: list[BoundedStr] = Field(default_factory=list)


class ResumeRequest(ContractModel):
    delivered_until_seq: int = Field(ge=0)


def sse(event: StreamEvent) -> str:
    return f"id: {event.event_id}\nevent: {event.type.value}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"


def compile_request(rt: Runtime, body: ChatStreamRequest, *, packet_id: str) -> JsonObject:
    compiler: HandoffCompiler = rt.extensions["handoff_compiler"]
    msgs = [CanonicalMessage.of(m.role, m.content, source="request") for m in body.messages]
    packet = HandoffPacket(
        packet_id=packet_id,
        objective=body.objective or "",
        constraints=list(body.constraints),
        messages=msgs,
        generation=dict(body.generation),
    )
    compiled = compiler.compile(packet)
    last_user = next(
        (m.content for m in reversed(body.messages) if m.role == "user"), body.messages[-1].content
    )
    payload: JsonObject = {
        "ask": last_user,
        "messages": compiled.messages,
        "handoff_packet_id": packet_id,
    }
    if body.generation:
        payload["generation"] = dict(body.generation)
    if body.tools:
        payload["tools"] = list(body.tools)
        payload["tool_choice"] = "auto"
    return payload


def route_for(rt: Runtime, body: ChatStreamRequest) -> Any:
    """Resolve model (canonical or key) → upstream policy → RoutingDecision, or an error response."""
    catalog: CatalogService = rt.extensions["catalog"]
    policy = body.model_policy
    if policy is None and body.model is None:
        from chat_engine.conversations.service import AUTO_WITH_FAILOVER

        policy = AUTO_WITH_FAILOVER
    if policy is None and body.model is not None:
        try:
            policy = catalog.resolve_policy(body.model, provider_key=body.provider)
        except AliasUnconfirmed:
            return ce_error_response(
                ChatEngineErrorCode.ALIAS_UNCONFIRMED,
                f"'{body.model}' has only proposed aliases; confirm one in /v1/catalog/aliases first.",
                details={"model": body.model},
            )
    try:
        decision = rt.router.route(
            RoutingRequest(
                operation=ProviderOperation.GENERATE_TEXT,
                model_policy=policy,
                required_capabilities=list(body.required_capabilities),
            )
        )
        return rt.reorder_decision(decision)  # CE-24/C (no-op unless enabled)
    except UnsupportedPolicyType as exc:
        return ce_error_response(
            ChatEngineErrorCode.CONFLICT,
            str(exc),
            details={"field": "model_policy"},
            http_status=422,
        )
    except NoEligibleCandidates as exc:
        return ce_error_response(
            ChatEngineErrorCode.NOT_FOUND,
            "No eligible model candidates for this request.",
            details={
                "excluded": [r.model_dump(mode="json", exclude_none=True) for r in exc.excluded]
            },
            http_status=503,
        )
    except FallbackNotConfigured as exc:
        return ce_error_response(ChatEngineErrorCode.CONFLICT, str(exc), http_status=503)


def persist_stream_turn(
    rt: Runtime,
    conversation_id: UUID,
    stream_id: str,
    *,
    packet_id: str | None,
    skills_used: list[JsonObject],
) -> None:
    """Record the assistant turn for a finished stream: complete, partial (cancelled /
    failed after deltas) or failed (nothing delivered)."""
    from chat_engine.conversations import TurnStatus

    svc: StreamService = rt.extensions["streaming"]
    chat_svc = rt.extensions["chat"]
    s = svc.summary(stream_id)
    if s is None:
        return
    if s.status is StreamEventType.COMPLETED:
        status = TurnStatus.COMPLETE
    elif s.text:
        status = TurnStatus.PARTIAL
    else:
        status = TurnStatus.FAILED
    chat_svc.record_stream_turn(
        conversation_id,
        text=s.text,
        status=status,
        provider_key=s.provider_key,
        model_key=s.model_key,
        usage=dict(s.usage),
        stream_id=stream_id,
        packet_id=packet_id,
        skills_used=skills_used,
    )


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/v1/chat", tags=["chat"])
    svc: StreamService = rt.extensions["streaming"]

    @router.post("/stream")
    async def chat_stream(request: Request, body: ChatStreamRequest) -> Response:
        conversation_id: UUID | None = None
        packet_id: str | None = None
        skills_used: list[JsonObject] = []
        if body.message is not None or body.conversation_id is not None:
            # conversation-backed: reuse the ChatService pipeline (CE-09)
            if not body.message:
                return ce_error_response(
                    ChatEngineErrorCode.CONFLICT,
                    "'message' is required when streaming into a conversation.",
                    http_status=422,
                )
            chat_svc = rt.extensions.get("chat")
            if chat_svc is None:
                return ce_error_response(
                    ChatEngineErrorCode.CONFLICT, "Conversations are not enabled.", http_status=503
                )
            from chat_engine.api.conversations import chat_error_response, not_found_response
            from chat_engine.conversations import ChatError, ChatRequest, NotFound

            try:
                prep = await chat_svc.prepare(
                    ChatRequest(
                        conversation_id=body.conversation_id,
                        project_id=body.project_id,
                        message=body.message,
                        model=body.model,
                        provider=body.provider,
                        generation=dict(body.generation),
                        tools=list(body.tools),
                        required_capabilities=list(body.required_capabilities),
                        skills=list(body.skills) if body.skills else None,
                    )
                )
            except NotFound as exc:
                return not_found_response(exc)
            except ChatError as exc:
                return chat_error_response(exc)
            chat_svc.store.append_turn(prep.user_turn)
            decision = prep.decision
            payload = prep.payload
            conversation_id = prep.conversation.id
            packet_id = prep.packet.packet_id
            skills_used = prep.skills_used
        else:
            if not body.messages:
                return ce_error_response(
                    ChatEngineErrorCode.CONFLICT,
                    "Provide 'messages' (stateless) or 'conversation_id'/'message'.",
                    http_status=422,
                )
            decision = route_for(rt, body)
            if isinstance(decision, JSONResponse):
                return decision
            payload = compile_request(rt, body, packet_id=str(uuid4()))

        async def gen() -> AsyncIterator[str]:
            async for ev in svc.run(
                decision=decision,
                payload=payload,
                timeout_ms=body.deadline_ms,
                stream_fallback=body.stream_fallback,
            ):
                yield sse(ev)
                if await request.is_disconnected() and ev.type not in (
                    StreamEventType.COMPLETED,
                    StreamEventType.FAILED,
                    StreamEventType.CANCELLED,
                ):
                    svc.cancel(ev.stream_id)
                if conversation_id is not None and ev.type in (
                    StreamEventType.COMPLETED,
                    StreamEventType.FAILED,
                    StreamEventType.CANCELLED,
                ):
                    persist_stream_turn(
                        rt,
                        conversation_id,
                        ev.stream_id,
                        packet_id=packet_id,
                        skills_used=skills_used,
                    )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    @router.post("/stream/{stream_id}:cancel")
    async def cancel(stream_id: str) -> Response:
        if not svc.cancel(stream_id):
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND, "Unknown stream.", details={"stream_id": stream_id}
            )
        return JSONResponse(content={"stream_id": stream_id, "cancel_requested": True})

    @router.get("/stream/{stream_id}")
    async def summary(stream_id: str) -> Response:
        s = svc.summary(stream_id)
        if s is None:
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND, "Unknown stream.", details={"stream_id": stream_id}
            )
        return JSONResponse(content=s.model_dump(mode="json", exclude_none=True))

    @router.post("/stream/{stream_id}:resume")
    async def resume(request: Request, stream_id: str, body: ResumeRequest) -> Response:
        st = svc.get(stream_id)
        if st is None:
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND, "Unknown stream.", details={"stream_id": stream_id}
            )
        if not st.summary.resumable:
            return ce_error_response(
                ChatEngineErrorCode.CONFLICT,
                "Stream is not resumable.",
                details={"status": st.summary.status.value},
            )
        # Build a continuation: delivered text becomes an assistant prefix the
        # next provider continues from — nothing already delivered is re-sent.
        payload = dict(st.payload)
        msgs = list(payload.get("messages", []))
        msgs.append({"role": "assistant", "content": st.summary.text})
        msgs.append(
            {
                "role": "user",
                "content": "Continue exactly where you stopped. Do not repeat any text already written.",
            }
        )
        payload["messages"] = msgs
        payload["resumed_from_seq"] = body.delivered_until_seq
        st.payload = payload
        st.summary = st.summary.model_copy(
            update={"resumable": False, "status": StreamEventType.STARTED}
        )
        assert st.decision is not None

        async def gen() -> AsyncIterator[str]:
            async for ev in svc.run(decision=st.decision, payload=payload, resume_from=st):  # type: ignore[arg-type]
                yield sse(ev)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router
